(() => {
  const root = document.querySelector("[data-store-stock]");
  if (!root) return;

  const employeeSearchUrl = root.getAttribute("data-employee-search-url") || "";
  const mode = (root.getAttribute("data-mode") || "").trim().toLowerCase();
  const personLabel = mode === "in" ? "who delivered" : "employee who received";
  const form = root.querySelector("[data-store-stock-form]");
  const filterInput = root.querySelector("[data-store-item-filter]");
  const rows = [...root.querySelectorAll("[data-store-item-row]")];
  const visibleCount = root.querySelector("[data-store-visible-count]");
  const filledCount = root.querySelector("[data-store-filled-count]");
  const filledSummary = root.querySelector("[data-store-filled-summary]");
  const noFilterResults = root.querySelector("[data-store-no-filter-results]");
  const qtyInputs = [...root.querySelectorAll("[data-store-qty-input]")];

  function debounce(fn, ms) {
    let timer = 0;
    return (...args) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => fn(...args), ms);
    };
  }

  function qtyValue(input) {
    const value = Number(String(input.value || "").trim());
    return Number.isFinite(value) && value > 0 ? value : 0;
  }

  function updateFilledSummary() {
    let filled = 0;
    let totalQty = 0;
    qtyInputs.forEach((input) => {
      const value = qtyValue(input);
      const row = input.closest("[data-store-item-row]");
      if (row) row.classList.toggle("is-filled", value > 0);
      if (value > 0) {
        filled += 1;
        totalQty += value;
      }
    });
    if (filledCount) filledCount.textContent = String(filled);
    if (filledSummary) {
      filledSummary.textContent =
        filled === 0
          ? "No quantities entered yet"
          : `${filled} line${filled === 1 ? "" : "s"} · ${totalQty} total qty`;
    }
  }

  function updateFilter() {
    const q = (filterInput?.value || "").trim().toLowerCase();
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

  filterInput?.addEventListener("input", debounce(updateFilter, 120));
  qtyInputs.forEach((input) => {
    input.addEventListener("input", updateFilledSummary);
    input.addEventListener("change", updateFilledSummary);
  });
  updateFilledSummary();

  function bindEmployeeSearch() {
    const queryEl = root.querySelector("[data-store-employee-query]");
    const idEl = root.querySelector("[data-store-employee-id]");
    const selectedEl = root.querySelector("[data-store-employee-selected]");
    const resultsEl = root.querySelector("[data-store-employee-results]");
    if (!queryEl || !idEl || !resultsEl || !employeeSearchUrl) return;

    let seq = 0;

    function hideResults() {
      resultsEl.hidden = true;
      resultsEl.innerHTML = "";
    }

    function selectResult(row) {
      idEl.value = String(row.id || "");
      if (selectedEl) {
        selectedEl.hidden = false;
        selectedEl.textContent = row.label || row.name || "";
      }
      queryEl.value = "";
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
          `${employeeSearchUrl}?q=${encodeURIComponent(q)}`,
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

    const onInput = debounce(() => {
      idEl.value = "";
      if (selectedEl) {
        selectedEl.hidden = true;
        selectedEl.textContent = "";
      }
      runSearch();
    }, 180);

    queryEl.addEventListener("input", onInput);
    queryEl.addEventListener("focus", () => {
      if ((queryEl.value || "").trim()) runSearch();
    });
    document.addEventListener("click", (event) => {
      if (!resultsEl.contains(event.target) && event.target !== queryEl) {
        hideResults();
      }
    });
  }

  bindEmployeeSearch();

  form?.addEventListener("submit", (event) => {
    const employeeId = root.querySelector("[data-store-employee-id]")?.value;
    const hasQty = qtyInputs.some((input) => qtyValue(input) > 0);
    if (!employeeId) {
      event.preventDefault();
      window.alert(`Select the ${personLabel} from the live search list.`);
      return;
    }
    if (!hasQty) {
      event.preventDefault();
      window.alert("Enter a quantity for at least one item.");
    }
  });
})();
