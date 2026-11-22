"""GPUDashboard backend — FastAPI + pynvml.

A background thread samples every GPU once per second via NVML and keeps a
rolling in-memory history (5 minutes). The frontend polls two endpoints:
  GET /api/gpus      -> latest snapshot of all GPUs
  GET /api/history   -> time series for the charts
"""
import json
import subprocess
import threading
import time
import urllib.request
from collections import deque

import psutil
import pynvml
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

SAMPLE_INTERVAL = 1.0     # seconds between samples
HISTORY_SECONDS = 300     # keep last 5 minutes
LMSTUDIO_URL = "http://127.0.0.1:1234/api/v1/models"
LMSTUDIO_POLL_INTERVAL = 5.0  # model load/unload changes rarely; 5 s is plenty
LMS_POLL_INTERVAL = 10.0  # lms ps changes only on load/unload
LMS_FIRST_POLL = 3.0  # first poll after 3 s so panel appears quickly


def _to_str(v):
    """pynvml returns bytes on old versions, str on new ones."""
    return v.decode("utf-8") if isinstance(v, bytes) else str(v)


class NvmlReader:
    """NVML data source — reads GPU metrics via pynvml."""

    def __init__(self):
        pynvml.nvmlInit()
        self.driver_version = _to_str(pynvml.nvmlSystemGetDriverVersion())
        self.count = pynvml.nvmlDeviceGetCount()
        self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(self.count)]
        self.names = [_to_str(pynvml.nvmlDeviceGetName(h)) for h in self.handles]
        self.uuids = [_to_str(pynvml.nvmlDeviceGetUUID(h)) for h in self.handles]
        self.power_limits = []  # W, or None if the card doesn't report a limit
        for h in self.handles:
            try:
                self.power_limits.append(pynvml.nvmlDeviceGetPowerManagementLimit(h) / 1000.0)
            except pynvml.NVMLError:
                self.power_limits.append(None)

    def poll(self, index: int) -> dict:
        """Return one GPU sample."""
        h = self.handles[index]
        util = pynvml.nvmlDeviceGetUtilizationRates(h)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        try:
            fan = pynvml.nvmlDeviceGetFanSpeed(h)
        except pynvml.NVMLError:
            fan = None  # not supported on some cards
        return {
            "index": index,
            "name": self.names[index],
            "uuid": self.uuids[index],
            "util_gpu": util.gpu,
            "util_mem": util.memory,
            "mem_used_mib": mem.used // 1024**2,
            "mem_total_mib": mem.total // 1024**2,
            "temp_c": pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU),
            "power_w": round(pynvml.nvmlDeviceGetPowerUsage(h) / 1000.0, 1),
            "clock_graphics_mhz": pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_GRAPHICS),
            "clock_memory_mhz": pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_MEM),
            "fan_pct": fan,
            "power_limit_w": self.power_limits[index],
            "procs": self._gpu_processes(h),
        }

    def _gpu_processes(self, h):
        """Processes actively using this GPU right now (SM or memory activity)."""
        try:
            samples = pynvml.nvmlDeviceGetProcessUtilization(h, 0)
        except pynvml.NVMLError:
            return []
        out = []
        for s in samples:
            if s.smUtil > 0 or s.memUtil > 0:
                try:
                    name = psutil.Process(s.pid).name()
                except psutil.Error:
                    name = "unknown"
                out.append({"pid": s.pid, "name": name, "sm_pct": s.smUtil})
        return out


class PsutilReader:
    """Host stats data source — reads CPU/RAM/boot info via psutil."""

    def __init__(self):
        psutil.cpu_percent(interval=None)  # prime: first real value comes on next call

    def poll(self) -> dict:
        mem = psutil.virtual_memory()
        return {
            "cpu_pct": psutil.cpu_percent(interval=None),
            "ram_used_gb": round(mem.used / 1024**3, 1),
            "ram_total_gb": round(mem.total / 1024**3, 1),
            "ram_pct": mem.percent,
            "boot_time": psutil.boot_time(),  # epoch s; frontend shows btop-style "up HH:MM:SS"
        }


