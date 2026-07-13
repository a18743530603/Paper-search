const POLL_INTERVAL_MS = 2000;

function updateStatusBadge(element, status) {
  if (!element || !status) return;
  element.textContent = status;
  for (const className of Array.from(element.classList)) {
    if (className.startsWith("status-")) {
      element.classList.remove(className);
    }
  }
  element.classList.add(`status-${status}`);
}

function updatePaperStatus(paper) {
  const row = document.querySelector(`[data-paper-row][data-paper-id="${paper.id}"]`);
  if (row) {
    updateStatusBadge(row.querySelector("[data-download-status]"), paper.status);
    const dateCell = row.querySelector("[data-downloaded-at]");
    if (dateCell) dateCell.textContent = paper.downloaded_at || "-";
  }

  const detailStatus = document.querySelector(".detail [data-download-status]");
  if (detailStatus) {
    const previousStatus = detailStatus.textContent.trim();
    updateStatusBadge(detailStatus, paper.status);
    const downloadedAt = document.querySelector(".detail [data-downloaded-at]");
    if (downloadedAt) downloadedAt.textContent = paper.downloaded_at || "尚未下载";
    if (previousStatus === "downloading" && paper.status !== "downloading") {
      window.location.reload();
    }
  }

  const parseStatus = document.querySelector(".detail [data-parse-status]");
  if (parseStatus && paper.parse_status) {
    const previousParseStatus = parseStatus.textContent.trim();
    updateStatusBadge(parseStatus, paper.parse_status);
    if (previousParseStatus === "parsing" && paper.parse_status !== "parsing") {
      window.location.reload();
    }
  }

  const indexStatus = document.querySelector(".detail [data-index-status]");
  if (indexStatus && paper.index_status) {
    const previousIndexStatus = indexStatus.textContent.trim();
    updateStatusBadge(indexStatus, paper.index_status);
    if (previousIndexStatus === "indexing" && paper.index_status !== "indexing") {
      window.location.reload();
    }
  }

  const ragStatus = document.querySelector(".detail [data-rag-status]");
  if (ragStatus && paper.rag_status) {
    const previousRagStatus = ragStatus.textContent.trim();
    updateStatusBadge(ragStatus, paper.rag_status);
    if (previousRagStatus === "answering" && paper.rag_status !== "answering") {
      window.location.reload();
    }
  }
}

async function pollPaperStatuses() {
  const rows = document.querySelectorAll("[data-paper-row]");
  const detail = document.querySelector(".detail");
  if (!rows.length && !detail) return;

  try {
    const response = await fetch("/api/papers/statuses", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return;
    const payload = await response.json();
    const detailId = detail
      ? Number(window.location.pathname.split("/").filter(Boolean).pop())
      : null;
    for (const paper of payload.papers) {
      if (detailId && paper.id !== detailId) continue;
      updatePaperStatus(paper);
    }
  } catch (_error) {
    // A temporary polling failure should not interrupt normal page use.
  }
}

pollPaperStatuses();
window.setInterval(pollPaperStatuses, POLL_INTERVAL_MS);
