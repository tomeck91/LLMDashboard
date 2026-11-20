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

Then open **http://localhost:8000** (the .bat files also do this automatically).

The server binds to `0.0.0.0`, so it's also reachable from other devices on your LAN:
**http://\<pc-name\>:8000** or **http://\<pc-ip\>:8000** (find the IP with `ipconfig`).
Note: this is read-only stats with no authentication — anyone on your network can view it.

### Quick way: batch files

| File | Action |
|---|---|
| `start.bat` | Starts the server **hidden** (no console window), opens the browser. No-op if already running |
| `stop.bat` | Kills whatever is listening on port 8000 (safe to run when already stopped) |
| `restart.bat` | stop + start |

Server output goes to `server.log` / `server.err.log` in this folder (per-request logging is off; only warnings/errors are recorded).

## What it shows

- Header: host CPU % and RAM (used/total, %), plus the model currently loaded in LM Studio with quantization, size and max context (polled from `http://127.0.0.1:1234/api/v1/models` every 5 s; shows "LM Studio offline" if the server isn't running)
- Per-GPU cards (1 s refresh): utilization %, VRAM bar, active GPU processes (name + SM %), temperature, power draw (with TDP limit), graphics clock, fan speed
- Live charts (5 min rolling window, 1 s samples): GPU util %, VRAM used, temperature, power — one line per GPU

## Files

| File | Purpose |
|---|---|
| `main.py` | FastAPI app + background NVML sampler thread (ring buffer, last 5 min) |
| `static/index.html` | UI shell + styles |
| `static/app.js` | Polling logic + Chart.js charts |
| `_smoke_test.py` | Standalone diagnostic: verifies every pynvml metric works (`.\.venv\Scripts\python _smoke_test.py`) |

## API

- `GET /api/gpus` — latest snapshot of all GPUs (JSON)
- `GET /api/history?minutes=5` — time series for charts (1 s samples, max 30 min)
