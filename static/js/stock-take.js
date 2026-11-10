(() => {
  const root = document.querySelector("[data-stock-take]");
  if (!root) return;

  const form = root.querySelector("[data-stock-take-form]");
  if (!form) return;

  const scopeInputs = form.querySelectorAll("[data-stock-take-scope]");
  const panels = form.querySelectorAll("[data-stock-take-panel]");

  const syncScope = () => {
    const mode =
      form.querySelector("[data-stock-take-scope]:checked")?.value || "category";
    panels.forEach((panel) => {
      panel.hidden = panel.getAttribute("data-stock-take-panel") !== mode;
    });
    scopeInputs.forEach((input) => {
      input.closest(".stock-visibility-mode")?.classList.toggle(
        "is-selected",
        input.checked
      );
    });
  };

  scopeInputs.forEach((input) => input.addEventListener("change", syncScope));
  syncScope();

  const search = form.querySelector("[data-stock-take-item-search]");
  search?.addEventListener("input", () => {
    const query = search.value.trim().toLowerCase();
    form.querySelectorAll("[data-stock-take-item]").forEach((row) => {
      const hay = (row.getAttribute("data-search") || "").toLowerCase();
      row.hidden = Boolean(query) && !hay.includes(query);
    });
  });
})();
