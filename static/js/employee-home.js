(() => {
  const form = document.querySelector("[data-employee-receipts-filters]");
  if (!form) return;

  const mode = form.querySelector("[data-receipt-filter-mode]");
  const panels = form.querySelectorAll("[data-filter-panel]");

  const sync = () => {
    const current = (mode?.value || "all").trim();
    panels.forEach((panel) => {
      const match = panel.getAttribute("data-filter-panel") === current;
      panel.hidden = !match;
      panel.classList.toggle("is-hidden", !match);
    });
  };

  mode?.addEventListener("change", sync);
  sync();
})();

(() => {
  const modal = document.querySelector("[data-employee-receipt-modal]");
  if (!modal) return;

  const modalBody = modal.querySelector("[data-receipt-modal-body]");
  const modalTitle = modal.querySelector("[data-receipt-modal-title]");
  let loadSeq = 0;

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const money = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value ?? "0");
    return n.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
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

  const isCreditReceipt = (receipt = {}) =>
    Boolean(receipt.is_credit || receipt.kind === "credit");

  const creditDueValue = (receipt = {}) => {
    const due = Number(receipt.credit_due);
    if (Number.isFinite(due) && due > 0) return due;
    const total = Number(receipt.total);
    const paid = Number(receipt.amount_paid);
    if (Number.isFinite(total) && Number.isFinite(paid)) {
      return Math.max(total - paid, 0);
    }
    return 0;
  };

  const refreshIcons = () => {
    if (typeof window.lucide?.createIcons === "function") {
      window.lucide.createIcons();
    }
  };

  const closeModal = () => {
    modal.hidden = true;
    document.body.classList.remove("workspace-modal-open");
  };

  const openModal = () => {
    modal.hidden = false;
    document.body.classList.add("workspace-modal-open");
    refreshIcons();
  };

  const renderDetail = (payload) => {
    const receipt = payload.receipt || {};
    if (modalTitle) {
      modalTitle.textContent = receipt.tally_code || receipt.receipt_number || "Receipt";
    }

    const lines = receipt.lines || [];
    const itemsHtml = lines
      .map((line) => {
        const serials = (line.serial_numbers || [])
          .map((s) => `<code>${escapeHtml(s)}</code>`)
          .join(" ");
        const returnedNote =
          Number(line.returned_quantity) > 0
            ? `<span class="shop-receipt-muted">Returned ${escapeHtml(
                line.returned_quantity
              )} of ${escapeHtml(line.quantity)}</span>`
            : "";
        return `<li class="shop-receipt-sheet-line">
  <div class="shop-receipt-sheet-line-item">
    <strong>${escapeHtml(line.item_name)}</strong>
    ${returnedNote}
    ${serials ? `<div class="shop-receipt-serials">${serials}</div>` : ""}
  </div>
  <span class="shop-receipt-sheet-line-qty">${escapeHtml(
    line.quantity
  )}</span>
  <span class="shop-receipt-sheet-line-price">${escapeHtml(
    money(line.unit_price)
  )}</span>
  <span class="shop-receipt-sheet-line-total">${escapeHtml(
    money(line.line_total)
  )}</span>
</li>`;
      })
      .join("");

    const clientLine = receipt.client_name
      ? `${receipt.client_name}${
          receipt.client_phone ? ` · ${receipt.client_phone}` : ""
        }`
      : receipt.client_phone || "";
    const paidAtLabel =
      receipt.status === "confirmed" ||
      receipt.status === "partial_return" ||
      receipt.is_partial_confirmation
        ? receipt.confirmed_payment_label || ""
        : "";
    const existingRef = String(receipt.mpesa_receipt_number || "").trim();
    const isCredit = isCreditReceipt(receipt);
    const creditDue = creditDueValue(receipt);
    const creditBalanceRow =
      isCredit && creditDue > 0
        ? `<div><dt>Balance due</dt><dd>KSh ${escapeHtml(
            money(creditDue)
          )}</dd></div>`
        : "";
    const paymentRow =
      receipt.kind === "sale" || receipt.payment_label
        ? `<div><dt>Payment</dt><dd>${escapeHtml(
            receipt.payment_label || "—"
          )}</dd></div>`
        : "";
    const receiptText = String(payload.receipt_text || "").trim();

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
            receipt.status
          )}">${escapeHtml(receipt.status_label)}</span>
        </div>
      </div>
      <dl class="shop-receipt-sheet-rows">
        <div><dt>Sold</dt><dd>${escapeHtml(
          receipt.created_label || "—"
        )}</dd></div>
        ${
          paidAtLabel
            ? `<div><dt>Paid</dt><dd>${escapeHtml(paidAtLabel)}</dd></div>`
            : ""
        }
        <div><dt>Printed by</dt><dd>${escapeHtml(
          receipt.cashier || "—"
        )}</dd></div>
        <div><dt>Customer</dt><dd>${escapeHtml(
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
        <div><dt>Subtotal</dt><dd>KSh ${escapeHtml(
          money(receipt.subtotal)
        )}</dd></div>
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
      ${
        receiptText
          ? `<div class="shop-receipt-sheet-sep" aria-hidden="true"></div>
      <p class="shop-receipt-text-label">Printed receipt</p>
      <pre class="shop-receipt-text">${escapeHtml(receiptText)}</pre>`
          : ""
      }
    </div>
  </div>
</div>`;
    }
  };

  const renderError = (message) => {
    if (!modalBody) return;
    modalBody.innerHTML = `<p class="shop-receipts-status is-error">${escapeHtml(
      message || "Could not load the receipt."
    )}</p>`;
  };

  const openReceipt = async (url) => {
    if (!url) return;
    const seq = ++loadSeq;
    if (modalTitle) modalTitle.textContent = "Receipt";
    if (modalBody) {
      modalBody.innerHTML =
        '<p class="shop-receipts-status">Loading receipt…</p>';
    }
    openModal();
    try {
      const response = await fetch(url, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      const payload = await response.json().catch(() => ({}));
      if (seq !== loadSeq) return;
      if (!response.ok || payload.ok === false) {
        renderError(payload.error || payload.message);
        return;
      }
      renderDetail(payload);
      refreshIcons();
    } catch (error) {
      if (seq !== loadSeq) return;
      renderError("Network error — could not load the receipt.");
    }
  };

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-receipt-view]");
    if (trigger) {
      event.preventDefault();
      openReceipt(trigger.getAttribute("data-detail-url") || "");
      return;
    }
    if (event.target.closest("[data-receipt-modal-close]")) {
      closeModal();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });
})();
