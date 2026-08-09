(() => {
  const root = document.querySelector("[data-shop-receipts]");
  if (!root) return;

  const listUrl = root.getAttribute("data-receipts-list-url") || "";
  const detailUrlTemplate =
    root.getAttribute("data-receipt-detail-url") || "";
  const returnUrlTemplate =
    root.getAttribute("data-receipt-return-url") || "";
  const verifyUrl = root.getAttribute("data-verify-login-url") || "";
  const printChannels = (root.getAttribute("data-print-channels") || "")
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);

  const searchInput = root.querySelector("[data-receipts-search]");
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
  const startReturnBtn = modal?.querySelector("[data-receipt-start-return]");
  const cancelReturnBtn = modal?.querySelector("[data-receipt-cancel-return]");
  const confirmReturnBtn = modal?.querySelector("[data-receipt-confirm-return]");

  let searchTimer = null;
  let loadSeq = 0;
  let detailSeq = 0;
  let verifyTimer = null;
  let verifySeq = 0;
  let verified = false;
  let currentDetail = null;
  let returnMode = false;
  let printPayload = null;

  const todayIso = () => {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
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
    params.set("filter", mode);
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
    if (status === "partial_return") return "is-partial";
    return "is-active";
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
        return `<tr class="shop-receipts-row" data-receipt-id="${row.id}" tabindex="0">
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
    verified = false;
  };

  const openModal = () => {
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("workspace-modal-open");
    refreshIcons();
  };

  const setReturnMode = (on) => {
    returnMode = Boolean(on);
    if (startReturnBtn) startReturnBtn.hidden = returnMode || !currentDetail?.receipt?.can_return;
    if (cancelReturnBtn) cancelReturnBtn.hidden = !returnMode;
    if (confirmReturnBtn) {
      confirmReturnBtn.hidden = !returnMode;
      confirmReturnBtn.disabled = true;
    }
    if (reprintBtn) reprintBtn.hidden = returnMode;
    const panel = modalBody?.querySelector("[data-return-panel]");
    const detailPanel = modalBody?.querySelector("[data-detail-panel]");
    if (panel) panel.hidden = !returnMode;
    if (detailPanel) detailPanel.hidden = returnMode;
    if (!returnMode) {
      verified = false;
      syncConfirmReturn();
      return;
    }
    verified = false;
    const codeInput = modalBody?.querySelector("[data-return-login-code]");
    if (codeInput) codeInput.value = "";
    setReturnStatus("Enter an active staff 6-digit ID to authorise the return.");
    syncConfirmReturn();
    refreshIcons();
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

  const syncConfirmReturn = () => {
    if (!confirmReturnBtn) return;
    const lines = selectedReturnLines();
    confirmReturnBtn.disabled = !(returnMode && verified && lines.length);
  };

  const verifyReturnCode = async () => {
    const codeInput = modalBody?.querySelector("[data-return-login-code]");
    const code = (codeInput?.value || "").trim();
    const current = ++verifySeq;
    if (code.length < 6) {
      verified = false;
      setReturnStatus(
        code.length
          ? `Enter ${6 - code.length} more digit${6 - code.length === 1 ? "" : "s"}.`
          : "Enter an active staff 6-digit ID to authorise the return."
      );
      syncConfirmReturn();
      return;
    }
    if (!verifyUrl) {
      verified = false;
      setReturnStatus("Verification is unavailable.", { error: true });
      syncConfirmReturn();
      return;
    }
    setReturnStatus("Checking staff ID…");
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
      verified = Boolean(res.ok && data.ok);
      setReturnStatus(
        verified
          ? `Authorised${data.name ? `: ${data.name}` : ""}.`
          : data.error || "Invalid staff ID.",
        { ok: verified, error: !verified }
      );
      syncConfirmReturn();
    } catch (_err) {
      if (current !== verifySeq) return;
      verified = false;
      setReturnStatus("Could not verify staff ID.", { error: true });
      syncConfirmReturn();
    }
  };

  const scheduleVerify = () => {
    window.clearTimeout(verifyTimer);
    verifyTimer = window.setTimeout(verifyReturnCode, 280);
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
    if (startReturnBtn) {
      startReturnBtn.hidden = !receipt.can_return;
    }
    if (cancelReturnBtn) cancelReturnBtn.hidden = true;
    if (confirmReturnBtn) confirmReturnBtn.hidden = true;
    if (reprintBtn) reprintBtn.hidden = false;

    const lines = receipt.lines || [];
    const returnable = receipt.returnable_lines || [];
    const clientBlock =
      receipt.client_name || receipt.client_phone
        ? `<div class="shop-receipt-card">
  <h3>Customer</h3>
  <dl>
    <div><dt>Name</dt><dd>${escapeHtml(receipt.client_name || "—")}</dd></div>
    <div><dt>Phone</dt><dd>${escapeHtml(receipt.client_phone || "—")}</dd></div>
  </dl>
</div>`
        : `<div class="shop-receipt-card">
  <h3>Customer</h3>
  <p class="shop-receipt-muted">No customer details on this receipt.</p>
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
  <input type="number" min="1" max="${maxQty}" step="1" value="${maxQty}" data-return-qty>
</label>`
  }
</div>`;
      })
      .join("");

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
<div class="shop-receipt-return" data-return-panel hidden>
  <p class="shop-receipt-muted">Select items to return. Stock is restored and the sale/credit updates automatically.</p>
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
  <p class="shop-receipts-status" data-return-status>Enter an active staff 6-digit ID to authorise the return.</p>
