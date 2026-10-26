(() => {
  const panel = document.querySelector("[data-shop-products]");
  if (!panel) return;

  const rows = Array.from(panel.querySelectorAll("[data-product-row]"));
  const searchInput = panel.querySelector("[data-product-search]");
  const noResults = panel.querySelector("[data-product-no-results]");
  const checkAll = panel.querySelector("[data-check-all]");
  const enableAllBtn = panel.querySelector("[data-enable-all]");
  const disableAllBtn = panel.querySelector("[data-disable-all]");
  const total = Number(panel.dataset.itemTotal || rows.length);

  const soldToggleFor = (row) => row.querySelector("[data-sold-toggle]");

  const refreshRowStatus = (row) => {
    const toggle = soldToggleFor(row);
    if (!toggle) return;
    const on = row.querySelector("[data-status-on]");
    const off = row.querySelector("[data-status-off]");
    if (on) on.hidden = !toggle.checked;
    if (off) off.hidden = toggle.checked;
  };

  const refreshCounts = () => {
    const enabled = rows.reduce(
      (sum, row) => sum + (soldToggleFor(row)?.checked ? 1 : 0),
      0
    );
    panel.querySelectorAll("[data-sold-count]").forEach((el) => {
      el.textContent = `${enabled} of ${total} enabled`;
    });
    if (checkAll) {
      checkAll.checked = enabled === total && total > 0;
      checkAll.indeterminate = enabled > 0 && enabled < total;
    }
  };

  const setAll = (checked) => {
    rows.forEach((row) => {
      const toggle = soldToggleFor(row);
      if (toggle) toggle.checked = checked;
      refreshRowStatus(row);
    });
    refreshCounts();
  };

  rows.forEach((row) => {
    soldToggleFor(row)?.addEventListener("change", () => {
      refreshRowStatus(row);
      refreshCounts();
    });
  });

  checkAll?.addEventListener("change", () => setAll(checkAll.checked));
  enableAllBtn?.addEventListener("click", () => setAll(true));
  disableAllBtn?.addEventListener("click", () => setAll(false));

  const updateSearch = () => {
    const query = (searchInput?.value || "").trim().toLowerCase();
    let shown = 0;
    rows.forEach((row) => {
      const match = !query || (row.dataset.searchText || "").includes(query);
      row.hidden = !match;
      if (match) shown += 1;
    });
    if (noResults) noResults.hidden = shown > 0 || rows.length === 0;
  };

  searchInput?.addEventListener("input", updateSearch);
  refreshCounts();
})();
