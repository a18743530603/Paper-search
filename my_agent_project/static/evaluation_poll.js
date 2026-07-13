const activeRun = document.querySelector("[data-evaluation-running]");

if (activeRun) {
  window.setTimeout(() => window.location.reload(), 2000);
}

const semanticWeight = document.querySelector("[data-semantic-weight]");
const semanticOutput = document.querySelector("[data-semantic-output]");
const keywordOutput = document.querySelector("[data-keyword-output]");

function updateWeightOutputs() {
  if (!semanticWeight || !semanticOutput || !keywordOutput) return;
  const semantic = Number(semanticWeight.value);
  semanticOutput.value = semantic.toFixed(2);
  keywordOutput.value = (1 - semantic).toFixed(2);
}

semanticWeight?.addEventListener("input", updateWeightOutputs);
updateWeightOutputs();

const chartColors = {
  grid: "#d9dfe6",
  text: "#667085",
  hit1: "#b45309",
  hit3: "#0f766e",
  hit5: "#2563eb",
};

function drawLineChart(canvas, labels, series) {
  if (!canvas || !labels.length) return;

  const container = canvas.parentElement;
  const width = Math.max(container.clientWidth, 280);
  const height = 260;
  const ratio = window.devicePixelRatio || 1;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);

  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);

  const padding = { top: 42, right: 20, bottom: 40, left: 48 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const xAt = (index) =>
    labels.length === 1
      ? padding.left + plotWidth / 2
      : padding.left + (index / (labels.length - 1)) * plotWidth;
  const yAt = (value) => padding.top + (1 - value) * plotHeight;

  context.font = "12px system-ui, sans-serif";
  context.lineWidth = 1;
  context.textAlign = "right";
  context.textBaseline = "middle";
  for (let value = 0; value <= 1.001; value += 0.25) {
    const y = yAt(value);
    context.strokeStyle = chartColors.grid;
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
    context.fillStyle = chartColors.text;
    context.fillText(`${Math.round(value * 100)}%`, padding.left - 8, y);
  }

  context.textAlign = "center";
  context.textBaseline = "top";
  labels.forEach((label, index) => {
    context.fillStyle = chartColors.text;
    context.fillText(label, xAt(index), height - padding.bottom + 12);
  });

  series.forEach((item, seriesIndex) => {
    context.strokeStyle = item.color;
    context.fillStyle = item.color;
    context.lineWidth = 2.5;
    context.beginPath();
    item.values.forEach((value, index) => {
      const x = xAt(index);
      const y = yAt(value);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();

    item.values.forEach((value, index) => {
      context.beginPath();
      context.arc(xAt(index), yAt(value), 4, 0, Math.PI * 2);
      context.fill();
    });

    const legendX = padding.left + seriesIndex * 92;
    context.fillRect(legendX, 16, 18, 3);
    context.fillStyle = chartColors.text;
    context.textAlign = "left";
    context.textBaseline = "middle";
    context.fillText(item.name, legendX + 24, 18);
  });
}

function renderEvaluationCharts() {
  const current = document.querySelector("[data-current-metrics]");
  if (current) {
    drawLineChart(
      document.querySelector("#top-k-chart"),
      ["K=1", "K=3", "K=5"],
      [
        {
          name: "Hit@K",
          color: chartColors.hit3,
          values: [
            Number(current.dataset.hit1),
            Number(current.dataset.hit3),
            Number(current.dataset.hit5),
          ],
        },
      ],
    );
  }

  const runs = [...document.querySelectorAll("[data-evaluation-run]")]
    .map((row) => ({
      id: Number(row.dataset.evaluationRun),
      hit1: Number(row.dataset.hit1),
      hit3: Number(row.dataset.hit3),
      hit5: Number(row.dataset.hit5),
    }))
    .sort((left, right) => left.id - right.id);

  if (runs.length) {
    drawLineChart(
      document.querySelector("#run-history-chart"),
      runs.map((run) => `#${run.id}`),
      [
        { name: "Hit@1", color: chartColors.hit1, values: runs.map((run) => run.hit1) },
        { name: "Hit@3", color: chartColors.hit3, values: runs.map((run) => run.hit3) },
        { name: "Hit@5", color: chartColors.hit5, values: runs.map((run) => run.hit5) },
      ],
    );
  }
}

let chartResizeTimer;
window.addEventListener("resize", () => {
  window.clearTimeout(chartResizeTimer);
  chartResizeTimer = window.setTimeout(renderEvaluationCharts, 120);
});

renderEvaluationCharts();
