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

  const verifyUrl = root.getAttribute("data-verify-login-url") || "";

  const modal = document.querySelector("[data-confirm-receipt-modal]");
  const modalBody = modal?.querySelector("[data-confirm-receipt-modal-body]");
  const modalTitle = modal?.querySelector("[data-confirm-receipt-modal-title]");
  const modalActions = modal?.querySelector("[data-confirm-receipt-modal-actions]");
  const startConfirmBtn = modal?.querySelector("[data-confirm-receipt-start]");
  const setPendingBtn = modal?.querySelector("[data-confirm-receipt-set-pending]");
  const setCancelledBtn = modal?.querySelector(
    "[data-confirm-receipt-set-cancelled]"
  );
  const cancelConfirmBtn = modal?.querySelector(
    "[data-confirm-receipt-cancel-mode]"
  );
  const submitConfirmBtn = modal?.querySelector(
    "[data-confirm-receipt-submit]"
  );

  let detailSeq = 0;
  let rowsSeq = 0;
  let currentDetail = null;
  let currentShopId = null;
  let autoConfirmOnLoad = false;
  let confirmMode = false;
  let confirmVerified = false;
  let verifyTimer = null;
  let verifySeq = 0;
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

  const todayIso = () => {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  };

  const splitLocalDateTime = (value) => {
    const raw = String(value || "").trim();
    if (!raw.includes("T")) {
      return { date: raw.slice(0, 10) || "", time: "" };
    }
    const [datePart, timePart = ""] = raw.split("T");
    return {
      date: (datePart || "").slice(0, 10),
      time: (timePart || "").slice(0, 5),
    };
  };

  // Default date to today; leave time empty for easy entry.
  const defaultConfirmPaymentParts = (receipt = {}) => {
    const saved = splitLocalDateTime(receipt.confirmed_payment_at_local);
    if (saved.date && saved.time) return saved;
    return {
      date: saved.date || todayIso(),
      time: saved.time || "",
    };
  };

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
    const paidWhen = escapeHtml(row.paid_when || "—");
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
  <td class="ax-confirm-sold-by" data-label="Sold by">${escapeHtml(
    row.sold_by || "—"
  )}</td>
  <td class="ax-confirm-sold-as" data-label="Sold as">${escapeHtml(
    row.sold_as_label || "—"
  )}</td>
  <td class="ax-confirm-amount" data-label="Amount">${amount}</td>
  <td class="ax-confirm-reference" data-label="Reference">${reference}</td>
  <td class="ax-confirm-when" data-label="Printed">${when}</td>
  <td class="ax-confirm-when ax-confirm-paid" data-label="Paid">${paidWhen}</td>
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
        params.set("kind", "all");
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
    autoConfirmOnLoad = false;
    confirmMode = false;
    confirmVerified = false;
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
    syncActionButtons();
    syncSelectionBar();
  };

  const isCreditReceipt = (receipt = {}) =>
    Boolean(receipt.is_credit || receipt.kind === "credit");

  const creditDueValue = (receipt = {}) => {
    const due = Number(receipt.credit_due);
    if (Number.isFinite(due) && due > 0) return due;
    const total = Number(receipt.total);
    const paid = Number(receipt.amount_paid);
    if (Number.isFinite(total)) {
      const bal = total - (Number.isFinite(paid) ? paid : 0);
      return bal > 0 ? bal : 0;
    }
    return 0;
  };

  const setConfirmStatus = (message, { ok = false, error = false } = {}) => {
    const el = modalBody?.querySelector("[data-confirm-status]");
    if (!el) return;
    el.textContent = message || "";
    el.classList.toggle("is-ok", ok);
    el.classList.toggle("is-error", error);
  };

  const confirmFieldValues = () => {
    const receipt = currentDetail?.receipt || {};
    const paymentDate = (
      modalBody?.querySelector("[data-confirm-payment-date]")?.value || ""
    ).trim();
    const paymentTime = (
      modalBody?.querySelector("[data-confirm-payment-time]")?.value || ""
    ).trim();
    const paymentAt =
      paymentDate && paymentTime ? `${paymentDate}T${paymentTime}` : "";
    const ref = (
      modalBody?.querySelector("[data-confirm-mpesa-ref]")?.value || ""
    ).trim();
    const amount = (
      modalBody?.querySelector("[data-confirm-credit-amount]")?.value || ""
    ).trim();
    const existingRef = String(receipt.mpesa_receipt_number || "").trim();
    const requireDetails = Boolean(receipt.require_mpesa_reference_on_confirm);
    const credit = isCreditReceipt(receipt);
    const due = creditDueValue(receipt);
    const method = (
      modalBody?.querySelector("[data-confirm-payment-method]")?.value || ""
    )
      .trim()
      .toLowerCase();
    const cashAmount = (
      modalBody?.querySelector("[data-confirm-cash-amount]")?.value || ""
    ).trim();
    const mpesaAmount = (
      modalBody?.querySelector("[data-confirm-mpesa-amount]")?.value || ""
    ).trim();
    const cardAmount = (
      modalBody?.querySelector("[data-confirm-card-amount]")?.value || ""
    ).trim();
    return {
      paymentAt,
      paymentDate,
      paymentTime,
      ref,
      amount,
      existingRef,
      requireDetails,
      credit,
      due,
      method,
      cashAmount,
      mpesaAmount,
      cardAmount,
    };
  };

  const syncSubmitConfirm = () => {
    if (!submitConfirmBtn) return;
    const {
      paymentAt,
      ref,
      amount,
      existingRef,
      requireDetails,
      credit,
      due,
      method,
      cashAmount,
      mpesaAmount,
      cardAmount,
    } = confirmFieldValues();
    const detailsOk =
      !requireDetails ||
      ((Boolean(ref) || Boolean(existingRef)) && Boolean(paymentAt));
    const amountOk =
      !credit || due <= 0 || (Boolean(amount) && Number(amount) > 0);
    const splitValues = {
      cash: cashAmount,
      mpesa: mpesaAmount,
      card: cardAmount,
    };
    const splitChannels = confirmMethodChannels(method);
    const splitOk =
      credit ||
      splitChannels.length < 2 ||
      splitChannels.every(
        (channel) =>
          Boolean(splitValues[channel]) &&
          Number.isFinite(Number(splitValues[channel]))
      );
    submitConfirmBtn.disabled = !(
      confirmMode &&
      !busy &&
      confirmVerified &&
      detailsOk &&
      amountOk &&
      splitOk
    );
  };

  const syncActionButtons = () => {
    const receipt = currentDetail?.receipt || {};
    if (startConfirmBtn) {
      startConfirmBtn.hidden = confirmMode || !receipt.can_confirm;
      startConfirmBtn.disabled = busy;
    }
    if (setPendingBtn) {
      setPendingBtn.hidden = confirmMode || !receipt.can_set_pending;
      setPendingBtn.disabled = busy;
    }
    if (setCancelledBtn) {
      setCancelledBtn.hidden = confirmMode || !receipt.can_cancel;
      setCancelledBtn.disabled = busy;
    }
    if (cancelConfirmBtn) {
      cancelConfirmBtn.hidden = !confirmMode;
      cancelConfirmBtn.disabled = busy;
    }
    if (submitConfirmBtn) {
      submitConfirmBtn.hidden = !confirmMode;
      if (!confirmMode) {
        submitConfirmBtn.disabled = true;
      } else {
        syncSubmitConfirm();
      }
    }
  };

  const setConfirmMode = (on) => {
    const receipt = currentDetail?.receipt || {};
    confirmMode = Boolean(on);
    const panel = modalBody?.querySelector("[data-confirm-panel]");
    const detailPanel = modalBody?.querySelector("[data-detail-panel]");
    if (panel) panel.hidden = !confirmMode;
    if (detailPanel) detailPanel.hidden = confirmMode;
    syncActionButtons();
    if (!confirmMode) {
      confirmVerified = false;
      syncSubmitConfirm();
      return;
    }
    confirmVerified = false;
    const codeInput = modalBody?.querySelector("[data-confirm-login-code]");
    if (codeInput) codeInput.value = "";
    const parts = defaultConfirmPaymentParts(receipt);
    const paymentDate = modalBody?.querySelector("[data-confirm-payment-date]");
    const paymentTime = modalBody?.querySelector("[data-confirm-payment-time]");
    if (paymentDate) paymentDate.value = parts.date;
    if (paymentTime) paymentTime.value = parts.time;
    const refInput = modalBody?.querySelector("[data-confirm-mpesa-ref]");
    if (refInput && !refInput.value) {
      refInput.value = String(receipt.mpesa_receipt_number || "").trim();
    }
    const amountInput = modalBody?.querySelector("[data-confirm-credit-amount]");
    if (amountInput) {
      const due = creditDueValue(receipt);
      amountInput.value = due > 0 ? String(due) : "";
    }
    window.setTimeout(() => {
      const focusEl =
        modalBody?.querySelector("[data-confirm-credit-amount]") ||
        modalBody?.querySelector("[data-confirm-login-code]");
      focusEl?.focus?.({ preventScroll: true });
    }, 40);
    const creditNotice = String(receipt.credit_confirm_notice || "").trim();
    setConfirmStatus(
      creditNotice ||
        (receipt.require_mpesa_reference_on_confirm
          ? "Date defaults to today. Enter time, reference number, and staff 6-digit ID."
          : "Date defaults to today. Enter time and staff 6-digit ID to confirm.")
    );
    syncSubmitConfirm();
    refreshIcons();
  };

  const verifyStaffCode = async () => {
    const codeInput = modalBody?.querySelector("[data-confirm-login-code]");
    const code = (codeInput?.value || "").trim();
    const current = ++verifySeq;
    if (code.length < 6) {
      confirmVerified = false;
      setConfirmStatus(
        code.length
          ? `Enter ${6 - code.length} more digit${6 - code.length === 1 ? "" : "s"}.`
          : "Enter an active staff 6-digit ID to authorise confirmation."
      );
      syncSubmitConfirm();
      return;
    }
    if (!verifyUrl) {
      confirmVerified = false;
      setConfirmStatus("Verification is unavailable.", { error: true });
      syncSubmitConfirm();
      return;
    }
    setConfirmStatus("Checking staff ID…");
    try {
      const body = new URLSearchParams({ login_code: code });
      const res = await fetch(verifyUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-CSRFToken": getCsrf(),
        },
        credentials: "same-origin",
        body,
      });
      const data = await res.json().catch(() => ({}));
      if (current !== verifySeq) return;
      const ok = Boolean(res.ok && data.ok);
      confirmVerified = ok;
      setConfirmStatus(
        ok
          ? `Authorised${data.name ? `: ${data.name}` : ""}.`
          : data.error || "Invalid staff ID.",
        { ok, error: !ok }
      );
      syncSubmitConfirm();
    } catch (_err) {
      if (current !== verifySeq) return;
      confirmVerified = false;
      setConfirmStatus("Could not verify staff ID.", { error: true });
      syncSubmitConfirm();
    }
  };

  const scheduleVerify = () => {
    window.clearTimeout(verifyTimer);
    verifyTimer = window.setTimeout(verifyStaffCode, 280);
  };

  const round2 = (n) => Math.round(n * 100) / 100;

  const CONFIRM_METHOD_CHANNELS = {
    cash: ["cash"],
    mpesa: ["mpesa"],
    card: ["card"],
    both: ["cash", "mpesa"],
    cash_card: ["cash", "card"],
    mpesa_card: ["mpesa", "card"],
    cash_mpesa_card: ["cash", "mpesa", "card"],
  };
  const CONFIRM_CHANNEL_LABELS = {
    cash: "Cash",
    mpesa: "M-Pesa",
    card: "Card",
  };
  const confirmMethodChannels = (method) =>
    CONFIRM_METHOD_CHANNELS[String(method || "").toLowerCase()] || [];

  const confirmReceiptTotal = () => {
    const n = Number(currentDetail?.receipt?.total || 0);
    return Number.isFinite(n) ? n : 0;
  };

  const syncConfirmPaymentSplit = () => {
    const select = modalBody?.querySelector("[data-confirm-payment-method]");
    const wrap = modalBody?.querySelector("[data-confirm-split-wrap]");
    if (!select || !wrap) return;
    const channels = confirmMethodChannels(select.value);
    wrap.hidden = channels.length < 2;
    if (channels.length < 2) return;
    const receipt = currentDetail?.receipt || {};
    for (const channel of ["cash", "mpesa", "card"]) {
      const field = wrap.querySelector(`[data-confirm-split-${channel}]`);
      const input = wrap.querySelector(`[data-confirm-${channel}-amount]`);
      const on = channels.includes(channel);
      if (field) field.hidden = !on;
      if (input && on && input.value === "") {
        input.value = String(receipt[`${channel}_amount`] ?? "");
      }
    }
  };

  const autoBalanceSplit = (edited) => {
    const select = modalBody?.querySelector("[data-confirm-payment-method]");
    const wrap = modalBody?.querySelector("[data-confirm-split-wrap]");
    if (!select || !wrap) return;
    const channels = confirmMethodChannels(select.value);
    if (channels.length < 2) return;
    const inputs = channels
      .map((channel) =>
        wrap.querySelector(`[data-confirm-${channel}-amount]`)
      )
      .filter(Boolean);
    const n = Number(edited.value);
    if (edited.value === "" || !Number.isFinite(n)) return;
    let remaining = round2(Math.max(0, confirmReceiptTotal() - n));
    const others = inputs.filter((el) => el !== edited);
    others.forEach((el, index) => {
      const value =
        index === others.length - 1
          ? Math.max(0, remaining)
          : Math.max(0, round2(remaining / (others.length - index)));
      el.value = String(value);
      remaining = round2(remaining - value);
    });
  };

  const renderDetail = (payload) => {
    const receipt = payload.receipt || {};
    currentDetail = payload;
    if (modalTitle) {
      modalTitle.textContent = receipt.receipt_number || "Receipt";
    }
    if (modalActions) modalActions.hidden = false;
    confirmMode = false;
    confirmVerified = false;
    syncActionButtons();

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
        return `<li class="shop-receipt-sheet-line">
  <div class="shop-receipt-sheet-line-item">
    <strong>${escapeHtml(line.item_name)}</strong>
    ${returnedNote}
    ${serials ? `<div class="shop-receipt-serials">${serials}</div>` : ""}
  </div>
  <span class="shop-receipt-sheet-line-qty">${line.remaining_quantity}<span class="shop-receipt-muted"> / ${line.quantity}</span></span>
  <span class="shop-receipt-sheet-line-price">${escapeHtml(money(line.unit_price))}</span>
  <span class="shop-receipt-sheet-line-total">${escapeHtml(money(line.remaining_total))}</span>
</li>`;
      })
      .join("");

    const existingRef = String(receipt.mpesa_receipt_number || "").trim();
    const requireConfirmDetails = Boolean(
      receipt.require_mpesa_reference_on_confirm
    );
    const optionalHint = requireConfirmDetails ? "required" : "optional";
    const paymentParts = defaultConfirmPaymentParts(receipt);
    const requiredAttrs = requireConfirmDetails
      ? `required aria-required="true"`
      : `aria-required="false"`;
    const isCredit = isCreditReceipt(receipt);
    const dueValue = creditDueValue(receipt);
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
    const methodLabels = {
      cash: "Cash",
      mpesa: "M-Pesa",
      card: "Card",
      both: "Cash + M-Pesa",
      cash_card: "Cash + Card",
      mpesa_card: "M-Pesa + Card",
      cash_mpesa_card: "Cash + M-Pesa + Card",
    };
    const currentMethod = String(receipt.payment_method || "")
      .trim()
      .toLowerCase();
    const saleMethod = methodLabels[currentMethod] ? currentMethod : "";
    let paidByField = "";
    let paymentSplitField = "";
    if (!isCredit && saleMethod) {
      const enabledMethods = (
        Array.isArray(receipt.payment_methods) && receipt.payment_methods.length
          ? receipt.payment_methods
          : ["cash", "mpesa", "both"]
      )
        .map((m) => String(m || "").toLowerCase())
        .filter((m) => methodLabels[m]);
      if (!enabledMethods.includes(saleMethod)) {
        enabledMethods.push(saleMethod);
      }
      const paidByOptions = enabledMethods
        .map(
          (m) =>
            `<option value="${m}"${m === saleMethod ? " selected" : ""}>${escapeHtml(
              methodLabels[m]
            )}</option>`
        )
        .join("");
      paidByField = `<div class="shop-receipt-mpesa-ref">
      <label class="shop-receipt-mpesa-ref-label" for="ax-confirm-payment-method">
        Paid by
      </label>
      <select
        id="ax-confirm-payment-method"
        class="shop-receipt-mpesa-ref-input"
        name="payment_method"
        data-confirm-payment-method
      >
        ${paidByOptions}
      </select>
    </div>`;
      const saleChannels = confirmMethodChannels(saleMethod);
      const splitFieldFor = (channel) => {
        const on = saleChannels.includes(channel);
        const stored = on ? String(receipt[`${channel}_amount`] ?? "") : "";
        const fallback =
          !on && saleChannels.length === 1 && saleChannels[0] === channel
            ? String(receipt.total ?? "")
            : stored;
        return `<div class="shop-receipt-mpesa-ref" data-confirm-split-${channel}${
          on ? "" : " hidden"
        }>
        <label class="shop-receipt-mpesa-ref-label" for="ax-confirm-${channel}-amount">
          ${CONFIRM_CHANNEL_LABELS[channel]} amount
        </label>
        <input
          id="ax-confirm-${channel}-amount"
          class="shop-receipt-mpesa-ref-input"
          type="number"
          name="${channel}_amount"
          min="0"
          step="0.01"
          value="${escapeHtml(fallback)}"
          inputmode="decimal"
          data-confirm-${channel}-amount
        >
      </div>`;
      };
      paymentSplitField = `<div class="shop-receipt-confirm-datetime" data-confirm-split-wrap${
        saleChannels.length > 1 ? "" : " hidden"
      }>
      ${splitFieldFor("cash")}
      ${splitFieldFor("mpesa")}
      ${splitFieldFor("card")}
    </div>`;
    }
    const confirmLead = isCredit
      ? "Enter the amount received. Full balance clears and confirms the credit; a smaller amount records a partial confirmation."
      : "Confirm this sale. Date defaults to today; enter the payment time, then confirm.";
    const confirmPanelHtml = `<div class="shop-receipt-return shop-receipt-confirm" data-confirm-panel hidden>
  <p class="shop-receipt-muted">
    ${confirmLead}
    ${
      requireConfirmDetails
        ? "Reference number is compulsory."
        : "Reference number is optional."
    }
  </p>
  <div class="shop-receipt-card shop-receipt-card--confirm">
    <div class="shop-receipt-confirm-fields">
      ${paidByField}
      ${paymentSplitField}
      ${creditAmountField}
      <div class="shop-receipt-confirm-datetime" data-confirm-payment-at-wrap>
        <div class="shop-receipt-mpesa-ref">
          <label class="shop-receipt-mpesa-ref-label" for="ax-confirm-payment-date">
            Payment date
            <span class="shop-receipt-muted">(${optionalHint})</span>
          </label>
          <input
            id="ax-confirm-payment-date"
            class="shop-receipt-mpesa-ref-input"
            type="date"
            name="payment_date"
            value="${escapeHtml(paymentParts.date)}"
            data-confirm-payment-date
            ${requiredAttrs}
          >
        </div>
        <div class="shop-receipt-mpesa-ref">
          <label class="shop-receipt-mpesa-ref-label" for="ax-confirm-payment-time">
            Payment time
            <span class="shop-receipt-muted">(${optionalHint})</span>
          </label>
          <input
            id="ax-confirm-payment-time"
            class="shop-receipt-mpesa-ref-input"
            type="time"
            name="payment_time"
            value="${escapeHtml(paymentParts.time)}"
            data-confirm-payment-time
            ${requiredAttrs}
          >
        </div>
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
  </div>
  <label class="shop-cart-input shop-receipt-return-code">
    <span>Staff 6-digit ID</span>
    <input
      type="password"
      inputmode="numeric"
      maxlength="6"
      autocomplete="one-time-code"
      placeholder="••••••"
      data-confirm-login-code
    >
  </label>
  <p class="shop-receipts-status" data-confirm-status>
    Enter an active staff 6-digit ID to authorise confirmation.
  </p>
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

    const clientLine = receipt.client_name
      ? `${receipt.client_name}${
          receipt.client_phone ? ` · ${receipt.client_phone}` : ""
        }`
      : receipt.client_phone || "";
    const paidAtLabel = String(
      receipt.confirmed_payment_label || ""
    ).trim();
    const paymentSplitParts = confirmMethodChannels(receipt.payment_method)
      .filter(
        (channel) =>
          confirmMethodChannels(receipt.payment_method).length > 1 &&
          receipt[`${channel}_amount`] != null &&
          receipt[`${channel}_amount`] !== ""
      )
      .map(
        (channel) =>
          `${CONFIRM_CHANNEL_LABELS[channel]} KSh ${money(
            receipt[`${channel}_amount`]
          )}`
      );
    const paymentSplitLabel = paymentSplitParts.join(" + ");
    const paymentRow =
      receipt.kind === "sale" || receipt.payment_label
        ? `<div><dt>Payment</dt><dd>${escapeHtml(
            receipt.payment_label || "—"
          )}${paymentSplitLabel ? ` · ${escapeHtml(paymentSplitLabel)}` : ""}</dd></div>`
        : "";
    const creditBalanceRow =
      isCredit && Number(receipt.credit_due) > 0
        ? `<div><dt>Balance due</dt><dd>KSh ${escapeHtml(
            money(receipt.credit_due)
          )}</dd></div>`
        : "";

    if (modalBody) {
      modalBody.innerHTML = `
