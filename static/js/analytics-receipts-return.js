(() => {
  const root = document.querySelector("[data-ax-receipts]");
  if (!root) return;

  const detailUrlTemplate = root.getAttribute("data-receipt-detail-url") || "";
  const returnUrlTemplate = root.getAttribute("data-receipt-return-url") || "";
  const verifyUrl = root.getAttribute("data-verify-login-url") || "";

  const modal = document.querySelector("[data-receipt-modal]");
  const modalBody = modal?.querySelector("[data-receipt-modal-body]");
  const modalTitle = modal?.querySelector("[data-receipt-modal-title]");
  const modalActions = modal?.querySelector("[data-receipt-modal-actions]");
  const startReturnBtn = modal?.querySelector("[data-receipt-start-return]");
  const cancelReturnBtn = modal?.querySelector("[data-receipt-cancel-return]");
  const confirmReturnBtn = modal?.querySelector("[data-receipt-confirm-return]");

  let detailSeq = 0;
  let verifyTimer = null;
  let verifySeq = 0;
  let verified = false;
  let currentDetail = null;
  let currentShopId = null;
  let returnMode = false;

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
    if (status === "partial_return") return "is-partial";
    return "is-active";
  };

  const closeModal = () => {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("workspace-modal-open");
    currentDetail = null;
    currentShopId = null;
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
    if (startReturnBtn) {
      startReturnBtn.hidden = returnMode || !currentDetail?.receipt?.can_return;
    }
    if (cancelReturnBtn) cancelReturnBtn.hidden = !returnMode;
    if (confirmReturnBtn) {
      confirmReturnBtn.hidden = !returnMode;
      confirmReturnBtn.disabled = true;
    }
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
    if (modalTitle) {
      modalTitle.textContent = receipt.receipt_number || "Receipt";
    }
    if (modalActions) modalActions.hidden = false;
    if (startReturnBtn) startReturnBtn.hidden = !receipt.can_return;
    if (cancelReturnBtn) cancelReturnBtn.hidden = true;
    if (confirmReturnBtn) confirmReturnBtn.hidden = true;

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

    setReturnMode(Boolean(receipt.can_return));
    refreshIcons();
  };

  const openReceipt = async (shopId, receiptId) => {
    if (!detailUrlTemplate || !shopId || !receiptId) return;
    currentShopId = shopId;
    const seq = ++detailSeq;
    openModal();
    if (modalActions) modalActions.hidden = true;
    if (modalBody) {
      modalBody.innerHTML = `<p class="shop-receipts-status">Loading receipt…</p>`;
    }
    if (modalTitle) modalTitle.textContent = "Receipt";
    try {
      const res = await fetch(urlFor(detailUrlTemplate, shopId, receiptId), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await res.json().catch(() => ({}));
      if (seq !== detailSeq) return;
      if (!res.ok || data.ok === false) {
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

  const submitReturn = async () => {
    if (!currentDetail?.receipt?.id || !returnUrlTemplate || !currentShopId) return;
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
      const res = await fetch(
        urlFor(returnUrlTemplate, currentShopId, currentDetail.receipt.id),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
            "X-CSRFToken": getCsrf(),
          },
          credentials: "same-origin",
          body: JSON.stringify({ login_code: code, lines }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        setReturnStatus(data.error || "Return failed.", { error: true });
        syncConfirmReturn();
        return;
      }
      renderDetail(data);
      window.setTimeout(() => window.location.reload(), 700);
    } catch (_err) {
      setReturnStatus("Return failed.", { error: true });
      syncConfirmReturn();
    }
  };

  root.addEventListener("click", (event) => {
    const row = event.target.closest("[data-receipt-id][data-shop-id]");
    if (!row || !root.contains(row)) return;
    if (event.target.closest("a")) return;
    openReceipt(row.getAttribute("data-shop-id"), row.getAttribute("data-receipt-id"));
  });

  root.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest("[data-receipt-id][data-shop-id]");
    if (!row || event.target !== row) return;
    event.preventDefault();
    openReceipt(row.getAttribute("data-shop-id"), row.getAttribute("data-receipt-id"));
  });

  modal?.querySelectorAll("[data-receipt-modal-close]").forEach((el) => {
    el.addEventListener("click", closeModal);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal && !modal.hidden) closeModal();
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
})();
