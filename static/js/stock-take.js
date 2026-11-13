(() => {
  const root = document.querySelector("[data-stock-take]");
  if (!root) return;

  const createRoot = root.querySelector("[data-stock-take-create]");
  const createPanel = createRoot?.querySelector("[data-stock-take-create-panel]");
  const createOpen = createRoot?.querySelector("[data-stock-take-create-open]");
  const setCreateOpen = (open) => {
    if (!createRoot || !createPanel || !createOpen) return;
    createPanel.hidden = !open;
    createOpen.hidden = open;
    createRoot.classList.toggle("is-open", open);
    if (open) {
      createPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };
  createOpen?.addEventListener("click", () => setCreateOpen(true));
  createRoot?.querySelectorAll("[data-stock-take-create-close]").forEach((btn) => {
    btn.addEventListener("click", () => setCreateOpen(false));
  });

  const editRoot = root.querySelector("[data-stock-take-edit]");
  const editPanel = editRoot?.querySelector("[data-stock-take-edit-panel]");
  const editOpen = editRoot?.querySelector("[data-stock-take-edit-open]");
  const setEditOpen = (open) => {
    if (!editRoot || !editPanel || !editOpen) return;
    editPanel.hidden = !open;
    editOpen.hidden = open;
    editRoot.classList.toggle("is-open", open);
    if (open) {
      editPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };
  editOpen?.addEventListener("click", () => setEditOpen(true));
  editRoot?.querySelectorAll("[data-stock-take-edit-close]").forEach((btn) => {
    btn.addEventListener("click", () => setEditOpen(false));
  });

  const forms = [...root.querySelectorAll("[data-stock-take-form]")];
  forms.forEach((form) => {
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
  });
})();
