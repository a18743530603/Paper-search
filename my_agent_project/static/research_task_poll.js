const taskRoot = document.querySelector("[data-research-task-id]");
const runningReportStatuses = new Set(["preparing", "retrieving", "generating"]);
const runningMessageStatuses = new Set([
  "pending",
  "retrieving",
  "generating",
  "streaming",
  "retry_wait",
]);

if (
  taskRoot?.dataset.researchTaskStatus === "running" ||
  runningReportStatuses.has(taskRoot?.dataset.researchReportStatus) ||
  runningMessageStatuses.has(taskRoot?.dataset.researchMessageStatus)
) {
  const taskId = taskRoot.dataset.researchTaskId;
  const poll = async () => {
    try {
      const response = await fetch(`/api/research-tasks/${taskId}`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) return;
      const task = await response.json();
      const reportStatus = task.report?.status || "not_started";
      const messageStatus = task.latest_message_status || "completed";
      const reportStatusNode = document.querySelector("[data-report-status]");
      if (reportStatusNode) reportStatusNode.textContent = reportStatus;
      if (
        task.status !== "running" &&
        !runningReportStatuses.has(reportStatus) &&
        !runningMessageStatuses.has(messageStatus)
      ) {
        window.location.reload();
        return;
      }
    } catch (_error) {
      // A temporary network failure should not stop later polling attempts.
    }
    window.setTimeout(poll, 2000);
  };
  window.setTimeout(poll, 2000);
}
