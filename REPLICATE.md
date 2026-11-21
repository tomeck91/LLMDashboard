# GPUDashboard — Session Extract for LLM Replication

> Hand this to an LLM agent and ask it to replicate the project session: build the
> dashboard from scratch on a similar machine, following the milestones in order.
> Everything below is verified to work on the reference machine (2× RTX 3090 24 GB,
> Windows 11, driver 617.42, nvidia-ml-py 13.615.71, LM Studio server on 127.0.0.1:1234).

## 1. Mission

Build a **live GPU/LLM monitoring dashboard** (header titled **"LLM Dashboard"**) for a
dual-GPU workstation:

- Backend: Python + **FastAPI + pynvml + psutil**, one file (`main.py`)
- Frontend: **vanilla HTML/JS + Chart.js from CDN** — no build step, no framework, no DB
- Minimal files, minimal dependencies (stdlib over new pip packages)
- Server binds **0.0.0.0:8000** (LAN-accessible, read-only, no auth — trusted home network)
- Runs **hidden** (no console window), no per-request log spam, double-start guarded
- Git-managed; user requests explicit `git commit` milestones — never commit unless asked
- Communication in English

## 2. Environment constraints (reference machine)

- Windows 11, PowerShell 5.1 (NonInteractive): `;` not `&&`, `Invoke-WebRequest -UseBasicParsing`,
  no `timeout /t` (use `Start-Sleep`), UTF-8 always
- Python venv in `.venv`; `requirements.txt` = `fastapi, uvicorn, nvidia-ml-py, psutil`
- LM Studio server on `http://127.0.0.1:1234` with a model loaded
- An OpenCode agent runs on the same box; its shell **hangs if it runs a long-running bat
  directly** (inherited output pipe). Simulate a double-click instead:
  `& powershell -NoProfile -Command "Start-Process -WindowStyle Hidden cmd.exe -ArgumentList '/c','<bat>'"`
  then poll the port/API.

## 3. Final requirements

### 3.1 API contract

`GET /api/gpus` →
```json
{
  "t": 1795200000.0,
  "driver_version": "617.42",
  "gpus": [{
    "index": 0, "name": "NVIDIA GeForce RTX 3090", "uuid": "GPU-...",
    "util_gpu": 42, "util_mem": 15,
    "mem_used_mib": 15500, "mem_total_mib": 24576,
    "temp_c": 62, "power_w": 165.8, "power_limit_w": 350.0,
    "clock_graphics_mhz": 1980, "clock_memory_mhz": 1219,
    "fan_pct": 34,
    "procs": [{"pid": 42928, "name": "llama-server.exe", "sm_pct": 63}]
  }],
  "host": {"cpu_pct": 12.0, "ram_used_gb": 45.2, "ram_total_gb": 63.7, "ram_pct": 71.0, "boot_time": 1795179510.27},
  "lmstudio": {"online": true, "loaded": [{
    "name": "Qwen3.8 27B UD", "quant": "Q4_K_S", "context": 128000,
    "size_gb": 15.2, "max_context": 262144
  }]}
}
```

`GET /api/history?minutes=5` (clamped 1..30) →
```json
{"interval_s": 1.0, "t": [epoch...], "gpus": [{
  "index": 0, "name": "...",
  "util_gpu": [...], "mem_used_mib": [...], "temp_c": [...], "power_w": [...]
}]}
```

### 3.2 UI spec (final state)

- **Header**: blinking block cursor, `LLM Dashboard` title, host CPU %, RAM `used/total GB (%)`,
  MODEL = first loaded LM Studio model + `(quant · size GB · ctx Nk)` + `+N more`,
  hover tooltip lists all models with running context; "none loaded" / "LM Studio offline" states;
  driver version + live clock right-aligned
- **Per-GPU card**: utilization segmented bars (GPU + MEM BUS) with % values; VRAM
  `used / total GB (pct)` + segmented bar; PROC line (active processes, name + SM %, hover for PIDs,
  "none" when idle); 2×2 dot-leader grid: TEMP, POWER (`draw/limit W`), CLOCK, FAN (`N/A` if unsupported)
- **Charts** (4, 2×2): GPU Utilization (%), VRAM Used (MiB), Temperature (°C), Power Draw (W) —
  5-minute window, one line per GPU
- **Footer**: `up HH:MM:SS` (real system uptime from `psutil.boot_time`), `N × GPU names`,
  `refresh 1s`, version tag right-aligned
- **Refresh**: cards every 1 s (`/api/gpus`), charts every 5 s (`/api/history`);
  red cursor + "connection lost — retrying…" on fetch failure

## 4. Architecture & key decisions

