(() => {
  const root = document.querySelector("[data-shop-expense]");
  const form = root?.querySelector("[data-shop-expense-form]");
  if (!root || !form) return;

  const verifyUrl = root.getAttribute("data-verify-login-url") || "";
  const supplierSearchUrl =
    root.getAttribute("data-expense-supplier-search-url") || "";
  const codeInput = form.querySelector("[data-expense-login-code]");
  const statusEl = form.querySelector("[data-expense-status]");
  const submitBtn = form.querySelector("[data-expense-submit]");
  const supplierIdInput = form.querySelector("[data-expense-supplier-id]");
  const supplierNameInput = form.querySelector("[data-expense-supplier-name]");
  const supplierPhoneInput = form.querySelector("[data-expense-supplier-phone]");
  const supplierDialInput = form.querySelector("[data-expense-supplier-dial]");
  const supplierIsoInput = form.querySelector("[data-expense-supplier-iso]");
  const categoryInput = form.querySelector("[data-expense-category]");
  const nameInput = form.querySelector("[data-expense-name]");
  const amountInput = form.querySelector("[data-expense-amount]");
  const paymentInput = form.querySelector("[data-expense-payment]");

  let verified = false;
  let verifyTimer = null;
  let verifySeq = 0;
  let searchTimer = null;
  let searchSeq = 0;

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

  const setStatus = (message, { ok = false, error = false } = {}) => {
    if (!statusEl) return;
    statusEl.textContent =
      message ||
      "Enter an active staff member’s 6-digit ID to record this expense.";
    statusEl.classList.toggle("is-ok", ok);
    statusEl.classList.toggle("is-error", error);
  };

  const fieldsReady = () => {
    const category = (categoryInput?.value || "").trim();
    const name = (nameInput?.value || "").trim();
    const amount = Number(amountInput?.value);
    const payment = (paymentInput?.value || "").trim();
    const supplierName = (supplierNameInput?.value || "").trim();
    const dial = (supplierDialInput?.value || "").trim();
    const phone = normalizeNationalPhone(
      supplierPhoneInput?.value || "",
      dial
    );
    return Boolean(
      category &&
        name &&
        Number.isFinite(amount) &&
        amount > 0 &&
        Number.isInteger(amount) &&
        payment &&
        supplierName &&
        dial &&
        phone.length === 9
    );
  };

  const syncSubmit = () => {
    if (submitBtn) submitBtn.disabled = !(verified && fieldsReady());
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
      syncSubmit();
      return false;
    }
    if (!/^\d{6}$/.test(code)) {
      verified = false;
      setStatus("Staff ID must be exactly 6 digits.", { error: true });
      syncSubmit();
      return false;
    }
    if (!verifyUrl) {
      verified = false;
      setStatus("Verification is unavailable. Refresh and try again.", {
        error: true,
      });
      syncSubmit();
      return false;
    }

    try {
      const body = new URLSearchParams({ login_code: code });
      const response = await fetch(verifyUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": getCsrf(),
        },
        credentials: "same-origin",
        body,
      });
      const data = await response.json().catch(() => ({}));
      if (current !== verifySeq) return false;
      if (!response.ok || !data.ok) {
        verified = false;
        setStatus(data.error || "Not a valid active staff ID.", { error: true });
        syncSubmit();
        return false;
      }
      verified = true;
      setStatus(
        `Verified: ${data.name || "staff"} (${data.employee_id || code}).`,
        { ok: true }
      );
      syncSubmit();
      return true;
    } catch (_) {
      if (current !== verifySeq) return false;
      verified = false;
      setStatus("Could not verify staff ID. Try again.", { error: true });
      syncSubmit();
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
    syncSubmit();
  };

  const renderSuggest = (wrap, results, { by = "name" } = {}) => {
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
    syncSubmit();

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
      renderSuggest(wrap, results, { by });
    } catch (_) {
      /* ignore network errors while typing */
    }
  };

  const queueSupplierSearch = (input) => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => runSupplierSearch(input), 220);
  };

  // Country picker scoped to this expense form (avoids clashing with buy-stock).
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
    syncSubmit();
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

  [
    categoryInput,
    nameInput,
    amountInput,
    paymentInput,
    supplierNameInput,
    supplierPhoneInput,
  ].forEach((el) => {
    el?.addEventListener("input", syncSubmit);
    el?.addEventListener("change", syncSubmit);
  });

  codeInput?.addEventListener("input", () => {
    verified = false;
    syncSubmit();
    window.clearTimeout(verifyTimer);
    verifyTimer = window.setTimeout(() => {
      verifyCode();
    }, 220);
  });
  codeInput?.addEventListener("blur", () => {
    verifyCode();
  });

  form.addEventListener("submit", async (event) => {
    if (!fieldsReady()) {
      event.preventDefault();
      setStatus("Fill all expense and supplier fields first.", { error: true });
      return;
    }
    const printSupplier = form.hasAttribute("data-supplier-print");
    if (!verified) {
      event.preventDefault();
      const ok = await verifyCode();
      if (!ok) return;
      if (!printSupplier) {
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit(submitBtn || undefined);
        } else {
          form.submit();
        }
        return;
      }
    }
    if (!printSupplier) {
      if (submitBtn) submitBtn.disabled = true;
      return;
    }

    event.preventDefault();
    if (submitBtn) submitBtn.disabled = true;
    setStatus("Recording expense and printing supplier receipt…");
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
        if (submitBtn) submitBtn.disabled = false;
        return;
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
      const next = data.next || window.location.href;
      window.location.assign(next);
    } catch (_) {
      setStatus("Network error while recording expense.", { error: true });
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  if (window.initUppercaseInputs) window.initUppercaseInputs(form);
  syncSubmit();
  if ((codeInput?.value || "").trim().length === 6) {
    verifyCode();
  }
  if (window.lucide?.createIcons) window.lucide.createIcons();
})();
