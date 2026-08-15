(() => {
  const root = document.querySelector("[data-shop-expense]");
  const form = root?.querySelector("[data-shop-expense-form]");
  if (!root || !form) return;

  const verifyUrl = root.getAttribute("data-verify-login-url") || "";
  const supplierSearchUrl =
    root.getAttribute("data-expense-supplier-search-url") || "";
  const codeInput = form.querySelector("[data-expense-login-code]");
  const statusEl = form.querySelector("[data-expense-status]");
  const hintEl = form.querySelector("[data-expense-hint]");
  const submitBtn = form.querySelector("[data-expense-submit]");
  const submitLabelEl = form.querySelector("[data-expense-submit-label]");
  const supplierIdInput = form.querySelector("[data-expense-supplier-id]");
  const supplierNameInput = form.querySelector("[data-expense-supplier-name]");
  const supplierPhoneInput = form.querySelector("[data-expense-supplier-phone]");
  const supplierDialInput = form.querySelector("[data-expense-supplier-dial]");
  const supplierIsoInput = form.querySelector("[data-expense-supplier-iso]");
  const paymentInput = form.querySelector("[data-expense-payment]");
  const categoryDraft = form.querySelector("[data-expense-category-draft]");
  const nameDraft = form.querySelector("[data-expense-name-draft]");
  const amountDraft = form.querySelector("[data-expense-amount-draft]");
  const addBtn = form.querySelector("[data-expense-add]");
  const parkedWrap = form.querySelector("[data-expense-parked-wrap]");
  const parkedRoot = form.querySelector("[data-expense-parked]");
  const idleEl = form.querySelector("[data-expense-idle]");
  const clearBtn = form.querySelector("[data-expense-clear]");
  const countEl = form.querySelector("[data-expense-count]");
  const categoryTemplate = form.querySelector("[data-expense-category-template]");
  const printSupplier = form.hasAttribute("data-supplier-print");

  let verified = false;
  let verifyTimer = null;
  let verifySeq = 0;
  let searchTimer = null;
  let searchSeq = 0;
  let autoRecordTimer = null;
  let autoRecordFlight = false;

  const getCsrf = () =>
    form.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const phoneDigits = (value) => String(value || "").replace(/\D+/g, "");

  const normalizeNationalPhone = (raw, dial = "+254") => {
    let digits = phoneDigits(raw);
    const dialDigitsOnly = phoneDigits(dial);
    if (
      dialDigitsOnly &&
      digits.startsWith(dialDigitsOnly) &&
      digits.length > dialDigitsOnly.length
    ) {
      digits = digits.slice(dialDigitsOnly.length);
    }
    while (digits.startsWith("0")) digits = digits.slice(1);
    return digits.slice(0, 9);
  };

  const escapeHtml = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const categoryLabel = (value) => {
    const match = [...(categoryDraft?.options || [])].find(
      (opt) => opt.value === String(value || "")
    );
    return match?.textContent?.trim() || value || "Expense";
  };

  const categoryOptionsHtml = () =>
    categoryTemplate?.innerHTML?.trim() || categoryDraft?.innerHTML || "";

  const setStatus = (message, { ok = false, error = false } = {}) => {
    if (!statusEl) return;
    statusEl.textContent = message || "";
    statusEl.classList.toggle("is-ok", ok);
    statusEl.classList.toggle("is-error", error);
  };

  const setHint = (message, error = false) => {
    if (!hintEl) return;
    hintEl.textContent = message || "";
    hintEl.hidden = !message;
    hintEl.classList.toggle("is-error", Boolean(error && message));
    hintEl.classList.toggle("is-ready", Boolean(message && !error));
  };

  const rows = () => [...(parkedRoot?.querySelectorAll("[data-expense-row]") || [])];

  const rowValues = (row) => {
    const category = (row.querySelector("[name='category']")?.value || "").trim();
    const name = (row.querySelector("[name='name']")?.value || "").trim();
    const amount = Number(row.querySelector("[name='amount']")?.value);
    return {
      category,
      name,
      amount,
      ok:
        Boolean(category && name) &&
        Number.isFinite(amount) &&
        amount > 0 &&
        Number.isInteger(amount),
    };
  };

  const supplierReady = () => {
    const payment = (paymentInput?.value || "").trim();
    const supplierName = (supplierNameInput?.value || "").trim();
    const dial = (supplierDialInput?.value || "").trim();
    const phone = normalizeNationalPhone(
      supplierPhoneInput?.value || "",
      dial
    );
    return Boolean(payment && supplierName && dial && phone.length === 9);
  };

  const readyRows = () => rows().filter((row) => rowValues(row).ok);

  const canAutoRecord = () => {
    const ready = readyRows();
    return (
      Boolean(printSupplier) &&
      verified &&
      ready.length > 0 &&
      ready.length === rows().length &&
      supplierReady()
    );
  };

  const submitExpenseWithPrint = async () => {
    if (!printSupplier || autoRecordFlight) return false;
    if (!canAutoRecord()) return false;

    autoRecordFlight = true;
    if (submitBtn) submitBtn.disabled = true;
    setStatus("Recording expenses and printing supplier receipt…", { ok: true });
    setHint("Recording and printing…");
    try {
      const body = new FormData(form);
      body.set("ajax", "1");
      const response = await fetch(form.getAttribute("action") || window.location.href, {
        method: "POST",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        body,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        setStatus(data.error || "Could not record expense.", { error: true });
        setHint(data.error || "Could not record expense.", true);
        autoRecordFlight = false;
        if (submitBtn) submitBtn.disabled = false;
        syncCart();
        return false;
      }
      setStatus(data.message || "Expense recorded. Printing supplier receipt…", {
        ok: true,
      });
      if (data.receipt_text && window.RichcomPrinter?.printReceipt) {
        await window.RichcomPrinter.printReceipt({
          text: data.receipt_text,
          channel: data.print_via || "",
          qr: data.receipt_qr || null,
          fontStyle: data.receipt_font || null,
          ticket: data.receipt_ticket || null,
          paperWidth: data.receipt_paper_width || "",
        });
      }
      window.location.assign(data.next || window.location.href);
      return true;
    } catch (_) {
      setStatus("Network error while recording expense.", { error: true });
      setHint("Network error while recording expense.", true);
      autoRecordFlight = false;
      if (submitBtn) submitBtn.disabled = false;
      syncCart();
      return false;
    }
  };

  const queueAutoRecordAndPrint = () => {
    if (!printSupplier) return;
    window.clearTimeout(autoRecordTimer);
    autoRecordTimer = window.setTimeout(() => {
      void submitExpenseWithPrint();
    }, 280);
  };

  const syncCart = () => {
    const all = rows();
    const ready = readyRows();
    if (parkedWrap) parkedWrap.hidden = all.length === 0;
    if (idleEl) idleEl.hidden = all.length > 0;
    if (clearBtn) clearBtn.hidden = all.length === 0;
    if (countEl) {
      countEl.textContent = all.length ? String(all.length) : "";
      countEl.hidden = all.length === 0;
    }
    if (submitLabelEl) {
      if (!printSupplier) {
        submitLabelEl.textContent = ready.length
          ? `Record ${ready.length} expense${ready.length === 1 ? "" : "s"}`
          : "Record expenses";
      } else {
        submitLabelEl.textContent = ready.length
          ? `Record ${ready.length} & print`
          : "Record & print";
      }
    }
    if (!all.length) {
      setHint("Add expenses, then enter supplier details.");
    } else if (ready.length < all.length) {
      setHint("Enter amount on every expense.", true);
    } else if (!supplierReady()) {
      setHint("Enter supplier phone, name, and payment.", true);
    } else if (!verified) {
      setHint("Enter staff ID to confirm.");
    } else {
      setHint(
        printSupplier
          ? `Verified — recording ${ready.length} expense${ready.length === 1 ? "" : "s"}…`
          : `Ready to record ${ready.length} expense${ready.length === 1 ? "" : "s"}.`
      );
    }
    if (submitBtn) {
      submitBtn.disabled =
        autoRecordFlight || !(verified && ready.length > 0 && supplierReady());
    }
    if (canAutoRecord()) queueAutoRecordAndPrint();
  };

  const clearDraft = ({ keepCategory = true } = {}) => {
    if (!keepCategory && categoryDraft) categoryDraft.value = "";
    if (nameDraft) nameDraft.value = "";
    if (amountDraft) amountDraft.value = "";
  };

  const addExpense = () => {
    const category = (categoryDraft?.value || "").trim();
    const name = (nameDraft?.value || "").trim().toUpperCase();
    const amount = Number(amountDraft?.value);
    if (!category) {
      setHint("Choose a category first.", true);
      categoryDraft?.focus();
      return false;
    }
    if (!name) {
      setHint("Enter an expense name.", true);
      nameDraft?.focus();
      return false;
    }
    if (!Number.isFinite(amount) || amount <= 0 || !Number.isInteger(amount)) {
      setHint("Enter a whole amount greater than zero.", true);
      amountDraft?.focus();
      return false;
    }
    if (!parkedRoot) return false;

    const row = document.createElement("article");
    row.className = "buy-stock-pick is-selected";
    row.setAttribute("data-expense-row", "");
    row.innerHTML = `
      <div class="buy-stock-pick-head">
        <div class="buy-stock-pick-copy">
          <div class="buy-stock-pick-title">
            <strong data-expense-row-title>${escapeHtml(name)}</strong>
            <button
              type="button"
              class="buy-stock-pick-remove"
              data-expense-remove
              aria-label="Remove ${escapeHtml(name)}"
              title="Remove"
            >
              <i data-lucide="x" aria-hidden="true"></i>
            </button>
          </div>
        </div>
      </div>
      <div class="buy-stock-pick-inputs">
        <div class="stock-item-inputs stock-item-inputs--matrix">
          <div class="stock-in-field-row buy-stock-pick-fields shop-expense-row-fields">
            <label class="stock-inline-field shop-expense-field-category">
              <span>Category</span>
              <select name="category">${categoryOptionsHtml()}</select>
            </label>
            <label class="stock-inline-field shop-expense-field-amount">
              <span>Amount</span>
              <input
                type="number"
                name="amount"
                min="1"
                step="1"
                inputmode="numeric"
                value="${amount}"
              >
            </label>
          </div>
          <input type="hidden" name="name" value="${escapeHtml(name)}">
        </div>
      </div>`;
    const select = row.querySelector("[name='category']");
    if (select) select.value = category;
    parkedRoot.insertBefore(row, parkedRoot.firstElementChild);
    if (window.lucide?.createIcons) window.lucide.createIcons();
    clearDraft();
    nameDraft?.focus();
    syncCart();
    return true;
  };

  const verifyCode = async () => {
    const code = (codeInput?.value || "").trim();
    const current = ++verifySeq;
    if (code.length < 6) {
      verified = false;
      setStatus(
        code.length
          ? `Enter ${6 - code.length} more digit${6 - code.length === 1 ? "" : "s"}.`
          : ""
      );
      syncCart();
      return false;
    }
    if (!/^\d{6}$/.test(code)) {
      verified = false;
      setStatus("Staff ID must be exactly 6 digits.", { error: true });
      syncCart();
      return false;
    }
    if (!verifyUrl) {
      verified = false;
      setStatus("Verification is unavailable. Refresh and try again.", {
        error: true,
      });
      syncCart();
      return false;
    }

    try {
      const { verifyStaffLoginCode } = await import("./offline/staff.js");
      const data = await verifyStaffLoginCode({
        url: verifyUrl,
        code,
        csrfToken: getCsrf(),
      });
      if (current !== verifySeq) return false;
      if (!data.ok) {
        verified = false;
        setStatus(data.error || "Not a valid active staff ID.", { error: true });
        syncCart();
        return false;
      }
      verified = true;
      setStatus(
        data.message ||
          `Verified: ${data.name || "staff"} (${data.employee_id || code}).`,
        { ok: true }
      );
      syncCart();
      queueAutoRecordAndPrint();
      return true;
    } catch (_) {
      if (current !== verifySeq) return false;
      verified = false;
      setStatus("Could not verify staff ID. Try again.", { error: true });
      syncCart();
      return false;
    }
  };

  const hideSuggest = (wrap) => {
    const suggest = wrap?.querySelector("[data-supplier-suggest]");
    if (!suggest) return;
    suggest.hidden = true;
    suggest.innerHTML = "";
  };

  const applySupplier = (supplier) => {
    if (!supplier) return;
    if (supplierIdInput) supplierIdInput.value = String(supplier.id || "");
    if (supplierNameInput) supplierNameInput.value = supplier.name || "";
    if (supplierDialInput && supplier.dial) {
      supplierDialInput.value = supplier.dial;
    }
    if (supplierIsoInput && supplier.iso) {
      supplierIsoInput.value = supplier.iso;
    }
    if (supplierPhoneInput) {
      supplierPhoneInput.value = normalizeNationalPhone(
        supplier.phone || "",
        supplier.dial || "+254"
      );
      supplierPhoneInput.dataset.supplierResolved = "1";
    }
    const phoneRoot = form.querySelector("[data-stock-phone-field]");
    if (phoneRoot && supplier.dial) {
      const dialDisplay = phoneRoot.querySelector("[data-stock-dial-display]");
      const flagImg = phoneRoot.querySelector("[data-stock-flag-img]");
      if (dialDisplay) dialDisplay.textContent = supplier.dial;
      if (flagImg && supplier.iso) {
        flagImg.src = `https://flagcdn.com/w40/${String(supplier.iso).toLowerCase()}.png`;
      }
    }
    form.querySelectorAll("[data-supplier-search-root]").forEach(hideSuggest);
    syncCart();
  };

  const renderSuggest = (wrap, results) => {
    const suggest = wrap?.querySelector("[data-supplier-suggest]");
    if (!suggest) return;
    suggest.innerHTML = "";
    if (!results.length) {
      suggest.hidden = true;
      return;
    }
    results.forEach((row) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "stock-supplier-suggest-item";
      btn.innerHTML = `<strong></strong><span></span>`;
      btn.querySelector("strong").textContent = row.name || "";
      btn.querySelector("span").textContent =
        `${row.dial || ""} ${row.phone || ""}`.trim();
      btn.addEventListener("click", () => applySupplier(row));
      suggest.appendChild(btn);
    });
    suggest.hidden = false;
  };

  const runSupplierSearch = async (input) => {
    if (!supplierSearchUrl) return;
    const wrap = input.closest("[data-supplier-search-root]");
    const by = input.getAttribute("data-supplier-search") || "name";
    const dial = (supplierDialInput?.value || "").trim();
    let query = (input.value || "").trim();

    if (by === "phone") {
      query = normalizeNationalPhone(query, dial);
      if (supplierPhoneInput) {
        supplierPhoneInput.value = query;
        delete supplierPhoneInput.dataset.supplierResolved;
      }
    } else {
      query = query.toUpperCase();
      if (supplierNameInput) supplierNameInput.value = query;
    }
    if (supplierIdInput) supplierIdInput.value = "";
    syncCart();

    if (
      (by === "name" && query.length < 2) ||
      (by === "phone" && query.length < 3)
    ) {
      hideSuggest(wrap);
      return;
    }

    const current = ++searchSeq;
    try {
      const params = new URLSearchParams({ q: query, by, dial });
      const response = await fetch(`${supplierSearchUrl}?${params}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const data = await response.json();
      if (current !== searchSeq) return;
      const results = Array.isArray(data.results) ? data.results : [];
      renderSuggest(wrap, results);
    } catch (_) {
      /* ignore network errors while typing */
    }
  };

  const queueSupplierSearch = (input) => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => runSupplierSearch(input), 220);
  };

  const countryMenu = root.querySelector("[data-stock-country-menu]");
  const countrySearch = countryMenu?.querySelector("[data-stock-country-search]");
  const countryOptions = [
    ...(countryMenu?.querySelectorAll(".stock-country-option") || []),
  ];
  let activePhoneField = null;

  const closeCountryMenu = () => {
    if (!countryMenu) return;
    countryMenu.hidden = true;
    activePhoneField
      ?.querySelector("[data-stock-country-trigger]")
      ?.setAttribute("aria-expanded", "false");
    activePhoneField = null;
  };

  const setCountryOnField = (phoneRoot, dial, iso) => {
    if (!phoneRoot) return;
    const dialInput =
      phoneRoot.querySelector("[data-expense-supplier-dial]") ||
      phoneRoot.querySelector("[data-stock-supplier-dial]");
    const isoInput =
      phoneRoot.querySelector("[data-expense-supplier-iso]") ||
      phoneRoot.querySelector("[data-stock-supplier-iso]");
    const flagImg = phoneRoot.querySelector("[data-stock-flag-img]");
    const dialDisplay = phoneRoot.querySelector("[data-stock-dial-display]");
    if (dialInput) dialInput.value = dial;
    if (isoInput) isoInput.value = iso;
    if (dialDisplay) dialDisplay.textContent = dial;
    if (flagImg) {
      flagImg.src = `https://flagcdn.com/w40/${String(iso).toLowerCase()}.png`;
    }
    countryOptions.forEach((opt) => {
      opt.classList.toggle("is-selected", opt.dataset.dial === dial);
    });
    if (supplierPhoneInput) {
      supplierPhoneInput.value = normalizeNationalPhone(
        supplierPhoneInput.value,
        dial
      );
    }
    syncCart();
  };

  const openCountryMenu = (phoneRoot, trigger) => {
    if (!countryMenu || !phoneRoot) return;
    activePhoneField = phoneRoot;
    const rect = trigger.getBoundingClientRect();
    countryMenu.hidden = false;
    countryMenu.style.left = `${Math.max(8, rect.left)}px`;
    countryMenu.style.top = `${rect.bottom + 6}px`;
    trigger.setAttribute("aria-expanded", "true");
    if (countrySearch) {
      countrySearch.value = "";
      countryOptions.forEach((opt) => {
        opt.closest("li").hidden = false;
      });
      countrySearch.focus();
    }
  };

  form.querySelectorAll("[data-stock-country-trigger]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      const phoneRoot = trigger.closest("[data-stock-phone-field]");
      if (countryMenu && !countryMenu.hidden && activePhoneField === phoneRoot) {
        closeCountryMenu();
        return;
      }
      openCountryMenu(phoneRoot, trigger);
    });
  });

  countryOptions.forEach((opt) => {
    opt.addEventListener("click", () => {
      if (!activePhoneField) return;
      setCountryOnField(activePhoneField, opt.dataset.dial, opt.dataset.iso);
      closeCountryMenu();
    });
  });

  countrySearch?.addEventListener("input", () => {
    const q = (countrySearch.value || "").trim().toLowerCase();
    countryOptions.forEach((opt) => {
      const hay = `${opt.dataset.name || ""} ${opt.dataset.dial || ""}`;
      opt.closest("li").hidden = Boolean(q) && !hay.includes(q);
    });
  });

  document.addEventListener("click", (event) => {
    if (!countryMenu || countryMenu.hidden) return;
    if (
      countryMenu.contains(event.target) ||
      event.target.closest?.("[data-stock-country-trigger]")
    ) {
      return;
    }
    closeCountryMenu();
  });

  form.querySelectorAll("[data-supplier-search]").forEach((input) => {
    input.addEventListener("input", () => queueSupplierSearch(input));
    input.addEventListener("focus", () => queueSupplierSearch(input));
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-supplier-search-root]")) return;
    form.querySelectorAll("[data-supplier-search-root]").forEach(hideSuggest);
  });

  addBtn?.addEventListener("click", (event) => {
    event.preventDefault();
    addExpense();
  });

  [categoryDraft, nameDraft, amountDraft].forEach((el) => {
    el?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      if (el === categoryDraft && !(nameDraft?.value || "").trim()) {
        nameDraft?.focus();
        return;
      }
      if (el === nameDraft && !(amountDraft?.value || "").trim()) {
        amountDraft?.focus();
        return;
      }
      addExpense();
    });
  });

  parkedRoot?.addEventListener("click", (event) => {
    const removeBtn = event.target.closest("[data-expense-remove]");
    if (!removeBtn) return;
    event.preventDefault();
    removeBtn.closest("[data-expense-row]")?.remove();
    syncCart();
    nameDraft?.focus();
  });

  parkedRoot?.addEventListener("input", (event) => {
    const row = event.target.closest("[data-expense-row]");
    if (!row) return;
    if (event.target.matches("[name='name']")) {
      const title = row.querySelector("[data-expense-row-title]");
      if (title) title.textContent = event.target.value || "Expense";
    }
    if (event.target.matches("[name='category']")) {
      const meta = row.querySelector(".buy-stock-pick-meta");
      if (meta) meta.textContent = categoryLabel(event.target.value);
    }
    syncCart();
  });

  clearBtn?.addEventListener("click", () => {
    if (parkedRoot) parkedRoot.innerHTML = "";
    syncCart();
    nameDraft?.focus();
  });

  [paymentInput, supplierNameInput, supplierPhoneInput].forEach((el) => {
    el?.addEventListener("input", syncCart);
    el?.addEventListener("change", syncCart);
  });

  codeInput?.addEventListener("input", () => {
    verified = false;
    syncCart();
    window.clearTimeout(verifyTimer);
    verifyTimer = window.setTimeout(() => {
      verifyCode();
    }, 220);
  });
  codeInput?.addEventListener("blur", () => {
    verifyCode();
  });

  form.addEventListener("submit", async (event) => {
    const ready = readyRows();
    if (!ready.length) {
      event.preventDefault();
      setHint("Add expenses first — tap Add.", true);
      nameDraft?.focus();
      return;
    }
    if (ready.length < rows().length) {
      event.preventDefault();
      setHint("Enter amount on every expense.", true);
      return;
    }
    if (!supplierReady()) {
      event.preventDefault();
      setHint("Enter supplier phone, name, and payment.", true);
      (supplierPhoneInput?.value ? supplierNameInput : supplierPhoneInput)?.focus();
      return;
    }
    if (!verified) {
      event.preventDefault();
      const ok = await verifyCode();
      if (!ok) return;
      if (printSupplier) {
        queueAutoRecordAndPrint();
        return;
      }
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit(submitBtn || undefined);
      } else {
        form.submit();
      }
      return;
    }
    if (!printSupplier) {
      if (submitBtn) submitBtn.disabled = true;
      return;
    }

    event.preventDefault();
    await submitExpenseWithPrint();
  });

  if (window.initUppercaseInputs) window.initUppercaseInputs(form);
  syncCart();
  if ((codeInput?.value || "").trim().length === 6) {
    verifyCode();
  }
  if (window.lucide?.createIcons) window.lucide.createIcons();
})();
