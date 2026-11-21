(() => {
  const root = document.querySelector("[data-shop-receipts]");
  if (!root) return;

  const listUrl = root.getAttribute("data-receipts-list-url") || "";
  const detailUrlTemplate =
    root.getAttribute("data-receipt-detail-url") || "";
  const confirmUrlTemplate =
    root.getAttribute("data-receipt-confirm-url") || "";
  const returnUrlTemplate =
    root.getAttribute("data-receipt-return-url") || "";
  const verifyUrl = root.getAttribute("data-verify-login-url") || "";
  const printChannels = (root.getAttribute("data-print-channels") || "")
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);

  const searchInput = root.querySelector("[data-receipts-search]");
  const kindFilter = root.querySelector("[data-receipts-kind]");
  const filterMode = root.querySelector("[data-receipts-filter-mode]");
  const dayInput = root.querySelector("[data-receipts-day]");
  const fromInput = root.querySelector("[data-receipts-from]");
  const toInput = root.querySelector("[data-receipts-to]");
  const monthInput = root.querySelector("[data-receipts-month]");
  const yearInput = root.querySelector("[data-receipts-year]");
  const listEl = root.querySelector("[data-receipts-list]");
  const countEl = root.querySelector("[data-receipts-count]");
  const statusEl = root.querySelector("[data-receipts-status]");
  const emptyEl = root.querySelector("[data-receipts-empty]");
  const countStatEl = root.querySelector("[data-receipts-count-stat]");
  const filterPanels = root.querySelectorAll("[data-filter-panel]");

  const modal = document.querySelector("[data-receipt-modal]");
  const modalBody = modal?.querySelector("[data-receipt-modal-body]");
  const modalTitle = modal?.querySelector("[data-receipt-modal-title]");
  const modalActions = modal?.querySelector("[data-receipt-modal-actions]");
  const reprintBtn = modal?.querySelector("[data-receipt-reprint]");
  const startConfirmBtn = modal?.querySelector("[data-receipt-start-confirm]");
  const cancelConfirmBtn = modal?.querySelector("[data-receipt-cancel-confirm]");
  const submitConfirmBtn = modal?.querySelector("[data-receipt-submit-confirm]");
  const startReturnBtn = modal?.querySelector("[data-receipt-start-return]");
  const cancelReturnBtn = modal?.querySelector("[data-receipt-cancel-return]");
  const confirmReturnBtn = modal?.querySelector("[data-receipt-confirm-return]");

  let searchTimer = null;
  let loadSeq = 0;
  let detailSeq = 0;
  let verifyTimer = null;
  let verifySeq = 0;
  let verified = false;
  let confirmVerified = false;
  let currentDetail = null;
  let returnMode = false;
  let confirmMode = false;
  let printPayload = null;

  const todayIso = () => {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  };

  // Default confirm date to today; leave time empty for easy entry.
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

  const defaultConfirmPaymentParts = (receipt = {}) => {
    const saved = splitLocalDateTime(receipt.confirmed_payment_at_local);
    if (saved.date && saved.time) return saved;
    return {
      date: saved.date || todayIso(),
      time: saved.time || "",
    };
  };

  const monthIso = () => todayIso().slice(0, 7);

  if (dayInput && !dayInput.value) dayInput.value = todayIso();
  if (fromInput && !fromInput.value) fromInput.value = todayIso();
  if (toInput && !toInput.value) toInput.value = todayIso();
  if (monthInput && !monthInput.value) monthInput.value = monthIso();
  if (yearInput && !yearInput.value) yearInput.value = String(new Date().getFullYear());

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

  const setPageStatus = (message, { error = false } = {}) => {
    if (!statusEl) return;
    if (!message) {
      statusEl.hidden = true;
      statusEl.textContent = "";
      statusEl.classList.remove("is-error", "is-ok");
      return;
    }
    statusEl.hidden = false;
    statusEl.textContent = message;
    statusEl.classList.toggle("is-error", error);
    statusEl.classList.toggle("is-ok", !error);
  };

  const refreshIcons = () => {
    if (typeof window.lucide?.createIcons === "function") {
      window.lucide.createIcons();
    }
  };

  const urlFor = (template, receiptId) =>
    String(template || "").replace(/\/0(\/|$)/, `/${receiptId}$1`);

  const syncFilterPanels = () => {
    const mode = (filterMode?.value || "day").trim();
    filterPanels.forEach((panel) => {
      const match = panel.getAttribute("data-filter-panel") === mode;
      panel.hidden = !match;
      panel.classList.toggle("is-hidden", !match);
    });
  };

  const buildListParams = () => {
    const params = new URLSearchParams();
    const q = (searchInput?.value || "").trim();
    const mode = (filterMode?.value || "day").trim();
    const kind = (kindFilter?.value || "sales_credits").trim();
    params.set("filter", mode);
    if (kind && kind !== "all") params.set("kind", kind);
    if (q) params.set("q", q);
    if (mode === "day") params.set("day", dayInput?.value || todayIso());
    if (mode === "period") {
      params.set("from", fromInput?.value || "");
      params.set("to", toInput?.value || "");
    }
    if (mode === "month") params.set("month", monthInput?.value || monthIso());
    if (mode === "year") params.set("year", yearInput?.value || "");
    return params;
  };

  const statusClass = (status) => {
    if (status === "cancelled") return "is-cancelled";
    if (
      status === "partial_return" ||
      status === "partial_confirmation" ||
      status === "partial" ||
      status === "unpaid"
    ) {
      return "is-partial";
    }
    if (status === "confirmed") return "is-confirmed";
    return "is-pending";
  };

  const paymentTimeLabel = (receipt = {}) =>
    receipt.status === "confirmed" ||
    receipt.status === "partial_return" ||
    receipt.is_partial_confirmation
      ? receipt.confirmed_payment_label || ""
      : "";

  const whenLabel = (receipt = {}) =>
    (receipt.source || "pos") === "pos" ? "Sold" : "Recorded";

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

  const renderList = (receipts, count) => {
    if (!listEl) return;
    if (countStatEl) {
      countStatEl.textContent = String(count || receipts.length || 0);
    }
    if (!receipts.length) {
      listEl.innerHTML = "";
      if (emptyEl) emptyEl.hidden = false;
      if (countEl) countEl.textContent = "No receipts found.";
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    if (countEl) {
      countEl.textContent =
        count === receipts.length
          ? `${count} receipt${count === 1 ? "" : "s"}`
          : `Showing ${receipts.length} of ${count} receipts`;
    }
    listEl.innerHTML = receipts
      .map((row) => {
        const client =
          row.client_name || row.client_phone
            ? `${escapeHtml(row.client_name || "—")}${
                row.client_phone
                  ? `<span>${escapeHtml(row.client_phone)}</span>`
                  : ""
              }`
            : "—";
        const source = escapeHtml(row.source || "pos");
        const soldAs = escapeHtml(row.sold_as_label || "—");
        const paidAt = paymentTimeLabel(row);
        return `<tr class="shop-receipts-row" data-receipt-id="${row.id}" data-receipt-source="${source}" tabindex="0">
  <td data-label="Receipt">
    <strong>${escapeHtml(row.tally_code || row.receipt_number)}</strong>
  </td>
  <td data-label="Type"><span class="shop-receipt-kind shop-receipt-kind--${escapeHtml(
    row.kind
  )}">${escapeHtml(row.kind_label)}</span></td>
  <td data-label="Sold as">${soldAs}</td>
  <td class="shop-receipts-client" data-label="Client">${client}</td>
  <td class="shop-receipts-total" data-label="Total">KSh ${escapeHtml(money(row.total))}</td>
  <td class="shop-receipts-when" data-label="${whenLabel(row)} at">${escapeHtml(row.created_label)}</td>
  <td class="shop-receipts-status-cell" data-label="Status"><span class="shop-receipt-status ${statusClass(
    row.status
  )}">${escapeHtml(row.status_label)}</span>${
          paidAt
            ? `<span class="shop-receipts-paid-at">Paid ${escapeHtml(paidAt)}</span>`
            : ""
        }</td>
</tr>`;
      })
      .join("");
  };

  const loadReceipts = async () => {
    if (!listUrl) return;
    const seq = ++loadSeq;
    setPageStatus("");
    if (countEl) countEl.textContent = "Loading receipts…";
    try {
      const res = await fetch(`${listUrl}?${buildListParams().toString()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await res.json().catch(() => ({}));
      if (seq !== loadSeq) return;
      if (!res.ok || !data.ok) {
        renderList([], 0);
        setPageStatus(data.error || "Could not load receipts.", { error: true });
        return;
      }
      renderList(data.receipts || [], data.count || 0);
    } catch (_err) {
      if (seq !== loadSeq) return;
      renderList([], 0);
      setPageStatus("Could not load receipts.", { error: true });
    }
  };

  const scheduleLoad = (delay = 220) => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(loadReceipts, delay);
  };

  const closeModal = () => {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("workspace-modal-open");
    currentDetail = null;
    printPayload = null;
    returnMode = false;
    confirmMode = false;
    verified = false;
    confirmVerified = false;
  };

  const openModal = () => {
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("workspace-modal-open");
    refreshIcons();
  };

  const syncActionButtons = () => {
    const receipt = currentDetail?.receipt || {};
    const busyMode = returnMode || confirmMode;
    if (reprintBtn) reprintBtn.hidden = busyMode;
    if (startConfirmBtn) {
      startConfirmBtn.hidden = busyMode || !receipt.can_confirm;
    }
    if (cancelConfirmBtn) cancelConfirmBtn.hidden = !confirmMode;
    if (submitConfirmBtn) {
      submitConfirmBtn.hidden = !confirmMode;
      if (!confirmMode) submitConfirmBtn.disabled = true;
    }
    if (startReturnBtn) {
      startReturnBtn.hidden = busyMode || !receipt.can_return;
    }
    if (cancelReturnBtn) cancelReturnBtn.hidden = !returnMode;
    if (confirmReturnBtn) {
      confirmReturnBtn.hidden = !returnMode;
      if (!returnMode) confirmReturnBtn.disabled = true;
    }
  };

  const setConfirmMode = (on) => {
    const receipt = currentDetail?.receipt || {};
    if (on && returnMode) setReturnMode(false);
    confirmMode = Boolean(on);
    const panel = modalBody?.querySelector("[data-confirm-panel]");
    const detailPanel = modalBody?.querySelector("[data-detail-panel]");
    const returnPanel = modalBody?.querySelector("[data-return-panel]");
    if (panel) panel.hidden = !confirmMode;
    if (detailPanel) detailPanel.hidden = confirmMode || returnMode;
    if (returnPanel && confirmMode) returnPanel.hidden = true;
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
    syncConfirmRefField();
    const amountInput = modalBody?.querySelector("[data-confirm-credit-amount]");
    if (amountInput) {
      const due = creditDueValue(receipt);
      amountInput.value = due > 0 ? String(due) : "";
      amountInput.focus?.({ preventScroll: true });
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
        (receipt.require_mpesa_reference_on_confirm && confirmRefApplies()
          ? "Date defaults to today. Enter time, reference number, and staff 6-digit ID."
          : "Date defaults to today. Enter time and staff 6-digit ID to confirm.")
    );
    syncSubmitConfirm();
    refreshIcons();
  };

  const setReturnMode = (on) => {
    if (on && confirmMode) setConfirmMode(false);
    returnMode = Boolean(on);
    const panel = modalBody?.querySelector("[data-return-panel]");
    const detailPanel = modalBody?.querySelector("[data-detail-panel]");
    const confirmPanel = modalBody?.querySelector("[data-confirm-panel]");
    if (panel) panel.hidden = !returnMode;
    if (detailPanel) detailPanel.hidden = returnMode || confirmMode;
    if (confirmPanel && returnMode) confirmPanel.hidden = true;
    syncActionButtons();
    if (!returnMode) {
      verified = false;
      syncConfirmReturn();
      return;
    }
    verified = false;
    const codeInput = modalBody?.querySelector("[data-return-login-code]");
    if (codeInput) codeInput.value = "";
    const reasonInput = modalBody?.querySelector("[data-return-reason]");
    if (reasonInput) {
      reasonInput.value = "";
      window.setTimeout(() => reasonInput.focus(), 40);
    }
    setReturnStatus("Enter a return reason and an active staff 6-digit ID.");
    syncConfirmReturn();
    refreshIcons();
  };

  const setConfirmStatus = (message, { ok = false, error = false } = {}) => {
    const el = modalBody?.querySelector("[data-confirm-status]");
    if (!el) return;
    el.textContent = message || "";
    el.classList.toggle("is-ok", ok);
    el.classList.toggle("is-error", error);
  };

  const setReturnStatus = (message, { ok = false, error = false } = {}) => {
    const el = modalBody?.querySelector("[data-return-status]");
    if (!el) return;
    el.textContent = message || "";
    el.classList.toggle("is-ok", ok);
    el.classList.toggle("is-error", error);
  };

  const selectedReturnLines = () => {
    const rows = modalBody?.querySelectorAll("[data-return-line]") || [];
    const lines = [];
    rows.forEach((row) => {
      const checked = row.querySelector("[data-return-check]")?.checked;
      if (!checked) return;
      const lineId = Number(row.getAttribute("data-line-id") || 0);
      const qtyInput = row.querySelector("[data-return-qty]");
      let qty = Number(qtyInput?.value || 0);
      const serialBoxes = [
        ...row.querySelectorAll("[data-return-serial]:checked"),
      ].map((el) => el.value);
      if (serialBoxes.length) qty = serialBoxes.length;
      if (!lineId || !qty) return;
      lines.push({
        line_id: lineId,
        quantity: qty,
        serial_numbers: serialBoxes,
      });
    });
    return lines;
  };

  const returnReason = () =>
    (modalBody?.querySelector("[data-return-reason]")?.value || "").trim();

  const syncConfirmReturn = () => {
    if (!confirmReturnBtn) return;
    const lines = selectedReturnLines();
    confirmReturnBtn.disabled = !(
      returnMode &&
      verified &&
      lines.length &&
      returnReason()
    );
  };

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
  const confirmMethodNeedsRef = (method) =>
    confirmMethodChannels(method).some((channel) => channel !== "cash");
  const confirmRefApplies = () => {
    const receipt = currentDetail?.receipt || {};
    if (isCreditReceipt(receipt)) return true;
    const select = modalBody?.querySelector("[data-confirm-payment-method]");
    if (!select) return true;
    return confirmMethodNeedsRef(select.value);
  };
  const syncConfirmRefField = () => {
    const wrap = modalBody?.querySelector("[data-confirm-mpesa-ref-wrap]");
    if (!wrap) return;
    const input = wrap.querySelector("[data-confirm-mpesa-ref]");
    const visible = confirmRefApplies();
    wrap.hidden = !visible;
    if (input) {
      const required =
        visible &&
        Boolean(currentDetail?.receipt?.require_mpesa_reference_on_confirm);
      input.required = required;
      input.setAttribute("aria-required", required ? "true" : "false");
    }
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
      refApplies: confirmRefApplies(),
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
      refApplies,
    } = confirmFieldValues();
    const detailsOk =
      !requireDetails ||
      (Boolean(paymentAt) &&
        (!refApplies || Boolean(ref) || Boolean(existingRef)));
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
      confirmVerified &&
      detailsOk &&
      amountOk &&
      splitOk
    );
  };

  const verifyStaffCode = async ({
    codeSelector,
    setStatus,
    setVerified,
    afterVerify,
    emptyMessage,
  }) => {
    const codeInput = modalBody?.querySelector(codeSelector);
    const code = (codeInput?.value || "").trim();
    const current = ++verifySeq;
    if (code.length < 6) {
      setVerified(false);
      setStatus(
        code.length
          ? `Enter ${6 - code.length} more digit${6 - code.length === 1 ? "" : "s"}.`
          : emptyMessage
      );
      afterVerify();
      return;
    }
    if (!verifyUrl) {
      setVerified(false);
      setStatus("Verification is unavailable.", { error: true });
      afterVerify();
      return;
    }
    setStatus("Checking staff ID…");
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
      setVerified(ok);
      setStatus(
        ok
          ? `Authorised${data.name ? `: ${data.name}` : ""}.`
          : data.error || "Invalid staff ID.",
        { ok, error: !ok }
      );
      afterVerify();
    } catch (_err) {
      if (current !== verifySeq) return;
      setVerified(false);
      setStatus("Could not verify staff ID.", { error: true });
      afterVerify();
    }
  };

  const verifyReturnCode = () =>
    verifyStaffCode({
      codeSelector: "[data-return-login-code]",
      setStatus: setReturnStatus,
      setVerified: (value) => {
        verified = value;
      },
      afterVerify: syncConfirmReturn,
      emptyMessage:
        "Enter an active staff 6-digit ID to authorise the return.",
    });

  const verifyConfirmCode = () =>
    verifyStaffCode({
      codeSelector: "[data-confirm-login-code]",
      setStatus: setConfirmStatus,
      setVerified: (value) => {
        confirmVerified = value;
      },
      afterVerify: syncSubmitConfirm,
      emptyMessage:
        "Enter an active staff 6-digit ID to authorise confirmation.",
    });

  const scheduleVerify = (kind = "return") => {
    window.clearTimeout(verifyTimer);
    verifyTimer = window.setTimeout(
      kind === "confirm" ? verifyConfirmCode : verifyReturnCode,
      280
    );
  };

  const renderDetail = (payload) => {
    const receipt = payload.receipt || {};
    currentDetail = payload;
    printPayload = {
      text: payload.receipt_text || "",
      ticket: payload.receipt_ticket || null,
      qr: payload.receipt_qr || null,
      font: payload.receipt_font || null,
      paper: payload.receipt_paper_width || "80",
    };
    if (modalTitle) {
      modalTitle.textContent = receipt.tally_code || receipt.receipt_number || "Receipt";
    }
    if (modalActions) modalActions.hidden = false;
    confirmMode = false;
    returnMode = false;
    confirmVerified = false;
    verified = false;
    syncActionButtons();

    const lines = receipt.lines || [];
    const returnable = receipt.returnable_lines || [];
    const partyTitle =
      receipt.source === "stock" || receipt.source === "expense" || receipt.kind === "stock" || receipt.kind === "expense"
        ? "Supplier"
        : "Customer";
    const existingRef = String(receipt.mpesa_receipt_number || "").trim();
    const paymentParts = defaultConfirmPaymentParts(receipt);
    const requireConfirmDetails = Boolean(
      receipt.require_mpesa_reference_on_confirm
    );
    const optionalHint = requireConfirmDetails ? "required" : "optional";
    const requiredAttrs = requireConfirmDetails
      ? `required aria-required="true"`
      : `aria-required="false"`;
    const clientLine = receipt.client_name
      ? `${receipt.client_name}${
          receipt.client_phone ? ` · ${receipt.client_phone}` : ""
        }`
      : receipt.client_phone || "";

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

    const returnRows = returnable
      .map((line) => {
        const maxQty = Number(line.remaining_quantity || 0);
        const serialOptions = (line.remaining_serial_numbers || [])
          .map(
            (serial) =>
              `<label class="shop-receipt-serial-option">
  <input type="checkbox" value="${escapeHtml(serial)}" data-return-serial>
  <span>${escapeHtml(serial)}</span>
</label>`
          )
          .join("");
        return `<div class="shop-receipt-return-line" data-return-line data-line-id="${line.id}">
  <label class="shop-receipt-return-check">
    <input type="checkbox" data-return-check>
    <span>
      <strong>${escapeHtml(line.item_name)}</strong>
      <em>Up to ${maxQty} · KSh ${escapeHtml(money(line.unit_price))}</em>
    </span>
  </label>
  ${
    serialOptions
      ? `<div class="shop-receipt-return-serials">${serialOptions}</div>`
      : `<label class="shop-receipt-return-qty">
  <span>Qty</span>
  <input type="text" min="0.001" max="${maxQty}" step="0.001" value="${maxQty}" inputmode="decimal" data-return-qty>
</label>`
  }
</div>`;
      })
      .join("");

    const detailStatus = receipt.is_partial_confirmation
      ? "partial_confirmation"
      : receipt.status;
    const creditNotice = String(receipt.credit_confirm_notice || "").trim();
    const creditNoticeHtml = creditNotice
      ? `<p class="shop-receipts-status">${escapeHtml(creditNotice)}</p>`
      : "";
    const isCredit = isCreditReceipt(receipt);
    const creditDue = creditDueValue(receipt);
    const confirmLead = isCredit
      ? "Enter the amount received. Full balance clears and confirms the credit; a smaller amount records a partial confirmation."
      : "Confirm this sale. Date defaults to today; enter the payment time, then confirm.";
    const creditAmountField = isCredit
      ? `<div class="shop-receipt-mpesa-ref" data-confirm-credit-amount-wrap>
        <label class="shop-receipt-mpesa-ref-label" for="shop-confirm-credit-amount">
          Confirm amount
          <span class="shop-receipt-muted">(due KSh ${escapeHtml(
            money(creditDue)
          )})</span>
        </label>
        <input
          id="shop-confirm-credit-amount"
          class="shop-receipt-mpesa-ref-input"
          type="number"
          name="amount"
          min="0.01"
          step="0.01"
          max="${escapeHtml(String(creditDue || ""))}"
          value="${escapeHtml(creditDue > 0 ? String(creditDue) : "")}"
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
        <label class="shop-receipt-mpesa-ref-label" for="shop-confirm-payment-method">
          Paid by
        </label>
        <select
          id="shop-confirm-payment-method"
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
        const value = on ? String(receipt[`${channel}_amount`] ?? "") : "";
        return `<div class="shop-receipt-mpesa-ref" data-confirm-split-${channel}${
          on ? "" : " hidden"
        }>
          <label class="shop-receipt-mpesa-ref-label" for="shop-confirm-${channel}-amount">
            ${CONFIRM_CHANNEL_LABELS[channel]} amount
          </label>
          <input
            id="shop-confirm-${channel}-amount"
            class="shop-receipt-mpesa-ref-input"
            type="number"
            name="${channel}_amount"
            min="0"
            step="0.01"
            value="${escapeHtml(value)}"
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

    const refAppliesInitial =
      isCredit || !saleMethod || confirmMethodNeedsRef(saleMethod);
    const refAttrs =
      requireConfirmDetails && refAppliesInitial
        ? requiredAttrs
        : `aria-required="false"`;
    const paidAtLabel = paymentTimeLabel(receipt);
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
    const creditBalanceRow =
      isCredit && Number(receipt.credit_due) > 0
        ? `<div><dt>Balance due</dt><dd>KSh ${escapeHtml(
            money(receipt.credit_due)
          )}</dd></div>`
        : "";
    const paymentRow =
      receipt.kind === "sale" || receipt.payment_label
        ? `<div><dt>Payment</dt><dd>${escapeHtml(
            receipt.payment_label || "—"
          )}${paymentSplitLabel ? ` · ${escapeHtml(paymentSplitLabel)}` : ""}</dd></div>`
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
          receipt.tally_code || receipt.receipt_number || "—"
        )}</h3>
        <div class="shop-receipt-summary-badges">
          <span class="shop-receipt-kind shop-receipt-kind--${escapeHtml(
            receipt.kind
          )}">${escapeHtml(receipt.kind_label)}</span>
          <span class="shop-receipt-status ${statusClass(
            detailStatus
          )}">${escapeHtml(receipt.status_label)}</span>
        </div>
      </div>
      ${creditNoticeHtml}
      <dl class="shop-receipt-sheet-rows">
        <div><dt>${escapeHtml(whenLabel(receipt))}</dt><dd>${escapeHtml(
          receipt.created_label || "—"
        )}</dd></div>
        ${
          paidAtLabel
            ? `<div><dt>Paid</dt><dd>${escapeHtml(paidAtLabel)}</dd></div>`
            : ""
        }
        <div><dt>Served by</dt><dd>${escapeHtml(receipt.cashier || "—")}</dd></div>
        <div><dt>${escapeHtml(partyTitle)}</dt><dd>${escapeHtml(
          clientLine || "Walk-in"
        )}</dd></div>
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
      <ul class="shop-receipt-sheet-lines">${
        itemsHtml || `<li class="shop-receipt-sheet-empty">No items</li>`
      }</ul>
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
<div class="shop-receipt-return shop-receipt-confirm" data-confirm-panel hidden>
  <p class="shop-receipt-muted">
    ${confirmLead}
    ${
      requireConfirmDetails
        ? "Reference number is compulsory for M-Pesa and Card payments."
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
          <label class="shop-receipt-mpesa-ref-label" for="shop-confirm-payment-date">
            Payment date
            <span class="shop-receipt-muted">(${optionalHint})</span>
          </label>
          <input
            id="shop-confirm-payment-date"
            class="shop-receipt-mpesa-ref-input"
            type="date"
            name="payment_date"
            value="${escapeHtml(paymentParts.date)}"
            data-confirm-payment-date
            ${requiredAttrs}
          >
        </div>
        <div class="shop-receipt-mpesa-ref">
          <label class="shop-receipt-mpesa-ref-label" for="shop-confirm-payment-time">
            Payment time
            <span class="shop-receipt-muted">(${optionalHint})</span>
          </label>
          <input
            id="shop-confirm-payment-time"
            class="shop-receipt-mpesa-ref-input"
            type="time"
            name="payment_time"
            value="${escapeHtml(paymentParts.time)}"
            data-confirm-payment-time
            ${requiredAttrs}
          >
        </div>
      </div>
      <div class="shop-receipt-mpesa-ref" data-confirm-mpesa-ref-wrap${
        refAppliesInitial ? "" : " hidden"
      }>
        <label class="shop-receipt-mpesa-ref-label" for="shop-confirm-mpesa-ref">
          Reference number
          <span class="shop-receipt-muted">(${optionalHint})</span>
        </label>
        <input
          id="shop-confirm-mpesa-ref"
          class="shop-receipt-mpesa-ref-input"
          type="text"
          name="mpesa_receipt_number"
          value="${escapeHtml(existingRef)}"
          placeholder="e.g. TKL4X2ABCD"
          maxlength="40"
          autocomplete="off"
          spellcheck="false"
          data-confirm-mpesa-ref
          ${refAttrs}
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
</div>
<div class="shop-receipt-return" data-return-panel hidden>
  <style>
    .shop-receipt-return-reason{display:grid!important;gap:.35rem;margin:0;min-width:0}
    .shop-receipt-return-reason>span{display:block;font-size:.72rem;font-weight:700;opacity:.85}
    .shop-receipt-return-reason textarea{
      display:block;box-sizing:border-box;width:100%;min-height:4.5rem;resize:vertical;
      padding:.55rem .7rem;border:1px solid var(--line,#334155);border-radius:10px;
      background:var(--input-bg,var(--surface,#0f172a));color:var(--text-strong,inherit);
      font:inherit;font-size:.9rem;line-height:1.35
    }
  </style>
  <p class="shop-receipt-muted">Select items to return. Stock is restored and the sale/credit updates automatically.</p>
  <label class="shop-cart-input shop-receipt-return-reason">
    <span>Reason for return</span>
    <textarea
      rows="3"
      maxlength="500"
      placeholder="Why is this being returned?"
      data-return-reason
      required
      aria-required="true"
    ></textarea>
  </label>
  <div class="shop-receipt-return-list">
    ${
      returnRows ||
      `<p class="shop-receipt-muted">Nothing left to return on this receipt.</p>`
    }
  </div>
  <label class="shop-cart-input shop-receipt-return-code">
    <span>Staff 6-digit ID</span>
    <input
      type="password"
      inputmode="numeric"
      maxlength="6"
      autocomplete="one-time-code"
      placeholder="••••••"
      data-return-login-code
    >
  </label>
  <p class="shop-receipts-status" data-return-status>Enter a return reason and an active staff 6-digit ID.</p>
</div>`;
    }

    refreshIcons();
  };

  const openReceipt = async (receiptId, source = "pos") => {
    if (!detailUrlTemplate || !modalBody) return;
    const seq = ++detailSeq;
    openModal();
    if (modalTitle) modalTitle.textContent = "Receipt";
    if (modalActions) modalActions.hidden = true;
    modalBody.innerHTML = `<p class="shop-receipts-status">Loading receipt…</p>`;
    try {
      const detailUrl = new URL(
        urlFor(detailUrlTemplate, receiptId),
        window.location.origin
      );
      if (source && source !== "pos") {
        detailUrl.searchParams.set("source", source);
      }
      const res = await fetch(detailUrl.toString(), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await res.json().catch(() => ({}));
      if (seq !== detailSeq) return;
      if (!res.ok || !data.ok) {
        modalBody.innerHTML = `<p class="shop-receipts-status is-error">${escapeHtml(
          data.error || "Could not load receipt."
        )}</p>`;
        return;
      }
      renderDetail(data);
    } catch (_err) {
      if (seq !== detailSeq) return;
      modalBody.innerHTML = `<p class="shop-receipts-status is-error">Could not load receipt.</p>`;
    }
  };

  const printReceiptText = async (text, qr = null, fontStyle = null, ticket = null) => {
    const printer = window.RichcomPrinter;
    const status = printer?.getStatus?.();
    const targetChannel =
      (status?.connected && status.channel) || printChannels[0] || "";
    const styleOverride =
      fontStyle && typeof fontStyle === "object" ? fontStyle : null;
    const qrPayload = {
      payload: qr?.payload || qr?.url || "",
      label: qr?.label || "",
      ready: Boolean(qr?.ready),
      image_data_url: qr?.image_data_url || "",
    };

    if (printer && !printer.canAutoPrint?.(targetChannel)) {
      try {
        // Saved or shop-default Wi‑Fi printers restore on demand here.
        await printer.ensureConnected?.(targetChannel);
      } catch (_err) {
        /* fall through to browser print */
      }
    }
    if (printer?.canAutoPrint?.(targetChannel)) {
      try {
        await printer.print(
          targetChannel,
          text,
          qrPayload,
          styleOverride,
          ticket
        );
        return true;
      } catch (_err) {
        /* fall through to browser print */
      }
    }

    // Wi‑Fi / Bluetooth print straight to the connected device — the Windows
    // print dialog is only a fallback for USB (Windows printer) mode.
    if (targetChannel === "wifi" || targetChannel === "bluetooth") {
      return false;
    }

    if (typeof printer?.browserPrint === "function") {
      try {
        await printer.browserPrint(text, qrPayload, "Receipt", styleOverride, ticket);
        return true;
      } catch (_err) {
        /* fall through */
      }
    }

    const paperMm =
      styleOverride?.paper_width === "58" || styleOverride?.paper_width === "80"
        ? styleOverride.paper_width
        : root.dataset.posReceiptWidth === "58"
          ? "58"
          : "80";
    const pageWidth = `${paperMm}mm`;
    const sizeKey = String(styleOverride?.size || "medium").toLowerCase();
    const pxFallback =
      paperMm === "58"
        ? { small: "8.5px", medium: "9.5px", large: "11px", xlarge: "12px" }
        : { small: "10px", medium: "11.5px", large: "13px", xlarge: "14.5px" };
    const fontSize =
      styleOverride?.[`size_px_${paperMm}`] ||
      pxFallback[sizeKey] ||
      pxFallback.medium;
    const fontWeight = styleOverride?.weight_css || "400";
    const frame = document.createElement("iframe");
    frame.setAttribute("aria-hidden", "true");
    frame.style.cssText =
      "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
    document.body.appendChild(frame);
    const doc = frame.contentDocument || frame.contentWindow?.document;
    if (!doc) {
      frame.remove();
      return false;
    }
    const prepared = printer?.maskReceiptForPrint
      ? printer.maskReceiptForPrint(text, ticket)
      : { text };
    doc.open();
    doc.write(`<!doctype html><html><head><title>Receipt</title>
<link rel="stylesheet" href="/static/css/fonts.css?v=20261019-assets">
<style>
@page { size: ${pageWidth} auto; margin: 2mm; }
body { margin: 0; padding: 2mm; font-family: "Manrope", system-ui, sans-serif; font-size: ${fontSize}; font-weight: 900; color: #000 !important; background: #fff; -webkit-font-smoothing: none; text-shadow: 0.35px 0 0 #000, -0.35px 0 0 #000; }
pre { margin: 0; white-space: pre-wrap; word-break: break-word; }
</style></head><body><pre>${escapeHtml(prepared.text)}</pre></body></html>`);
    doc.close();
    const win = frame.contentWindow;
    if (!win) {
      frame.remove();
      return false;
    }
    win.focus();
    win.print();
    window.setTimeout(() => frame.remove(), 1200);
    return true;
  };

  const submitConfirm = async () => {
    if (!currentDetail?.receipt?.id || !confirmUrlTemplate) return;
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
      refApplies,
    } = confirmFieldValues();
    if (requireDetails) {
      if (refApplies && !ref && !existingRef) {
        setConfirmStatus(
          "Enter the reference number before confirming this receipt.",
          { error: true }
        );
        modalBody?.querySelector("[data-confirm-mpesa-ref]")?.focus();
        syncSubmitConfirm();
        return;
      }
      if (!paymentAt) {
        const { paymentDate, paymentTime } = confirmFieldValues();
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
      const total = Number(currentDetail?.receipt?.total || 0);
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
          ?.querySelector(`[data-confirm-${methodChannels[0]}-amount]`)
          ?.focus();
        syncSubmitConfirm();
        return;
      }
      const paidSum = methodChannels.reduce(
        (sum, channel) => sum + parts[channel],
        0
      );
      if (
        Math.abs(Math.round(paidSum * 100) - Math.round(total * 100)) > 0
      ) {
        setConfirmStatus(
          `${labels} must add up to the receipt total (KSh ${total.toFixed(2)}).`,
          { error: true }
        );
        modalBody
          ?.querySelector(`[data-confirm-${methodChannels[0]}-amount]`)
          ?.focus();
        syncSubmitConfirm();
        return;
      }
    }
    if (!confirmVerified || code.length !== 6) {
      syncSubmitConfirm();
      return;
    }
    const receipt = currentDetail.receipt || {};
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
        mpesa_receipt_number: refApplies ? ref : "",
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
        urlFor(confirmUrlTemplate, currentDetail.receipt.id),
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
      setPageStatus(data.message || "Receipt confirmed.", { error: false });
      renderDetail(data);
      scheduleLoad(80);
    } catch (_err) {
      setConfirmStatus("Confirm failed.", { error: true });
      syncSubmitConfirm();
    }
  };

  const submitReturn = async () => {
    if (!currentDetail?.receipt?.id || !returnUrlTemplate) return;
    const lines = selectedReturnLines();
    const reason = returnReason();
    const code = (
      modalBody?.querySelector("[data-return-login-code]")?.value || ""
    ).trim();
    if (!verified || !lines.length || !reason || code.length !== 6) {
      if (!reason) {
        setReturnStatus("Enter a reason for the return.", { error: true });
      }
      syncConfirmReturn();
      return;
    }
    if (confirmReturnBtn) confirmReturnBtn.disabled = true;
    setReturnStatus("Processing return…");
    try {
      const res = await fetch(urlFor(returnUrlTemplate, currentDetail.receipt.id), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-CSRFToken": getCsrf(),
        },
        credentials: "same-origin",
        body: JSON.stringify({ login_code: code, reason, lines }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        setReturnStatus(data.error || "Return failed.", { error: true });
        syncConfirmReturn();
        return;
      }
      setPageStatus(data.message || "Return completed.", { error: false });
      renderDetail(data);
      setReturnMode(false);
      scheduleLoad(80);
    } catch (_err) {
      setReturnStatus("Return failed.", { error: true });
      syncConfirmReturn();
    }
  };

  filterMode?.addEventListener("change", () => {
    syncFilterPanels();
    scheduleLoad(60);
  });
  kindFilter?.addEventListener("change", () => scheduleLoad(60));
  [dayInput, fromInput, toInput, monthInput, yearInput].forEach((input) => {
    input?.addEventListener("change", () => scheduleLoad(60));
    input?.addEventListener("input", () => scheduleLoad(180));
  });
  searchInput?.addEventListener("input", () => scheduleLoad(180));

  listEl?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-receipt-id]");
    if (!row) return;
    openReceipt(
      row.getAttribute("data-receipt-id"),
      row.getAttribute("data-receipt-source") || "pos"
    );
  });
  listEl?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest("[data-receipt-id]");
    if (!row) return;
    event.preventDefault();
    openReceipt(
      row.getAttribute("data-receipt-id"),
      row.getAttribute("data-receipt-source") || "pos"
    );
  });

  modal?.querySelectorAll("[data-receipt-modal-close]").forEach((el) => {
    el.addEventListener("click", closeModal);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal && !modal.hidden) closeModal();
  });

  reprintBtn?.addEventListener("click", async () => {
    if (!printPayload?.text && !printPayload?.ticket) return;
    reprintBtn.disabled = true;
    try {
      await printReceiptText(
        printPayload.text,
        printPayload.qr,
        {
          ...(printPayload.font || {}),
          paper_width: printPayload.paper || "80",
        },
        printPayload.ticket
      );
    } finally {
      reprintBtn.disabled = false;
    }
  });

  startConfirmBtn?.addEventListener("click", () => setConfirmMode(true));
  cancelConfirmBtn?.addEventListener("click", () => setConfirmMode(false));
  submitConfirmBtn?.addEventListener("click", submitConfirm);
  startReturnBtn?.addEventListener("click", () => setReturnMode(true));
  cancelReturnBtn?.addEventListener("click", () => setReturnMode(false));
  confirmReturnBtn?.addEventListener("click", submitReturn);

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
      .map((channel) => wrap.querySelector(`[data-confirm-${channel}-amount]`))
      .filter(Boolean);
    const n = Number(edited.value);
    if (edited.value === "" || !Number.isFinite(n)) return;
    const total = Number(currentDetail?.receipt?.total || 0);
    let remaining = Math.round(
      Math.max(0, (Number.isFinite(total) ? total : 0) - n) * 100
    ) / 100;
    const others = inputs.filter((el) => el !== edited);
    others.forEach((el, index) => {
      const value =
        index === others.length - 1
          ? Math.max(0, remaining)
          : Math.max(
              0,
              Math.round((remaining / (others.length - index)) * 100) / 100
            );
      el.value = String(value);
      remaining = Math.round((remaining - value) * 100) / 100;
    });
  };

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
      scheduleVerify("confirm");
      syncSubmitConfirm();
      return;
    }
    if (
      target?.matches?.(
        "[data-confirm-payment-date], [data-confirm-payment-time], [data-confirm-mpesa-ref], [data-confirm-credit-amount]"
      )
    ) {
      syncSubmitConfirm();
      return;
    }
    if (target?.matches?.("[data-return-login-code]")) {
      verified = false;
      scheduleVerify("return");
      syncConfirmReturn();
      return;
    }
    if (target?.matches?.("[data-return-reason]")) {
      syncConfirmReturn();
      return;
    }
    if (
      target?.matches?.(
        "[data-return-check], [data-return-qty], [data-return-serial]"
      )
    ) {
      const row = target.closest("[data-return-line]");
      if (row && target.matches("[data-return-serial]")) {
        const check = row.querySelector("[data-return-check]");
        const any = row.querySelectorAll("[data-return-serial]:checked").length;
        if (check) check.checked = any > 0;
      }
      if (row && target.matches("[data-return-check]") && target.checked) {
        const serials = row.querySelectorAll("[data-return-serial]");
        if (serials.length && ![...serials].some((el) => el.checked)) {
          serials.forEach((el) => {
            el.checked = true;
          });
        }
      }
      syncConfirmReturn();
    }
  });

  modalBody?.addEventListener("change", (event) => {
    const target = event.target;
    if (target?.matches?.("[data-confirm-payment-method]")) {
      syncConfirmPaymentSplit();
      syncConfirmRefField();
      syncSubmitConfirm();
      return;
    }
    if (
      target?.matches?.(
        "[data-confirm-payment-date], [data-confirm-payment-time], [data-confirm-mpesa-ref], [data-confirm-credit-amount]"
      )
    ) {
      syncSubmitConfirm();
      return;
    }
    if (
      target?.matches?.(
        "[data-return-check], [data-return-qty], [data-return-serial]"
      )
    ) {
      syncConfirmReturn();
    }
  });

  syncFilterPanels();
  loadReceipts();
  refreshIcons();
})();
