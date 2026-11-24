const GPU_COLORS = ["#ffaa22", "#ffdd88"]; // amber / warm amber-light
const SUP = ["\u00b9", "\u00b2", "\u00b3", "\u00b4", "\u2075", "\u2076", "\u2077", "\u2078", "\u2079", "\u2070"];
const METRICS = [
  { key: "util_gpu", label: "GPU Utilization (%)", tickSuffix: "%" },
  { key: "mem_used_mib", label: "VRAM Used (MiB / GB)", toGB: true, tickSuffix: "GB" },
  { key: "temp_c", label: "Temperature (°C)", tickSuffix: "°C" },
  { key: "power_w", label: "Power Draw (W)", tickSuffix: " W" },
];

const cardsEl = document.getElementById("cards");
const chartsEl = document.getElementById("charts");
const modelsEl = document.getElementById("models");
const statusEl = document.getElementById("status");
const metaEl = document.getElementById("driver-meta");
const dotEl = document.getElementById("live-dot");
const clockEl = document.getElementById("clock");
const uptimeEl = document.getElementById("uptime");
const footGpusEl = document.getElementById("foot-gpus");
const hostCpuEl = document.getElementById("host-cpu");
const hostRamEl = document.getElementById("host-ram");

let charts = {}; // metric key -> Chart instance

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

function fmtTime(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour12: false });
}

// level color: amber -> warn -> hot red
function levelColor(pct) {
  if (pct >= 85) return "var(--hot)";
  if (pct >= 60) return "var(--warn)";
  return "var(--amber)";
}

// terminal segmented bar: N block cells, filled up to pct
function seg(pct, cells = 24, color = "currentColor") {
  const on = Math.max(0, Math.min(cells, Math.round((pct / 100) * cells)));
  let out = "";
  for (let i = 0; i < cells; i++) out += `<i${i < on ? ' class="on"' : ""}></i>`;
  return `<div class="seg" style="color:${color}">${out}</div>`;
}

// ---------- host (header) ----------
function renderHost(host) {
  if (!host) return;
  hostCpuEl.textContent = `${Math.round(host.cpu_pct)}%`;
  hostRamEl.textContent = `${host.ram_used_gb.toFixed(1)}/${host.ram_total_gb.toFixed(0)} GB (${Math.round(host.ram_pct)}%)`;
  if (host.boot_time) {
    const up = Math.floor(Date.now() / 1000 - host.boot_time);
    const d = Math.floor(up / 86400);
    const hms = new Date(up * 1000).toISOString().slice(11, 19);
    uptimeEl.textContent = `up ${d > 0 ? d + "d " : ""}${hms}`;
  }
}

// ---------- cards ----------
function renderCards(data) {
  statusEl.style.display = "none";
  metaEl.textContent = `driver ${data.driver_version}`;
  // Track max NVML power limit for chart Y-axis
  maxPowerW = Math.max(...data.gpus.map(g => g.power_limit_w || 0), 350);
  const names = [...new Set(data.gpus.map((g) => g.name.replace("NVIDIA GeForce ", "")))];
  footGpusEl.textContent = `${data.gpus.length} \u00d7 ${names.join(" + ")}`;

  cardsEl.innerHTML = data.gpus.map((g, i) => {
    const color = "#ffaa22"; // warm amber for all bars
    const memPct = g.mem_total_mib ? (g.mem_used_mib / g.mem_total_mib) * 100 : 0;
    const tempCls = g.temp_c >= 80 ? "hot" : g.temp_c >= 70 ? "warn" : "";
    const procTip = g.procs.map((p) => `${p.name} (PID ${p.pid})`).join(", ");
    const procText = g.procs.length ? g.procs.map((p) => `${p.name} (${p.sm_pct}%)`).join(", ") : "none";
    return `
      <div class="card">
        <div class="card-head"><span class="pnum">${SUP[i % SUP.length]}</span>GPU ${g.index}<span class="sep">//</span><span class="gpu-name">${g.name.replace("NVIDIA GeForce ", "")}</span></div>
        <div class="util-grid">
          <div class="util-line"><label>GPU</label>${seg(g.util_gpu, 24, levelColor(g.util_gpu))}<b style="color:${levelColor(g.util_gpu)}">${g.util_gpu}%</b></div>
          <div class="util-line"><label>MEM</label>${seg(g.util_mem, 24, levelColor(g.util_mem))}<b style="color:${levelColor(g.util_mem)}">${g.util_mem}%</b></div>
          <div class="util-line detail"><label>VRAM</label>${seg(memPct, 24, color)}<b>${(g.mem_used_mib / 1024).toFixed(1)} GB (${memPct.toFixed(0)}%)</b></div>
          <div class="util-line detail"><label>POWER</label>${seg(Math.min(100, (g.power_w / (g.power_limit_w || 1)) * 100), 24, levelColor(g.power_w / (g.power_limit_w || 1) * 100))}<b>${g.power_limit_w ? `${Math.round(g.power_w)}/${g.power_limit_w} W` : g.power_w + " W"}</b></div>
        </div>
        <div class="row proc-row" title="${procTip}"><span class="lbl">PROC</span><span class="dots"></span><b>${procText}</b></div>
        <div class="stats-grid">
          <div class="row"><span class="lbl">TEMP</span><span class="dots"></span><b class="${tempCls}">${g.temp_c}&deg;C</b></div>
          <div class="row"><span class="lbl">CORE</span><span class="dots"></span><b>${g.clock_graphics_mhz} MHz</b></div>
          <div class="row"><span class="lbl">FAN</span><span class="dots"></span><b>${g.fan_pct == null ? "N/A" : g.fan_pct + "%"}</b></div>
          <div class="row"><span class="lbl">MEM</span><span class="dots"></span><b>${g.clock_memory_mhz} MHz</b></div>
        </div>
      </div>`;
  }).join("");
}

