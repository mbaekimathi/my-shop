(() => {
  const form = document.querySelector("[data-employee-receipts-filters]");
  if (!form) return;

  const mode = form.querySelector("[data-receipt-filter-mode]");
  const panels = form.querySelectorAll("[data-filter-panel]");

  const sync = () => {
    const current = (mode?.value || "all").trim();
    panels.forEach((panel) => {
      const match = panel.getAttribute("data-filter-panel") === current;
      panel.hidden = !match;
      panel.classList.toggle("is-hidden", !match);
    });
  };

  mode?.addEventListener("change", sync);
  sync();
})();