<div class="shop-receipt-detail" data-detail-panel>
  <div class="shop-receipt-card shop-receipt-card--sheet">
    <div class="shop-receipt-sheet">
      <div class="shop-receipt-sheet-head">
        <p class="shop-receipt-sheet-brand">${escapeHtml(
          receipt.shop_name || "MY-SHOP"
        )} · ${escapeHtml(receipt.kind_label || "Receipt")}</p>
        <h3 class="shop-receipt-sheet-no">${escapeHtml(
          receipt.receipt_number || "—"
        )}</h3>
        <div class="shop-receipt-summary-badges">
          <span class="shop-receipt-kind shop-receipt-kind--${escapeHtml(
            (receipt.kind || "").replace(/\s+/g, "_")
          )}">${escapeHtml(receipt.kind_label || "")}</span>
          <span class="shop-receipt-status ${statusClass(
            detailStatus
          )}">${escapeHtml(receipt.status_label || "")}</span>
        </div>
      </div>
      ${creditNoticeHtml}
      <dl class="shop-receipt-sheet-rows">
        <div><dt>Printed</dt><dd>${escapeHtml(
          receipt.created_label || "—"
        )}</dd></div>
        ${
          paidAtLabel
            ? `<div><dt>Paid</dt><dd>${escapeHtml(paidAtLabel)}</dd></div>`
            : ""
        }
        <div><dt>Served by</dt><dd>${escapeHtml(receipt.cashier || "—")}</dd></div>
        <div><dt>Client</dt><dd>${escapeHtml(clientLine || "Walk-in")}</dd></div>
        ${
          existingRef
            ? `<div><dt>Reference</dt><dd>${escapeHtml(existingRef)}</dd></div>`
            : ""
        }
      </dl>
      <div class="shop-receipt-sheet-sep" aria-hidden="true"></div>
      <div class="shop-receipt-sheet-lines-head" aria-hidden="true">
        <span>Item</span><span class="is-num">Qty</span><span class="is-num">Price</span><span class="is-num">Total</span>
      </div>
      ${
        lines.length
          ? `<ul class="shop-receipt-sheet-lines">${itemsHtml}</ul>`
          : `<p class="shop-receipt-muted">No line items on this receipt.</p>`
      }
      <dl class="shop-receipt-sheet-rows">
        <div><dt>Subtotal</dt><dd>KSh ${escapeHtml(money(receipt.subtotal))}</dd></div>
        ${
          Number(receipt.tax_amount) > 0
            ? `<div><dt>Tax (${escapeHtml(
                receipt.tax_percent
              )}%)</dt><dd>KSh ${escapeHtml(money(receipt.tax_amount))}</dd></div>`
            : ""
        }
        <div class="is-total"><dt>Total</dt><dd>KSh ${escapeHtml(
          money(receipt.total)
        )}</dd></div>
      </dl>
      ${
        paymentRow || creditBalanceRow || isCredit
          ? `<div class="shop-receipt-sheet-sep" aria-hidden="true"></div>
      <dl class="shop-receipt-sheet-rows">
        ${paymentRow}
        ${
          isCredit
            ? `<div><dt>Paid</dt><dd>KSh ${escapeHtml(
                money(receipt.amount_paid)
              )}</dd></div>`
            : ""
        }
        ${creditBalanceRow}
      </dl>`
          : ""
      }
    </div>
  </div>