// ---------- charts ----------
const MONO = '"Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace';

function ensureCharts() {
  for (let n = 0; n < METRICS.length; n++) {
    const m = METRICS[n];
    if (charts[m.key]) continue;
    const panel = document.createElement("div");
    panel.className = "chart-panel";
    panel.innerHTML = `<h3><span class="pnum">${SUP[(n + 2) % SUP.length]}</span>${m.label}</h3><div class="chart-box"><canvas></canvas></div>`;
    chartsEl.appendChild(panel);

    const ctx = panel.querySelector("canvas").getContext("2d");
    charts[m.key] = new Chart(ctx, {
      type: "line",
      data: { labels: [], datasets: [] },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            ticks: { color: "#8a7a52", maxTicksLimit: 6, autoSkip: true, font: { family: MONO, size: 10 } },
            grid: { color: "rgba(136, 85, 0, 0.14)" },
            border: { color: "rgba(255, 170, 34, 0.25)" },
          },
          y: {
            ticks: { color: "#8a7a52", font: { family: MONO, size: 10 } },
            grid: { color: "rgba(136, 85, 0, 0.14)" },
            border: { color: "rgba(255, 170, 34, 0.25)" },
          },
        },
        plugins: {
          legend: { labels: { color: "#b8a078", boxWidth: 10, boxHeight: 10, font: { family: MONO, size: 10 } } },
          tooltip: { titleFont: { family: MONO, size: 10 }, bodyFont: { family: MONO, size: 10 } },
        },
      },
    });
  }
}

// Per-metric fixed Y range: [min, max] — avoids auto-scale jitter
const METRIC_RANGE = {
  util_gpu:       [0, 100],
  util_mem:       [0, 100],
  mem_used_mib:   [0, 24],             // 0–24 GB (display)
  temp_c:         [0, 100],
  power_w:        null,                // set from NVML power_limit_w once known
};

// Track max power limit across all GPUs (from NVML)
let maxPowerW = 350; // fallback to common max if not yet known

