(() => {
  const itemPanel = document.querySelector("[data-item-total]");
  const searchInput = document.querySelector("[data-item-search]");
  const itemList = document.querySelector("[data-item-list]");
  const noResults = document.querySelector("[data-item-no-results]");
  const visibleCountEl = document.querySelector("[data-item-visible-count]");

  if (!searchInput || !itemPanel) return;
  // Progressive catalog APIs own search/filter.
  if (
    itemPanel.hasAttribute("data-catalog-api") ||
    itemPanel.hasAttribute("data-stock-catalog-api") ||
    itemPanel.hasAttribute("data-item-catalog-api")
  ) {
    return;
  }

  const totalCount = Number(itemPanel.dataset.itemTotal) || 0;
  const categoryGroups = document.querySelectorAll("[data-item-category-group]");
  let frame = 0;

  const normalize = (value) => (value || "").toLowerCase().trim();

  const tokensFrom = (query) =>
    normalize(query)
      .split(/\s+/)
      .filter(Boolean);

  const matchesQuery = (haystack, tokens) => {
    if (!tokens.length) return true;
    const text = normalize(haystack);
    return tokens.every((token) => text.includes(token));
  };

  const updateCountLabel = (visible, query) => {
    if (!visibleCountEl) return;
    if (query) {
      visibleCountEl.textContent = `${visible} of ${totalCount} item${totalCount === 1 ? "" : "s"}`;
      visibleCountEl.hidden = false;
    } else {
      visibleCountEl.textContent = `${totalCount} item${totalCount === 1 ? "" : "s"}`;
      if (visibleCountEl.hasAttribute("data-count-hide-idle")) {
        visibleCountEl.hidden = true;
      }
    }
  };

  const filterItems = () => {
    const query = searchInput.value;
    const tokens = tokensFrom(query);
    const hasQuery = tokens.length > 0;
    let visibleCount = 0;

    if (categoryGroups.length) {
      categoryGroups.forEach((group) => {
        let groupVisible = 0;
        const categoryLabel =
          group.querySelector(".shop-floor-category-head h3, [data-category-label]")
            ?.textContent || "";

        group.querySelectorAll("[data-item-row]").forEach((row) => {
          const haystack = `${row.dataset.searchText || ""} ${categoryLabel}`;
          const match = matchesQuery(haystack, tokens);
          row.hidden = !match;
          const formRow =
            row.querySelector(":scope > [data-stock-item-inputs]") ||
            (row.nextElementSibling?.matches?.("[data-stock-item-inputs]")
              ? row.nextElementSibling
              : null);
          if (formRow) {
            formRow.hidden =
              !match ||
              !(
                row.classList.contains("is-open") ||
                row.classList.contains("is-selected")
              );
          }
          if (match) {
            groupVisible += 1;
            visibleCount += 1;
          }
        });

        group.hidden = groupVisible === 0;
        const countEl = group.querySelector("[data-category-count]");
        if (countEl) countEl.textContent = String(groupVisible);
      });
    } else {
      document.querySelectorAll("[data-item-row]").forEach((row) => {
        const match = matchesQuery(row.dataset.searchText || "", tokens);
        row.hidden = !match;
        if (match) visibleCount += 1;
      });
    }

    if (itemList) itemList.hidden = visibleCount === 0 && hasQuery;
    if (noResults) noResults.hidden = visibleCount > 0 || !hasQuery;
    updateCountLabel(visibleCount, hasQuery);
  };

  const scheduleFilter = () => {
    if (frame) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      frame = 0;
      filterItems();
    });
  };

  searchInput.addEventListener("input", scheduleFilter);
  searchInput.addEventListener("search", scheduleFilter);

  if (searchInput.value.trim()) {
    filterItems();
  }
})();
