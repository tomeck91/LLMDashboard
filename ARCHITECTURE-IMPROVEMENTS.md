# GPUDashboard — Architecture Improvements Plan

**Date:** 2026-11-22  
**Source:** `architecture-review-20261122-1605.html`  
**Status:** Draft — awaiting user prioritisation

---

## Executive Summary

The dashboard is a functional single-file backend (`main.py`) + single-file frontend (`static/app.js`) monitoring 2× RTX 3090 GPUs, LM Studio models, and host stats. It has no tests beyond a hardware probe that duplicates production arithmetic.

**Key friction points:**
- `Sampler` interface is as wide as its implementation (2 methods + 9 public attrs + undocumented schema)
- Two LM Studio readers with overlapping purpose but different field names
- Unit conversions and status classification split across the seam
- No test infrastructure for `Sampler`/`LmsPoller`
- Polling cadence duplicated between backend constants and frontend literals

---

**Status:** C1 — Deepen the Sampler — **in progress**  
**Status:** C2 — Collapse the Two LM Studio Readers — **DONE** (merged HTTP + CLI into single `LmStudioReader`, unified output shape, frontend updated)

Implementation:
1. Added reader classes as inner classes in `main.py`
2. Refactored `Sampler.__init__` to store config only (lazy init)
3. Added `Sampler._start_thread()` for lazy thread startup
4. Moved `_read_gpu`, `_host_stats`, `_lmstudio_stats`, `_gpu_processes` into reader methods
5. Updated `Sampler._run()` to call `reader.poll()` instead of inline logic
6. Added `driver_version`, `count`, `names` as properties or included in snapshot
7. Replaced `sampler = Sampler()` with `get_sampler()` factory
8. Writing tests for each reader and for `Sampler.snapshot()`, `Sampler.history_payload()`

---

**Status:** C2 — Collapse the Two LM Studio Readers — **DONE**

Implementation:
1. Created unified `LmStudioReader` that merges HTTP API + CLI data sources
2. `_merge_models()` function joins by display name, preserves HTTP config fields, adds CLI runtime fields (status, ttl, device)
3. Frontend `renderHost()` simplified — single `lm` parameter instead of `lm, lms`
4. `renderModels()` updated to use unified shape (`size_gb` instead of `sizeBytes`)
5. API response: removed `lms` key; `lmstudio` now contains merged data
6. Tests: added `TestMergeModels` (5 tests covering HTTP-only, CLI-only, merge, fill-missing, multi-model)
7. Spec updated to reflect new unified output shape

---

## Improvement Candidates

### C1 — Deepen the Sampler (PRIORITY: HIGH)

**What changes**
- Make `Sampler` take its sources as constructor arguments instead of hard-wiring NVML/psutil/HTTP/CLI calls
- Move hardware quirks (NVML bytes vs str, fan/power `None` fallbacks) inside adapter modules
- Let the module own cadence, ring buffer and thread; callers inject fakes for testing

**Files**
- `main.py` L103–245 (`Sampler`, `LmsPoller`)
- Add new modules: `sources.py` (interface definitions) or inline adapters inside `main.py`

**Benefits**
- locality: hardware quirks in one place
- interface shrinks; implementation absorbs attrs
- two adapters justify the seam: real + fake for tests
- `_smoke_test.py` can exercise production code

**Risks**
- Requires refactoring all callers of `Sampler` to pass sources (only FastAPI endpoints currently)

---

### C2 — Collapse the Two LM Studio Readers (PRIORITY: HIGH)

**What changes**
- Introduce a single `Model` module with one shape: `{name, quant, size_gb, context_max, context_running, parallel}`
- HTTP probe and CLI poller become adapters feeding it
- Drop duplicate raw fields (`identifier`, `modelKey`, `publisher`, `ttl_ms`, `maxContextLength`, `quantization`)
- Frontend consumes one unified shape

**Files**
- `main.py` L54–86 (`LmsPoller._poll_ps`), L182–205 (`_lmstudio_stats`)
- `static/app.js` L63–96 (renderHost disambiguation)

**Benefits**
- one vocabulary for "loaded model"
- interface narrower than its deepest consumer
- locality: parse quirks stop leaking to app.js
- header/table disagreement impossible