class LmStudioReader:
    """LM Studio HTTP API data source — polls /api/v1/models."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cached = {"online": False, "loaded": []}
        self._last_poll = 0.0

    def poll(self) -> dict:
        now = time.time()
        if now - self._last_poll < LMSTUDIO_POLL_INTERVAL:
            return self._cached
        self._last_poll = now
        try:
            with urllib.request.urlopen(LMSTUDIO_URL, timeout=2) as r:
                data = json.loads(r.read().decode("utf-8"))
            loaded = []
            for m in data.get("models", []):
                for inst in m.get("loaded_instances", []):
                    q = m.get("quantization") or {}
                    size = m.get("size_bytes")
                    loaded.append({
                        "name": m.get("display_name") or m.get("key"),
                        "quant": q.get("name"),
                        "context": (inst.get("config") or {}).get("context_length"),
                        "size_gb": round(size / 1024**3, 1) if size else None,
                        "max_context": m.get("max_context_length"),
                    })
            result = {"online": True, "loaded": loaded}
        except Exception:
            result = {"online": False, "loaded": []}
        with self._lock:
            self._cached = result
        return result


class LmsReader:
    """LMS CLI data source — polls `lms ps --json` in background thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self.loaded: list[dict] = []
        self._last_ps = -LMS_FIRST_POLL  # force first poll immediately
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while True:
            now = time.time()
            try:
                if now - self._last_ps >= LMS_POLL_INTERVAL:
                    self._last_ps = now
                    self._poll_ps()
            except Exception as e:
                print(f"LMS poll error: {e}")
            time.sleep(2)  # lightweight sleep; re-check every 2 s

    def _poll_ps(self):
        """Run `lms ps --json` and parse loaded models."""
        try:
            r = subprocess.run(
                ["lms", "ps", "--json"],
                capture_output=True, text=True, timeout=5,
            )
            data = json.loads(r.stdout)
            loaded = []
            for m in data:
                ttl = m.get("ttlMs")
                loaded.append({
                    "identifier": m.get("identifier"),
                    "name": m.get("displayName"),
                    "status": m.get("status"),  # idle|loading|loaded|unloading
                    "size_gb": round(m.get("sizeBytes", 0) / 1024**3, 1),
                    "context": m.get("maxContextLength"),
                    "parallel": m.get("parallel"),
                    "quant": m.get("quantization", {}).get("name"),
                    "ttl_ms": ttl,
                    "ttl_remaining": _format_ttl(ttl) if ttl else None,
                    "device": "Local" if m.get("deviceIdentifier") is None else m.get("deviceIdentifier"),
                    "sizeBytes": m.get("sizeBytes"),
                    "maxContextLength": m.get("maxContextLength"),
                    "quantization": m.get("quantization"),
                    "modelKey": m.get("modelKey"),
                    "publisher": m.get("publisher"),
                })
            with self._lock:
                self.loaded = loaded
        except Exception:
            with self._lock:
                self.loaded = []

    def poll(self) -> dict:
        with self._lock:
            return {"loaded": list(self.loaded)}