</div>`;
    }

    returnMode = false;
    refreshIcons();
  };

  const openReceipt = async (receiptId) => {
    if (!detailUrlTemplate || !modalBody) return;
    const seq = ++detailSeq;
    openModal();
    if (modalTitle) modalTitle.textContent = "Receipt";
    if (modalActions) modalActions.hidden = true;
    modalBody.innerHTML = `<p class="shop-receipts-status">Loading receipt…</p>`;
    try {
      const res = await fetch(urlFor(detailUrlTemplate, receiptId), {
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
    doc.open();
    doc.write(`<!doctype html><html><head><title>Receipt</title>
<style>
@page { size: ${pageWidth} auto; margin: 2mm; }
body { margin: 0; padding: 2mm; font-family: "Courier New", monospace; font-size: ${fontSize}; font-weight: 900; color: #000 !important; background: #fff; -webkit-font-smoothing: none; text-shadow: 0.35px 0 0 #000, -0.35px 0 0 #000; }
pre { margin: 0; white-space: pre-wrap; word-break: break-word; }
</style></head><body><pre>${escapeHtml(text)}</pre></body></html>`);
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

  const submitReturn = async () => {
    if (!currentDetail?.receipt?.id || !returnUrlTemplate) return;
    const lines = selectedReturnLines();
    const code = (
      modalBody?.querySelector("[data-return-login-code]")?.value || ""
    ).trim();
    if (!verified || !lines.length || code.length !== 6) {
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
        body: JSON.stringify({ login_code: code, lines }),
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
  [dayInput, fromInput, toInput, monthInput, yearInput].forEach((input) => {
    input?.addEventListener("change", () => scheduleLoad(60));
    input?.addEventListener("input", () => scheduleLoad(180));
  });
  searchInput?.addEventListener("input", () => scheduleLoad(180));

  listEl?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-receipt-id]");
    if (!row) return;
    openReceipt(row.getAttribute("data-receipt-id"));
  });
  listEl?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest("[data-receipt-id]");
    if (!row) return;
    event.preventDefault();
    openReceipt(row.getAttribute("data-receipt-id"));
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

  startReturnBtn?.addEventListener("click", () => setReturnMode(true));
  cancelReturnBtn?.addEventListener("click", () => setReturnMode(false));
  confirmReturnBtn?.addEventListener("click", submitReturn);

  modalBody?.addEventListener("input", (event) => {
    const target = event.target;
    if (target?.matches?.("[data-return-login-code]")) {
      verified = false;
      scheduleVerify();
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
