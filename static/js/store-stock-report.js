(() => {
  const root = document.querySelector("[data-store-stock-report]");
  if (!root) return;

  const itemSearchUrl = root.getAttribute("data-item-search-url") || "";
  const form = root.querySelector("[data-store-report-filters]");
  const rowFilter = root.querySelector("[data-store-report-row-filter]");
  const rows = [...root.querySelectorAll("[data-store-report-row]")];
  const visibleCount = root.querySelector("[data-store-report-visible-count]");
  const noFilterResults = root.querySelector("[data-store-report-no-filter-results]");

  function debounce(fn, ms) {
    let timer = 0;
    return (...args) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => fn(...args), ms);
    };
  }

  function syncRangeFields(filtersForm) {
    const rangeSelect = filtersForm.querySelector("[data-analytics-range]");
    const rangeFields = [...filtersForm.querySelectorAll("[data-range-field]")];
    const sync = () => {
      const range = rangeSelect?.value || "all";
      rangeFields.forEach((field) => {
        const match = field.getAttribute("data-range-field") === range;
        field.hidden = !match;
        field.disabled = !match;
        field.required = match && range !== "all";
      });
    };
    rangeSelect?.addEventListener("change", sync);
    sync();
  }

  if (form) syncRangeFields(form);

  function updateRowFilter() {
    const q = (rowFilter?.value || "").trim().toLowerCase();
    let shown = 0;
    rows.forEach((row) => {
      const hay = (row.getAttribute("data-search") || "").toLowerCase();
      const match = !q || hay.includes(q);
      row.hidden = !match;
      if (match) shown += 1;
    });
    if (visibleCount) visibleCount.textContent = String(shown);
    if (noFilterResults) {
      noFilterResults.hidden = !(rows.length && shown === 0);
    }
  }

  rowFilter?.addEventListener("input", debounce(updateRowFilter, 120));

  function bindItemSearch() {
    const queryEl = root.querySelector("[data-store-report-item-query]");
    const idEl = root.querySelector("[data-store-report-item-id]");
    const selectedEl = root.querySelector("[data-store-report-item-selected]");
    const resultsEl = root.querySelector("[data-store-report-item-results]");
    const clearBtn = root.querySelector("[data-store-report-item-clear]");
    if (!queryEl || !idEl || !resultsEl || !itemSearchUrl) return;

    let seq = 0;

    function hideResults() {
      resultsEl.hidden = true;
      resultsEl.innerHTML = "";
    }

    function clearItem() {
      idEl.value = "";
      queryEl.value = "";
      if (selectedEl) {
        selectedEl.hidden = true;
        selectedEl.textContent = "";
      }
      if (clearBtn) clearBtn.hidden = true;
      hideResults();
    }

    function selectResult(row) {
      idEl.value = String(row.id || "");
      const label = row.label || row.name || "";
      queryEl.value = label;
      if (selectedEl) {
        selectedEl.hidden = false;
        const qty = row.store_qty != null ? ` · store qty ${row.store_qty}` : "";
        selectedEl.textContent = `${label}${qty}`;
      }
      if (clearBtn) clearBtn.hidden = false;
      hideResults();
    }

    async function runSearch() {
      const q = (queryEl.value || "").trim();
      if (q.length < 1) {
        hideResults();
        return;
      }
      const mySeq = ++seq;
      try {
        const response = await fetch(
          `${itemSearchUrl}?q=${encodeURIComponent(q)}`,
          {
            headers: { Accept: "application/json" },
            credentials: "same-origin",
          }
        );
        if (!response.ok) return;
        const data = await response.json();
        if (mySeq !== seq) return;
        const list = Array.isArray(data.results) ? data.results : [];
        resultsEl.innerHTML = "";
        if (!list.length) {
          const empty = document.createElement("li");
          empty.className = "store-stock-empty";
          empty.textContent = "No matches";
          resultsEl.appendChild(empty);
          resultsEl.hidden = false;
          return;
        }
        list.forEach((row) => {
          const li = document.createElement("li");
          const btn = document.createElement("button");
          btn.type = "button";
          btn.textContent = row.label || row.name || String(row.id);
          btn.addEventListener("click", () => selectResult(row));
          li.appendChild(btn);
          resultsEl.appendChild(li);
        });
        resultsEl.hidden = false;
      } catch (_err) {
        /* ignore */
      }
    }

    queryEl.addEventListener(
      "input",
      debounce(() => {
        idEl.value = "";
        if (selectedEl) {
          selectedEl.hidden = true;
          selectedEl.textContent = "";
        }
        if (clearBtn) clearBtn.hidden = true;
        runSearch();
      }, 180)
    );
    queryEl.addEventListener("focus", () => {
      if ((queryEl.value || "").trim() && !idEl.value) runSearch();
    });
    clearBtn?.addEventListener("click", clearItem);
    document.addEventListener("click", (event) => {
      if (!resultsEl.contains(event.target) && event.target !== queryEl) {
        hideResults();
      }
    });
  }

  bindItemSearch();
})();