1. **One background sampler thread** in `main.py`: every 1 s reads all GPUs (NVML), host (psutil),
   and — every 5 s only — LM Studio (`urllib` stdlib, 2 s timeout). Results go into
   `deque(maxlen=300)` (5 min) + `latest`; a `threading.Lock` guards both. No DB.
2. **pynvml over nvidia-smi CLI.** Wrap every string-returning call in
   `_to_str()` (bytes on old pynvml, str on new).
3. **Active processes**: use `nvmlDeviceGetProcessUtilization(h, 0)` and keep samples with
   `smUtil > 0 or memUtil > 0`. **Do NOT use** `nvmlDeviceGetComputeRunningProcesses` on Windows —
   it lists every app with a GPU context (explorer, dwm, Discord… 30+ entries of noise).
   Resolve names via `psutil.Process(pid).name()`.
4. **Per-process VRAM is `None`** on the reference driver — never show GB per process.
5. **LM Studio "loaded" rule**: poll `/api/v1/models`; a model is loaded when its
   `loaded_instances` array is non-empty. Read `display_name`, `quantization.name`,
   `loaded_instances[].config.context_length`, `size_bytes`, `max_context_length`.
6. **Power limit** via `nvmlDeviceGetPowerManagementLimit` (µW → W), cached at init, `None`-safe.
7. **Fan speed** may throw `NVMLError` on some cards → `None` → frontend shows `N/A`.
8. **`psutil.cpu_percent(interval=None)` must be primed** at startup (first call returns 0.0).
9. **Uvicorn**: `host="0.0.0.0"`, `port=8000`, `log_level="warning"` (kills per-request INFO spam).
10. **Static mount**: `app.mount("/", StaticFiles(directory="static", html=True))` — API routes
    registered first, static last.
11. **Hidden launch**: `start.bat` / `stop.bat` / `restart.bat` in the repo. start = no-op if port
    8000 busy, else launch python detached/hidden (output → `server.log`/`server.err.log`) and open
    browser; stop = kill PID owning port 8000; restart = stop + start. Note: the venv shim
    re-execs real python, so **two python processes is normal** — the child owns the port.
12. **Frontend**: single `app.js` + `index.html` (CSS inline). Card/charts DOM rebuilt per tick
    (cheap at 1 Hz); Chart.js instances created once and updated with `chart.update("none")`.
    Segmented bars = N flex `<i>` cells (24), filled by count, `currentColor` for the fill.

## 5. Session timeline (replicate in this order)

| # | Milestone (git commit message) | What it added |
|---|---|---|
| 1 | Initial version: FastAPI + pynvml GPU dashboard with live stats and charts | Sampler thread, `/api/gpus`, `/api/history`, cards (util, VRAM, temp, power, clock, fan), 4 charts, smoke test script |
| 2 | Add start/stop/restart batch files | Hidden server management, double-start guard |
| 3 | Run server hidden (no console window) and silence per-request logging | Detached launch, `log_level="warning"` |
| 4 | Add host CPU/RAM to header, rename to LLM Dashboard, uniform utilization rows | psutil host stats folded into sampler + same response; header stats |
| 5 | Show loaded LM Studio model in header (polls local server API every 5s) | LM Studio poller, MODEL header stat |
| 6 | Add active GPU processes, power TDP limit and model details; bind to 0.0.0.0 for LAN access | `nvmlDeviceGetProcessUtilization` procs, `power_limit_w`, model size/max-ctx in header, LAN bind |
| 7 | Restyle to btop/terminal theme: segmented bars, dot-leader rows, mono font, uptime footer | `psutil.boot_time()` → `up HH:MM:SS`, clock, footer bar, mono font, 24-cell seg bars, dot leaders |
| 8 | Restyle to cyberpunk theme: neon palette, HUD corner brackets, scanlines, glow effects | Final visual (spec below) |

Each milestone was verified live (API via PowerShell `Invoke-RestMethod`, UI via browser
screenshot) before the user requested the commit.

## 6. Final theme spec (cyberpunk)

- **Palette**: `--bg:#05060f` (deep navy-black), `--panel:rgba(10,13,28,.92)`,
  `--pink:#ff2a6d` (hot pink, primary neon), `--cyan:#00f0ff`, `--violet:#b537f2`,
  `--acid:#f9f002`, `--muted:#6b7394`, `--text:#d8e0f0`. GPU 0 = pink, GPU 1 = cyan.
- **Level colors** (utilization bars + % values): `<60%` cyan, `≥60%` acid, `≥85%` pink.
  Temp value: `≥70°C` acid, `≥80°C` pink.
- **Effects**: faint 34 px cyber-grid `background-image` on body; fixed CRT scanline overlay
  (`repeating-linear-gradient`, 1 px dark line / 3 px, `pointer-events:none`, z-index 999);
  neon `text-shadow` glows on title/model/PROC/uptime/version; title has a subtle CSS
  flicker keyframe (brief opacity dips at 91–97 %); blinking pink block cursor (9×15 px, steps(1)).
