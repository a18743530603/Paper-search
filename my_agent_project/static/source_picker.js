document.addEventListener("DOMContentLoaded", () => {
  const picker = document.querySelector("[data-source-picker]");
  if (!picker) return;

  const form = picker.closest("form");
  const checkboxes = Array.from(
    picker.querySelectorAll('input[name="sources"]')
  );
  const presetSelect = picker.querySelector("[data-source-preset]");
  const presetDescription = picker.querySelector("[data-preset-description]");
  const selectAllButton = picker.querySelector("[data-select-all]");
  const clearAllButton = picker.querySelector("[data-clear-all]");
  const countLabel = picker.querySelector("[data-source-count]");
  const detailsCount = picker.querySelector("[data-details-count]");
  const selectedNames = picker.querySelector("[data-source-names]");
  const errorLabel = picker.querySelector("[data-source-error]");
  const submitButton = form?.querySelector("[data-create-task]");

  const sourceIdsForOption = (option) => {
    if (!option?.dataset.sources) return [];
    try {
      return JSON.parse(option.dataset.sources);
    } catch (_error) {
      return [];
    }
  };

  const currentSourceIds = () =>
    checkboxes.filter((item) => item.checked).map((item) => item.value);

  const sameSources = (left, right) => {
    if (left.length !== right.length) return false;
    const rightSet = new Set(right);
    return left.every((sourceId) => rightSet.has(sourceId));
  };

  const syncPreset = () => {
    if (!presetSelect) return;
    const current = currentSourceIds();
    const matchingOption = Array.from(presetSelect.options).find((option) =>
      option.value !== "custom" && sameSources(current, sourceIdsForOption(option))
    );
    presetSelect.value = matchingOption?.value || "custom";
    if (presetDescription) {
      presetDescription.textContent = matchingOption?.dataset.description ||
        "已按你的需要调整来源，仍可在下方继续修改。";
    }
  };

  const updateState = () => {
    const selected = checkboxes.filter((item) => item.checked);
    const selectedCount = selected.length;
    if (countLabel) {
      countLabel.textContent = `已选择 ${selectedCount} 个`;
    }
    if (detailsCount) detailsCount.textContent = `${selectedCount} / ${checkboxes.length}`;
    if (selectedNames) {
      selectedNames.textContent = selectedCount
        ? selected.map((item) => item.dataset.sourceLabel || item.value).join("、")
        : "尚未选择来源";
    }
    if (errorLabel) errorLabel.hidden = selectedCount > 0;
    if (submitButton) submitButton.disabled = selectedCount === 0;
    syncPreset();
  };

  presetSelect?.addEventListener("change", () => {
    if (presetSelect.value === "custom") return;
    const selectedIds = new Set(sourceIdsForOption(presetSelect.selectedOptions[0]));
    checkboxes.forEach((item) => {
      item.checked = selectedIds.has(item.value);
    });
    updateState();
  });

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
