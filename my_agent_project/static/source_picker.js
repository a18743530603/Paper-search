document.addEventListener("DOMContentLoaded", () => {
  const picker = document.querySelector("[data-source-picker]");
  if (!picker) return;

  const form = picker.closest("form");
  const checkboxes = Array.from(
    picker.querySelectorAll('input[name="sources"]')
  );
  const selectAllButton = picker.querySelector("[data-select-all]");
  const clearAllButton = picker.querySelector("[data-clear-all]");
  const countLabel = picker.querySelector("[data-source-count]");
  const errorLabel = picker.querySelector("[data-source-error]");
  const submitButton = form?.querySelector("[data-create-task]");

  const updateState = () => {
    const selectedCount = checkboxes.filter((item) => item.checked).length;
    if (countLabel) {
      countLabel.textContent = `${selectedCount} / ${checkboxes.length} 已选择`;
    }
    if (errorLabel) errorLabel.hidden = selectedCount > 0;
    if (submitButton) submitButton.disabled = selectedCount === 0;
  };

  selectAllButton?.addEventListener("click", () => {
    checkboxes.forEach((item) => {
      item.checked = true;
    });
    updateState();
  });

  clearAllButton?.addEventListener("click", () => {
    checkboxes.forEach((item) => {
      item.checked = false;
    });
    updateState();
  });

  checkboxes.forEach((item) => item.addEventListener("change", updateState));
  form?.addEventListener("submit", (event) => {
    if (!checkboxes.some((item) => item.checked)) {
      event.preventDefault();
      updateState();
    }
  });

  updateState();
});
