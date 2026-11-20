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

Then open **http://localhost:8000**.

## What it shows

- Per-GPU cards (1 s refresh): utilization %, VRAM bar, temperature, power draw, graphics clock, fan speed
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
