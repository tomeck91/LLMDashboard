# GPUDashboard

Live GPU monitoring dashboard for NVIDIA GPUs (2× RTX 3090 on this machine).

Backend: Python + FastAPI + pynvml. Frontend: vanilla HTML/JS + Chart.js (CDN, no build step).

## Run

```powershell
cd C:\AI\OpenCode\GPUDashboard
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py
```

Then open **http://localhost:8000**. The server binds to `0.0.0.0`, so it's also reachable from other devices on your LAN: **http://\<pc-ip\>:8000** (find the IP with `ipconfig`).

> **Security note:** This is read-only stats with no authentication — anyone on your network can view it.

### Quick way: batch files

| File | Action |
|---|---|
| `start.bat` | Starts the server hidden (no console window), opens the browser. No-op if already running. |
| `stop.bat` | Kills whatever is listening on port 8000 (safe to run when already stopped). |
| `restart.bat` | stop + start. |

Server output goes to `server.log` / `server.err.log` in this folder.

## What it shows

### Header
- Host CPU % and RAM (used/total, %)
- Model currently loaded in LM Studio — name, quantization, size, max context, status badge and TTL countdown
  - Polled from `http://127.0.0.1:1234/api/v1/models` (HTTP API) + `lms ps --json` (CLI runtime data), merged into one unified shape every ~10 s

### Per-GPU cards (1 s refresh)
- GPU / Memory utilization % (segmented terminal bars, color-coded amber → warn → hot)
- VRAM bar (MiB / GB)
- Active GPU processes (name + SM % util; hovered to see full PID list)
- Temperature, power draw (with TDP limit), graphics clock, memory clock, fan speed

### Live charts (5 min rolling window, 1 s samples)
- GPU Utilization (%) — fixed Y-axis 0–100
- VRAM Used (MiB / GB) — fixed Y-axis 0–24 GB
- Temperature (°C) — fixed Y-axis 0–100
- Power Draw (W) — dynamic max capped at NVML power_limit_w

## Architecture

```
┌─────────────┐     ┌──────────┐     ┌──────────────┐
│  pynvml     │     │ psutil   │     │ LM Studio    │
│  (NVML)     │     │          │     │ HTTP + CLI   │
└──────┬──────┘     └────┬─────┘     └──────┬───────┘
       │                 │                   │
       ▼                 ▼                   ▼
┌──────────────────────────────────────────────────┐
│              NvmlReader / PsutilReader           │
│              LmStudioReader (HTTP + CLI merge)   │
└──────────────────────────┬───────────────────────┘
                           │ poll every 1 s
                           ▼
┌──────────────────────────────────────────────────┐
│                   Sampler                        │
│  singleton · lazy-init · background thread       │
│  ring buffer (deque, maxlen=300, ~5 min)         │
└──────┬───────────────────────────────────────────┘
       │
       ├──► GET /api/gpus      → latest snapshot + driver version
       └──► GET /api/history   → time series for charts (1–30 min)
```

### Deep module: Sampler

The `Sampler` class is a **deep module** — small public interface, large internal implementation.

- **Public interface:** only two methods — `snapshot()` and `history_payload(minutes)`
- **Internal adapters:** `NvmlReader`, `PsutilReader`, `LmStudioReader` (which itself merges HTTP API + CLI data)
- **Lazy init:** NVML handles, threads, and readers are created on first real use (`_ensure_started`)
- **Thread-safe:** double-checked locking for singleton creation; per-sample lock for shared state

### Model merging: LmStudioReader

Two independent data sources feed into one unified model shape:

| Source | Provides | Polls via |
|---|---|---|
| HTTP API | name, quantization, context_length, parallel, size_bytes | `GET /api/v1/models` every 10 s |
| CLI (`lms ps`) | status (idle/loading/loaded/unloading), TTL countdown, device | `lms ps --json` every 2 s (actual poll every 10 s) |

The `_merge_models()` function merges by model display name — HTTP provides config fields, CLI provides runtime fields. Where both provide the same field, the more complete value wins.

## Files

