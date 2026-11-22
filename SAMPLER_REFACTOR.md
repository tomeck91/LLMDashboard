# Sampler Refactor — Design Document

**Date:** 2026-11-22  
**Candidate:** C1 — Deepen the Sampler  
**Status:** Implementation plan approved

---

## Final Design Decisions

| Decision | Chosen option |
|---|---|
| Public interface | Two methods only (`snapshot()`, `history_payload(minutes)`) |
| Source structure | One method per data type (`poll_gpu(i)`, `poll_host()`, `poll_lmstudio()`, `poll_lms()`) |
| Thread ownership | Keep threads in Sampler (one background sampler thread) |
| Singleton pattern | Lazy init — `__init__` stores config only, no side effects |
| History shape | Keep same (`interval_s`, `t`, `gpus`) |
| Source module location | Inner classes inside `main.py` |
| NVML handle caching | Cache at init (current) |
| Reader method name | `poll()` |
| LmsPoller structure | Fold into Sampler as `self.lms_reader = LmsReader()` |
| Lazy init implementation | No `__init__` side effects; thread starts on first `snapshot()` |
| History storage | Keep `deque(maxlen=300)` |
| NVML init failure | Raise immediately (fail fast) |
| FastAPI routes | No changes |

---

## New Module Structure

```python
# main.py

class Sampler:
    """Deep module: small interface, large implementation."""
    
    def __init__(self):
        # Configuration only — no NVML init, no threads
        self.history = deque(maxlen=300)
        self._latest = None
        self._lock = threading.Lock()
        self._thread = None  # Created lazily
    
    def snapshot(self) -> dict | None:
        """Return latest sample or None if not yet sampled."""
        with self._lock:
            return self._latest
    
    def history_payload(self, minutes: int = 5) -> dict:
        """Return time series for charts."""
        ...
    
    def _start_thread(self):
        """Lazily start the background sampler thread."""
        ...


class NvmlReader:
    """NVML data source — one reader per data type."""
    
    def __init__(self):
        pynvml.nvmlInit()
        self.count = pynvml.nvmlDeviceGetCount()
        self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(self.count)]
        self.names = [_to_str(pynvml.nvmlDeviceGetName(h)) for h in self.handles]
        ...
    
    def poll(self, index: int) -> dict:
        """Return one GPU sample."""
        h = self.handles[index]
        util = pynvml.nvmlDeviceGetUtilizationRates(h)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        return {
            "index": index,
            "name": self.names[index],
            "util_gpu": util.gpu,
            ...
        }


class PsutilReader:
    """Host stats data source."""
    
    def __init__(self):
        psutil.cpu_percent(interval=None)  # prime
    
    def poll(self) -> dict:
        mem = psutil.virtual_memory()
        return {
            "cpu_pct": psutil.cpu_percent(interval=None),
            "ram_used_gb": round(mem.used / 1024**3, 1),
            ...
        }


class LmStudioReader:
    """LM Studio HTTP API data source."""
    
    def __init__(self):
        self._last_poll = 0.0
    
    def poll(self) -> dict:
        now = time.time()
        if now - self._last_poll < 5.0:
            return self._cached
        ...
        return {"online": True, "loaded": [...]}


class LmsReader:
    """LMS CLI data source."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._last_poll = -3.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def poll(self) -> dict:
        with self._lock:
            return {"loaded": list(self._loaded)}
    
    def _run(self):
        while True:
            ...
            subprocess.run(["lms", "ps", "--json"], ...)
            ...


# Module-level singleton (lazy init)
_sampler = None

def get_sampler() -> Sampler:
    """Return the singleton Sampler, creating it on first call."""
    global _sampler
    if _sampler is None:
        _sampler = Sampler()
        _sampler._start_thread()
    return _sampler


# FastAPI routes unchanged
@app.get("/api/gpus")
def api_gpus():
    sampler = get_sampler()
    snap = sampler.snapshot() or {"t": None, "gpus": []}
    return {**snap, "driver_version": sampler.driver_version}

@app.get("/api/history")
def api_history(minutes: int = 5):
    sampler = get_sampler()
    return sampler.history_payload(min(max(minutes, 1), 30))
```

---

## Implementation Steps

1. **Add reader classes** (`NvmlReader`, `PsutilReader`, `LmStudioReader`, `LmsReader`) as inner classes in `main.py`
2. **Refactor `Sampler.__init__`** — store config only, don't call NVML, don't start threads
3. **Add `Sampler._start_thread()`** — lazy thread starter called by first `snapshot()`
4. **Move `_read_gpu`, `_host_stats`, `_lmstudio_stats`, `_gpu_processes` into reader methods**
5. **Update `Sampler._run()`** to call `reader.poll()` instead of inline logic
6. **Add `driver_version`, `count`, `names` as properties or include in snapshot**
7. **Replace `sampler = Sampler()` with `get_sampler()` factory**
8. **Write tests** for each reader and for `Sampler.snapshot()`, `Sampler.history_payload()`

---

## Test Strategy

```python
# test_main.py (new file)

def test_nvml_reader_poll():
    reader = NvmlReader()
    sample = reader.poll(0)
    assert "util_gpu" in sample
    assert "mem_used_mib" in sample

def test_psutil_reader_poll():
    reader = PsutilReader()
    sample = reader.poll()
    assert "cpu_pct" in sample
    assert "ram_used_gb" in sample

def test_sampler_with_fake_readers():
    class FakeGpuReader:
        def poll(self, i):
            return {"index": 0, "name": "Fake GPU", "util_gpu": 50, ...}
    
    sampler = Sampler(gpu_reader=FakeGpuReader())
    snap = sampler.snapshot()
    assert snap["gpus"][0]["util_gpu"] == 50
```

---

## Breaking Changes

None. The FastAPI endpoints stay the same. The public interface (`snapshot()`, `history_payload(minutes)`) stays the same. Only internal structure changes.

---

*End of design document.*