def _format_ttl(ms):
    """Format milliseconds TTL as 'MM:SS' countdown."""
    if not ms:
        return None
    s = max(0, ms // 1000)
    m = s // 60
    sec = s % 60
    return f"{m:02d}:{sec:02d}"


class Sampler:
    """Deep module: small interface, large implementation.

    Public interface (only two methods):
      - snapshot() -> dict | None
      - history_payload(minutes=5) -> dict

    All data sources (NVML, psutil, HTTP, CLI) are internal adapters.
    """

    def __init__(self):
        # Configuration only — no NVML init, no threads (lazy init)
        self.history: deque[dict] = deque(maxlen=HISTORY_SECONDS)
        self._latest: dict | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _ensure_started(self):
        """Lazily start the background sampler thread."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _wait_for_first_sample(self, timeout: float = 5.0):
        """Block until first sample is available or timeout."""
        import time
        start = time.time()
        while self._latest is None:
            if time.time() - start > timeout:
                return
            time.sleep(0.1)

    def snapshot(self) -> dict | None:
        """Return latest sample or None if not yet sampled."""
        self._ensure_started()
        with self._lock:
            return self._latest

    def snapshot_blocking(self, timeout: float = 5.0) -> dict | None:
        """Return latest sample, waiting up to timeout for first sample."""
        self._ensure_started()
        self._wait_for_first_sample(timeout)
        with self._lock:
            return self._latest

    def history_payload(self, minutes: int = 5) -> dict:
        """Return time series for charts."""
        # Ensure sampler is running before accessing properties
        self._ensure_started()
        n = min(int(minutes * 60), len(self.history))
        samples = list(self.history)[-n:] if n else []
        gpus = [
            {
                "index": i,
                "name": self.names[i],
                "util_gpu": [s["gpus"][i]["util_gpu"] for s in samples],
                "mem_used_mib": [s["gpus"][i]["mem_used_mib"] for s in samples],
                "temp_c": [s["gpus"][i]["temp_c"] for s in samples],
                "power_w": [s["gpus"][i]["power_w"] for s in samples],
            }
            for i in range(self.count)
        ]
        return {"interval_s": SAMPLE_INTERVAL, "t": [s["t"] for s in samples], "gpus": gpus}

    def _run(self):
        """Background loop: poll all sources every SAMPLE_INTERVAL seconds."""
        # Initialize readers (NVML, psutil, HTTP, CLI)
        self._gpu_reader = NvmlReader()
        self._host_reader = PsutilReader()
        self._lmstudio_reader = LmStudioReader()
        self._lms_reader = LmsReader()

        while True:
            try:
                now = time.time()
                sample = {
                    "t": round(now, 1),
                    "gpus": [self._gpu_reader.poll(i) for i in range(self._gpu_reader.count)],
                    "host": self._host_reader.poll(),
                    "lmstudio": self._lmstudio_reader.poll(),
                    "lms": self._lms_reader.poll(),
                }
            except pynvml.NVMLError as e:
                print(f"NVML error during sampling: {e}")
                time.sleep(SAMPLE_INTERVAL)
                continue
            with self._lock:
                self.history.append(sample)
                self._latest = sample
            time.sleep(SAMPLE_INTERVAL)

    @property
    def driver_version(self) -> str:
        """GPU driver version (from NVML)."""
        if hasattr(self, "_gpu_reader"):
            return self._gpu_reader.driver_version
        raise RuntimeError("Sampler not yet initialized")

    @property
    def count(self) -> int:
        """Number of GPUs."""
        if hasattr(self, "_gpu_reader"):
            return self._gpu_reader.count
        raise RuntimeError("Sampler not yet initialized")

    @property
    def names(self) -> list[str]:
        """GPU names in order."""
        if hasattr(self, "_gpu_reader"):
            return self._gpu_reader.names
        raise RuntimeError("Sampler not yet initialized")


# Module-level singleton (lazy init)
_sampler: Sampler | None = None


def get_sampler() -> Sampler:
    """Return the singleton Sampler, creating it on first call."""
    global _sampler
    if _sampler is None:
        _sampler = Sampler()
    return _sampler


app = FastAPI(title="GPUDashboard")


@app.get("/api/gpus")
def api_gpus():
    sampler = get_sampler()
    snap = sampler.snapshot_blocking(timeout=2) or {"t": None, "gpus": []}
    try:
        driver = sampler.driver_version
    except RuntimeError:
        driver = "initializing..."
    return {**snap, "driver_version": driver}


@app.get("/api/history")
def api_history(minutes: int = 5):
    sampler = get_sampler()
    # Wait briefly for first sample before building history
    sampler.snapshot_blocking(timeout=1)
    return sampler.history_payload(min(max(minutes, 1), 30))


# Mount static files AFTER API routes so /static/* and / work correctly
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