- **Panels**: 1 px `rgba(0,240,255,.22)` border + HUD **corner brackets** via `::before`/`::after`
  (16 px L-shapes, 2 px neon borders, `drop-shadow` glow; cards pink top-left + cyan bottom-right,
  charts violet top-left + pink bottom-right); soft outer glow `box-shadow`.
- **Typography**: monospace stack `"Cascadia Mono","Cascadia Code","JetBrains Mono",Consolas,monospace`,
  13 px, `tabular-nums`; panel titles uppercase, 1.5 px letter-spacing, red-pink superscript
  index (¹²³⁴⁵⁶), `//` separator in violet between GPU index and card name.
- **Rows**: dot-leader layout `LABEL ⋯⋯ value` (flex row, `border-bottom:1px dotted` middle span).
- **Charts**: neon pink/cyan 1.5 px straight lines (`tension:0`, no fill, no points),
  violet-tinted grid `rgba(181,55,242,.14)`, cyan-tinted axis border, mono 10 px ticks `#6b7394`.

## 7. Gotchas & lessons (cost real debugging time)

- **pynvml string types**: bytes vs str varies by version → `_to_str()` everywhere.
- **Windows NVML process lists are noise** (see §4.3) — the utilization API is the fix.
- **`usedGpuMemory` per process = None** on driver 617.42 — don't build features on it.
- **LM Studio has no passive stats endpoint** — tokens/sec & TTFT only exist inside responses to
  requests you make; skip live inference stats (verified against the docs).
- **Agent shell + long-running bats** → hang (inherited pipe). Use the `Start-Process -WindowStyle
  Hidden cmd /c` simulation (§2).
- **PowerShell 5.1**: no `&&`, no `timeout /t`, `Invoke-WebRequest` needs `-UseBasicParsing`;
  python/pip stderr shows as red noise (harmless).
- **LAN bind**: first 0.0.0.0 bind may trigger a Windows Firewall prompt ("Allow on private
  networks?") — that's the usual cause of "works locally, not from phone".
- **Git on Windows**: LF→CRLF warnings on every commit are harmless normalization.
- **Something on the LAN polls `GET /health` every ~30 s** → 404, invisible at
  `log_level="warning"`; harmless (a `/health` endpoint was offered, never requested).
- **Self-restart problem**: an agent cannot kill its own process tree mid-turn; use a timed
  detached bat (sleep → taskkill → relaunch) when a restart is needed.

## 8. Verification checklist (after each milestone)

1. `Invoke-RestMethod http://127.0.0.1:8000/api/gpus` — 2 GPUs, all fields present and sane
   (util 0–100, mem used < total, temp ~30–85, power 30–350 W, procs list, lmstudio block).
2. `Invoke-RestMethod "http://127.0.0.1:8000/api/history?minutes=5"` — `t` grows over time.
3. Served files contain the new markup (`Invoke-WebRequest` + regex check).
4. LAN: `Invoke-RestMethod http://<pc-ip>:8000/api/gpus` from another interface/IP.
5. Visual: browser screenshot of the page (via a browser-automation tool if available).
6. `git status --short` clean before/after a requested commit.

## 9. Appendix: browser tooling side-quest (same session)

Setup of OpenCode's `browse` tool (drives the user's real Chrome):

- Official installer: `irm https://opencode.ai/browse/install.ps1 | iex` → installs
  `browse.exe` to `%USERPROFILE%\.local\bin`, then runs `browse.exe install` (PATH, agent MCP
  config, opens a pairing page in Chrome, waits ≤90 s for connect).
- **Windows Defender ASR** ("Use advanced protection against ransomware") can block execution of
  the freshly downloaded binary (files created by a network process). Fix is user-side: disable
  that ASR rule or add a file exclusion in Windows Security, then re-run the installer.
- **Architecture**: `browse.exe` runs as an **orphan daemon relay** at `127.0.0.1:19988`
  (survives agent restarts); the Chrome extension connects to it; MCP tools talk to the relay.
- **Pairing is session-scoped**: when the extension's WebSocket closes (code 1001 "going away" —
  Chrome restart, or MV3 service-worker idle-kill after ~30 s), the relay invalidates the pairing;
  a plain reconnect does NOT re-pair. Symptoms: relay log shows `extension.connected` but MCP says
  "installed but not paired". Fix: re-run `browse install` (auto-pairs in <1 s if the extension
  is connected) or `browse pair` (prints a code for the extension's Options page).
- Diagnose with: `browse.exe status` (CLI), relay log at `~/.local/state/browse/logs/relay.log`,
  state in `~/.local/state/browse` (`relay.json`, `pairing-codes.json`).
