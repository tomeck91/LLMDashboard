const GPU_COLORS = ["#ff2a6d", "#00f0ff"]; // hot pink / neon cyan
const SUP = ["\u00b9", "\u00b2", "\u00b3", "\u00b4", "\u2075", "\u2076", "\u2077", "\u2078", "\u2079", "\u2070"];
const METRICS = [
  { key: "util_gpu", label: "GPU Utilization (%)" },
  { key: "mem_used_mib", label: "VRAM Used (MiB)" },
  { key: "temp_c", label: "Temperature (\u00b0C)" },
  { key: "power_w", label: "Power Draw (W)" },
];

const cardsEl = document.getElementById("cards");
const chartsEl = document.getElementById("charts");
const statusEl = document.getElementById("status");
const metaEl = document.getElementById("driver-meta");
const dotEl = document.getElementById("live-dot");
const clockEl = document.getElementById("clock");
const uptimeEl = document.getElementById("uptime");
const footGpusEl = document.getElementById("foot-gpus");
const hostCpuEl = document.getElementById("host-cpu");
const hostRamEl = document.getElementById("host-ram");
const hostModelEl = document.getElementById("host-model");

let charts = {}; // metric key -> Chart instance

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

function fmtTime(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour12: false });
}

// level color: cyan -> acid yellow -> hot pink
function levelColor(pct) {
  if (pct >= 85) return "var(--pink)";
  if (pct >= 60) return "var(--acid)";
  return "var(--cyan)";
}

// terminal segmented bar: N block cells, filled up to pct
function seg(pct, cells = 24, color = "currentColor") {
  const on = Math.max(0, Math.min(cells, Math.round((pct / 100) * cells)));
  let out = "";
  for (let i = 0; i < cells; i++) out += `<i${i < on ? ' class="on"' : ""}></i>`;
  return `<div class="seg" style="color:${color}">${out}</div>`;
}

// ---------- host (header) ----------
function renderHost(host, lm) {
  if (!host) return;
  hostCpuEl.textContent = `${Math.round(host.cpu_pct)}%`;
  hostRamEl.textContent = `${host.ram_used_gb.toFixed(1)}/${host.ram_total_gb.toFixed(0)} GB (${Math.round(host.ram_pct)}%)`;
  if (host.boot_time) {
    const up = Math.floor(Date.now() / 1000 - host.boot_time);
    const d = Math.floor(up / 86400);
    const hms = new Date(up * 1000).toISOString().slice(11, 19);
    uptimeEl.textContent = `up ${d > 0 ? d + "d " : ""}${hms}`;
  }
  if (!lm) return;
  if (lm.online && lm.loaded.length) {
    const fmtCtx = (n) => (n >= 1000 ? Math.round(n / 1000) + "k" : String(n));
    const detail = (m) => {
      const parts = [];
      if (m.quant) parts.push(m.quant);
      if (m.size_gb) parts.push(m.size_gb + " GB");
      if (m.max_context) parts.push("ctx " + fmtCtx(m.max_context));
      return parts.length ? ` (${parts.join(" · ")})` : "";
    };
    const m = lm.loaded[0];
    const extra = lm.loaded.length > 1 ? ` +${lm.loaded.length - 1} more` : "";
    hostModelEl.textContent = m.name + detail(m) + extra;
    hostModelEl.title = lm.loaded
      .map((x) => `${x.name}${detail(x)}${x.context ? `, running ctx ${x.context}` : ""}`)
      .join("\n");
  } else {
    hostModelEl.textContent = lm.online ? "none loaded" : "LM Studio offline";
    hostModelEl.title = "";
  }
}

// ---------- cards ----------
function renderCards(data) {
  statusEl.style.display = "none";
  metaEl.textContent = `driver ${data.driver_version}`;
  dotEl.style.background = "var(--green)";
  renderHost(data.host, data.lmstudio);
  const names = [...new Set(data.gpus.map((g) => g.name.replace("NVIDIA GeForce ", "")))];
  footGpusEl.textContent = `${data.gpus.length} \u00d7 ${names.join(" + ")}`;

  cardsEl.innerHTML = data.gpus.map((g, i) => {
    const color = GPU_COLORS[i % GPU_COLORS.length];
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
        </div>
        <div class="row"><span class="lbl">VRAM</span><span class="dots"></span><b>${(g.mem_used_mib / 1024).toFixed(1)} / ${(g.mem_total_mib / 1024).toFixed(0)} GB (${memPct.toFixed(0)}%)</b></div>
        ${seg(memPct, 24, color)}
        <div class="row proc-row" title="${procTip}"><span class="lbl">PROC</span><span class="dots"></span><b>${procText}</b></div>
        <div class="stats-grid">
          <div class="row"><span class="lbl">TEMP</span><span class="dots"></span><b class="${tempCls}">${g.temp_c}&deg;C</b></div>
          <div class="row"><span class="lbl">POWER</span><span class="dots"></span><b>${g.power_limit_w ? `${Math.round(g.power_w)}/${g.power_limit_w} W` : g.power_w + " W"}</b></div>
          <div class="row"><span class="lbl">CLOCK</span><span class="dots"></span><b>${g.clock_graphics_mhz} MHz</b></div>
          <div class="row"><span class="lbl">FAN</span><span class="dots"></span><b>${g.fan_pct == null ? "N/A" : g.fan_pct + "%"}</b></div>
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
        plugins: {
          legend: { labels: { color: "#8a92b8", boxWidth: 10, boxHeight: 10, font: { family: MONO, size: 10 } } },
          tooltip: { titleFont: { family: MONO, size: 10 }, bodyFont: { family: MONO, size: 10 } },
        },
        scales: {
          x: {
            ticks: { color: "#6b7394", maxTicksLimit: 6, autoSkip: true, font: { family: MONO, size: 10 } },
            grid: { color: "rgba(181, 55, 242, 0.14)" },
            border: { color: "rgba(0, 240, 255, 0.25)" },
          },
          y: {
            ticks: { color: "#6b7394", font: { family: MONO, size: 10 } },
            grid: { color: "rgba(181, 55, 242, 0.14)" },
            border: { color: "rgba(0, 240, 255, 0.25)" },
          },
        },
      },
    });
  }
}

function updateCharts(hist) {
  ensureCharts();
  const labels = hist.t.map(fmtTime);
  for (const m of METRICS) {
    const chart = charts[m.key];
    chart.data.labels = labels;
    chart.data.datasets = hist.gpus.map((g, i) => ({
      label: `GPU ${g.index}`,
      data: g[m.key],
      borderColor: GPU_COLORS[i % GPU_COLORS.length],
      fill: false,
      pointRadius: 0,
      borderWidth: 1.5,
      tension: 0,
    }));
    chart.update("none");
  }
}

// ---------- polling ----------
async function tick() {
  clockEl.textContent = new Date().toLocaleTimeString([], { hour12: false });
  try {
    const data = await fetchJSON("/api/gpus");
    if (data.gpus.length) renderCards(data);
  } catch (e) {
    dotEl.style.background = "var(--red)";
    metaEl.textContent = "connection lost — retrying…";
  }
}

async function tickHistory() {
  try {
    const hist = await fetchJSON("/api/history?minutes=5");
    if (hist.t.length > 1) updateCharts(hist);
  } catch (e) { /* transient; next tick retries */ }
}

tick();
setInterval(tick, 1000);
tickHistory();
setInterval(tickHistory, 5000);
