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
from pathlib import Path

import psutil
import pynvml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

SAMPLE_INTERVAL = 1.0     # seconds between samples
HISTORY_SECONDS = 300     # keep last 5 minutes
LMSTUDIO_URL = "http://127.0.0.1:1234/api/v1/models"
LMS_POLL_INTERVAL = 10.0  # lms ps changes only on load/unload
LMS_FIRST_POLL = 3.0      # first poll after 3 s so panel appears quickly
_PROJECT_ROOT = Path(__file__).resolve().parent  # git repo root (main.py lives at the top)
STATIC_DIR = _PROJECT_ROOT / "static"


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
    """Unified LM Studio reader — merges HTTP API + CLI data sources.

    The HTTP API (GET /api/v1/models) provides model identity and config
    metadata (name, quantization, context_length, parallel). The CLI tool
    (`lms ps --json`) provides runtime state (status, TTL countdown, device).

    Both sources are polled in the background; results are merged by model
    name so the frontend receives one shape regardless of which source
    provided each field.
    """

    def __init__(self):
        self._cli_reader = _LmsCliReader()
        self._stats_reader = _LmsStatsReader()

    def poll(self) -> dict:
        """Return merged model data from both sources, plus last-request stats."""
        http_data = _poll_http_raw()
        cli_data = self._cli_reader.poll()
        models = _merge_models(http_data.get("loaded", []), cli_data.get("loaded", []))
        stats = self._stats_reader.poll()
        for m in models:
            m["stats"] = stats.get(m.get("id"))
        return {
            "online": http_data["online"] or bool(cli_data.get("loaded")),
            "loaded": models,
        }


def _poll_http_raw() -> dict:
    """Poll LM Studio HTTP API. Returns {online, loaded}."""
    try:
        with urllib.request.urlopen(LMSTUDIO_URL, timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        loaded = []
        for m in data.get("models", []):
            for inst in m.get("loaded_instances", []):
                q = m.get("quantization") or {}
                size = m.get("size_bytes")
                config = (inst.get("config") or {})
                loaded.append({
                    "id": inst.get("id") or m.get("key"),
                    "name": m.get("display_name") or m.get("key"),
                    "quant": q.get("name"),
                    "context": config.get("context_length"),
                    "parallel": config.get("parallel"),
                    "size_gb": round(size / 1024**3, 1) if size else None,
                })
        return {"online": True, "loaded": loaded}
    except Exception:
        return {"online": False, "loaded": []}


class _LmsCliReader:
    """Internal: polls `lms ps --json` in a background thread."""

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
                    "id": m.get("identifier"),
                    "name": m.get("displayName"),
                    "status": m.get("status"),  # idle|loading|loaded|unloading
                    "size_gb": round(m.get("sizeBytes", 0) / 1024**3, 1),
                    "context": m.get("maxContextLength"),
                    "parallel": m.get("parallel"),
                    "quant": m.get("quantization", {}).get("name"),
                    "ttl_ms": ttl,
                    "ttl_remaining": _format_ttl(ttl) if ttl else None,
                    "device": "Local" if m.get("deviceIdentifier") is None else m.get("deviceIdentifier"),
                })
            with self._lock:
                self.loaded = loaded
        except Exception:
            with self._lock:
                self.loaded = []

    def poll(self) -> dict:
        with self._lock:
            return {"loaded": list(self.loaded)}


def _parse_stats_line(line: str):
    """Parse one `lms log stream -s model --stats --json` line.

    Returns (model_id, stats_dict) for a finished prediction, else None.
    Only values LM Studio reports exactly are kept; prompt/output text is discarded.
    """
    if '"stats"' not in line:  # cheap pre-filter: input echoes carry no stats
        return None
    try:
        data = json.loads(line).get("data") or {}
    except ValueError:
        return None
    s = data.get("stats")
    model_id = data.get("modelIdentifier")
    if not s or not model_id:
        return None
    return model_id, {
        "gen_tps": s.get("tokensPerSecond"),
        "prompt_tokens": s.get("promptTokensCount"),
        "gen_tokens": s.get("predictedTokensCount"),
        "total_s": s.get("totalTimeSec"),
        "t": time.time(),
    }