function updateCharts(hist) {
  ensureCharts();
  const labels = hist.t.map(fmtTime);
  for (const m of METRICS) {
    const chart = charts[m.key];
    chart.data.labels = labels;
    chart.data.datasets = hist.gpus.map((g, i) => ({
      label: `GPU ${g.index}`,
      data: m.toGB ? g[m.key].map(v => +(v / 1024).toFixed(1)) : g[m.key],
      borderColor: GPU_COLORS[i % GPU_COLORS.length],
      fill: false,
      pointRadius: 0,
      borderWidth: 1.5,
      tension: 0,
    }));

    // Compute fixed Y range for metrics without a hard-coded range
    if (METRIC_RANGE[m.key] === null) {
      const allVals = chart.data.datasets.flatMap(d => d.data);
      if (allVals.length === 0) {
        // No data yet — use sensible defaults
        if (m.key === "power_w") {
          chart.options.scales.y.suggestedMin = 0;
          chart.options.scales.y.suggestedMax = maxPowerW;
        } else {
          chart.options.scales.y.suggestedMin = 0;
          chart.options.scales.y.suggestedMax = 100;
        }
      } else {
        const lo = Math.min(...allVals);
        const hi = Math.max(...allVals);
        // Power: cap max at NVML power_limit_w, don't auto-scale beyond it
        if (m.key === "power_w") {
          chart.options.scales.y.suggestedMin = 0;
          chart.options.scales.y.suggestedMax = maxPowerW;
        } else {
          const pad = Math.max(0.5, (hi - lo) * 0.05); // 5% padding
          chart.options.scales.y.suggestedMin = Math.max(0, +(lo - pad).toFixed(1));
          chart.options.scales.y.suggestedMax = +(hi + pad).toFixed(1);
        }
      }
    } else {
      const [lo, hi] = METRIC_RANGE[m.key];
      chart.options.scales.y.suggestedMin = lo;
      chart.options.scales.y.suggestedMax = hi;
    }

    // Add unit suffix to y-axis ticks
    if (m.tickSuffix) {
      chart.options.scales.y.ticks.callback = (value) => value + m.tickSuffix;
    } else {
      chart.options.scales.y.ticks.callback = null;
    }

    chart.update("none");
  }
}

// ---------- polling ----------
async function tick() {
  clockEl.textContent = new Date().toLocaleTimeString([], { hour12: false });
  try {
    const data = await fetchJSON("/api/gpus");
    dotEl.classList.remove("offline");
    if (data.gpus.length) renderCards(data);           // sets "driver X" meta
    else metaEl.textContent = data.driver_version || ""; // "initializing..." or init error
    renderHost(data.host);
    renderModels(data.lmstudio);
  } catch (e) {
    dotEl.classList.add("offline");
    metaEl.textContent = "connection lost — retrying…";
  }
}

async function tickHistory() {
  try {
    const hist = await fetchJSON("/api/history?minutes=5");
    if (hist.t.length > 1) updateCharts(hist);
  } catch (e) { /* transient; next tick retries */ }
}

// ---------- models panel ----------
function renderModels(lm) {
  if (!modelsEl.querySelector("table")) {
    modelsEl.innerHTML = `<table>
      <thead><tr><th>MODEL</th><th>STATUS</th><th>GEN</th><th>SIZE</th><th>QUANT</th><th>CTX</th><th>TTL</th></tr></thead>
      <tbody id="models-tbody"></tbody>
    </table>`;
  }
  const tbody = modelsEl.querySelector("#models-tbody");
  if (!lm || !lm.loaded.length) {
    const msg = lm && lm.online ? "no model loaded" : "LM Studio offline";
    tbody.innerHTML = `<tr><td colspan="7" class="size">${msg}</td></tr>`;
    return;
  }
  const fmtSize = (gb) => gb != null ? gb.toFixed(1) + " GB" : "—";
  const fmtCtx = (n) => n >= 1000 ? (n / 1000).toFixed(0) + "k" : String(n);
  const order = { generating: 0, processingPrompt: 0, loaded: 0, loading: 1, idle: 2, unloading: 3 };
  const sorted = [...lm.loaded].sort((a, b) => (order[a.status] ?? 2) - (order[b.status] ?? 2));
  const BADGE = { generating: "loaded", processingPrompt: "loaded", loaded: "loaded", loading: "loading", unloading: "unloading" };
  tbody.innerHTML = sorted.map((m) => {
    const s = m.status || "";
    const cls = BADGE[s] || "idle";
    const badgeLabel = s === "processingPrompt" ? "prompt" : s;
    const ttl = m.ttl_remaining || "";
    // last finished request (from `lms log stream --stats`)
    const st = m.stats;
    const gen = st && st.gen_tps != null ? `${st.gen_tps.toFixed(1)} t/s` : "—";
    const tip = st ? `last request ${fmtTime(st.t)}: prompt ${st.prompt_tokens} tokens, generated ${st.gen_tokens} tokens in ${(st.total_s ?? 0).toFixed(1)}s` : "no finished request seen yet";
    return `<tr>
      <td>${m.name}</td>
      <td><span class="badge ${cls}">${badgeLabel}</span></td>
      <td class="gen" title="${tip}">${gen}</td>      <td class="size">${fmtSize(m.size_gb)}</td>
      <td>${m.quant || "—"}</td>
      <td>${m.context ? fmtCtx(m.context) : "—"}</td>
      <td>${ttl || "—"}</td>
    </tr>`;
  }).join("");
}

tick();
setInterval(tick, 1000);
tickHistory();
setInterval(tickHistory, 5000);
