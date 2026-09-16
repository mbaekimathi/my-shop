(() => {
  const root = document.querySelector("[data-ax-confirm-receipts]");
  if (!root) return;

  const detailUrlTemplate = root.getAttribute("data-receipt-detail-url") || "";
  const confirmUrlTemplate = root.getAttribute("data-receipt-confirm-url") || "";
  const cancelUrlTemplate = root.getAttribute("data-receipt-cancel-url") || "";

  const modal = document.querySelector("[data-confirm-receipt-modal]");
  const modalBody = modal?.querySelector("[data-confirm-receipt-modal-body]");
  const modalTitle = modal?.querySelector("[data-confirm-receipt-modal-title]");
  const modalActions = modal?.querySelector("[data-confirm-receipt-modal-actions]");
  const confirmBtn = modal?.querySelector("[data-confirm-receipt-confirm]");
  const cancelBtn = modal?.querySelector("[data-confirm-receipt-cancel]");

  let detailSeq = 0;
  let currentDetail = null;
  let currentShopId = null;
  let busy = false;

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
    if (status === "confirmed") return "is-confirmed";
    return "is-pending";
  };

  const closeModal = () => {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("workspace-modal-open");
    currentDetail = null;
    currentShopId = null;
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
    if (confirmBtn) confirmBtn.disabled = busy || !currentDetail?.receipt?.can_confirm;
    if (cancelBtn) cancelBtn.disabled = busy || !currentDetail?.receipt?.can_cancel;
  };

  const renderDetail = (payload) => {
    const receipt = payload.receipt || {};
    currentDetail = payload;
    if (modalTitle) {
      modalTitle.textContent = receipt.receipt_number || "Receipt";
    }
    if (modalActions) modalActions.hidden = false;
    if (confirmBtn) {
      confirmBtn.hidden = !receipt.can_confirm;
      confirmBtn.disabled = !receipt.can_confirm;
    }
    if (cancelBtn) {
      cancelBtn.hidden = !receipt.can_cancel;
      cancelBtn.disabled = !receipt.can_cancel;
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

    if (modalBody) {
      modalBody.innerHTML = `
<div class="shop-receipt-detail">
  <div class="shop-receipt-summary">
    <div class="shop-receipt-summary-badges">
      <span class="shop-receipt-kind shop-receipt-kind--${escapeHtml(
        receipt.kind || ""
      )}">${escapeHtml(receipt.kind_label || "")}</span>
      <span class="shop-receipt-status ${statusClass(
        receipt.status
      )}">${escapeHtml(receipt.status_label || "")}</span>
    </div>
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
        ? `<div class="shop-receipt-items-wrap">
  <table class="shop-receipt-items">
    <thead>
      <tr>
        <th>Item</th>
        <th>Qty</th>
        <th>Price</th>
        <th>Total</th>
      </tr>
    </thead>
    <tbody>${itemsHtml}</tbody>
  </table>
</div>`
        : `<p class="shop-receipt-muted">No line items on this receipt.</p>`
    }
  </div>
</div>`;
    }
    refreshIcons();
  };

  const loadDetail = async (shopId, receiptId) => {
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
      const res = await fetch(urlFor(detailUrlTemplate, shopId, receiptId), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
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

  const postAction = async (template, successFallback) => {
    if (busy || !currentDetail?.receipt?.id || !currentShopId || !template) return;
    setActionBusy(true);
    try {
      const res = await fetch(
        urlFor(template, currentShopId, currentDetail.receipt.id),
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-CSRFToken": getCsrf(),
          },
          credentials: "same-origin",
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        window.alert(data.error || "Action failed.");
        setActionBusy(false);
        return;
      }
      closeModal();
      window.location.reload();
    } catch (_err) {
      window.alert(successFallback || "Action failed.");
      setActionBusy(false);
    }
  };

  root.querySelectorAll(".ax-receipt-row[data-receipt-id]").forEach((row) => {
    const open = () => {
      const receiptId = Number(row.getAttribute("data-receipt-id") || 0);
      const shopId = Number(row.getAttribute("data-shop-id") || 0);
      if (!receiptId || !shopId) return;
      loadDetail(shopId, receiptId);
    };
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
  });

  modal
    ?.querySelectorAll("[data-confirm-receipt-modal-close]")
    .forEach((el) => el.addEventListener("click", closeModal));

  confirmBtn?.addEventListener("click", () => {
    if (!currentDetail?.receipt?.can_confirm) return;
    postAction(confirmUrlTemplate, "Could not confirm receipt.");
  });

  cancelBtn?.addEventListener("click", () => {
    if (!currentDetail?.receipt?.can_cancel) return;
    const number = currentDetail?.receipt?.receipt_number || "this receipt";
    if (
      !window.confirm(
        `Cancel ${number}? Sale and credit items will be restocked.`
      )
    ) {
      return;
    }
    postAction(cancelUrlTemplate, "Could not cancel receipt.");
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal && !modal.hidden) {
      closeModal();
    }
  });
})();
