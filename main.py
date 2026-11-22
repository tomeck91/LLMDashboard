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


class LmsPoller:
    """Poll `lms ps --json` in background thread."""

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

    def snapshot(self):
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

        self._lock = threading.Lock()
        self.history: deque[dict] = deque(maxlen=HISTORY_SECONDS)
        self.latest: dict | None = None
        self.lmstudio = {"online": False, "loaded": []}
        self._last_lm = 0.0
        self.lms = LmsPoller()
        psutil.cpu_percent(interval=None)  # prime: first real value comes on next call

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _gpu_processes(self, h):
        """Processes actively using this GPU right now (SM or memory activity).
        nvmlDeviceGetComputeRunningProcesses on Windows lists every app with a GPU
        context (explorer, dwm, Discord, ...) — noise, so we use the utilization API."""
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

    def _read_gpu(self, i):
        h = self.handles[i]
        util = pynvml.nvmlDeviceGetUtilizationRates(h)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        try:
            fan = pynvml.nvmlDeviceGetFanSpeed(h)
        except pynvml.NVMLError:
            fan = None  # not supported on some cards
        return {
            "index": i,
            "name": self.names[i],
            "uuid": self.uuids[i],
            "util_gpu": util.gpu,
            "util_mem": util.memory,
            "mem_used_mib": mem.used // 1024**2,
            "mem_total_mib": mem.total // 1024**2,
            "temp_c": pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU),
            "power_w": round(pynvml.nvmlDeviceGetPowerUsage(h) / 1000.0, 1),
            "clock_graphics_mhz": pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_GRAPHICS),
            "clock_memory_mhz": pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_MEM),
            "fan_pct": fan,
            "power_limit_w": self.power_limits[i],
            "procs": self._gpu_processes(h),
        }

    def _host_stats(self):
        mem = psutil.virtual_memory()
        return {
            "cpu_pct": psutil.cpu_percent(interval=None),
            "ram_used_gb": round(mem.used / 1024**3, 1),
            "ram_total_gb": round(mem.total / 1024**3, 1),
            "ram_pct": mem.percent,
            "boot_time": psutil.boot_time(),  # epoch s; frontend shows btop-style "up HH:MM:SS"
        }

    def _lmstudio_stats(self, now):
        """Poll the local LM Studio server; only every LMSTUDIO_POLL_INTERVAL seconds."""
        if now - self._last_lm < LMSTUDIO_POLL_INTERVAL:
            return self.lmstudio
        self._last_lm = now
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
            self.lmstudio = {"online": True, "loaded": loaded}
        except Exception:
            self.lmstudio = {"online": False, "loaded": []}
        return self.lmstudio

    def _run(self):
        while True:
            try:
                now = time.time()
                sample = {
                    "t": round(now, 1),
                    "gpus": [self._read_gpu(i) for i in range(self.count)],
                    "host": self._host_stats(),
                    "lmstudio": self._lmstudio_stats(now),
                    "lms": self.lms.snapshot(),
                }
            except pynvml.NVMLError as e:
                print(f"NVML error during sampling: {e}")
                time.sleep(SAMPLE_INTERVAL)
                continue
            with self._lock:
                self.history.append(sample)
                self.latest = sample
            time.sleep(SAMPLE_INTERVAL)

    def snapshot(self):
        with self._lock:
            return self.latest

    def history_payload(self, minutes=5):
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


app = FastAPI(title="GPUDashboard")
sampler = Sampler()


@app.get("/api/gpus")
def api_gpus():
    snap = sampler.snapshot() or {"t": None, "gpus": []}
    return {**snap, "driver_version": sampler.driver_version}


@app.get("/api/history")
def api_history(minutes: int = 5):
    return sampler.history_payload(min(max(minutes, 1), 30))


app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    # 0.0.0.0: reachable from the LAN (e.g. http://<pc-name>:8000 or http://<pc-ip>:8000)
    # log_level="warning": no per-request INFO spam (goes to server.err.log only on errors)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
