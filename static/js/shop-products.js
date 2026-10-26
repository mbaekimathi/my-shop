(() => {
  const panel = document.querySelector("[data-shop-products]");
  if (!panel) return;

  const groups = Array.from(panel.querySelectorAll("[data-category-group]"));
  const rows = Array.from(panel.querySelectorAll("[data-product-row]"));
  const searchInput = panel.querySelector("[data-product-search]");
  const noResults = panel.querySelector("[data-product-no-results]");
  const enableAllBtn = panel.querySelector("[data-enable-all]");
  const disableAllBtn = panel.querySelector("[data-disable-all]");
  const total = Number(panel.dataset.itemTotal || rows.length);

  const soldToggleFor = (row) => row.querySelector("[data-sold-toggle]");
  const groupRows = (group) =>
    Array.from(group.querySelectorAll("[data-product-row]"));

  const refreshRowStatus = (row) => {
    const toggle = soldToggleFor(row);
    if (!toggle) return;
    const on = row.querySelector("[data-status-on]");
    const off = row.querySelector("[data-status-off]");
    if (on) on.hidden = !toggle.checked;
    if (off) off.hidden = toggle.checked;
  };

  const refreshGroupCheckAll = (group) => {
    const checkAll = group.querySelector("[data-check-all]");
    if (!checkAll) return;
    const toggles = groupRows(group)
      .map(soldToggleFor)
      .filter(Boolean);
    const enabled = toggles.filter((t) => t.checked).length;
    checkAll.checked = enabled === toggles.length && toggles.length > 0;
    checkAll.indeterminate = enabled > 0 && enabled < toggles.length;
  };

  const refreshCounts = () => {
    const enabled = rows.reduce(
      (sum, row) => sum + (soldToggleFor(row)?.checked ? 1 : 0),
      0
    );
    panel.querySelectorAll("[data-sold-count]").forEach((el) => {
      el.textContent = `${enabled} of ${total} enabled`;
    });
    groups.forEach(refreshGroupCheckAll);
  };

  const setGroup = (group, checked) => {
    groupRows(group).forEach((row) => {
      const toggle = soldToggleFor(row);
      if (toggle) toggle.checked = checked;
      refreshRowStatus(row);
    });
    refreshGroupCheckAll(group);
  };

  const setAll = (checked) => {
    groups.forEach((group) => setGroup(group, checked));
    refreshCounts();
  };

  groups.forEach((group) => {
    group.querySelector("[data-check-all]")?.addEventListener(
      "change",
      (event) => {
        setGroup(group, event.target.checked);
        refreshCounts();
      }
    );
  });

  rows.forEach((row) => {
    soldToggleFor(row)?.addEventListener("change", () => {
      refreshRowStatus(row);
      refreshCounts();
    });
  });

  enableAllBtn?.addEventListener("click", () => setAll(true));
  disableAllBtn?.addEventListener("click", () => setAll(false));

  const updateSearch = () => {
    const query = (searchInput?.value || "").trim().toLowerCase();
    let shown = 0;
    groups.forEach((group) => {
      let groupShown = 0;
      groupRows(group).forEach((row) => {
        const match =
          !query || (row.dataset.searchText || "").includes(query);
        row.hidden = !match;
        if (match) groupShown += 1;
      });
      shown += groupShown;
      const countEl = group.querySelector("[data-category-count]");
      if (countEl) {
        countEl.textContent = String(
          query ? groupShown : groupRows(group).length
        );
      }
      group.hidden = groupShown === 0;
    });
    if (noResults) noResults.hidden = shown > 0 || rows.length === 0;
  };

  searchInput?.addEventListener("input", updateSearch);

  // Collapse the whole form into one JSON field before submitting — hundreds
  // of per-item inputs can trip Django's DATA_UPLOAD_MAX_NUMBER_FIELDS limit
  // or host-level request filters (mod_security), which respond with a bare
  // 400 before the view runs.
  const form = panel.querySelector("[data-shop-products-form]");
  const payload = form?.querySelector("[data-products-payload]");

  form?.addEventListener("submit", () => {
    if (!payload) return;
    const items = rows.map((row) => {
      const toggle = soldToggleFor(row);
      const price = row.querySelector("[data-price-input]");
      return {
        id: toggle ? toggle.name.replace(/^sold_/, "") : "",
        sold: Boolean(toggle?.checked),
        price: price ? price.value : "",
      };
    });
    payload.value = JSON.stringify(items);
    rows.forEach((row) => {
      row
        .querySelectorAll("[data-sold-toggle], [data-price-input]")
        .forEach((input) => {
          input.disabled = true;
        });
    });
  });

  refreshCounts();
})();
