(() => {
  const form = document.querySelector("[data-stock-bulk-form]");
  const panel = document.querySelector("[data-stock-mode]");
  if (!form || !panel) return;

  const mode = panel.dataset.stockMode || "view";

  const navigateWithShops = () => {
    const params = new URLSearchParams();
    params.set("mode", mode);
    const shopSelect = form.querySelector("[data-stock-shop-nav]");
    const fromSelect = form.querySelector("[data-stock-from-nav]");
    if (shopSelect?.value) params.set("shop_id", shopSelect.value);
    if (mode === "request" && fromSelect?.value) {
      params.set("requested_from_shop_id", fromSelect.value);
    }
    window.location.assign(`${window.location.pathname}?${params.toString()}`);
  };

  form.querySelectorAll("[data-stock-shop-nav], [data-stock-from-nav]").forEach((select) => {
    select.addEventListener("change", navigateWithShops);
  });

  if (mode === "view") return;

  const floatRoot = document.querySelector("[data-stock-float]");
  const emptyEl = floatRoot?.querySelector("[data-stock-float-empty]");
  const heldEl = floatRoot?.querySelector("[data-stock-float-held]");
  const heldCountEl = floatRoot?.querySelector("[data-stock-float-held-count]");
  const summaryEl = floatRoot?.querySelector("[data-stock-float-summary]");
  const linesEl = floatRoot?.querySelector("[data-stock-float-lines]");
  const clearBtn = floatRoot?.querySelector("[data-stock-float-clear]");
  const submitBtn = floatRoot?.querySelector("[data-stock-float-submit]");
  const readyCountEl = floatRoot?.querySelector("[data-stock-ready-count]");
  const readyUnitsEl = floatRoot?.querySelector("[data-stock-ready-units]");
  const usesCatalogApi = panel.hasAttribute("data-stock-catalog-api");
  let catalogBusy = usesCatalogApi && panel.hasAttribute("data-stock-catalog-busy");
  const applyPanel = floatRoot?.querySelector("[data-stock-float-apply]");
  const applyBtn = floatRoot?.querySelector("[data-stock-float-apply-btn]");
  const applyStatus = floatRoot?.querySelector("[data-stock-float-apply-status]");
  const floatSupplierName = floatRoot?.querySelector("[data-stock-float-supplier-name]");
  const floatSupplierDial = floatRoot?.querySelector("[data-stock-float-supplier-dial]");
  const floatSupplierPhone = floatRoot?.querySelector("[data-stock-float-supplier-phone]");
  const floatSupplierId = floatRoot?.querySelector("[data-stock-float-supplier-id]");
  const floatPayment = floatRoot?.querySelector("[data-stock-float-payment]");
  const floatReason = floatRoot?.querySelector("[data-stock-float-reason]");
  const floatRefund = floatRoot?.querySelector("[data-stock-float-refund]");
  const floatRefundAmount = floatRoot?.querySelector("[data-stock-float-refund-amount]");
  const floatRefundAmountWrap = floatRoot?.querySelector("[data-stock-float-refund-amount-wrap]");
  const loginCodeInput = floatRoot?.querySelector("[data-stock-float-login-code]");
  const loginStatusEl = floatRoot?.querySelector("[data-stock-float-login-status]");
  const verifyLoginUrl = form.getAttribute("data-verify-login-url") || "";
  const requiresLoginCode = Boolean(loginCodeInput);

  const getRows = () => [
    ...panel.querySelectorAll("[data-item-row][data-item-id]"),
    ...(document.querySelector("[data-stock-parked]")?.querySelectorAll("[data-item-row][data-item-id]") || []),
  ];
  // Deduplicate (parked rows are also under panel if nested).
  const rows = () => {
    const seen = new Set();
    return getRows().filter((row) => {
      const id = row.getAttribute("data-item-id");
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  };
  let appliedDetails = null;
  let loginVerified = false;
  let loginVerifyTimer = null;
  let loginVerifySeq = 0;
  let autoStockInFlight = false;
  let autoStockInTimer = null;

  const getInputsRow = (row) => {
    const next = row.nextElementSibling;
    return next?.matches?.("[data-stock-item-inputs]") ? next : null;
  };

  const findItemRowFromNode = (node) => {
    const inputsRow = node?.closest?.("[data-stock-item-inputs]");
    if (!inputsRow) return null;
    const prev = inputsRow.previousElementSibling;
    return prev?.matches?.("[data-item-row][data-item-id]") ? prev : null;
  };

  const refreshIcons = () => {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const floatToggle = floatRoot?.querySelector("[data-stock-float-toggle]");
  const floatCollapseMq = window.matchMedia("(max-width: 1199px)");
  const floatCollapseKey = `stock-float-collapsed:${mode}`;

  const setFloatCollapsed = (collapsed, { persist = true } = {}) => {
    if (!floatRoot || !floatToggle) return;
    const next = Boolean(collapsed) && floatCollapseMq.matches;
    floatRoot.classList.toggle("is-collapsed", next);
    floatToggle.setAttribute("aria-expanded", next ? "false" : "true");
    floatToggle.setAttribute(
      "aria-label",
      next ? "Open submit section" : "Close submit section"
    );
    if (persist && floatCollapseMq.matches) {
      try {
        sessionStorage.setItem(floatCollapseKey, next ? "1" : "0");
      } catch (_) {
        /* ignore */
      }
    }
  };

  const readStoredFloatCollapsed = () => {
    try {
      const stored = sessionStorage.getItem(floatCollapseKey);
      if (stored === "0") return false;
      if (stored === "1") return true;
    } catch (_) {
      /* ignore */
    }
    return true;
  };

  if (floatRoot && floatToggle) {
    setFloatCollapsed(readStoredFloatCollapsed(), { persist: false });
    floatToggle.addEventListener("click", () => {
      if (!floatCollapseMq.matches) return;
      setFloatCollapsed(!floatRoot.classList.contains("is-collapsed"));
    });
    const syncFloatCollapseForViewport = () => {
      if (!floatCollapseMq.matches) {
        floatRoot.classList.remove("is-collapsed");
        floatToggle.setAttribute("aria-expanded", "true");
        floatToggle.removeAttribute("aria-label");
        return;
      }
      setFloatCollapsed(readStoredFloatCollapsed(), { persist: false });
    };
    if (typeof floatCollapseMq.addEventListener === "function") {
      floatCollapseMq.addEventListener("change", syncFloatCollapseForViewport);
    } else if (typeof floatCollapseMq.addListener === "function") {
      floatCollapseMq.addListener(syncFloatCollapseForViewport);
    }
  }

  const tracksSerial = (row) =>
    mode !== "request" && row.dataset.trackSerial === "1";

  const getSerialRows = (row) =>
    [...(getInputsRow(row)?.querySelectorAll(".stock-serial-row") || [])];

  const normalizeSerial = (value) => String(value || "").trim().toUpperCase();

  const collectSerials = (row) => {
    const seen = new Set();
    const serials = [];
    getSerialRows(row).forEach((serialRow) => {
      const serial = normalizeSerial(
        serialRow.querySelector("[data-stock-serial-input]")?.value
      );
      if (!serial || seen.has(serial)) return;
      seen.add(serial);
      serials.push(serial);
    });
    return serials;
  };

  const updateSerialRemoveButtons = (row) => {
    const serialRows = getSerialRows(row);
    serialRows.forEach((serialRow) => {
      const removeBtn = serialRow.querySelector("[data-stock-serial-remove]");
      if (!removeBtn) return;
      removeBtn.hidden = serialRows.length <= 1;
    });
  };

  const phoneDigits = (value) => String(value || "").replace(/\D+/g, "");

  const normalizeNationalPhone = (raw, dial = "+254") => {
    let digits = phoneDigits(raw);
    const dialDigitsOnly = phoneDigits(dial);
    if (dialDigitsOnly && digits.startsWith(dialDigitsOnly) && digits.length > dialDigitsOnly.length) {
      digits = digits.slice(dialDigitsOnly.length);
    }
    while (digits.startsWith("0")) digits = digits.slice(1);
    return digits.slice(0, 9);
  };

  const setCountryOnField = (root, dial, iso) => {
    if (!root) return;
    const dialInput =
      root.querySelector("[data-stock-supplier-dial]") ||
      root.querySelector("[data-stock-float-supplier-dial]");
    const isoInput =
      root.querySelector("[data-stock-supplier-iso]") ||
      root.querySelector("[data-stock-float-supplier-iso]");
    const flagImg = root.querySelector("[data-stock-flag-img]");
    const dialDisplay = root.querySelector("[data-stock-dial-display]");
    const phoneInput =
      root.querySelector("[data-stock-supplier-phone]") ||
      root.querySelector("[data-stock-float-supplier-phone]");
    if (dialInput && dial) dialInput.value = dial;
    if (isoInput && iso) isoInput.value = iso;
    if (dialDisplay && dial) dialDisplay.textContent = dial;
    if (flagImg && iso) {
      flagImg.src = `https://flagcdn.com/w40/${String(iso).toLowerCase()}.png`;
    }
    if (phoneInput && phoneInput.value) {
      phoneInput.value = normalizeNationalPhone(phoneInput.value, dial || dialInput?.value || "+254");
    }
  };

  const normalizePhoneInput = (input) => {
    if (!(input instanceof HTMLInputElement)) return "";
    if (
      !input.matches("[data-stock-supplier-phone], [data-stock-float-supplier-phone]")
    ) {
      return input.value;
    }
    const root = input.closest("[data-stock-phone-field]") || input.parentElement;
    const dial =
      root?.querySelector("[data-stock-supplier-dial], [data-stock-float-supplier-dial]")
        ?.value || "+254";
    const start = input.selectionStart;
    const before = input.value;
    const normalized = normalizeNationalPhone(before, dial);
    if (normalized !== before) {
      input.value = normalized;
      if (typeof start === "number") {
        const next = Math.min(normalized.length, start);
        input.setSelectionRange(next, next);
      }
    }
    return normalized;
  };

  const syncRefundAmountVisibility = (root, refundValue) => {
    if (!root) return;
    const show = String(refundValue || "").toLowerCase() === "yes";
    root
      .querySelectorAll("[data-stock-refund-amount-wrap], [data-stock-float-refund-amount-wrap]")
      .forEach((wrap) => {
        wrap.hidden = !show;
        wrap.classList.toggle("is-refund-amount-visible", show);
        if (!show) {
          const amountInput = wrap.querySelector(
            "[data-stock-refund-amount], [data-stock-float-refund-amount]"
          );
          if (amountInput) amountInput.value = "";
        }
      });
  };

  const syncRefundFromSelect = (select) => {
    if (!(select instanceof HTMLSelectElement)) return;
    if (select.matches("[data-stock-float-refund]")) {
      const scope =
        select.closest("[data-stock-float-apply]") ||
        floatRoot ||
        select.parentElement;
      syncRefundAmountVisibility(scope, select.value);
      return;
    }
    if (select.matches("[data-stock-refund]")) {
      const scope =
        select.closest("[data-stock-item-inputs]") ||
        select.closest(".stock-item-inputs") ||
        select.parentElement;
      syncRefundAmountVisibility(scope, select.value);
    }
  };

  const writeSupplierMeta = (row, details) => {
    if (!details) return;
    const inputs = getInputsRow(row);
    if (!inputs) return;
    const payment = inputs.querySelector("[data-stock-payment]");
    const name = inputs.querySelector("[data-stock-supplier-name]");
    const phone = inputs.querySelector("[data-stock-supplier-phone]");
    const supplierId = inputs.querySelector("[data-stock-supplier-id]");
    if (payment) payment.value = details.payment;
    if (name) name.value = details.name;
    setCountryOnField(inputs, details.dial, details.iso || "KE");
    if (phone) phone.value = normalizeNationalPhone(details.phone, details.dial || "+254");
    if (supplierId) supplierId.value = details.supplierId ? String(details.supplierId) : "";
  };

  const writeOutMeta = (row, details) => {
    if (!details) return;
    const inputs = getInputsRow(row);
    if (!inputs) return;
    const reason = inputs.querySelector("[data-stock-reason]");
    const refund = inputs.querySelector("[data-stock-refund]");
    const amount = inputs.querySelector("[data-stock-refund-amount]");
    if (reason) reason.value = details.reason || "";
    if (refund) {
      refund.value = details.refund || "";
      syncRefundFromSelect(refund);
    }
    if (amount && details.refund === "yes") {
      amount.value = details.refundAmount || "";
    }
  };

  const syncSerialQuantity = (row) => {
    if (!tracksSerial(row)) return 0;
    const inputs = getInputsRow(row);
    const serialHidden = inputs?.querySelector("[data-stock-serials]");
    const qtyInput = inputs?.querySelector("[data-stock-qty]");
    const countEl = inputs?.querySelector("[data-stock-serial-count]");
    const serials = collectSerials(row);
    if (serialHidden) serialHidden.value = serials.join("\n");
    if (qtyInput) qtyInput.value = serials.length ? String(serials.length) : "";
    if (countEl) countEl.textContent = String(serials.length);
    updateSerialRemoveButtons(row);
    return serials.length;
  };

  const createSerialRow = (row, { serial = "", enabled = true } = {}) => {
    const list = getInputsRow(row)?.querySelector("[data-stock-serial-list]");
    if (!list) return null;

    const serialRow = document.createElement("div");
    serialRow.className = "stock-serial-row";

    let inputParent = serialRow;
    if (mode === "out") {
      const wrap = document.createElement("div");
      wrap.className = "stock-serial-input-wrap";
      wrap.setAttribute("data-serial-search-root", "");
      serialRow.appendChild(wrap);
      inputParent = wrap;
    }

    const input = document.createElement("input");
    input.type = "text";
    input.placeholder =
      mode === "out" ? "Search serial to stock out" : "Enter serial number";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.setAttribute("data-stock-serial-input", "");
    input.setAttribute("data-stock-field", "");
    if (mode === "out") input.setAttribute("data-serial-search", "");
    input.value = serial;
    input.disabled = !enabled;
    inputParent.appendChild(input);

    if (mode === "out") {
      const suggest = document.createElement("div");
      suggest.className = "stock-supplier-suggest";
      suggest.setAttribute("data-serial-suggest", "");
      suggest.hidden = true;
      inputParent.appendChild(suggest);
    }

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "stock-serial-remove";
    removeBtn.setAttribute("data-stock-serial-remove", "");
    removeBtn.setAttribute("aria-label", "Remove serial");
    removeBtn.hidden = true;
    removeBtn.innerHTML = '<i data-lucide="x" aria-hidden="true"></i>';
    serialRow.appendChild(removeBtn);

    list.appendChild(serialRow);
    refreshIcons();
    updateSerialRemoveButtons(row);
    return input;
  };

  const resetSerialList = (row) => {
    const inputs = getInputsRow(row);
    const list = inputs?.querySelector("[data-stock-serial-list]");
    if (!list) return;
    list.innerHTML = "";
    createSerialRow(row, { enabled: row.classList.contains("is-open") });
    const countEl = inputs?.querySelector("[data-stock-serial-count]");
    if (countEl) countEl.textContent = "0";
    const serialHidden = inputs?.querySelector("[data-stock-serials]");
    if (serialHidden) serialHidden.value = "";
    const qtyInput = inputs?.querySelector("[data-stock-qty]");
    if (qtyInput) qtyInput.value = "";
  };

  const refreshRowState = (row) => {
    syncSerialQuantity(row);
    syncFilled(row);
    renderSummary();
  };

  const addSerialRow = (row) => {
    const input = createSerialRow(row, { enabled: row.classList.contains("is-open") });
    refreshRowState(row);
    input?.focus();
    return input;
  };

  const getQty = (row) => {
    if (tracksSerial(row)) return syncSerialQuantity(row);
    const input = getInputsRow(row)?.querySelector("[data-stock-qty]");
    const value = Number(input?.value || 0);
    return Number.isFinite(value) && value > 0 ? value : 0;
  };

  const rowHasBuyingPrice = (row) =>
    (getInputsRow(row)?.querySelector("[data-stock-buying-price]")?.value || "").trim() !== "";

  const rowHasSupplierDetails = (row) => {
    const inputs = getInputsRow(row);
    if (!inputs) return false;
    const payment = (inputs.querySelector("[data-stock-payment]")?.value || "").trim();
    const name = (inputs.querySelector("[data-stock-supplier-name]")?.value || "").trim();
    const phone = (inputs.querySelector("[data-stock-supplier-phone]")?.value || "").trim();
    const dial = (inputs.querySelector("[data-stock-supplier-dial]")?.value || "").trim();
    return Boolean(payment && name && phone && dial);
  };

  const rowHasOutDetails = (row) => {
    const inputs = getInputsRow(row);
    if (!inputs) return false;
    const reason = (inputs.querySelector("[data-stock-reason]")?.value || "").trim();
    const refund = (inputs.querySelector("[data-stock-refund]")?.value || "").trim().toLowerCase();
    if (!reason || (refund !== "yes" && refund !== "no")) return false;
    if (refund === "yes") {
      const amount = Number(inputs.querySelector("[data-stock-refund-amount]")?.value || 0);
      return Number.isFinite(amount) && amount > 0;
    }
    return true;
  };

  const rowReadyToPark = (row) => {
    const qty = getQty(row);
    if (mode === "in") return qty > 0 && rowHasBuyingPrice(row);
    return qty > 0;
  };

  const rowIncompleteReason = (row) => {
    if (mode !== "in") return null;
    const qty = getQty(row);
    const hasPrice = rowHasBuyingPrice(row);
    if (qty > 0 && !hasPrice) return "price";
    if (hasPrice && qty === 0) return "qty";
    return null;
  };

  const focusIncompleteItem = (row, reason) => {
    setRowOpen(row, true);
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    const inputs = getInputsRow(row);
    if (reason === "price") {
      inputs?.querySelector("[data-stock-buying-price]")?.focus();
      setApplyStatus("Enter buying price before moving to another item.", true);
      return;
    }
    const qtyFocus =
      inputs?.querySelector("[data-stock-serial-input]") ||
      inputs?.querySelector("[data-stock-qty]");
    qtyFocus?.focus();
    setApplyStatus("Enter quantity or serial numbers before moving to another item.", true);
  };

  const moveItemPairToTop = (row) => {
    const inputs = getInputsRow(row);
    const parent = row.parentElement;
    if (!parent || !inputs) return;
    const anchor = parent.firstElementChild;
    if (!anchor || anchor === row) return;
    parent.insertBefore(row, anchor);
    parent.insertBefore(inputs, row.nextSibling);
  };

  const clearEmptyOpenRow = (row) => {
    if (tracksSerial(row)) resetSerialList(row);
    clearItemMeta(row);
    getInputsRow(row)
      ?.querySelectorAll("[data-stock-field]")
      .forEach((field) => {
        if (
          field.matches(
            "[data-stock-serial-input], [data-stock-serials], [data-stock-buying-price], [data-stock-payment], [data-stock-supplier-name], [data-stock-supplier-dial], [data-stock-supplier-phone]"
          )
        ) {
          return;
        }
        if (field.type === "hidden" && !field.matches("[data-stock-qty]")) return;
        if (field.tagName === "SELECT") field.value = "";
        else field.value = "";
      });
    const countEl = getInputsRow(row)?.querySelector("[data-stock-serial-count]");
    if (countEl) countEl.textContent = "0";
  };

  const syncParkedVisibility = () => {
    const root = parkedRoot();
    const wrap = root?.closest(".buy-stock-simple-parked");
    if (wrap) wrap.hidden = !root?.querySelector("[data-item-row]");
  };

  const syncItemRemoveControls = (row) => {
    if (!row) return;
    const show = row.classList.contains("is-open") || row.classList.contains("is-filled");
    row.querySelectorAll(":scope > [data-stock-item-remove]").forEach((btn) => {
      btn.hidden = !show;
    });
  };

  const removeItemRow = (row, { silent = false } = {}) => {
    if (!row) return;
    const inputs = getInputsRow(row);
    if (tracksSerial(row)) resetSerialList(row);
    clearItemMeta(row);
    inputs?.querySelectorAll("[data-stock-field]").forEach((field) => {
      if (field.tagName === "SELECT") field.value = "";
      else if (field.type !== "hidden" || field.matches("[data-stock-qty]")) {
        field.value = "";
      }
    });
    const countEl = inputs?.querySelector("[data-stock-serial-count]");
    if (countEl) countEl.textContent = "0";
    setRowOpen(row, false);
    syncFilled(row);

    if (isParkedRow(row)) {
      row.remove();
      inputs?.remove();
      syncParkedVisibility();
    } else {
      syncItemRemoveControls(row);
    }
    if (!silent) setApplyStatus("Item removed.");
  };

  const closeAndParkRow = (row) => {
    const incomplete = rowIncompleteReason(row);
    if (incomplete) {
      // Allow dismissing a partial selection instead of trapping the user.
      removeItemRow(row);
      return true;
    }
    if (rowReadyToPark(row)) {
      // Fill meta from float only when this item still needs details.
      if (mode === "in" && !rowHasSupplierDetails(row)) {
        const details = readFloatDetails();
        const floatReady =
          details.name && details.dial && details.phone && details.payment;
        if (floatReady) {
          appliedDetails = details;
          writeSupplierMeta(row, details);
        } else if (appliedDetails) {
          writeSupplierMeta(row, appliedDetails);
        }
      }
      if (mode === "out" && !rowHasOutDetails(row)) {
        const details = readFloatDetails();
        const floatReady = details.reason && (details.refund === "yes" || details.refund === "no");
        const refundOk =
          details.refund !== "yes" ||
          (Number(details.refundAmount) > 0 && Number.isFinite(Number(details.refundAmount)));
        if (floatReady && refundOk) {
          appliedDetails = details;
          writeOutMeta(row, details);
        } else if (appliedDetails) {
          writeOutMeta(row, appliedDetails);
        }
      }
      setRowOpen(row, false);
      moveItemPairToTop(row);
      syncFilled(row);
      return true;
    }
    setRowOpen(row, false);
    if (getQty(row) === 0 && !(mode === "in" && rowHasBuyingPrice(row))) {
      clearEmptyOpenRow(row);
    }
    syncFilled(row);
    return true;
  };

  const setFieldsEnabled = (row, enabled) => {
    const inputs = getInputsRow(row);
    inputs?.querySelectorAll("[data-stock-field]").forEach((field) => {
      field.disabled = !enabled;
    });
    inputs?.querySelectorAll("[data-stock-country-trigger]").forEach((btn) => {
      btn.disabled = !enabled;
    });
  };

  const setRowOpen = (row, open) => {
    const inputs = getInputsRow(row);
    row.classList.toggle("is-open", open);
    row.setAttribute("aria-expanded", String(open));
    if (inputs) inputs.hidden = !open;
    setFieldsEnabled(row, open);
    if (open) {
      const refundSelect = inputs?.querySelector("[data-stock-refund]");
      if (refundSelect) syncRefundFromSelect(refundSelect);
      const focusTarget =
        inputs?.querySelector("[data-stock-buying-price]") ||
        inputs?.querySelector("[data-stock-serial-input]") ||
        inputs?.querySelector("[data-stock-qty]");
      focusTarget?.focus();
      refreshRowState(row);
    }
    syncItemRemoveControls(row);
  };

  const syncFilled = (row) => {
    const qty = getQty(row);
    const filled = qty > 0;
    row.classList.toggle("is-filled", filled);
    syncItemRemoveControls(row);

    const current = Number(row.dataset.itemStock || 0);
    const qtyEl = row.querySelector("[data-stock-display-qty]");
    if (!qtyEl) return;

    qtyEl.classList.remove("is-projected", "is-projected-out", "is-empty");

    if (filled && mode === "in") {
      const total = current + qty;
      qtyEl.textContent = `${current} + ${qty} = ${total}`;
      qtyEl.title = `Was ${current}, adding ${qty}`;
      qtyEl.classList.add("is-projected");
      return;
    }

    if (filled && mode === "out") {
      const total = Math.max(0, current - qty);
      qtyEl.textContent = `${current} − ${qty} = ${total}`;
      qtyEl.title = `Was ${current}, removing ${qty}`;
      qtyEl.classList.add("is-projected", "is-projected-out");
      return;
    }

    qtyEl.textContent = String(current);
    qtyEl.removeAttribute("title");
    if (current === 0) qtyEl.classList.add("is-empty");
  };

  const parkedRoot = () => document.querySelector("[data-stock-parked]");

  const isParkedRow = (row) => {
    const root = parkedRoot();
    return Boolean(root && row && root.contains(row));
  };

  const collectReady = () =>
    rows()
      .map((row) => {
        const qty = getQty(row);
        if (!qty) return null;
        return {
          row,
          id: row.dataset.itemId,
          name: row.dataset.itemName || "Item",
          quantity: qty,
          held: isParkedRow(row),
        };
      })
      .filter(Boolean);

  const isCatalogBusy = () =>
    catalogBusy || panel.getAttribute("data-stock-catalog-busy") === "1";

  const setApplyStatus = (message, isError = false) => {
    if (!applyStatus) return;
    if (!message) {
      applyStatus.hidden = true;
      applyStatus.textContent = "";
      applyStatus.classList.remove("is-error");
      return;
    }
    applyStatus.hidden = false;
    applyStatus.textContent = message;
    applyStatus.classList.toggle("is-error", isError);
  };

  const floatSupplierReady = () => {
    if (mode !== "in") return false;
    const details = readFloatDetails();
    return Boolean(
      details.name &&
        details.dial &&
        details.phone &&
        details.phone.length === 9 &&
        details.payment
    );
  };

  const canSubmitStockIn = (ready) => {
    if (!ready.length) return false;
    const pricesOk = ready.every((item) => rowHasBuyingPrice(item.row));
    if (!pricesOk) return false;
    if (ready.every((item) => rowHasSupplierDetails(item.row))) return true;
    return floatSupplierReady();
  };

  const canSubmitStockOut = (ready) => {
    if (!ready.length) return false;
    return ready.every((item) => rowHasOutDetails(item.row));
  };

  const getCsrf = () =>
    form.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const setLoginStatus = (message, { ok = false, error = false } = {}) => {
    if (!loginStatusEl) return;
    loginStatusEl.textContent =
      message || "Enter an active staff member’s 6-digit ID to stock in.";
    loginStatusEl.classList.toggle("is-ok", ok);
    loginStatusEl.classList.toggle("is-error", error);
  };

  const verifyLoginCode = async () => {
    if (!requiresLoginCode) return true;
    const code = (loginCodeInput?.value || "").trim();
    const current = ++loginVerifySeq;
    if (code.length < 6) {
      loginVerified = false;
      setLoginStatus(
        code.length
          ? `Enter ${6 - code.length} more digit${6 - code.length === 1 ? "" : "s"}.`
          : ""
      );
      if (submitBtn) {
        const ready = collectReady();
        const detailsReady =
          mode === "in" ? canSubmitStockIn(ready) : mode === "out" ? canSubmitStockOut(ready) : ready.length > 0;
        submitBtn.disabled = !(detailsReady && loginVerified);
      }
      return false;
    }
    if (!/^\d{6}$/.test(code)) {
      loginVerified = false;
      setLoginStatus("Staff ID must be exactly 6 digits.", { error: true });
      if (submitBtn) submitBtn.disabled = true;
      return false;
    }
    if (!verifyLoginUrl) {
      loginVerified = false;
      setLoginStatus("Verification is unavailable. Refresh and try again.", {
        error: true,
      });
      if (submitBtn) submitBtn.disabled = true;
      return false;
    }

    try {
      const body = new URLSearchParams({ login_code: code });
      const response = await fetch(verifyLoginUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": getCsrf(),
        },
        credentials: "same-origin",
        body,
      });
      const data = await response.json().catch(() => ({}));
      if (current !== loginVerifySeq) return false;
      if (!response.ok || !data.ok) {
        loginVerified = false;
        setLoginStatus(data.error || "Not a valid active staff ID.", {
          error: true,
        });
        if (submitBtn) submitBtn.disabled = true;
        return false;
      }
      loginVerified = true;
      setLoginStatus(
        `Verified: ${data.name || "staff"} (${data.employee_id || code}).`,
        { ok: true }
      );
      renderSummary();
      return true;
    } catch (_error) {
      if (current !== loginVerifySeq) return false;
      loginVerified = false;
      setLoginStatus("Could not verify staff ID. Try again.", { error: true });
      if (submitBtn) submitBtn.disabled = true;
      return false;
    }
  };

  const renderSummary = () => {
    const ready = collectReady();
    const units = ready.reduce((sum, item) => sum + item.quantity, 0);
    const heldCount = ready.filter((item) => item.held).length;
    const hasReady = ready.length > 0;
    const detailsReady =
      mode === "in"
        ? canSubmitStockIn(ready)
        : mode === "out"
          ? canSubmitStockOut(ready)
          : hasReady;
    const busy = isCatalogBusy();
    const submitReady =
      detailsReady && (!requiresLoginCode || loginVerified) && !busy;

    if (emptyEl) emptyEl.hidden = hasReady;
    if (heldEl) {
      heldEl.hidden = heldCount === 0;
      if (heldCountEl) heldCountEl.textContent = String(heldCount);
    }
    if (summaryEl) summaryEl.hidden = true;
    if (linesEl) linesEl.hidden = !hasReady;
    if (clearBtn) clearBtn.hidden = !hasReady;
    if (applyPanel) applyPanel.hidden = !(mode === "in" || mode === "out");
    if (submitBtn) {
      submitBtn.disabled = !submitReady;
      submitBtn.classList.toggle("is-catalog-busy", busy);
    }
    if (readyCountEl) readyCountEl.textContent = String(ready.length);
    if (readyUnitsEl) readyUnitsEl.textContent = String(units);

    if (mode === "in") {
      if (!hasReady) {
        setApplyStatus("Fill items first, then autofill supplier details here.");
      } else {
        const incomplete = ready.filter(
          (item) =>
            !(
              rowHasBuyingPrice(item.row) &&
              (rowHasSupplierDetails(item.row) || floatSupplierReady())
            )
        ).length;
        if (incomplete) {
          setApplyStatus(
            floatSupplierReady()
              ? `${incomplete} item(s) still need a buying price.`
              : "Add supplier details below, and a buying price on each item.",
            true
          );
        } else if (appliedDetails || floatSupplierReady()) {
          setApplyStatus(`Ready to stock in ${ready.length} item(s).`);
        } else if (requiresLoginCode && !loginVerified) {
          setApplyStatus("Item details complete. Enter a valid staff ID to stock in.");
        } else {
          setApplyStatus("All item details complete. You can submit.");
        }
      }
    } else if (mode === "out") {
      if (!hasReady) {
        setApplyStatus("Fill items first, then autofill reason and refund here.");
      } else {
        const incomplete = ready.filter((item) => !rowHasOutDetails(item.row)).length;
        if (incomplete) {
          setApplyStatus(`${incomplete} item(s) need reason and refund details.`, true);
        } else if (appliedDetails) {
          setApplyStatus(`Stock-out details applied to ${ready.length} item(s).`);
        } else {
          setApplyStatus("All item details complete. You can submit.");
        }
      }
    } else if (!hasReady) {
      setApplyStatus("");
    }

    if (!linesEl) return;
    linesEl.innerHTML = "";
    ready.forEach((item) => {
      const complete =
        mode === "in"
          ? rowHasBuyingPrice(item.row) &&
            (rowHasSupplierDetails(item.row) || floatSupplierReady())
          : mode === "out"
            ? rowHasOutDetails(item.row)
            : true;
      const li = document.createElement("li");
      li.className = `stock-float-line stock-float-line--summary${
        complete ? " is-complete" : " is-incomplete"
      }${item.held ? " is-held" : ""}`;
      li.dataset.itemId = item.id;
      li.innerHTML = `
        <span class="stock-float-line-name"></span>
        ${item.held ? '<span class="stock-float-line-held">Held</span>' : ""}
        <span class="stock-float-line-qty"></span>
        <span class="stock-float-line-status" aria-label="${complete ? "Details complete" : "Details incomplete"}">
          <i data-lucide="${complete ? "check" : "x"}" aria-hidden="true"></i>
        </span>
        <button
          type="button"
          class="stock-float-line-remove"
          data-stock-float-remove
          data-item-id="${item.id}"
          aria-label="Remove ${String(item.name).replace(/"/g, "&quot;")}"
          title="Remove item"
        >
          <i data-lucide="x" aria-hidden="true"></i>
        </button>
      `;
      li.querySelector(".stock-float-line-name").textContent = item.name;
      li.querySelector(".stock-float-line-qty").textContent = String(item.quantity);
      linesEl.appendChild(li);
    });
    refreshIcons();
  };

  const readFloatDetails = () => {
    if (mode === "out") {
      const reason = (floatReason?.value || "").trim().toLowerCase();
      const refund = (floatRefund?.value || "").trim().toLowerCase();
      const refundAmount = (floatRefundAmount?.value || "").trim();
      if (floatRefund) syncRefundFromSelect(floatRefund);
      return { reason, refund, refundAmount };
    }
    const name = (floatSupplierName?.value || "").trim().toUpperCase();
    const dial = (floatSupplierDial?.value || "").trim();
    const iso = (floatRoot?.querySelector("[data-stock-float-supplier-iso]")?.value || "KE").trim();
    const phone = normalizeNationalPhone(floatSupplierPhone?.value || "", dial || "+254");
    if (floatSupplierPhone) floatSupplierPhone.value = phone;
    const payment = (floatPayment?.value || "").trim().toLowerCase();
    const supplierId = (floatSupplierId?.value || "").trim();
    return { name, dial, iso, phone, payment, supplierId };
  };

  const prepareStockInRows = () => {
    rows().forEach((row) => {
      if (tracksSerial(row)) syncSerialQuantity(row);
    });
    const ready = collectReady();
    if (mode === "in") {
      const details = readFloatDetails();
      if (details.name && details.dial && details.phone && details.payment) {
        appliedDetails = details;
        ready.forEach((item) => writeSupplierMeta(item.row, details));
      }
    }
    ready.forEach((item) => {
      setRowOpen(item.row, true);
      syncSerialQuantity(item.row);
      setFieldsEnabled(item.row, true);
    });
    return ready;
  };

  const queueAutoStockInAndPrint = () => {
    if (!form.hasAttribute("data-supplier-print") || mode !== "in") return;
    window.clearTimeout(autoStockInTimer);
    autoStockInTimer = window.setTimeout(() => {
      submitStockInWithPrint();
    }, 280);
  };

  const submitStockInWithPrint = async () => {
    if (!form.hasAttribute("data-supplier-print") || mode !== "in") return false;
    if (autoStockInFlight || isCatalogBusy()) return false;
    if (requiresLoginCode && !loginVerified) return false;

    const ready = prepareStockInRows();
    if (!ready.length || !canSubmitStockIn(ready)) return false;

    autoStockInFlight = true;
    if (submitBtn) submitBtn.disabled = true;
    setApplyStatus("Recording stock and printing supplier receipt…");
    try {
      const body = new FormData(form);
      body.set("ajax", "1");
      const response = await fetch(
        form.getAttribute("action") || window.location.href,
        {
          method: "POST",
          headers: { Accept: "application/json" },
          credentials: "same-origin",
          body,
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        setApplyStatus(data.error || "Could not stock in items.", true);
        autoStockInFlight = false;
        if (submitBtn) submitBtn.disabled = false;
        renderSummary();
        return false;
      }
      setApplyStatus(data.message || "Stocked in. Printing supplier receipt…");
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
      setApplyStatus("Network error while stocking in.", true);
      autoStockInFlight = false;
      if (submitBtn) submitBtn.disabled = false;
      renderSummary();
      return false;
    }
  };

  const applyDetailsToReady = () => {
    const details = readFloatDetails();

    if (mode === "out") {
      if (floatReason) floatReason.value = details.reason;
      if (floatRefund) floatRefund.value = details.refund;
      if (floatRefundAmount && details.refund === "yes") {
        floatRefundAmount.value = details.refundAmount;
      }
      if (!details.reason) {
        setApplyStatus("Choose a stock-out reason.", true);
        floatReason?.focus();
        return false;
      }
      if (details.refund !== "yes" && details.refund !== "no") {
        setApplyStatus("Choose whether a refund applies.", true);
        floatRefund?.focus();
        return false;
      }
      if (details.refund === "yes") {
        const amount = Number(details.refundAmount);
        if (!Number.isFinite(amount) || amount <= 0) {
          setApplyStatus("Enter a refund amount greater than zero.", true);
          floatRefundAmount?.focus();
          return false;
        }
      }
      appliedDetails = details;
      const ready = collectReady();
      if (!ready.length) {
        setApplyStatus("Add item quantity/serials first, then autofill again.", true);
        return false;
      }
      ready.forEach((item) => writeOutMeta(item.row, details));
      setApplyStatus(`Autofilled stock-out details on ${ready.length} item(s).`);
      renderSummary();
      return true;
    }

    if (floatSupplierName) floatSupplierName.value = details.name;
    if (floatSupplierPhone) floatSupplierPhone.value = details.phone;

    if (!details.name) {
      setApplyStatus("Enter supplier name.", true);
      floatSupplierName?.focus();
      return false;
    }
    if (!details.dial) {
      setApplyStatus("Select a country.", true);
      floatRoot
        ?.querySelector("[data-stock-float-phone-wrap] [data-stock-country-trigger]")
        ?.focus();
      return false;
    }
    if (!details.phone) {
      setApplyStatus("Enter supplier phone.", true);
      floatSupplierPhone?.focus();
      return false;
    }
    if (!details.payment) {
      setApplyStatus("Choose payment status.", true);
      floatPayment?.focus();
      return false;
    }

    appliedDetails = details;
    const ready = collectReady();
    if (!ready.length) {
      setApplyStatus("Add item quantity/serials first, then autofill again.", true);
      return false;
    }

    ready.forEach((item) => {
      writeSupplierMeta(item.row, details);
    });
    setApplyStatus(`Autofilled supplier details on ${ready.length} item(s).`);
    renderSummary();
    return true;
  };

  const clearItemMeta = (row) => {
    const inputs = getInputsRow(row);
    if (!inputs) return;
    const buying = inputs.querySelector("[data-stock-buying-price]");
    const payment = inputs.querySelector("[data-stock-payment]");
    const name = inputs.querySelector("[data-stock-supplier-name]");
    const phone = inputs.querySelector("[data-stock-supplier-phone]");
    const supplierId = inputs.querySelector("[data-stock-supplier-id]");
    const reason = inputs.querySelector("[data-stock-reason]");
    const refund = inputs.querySelector("[data-stock-refund]");
    const refundAmount = inputs.querySelector("[data-stock-refund-amount]");
    if (buying) buying.value = "";
    if (payment) payment.value = "";
    if (name) name.value = "";
    if (phone) phone.value = "";
    if (supplierId) supplierId.value = "";
    if (reason) reason.value = "";
    if (refund) refund.value = "";
    if (refundAmount) refundAmount.value = "";
    syncRefundAmountVisibility(inputs, "");
    setCountryOnField(inputs, "+254", "KE");
  };

  const clearAll = () => {
    appliedDetails = null;
    if (floatSupplierName) floatSupplierName.value = "";
    if (floatSupplierPhone) floatSupplierPhone.value = "";
    if (floatSupplierId) floatSupplierId.value = "";
    if (floatPayment) floatPayment.value = "";
    if (floatReason) floatReason.value = "";
    if (floatRefund) floatRefund.value = "";
    if (floatRefundAmount) floatRefundAmount.value = "";
    syncRefundAmountVisibility(floatRoot, "");
    setCountryOnField(floatRoot?.querySelector("[data-stock-float-phone-wrap]"), "+254", "KE");
    setApplyStatus("");

    [...rows()].forEach((row) => {
      removeItemRow(row, { silent: true });
    });
    renderSummary();
  };

  panel.addEventListener("click", (event) => {
    const itemRemoveBtn = event.target.closest("[data-stock-item-remove]");
    if (itemRemoveBtn) {
      event.preventDefault();
      event.stopPropagation();
      const row =
        itemRemoveBtn.closest("[data-item-row][data-item-id]") ||
        findItemRowFromNode(itemRemoveBtn);
      if (row) {
        removeItemRow(row);
        renderSummary();
      }
      return;
    }

    const addBtn = event.target.closest("[data-stock-serial-add]");
    if (addBtn) {
      event.preventDefault();
      const row = findItemRowFromNode(addBtn);
      if (row) addSerialRow(row);
      return;
    }

    const removeBtn = event.target.closest("[data-stock-serial-remove]");
    if (removeBtn) {
      event.preventDefault();
      const row = findItemRowFromNode(removeBtn);
      const serialRow = removeBtn.closest(".stock-serial-row");
      if (!row || !serialRow) return;
      const serialRows = getSerialRows(row);
      if (serialRows.length <= 1) {
        const input = serialRow.querySelector("[data-stock-serial-input]");
        if (input) input.value = "";
      } else {
        serialRow.remove();
      }
      refreshRowState(row);
      getSerialRows(row)
        .at(-1)
        ?.querySelector("[data-stock-serial-input]")
        ?.focus();
      return;
    }

    const toggle = event.target.closest("[data-stock-item-toggle]");
    if (!toggle) return;
    const row = toggle.closest("[data-item-row][data-item-id]");
    const allRows = rows();
    if (!row || !allRows.includes(row)) return;

    const willOpen = !row.classList.contains("is-open");

    if (willOpen) {
      const openOthers = allRows.filter(
        (other) => other !== row && other.classList.contains("is-open")
      );
      for (const other of openOthers) {
        if (!closeAndParkRow(other)) return;
      }
      setRowOpen(row, true);
      // Popup search-first: clear live search so the selected item parks cleanly.
      if (panel.hasAttribute("data-stock-catalog-search-first")) {
        const searchInput = panel.querySelector("[data-item-search]");
        if (searchInput && searchInput.value) {
          searchInput.value = "";
          searchInput.dispatchEvent(new Event("search", { bubbles: true }));
        }
      }
      row.scrollIntoView({ behavior: "smooth", block: "nearest" });
      renderSummary();
      return;
    }

    if (!closeAndParkRow(row)) return;
    renderSummary();
  });

  panel.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!target.matches("[data-stock-serial-input]")) return;
    event.preventDefault();
    event.stopPropagation();
    const row = findItemRowFromNode(target);
    if (!row) return;

    if (mode === "out") {
      const root = target.closest("[data-serial-search-root]");
      const firstOption = root?.querySelector(
        ".stock-supplier-suggest-option:not([disabled])"
      );
      if (firstOption) {
        firstOption.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        return;
      }
    }

    refreshRowState(row);
    addSerialRow(row);
  });

  panel.addEventListener("focusin", (event) => {
    if (mode !== "out") return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!target.matches("[data-serial-search]")) return;
    queueSerialSearch(target);
  });

  const onFieldChange = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (
      target.matches(
        "[data-stock-float-supplier-name], [data-stock-float-supplier-phone], [data-stock-float-supplier-dial], [data-stock-float-payment], [data-stock-float-reason], [data-stock-float-refund], [data-stock-float-refund-amount]"
      )
    ) {
      if (target.matches("[data-stock-float-refund]")) {
        syncRefundFromSelect(target);
      }
      if (target.matches("[data-stock-float-supplier-name], [data-stock-float-supplier-phone]")) {
        if (target.matches("[data-stock-float-supplier-phone]")) {
          normalizePhoneInput(target);
        } else {
          const start = target.selectionStart;
          const end = target.selectionEnd;
          target.value = target.value.toUpperCase();
          if (typeof start === "number" && typeof end === "number") {
            target.setSelectionRange(start, end);
          }
        }
        if (target.matches("[data-supplier-search]")) queueSupplierSearch(target);
      }
      renderSummary();
      if (loginVerified) queueAutoStockInAndPrint();
      return;
    }

    const itemRow = findItemRowFromNode(target);
    if (!itemRow) return;
    if (target.matches(
      "[data-stock-serial-input], [data-stock-qty], [data-stock-buying-price], [data-stock-supplier-name], [data-stock-supplier-phone], [data-stock-supplier-dial], [data-stock-payment], [data-stock-reason], [data-stock-refund], [data-stock-refund-amount]"
    )) {
      if (target.matches("[data-stock-refund]")) {
        syncRefundFromSelect(target);
      }
      if (target.matches("[data-stock-supplier-phone]")) {
        normalizePhoneInput(target);
      } else if (target.matches("[data-stock-supplier-name]")) {
        const start = target.selectionStart;
        const end = target.selectionEnd;
        target.value = target.value.toUpperCase();
        if (typeof start === "number" && typeof end === "number") {
          target.setSelectionRange(start, end);
        }
      }
      if (target.matches("[data-supplier-search]")) queueSupplierSearch(target);
      if (mode === "out" && target.matches("[data-serial-search]")) {
        const start = target.selectionStart;
        const end = target.selectionEnd;
        target.value = target.value.toUpperCase();
        if (typeof start === "number" && typeof end === "number") {
          target.setSelectionRange(start, end);
        }
        queueSerialSearch(target);
      }
      refreshRowState(itemRow);
      if (loginVerified) queueAutoStockInAndPrint();
      return;
    }
    syncFilled(itemRow);
    renderSummary();
  };

  panel.addEventListener("input", onFieldChange);
  panel.addEventListener("change", onFieldChange);
  floatRoot?.addEventListener("input", onFieldChange);
  floatRoot?.addEventListener("change", onFieldChange);

  // Dedicated refund toggle so amount field always appears for Yes.
  const onRefundChange = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!target.matches("[data-stock-refund], [data-stock-float-refund]")) return;
    syncRefundFromSelect(target);
    renderSummary();
  };
  document.addEventListener("change", onRefundChange);
  document.addEventListener("input", onRefundChange);

  if (floatRefund) syncRefundFromSelect(floatRefund);
  document.querySelectorAll("[data-stock-refund]").forEach((select) => {
    syncRefundFromSelect(select);
  });

  applyBtn?.addEventListener("click", (event) => {
    event.preventDefault();
    applyDetailsToReady();
  });

  const supplierSearchUrl = form.dataset.supplierSearchUrl || "";
  let supplierSearchTimer = null;
  let supplierSearchSeq = 0;
  let fillingSupplier = false;

  const hideSupplierSuggest = (root) => {
    const nodes = root
      ? root.querySelectorAll("[data-supplier-suggest]")
      : document.querySelectorAll("[data-supplier-suggest]");
    nodes.forEach((el) => {
      el.hidden = true;
      el.innerHTML = "";
    });
  };

  const resolveSupplierTargets = (fromInput) => {
    const floatField = fromInput.closest("[data-stock-float-apply]");
    if (floatField) {
      return {
        scope: floatRoot,
        nameInput: floatSupplierName,
        phoneInput: floatSupplierPhone,
        dialRoot: floatRoot?.querySelector("[data-stock-float-phone-wrap]"),
        dialInput: floatSupplierDial,
      };
    }
    const inputs = getInputsRow(findItemRowFromNode(fromInput));
    if (!inputs) return null;
    return {
      scope: inputs,
      nameInput: inputs.querySelector("[data-stock-supplier-name]"),
      phoneInput: inputs.querySelector("[data-stock-supplier-phone]"),
      dialRoot: inputs,
      dialInput: inputs.querySelector("[data-stock-supplier-dial]"),
    };
  };

  const applySupplierResult = (fromInput, supplier, { fillAll = false, sourceBy = "" } = {}) => {
    const targets = resolveSupplierTargets(fromInput);
    if (!targets || !supplier) return;
    fillingSupplier = true;
    const by = sourceBy || fromInput.getAttribute("data-supplier-search") || "";
    const supplierId = supplier.id != null ? String(supplier.id) : "";

    if (fillAll || by === "phone") {
      if (targets.nameInput) targets.nameInput.value = supplier.name || "";
    }
    if (fillAll || by === "name") {
      setCountryOnField(targets.dialRoot, supplier.dial || "+254", supplier.iso || "KE");
      if (targets.phoneInput) {
        targets.phoneInput.value = normalizeNationalPhone(
          supplier.phone || "",
          supplier.dial || "+254"
        );
      }
    }
    if (fillAll && targets.nameInput && !targets.nameInput.value) {
      targets.nameInput.value = supplier.name || "";
    }

    if (targets.scope === floatRoot || fromInput.closest("[data-stock-float-apply]")) {
      if (floatSupplierId) floatSupplierId.value = supplierId;
    } else {
      const idInput = targets.scope?.querySelector("[data-stock-supplier-id]");
      if (idInput) idInput.value = supplierId;
    }

    fillingSupplier = false;
    hideSupplierSuggest(targets.scope);
    const itemRow = findItemRowFromNode(fromInput);
    if (itemRow) refreshRowState(itemRow);
  };

  const renderSupplierSuggest = (input, results) => {
    const root = input.closest("[data-supplier-search-root]");
    const suggest = root?.querySelector("[data-supplier-suggest]");
    if (!suggest) return;
    suggest.innerHTML = "";
    if (!results.length) {
      suggest.hidden = true;
      return;
    }
    results.forEach((supplier) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "stock-supplier-suggest-option";
      btn.innerHTML = `<strong></strong><small></small>`;
      btn.querySelector("strong").textContent = supplier.name;
      btn.querySelector("small").textContent = `${supplier.dial}${supplier.phone}`;
      btn.addEventListener("mousedown", (event) => {
        event.preventDefault();
        applySupplierResult(input, supplier, { fillAll: true });
      });
      suggest.appendChild(btn);
    });
    suggest.hidden = false;
  };

  const runSupplierSearch = async (input) => {
    if (!supplierSearchUrl || fillingSupplier || mode !== "in") return;
    const by = input.getAttribute("data-supplier-search");
    if (!by) return;
    const query = (input.value || "").trim();
    const root = input.closest("[data-supplier-search-root]");
    if (query.length < 2) {
      hideSupplierSuggest(root);
      return;
    }

    const dial =
      root?.querySelector("[data-stock-float-supplier-dial], [data-stock-supplier-dial]")?.value ||
      "";
    const seq = ++supplierSearchSeq;
    const params = new URLSearchParams({ q: query, by });
    if (by === "phone" && dial) params.set("dial", dial);

    try {
      const response = await fetch(`${supplierSearchUrl}?${params.toString()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const data = await response.json();
      if (seq !== supplierSearchSeq) return;
      const results = Array.isArray(data.results) ? data.results : [];
      renderSupplierSuggest(input, results);
      if (results.length === 1) {
        const only = results[0];
        const digits = (value) => String(value || "").replace(/\D+/g, "");
        if (by === "phone" && digits(query).length >= 7 && digits(only.phone).includes(digits(query))) {
          applySupplierResult(input, only, { sourceBy: "phone" });
        } else if (by === "name" && query.length >= 3 && only.name.includes(query.toUpperCase())) {
          applySupplierResult(input, only, { sourceBy: "name" });
        }
      }
    } catch (_error) {
      /* ignore network errors during typing */
    }
  };

  const queueSupplierSearch = (input) => {
    window.clearTimeout(supplierSearchTimer);
    supplierSearchTimer = window.setTimeout(() => runSupplierSearch(input), 280);
  };

  const serialSearchUrl = form.dataset.serialSearchUrl || "";
  let serialSearchTimer = null;
  let serialSearchSeq = 0;

  const hideSerialSuggest = (root) => {
    const nodes = root
      ? root.querySelectorAll("[data-serial-suggest]")
      : document.querySelectorAll("[data-serial-suggest]");
    nodes.forEach((el) => {
      el.hidden = true;
      el.innerHTML = "";
    });
  };

  const otherSelectedSerials = (row, exceptInput) => {
    const selected = [];
    getSerialRows(row).forEach((serialRow) => {
      const input = serialRow.querySelector("[data-stock-serial-input]");
      if (!input || input === exceptInput) return;
      const value = normalizeSerial(input.value);
      if (value) selected.push(value);
    });
    return selected;
  };

  const renderSerialSuggest = (input, results) => {
    const root = input.closest("[data-serial-search-root]");
    const suggest = root?.querySelector("[data-serial-suggest]");
    if (!suggest) return;
    suggest.innerHTML = "";
    if (!results.length) {
      const empty = document.createElement("button");
      empty.type = "button";
      empty.className = "stock-supplier-suggest-option";
      empty.disabled = true;
      empty.innerHTML = `<strong>No matching serials</strong><small>Available at this shop only</small>`;
      suggest.appendChild(empty);
      suggest.hidden = false;
      return;
    }
    results.forEach((serial) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "stock-supplier-suggest-option";
      btn.innerHTML = `<strong></strong>`;
      btn.querySelector("strong").textContent = serial;
      btn.addEventListener("mousedown", (event) => {
        event.preventDefault();
        input.value = serial;
        hideSerialSuggest(root);
        const row = findItemRowFromNode(input);
        if (!row) return;
        refreshRowState(row);
        const next = addSerialRow(row);
        next?.focus();
      });
      suggest.appendChild(btn);
    });
    suggest.hidden = false;
  };

  const runSerialSearch = async (input) => {
    if (mode !== "out" || !serialSearchUrl) return;
    const row = findItemRowFromNode(input);
    if (!row || !tracksSerial(row)) return;
    const itemId = row.dataset.itemId || "";
    const shopId = form.querySelector("[data-stock-shop]")?.value || "";
    if (!itemId || !shopId) return;

    const root = input.closest("[data-serial-search-root]");
    const query = normalizeSerial(input.value);
    const seq = ++serialSearchSeq;
    const params = new URLSearchParams({
      item_id: itemId,
      shop_id: shopId,
      q: query,
    });
    otherSelectedSerials(row, input).forEach((serial) => params.append("exclude", serial));

    try {
      const response = await fetch(`${serialSearchUrl}?${params.toString()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const data = await response.json();
      if (seq !== serialSearchSeq) return;
      const results = Array.isArray(data.results) ? data.results : [];
      renderSerialSuggest(input, results);
      if (results.length === 1 && query && results[0] === query) {
        hideSerialSuggest(root);
      }
    } catch (_error) {
      /* ignore network errors during typing */
    }
  };

  const queueSerialSearch = (input) => {
    window.clearTimeout(serialSearchTimer);
    serialSearchTimer = window.setTimeout(() => runSerialSearch(input), 220);
  };

  const syncAllRows = () => {
    rows().forEach((row) => {
      setRowOpen(row, false);
      syncFilled(row);
    });
  };
  syncAllRows();
  document.addEventListener("stock-catalog:rendered", () => {
    // Keep open/filled state for parked rows; only close brand-new unloaded rows.
    rows().forEach((row) => {
      if (row.classList.contains("is-open") || row.classList.contains("is-filled")) return;
      setRowOpen(row, false);
      syncFilled(row);
    });
    renderSummary();
  });

  document.addEventListener("stock-catalog:busy", (event) => {
    catalogBusy = Boolean(event.detail?.busy);
    renderSummary();
  });

  clearBtn?.addEventListener("click", clearAll);

  floatRoot?.addEventListener("click", (event) => {
    const removeBtn = event.target.closest("[data-stock-float-remove]");
    if (!removeBtn) return;
    event.preventDefault();
    const itemId = removeBtn.getAttribute("data-item-id");
    if (!itemId) return;
    const row = rows().find((r) => r.getAttribute("data-item-id") === itemId);
    if (!row) return;
    removeItemRow(row);
    renderSummary();
  });

  loginCodeInput?.addEventListener("input", () => {
    loginVerified = false;
    if (submitBtn) submitBtn.disabled = true;
    window.clearTimeout(loginVerifyTimer);
    loginVerifyTimer = window.setTimeout(async () => {
      const ok = await verifyLoginCode();
      if (ok) queueAutoStockInAndPrint();
    }, 220);
  });
  loginCodeInput?.addEventListener("blur", async () => {
    const ok = await verifyLoginCode();
    if (ok) queueAutoStockInAndPrint();
  });

  form.addEventListener("submit", async (event) => {
    if (isCatalogBusy()) {
      event.preventDefault();
      setApplyStatus("Still loading items — wait a moment, then submit.", true);
      return;
    }

    const shopSelect = form.querySelector("[data-stock-shop]");
    const fromSelect = form.querySelector("[data-stock-from-shop]");
    const printSupplier = form.hasAttribute("data-supplier-print") && mode === "in";

    if (shopSelect && !shopSelect.value) {
      event.preventDefault();
      shopSelect.focus();
      return;
    }

    if (mode === "request") {
      if (fromSelect && !fromSelect.value) {
        event.preventDefault();
        fromSelect.focus();
        return;
      }
      if (shopSelect && fromSelect && shopSelect.value === fromSelect.value) {
        event.preventDefault();
        fromSelect.focus();
        return;
      }
    }

    rows().forEach((row) => {
      if (tracksSerial(row)) syncSerialQuantity(row);
    });

    const ready = collectReady();
    if (!ready.length) {
      event.preventDefault();
      setApplyStatus("Add at least one item with quantity before stocking in.", true);
      return;
    }

    if (printSupplier) {
      event.preventDefault();
      if (requiresLoginCode && !loginVerified) {
        const ok = await verifyLoginCode();
        if (!ok) {
          loginCodeInput?.focus();
          return;
        }
      }
      const details = readFloatDetails();
      if (details.name && details.dial && details.phone && details.payment) {
        appliedDetails = details;
        ready.forEach((item) => writeSupplierMeta(item.row, details));
      }
      if (!canSubmitStockIn(ready)) {
        if (!ready.every((item) => rowHasBuyingPrice(item.row))) {
          const missingPrice = ready.find((item) => !rowHasBuyingPrice(item.row));
          if (missingPrice) {
            setRowOpen(missingPrice.row, true);
            getInputsRow(missingPrice.row)
              ?.querySelector("[data-stock-buying-price]")
              ?.focus();
          }
          setApplyStatus("Enter buying price for each ready item.", true);
          return;
        }
        applyPanel.hidden = false;
        setApplyStatus("Enter supplier name, phone, and payment status.", true);
        floatSupplierPhone?.focus();
        return;
      }
      await submitStockInWithPrint();
      return;
    }

    if (requiresLoginCode && !loginVerified) {
      event.preventDefault();
      const ok = await verifyLoginCode();
      if (!ok) {
        loginCodeInput?.focus();
        return;
      }
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit(submitBtn || undefined);
      } else {
        form.submit();
      }
      return;
    }

    if (mode === "in") {
      const details = readFloatDetails();
      if (details.name && details.dial && details.phone && details.payment) {
        appliedDetails = details;
        ready.forEach((item) => writeSupplierMeta(item.row, details));
      }
      if (!canSubmitStockIn(ready)) {
        event.preventDefault();
        if (!ready.every((item) => rowHasBuyingPrice(item.row))) {
          const missingPrice = ready.find((item) => !rowHasBuyingPrice(item.row));
          if (missingPrice) {
            setRowOpen(missingPrice.row, true);
            getInputsRow(missingPrice.row)
              ?.querySelector("[data-stock-buying-price]")
              ?.focus();
          }
          setApplyStatus("Enter buying price for each ready item.", true);
          return;
        }
        applyPanel.hidden = false;
        setApplyStatus("Enter supplier name, phone, and payment status.", true);
        floatSupplierPhone?.focus();
        return;
      }
    }

    if (mode === "out") {
      if (!canSubmitStockOut(ready)) {
        event.preventDefault();
        applyPanel.hidden = false;
        setApplyStatus("Apply reason and refund details first.", true);
        floatReason?.focus();
        return;
      }
      const details = readFloatDetails();
      const refundOk =
        details.refund !== "yes" ||
        (Number(details.refundAmount) > 0 && Number.isFinite(Number(details.refundAmount)));
      const fallback =
        details.reason && (details.refund === "yes" || details.refund === "no") && refundOk
          ? details
          : appliedDetails;
      if (fallback) {
        ready.forEach((item) => {
          if (!rowHasOutDetails(item.row)) writeOutMeta(item.row, fallback);
        });
      }
    }

    rows().forEach((row) => {
      const active = getQty(row) > 0;
      if (active) {
        setRowOpen(row, true);
        syncSerialQuantity(row);
      }
      setFieldsEnabled(row, active);
    });
  });

  if (window.initUppercaseInputs) window.initUppercaseInputs(floatRoot || document);

  // Shared flag country picker (compact, one menu for all rows).
  const countryMenu =
    form.querySelector("[data-stock-country-menu]") ||
    form.parentElement?.querySelector?.("[data-stock-country-menu]") ||
    document.querySelector("[data-stock-country-menu]");
  const countrySearch = countryMenu?.querySelector("[data-stock-country-search]");
  const countryOptions = [...(countryMenu?.querySelectorAll(".stock-country-option") || [])];
  let activePhoneField = null;

  const closeCountryMenu = () => {
    if (!countryMenu) return;
    countryMenu.hidden = true;
    document
      .querySelectorAll("[data-stock-country-trigger][aria-expanded='true']")
      .forEach((btn) => btn.setAttribute("aria-expanded", "false"));
    activePhoneField = null;
    if (countrySearch) countrySearch.value = "";
    countryOptions.forEach((option) => {
      if (option.parentElement) option.parentElement.hidden = false;
    });
  };

  const openCountryMenu = (trigger) => {
    if (!countryMenu || !trigger || trigger.disabled) return;
    const phoneField = trigger.closest("[data-stock-phone-field]");
    if (!phoneField) return;

    activePhoneField = phoneField;
    const rect = trigger.getBoundingClientRect();
    const menuWidth = Math.min(264, window.innerWidth - 24);
    let left = rect.left;
    if (left + menuWidth > window.innerWidth - 12) {
      left = Math.max(12, window.innerWidth - menuWidth - 12);
    }
    let top = rect.bottom + 6;
    countryMenu.hidden = false;
    const menuHeight = countryMenu.offsetHeight || 240;
    if (top + menuHeight > window.innerHeight - 12) {
      top = Math.max(12, rect.top - menuHeight - 6);
    }
    countryMenu.style.left = `${left}px`;
    countryMenu.style.top = `${top}px`;

    document
      .querySelectorAll("[data-stock-country-trigger]")
      .forEach((btn) => btn.setAttribute("aria-expanded", "false"));
    trigger.setAttribute("aria-expanded", "true");

    const currentDial =
      phoneField.querySelector("[data-stock-supplier-dial], [data-stock-float-supplier-dial]")
        ?.value || "";
    countryOptions.forEach((option) => {
      option.classList.toggle("is-selected", option.dataset.dial === currentDial);
    });
    refreshIcons();
    countrySearch?.focus();
  };

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-stock-country-trigger]");
    if (trigger) {
      // Only handle triggers that belong to this stock form / float.
      if (!form.contains(trigger) && !(floatRoot && floatRoot.contains(trigger))) {
        return;
      }
      event.preventDefault();
      if (trigger.getAttribute("aria-expanded") === "true") {
        closeCountryMenu();
      } else {
        openCountryMenu(trigger);
      }
      return;
    }

    const option = event.target.closest(".stock-country-option");
    if (option && countryMenu && !countryMenu.hidden && countryMenu.contains(option)) {
      event.preventDefault();
      const { dial, iso } = option.dataset;
      if (activePhoneField && dial && iso) {
        setCountryOnField(activePhoneField, dial, iso);
        const itemRow = findItemRowFromNode(activePhoneField);
        if (itemRow) refreshRowState(itemRow);
      }
      closeCountryMenu();
      return;
    }

    if (
      countryMenu &&
      !countryMenu.hidden &&
      !countryMenu.contains(event.target)
    ) {
      closeCountryMenu();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeCountryMenu();
      hideSupplierSuggest();
      hideSerialSuggest();
    }
  });

  document.addEventListener("mousedown", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest("[data-supplier-search-root]")) return;
    if (target.closest("[data-serial-search-root]")) return;
    hideSupplierSuggest();
    hideSerialSuggest();
  });

  countrySearch?.addEventListener("input", () => {
    const q = countrySearch.value.trim().toLowerCase();
    countryOptions.forEach((option) => {
      const hay = `${option.dataset.name} ${option.dataset.dial} ${option.dataset.iso}`.toLowerCase();
      if (option.parentElement) {
        option.parentElement.hidden = Boolean(q) && !hay.includes(q);
      }
    });
  });

  window.addEventListener("scroll", () => {
    if (countryMenu && !countryMenu.hidden) closeCountryMenu();
  }, true);

  renderSummary();
})();