| File | Purpose |
|---|---|
| `main.py` | FastAPI app + background sampler thread + all data readers (~470 lines) |
| `static/index.html` | UI shell + amber monochrome CSS (184 lines) |
| `static/app.js` | Polling logic, card rendering, Chart.js charts, models table (306 lines) |
| `_smoke_test.py` | Standalone diagnostic: verifies every pynvml metric works |
| `test_main.py` | pytest unit tests for NvmlReader, PsutilReader, LmStudioReader, Sampler, _merge_models |
| `requirements.txt` | Dependencies: fastapi, uvicorn, nvidia-ml-py, psutil |
| `.gitignore` | Excludes .venv/, __pycache__/, *.pyc, *.log |

## API

### Endpoints

| Endpoint | Description | Example |
|---|---|---|
| `GET /` | Serves the dashboard HTML (version injected from git tags) | — |
| `GET /api/gpus` | Latest snapshot of all GPUs + host stats + LM Studio state | — |
| `GET /api/history?minutes=5` | Time series for charts (1 s samples, 1–30 min window) | `?minutes=10` |

### Response shapes

**`GET /api/gpus`:**
```json
{
  "t": 1732276800.5,
  "driver_version": "575.xx",
  "gpus": [
    {
      "index": 0,
      "name": "NVIDIA GeForce RTX 3090",
      "uuid": "GPU-xxxxxxxx",
      "util_gpu": 42,
      "util_mem": 68,
      "mem_used_mib": 14336,
      "mem_total_mib": 24576,
      "temp_c": 65,
      "power_w": 120.5,
      "clock_graphics_mhz": 900,
      "clock_memory_mhz": 1313,
      "fan_pct": 30,
      "power_limit_w": 350,
      "procs": [{"pid": 12345, "name": "python", "sm_pct": 42}]
    }
  ],
  "host": {
    "cpu_pct": 15,
    "ram_used_gb": 12.4,
    "ram_total_gb": 64,
    "ram_pct": 19,
    "boot_time": 1732000000
  },
  "lmstudio": {
    "online": true,
    "loaded": [
      {"name": "Qwen3.6-35B-A3B", "quant": "IQ3_S", "size_gb": 19.2, "context": 8192, "parallel": null, "status": "loaded", "ttl_remaining": "05:23", "device": "Local"}
    ]
  }
}
```

**`GET /api/history?minutes=5`:**
```json
{
  "interval_s": 1.0,
  "t": [1732276800.5, 1732276801.5, ...],
  "gpus": [
    {
      "index": 0,
      "name": "NVIDIA GeForce RTX 3090",
      "util_gpu": [42, 38, 55, ...],
      "mem_used_mib": [14336, 14336, 14336, ...],
      "temp_c": [65, 66, 67, ...],
      "power_w": [120.5, 118.2, 135.0, ...]
    }
  ]
}
```

## Versioning

The footer version is injected at request time from git tags:

- The HTML file contains the placeholder `v0-stable` in the footer.
- The `/` route reads `static/index.html`, runs `html.replace("v0-stable", _git_tag())`, and returns it.
- `_git_tag()` calls `git describe --tags --abbrev=0` from the project directory; falls back to `"dev"` if no tags exist.

**To tag a new version:**
```powershell
git tag v0.18.0 HEAD
git push origin v0.18.0
```

The footer will automatically show `v0.18.0` on the next page load. No code changes needed.

## Key technical details

- **Route ordering:** The `/` route is defined *before* the `StaticFiles` mount at `/`. In Starlette, mounts act as catch-all routes and would intercept `/` if placed first.
- **Cache control:** A middleware adds `Cache-Control: no-cache` to `/` and `/static/*` responses so version changes are always reflected.
- **No template engine:** Version injection uses plain string replace (`html.replace()`) rather than Jinja2 — avoids `TypeError: unhashable type: 'dict'` errors when `Request` objects in context break Jinja2's template cache.
- **Sampling rate:** 1 sample per second per GPU, kept in a deque with maxlen=300 (5 minutes of history).
- **LM Studio polling:** HTTP API every 10 s; CLI poll runs every 2 s internally but only executes the command every 10 s (faster re-check loop just reads cached data).

## Testing

```powershell
cd C:\AI\OpenCode\GPUDashboard
.\.venv\Scripts\python -m pytest test_main.py -v
```

Tests cover: NvmlReader init + poll structure, PsutilReader fields, LmStudioReader merged shape, Sampler lazy-init behavior, and _merge_models merging logic.

## Smoke test

Verifies every pynvml metric can be read without error:

```powershell
.\.venv\Scripts\python _smoke_test.py
```

Outputs each GPU's name, UUID, driver version, and all per-GPU metrics in a single pass.
