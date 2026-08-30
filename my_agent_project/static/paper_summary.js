const PAPER_SUMMARY_POLL_INTERVAL_MS = 2000;
const paperSummaryControls = document.querySelectorAll("[data-paper-summary]");

function paperSummaryNodes(control) {
  const summaryUrl = control.dataset.summaryUrl;
  return {
    status: control.querySelector("[data-summary-status]"),
    error: control.querySelector("[data-summary-error]"),
    generate: control.querySelector("[data-summary-generate]"),
    view: control.querySelector("[data-summary-view]"),
    regenerate: control.querySelector("[data-summary-regenerate]"),
    panel: document.querySelector(
      `[data-paper-summary-panel][data-summary-url="${summaryUrl}"]`,
    ),
  };
}

function showPaperSummaryAction(button, visible, disabled = false) {
  if (!button) return;
  button.hidden = !visible;
  button.disabled = disabled;
}

function closePaperSummary(control) {
  const { view, panel } = paperSummaryNodes(control);
  if (!panel || !view) return;
  panel.hidden = true;
  view.setAttribute("aria-expanded", "false");
  view.textContent = "查看论文解读";
}

function fillPaperSummary(control, summary) {
  const { panel } = paperSummaryNodes(control);
  if (!panel) return;
  const content = panel.querySelector("[data-summary-content]");
  const meta = panel.querySelector("[data-summary-meta]");
  if (content) content.textContent = summary.content || "";
  if (meta) {
    const details = [];
    if (summary.model) details.push(`模型：${summary.model}`);
    if (summary.completed_at) details.push(`完成时间：${summary.completed_at}`);
    meta.textContent = details.join(" · ");
  }
}

function renderPaperSummary(control, summary) {
  const { status, error, generate, view, regenerate } = paperSummaryNodes(control);
  control.paperSummary = summary;
  if (error) {
    error.hidden = true;
    error.textContent = "";
  }

  if (!summary) {
    if (status) status.textContent = "论文解读：尚未生成";
    showPaperSummaryAction(generate, true);
    showPaperSummaryAction(view, false);
    showPaperSummaryAction(regenerate, false);
    closePaperSummary(control);
    return false;
  }

  const isRunning = ["queued", "generating"].includes(summary.status)
    || ["queued", "running", "retry_wait"].includes(summary.job_status);
  if (isRunning) {
    if (status) status.textContent = "论文解读生成中...";
    showPaperSummaryAction(generate, false);
    showPaperSummaryAction(view, false);
    showPaperSummaryAction(regenerate, true, true);
    closePaperSummary(control);
    return true;
  }

  if (summary.status === "completed") {
    if (status) status.textContent = "论文解读：已完成";
    showPaperSummaryAction(generate, false);
    showPaperSummaryAction(view, true);
    showPaperSummaryAction(regenerate, true);
    fillPaperSummary(control, summary);
    return false;
  }

  if (status) status.textContent = "论文解读：生成失败";
  showPaperSummaryAction(generate, false);
  showPaperSummaryAction(view, false);
  showPaperSummaryAction(regenerate, true);
  closePaperSummary(control);
  if (error) {
    error.textContent = summary.error || "论文解读生成失败，请重试。";
    error.hidden = false;
  }
  return false;
}

function schedulePaperSummaryPoll(control) {
  if (control.paperSummaryPollTimer) return;
  control.paperSummaryPollTimer = window.setTimeout(async () => {
    control.paperSummaryPollTimer = null;
    await refreshPaperSummary(control);
  }, PAPER_SUMMARY_POLL_INTERVAL_MS);
}

async function refreshPaperSummary(control) {
  if (control.dataset.paperReady !== "true") return;
  try {
    const response = await fetch(control.dataset.summaryUrl, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) throw new Error("无法读取论文解读状态");
    const payload = await response.json();
    if (renderPaperSummary(control, payload.summary)) {
      schedulePaperSummaryPoll(control);
    }
  } catch (_error) {
    const { status, generate, view, regenerate } = paperSummaryNodes(control);
    if (status) status.textContent = "论文解读：状态暂不可用";
    showPaperSummaryAction(generate, false);
    showPaperSummaryAction(view, false);
    showPaperSummaryAction(regenerate, false);
    schedulePaperSummaryPoll(control);
  }
}

async function requestPaperSummary(control) {
  if (control.dataset.paperReady !== "true" || control.dataset.submitting === "true") {
    return;
  }
  control.dataset.submitting = "true";
  const { status, error, generate, view, regenerate } = paperSummaryNodes(control);
  if (status) status.textContent = "论文解读生成中...";
  if (error) error.hidden = true;
  showPaperSummaryAction(generate, false);
  showPaperSummaryAction(view, false);
  showPaperSummaryAction(regenerate, true, true);
  closePaperSummary(control);

  try {
    const response = await fetch(control.dataset.summaryUrl, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "无法创建论文解读任务");
    }
    if (renderPaperSummary(control, payload.summary)) {
      schedulePaperSummaryPoll(control);
    }
  } catch (requestError) {
    if (status) status.textContent = "论文解读：请求失败";
    showPaperSummaryAction(generate, !control.paperSummary);
    showPaperSummaryAction(view, control.paperSummary?.status === "completed");
    showPaperSummaryAction(regenerate, Boolean(control.paperSummary));
    if (error) {
      error.textContent = requestError.message;
      error.hidden = false;
    }
  } finally {
    control.dataset.submitting = "false";
  }
}

for (const control of paperSummaryControls) {
  const { generate, view, regenerate, panel } = paperSummaryNodes(control);
  generate?.addEventListener("click", () => requestPaperSummary(control));
  regenerate?.addEventListener("click", () => requestPaperSummary(control));
  view?.addEventListener("click", () => {
    if (!panel || control.paperSummary?.status !== "completed") return;
    const opening = panel.hidden;
    panel.hidden = !opening;
    view.setAttribute("aria-expanded", String(opening));
    view.textContent = opening ? "收起论文解读" : "查看论文解读";
    if (opening) fillPaperSummary(control, control.paperSummary);
  });
  refreshPaperSummary(control);
}
