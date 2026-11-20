const GPU_COLORS = ["#22d3ee", "#a78bfa"];
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

let charts = {}; // metric key -> Chart instance

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

function fmtTime(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour12: false });
}

function hexToRgba(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

// ---------- cards ----------
function renderCards(data) {
  statusEl.style.display = "none";
  metaEl.textContent = `driver ${data.driver_version}`;
  dotEl.style.background = "#22c55e";

  cardsEl.innerHTML = data.gpus.map((g, i) => {
    const color = GPU_COLORS[i % GPU_COLORS.length];
    const memPct = g.mem_total_mib ? (g.mem_used_mib / g.mem_total_mib) * 100 : 0;
    return `
      <div class="card">
        <div class="card-head">
          <span class="gpu-dot" style="background:${color}"></span>
          GPU ${g.index} &middot; ${g.name.replace("NVIDIA GeForce ", "")}
        </div>
        <div class="util-row">
          <div class="util-big">${g.util_gpu}<span>%</span></div>
          <div class="util-label">GPU UTILIZATION<br>MEM ${g.util_mem}%</div>
        </div>
        <div class="vram-bar"><i style="width:${memPct.toFixed(1)}%; background:${color}"></i></div>
        <div class="vram-text">${(g.mem_used_mib / 1024).toFixed(1)} / ${(g.mem_total_mib / 1024).toFixed(0)} GB VRAM</div>
        <div class="stats-grid">
          <div class="stat"><label>TEMP</label><b>${g.temp_c}&deg;C</b></div>
          <div class="stat"><label>POWER</label><b>${g.power_w} W</b></div>
          <div class="stat"><label>CLOCK</label><b>${g.clock_graphics_mhz} MHz</b></div>
          <div class="stat"><label>FAN</label><b>${g.fan_pct == null ? "N/A" : g.fan_pct + "%"}</b></div>
        </div>
      </div>`;
  }).join("");
}

// ---------- charts ----------
function ensureCharts() {
  for (const m of METRICS) {
    if (charts[m.key]) continue;
    const panel = document.createElement("div");
    panel.className = "chart-panel";
    panel.innerHTML = `<h3>${m.label}</h3><div class="chart-box"><canvas></canvas></div>`;
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
        plugins: { legend: { labels: { color: "#8b95a5", boxWidth: 12 } } },
        scales: {
          x: { ticks: { color: "#647084", maxTicksLimit: 6, autoSkip: true }, grid: { color: "#1f2937" } },
          y: { ticks: { color: "#647084" }, grid: { color: "#1f2937" } },
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
      backgroundColor: hexToRgba(GPU_COLORS[i % GPU_COLORS.length], 0.12),
      fill: true,
      pointRadius: 0,
      borderWidth: 1.5,
      tension: 0.25,
    }));
    chart.update("none");
  }
}

// ---------- polling ----------
async function tick() {
  try {
    const data = await fetchJSON("/api/gpus");
    if (data.gpus.length) renderCards(data);
  } catch (e) {
    dotEl.style.background = "#ef4444";
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
