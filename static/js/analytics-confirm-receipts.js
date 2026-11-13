(() => {
  const root = document.querySelector("[data-ax-confirm-receipts]");
  if (!root) return;

  const detailUrlTemplate = root.getAttribute("data-receipt-detail-url") || "";
  const confirmUrlTemplate = root.getAttribute("data-receipt-confirm-url") || "";
  const cancelUrlTemplate = root.getAttribute("data-receipt-cancel-url") || "";
  const setStatusUrlTemplate =
    root.getAttribute("data-receipt-set-status-url") || "";
  const rowsUrl = root.getAttribute("data-receipt-rows-url") || "";
  const asyncRows = root.getAttribute("data-async-rows") === "1";

  const card = root.querySelector("[data-ax-confirm-card]");
  const liveSearch = card?.querySelector("[data-ax-confirm-live-search]");
  // Keep the cart outside transformed workspace ancestors so fixed = viewport.
  const selectBar = document.querySelector("[data-ax-confirm-select-bar]");
  const selectCount = selectBar?.querySelector("[data-ax-confirm-select-count]");
  const selectBadge = selectBar?.querySelector("[data-ax-confirm-select-badge]");
  const cartToggle = selectBar?.querySelector("[data-ax-confirm-cart-toggle]");
  const cartPanel = selectBar?.querySelector("[data-ax-confirm-cart-panel]");
  const cartClose = selectBar?.querySelector("[data-ax-confirm-cart-close]");
  const selectAll = card?.querySelector("[data-ax-confirm-select-all]");
  const clearSelectedBtn = selectBar?.querySelector("[data-ax-confirm-clear-selected]");
  const confirmSelectedBtn = selectBar?.querySelector(
    "[data-ax-confirm-confirm-selected]"
  );
  const cancelSelectedBtn = selectBar?.querySelector(
    "[data-ax-confirm-cancel-selected]"
  );
  const pendingSelectedBtn = selectBar?.querySelector(
    "[data-ax-confirm-pending-selected]"
  );
  const table = card?.querySelector("[data-ax-confirm-table]");
  const tbody = card?.querySelector("[data-ax-confirm-rows]");
  const loadingEl = card?.querySelector("[data-ax-confirm-loading]");
  const emptyEl = card?.querySelector("[data-ax-confirm-empty]");
  const noResults = card?.querySelector("[data-ax-confirm-no-results]");
  const errorEl = card?.querySelector("[data-ax-confirm-error]");
  const footnote = root.querySelector("[data-ax-confirm-footnote]");
  const periodEl = root.querySelector("[data-ax-confirm-period]");
  const filtersForm = root.querySelector("[data-analytics-filters]");
  const printBtn = root.querySelector("[data-ax-confirm-print]");

  const modal = document.querySelector("[data-confirm-receipt-modal]");
  const modalBody = modal?.querySelector("[data-confirm-receipt-modal-body]");
  const modalTitle = modal?.querySelector("[data-confirm-receipt-modal-title]");
  const modalActions = modal?.querySelector("[data-confirm-receipt-modal-actions]");
  const statusWrap = modal?.querySelector("[data-confirm-receipt-status-wrap]");
  const statusSelect = modal?.querySelector("[data-confirm-receipt-status]");
  const saveStatusBtn = modal?.querySelector("[data-confirm-receipt-save-status]");

  let detailSeq = 0;
  let rowsSeq = 0;
  let currentDetail = null;
  let currentShopId = null;
  let preferConfirmedStatus = false;
  let busy = false;
  let searchTimer = 0;
  let countLabel = "receipt";
  const CART_POS_KEY = "ax-confirm-cart-pos";
  const CART_DRAG_THRESHOLD = 8;
  let cartDrag = null;
  let cartMoved = false;

  // Mount on body so position:fixed tracks the viewport, not the receipt list.
  if (selectBar && selectBar.parentElement !== document.body) {
    document.body.appendChild(selectBar);
  }

  const getCsrf = () =>
    root.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const money = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value ?? "0");
    return Math.round(n).toLocaleString(undefined, {
      maximumFractionDigits: 0,
    });
  };

  const refreshIcons = () => {
    if (typeof window.lucide?.createIcons === "function") {
      window.lucide.createIcons();
    }
  };

  const urlFor = (template, shopId, receiptId) => {
    let seen = 0;
    return String(template || "").replace(/\/0(?=\/|$)/g, () => {
      seen += 1;
      if (seen === 1) return `/${shopId}`;
      if (seen === 2) return `/${receiptId}`;
      return "/0";
    });
  };

  const statusClass = (status) => {
    if (status === "cancelled") return "is-cancelled";
    if (status === "partial_return" || status === "partial_confirmation") {
      return "is-partial";
    }
    if (status === "confirmed") return "is-confirmed";
    return "is-pending";
  };

  const pluralize = (count, label) =>
    `${count} ${label}${count === 1 ? "" : "s"}`;

  const allRows = () =>
    [...(tbody?.querySelectorAll(".ax-receipt-row[data-receipt-id]") || [])];

  const rowCheckbox = (row) => row.querySelector("[data-ax-confirm-select-row]");

  const isActionable = (row) =>
    row.getAttribute("data-can-confirm") === "1" ||
    row.getAttribute("data-can-cancel") === "1" ||
    row.getAttribute("data-can-set-pending") === "1";

  const statusKeyForUi = (status) => {
    const key = String(status || "").trim().toLowerCase();
    if (key === "confirmed") return "confirmed";
    if (key === "cancelled") return "cancelled";
    if (key === "partial_return") return "cancelled";
    return "pending";
  };

  const selectedRows = () =>
    allRows().filter((row) => Boolean(rowCheckbox(row)?.checked));

  const setListState = ({ loading = false, error = false, empty = false } = {}) => {
    if (loadingEl) loadingEl.hidden = !loading;
    if (errorEl) errorEl.hidden = !error;
    if (emptyEl) emptyEl.hidden = !empty;
    if (noResults) noResults.hidden = true;
    if (table) table.hidden = loading || error || empty;
  };

  const setCartOpen = (open) => {
    const isOpen = Boolean(open) && selectedRows().length > 0;
    if (selectBar) selectBar.classList.toggle("is-open", isOpen);
    if (cartPanel) cartPanel.hidden = !isOpen;
    if (cartToggle) {
      cartToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
      cartToggle.classList.toggle("is-open", isOpen);
    }
  };

  const clampCartPosition = (left, top) => {
    if (!selectBar) return { left, top };
    const margin = 8;
    const width = selectBar.offsetWidth || 56;
    const height = selectBar.offsetHeight || 56;
    const maxLeft = Math.max(margin, window.innerWidth - width - margin);
    const maxTop = Math.max(margin, window.innerHeight - height - margin);
    return {
      left: Math.min(Math.max(margin, left), maxLeft),
      top: Math.min(Math.max(margin, top), maxTop),
    };
  };

  const applyCartPosition = (left, top, { persist = false } = {}) => {
    if (!selectBar) return;
    const next = clampCartPosition(left, top);
    selectBar.style.setProperty("--ax-confirm-cart-x", `${next.left}px`);
    selectBar.style.setProperty("--ax-confirm-cart-y", `${next.top}px`);
    selectBar.classList.add("is-anchored");
    selectBar.classList.toggle(
      "is-left",
      next.left + selectBar.offsetWidth / 2 < window.innerWidth / 2
    );
    if (persist) {
      try {
        window.localStorage.setItem(
          CART_POS_KEY,
          JSON.stringify({ left: next.left, top: next.top })
        );
      } catch (_err) {
        /* ignore quota / private mode */
      }
    }
  };

  const restoreCartPosition = () => {
    if (!selectBar) return;
    try {
      const raw = window.localStorage.getItem(CART_POS_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (
        typeof saved?.left !== "number" ||
        typeof saved?.top !== "number" ||
        !Number.isFinite(saved.left) ||
        !Number.isFinite(saved.top)
      ) {
        return;
      }
      applyCartPosition(saved.left, saved.top);
    } catch (_err) {
      /* ignore bad storage */
    }
  };

  const keepCartInViewport = () => {
    if (!selectBar || selectBar.hidden || !selectBar.classList.contains("is-anchored")) {
      return;
    }
    const rect = selectBar.getBoundingClientRect();
    applyCartPosition(rect.left, rect.top, { persist: true });
  };

  const syncSelectionBar = () => {
    const selected = selectedRows();
    const count = selected.length;
    const canConfirm = selected.some(
      (row) => row.getAttribute("data-can-confirm") === "1"
    );
    const canCancel = selected.some(
      (row) => row.getAttribute("data-can-cancel") === "1"
    );
    const canPending = selected.some(
      (row) => row.getAttribute("data-can-set-pending") === "1"
    );

    const wasHidden = Boolean(selectBar?.hidden);
    if (selectBar) selectBar.hidden = count === 0;
    if (selectCount) selectCount.textContent = `${count} selected`;
    if (selectBadge) {
      selectBadge.textContent = String(count);
      selectBadge.hidden = count === 0;
    }
    if (cartToggle) {
      cartToggle.setAttribute(
        "aria-label",
        count ? `${count} selected receipts` : "Selected receipts"
      );
    }
    if (clearSelectedBtn) clearSelectedBtn.disabled = busy || count === 0;
    if (confirmSelectedBtn) confirmSelectedBtn.disabled = busy || !canConfirm;
    if (cancelSelectedBtn) cancelSelectedBtn.disabled = busy || !canCancel;
    if (pendingSelectedBtn) pendingSelectedBtn.disabled = busy || !canPending;

    if (count === 0) {
      setCartOpen(false);
    } else if (wasHidden) {
      restoreCartPosition();
      refreshIcons();
    }

    allRows().forEach((row) => {
      row.classList.toggle("is-selected", Boolean(rowCheckbox(row)?.checked));
    });

    if (selectAll) {
      const actionable = allRows().filter(
        (row) => isActionable(row) && !row.hidden
      );
      const checked = actionable.filter((row) => rowCheckbox(row)?.checked);
      selectAll.disabled = actionable.length === 0;
      selectAll.checked =
        actionable.length > 0 && checked.length === actionable.length;
      selectAll.indeterminate =
        checked.length > 0 && checked.length < actionable.length;
    }
  };

  const isRowSelected = (row) => Boolean(rowCheckbox(row)?.checked);

  const resetLiveSearchForNext = ({ focus = true } = {}) => {
    if (liveSearch) liveSearch.value = "";
    filterRows();
    if (focus && liveSearch) liveSearch.focus();
  };

  const clearLiveSearch = ({ focus = true } = {}) => {
    resetLiveSearchForNext({ focus });
  };

  const filterRows = () => {
    const query = (liveSearch?.value || "").trim().toLowerCase();
    const tokens = query.split(/\s+/).filter(Boolean);
    let visible = 0;
    let matchVisible = 0;
    const rows = allRows();
    rows.forEach((row) => {
      const selected = isRowSelected(row);
      const haystack = (
        row.getAttribute("data-search-text") ||
        row.textContent ||
        ""
      ).toLowerCase();
      const match =
        !tokens.length || tokens.every((token) => haystack.includes(token));
      // Keep already-selected receipts visible while searching for the next one.
      const show = selected || match;
      row.hidden = !show;
      if (show) visible += 1;
      if (match && !selected) matchVisible += 1;
    });
    const hasRows = rows.length > 0;
    if (noResults) {
      noResults.hidden =
        !hasRows || !query || matchVisible > 0 || selectedRows().length > 0;
    }
    if (table) {
      table.hidden = !hasRows || (visible === 0 && Boolean(query));
    }
    if (emptyEl) emptyEl.hidden = hasRows || Boolean(query);
    syncSelectionBar();
  };

  const clearSelection = () => {
    allRows().forEach((row) => {
      const input = rowCheckbox(row);
      if (input) input.checked = false;
    });
    syncSelectionBar();
  };

  const visibleActionableRows = () =>
    allRows().filter((row) => !row.hidden && isActionable(row));

  const pickBestSearchMatch = () => {
    const query = (liveSearch?.value || "").trim().toLowerCase();
    if (!query) return null;
    const candidates = visibleActionableRows();
    if (!candidates.length) return null;
    const exact = candidates.find(
      (row) =>
        (row.getAttribute("data-receipt-number") || "").toLowerCase() === query
    );
    if (exact) return exact;
    if (candidates.length === 1) return candidates[0];
    return null;
  };

  const selectRow = (row, { clearSearch = true } = {}) => {
    const input = rowCheckbox(row);
    if (!input || input.disabled) return false;
    input.checked = true;
    syncSelectionBar();
    if (clearSearch) {
      resetLiveSearchForNext({ focus: true });
    }
    return true;
  };

  const amountLabel = (row) => {
    if (row.amount_label) return String(row.amount_label);
    if (row.amount == null || row.amount === "") return "—";
    return `KSh ${money(row.amount)}`;
  };

  const renderRowHtml = (row) => {
    const actionable = Boolean(
      row.can_confirm || row.can_cancel || row.can_set_pending
    );
    const number = escapeHtml(row.number || "");
    const client = escapeHtml(row.client || "—");
    const phone = escapeHtml(row.client_phone || "—");
    const reference = escapeHtml((row.reference || "").trim() || "—");
    const amount = escapeHtml(amountLabel(row));
    const status = escapeHtml(row.status || "");
    const statusLabel = escapeHtml(row.status_label || "");
    const when = escapeHtml(row.when || "");
    const searchText = escapeHtml(row.search_text || row.number || "");
    const selectCell = actionable
      ? `<td class="ax-col-select" data-label="Select">
  <label class="ax-select-row">
    <span class="visually-hidden">Select ${number}</span>
    <input type="checkbox" data-ax-confirm-select-row aria-label="Select ${number}">
  </label>
</td>`
      : `<td class="ax-col-select" data-label="Select"><span class="ax-select-na" aria-hidden="true">—</span></td>`;

    return `<tr
  class="ax-receipt-row${actionable ? " is-selectable" : ""}"
  data-receipt-id="${Number(row.receipt_id) || 0}"
  data-shop-id="${Number(row.shop_id) || 0}"
  data-receipt-source="${escapeHtml(row.source || "pos")}"
  data-receipt-number="${number}"
  data-receipt-kind="${escapeHtml(row.kind || "sale")}"
  data-receipt-status="${status}"
  data-can-confirm="${row.can_confirm ? "1" : "0"}"
  data-can-cancel="${row.can_cancel ? "1" : "0"}"
  data-can-set-pending="${row.can_set_pending ? "1" : "0"}"
  data-can-edit-status="${row.can_edit_status ? "1" : "0"}"
  data-require-confirm-details="${
    row.require_mpesa_reference_on_confirm ? "1" : "0"
  }"
  data-credit-clear-on-confirm="${row.credit_clear_on_confirm ? "1" : "0"}"
  data-credit-confirm-notice="${escapeHtml(row.credit_confirm_notice || "")}"
  data-search-text="${searchText}"
  tabindex="0"
>
  ${selectCell}
  <td class="ax-cell--primary ax-confirm-receipt-status" data-label="Receipt">
    <div class="ax-confirm-stack">
      <span class="ax-confirm-stack-main">${number}</span>
      <span class="shop-receipt-status ${statusClass(status)}">${statusLabel}</span>
    </div>
  </td>
  <td class="ax-confirm-client-phone" data-label="Client">
    <div class="ax-confirm-stack">
      <span class="ax-confirm-stack-main">${client}</span>
      <span class="ax-confirm-stack-sub">${phone}</span>
    </div>
  </td>
  <td class="ax-confirm-amount" data-label="Amount">${amount}</td>
  <td class="ax-confirm-reference" data-label="Reference">${reference}</td>
  <td class="ax-confirm-when" data-label="When">${when}</td>
</tr>`;
  };

  const updateFootnote = (total) => {
    if (!footnote) return;
    footnote.textContent = `Showing ${pluralize(
      total,
      countLabel
    )} · pending first, then cancelled, then confirmed`;
  };

  const renderRows = (rows) => {
    if (!tbody) return;
    tbody.innerHTML = rows.map(renderRowHtml).join("");
    const total = rows.length;
    updateFootnote(total);
    if (!total) {
      setListState({ empty: true });
      syncSelectionBar();
      return;
    }
    setListState({});
    filterRows();
  };

  const rowsQueryUrl = () => {
    if (!rowsUrl) return "";
    // Prefer the applied URL so async rows match the filtered period on the page.
    const params = new URLSearchParams(window.location.search);
    if (![...params.keys()].length && filtersForm) {
      new FormData(filtersForm).forEach((value, key) => {
        params.append(key, String(value));
      });
    }
    if (!params.has("kind")) {
      const receiptKind = (params.get("receipt_kind") || "").trim().toLowerCase();
      if (receiptKind === "all" || receiptKind === "any") params.set("kind", "all");
      else if (receiptKind === "credit" || receiptKind === "credits") {
        params.set("kind", "credit");
      } else if (receiptKind === "sale" || receiptKind === "sales") {
        params.set("kind", "sale");
      } else {
        params.set("kind", "sale");
      }
    }
    if (!params.has("range")) {
      const now = new Date();
      const date = [
        now.getFullYear(),
        String(now.getMonth() + 1).padStart(2, "0"),
        String(now.getDate()).padStart(2, "0"),
      ].join("-");
      params.set("range", "day");
      params.set("date", date);
    }
    const qs = params.toString();
    return qs ? `${rowsUrl}?${qs}` : rowsUrl;
  };

  const loadRows = async () => {
    if (!rowsUrl) {
      setListState({ empty: allRows().length === 0 });
      syncSelectionBar();
      return;
    }
    const seq = ++rowsSeq;
    setListState({ loading: true });
    if (selectAll) {
      selectAll.checked = false;
      selectAll.indeterminate = false;
      selectAll.disabled = true;
    }
    try {
      const res = await fetch(rowsQueryUrl(), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await res.json().catch(() => ({}));
      if (seq !== rowsSeq) return;
      if (!res.ok || !data.ok) {
        setListState({ error: true });
        if (footnote) footnote.textContent = "Could not load receipts.";
        return;
      }
      countLabel = data.count_label || "receipt";
      if (periodEl && data.report_period_label) {
        periodEl.textContent = data.report_period_label;
      }
      renderRows(Array.isArray(data.rows) ? data.rows : []);
    } catch (_err) {
      if (seq !== rowsSeq) return;
      setListState({ error: true });
      if (footnote) footnote.textContent = "Could not load receipts.";
    }
  };

  const closeModal = () => {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("workspace-modal-open");
    currentDetail = null;
    currentShopId = null;
    preferConfirmedStatus = false;
    busy = false;
  };

  const openModal = () => {
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("workspace-modal-open");
    refreshIcons();
  };

  const setActionBusy = (on) => {
    busy = Boolean(on);
    if (saveStatusBtn) {
      saveStatusBtn.disabled = busy || !currentDetail?.receipt?.can_edit_status;
    }
    if (statusSelect) statusSelect.disabled = busy;
    syncSelectionBar();
  };

  const syncStatusOptions = (receipt) => {
    if (!statusSelect) return;
    const current = statusKeyForUi(receipt?.status);
    const isPartial = String(receipt?.status || "") === "partial_return";
    [...statusSelect.options].forEach((option) => {
      if (isPartial) {
        option.hidden = option.value !== "cancelled" && option.value !== current;
        option.disabled = option.hidden;
      } else {
        option.hidden = false;
        option.disabled = false;
      }
    });
    statusSelect.value = isPartial ? "cancelled" : current;
  };

  const isMpesaPayment = (method) => {
    const key = String(method || "").trim().toLowerCase();
    return key === "mpesa" || key === "both";
  };

  const shouldShowConfirmFields = (receipt, chosenStatus) => {
    if (!receipt) return false;
    if (!Boolean(receipt.can_edit_status)) return false;
    if (String(receipt.status || "") === "partial_return") return false;
    const chosen = chosenStatus || statusKeyForUi(receipt.status);
    // Always offer payment time + reference when confirming (required only if setting on).
    return chosen === "confirmed";
  };

  const syncConfirmFieldsVisibility = () => {
    const receipt = currentDetail?.receipt || {};
    const chosen = statusSelect?.value || statusKeyForUi(receipt.status);
    const wrap = modalBody?.querySelector("[data-confirm-fields]");
    const show = shouldShowConfirmFields(receipt, chosen);
    if (wrap) wrap.hidden = !show;
    const requireDetails = Boolean(receipt.require_mpesa_reference_on_confirm);
    modalBody
      ?.querySelectorAll(
        "[data-confirm-payment-at], [data-confirm-mpesa-ref]"
      )
      .forEach((input) => {
        if (requireDetails) {
          input.setAttribute("required", "required");
          input.setAttribute("aria-required", "true");
        } else {
          input.removeAttribute("required");
          input.setAttribute("aria-required", "false");
        }
      });
  };

  const renderDetail = (payload) => {
    const receipt = payload.receipt || {};
    currentDetail = payload;
    if (modalTitle) {
      modalTitle.textContent = receipt.receipt_number || "Receipt";
    }
    if (modalActions) modalActions.hidden = false;
    const canEdit = Boolean(receipt.can_edit_status);
    if (statusWrap) statusWrap.hidden = !canEdit;
    if (saveStatusBtn) {
      saveStatusBtn.hidden = !canEdit;
      saveStatusBtn.disabled = !canEdit;
    }
    syncStatusOptions(receipt);
    if (preferConfirmedStatus && statusSelect && canEdit) {
      const current = statusKeyForUi(receipt.status);
      if (current !== "confirmed" && !statusSelect.querySelector("option[value='confirmed']")?.disabled) {
        statusSelect.value = "confirmed";
      }
      preferConfirmedStatus = false;
    }

    const lines = receipt.lines || [];
    const itemsHtml = lines
      .map((line) => {
        const serials = (line.serial_numbers || [])
          .map((s) => `<code>${escapeHtml(s)}</code>`)
          .join(" ");
        const returnedNote =
          line.returned_quantity > 0
            ? `<span class="shop-receipt-muted">Returned ${line.returned_quantity} of ${line.quantity}</span>`
            : "";
        return `<li class="shop-receipt-item-row">
  <div class="shop-receipt-item-row-main">
    <strong>${escapeHtml(line.item_name)}</strong>
    ${returnedNote}
    ${serials ? `<div class="shop-receipt-serials">${serials}</div>` : ""}
  </div>
  <div class="shop-receipt-item-row-meta">
    <span>${line.remaining_quantity}<span class="shop-receipt-muted"> / ${line.quantity}</span></span>
    <span>KSh ${escapeHtml(money(line.unit_price))}</span>
    <span class="shop-receipt-item-row-total">KSh ${escapeHtml(
      money(line.remaining_total)
    )}</span>
  </div>
</li>`;
      })
      .join("");

    const existingRef = String(receipt.mpesa_receipt_number || "").trim();
    const requireConfirmDetails = Boolean(
      receipt.require_mpesa_reference_on_confirm
    );
    const optionalHint = requireConfirmDetails ? "required" : "optional";
    // Only show a previously entered confirm payment time — never auto-fill print time.
    const paymentAtValue = String(
      receipt.confirmed_payment_at_local || ""
    ).trim();
    const showConfirmFields = shouldShowConfirmFields(
      receipt,
      statusSelect?.value || statusKeyForUi(receipt.status)
    );
    const requiredAttrs = requireConfirmDetails
      ? `required aria-required="true"`
      : `aria-required="false"`;
    const isCredit = Boolean(receipt.is_credit || receipt.kind === "credit");
    const creditDue = Number(receipt.credit_due);
    const dueValue =
      Number.isFinite(creditDue) && creditDue > 0
        ? creditDue
        : Math.max(
            0,
            Number(receipt.total || 0) - Number(receipt.amount_paid || 0)
          );
    const creditAmountField = isCredit
      ? `<div class="shop-receipt-mpesa-ref" data-confirm-credit-amount-wrap>
      <label class="shop-receipt-mpesa-ref-label" for="ax-confirm-credit-amount">
        Confirm amount
        <span class="shop-receipt-muted">(due KSh ${escapeHtml(
          money(dueValue)
        )})</span>
      </label>
      <input
        id="ax-confirm-credit-amount"
        class="shop-receipt-mpesa-ref-input"
        type="number"
        name="amount"
        min="0.01"
        step="0.01"
        max="${escapeHtml(String(dueValue || ""))}"
        value="${escapeHtml(dueValue > 0 ? String(dueValue) : "")}"
        inputmode="decimal"
        data-confirm-credit-amount
        required
        aria-required="true"
      >
    </div>`
      : "";
    const confirmFieldsHtml = `<div class="shop-receipt-card shop-receipt-card--confirm" data-confirm-fields${
      showConfirmFields ? "" : " hidden"
    }>
  <div class="shop-receipt-confirm-fields">
    ${creditAmountField}
    <div class="shop-receipt-mpesa-ref" data-confirm-payment-at-wrap>
      <label class="shop-receipt-mpesa-ref-label" for="ax-confirm-payment-at">
        Payment date &amp; time
        <span class="shop-receipt-muted">(${optionalHint})</span>
      </label>
      <input
        id="ax-confirm-payment-at"
        class="shop-receipt-mpesa-ref-input"
        type="datetime-local"
        name="payment_at"
        value="${escapeHtml(paymentAtValue)}"
        data-confirm-payment-at
        ${requiredAttrs}
      >
    </div>
    <div class="shop-receipt-mpesa-ref" data-confirm-mpesa-ref-wrap>
      <label class="shop-receipt-mpesa-ref-label" for="ax-confirm-mpesa-ref">
        Reference number
        <span class="shop-receipt-muted">(${optionalHint})</span>
      </label>
      <input
        id="ax-confirm-mpesa-ref"
        class="shop-receipt-mpesa-ref-input"
        type="text"
        name="mpesa_receipt_number"
        value="${escapeHtml(existingRef)}"
        placeholder="e.g. TKL4X2ABCD"
        maxlength="40"
        autocomplete="off"
        spellcheck="false"
        data-confirm-mpesa-ref
        ${requiredAttrs}
      >
    </div>
  </div>
</div>`;
    const creditNotice = String(receipt.credit_confirm_notice || "").trim();
    const creditNoticeHtml = creditNotice
      ? `<p class="shop-receipts-status" data-credit-confirm-notice>${escapeHtml(
          creditNotice
        )}</p>`
      : "";
    const detailStatus = receipt.is_partial_confirmation
      ? "partial_confirmation"
      : receipt.status;

    if (modalBody) {
      modalBody.innerHTML = `
<div class="shop-receipt-detail">
  <div class="shop-receipt-summary">
    <div class="shop-receipt-summary-badges">
      <span class="shop-receipt-kind shop-receipt-kind--${escapeHtml(
        (receipt.kind || "").replace(/\s+/g, "_")
      )}">${escapeHtml(receipt.kind_label || "")}</span>
      <span class="shop-receipt-status ${statusClass(
        detailStatus
      )}">${escapeHtml(receipt.status_label || "")}</span>
    </div>
    ${creditNoticeHtml}
    <p class="shop-receipt-summary-when"><strong>${escapeHtml(
      receipt.created_label || "—"
    )}</strong></p>
    <p class="shop-receipt-muted">Cashier: ${escapeHtml(
      receipt.cashier || "—"
    )}</p>
  </div>

  <div class="shop-receipt-card">
    <h3>Payment &amp; client</h3>
    <dl>
      <div><dt>Payment</dt><dd>${escapeHtml(
        receipt.payment_label || "—"
      )}</dd></div>
      <div><dt>Client</dt><dd>${escapeHtml(
        receipt.client_name || "Walk-in"
      )}</dd></div>
      <div><dt>Phone</dt><dd>${escapeHtml(
        receipt.client_phone || "—"
      )}</dd></div>
      <div><dt>Shop</dt><dd>${escapeHtml(receipt.shop_name || "—")}</dd></div>
      <div class="is-total"><dt>Total</dt><dd>KSh ${escapeHtml(
        money(receipt.total)
      )}</dd></div>
    </dl>
  </div>

  <div class="shop-receipt-card shop-receipt-card--items">
    <h3>Items</h3>
    ${
      lines.length
        ? `<ul class="shop-receipt-item-list">${itemsHtml}</ul>`
        : `<p class="shop-receipt-muted">No line items on this receipt.</p>`
    }
  </div>

  ${confirmFieldsHtml}
</div>`;
    }
    syncConfirmFieldsVisibility();
    if (showConfirmFields) {
      modalBody
        ?.querySelector("[data-confirm-mpesa-ref]")
        ?.focus?.({ preventScroll: true });
    }
    refreshIcons();
  };

  const loadDetail = async (shopId, receiptId, source) => {
    if (!detailUrlTemplate || !shopId || !receiptId) return;
    const seq = ++detailSeq;
    currentShopId = shopId;
    if (modalTitle) modalTitle.textContent = "Receipt";
    if (modalActions) modalActions.hidden = true;
    if (modalBody) {
      modalBody.innerHTML = `<p class="shop-receipts-status">Loading receipt…</p>`;
    }
    openModal();
    try {
      const sourceKey = encodeURIComponent(source || "pos");
      const res = await fetch(
        `${urlFor(detailUrlTemplate, shopId, receiptId)}?source=${sourceKey}`,
        {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        }
      );
      const data = await res.json().catch(() => ({}));
      if (seq !== detailSeq) return;
      if (!res.ok || !data.ok) {
        if (modalBody) {
          modalBody.innerHTML = `<p class="shop-receipts-status is-error">${escapeHtml(
            data.error || "Could not load receipt."
          )}</p>`;
        }
        return;
      }
      renderDetail(data);
    } catch (_err) {
      if (seq !== detailSeq) return;
      if (modalBody) {
        modalBody.innerHTML = `<p class="shop-receipts-status is-error">Could not load receipt.</p>`;
      }
    }
  };

  const postReceiptStatus = async (row, status) => {
    const receiptId = Number(row.getAttribute("data-receipt-id") || 0);
    const shopId = Number(row.getAttribute("data-shop-id") || 0);
    const source = row.getAttribute("data-receipt-source") || "pos";
    if (!receiptId || !shopId || !setStatusUrlTemplate) {
      return { ok: false, error: "Missing receipt." };
    }
    if (source !== "pos") {
      return {
        ok: false,
        error: "Only sales receipts can change status here.",
      };
    }
    try {
      const body = new URLSearchParams();
      body.set("status", status);
      const res = await fetch(urlFor(setStatusUrlTemplate, shopId, receiptId), {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "X-CSRFToken": getCsrf(),
        },
        credentials: "same-origin",
        body,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        return {
          ok: false,
          error: data.error || "Action failed.",
          number: row.getAttribute("data-receipt-number") || String(receiptId),
        };
      }
      return { ok: true };
    } catch (_err) {
      return {
        ok: false,
        error: "Action failed.",
        number: row.getAttribute("data-receipt-number") || String(receiptId),
      };
    }
  };

  const rowRequiresConfirmDetails = (row) =>
    row.getAttribute("data-require-confirm-details") === "1";

  const openConfirmWithDetails = (row) => {
    preferConfirmedStatus = true;
    openRowDetail(row);
  };

  const runBulkAction = async (mode) => {
    if (busy) return;
    const selected = selectedRows();
    const status =
      mode === "confirm"
        ? "confirmed"
        : mode === "cancel"
          ? "cancelled"
          : "pending";
    const attr =
      mode === "confirm"
        ? "data-can-confirm"
        : mode === "cancel"
          ? "data-can-cancel"
          : "data-can-set-pending";
    const targets = selected.filter((row) => row.getAttribute(attr) === "1");
    if (!targets.length) return;

    if (mode === "confirm") {
      // Credits need an amount in the modal (full clear or partial confirmation).
      const creditTargets = targets.filter(
        (row) => row.getAttribute("data-receipt-kind") === "credit"
      );
      if (creditTargets.length) {
        if (creditTargets.length === 1 && targets.length === 1) {
          openConfirmWithDetails(creditTargets[0]);
          return;
        }
        window.alert(
          "Credits need a confirm amount. Open each credit, enter the amount (full balance clears; less is a partial confirmation), then save as Confirmed."
        );
        openConfirmWithDetails(creditTargets[0]);
        return;
      }
      // Always open the modal so payment time + reference can be entered
      // (required when compulsory is on; optional when off).
      if (targets.length === 1) {
        openConfirmWithDetails(targets[0]);
        return;
      }
      const needsDetails = targets.filter(rowRequiresConfirmDetails);
      if (needsDetails.length) {
        window.alert(
          "Compulsory reference is enabled. Open each receipt, enter the reference number and payment date & time, then save as Confirmed."
        );
        openConfirmWithDetails(needsDetails[0]);
        return;
      }
      // Multiple optional confirms: allow silent bulk, or open first to enter details.
      const enterDetails = window.confirm(
        `Confirm ${targets.length} receipts now?\n\nOK = confirm without reference/payment time\nCancel = open the first receipt to enter details`
      );
      if (!enterDetails) {
        openConfirmWithDetails(targets[0]);
        return;
      }
    }

    if (mode === "cancel") {
      const label =
        targets.length === 1
          ? targets[0].getAttribute("data-receipt-number") || "this receipt"
          : `${targets.length} receipts`;
      if (
        !window.confirm(
          `Set ${label} to cancelled? Remaining sale items will be restocked.`
        )
      ) {
        return;
      }
    }

    const label =
      mode === "confirm" ? "Confirm" : mode === "cancel" ? "Cancel" : "Pending";
    setActionBusy(true);
    const failures = [];
    for (const row of targets) {
      const result = await postReceiptStatus(row, status);
      if (!result.ok) {
        failures.push(
          `${result.number || "Receipt"}: ${result.error || "failed"}`
        );
      }
    }
    if (failures.length) {
      window.alert(`${label} finished with errors:\n${failures.join("\n")}`);
    }
    window.location.reload();
  };

  const openRowDetail = (row) => {
    const receiptId = Number(row.getAttribute("data-receipt-id") || 0);
    const shopId = Number(row.getAttribute("data-shop-id") || 0);
    const source = row.getAttribute("data-receipt-source") || "pos";
    if (!receiptId || !shopId) return;
    loadDetail(shopId, receiptId, source);
  };

  card?.addEventListener("click", (event) => {
    const row = event.target.closest(".ax-receipt-row[data-receipt-id]");
    if (!row || !card.contains(row)) return;
    if (event.target.closest(".ax-col-select, .ax-select-row, input")) return;
    openRowDetail(row);
  });

  card?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest(".ax-receipt-row[data-receipt-id]");
    if (!row || !card.contains(row)) return;
    if (event.target.closest("input, label")) return;
    event.preventDefault();
    openRowDetail(row);
  });

  card?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;

    if (target.matches("[data-ax-confirm-select-all]")) {
      const checked = target.checked;
      visibleActionableRows().forEach((row) => {
        const input = rowCheckbox(row);
        if (input) input.checked = checked;
      });
      syncSelectionBar();
      if (checked) {
        resetLiveSearchForNext({ focus: true });
      } else {
        filterRows();
      }
      return;
    }

    if (target.matches("[data-ax-confirm-select-row]")) {
      // Keep the row in place so consecutive checks don't jump the list.
      syncSelectionBar();
      if (liveSearch?.value) {
        resetLiveSearchForNext({ focus: false });
      }
    }
  });

  liveSearch?.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(filterRows, 120);
  });
  liveSearch?.addEventListener("search", filterRows);
  liveSearch?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const match = pickBestSearchMatch();
    if (!match) return;
    selectRow(match, { clearSearch: true });
  });

  const onCartPointerDown = (event) => {
    if (!selectBar || !cartToggle || event.button != null && event.button !== 0) {
      return;
    }
    if (event.target.closest("[data-ax-confirm-cart-panel]")) return;
    const rect = selectBar.getBoundingClientRect();
    cartMoved = false;
    cartDrag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originLeft: rect.left,
      originTop: rect.top,
    };
    try {
      cartToggle.setPointerCapture(event.pointerId);
    } catch (_err) {
      /* older browsers */
    }
  };

  const onCartPointerMove = (event) => {
    if (!cartDrag || event.pointerId !== cartDrag.pointerId || !selectBar) return;
    const dx = event.clientX - cartDrag.startX;
    const dy = event.clientY - cartDrag.startY;
    if (!cartMoved && Math.hypot(dx, dy) < CART_DRAG_THRESHOLD) return;
    cartMoved = true;
    selectBar.classList.add("is-dragging");
    setCartOpen(false);
    applyCartPosition(cartDrag.originLeft + dx, cartDrag.originTop + dy);
    event.preventDefault();
  };

  const onCartPointerUp = (event) => {
    if (!cartDrag || event.pointerId !== cartDrag.pointerId) return;
    const wasDrag = cartMoved;
    cartDrag = null;
    if (selectBar) selectBar.classList.remove("is-dragging");
    try {
      cartToggle?.releasePointerCapture(event.pointerId);
    } catch (_err) {
      /* ignore */
    }
    if (wasDrag) {
      const rect = selectBar?.getBoundingClientRect();
      if (rect) applyCartPosition(rect.left, rect.top, { persist: true });
      return;
    }
    if (!selectedRows().length) return;
    const open = cartToggle?.getAttribute("aria-expanded") !== "true";
    setCartOpen(open);
    refreshIcons();
  };

  cartToggle?.addEventListener("pointerdown", onCartPointerDown);
  cartToggle?.addEventListener("pointermove", onCartPointerMove);
  cartToggle?.addEventListener("pointerup", onCartPointerUp);
  cartToggle?.addEventListener("pointercancel", onCartPointerUp);
  cartToggle?.addEventListener("click", (event) => {
    // pointerup already handles open/close; block native click after drag.
    if (cartMoved) {
      event.preventDefault();
      event.stopPropagation();
    }
  });

  window.addEventListener("resize", keepCartInViewport);

  cartClose?.addEventListener("click", () => {
    setCartOpen(false);
  });

  clearSelectedBtn?.addEventListener("click", () => {
    if (busy) return;
    clearSelection();
    filterRows();
  });

  confirmSelectedBtn?.addEventListener("click", () => {
    runBulkAction("confirm");
  });

  cancelSelectedBtn?.addEventListener("click", () => {
    runBulkAction("cancel");
  });

  pendingSelectedBtn?.addEventListener("click", () => {
    runBulkAction("pending");
  });

  modal
    ?.querySelectorAll("[data-confirm-receipt-modal-close]")
    .forEach((el) => el.addEventListener("click", closeModal));

  statusSelect?.addEventListener("change", syncConfirmFieldsVisibility);

  printBtn?.addEventListener("click", () => {
    const printUrl =
      printBtn.getAttribute("data-print-url") ||
      root.getAttribute("data-receipt-print-url") ||
      "";
    if (!printUrl) return;
    const params = new URLSearchParams();
    if (filtersForm) {
      new FormData(filtersForm).forEach((value, key) => {
        if (value == null) return;
        const text = String(value).trim();
        if (!text && key !== "shop_id") return;
        params.append(key, text);
      });
    } else {
      new URLSearchParams(window.location.search).forEach((value, key) => {
        params.append(key, value);
      });
    }
    params.set("auto", "1");
    params.delete("download");
    params.delete("estimate");
    const href = `${printUrl}?${params.toString()}`;
    window.open(href, "_blank", "noopener");
  });

  const appendConfirmFields = (body) => {
    const receipt = currentDetail?.receipt || {};
    const requireDetails = Boolean(receipt.require_mpesa_reference_on_confirm);
    const atInput = modal?.querySelector("[data-confirm-payment-at]");
    const paymentAt = (atInput?.value || "").trim();
    const refInput = modal?.querySelector("[data-confirm-mpesa-ref]");
    const ref = (refInput?.value || "").trim();
    const amountInput = modal?.querySelector("[data-confirm-credit-amount]");
    const amount = (amountInput?.value || "").trim();
    const existingRef = String(receipt.mpesa_receipt_number || "").trim();
    const isCredit = Boolean(receipt.is_credit || receipt.kind === "credit");
    const due = Number(receipt.credit_due);
    const dueValue =
      Number.isFinite(due) && due > 0
        ? due
        : Math.max(
            0,
            Number(receipt.total || 0) - Number(receipt.amount_paid || 0)
          );

    if (requireDetails) {
      if (!ref && !existingRef) {
        window.alert(
          "Enter the reference number before confirming this receipt."
        );
        refInput?.focus();
        return false;
      }
      if (!paymentAt) {
        window.alert("Enter the payment date and time before confirming.");
        atInput?.focus();
        return false;
      }
    }
    if (isCredit && dueValue > 0) {
      const n = Number(amount);
      if (!amount || !Number.isFinite(n) || n <= 0) {
        window.alert("Enter the amount to confirm for this credit.");
        amountInput?.focus();
        return false;
      }
      if (n > dueValue + 0.0001) {
        window.alert(
          `Confirm amount cannot exceed the balance due (KSh ${money(dueValue)}).`
        );
        amountInput?.focus();
        return false;
      }
      body.set("amount", amount);
      body.set("payment_method", ref || existingRef ? "mpesa" : "cash");
    }
    if (ref) body.set("mpesa_receipt_number", ref);
    if (paymentAt) body.set("payment_at", paymentAt);
    return true;
  };

  saveStatusBtn?.addEventListener("click", async () => {
    if (busy || !currentDetail?.receipt?.can_edit_status) return;
    if (!setStatusUrlTemplate || !currentShopId || !currentDetail?.receipt?.id) {
      return;
    }
    if ((currentDetail?.receipt?.source || "pos") !== "pos") {
      window.alert("Only sales receipts can change status here.");
      return;
    }
    const status = statusSelect?.value || statusKeyForUi(currentDetail.receipt.status);
    const body = new URLSearchParams();
    body.set("status", status);
    if (status === "confirmed") {
      if (!appendConfirmFields(body)) return;
      const receipt = currentDetail.receipt || {};
      if (receipt.is_credit || receipt.kind === "credit") {
        const amount = Number(body.get("amount") || 0);
        const due = Number(receipt.credit_due);
        const dueValue =
          Number.isFinite(due) && due > 0
            ? due
            : Math.max(
                0,
                Number(receipt.total || 0) - Number(receipt.amount_paid || 0)
              );
        const full = !(dueValue > 0) || amount >= dueValue - 0.0001;
        const notice = full
          ? "This will clear the credit fully and convert it to a confirmed sale."
          : "This will record a partial confirmation for the amount entered.";
        if (!window.confirm(`${notice}\n\nContinue?`)) {
          return;
        }
      }
    }
    if (status === "cancelled") {
      const number = currentDetail?.receipt?.receipt_number || "this receipt";
      if (
        !window.confirm(
          `Cancel ${number}? Sale and credit items will be restocked.`
        )
      ) {
        return;
      }
    }
    setActionBusy(true);
    try {
      const res = await fetch(
        urlFor(setStatusUrlTemplate, currentShopId, currentDetail.receipt.id),
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-CSRFToken": getCsrf(),
          },
          credentials: "same-origin",
          body,
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        window.alert(data.error || "Could not update receipt status.");
        setActionBusy(false);
        return;
      }
      closeModal();
      window.location.reload();
    } catch (_err) {
      window.alert("Could not update receipt status.");
      setActionBusy(false);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (modal && !modal.hidden) {
      closeModal();
      return;
    }
    if (selectBar && !selectBar.hidden && selectBar.classList.contains("is-open")) {
      setCartOpen(false);
    }
  });

  if (asyncRows) {
    loadRows();
  } else {
    syncSelectionBar();
  }
})();
