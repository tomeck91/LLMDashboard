"""GPUDashboard backend — FastAPI + pynvml.

A background thread samples every GPU once per second via NVML and keeps a
rolling in-memory history (5 minutes). The frontend polls two endpoints:
  GET /api/gpus      -> latest snapshot of all GPUs
  GET /api/history   -> time series for the charts
"""
import json
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


def _to_str(v):
    """pynvml returns bytes on old versions, str on new ones."""
    return v.decode("utf-8") if isinstance(v, bytes) else str(v)


class Sampler:
    def __init__(self):
        pynvml.nvmlInit()
        self.driver_version = _to_str(pynvml.nvmlSystemGetDriverVersion())
        self.count = pynvml.nvmlDeviceGetCount()
        self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(self.count)]
        self.names = [_to_str(pynvml.nvmlDeviceGetName(h)) for h in self.handles]
        self.uuids = [_to_str(pynvml.nvmlDeviceGetUUID(h)) for h in self.handles]

        self._lock = threading.Lock()
        self.history: deque[dict] = deque(maxlen=HISTORY_SECONDS)
        self.latest: dict | None = None
        self.lmstudio = {"online": False, "loaded": []}
        self._last_lm = 0.0
        psutil.cpu_percent(interval=None)  # prime: first real value comes on next call

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

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
        }

    def _host_stats(self):
        mem = psutil.virtual_memory()
        return {
            "cpu_pct": psutil.cpu_percent(interval=None),
            "ram_used_gb": round(mem.used / 1024**3, 1),
            "ram_total_gb": round(mem.total / 1024**3, 1),
            "ram_pct": mem.percent,
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
                    loaded.append({
                        "name": m.get("display_name") or m.get("key"),
                        "quant": q.get("name"),
                        "context": (inst.get("config") or {}).get("context_length"),
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

    # log_level="warning": no per-request INFO spam (goes to server.err.log only on errors)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
