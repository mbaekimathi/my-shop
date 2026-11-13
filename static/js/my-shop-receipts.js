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

  // Default confirm moment: today's date + print clock time (editable).
  const defaultConfirmPaymentAt = (receipt = {}) => {
    const saved = String(receipt.confirmed_payment_at_local || "").trim();
    if (saved) return saved;
    const printed = String(receipt.created_at_local || "").trim();
    const printTime = printed.includes("T")
      ? printed.split("T")[1].slice(0, 5)
      : "";
    return printTime ? `${todayIso()}T${printTime}` : `${todayIso()}T00:00`;
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
    if (status === "partial_return" || status === "partial" || status === "unpaid") {
      return "is-partial";
    }
    if (status === "confirmed") return "is-confirmed";
    return "is-pending";
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
        return `<tr class="shop-receipts-row" data-receipt-id="${row.id}" data-receipt-source="${source}" tabindex="0">
  <td data-label="Receipt">
    <strong>${escapeHtml(row.receipt_number)}</strong>
  </td>
  <td data-label="Type"><span class="shop-receipt-kind shop-receipt-kind--${escapeHtml(
    row.kind
  )}">${escapeHtml(row.kind_label)}</span></td>
  <td class="shop-receipts-client" data-label="Client">${client}</td>
  <td class="shop-receipts-total" data-label="Total">KSh ${escapeHtml(money(row.total))}</td>
  <td class="shop-receipts-when" data-label="When">${escapeHtml(row.created_label)}</td>
  <td data-label="Status"><span class="shop-receipt-status ${statusClass(
    row.status
  )}">${escapeHtml(row.status_label)}</span></td>
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
    const paymentAt = modalBody?.querySelector("[data-confirm-payment-at]");
    const receipt = currentDetail?.receipt || {};
    if (paymentAt) {
      paymentAt.value = defaultConfirmPaymentAt(receipt);
    }
    const refInput = modalBody?.querySelector("[data-confirm-mpesa-ref]");
    if (refInput && !refInput.value) {
      refInput.value = String(receipt.mpesa_receipt_number || "").trim();
    }
    window.setTimeout(() => {
      modalBody?.querySelector("[data-confirm-login-code]")?.focus?.({
        preventScroll: true,
      });
    }, 40);
    setConfirmStatus(
      receipt.require_mpesa_reference_on_confirm
        ? "Defaults: today + print time. Enter reference number and staff 6-digit ID."
        : "Defaults: today + print time. Enter staff 6-digit ID to confirm."
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

  const confirmFieldValues = () => {
    const receipt = currentDetail?.receipt || {};
    const paymentAt = (
      modalBody?.querySelector("[data-confirm-payment-at]")?.value || ""
    ).trim();
    const ref = (
      modalBody?.querySelector("[data-confirm-mpesa-ref]")?.value || ""
    ).trim();
    const existingRef = String(receipt.mpesa_receipt_number || "").trim();
    const requireDetails = Boolean(receipt.require_mpesa_reference_on_confirm);
    return { paymentAt, ref, existingRef, requireDetails };
  };

  const syncSubmitConfirm = () => {
    if (!submitConfirmBtn) return;
    const { paymentAt, ref, existingRef, requireDetails } = confirmFieldValues();
    const detailsOk =
      !requireDetails ||
      ((Boolean(ref) || Boolean(existingRef)) && Boolean(paymentAt));
    submitConfirmBtn.disabled = !(
      confirmMode &&
      confirmVerified &&
      detailsOk
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
      modalTitle.textContent = receipt.receipt_number || "Receipt";
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
    const savedPaymentAt = String(
      receipt.confirmed_payment_at_local || ""
    ).trim();
    const paymentAtLocal = defaultConfirmPaymentAt(receipt);
    const requireConfirmDetails = Boolean(
      receipt.require_mpesa_reference_on_confirm
    );
    const optionalHint = requireConfirmDetails ? "required" : "optional";
    const requiredAttrs = requireConfirmDetails
      ? `required aria-required="true"`
      : `aria-required="false"`;
    const confirmMetaRows = [];
    if (existingRef) {
      confirmMetaRows.push(
        `<div><dt>Reference</dt><dd>${escapeHtml(existingRef)}</dd></div>`
      );
    }
    if (savedPaymentAt) {
      confirmMetaRows.push(
        `<div><dt>Paid at</dt><dd>${escapeHtml(
          savedPaymentAt.replace("T", " ")
        )}</dd></div>`
      );
    }
    const clientBlock =
      receipt.client_name || receipt.client_phone
        ? `<div class="shop-receipt-card">
  <h3>${partyTitle}</h3>
  <dl>
    <div><dt>Name</dt><dd>${escapeHtml(receipt.client_name || "—")}</dd></div>
    <div><dt>Phone</dt><dd>${escapeHtml(receipt.client_phone || "—")}</dd></div>
    ${confirmMetaRows.join("")}
  </dl>
</div>`
        : `<div class="shop-receipt-card">
  <h3>${partyTitle}</h3>
  ${
    confirmMetaRows.length
      ? `<dl>${confirmMetaRows.join("")}</dl>`
      : `<p class="shop-receipt-muted">No ${partyTitle.toLowerCase()} details on this receipt.</p>`
  }
</div>`;

    const itemsHtml = lines
      .map((line) => {
        const serials = (line.serial_numbers || [])
          .map((s) => `<code>${escapeHtml(s)}</code>`)
          .join(" ");
        const returnedNote =
          line.returned_quantity > 0
            ? `<span class="shop-receipt-muted">Returned ${line.returned_quantity} of ${line.quantity}</span>`
            : "";
        return `<tr>
  <td class="shop-receipt-item-primary" data-label="Item">
    <strong>${escapeHtml(line.item_name)}</strong>
    ${returnedNote}
    ${serials ? `<div class="shop-receipt-serials">${serials}</div>` : ""}
  </td>
  <td data-label="Qty">${line.remaining_quantity}<span class="shop-receipt-muted"> / ${line.quantity}</span></td>
  <td data-label="Price">KSh ${escapeHtml(money(line.unit_price))}</td>
  <td data-label="Total">KSh ${escapeHtml(money(line.remaining_total))}</td>
</tr>`;
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

    const paperWidth = String(
      payload.receipt_paper_width || printPayload.paper || "80"
    );
    const ticket = printPayload.ticket;
    const ticketHtml =
      ticket && window.RichcomPrinter?.renderTicketHtml
        ? window.RichcomPrinter.renderTicketHtml(
            ticket,
            printPayload.qr,
            paperWidth === "58" ? "58" : "80"
          )
        : "";
    const ticketPreview = ticketHtml
      ? `<div class="shop-receipt-ticket-preview">
  <h3>Document preview</h3>
  <div class="receipt-ticket receipt-ticket--${escapeHtml(
    paperWidth === "58" ? "58" : "80"
  )}" data-receipt-modal-ticket>
    ${ticketHtml}
  </div>