</div>
${confirmPanelHtml}`;
    }
    refreshIcons();
    if (autoConfirmOnLoad) {
      autoConfirmOnLoad = false;
      if (receipt.can_confirm) setConfirmMode(true);
    }
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
    autoConfirmOnLoad = true;
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
          "Credits need a confirm amount. Open each credit, enter the amount (full balance clears; less is a partial confirmation), then confirm it."
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
          "Compulsory reference is enabled. Open each receipt, enter the reference number and payment date & time, then confirm it."
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

  modalBody?.addEventListener("change", (event) => {
    const target = event.target;
    if (target?.matches?.("[data-confirm-payment-method]")) {
      syncConfirmPaymentSplit();
      syncSubmitConfirm();
      return;
    }
    if (
      target?.matches?.(
        "[data-confirm-payment-date], [data-confirm-payment-time], [data-confirm-mpesa-ref], [data-confirm-credit-amount]"
      )
    ) {
      syncSubmitConfirm();
    }
  });

  modalBody?.addEventListener("input", (event) => {
    const target = event.target;
    if (
      target?.matches?.(
        "[data-confirm-cash-amount], [data-confirm-mpesa-amount], [data-confirm-card-amount]"
      )
    ) {
      autoBalanceSplit(target);
      syncSubmitConfirm();
      return;
    }
    if (target?.matches?.("[data-confirm-login-code]")) {
      confirmVerified = false;
      scheduleVerify();
      syncSubmitConfirm();
      return;
    }
    if (
      target?.matches?.(
        "[data-confirm-payment-date], [data-confirm-payment-time], [data-confirm-mpesa-ref], [data-confirm-credit-amount]"
      )
    ) {
      syncSubmitConfirm();
    }
  });

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

  const submitConfirm = async () => {
    if (!currentDetail?.receipt?.id || !confirmUrlTemplate || !currentShopId) {
      return;
    }
    const code = (
      modalBody?.querySelector("[data-confirm-login-code]")?.value || ""
    ).trim();
    const {
      paymentAt,
      ref,
      amount,
      existingRef,
      requireDetails,
      credit,
      due,
      method,
      cashAmount,
      mpesaAmount,
      cardAmount,
    } = confirmFieldValues();
    if (requireDetails) {
      if (!ref && !existingRef) {
        setConfirmStatus(
          "Enter the reference number before confirming this receipt.",
          { error: true }
        );
        modalBody?.querySelector("[data-confirm-mpesa-ref]")?.focus();
        syncSubmitConfirm();
        return;
      }
      if (!paymentAt) {
        const { paymentDate } = confirmFieldValues();
        setConfirmStatus(
          "Enter the payment date and time before confirming.",
          { error: true }
        );
        modalBody
          ?.querySelector(
            !paymentDate
              ? "[data-confirm-payment-date]"
              : "[data-confirm-payment-time]"
          )
          ?.focus();
        syncSubmitConfirm();
        return;
      }
    }
    if (credit && due > 0) {
      const n = Number(amount);
      if (!amount || !Number.isFinite(n) || n <= 0) {
        setConfirmStatus("Enter the amount to confirm for this credit.", {
          error: true,
        });
        modalBody?.querySelector("[data-confirm-credit-amount]")?.focus();
        syncSubmitConfirm();
        return;
      }
      if (n > due + 0.0001) {
        setConfirmStatus(
          `Confirm amount cannot exceed the balance due (KSh ${money(due)}).`,
          { error: true }
        );
        modalBody?.querySelector("[data-confirm-credit-amount]")?.focus();
        syncSubmitConfirm();
        return;
      }
    }
    const methodChannels = confirmMethodChannels(method);
    if (!credit && methodChannels.length > 1) {
      const parts = {
        cash: Number(cashAmount),
        mpesa: Number(mpesaAmount),
        card: Number(cardAmount),
      };
      const total = confirmReceiptTotal();
      const labels = methodChannels
        .map((channel) => CONFIRM_CHANNEL_LABELS[channel])
        .join(" + ");
      const invalid = methodChannels.some(
        (channel) => !Number.isFinite(parts[channel]) || parts[channel] < 0
      );
      if (invalid) {
        setConfirmStatus(`Enter how much was paid by ${labels}.`, {
          error: true,
        });
        modalBody
          ?.querySelector(
            `[data-confirm-${methodChannels[0]}-amount]`
          )
          ?.focus();
        syncSubmitConfirm();
        return;
      }
      const paidSum = methodChannels.reduce(
        (sum, channel) => sum + parts[channel],
        0
      );
      if (Math.abs(round2(paidSum) - round2(total)) > 0.009) {
        setConfirmStatus(
          `${labels} must add up to the receipt total (KSh ${total.toFixed(2)}).`,
          { error: true }
        );
        modalBody
          ?.querySelector(
            `[data-confirm-${methodChannels[0]}-amount]`
          )
          ?.focus();
        syncSubmitConfirm();
        return;
      }
    }
    if (!confirmVerified || code.length !== 6) {
      syncSubmitConfirm();
      return;
    }
    if (credit) {
      const n = Number(amount);
      const full = !(due > 0) || (Number.isFinite(n) && n >= due - 0.0001);
      const notice = full
        ? "This will clear the credit fully and convert it to a confirmed sale."
        : "This will record a partial confirmation for the amount entered.";
      if (!window.confirm(`${notice}\n\nContinue?`)) {
        syncSubmitConfirm();
        return;
      }
    }
    if (submitConfirmBtn) submitConfirmBtn.disabled = true;
    setConfirmStatus("Confirming receipt…");
    try {
      const body = {
        login_code: code,
        mpesa_receipt_number: ref,
        payment_at: paymentAt,
      };
      if (credit) {
        body.amount = amount;
        body.payment_method = ref || existingRef ? "mpesa" : "cash";
      } else if (method) {
        body.payment_method = method;
        if (methodChannels.includes("cash")) body.cash_amount = cashAmount;
        if (methodChannels.includes("mpesa")) body.mpesa_amount = mpesaAmount;
        if (methodChannels.includes("card")) body.card_amount = cardAmount;
      }
      const res = await fetch(
        urlFor(confirmUrlTemplate, currentShopId, currentDetail.receipt.id),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
            "X-CSRFToken": getCsrf(),
          },
          credentials: "same-origin",
          body: JSON.stringify(body),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        setConfirmStatus(data.error || "Confirm failed.", { error: true });
        syncSubmitConfirm();
        return;
      }
      closeModal();
      loadRows();
    } catch (_err) {
      setConfirmStatus("Confirm failed.", { error: true });
      syncSubmitConfirm();
    }
  };

  const submitModalStatus = async (status) => {
    if (busy || !currentDetail?.receipt?.id || !currentShopId) return;
    if (!setStatusUrlTemplate) return;
    if ((currentDetail?.receipt?.source || "pos") !== "pos") {
      window.alert("Only sales receipts can change status here.");
      return;
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
      const body = new URLSearchParams();
      body.set("status", status);
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
      renderDetail(data);
      loadRows();
    } catch (_err) {
      window.alert("Could not update receipt status.");
    }
    setActionBusy(false);
  };

  startConfirmBtn?.addEventListener("click", () => setConfirmMode(true));
  cancelConfirmBtn?.addEventListener("click", () => setConfirmMode(false));
  submitConfirmBtn?.addEventListener("click", submitConfirm);
  setPendingBtn?.addEventListener("click", () => submitModalStatus("pending"));
  setCancelledBtn?.addEventListener("click", () =>
    submitModalStatus("cancelled")
  );

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
