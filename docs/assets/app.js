// Dashboard client-side JS.
// Lee window.__SNAPSHOT__ y window.__HISTORY__ para renderizar charts y sparklines.

(function () {
  "use strict";

  function drawSparkline(canvas) {
    const raw = canvas.getAttribute("data-points");
    if (!raw) return;
    let points;
    try {
      points = JSON.parse(raw);
    } catch (e) {
      return;
    }
    if (!Array.isArray(points) || points.length < 2) return;

    const ctx = canvas.getContext("2d");
    const W = canvas.width;
    const H = canvas.height;
    const vals = points.map((p) => p.v);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const range = max - min || 1;

    const xStep = W / (points.length - 1);
    ctx.clearRect(0, 0, W, H);

    // Linea
    ctx.beginPath();
    points.forEach((p, i) => {
      const x = i * xStep;
      const y = H - ((p.v - min) / range) * (H - 4) - 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    const lastVsFirst = vals[vals.length - 1] - vals[0];
    ctx.strokeStyle = lastVsFirst >= 0 ? "#22c55e" : "#ef4444";
    ctx.lineWidth = 1.4;
    ctx.stroke();

    // Punto final
    const lastX = (points.length - 1) * xStep;
    const lastY = H - ((vals[vals.length - 1] - min) / range) * (H - 4) - 2;
    ctx.beginPath();
    ctx.arc(lastX, lastY, 2.2, 0, Math.PI * 2);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.fill();
  }

  function initSparklines() {
    document.querySelectorAll("canvas.sparkline").forEach(drawSparkline);
  }

  function initPhaseHistoryChart() {
    const canvas = document.getElementById("phase-history-chart");
    if (!canvas || !window.Chart) return;
    const history = window.__HISTORY__ || {};
    const series = (history.phase_timeline || []).slice(-180);
    if (series.length === 0) return;

    const labels = series.map((p) => p.date);
    const phaseColors = { 1: "#22c55e", 2: "#f59e0b", 3: "#ef4444", 4: "#3b82f6" };

    new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Crecimiento",
            data: series.map((p) => p.growth),
            borderColor: "#22c55e",
            backgroundColor: "rgba(34, 197, 94, 0.15)",
            tension: 0.25,
            fill: false,
          },
          {
            label: "Stress / Inflacion",
            data: series.map((p) => p.stress),
            borderColor: "#ef4444",
            backgroundColor: "rgba(239, 68, 68, 0.15)",
            tension: 0.25,
            fill: false,
          },
          {
            label: "Fase (1-4)",
            data: series.map((p) => p.phase_id),
            borderColor: "#94a3b8",
            backgroundColor: "rgba(148, 163, 184, 0.05)",
            yAxisID: "y2",
            stepped: true,
            pointRadius: 0,
            borderDash: [4, 4],
          },
        ],
      },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { color: "#e2e8f0" } } },
        scales: {
          x: { ticks: { color: "#94a3b8", maxTicksLimit: 10 }, grid: { color: "rgba(255,255,255,0.04)" } },
          y: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(255,255,255,0.04)" }, min: -1.2, max: 1.2 },
          y2: {
            position: "right",
            min: 0.5, max: 4.5,
            ticks: {
              color: "#94a3b8",
              callback: (v) => ({ 1: "EXP", 2: "REC", 3: "CON", 4: "DES" })[Math.round(v)] || "",
            },
            grid: { display: false },
          },
        },
      },
    });
  }

  function initAxesChart() {
    const canvas = document.getElementById("axes-history-chart");
    if (!canvas || !window.Chart) return;
    const history = window.__HISTORY__ || {};
    const phaseTl = (history.phase_timeline || []).slice(-365);
    const sentTl = (history.sentiment_timeline || []).slice(-365);
    if (phaseTl.length === 0 && sentTl.length === 0) return;

    // Merge by date
    const dates = Array.from(new Set([...phaseTl.map((x) => x.date), ...sentTl.map((x) => x.date)])).sort();
    const macroMap = new Map(phaseTl.map((x) => [x.date, x.growth - x.stress]));
    const sentMap = new Map(sentTl.map((x) => [x.date, x.sentiment]));

    new Chart(canvas, {
      type: "line",
      data: {
        labels: dates,
        datasets: [
          {
            label: "Macro (crec - stress)",
            data: dates.map((d) => macroMap.has(d) ? macroMap.get(d) : null),
            borderColor: "#38bdf8",
            backgroundColor: "rgba(56, 189, 248, 0.15)",
            tension: 0.25,
            spanGaps: true,
          },
          {
            label: "Sentimiento mercado",
            data: dates.map((d) => sentMap.has(d) ? sentMap.get(d) : null),
            borderColor: "#a78bfa",
            backgroundColor: "rgba(167, 139, 250, 0.15)",
            tension: 0.25,
            spanGaps: true,
          },
        ],
      },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { color: "#e2e8f0" } } },
        scales: {
          x: { ticks: { color: "#94a3b8", maxTicksLimit: 12 }, grid: { color: "rgba(255,255,255,0.04)" } },
          y: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(255,255,255,0.04)" } },
        },
      },
    });
  }

  function init() {
    initSparklines();
    initPhaseHistoryChart();
    initAxesChart();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