class _LmsStatsReader:
    """Internal: keeps `lms log stream -s model --stats --json` running and
    remembers the stats of the last finished request per model identifier."""

    def __init__(self):
        self._lock = threading.Lock()
        self._stats: dict[str, dict] = {}
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                proc = subprocess.Popen(
                    ["lms", "log", "stream", "-s", "model", "--stats", "--json"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                for line in proc.stdout:
                    parsed = _parse_stats_line(line)
                    if parsed:
                        with self._lock:
                            self._stats[parsed[0]] = parsed[1]
                proc.wait()
            except Exception as e:
                print(f"LMS log stream error: {e}")
            time.sleep(5)  # stream ended (LM Studio closed?) — retry

    def poll(self) -> dict:
        with self._lock:
            return dict(self._stats)


def _merge_models(http_models: list[dict], cli_models: list[dict]) -> list[dict]:
    """Merge HTTP API + CLI model data into a single unified shape.

    Each model is keyed by its ``name`` field (display name from either source).
    CLI data provides runtime fields (status, ttl, device); HTTP data provides
    config fields (context_length from loaded_instances[].config, parallel from
    loaded_instances[].config.parallel). Where both provide the same field,
    the more complete value wins.

    Returns list of dicts with this shape:
        {name, quant, size_gb, context, parallel, status, ttl_remaining, device}
    """
    merged: dict[str, dict] = {}

    for m in http_models:
        name = m.get("name") or "unknown"
        merged[name] = {
            "id": m.get("id"),
            "name": name,
            "quant": m.get("quant"),
            "size_gb": m.get("size_gb"),
            "context": m.get("context"),
            "parallel": m.get("parallel"),
            "status": None,
            "ttl_remaining": None,
            "device": None,
        }

    for m in cli_models:
        name = m.get("name") or "unknown"
        if name in merged:
            # Merge into existing HTTP entry — CLI provides runtime fields
            entry = merged[name]
            if not entry["id"] and m.get("id"):
                entry["id"] = m["id"]
            if not entry["quant"] and m.get("quant"):
                entry["quant"] = m["quant"]
            if not entry["size_gb"] and m.get("size_gb"):
                entry["size_gb"] = m["size_gb"]
            if entry["context"] is None and m.get("context"):
                entry["context"] = m["context"]
            if entry["parallel"] is None and m.get("parallel"):
                entry["parallel"] = m["parallel"]
            # Always add CLI runtime fields (status, ttl, device)
            if not entry["status"] and m.get("status"):
                entry["status"] = m["status"]
            if not entry["ttl_remaining"] and m.get("ttl_remaining"):
                entry["ttl_remaining"] = m["ttl_remaining"]
            if not entry["device"] and m.get("device"):
                entry["device"] = m["device"]
        else:
            # CLI-only model (not in HTTP API)
            merged[name] = {
                "id": m.get("id"),
                "name": name,
                "quant": m.get("quant"),
                "size_gb": m.get("size_gb"),
                "context": m.get("context"),
                "parallel": m.get("parallel"),
                "status": m.get("status"),
                "ttl_remaining": m.get("ttl_remaining"),
                "device": m.get("device"),
            }

    return list(merged.values())


def _format_ttl(ms):
    """Format milliseconds TTL as 'MM:SS' countdown."""
    if not ms:
        return None
    s = int(max(0, ms) // 1000)
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
        self.error: str | None = None  # set if the sampler thread fails to start
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
        if not hasattr(self, "_gpu_reader"):  # not initialized yet (or init failed)
            return {"interval_s": SAMPLE_INTERVAL, "t": [], "gpus": []}
        with self._lock:
            samples = list(self.history)
        n = min(int(minutes * 60), len(samples))
        samples = samples[-n:] if n else []
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
        # Initialize readers (NVML, psutil, HTTP+CLI). _gpu_reader is assigned
        # last so hasattr(self, "_gpu_reader") means "all readers ready".
        try:
            host_reader = PsutilReader()
            lmstudio_reader = LmStudioReader()
            gpu_reader = NvmlReader()
        except Exception as e:
            self.error = f"sampler init failed: {e}"
            print(self.error)
            return
        self._host_reader = host_reader
        self._lmstudio_reader = lmstudio_reader
        self._gpu_reader = gpu_reader

        while True:
            try:
                now = time.time()
                sample = {
                    "t": round(now, 1),
                    "gpus": [self._gpu_reader.poll(i) for i in range(self._gpu_reader.count)],
                    "host": self._host_reader.poll(),
                    "lmstudio": self._lmstudio_reader.poll(),
                }
            except Exception as e:  # keep the thread alive on any transient error
                print(f"Sampling error: {e}")
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


# Module-level singleton (lazy init, thread-safe)
_sampler: Sampler | None = None
_sampler_lock = threading.Lock()


def get_sampler() -> Sampler:
    """Return the singleton Sampler, creating it on first call."""
    global _sampler
    if _sampler is None:
        with _sampler_lock:
            if _sampler is None:  # double-check
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
        driver = sampler.error or "initializing..."
    return {**snap, "driver_version": driver}


@app.get("/api/history")
def api_history(minutes: int = 5):
    sampler = get_sampler()
    # Wait briefly for first sample before building history
    sampler.snapshot_blocking(timeout=1)
    return sampler.history_payload(min(max(minutes, 1), 30))


@app.get("/health")
def health_check():
    return {"status": "ok"}


# Route for / must come BEFORE static mounts (mounts act as catch-all routes)
@app.get("/")
async def serve_index():
    """Serve index.html with injected version from git tags."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    tag = _git_tag()
    html = html.replace("v0-stable", tag)
    return HTMLResponse(content=html)

# Serve static files (app.js, etc.) at root level
app.mount("/", StaticFiles(directory=STATIC_DIR, html=False), name="static_root")


def _git_tag():
    """Return the latest git tag, falling back to 'dev'."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True, text=True, timeout=3, cwd=str(_PROJECT_ROOT),
        )
        tag = result.stdout.strip() or None
    except Exception:
        tag = None
    return tag or "dev"


@app.middleware("http")
async def add_no_cache(request, call_next):
    response = await call_next(request)
    # Static files are mounted at "/" (e.g. /app.js), so cover everything except the API
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