</div>`
      : "";

    if (modalBody) {
      modalBody.innerHTML = `
<div class="shop-receipt-detail" data-detail-panel>
  <div class="shop-receipt-summary">
    <div class="shop-receipt-summary-badges">
      <span class="shop-receipt-kind shop-receipt-kind--${escapeHtml(
        receipt.kind
      )}">${escapeHtml(receipt.kind_label)}</span>
      <span class="shop-receipt-status ${statusClass(
        receipt.status
      )}">${escapeHtml(receipt.status_label)}</span>
    </div>
    <p class="shop-receipt-summary-when"><strong>${escapeHtml(
      receipt.created_label || ""
    )}</strong></p>
    <p class="shop-receipt-muted">Cashier: ${escapeHtml(
      receipt.cashier || "—"
    )}</p>
  </div>
  ${ticketPreview}
  ${clientBlock}
  <div class="shop-receipt-card shop-receipt-card--items">
    <h3>Items</h3>
    <div class="shop-receipt-items-wrap">
      <table class="shop-receipt-items">
        <thead>
          <tr>
            <th>Item</th>
            <th>Qty</th>
            <th>Price</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>${itemsHtml || `<tr><td colspan="4">No items</td></tr>`}</tbody>
      </table>
    </div>
    <dl class="shop-receipt-totals">
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
      ${
        receipt.kind === "sale"
          ? `<div><dt>Payment</dt><dd>${escapeHtml(
              receipt.payment_label || "—"
            )}</dd></div>`
          : ""
      }
    </dl>
  </div>
</div>
<div class="shop-receipt-return shop-receipt-confirm" data-confirm-panel hidden>
  <p class="shop-receipt-muted">
    Confirm this sale. Date defaults to today and time to when it was printed — change either if needed, then confirm.
    ${
      requireConfirmDetails
        ? "Reference number is compulsory."
        : "Reference number is optional."
    }
  </p>
  <div class="shop-receipt-card shop-receipt-card--confirm">
    <div class="shop-receipt-confirm-fields">
      <div class="shop-receipt-mpesa-ref" data-confirm-payment-at-wrap>
        <label class="shop-receipt-mpesa-ref-label" for="shop-confirm-payment-at">
          Payment date &amp; time
          <span class="shop-receipt-muted">(${optionalHint})</span>
        </label>
        <input
          id="shop-confirm-payment-at"
          class="shop-receipt-mpesa-ref-input"
          type="datetime-local"
          name="payment_at"
          value="${escapeHtml(paymentAtLocal)}"
          data-confirm-payment-at
          ${requiredAttrs}
        >
      </div>
      <div class="shop-receipt-mpesa-ref" data-confirm-mpesa-ref-wrap>
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
    const { paymentAt, ref, existingRef, requireDetails } = confirmFieldValues();
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
        setConfirmStatus(
          "Enter the payment date and time before confirming.",
          { error: true }
        );
        modalBody?.querySelector("[data-confirm-payment-at]")?.focus();
        syncSubmitConfirm();
        return;
      }
    }
    if (!confirmVerified || code.length !== 6) {
      syncSubmitConfirm();
      return;
    }
    if (submitConfirmBtn) submitConfirmBtn.disabled = true;
    setConfirmStatus("Confirming receipt…");
    try {
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
          body: JSON.stringify({
            login_code: code,
            mpesa_receipt_number: ref,
            payment_at: paymentAt,
          }),
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

  modalBody?.addEventListener("input", (event) => {
    const target = event.target;
    if (target?.matches?.("[data-confirm-login-code]")) {
      confirmVerified = false;
      scheduleVerify("confirm");
      syncSubmitConfirm();
      return;
    }
    if (
      target?.matches?.(
        "[data-confirm-payment-at], [data-confirm-mpesa-ref]"
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
    if (
      target?.matches?.(
        "[data-confirm-payment-at], [data-confirm-mpesa-ref]"
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
