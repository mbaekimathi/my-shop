(function () {
  const root = document.querySelector("[data-credit-note]");
  if (!root) return;

  const payForm = root.querySelector("[data-credit-note-pay-form]");
  const payAmount = root.querySelector("[data-credit-note-amount]");
  const payPhone = root.querySelector("[data-credit-note-phone]");
  const paySubmit = root.querySelector("[data-credit-note-pay-submit]");
  const payStatus = root.querySelector("[data-credit-note-pay-status]");
  const stkIdInput = root.querySelector("[data-credit-note-stk-id]");
  const balanceEl = root.querySelector("[data-credit-note-balance]");
  const modal = document.querySelector("[data-credit-note-modal]");
  const modalTitle = modal?.querySelector("[data-credit-note-modal-title]");
  const modalMeta = modal?.querySelector("[data-credit-note-modal-meta]");
  const modalBody = modal?.querySelector("[data-credit-note-modal-body]");

  const payUrl = root.getAttribute("data-pay-url") || "";
  const stkInitiateUrl = root.getAttribute("data-stk-initiate-url") || "";
  const stkStatusTemplate = root.getAttribute("data-stk-status-url-template") || "";
  const stkReady = root.getAttribute("data-stk-ready") === "1";

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const getCsrf = () =>
    payForm?.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const setPayStatus = (message, { error = false } = {}) => {
    if (!payStatus) return;
    payStatus.hidden = !message;
    payStatus.textContent = message || "";
    payStatus.className = error
      ? "credit-note-pay-status is-error"
      : "credit-note-pay-status is-ok";
  };

  const renderLines = (lines) => {
    if (!Array.isArray(lines) || !lines.length) {
      return '<p class="ax-empty">No items on this receipt.</p>';
    }
    const rows = lines
      .map(
        (line) => `<tr>
          <td class="ax-cell--primary" data-label="Item"><strong>${escapeHtml(line.name || "Item")}</strong></td>
          <td data-label="Qty">${escapeHtml(line.qty ?? "—")}</td>
          <td data-label="Unit">${escapeHtml(line.unit || "—")}</td>
          <td data-label="Total">${escapeHtml(line.total || "—")}</td>
        </tr>`
      )
      .join("");
    return `<div class="ax-table-wrap credit-note-lines-wrap"><table class="ax-table credit-note-lines"><thead><tr><th>Item</th><th>Qty</th><th>Unit</th><th>Total</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  };

  const openModal = (button) => {
    if (!modal) return;
    const linesId = button.getAttribute("data-lines-id");
    const script = linesId ? document.getElementById(linesId) : null;
    let lines = [];
    if (script) {
      try {
        lines = JSON.parse(script.textContent || "[]");
      } catch (_err) {
        lines = [];
      }
    }
    if (modalTitle) {
      modalTitle.textContent = button.getAttribute("data-receipt-number") || "Receipt items";
    }
    if (modalMeta) {
      modalMeta.textContent = [
        button.getAttribute("data-receipt-shop") || "",
        button.getAttribute("data-receipt-total")
          ? `Total ${button.getAttribute("data-receipt-total")}`
          : "",
        button.getAttribute("data-receipt-due")
          ? `Due ${button.getAttribute("data-receipt-due")}`
          : "",
      ]
        .filter(Boolean)
        .join(" · ");
    }
    if (modalBody) modalBody.innerHTML = renderLines(lines);
    modal.hidden = false;
    document.body.classList.add("workspace-modal-open");
    window.lucide?.createIcons?.();
  };

  const closeModal = () => {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("workspace-modal-open");
  };

  async function pollStk(paymentId) {
    const statusUrl = stkStatusTemplate.replace("__ID__", paymentId);
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 2500));
      const response = await fetch(statusUrl, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not check M-Pesa status.");
      }
      if (data.success) return data;
      if (data.failed) {
        throw new Error(data.result_desc || "Payment was not completed on the phone.");
      }
      setPayStatus(data.result_desc || "Waiting for you to enter your M-Pesa PIN…");
    }
    throw new Error("Timed out waiting for M-Pesa confirmation.");
  }

  async function submitPayment(event) {
    event.preventDefault();
    if (!payForm || !payUrl) return;
    const amount = (payAmount?.value || "").trim();
    const phone = (payPhone?.value || "").trim();
    if (!amount) {
      setPayStatus("Enter an amount to pay.", { error: true });
      return;
    }
    if (!phone) {
      setPayStatus("Enter your M-Pesa phone number.", { error: true });
      return;
    }
    if (!stkReady || !stkInitiateUrl) {
      setPayStatus("M-Pesa payments are not available right now.", { error: true });
      return;
    }
    if (paySubmit) paySubmit.disabled = true;
    if (stkIdInput) stkIdInput.value = "";

    try {
      setPayStatus("Sending M-Pesa prompt to your phone…");
      const body = new URLSearchParams({ amount, phone });
      const startRes = await fetch(stkInitiateUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCsrf(),
          Accept: "application/json",
        },
        body,
        credentials: "same-origin",
      });
      const startData = await startRes.json().catch(() => ({}));
      if (!startRes.ok || !startData.ok || !startData.id) {
        throw new Error(startData.error || "Could not start M-Pesa payment.");
      }
      setPayStatus("Check your phone and enter your M-Pesa PIN…");
      const confirmed = await pollStk(startData.id);
      if (stkIdInput) stkIdInput.value = confirmed.id;
      setPayStatus("Payment confirmed. Updating your account…");

      const payBody = new FormData(payForm);
      payBody.set("amount", amount);
      payBody.set("phone", phone);
      payBody.set("stk_payment_id", confirmed.id);
      const response = await fetch(payUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCsrf(),
          Accept: "application/json",
        },
        body: payBody,
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not record payment.");
      }
      setPayStatus(data.message || "Payment recorded.");
      window.setTimeout(() => window.location.reload(), 700);
    } catch (err) {
      setPayStatus(err.message || "Payment failed.", { error: true });
    } finally {
      if (paySubmit) paySubmit.disabled = false;
    }
  }

  document.addEventListener("click", (event) => {
    const viewBtn = event.target.closest("[data-credit-note-view]");
    if (viewBtn) {
      event.preventDefault();
      openModal(viewBtn);
      return;
    }
    if (event.target.closest("[data-credit-note-modal-close]")) {
      event.preventDefault();
      closeModal();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal && !modal.hidden) closeModal();
  });

  payForm?.addEventListener("submit", submitPayment);

  window.lucide?.createIcons?.();
})();
