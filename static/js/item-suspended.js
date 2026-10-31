(function () {
  "use strict";

  document.querySelectorAll("[data-confirm-delete]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const name = form.dataset.itemName || "this item";
      const stockNote =
        form.dataset.hasStock === "true"
          ? " Its remaining stock will be written off."
          : "";
      if (
        !window.confirm(`Delete “${name}”?${stockNote} This cannot be undone.`)
      ) {
        event.preventDefault();
      }
    });
  });

  document.querySelectorAll("[data-confirm-delete-all]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const count = form.dataset.itemCount || "";
      if (
        !window.confirm(
          `Delete all ${count} suspended items? Any remaining stock will be ` +
            "written off. This cannot be undone."
        )
      ) {
        event.preventDefault();
      }
    });
  });

  // Bulk selection — checkboxes live in the table but submit with the header
  // form via the `form` attribute.
  const bulkForm = document.querySelector("[data-bulk-form]");
  const checks = Array.from(document.querySelectorAll("[data-item-check]"));
  const selectAll = document.querySelector("[data-select-all]");
  const selectedCountEl = document.querySelector("[data-selected-count]");
  const bulkButtons = bulkForm
    ? Array.from(bulkForm.querySelectorAll("[data-bulk-submit]"))
    : [];

  const visibleChecks = () =>
    checks.filter((check) => {
      const row = check.closest("[data-item-row]");
      return !row || !row.hidden;
    });

  const selectedChecks = () => checks.filter((check) => check.checked);

  const refresh = () => {
    const count = selectedChecks().length;
    bulkButtons.forEach((button) => {
      button.disabled = count === 0;
    });
    if (selectedCountEl) {
      selectedCountEl.textContent = `${count} selected`;
    }
    if (selectAll) {
      const visible = visibleChecks();
      const visibleChecked = visible.filter((check) => check.checked).length;
      selectAll.checked = visible.length > 0 && visibleChecked === visible.length;
      selectAll.indeterminate =
        visibleChecked > 0 && visibleChecked < visible.length;
    }
  };

  checks.forEach((check) => check.addEventListener("change", refresh));

  if (selectAll) {
    selectAll.addEventListener("change", () => {
      visibleChecks().forEach((check) => {
        check.checked = selectAll.checked;
      });
      refresh();
    });
  }

  if (bulkForm) {
    bulkForm.addEventListener("submit", (event) => {
      const count = selectedChecks().length;
      const isDelete =
        event.submitter && event.submitter.value === "delete_bulk";
      const plural = count === 1 ? "item" : "items";
      const message = isDelete
        ? `Delete ${count} selected ${plural}? Any remaining stock will be ` +
          "written off. This cannot be undone."
        : `Unsuspend ${count} selected ${plural}?`;
      if (!window.confirm(message)) {
        event.preventDefault();
      }
    });
  }

  refresh();
})();