**Risks**
- Reopens the decision recorded only in commit `695750f` ("use only lms ps --json")
- If HTTP probe is truly dead weight, delete it rather than adapterise it

---

### C3 — Move Unit Conversions + Status Classification to One Side (PRIORITY: HIGH)

**What changes**
- Pick a side:
  - **Option A (backend):** emit final units and classification; frontend renders only
  - **Option B (frontend):** emit raw values only; frontend owns all formatting
- Delete duplicate conversions (`MiB→GB`, `bytes→GB`, `fmtCtx` twice)
- Delete redundant fields

**Files**
- `main.py` L93–100 (`_format_ttl`), L69, L161–162, L176–177
- `static/app.js` L121, L231 (`fmtSize`), L61 + L232 (`fmtCtx`), L84 / L239 / L241 (status)

**Benefits**
- leverage: one conversion, every consumer
- locality: threshold changes touch one file
- deletion test passes: the wrappers vanish
- header/table disagreement impossible

**Risks**
- Option A contradicts spec.md L53 which mandates backend TTL formatting ("format as MM:SS")

---

### C4 — Make Render Path Testable Without Browser (PRIORITY: MEDIUM)

**What changes**
- Split each panel into pure functions from payload to plain data
- One thin renderer that turns data into markup
- Fix double `renderHost` call (L104 with 2 args, L203 with 3)

**Files**
- `static/app.js` L108–133 (card template), L43–48 (`seg()`), L104, L203

**Benefits**
- interface is the test surface: payload in, rows out
- no browser needed for formatting bugs
- one render per tick, not two
- keeps vanilla JS, no build step

---

### C5 — Replace Smoke Test with Real Tests (PRIORITY: MEDIUM)

**What changes**
- Once C1 is done, tests drive `snapshot()` and `history_payload()` with fake sources
- Keep hardware probe as environment check, separated from unit tests

**Files**
- `_smoke_test.py` → delete or repurpose as `env_check.py`

**Benefits**
- deletion test: duplicate arithmetic disappears
- tests cross the same seam callers do
- quirks (fan None, power-limit None) covered once

---

### C6 — Serve Polling Cadence Instead of Duplicating It (PRIORITY: LOW)

**What changes**
- Either drop `interval_s` from payload as dead weight
- Or have app.js read it and derive its intervals

**Files**
- `main.py` L20, L23–25, L245
- `static/app.js` L215, L254, L256
- `static/index.html` L179

**Benefits**
- locality: cadence change in one place
- or: interface loses a field nobody consumes

---

## Recommended Implementation Order

| Phase | Tasks | Rationale |
|---|---|---|
| **1** | C2 + C3 (backend) | Collapse LM Studio readers, then move units/status to backend (spec conflict noted). These are independent, high-impact. |
| **2** | C1 (backend) — **IN PROGRESS** | Deepen Sampler now that sources have a clear seam. Tests become possible. |
| **3** | C5 (tests) | Write tests for Sampler/LmsPoller using injected fakes. |
| **4** | C4 (frontend) | Extract view functions from templates; fix double `renderHost`. |
| **5** | C6 (cleanup) | Drop or consume `interval_s`. |

---

## Acceptance Criteria

After improvements:

- [ ] **C1: `Sampler` deepened** — `snapshot()` and `history_payload()` only; NVML/psutil/HTTP/CLI behind reader classes
- [ ] Tests hit `Sampler.snapshot()`, `Sampler.history_payload()` with fake readers
- [ ] One JSON shape for "loaded model"; no `identifier`/`modelKey`/`publisher`/`ttl_ms` in payloads
- [ ] VRAM unit consistent across cards (MiB) and charts (MiB) — or GB everywhere, but not both
- [ ] LMS status classification happens once; frontend receives pre-classified values or a simple enum
- [ ] `main.py` imports do not trigger NVML init or thread spawn
- [ ] No duplicate arithmetic between `_smoke_test.py` and production code

---

## Notes

- **No database** — all state in-memory (`deque(maxlen=300)`)
- **No auth** — read-only stats on port 8000, LAN-bound
- **One background sampler thread** — stays, only its inputs become injectable
- **pynvml over nvidia-smi** — kept as-is
- **Windows NVML noise policy** — `_gpu_processes` filter stays (util > 0 or memUtil > 0)

---

*End of plan.*
