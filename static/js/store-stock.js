(() => {
  function initStoreStock(root) {
  const employeeSearchUrl = root.getAttribute("data-employee-search-url") || "";
  const mode = (root.getAttribute("data-mode") || "").trim().toLowerCase();
  const requirePrice = root.getAttribute("data-require-price") || "";
  const personLabel = mode === "in" ? "who delivered" : "employee who received";
  const form = root.matches("[data-store-stock-form]")
    ? root
    : root.querySelector("[data-store-stock-form]") ||
      root.closest("[data-store-stock-form]");
  const filterInput = root.querySelector("[data-store-item-filter]");
  const rows = [...root.querySelectorAll("[data-store-item-row]")];
  const visibleCount = root.querySelector("[data-store-visible-count]");
  const filledCount = root.querySelector("[data-store-filled-count]");
  const filledSummary = root.querySelector("[data-store-filled-summary]");
  const noFilterResults = root.querySelector("[data-store-no-filter-results]");
  const qtyInputs = [...root.querySelectorAll("[data-store-qty-input]")];
  // Pick-and-add mode (stock in/out popups): rows are hidden until searched,
  // "Add" moves a row into the selected list and enables its inputs.
  const selectedWrap = root.querySelector("[data-store-selected-wrap]");
  const selectedList = root.querySelector("[data-store-selected-list]");
  const listWrap = root.querySelector("[data-store-list]");
  const idleEl = root.querySelector("[data-store-idle]");
  const pickMode = Boolean(selectedList);

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
      if (row.classList.contains("is-added")) {
        row.hidden = false;
        return;
      }
      const hay = (row.getAttribute("data-search") || "").toLowerCase();
      const match = hay.includes(q);
      const show = pickMode ? Boolean(q) && match : match;
      row.hidden = !show;
      if (show) shown += 1;
    });
    if (visibleCount) visibleCount.textContent = String(shown);
    if (noFilterResults) {
      noFilterResults.hidden = !(rows.length && q && shown === 0);
    }
    if (idleEl) {
      idleEl.hidden = Boolean(q) || shown > 0;
    }
  }

  function syncSelectedWrap() {
    if (selectedWrap) {
      selectedWrap.hidden = !selectedList || !selectedList.children.length;
    }
  }

  // Trade forms: trade_in shows per-item value fields; trade_out hides and
  // clears them. Re-runs on kind change and on pick-list add/remove.
  const tradeKinds = [...root.querySelectorAll("[data-trade-kind]")];
  const tradeScope = root.closest("[data-modal]") || root;
  const syncTradeKind = () => {
    if (!tradeKinds.length) return;
    const kind =
      tradeKinds.find((r) => r.checked)?.value || "trade_out";
    const isIn = kind === "trade_in";
    root.querySelectorAll("[data-trade-price]").forEach((el) => {
      el.hidden = !isIn;
      const inputs = el.matches("input,select")
        ? [el]
        : [...el.querySelectorAll("input,select")];
      inputs.forEach((input) => {
        const row = input.closest("[data-store-item-row]");
        const added =
          !pickMode || !row || row.classList.contains("is-added");
        input.disabled = !isIn || !added;
        if (!isIn) input.value = "";
      });
    });
    const lead = tradeScope.querySelector("[data-trade-lead]");
    if (lead) {
      lead.textContent = isIn
        ? lead.dataset.tradeLeadIn || "The person brings items into the store."
        : lead.dataset.tradeLeadOut || "The person takes items out of the store.";
    }
    const submitText = root.querySelector("[data-trade-submit-label]");
    if (submitText) {
      submitText.textContent = isIn ? "Record trade in" : "Record trade out";
    }
  };
  tradeKinds.forEach((radio) => {
    radio.addEventListener("change", syncTradeKind);
  });

  if (pickMode) {
    rows.forEach((row) => {
      const addBtn = row.querySelector("[data-store-row-add]");
      const removeBtn = row.querySelector("[data-store-row-remove]");
      const inputsWrap = row.querySelector("[data-store-row-inputs]");
      const inputs = [...row.querySelectorAll("input")];
      if (inputsWrap) inputsWrap.hidden = true;
      inputs.forEach((input) => {
        input.disabled = true;
      });
      addBtn?.addEventListener("click", () => {
        row.classList.add("is-added");
        selectedList.appendChild(row);
        if (inputsWrap) inputsWrap.hidden = false;
        inputs.forEach((input) => {
          input.disabled = false;
        });
        if (addBtn) addBtn.hidden = true;
        if (filterInput) filterInput.value = "";
        syncSelectedWrap();
        updateFilter();
        syncTradeKind();
        row.querySelector("[data-store-qty-input]")?.focus();
      });
      removeBtn?.addEventListener("click", () => {
        inputs.forEach((input) => {
          input.value = "";
          input.disabled = true;
        });
        if (inputsWrap) inputsWrap.hidden = true;
        if (addBtn) addBtn.hidden = false;
        row.classList.remove("is-added");
        row.classList.remove("is-filled");
        listWrap?.appendChild(row);
        syncSelectedWrap();
        updateFilledSummary();
        updateFilter();
        syncTradeKind();
      });
    });
    syncSelectedWrap();
  }

  filterInput?.addEventListener("input", debounce(updateFilter, 120));
  qtyInputs.forEach((input) => {
    input.addEventListener("input", updateFilledSummary);
    input.addEventListener("change", updateFilledSummary);
  });
  updateFilledSummary();
  updateFilter();
  syncTradeKind();

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

  // Supplier suggestions: typing in the supplier name/phone field searches the
  // registered-supplier API and picking a row fills both fields.
  function bindSupplierSearch() {
    const supplierSearchUrl =
      (form && form.getAttribute("data-supplier-search-url")) ||
      root.getAttribute("data-supplier-search-url") ||
      "";
    const inputs = [...root.querySelectorAll("[data-supplier-search]")];
    if (!supplierSearchUrl || !inputs.length) return;
    const nameInput = root.querySelector('input[name="supplier_name"]');
    const phoneInput = root.querySelector('input[name="supplier_phone"]');
    let seq = 0;

    const suggestOf = (input) =>
      input
        .closest("[data-supplier-search-root]")
        ?.querySelector("[data-supplier-suggest]");

    function hideSuggest(input) {
      const nodes = input
        ? [suggestOf(input)].filter(Boolean)
        : [...root.querySelectorAll("[data-supplier-suggest]")];
      nodes.forEach((el) => {
        el.hidden = true;
        el.innerHTML = "";
      });
    }

    function applySupplier(supplier) {
      if (nameInput) {
        nameInput.value = String(supplier.name || "").toUpperCase();
      }
      if (phoneInput) {
        phoneInput.value = `${supplier.dial || ""}${supplier.phone || ""}`;
      }
      hideSuggest();
    }

    function renderSuggest(input, results) {
      const suggest = suggestOf(input);
      if (!suggest) return;
      suggest.innerHTML = "";
      const by = input.getAttribute("data-supplier-search") || "name";
      if (!results.length) {
        const empty = document.createElement("button");
        empty.type = "button";
        empty.className = "stock-supplier-suggest-option is-empty";
        empty.disabled = true;
        empty.innerHTML =
          `<strong>No registered supplier</strong>` +
          `<small>Keep typing or enter a new supplier</small>`;
        suggest.appendChild(empty);
        suggest.hidden = false;
        return;
      }
      results.forEach((supplier) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "stock-supplier-suggest-option";
        btn.innerHTML = `<strong></strong><small></small>`;
        const phoneLabel = `${supplier.dial || ""}${supplier.phone || ""}`;
        if (by === "name") {
          btn.querySelector("strong").textContent = supplier.name || "";
          btn.querySelector("small").textContent = phoneLabel;
        } else {
          btn.querySelector("strong").textContent = phoneLabel;
          btn.querySelector("small").textContent = supplier.name || "";
        }
        btn.addEventListener("mousedown", (event) => {
          event.preventDefault();
          applySupplier(supplier);
        });
        suggest.appendChild(btn);
      });
      suggest.hidden = false;
    }

    async function runSearch(input) {
      const by = input.getAttribute("data-supplier-search") || "name";
      const query = (input.value || "").trim();
      const minLen = by === "phone" ? 3 : 2;
      if (query.length < minLen) {
        hideSuggest(input);
        return;
      }
      const mySeq = ++seq;
      const params = new URLSearchParams({ q: query, by });
      try {
        const response = await fetch(`${supplierSearchUrl}?${params}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        if (!response.ok) return;
        const data = await response.json();
        if (mySeq !== seq) return;
        if (!data || data.ok === false) return;
        const results = Array.isArray(data.results) ? data.results : [];
        renderSuggest(input, results);
        // Phone → confident match fills name straight away.
        if (by !== "phone" || !results.length) return;
        const digits = (value) => String(value || "").replace(/\D+/g, "");
        const qDigits = digits(query);
        if (qDigits.length < 7) return;
        const match =
          results.find((row) => digits(row.phone) === qDigits) ||
          (results.length === 1 && digits(results[0].phone).includes(qDigits)
            ? results[0]
            : null);
        if (match) applySupplier(match);
      } catch (_err) {
        /* ignore network errors during typing */
      }
    }

    const onType = debounce((input) => runSearch(input), 180);
    inputs.forEach((input) => {
      input.addEventListener("input", () => onType(input));
      input.addEventListener("focus", () => {
        if ((input.value || "").trim()) onType(input);
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          hideSuggest(input);
          return;
        }
        if (event.key !== "Enter") return;
        const suggest = suggestOf(input);
        if (!suggest || suggest.hidden) return;
        const first = suggest.querySelector(
          ".stock-supplier-suggest-option:not([disabled])"
        );
        if (first) {
          event.preventDefault();
          first.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        }
      });
    });
    document.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      if (event.target.closest("[data-supplier-search-root]")) return;
      hideSuggest();
    });
  }

  bindSupplierSearch();

  // One submit guard per form — the trade form holds two roots (give/receive)
  // and passes when either side has a quantity.
  if (form && !form.__storeStockSubmitBound) {
    form.__storeStockSubmitBound = true;
    form.addEventListener("submit", (event) => {
      const employeeEl = form.querySelector("[data-store-employee-id]");
      const hasQty = [...form.querySelectorAll("[data-store-qty-input]")].some(
        (input) => qtyValue(input) > 0
      );
      if (employeeEl && !employeeEl.value) {
        event.preventDefault();
        window.alert(`Select the ${personLabel} from the live search list.`);
        return;
      }
      if (!hasQty) {
        event.preventDefault();
        window.alert("Enter a quantity for at least one item.");
        return;
      }
      if (requirePrice) {
        const kind =
          tradeKinds.find((radio) => radio.checked)?.value || "";
        const priceNeeded =
          requirePrice === "always" ||
          (requirePrice === "trade_in" && kind === "trade_in");
        const missingPrice = priceNeeded
          ? [...form.querySelectorAll("[data-store-item-row]")].some((row) => {
              const qty = row.querySelector("[data-store-qty-input]");
              const price = row.querySelector("[data-store-price-input]");
              return (
                qty &&
                !qty.disabled &&
                qtyValue(qty) > 0 &&
                price &&
                !price.disabled &&
                !String(price.value || "").trim()
              );
            })
          : false;
        if (missingPrice) {
          event.preventDefault();
          window.alert("Enter a buying price on every filled line.");
        }
      }
    });
  }
  }

  // Stock-out details: custom reason shows only for "custom"; refund amount
  // only when refund is "yes". Bound per form so modals and page forms work.
  document.querySelectorAll("[data-stock-reason]").forEach((sel) => {
    const scope = sel.closest("form") || document;
    const wrap = scope.querySelector("[data-stock-custom-reason-wrap]");
    const input = wrap?.querySelector("input");
    const sync = () => {
      const show = sel.value === "custom";
      if (wrap) wrap.hidden = !show;
      if (input) input.required = show;
    };
    sel.addEventListener("change", sync);
    sync();
  });
  document.querySelectorAll("[data-stock-refund]").forEach((sel) => {
    const scope = sel.closest("form") || document;
    const wrap = scope.querySelector("[data-stock-refund-amount-wrap]");
    const input = wrap?.querySelector("input");
    const sync = () => {
      const show = sel.value === "yes";
      if (wrap) wrap.hidden = !show;
      if (input) input.required = show;
    };
    sel.addEventListener("change", sync);
    sync();
  });

  document.querySelectorAll("[data-store-stock]").forEach(initStoreStock);
})();
