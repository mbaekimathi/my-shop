(function () {
  const receiptModal = document.querySelector("[data-ax-receipt-modal]");
  const payModal = document.querySelector("[data-ax-pay-modal]");

  function syncModalOpen() {
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderLines(lines) {
    if (!Array.isArray(lines) || !lines.length) {
      return '<p class="ax-empty">No items on this receipt.</p>';
    }
    const rows = lines
      .map((line) => {
        const meta = line.meta
          ? `<span class="ax-receipt-line-meta">${escapeHtml(line.meta)}</span>`
          : "";
        return `<tr>
          <td class="ax-cell--primary" data-label="Item">
            <strong>${escapeHtml(line.name || "Item")}</strong>
            ${meta}
          </td>
          <td data-label="Qty">${escapeHtml(line.qty ?? "—")}</td>
          <td data-label="Unit">${escapeHtml(line.unit || "—")}</td>
          <td data-label="Total">${escapeHtml(line.total || "—")}</td>
        </tr>`;
      })
      .join("");
    return `<div class="ax-table-wrap">
      <table class="ax-table ax-receipt-lines">
        <thead>
          <tr>
            <th>Item</th>
            <th>Qty</th>
            <th>Unit</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }

  if (receiptModal) {
    const titleEl = receiptModal.querySelector("[data-ax-receipt-modal-title]");
    const metaEl = receiptModal.querySelector("[data-ax-receipt-modal-meta]");
    const bodyEl = receiptModal.querySelector("[data-ax-receipt-modal-body]");

    function closeReceiptModal() {
      receiptModal.hidden = true;
      if (titleEl) titleEl.textContent = "Receipt items";
      if (metaEl) metaEl.textContent = "";
      if (bodyEl) bodyEl.innerHTML = "";
      syncModalOpen();
    }

    function openReceiptModal(button) {
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
      const number = button.getAttribute("data-receipt-number") || "Receipt";
      const shop = button.getAttribute("data-receipt-shop") || "";
      const total = button.getAttribute("data-receipt-total") || "";
      const due = button.getAttribute("data-receipt-due") || "";
      if (titleEl) titleEl.textContent = number;
      if (metaEl) {
        metaEl.textContent = [shop, total ? `Total ${total}` : "", due ? `Due ${due}` : ""]
          .filter(Boolean)
          .join(" · ");
      }
      if (bodyEl) bodyEl.innerHTML = renderLines(lines);
      receiptModal.hidden = false;
      syncModalOpen();
      if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons();
      }
    }

    document.addEventListener("click", (event) => {
      const viewBtn = event.target.closest("[data-ax-receipt-view]");
      if (viewBtn) {
        event.preventDefault();
        openReceiptModal(viewBtn);
        return;
      }
      if (event.target.closest("[data-ax-receipt-modal-close]")) {
        event.preventDefault();
        closeReceiptModal();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !receiptModal.hidden) {
        closeReceiptModal();
      }
    });
  }

  if (payModal) {
    const payForm = payModal.querySelector("[data-ax-pay-form]");
    const payAmount = payModal.querySelector("[data-ax-pay-amount]");
    const payStatusEl = payModal.querySelector("[data-ax-pay-status]");
    const paySubmit = payModal.querySelector("[data-ax-pay-submit]");
    const payUrl = payModal.getAttribute("data-pay-url") || "";
    const stkInitiateUrl = payModal.getAttribute("data-stk-initiate-url") || "";
    const stkStatusTemplate =
      payModal.getAttribute("data-stk-status-url-template") || "";
    const stkReady = payModal.getAttribute("data-stk-ready") === "1";
    const phoneRow = payModal.querySelector("[data-ax-pay-phone-row]");
    const phoneInput = payModal.querySelector("[data-ax-pay-phone]");
    const stkIdInput = payModal.querySelector("[data-ax-stk-id]");
    const methodInputs = payModal.querySelectorAll("[data-ax-pay-method]");

    function getCsrf() {
      return (
        payForm?.querySelector("[name=csrfmiddlewaretoken]")?.value ||
        document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
        document.cookie
          .split("; ")
          .find((row) => row.startsWith("csrftoken="))
          ?.split("=")[1] ||
        ""
      );
    }

    function selectedMethod() {
      const checked = payModal.querySelector("[data-ax-pay-method]:checked");
      return checked?.value || "cash";
    }

    function syncMethodUi() {
      const method = selectedMethod();
      payModal.querySelectorAll(".ax-pay-method").forEach((label) => {
        const input = label.querySelector("input");
        label.classList.toggle("is-active", Boolean(input?.checked));
      });
      if (phoneRow) phoneRow.hidden = method !== "mpesa";
    }

    function setPayStatus(message, { error = false } = {}) {
      if (!payStatusEl) return;
      payStatusEl.hidden = !message;
      payStatusEl.textContent = message || "";
      payStatusEl.className = error ? "ax-pay-status is-error" : "ax-pay-status is-ok";
    }

    function openPayModal() {
      if (payAmount) {
        payAmount.value = "";
        payAmount.focus();
      }
      if (stkIdInput) stkIdInput.value = "";
      const cash = payModal.querySelector('[data-ax-pay-method][value="cash"]');
      if (cash) cash.checked = true;
      syncMethodUi();
      setPayStatus("");
      payModal.hidden = false;
      syncModalOpen();
      if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons();
      }
    }

    function closePayModal() {
      payModal.hidden = true;
      if (payAmount) payAmount.value = "";
      if (stkIdInput) stkIdInput.value = "";
      setPayStatus("");
      syncModalOpen();
    }

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
          throw new Error(data.result_desc || "Customer did not complete M-Pesa payment.");
        }
        setPayStatus(
          data.result_desc || "Waiting for customer to confirm on their phone…"
        );
      }
      throw new Error("Timed out waiting for M-Pesa confirmation.");
    }

    async function submitPayment(event) {
      event.preventDefault();
      if (!payForm || !payUrl) return;
      const amount = (payAmount?.value || "").trim();
      if (!amount) {
        setPayStatus("Enter an amount to pay.", { error: true });
        return;
      }
      const method = selectedMethod();
      if (paySubmit) paySubmit.disabled = true;
      if (stkIdInput) stkIdInput.value = "";

      try {
        if (method === "mpesa") {
          if (!stkReady || !stkInitiateUrl) {
            throw new Error("STK Push is not enabled in Daraja settings.");
          }
          const phone = (phoneInput?.value || "").trim();
          if (!phone) {
            throw new Error("Enter the client phone for M-Pesa STK Push.");
          }
          setPayStatus("Sending M-Pesa STK Push…");
          const body = new URLSearchParams({
            kind: payForm.querySelector("[name=kind]")?.value || "",
            account_id: payForm.querySelector("[name=account_id]")?.value || "",
            amount,
            phone,
          });
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
            throw new Error(startData.error || "Could not start M-Pesa STK Push.");
          }
          setPayStatus("Waiting for customer to confirm on their phone…");
          const confirmed = await pollStk(startData.id);
          if (stkIdInput) stkIdInput.value = confirmed.id;
          setPayStatus(
            confirmed.mpesa_receipt_number
              ? `M-Pesa confirmed (${confirmed.mpesa_receipt_number}). Applying…`
              : "M-Pesa confirmed. Applying…"
          );
        } else {
          setPayStatus("Recording payment…");
        }

        const body = new FormData(payForm);
        body.set("payment_method", method);
        const response = await fetch(payUrl, {
          method: "POST",
          headers: {
            "X-CSRFToken": getCsrf(),
            Accept: "application/json",
          },
          body,
          credentials: "same-origin",
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          setPayStatus(data.error || "Payment failed.", { error: true });
          return;
        }
        setPayStatus(data.message || "Payment recorded.");
        window.setTimeout(() => {
          window.location.reload();
        }, 450);
      } catch (err) {
        setPayStatus(err.message || "Payment failed.", { error: true });
      } finally {
        if (paySubmit) paySubmit.disabled = false;
      }
    }

    methodInputs.forEach((input) => {
      input.addEventListener("change", syncMethodUi);
    });

    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-ax-pay-open]")) {
        event.preventDefault();
        openPayModal();
        return;
      }
      if (event.target.closest("[data-ax-pay-modal-close]")) {
        event.preventDefault();
        closePayModal();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !payModal.hidden) {
        closePayModal();
      }
    });

    if (payForm) {
      payForm.addEventListener("submit", submitPayment);
    }
    syncMethodUi();
  }

  const ledgerCard = document.querySelector("[data-ax-ledger-card]");
  const ledgerSearch = ledgerCard?.querySelector("[data-ax-ledger-search]");
  if (ledgerSearch && ledgerCard) {
    const rows = () => ledgerCard.querySelectorAll("[data-ax-receipt-row]");
    const noResults = ledgerCard.querySelector("[data-ax-ledger-no-results]");
    const table = ledgerCard.querySelector(".ax-table");

    const filterRows = () => {
      const query = ledgerSearch.value.trim().toLowerCase();
      const tokens = query.split(/\s+/).filter(Boolean);
      let visible = 0;
      rows().forEach((row) => {
        const haystack = (row.getAttribute("data-search-text") || row.textContent || "").toLowerCase();
        const match = !tokens.length || tokens.every((token) => haystack.includes(token));
        row.hidden = !match;
        if (match) visible += 1;
      });
      if (noResults) noResults.hidden = visible > 0 || !query;
      if (table) table.hidden = visible === 0 && Boolean(query);
    };

    let timer = 0;
    ledgerSearch.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(filterRows, 120);
    });
    ledgerSearch.addEventListener("search", filterRows);
  }

  const shareModal = document.querySelector("[data-ax-share-modal]");
  if (shareModal) {
    const sharePhone = shareModal.querySelector("[data-ax-share-phone]");
    const shareMessagePreview = shareModal.querySelector("[data-ax-share-message-preview]");
    const shareCopyMessage = shareModal.querySelector("[data-ax-share-copy-message]");
    const shareOpenWa = shareModal.querySelector("[data-ax-share-open-wa]");
    const shareCopy = shareModal.querySelector("[data-ax-share-copy]");
    const shareStatus = shareModal.querySelector("[data-ax-share-status]");
    const companyName = shareModal.getAttribute("data-company-name") || "MY-SHOP";
    const clientName = shareModal.getAttribute("data-client-name") || "there";
    const balance = shareModal.getAttribute("data-balance") || "";
    const baseUrl = shareModal.getAttribute("data-credit-note-url") || "";

    const normalizeWaPhone = (value) => {
      let digits = String(value || "").replace(/\D/g, "");
      if (digits.startsWith("0")) digits = `254${digits.slice(1)}`;
      if (digits.startsWith("254")) return digits;
      if (digits.length === 9) return `254${digits}`;
      return digits;
    };

    const buildMessage = () => {
      const url = baseUrl;
      return (
        `Hello ${clientName},\n\n` +
        `Your credit balance at ${companyName} is ${balance}.\n` +
        `View your credit notes and pay with M-Pesa here:\n${url}\n\n` +
        `— ${companyName}`
      );
    };

    const syncMessagePreview = () => {
      const message = buildMessage();
      if (shareMessagePreview) shareMessagePreview.textContent = message;
      return message;
    };

    const syncWhatsAppHref = () => {
      if (!shareOpenWa) return;
      const phone = normalizeWaPhone(sharePhone?.value || "");
      const text = encodeURIComponent(syncMessagePreview());
      shareOpenWa.href = phone ? `https://wa.me/${phone}?text=${text}` : "#";
      shareOpenWa.classList.toggle("is-disabled", !phone);
      shareOpenWa.setAttribute("aria-disabled", phone ? "false" : "true");
    };

    const setShareStatus = (message) => {
      if (!shareStatus) return;
      shareStatus.hidden = !message;
      shareStatus.textContent = message || "";
    };

    const copyText = async (text, successLabel) => {
      try {
        await navigator.clipboard.writeText(text);
        setShareStatus(successLabel);
      } catch (_err) {
        const area = document.createElement("textarea");
        area.value = text;
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
        setShareStatus(successLabel);
      }
    };

    const openShareModal = () => {
      shareModal.hidden = false;
      document.body.classList.add("workspace-modal-open");
      syncMessagePreview();
      syncWhatsAppHref();
      window.lucide?.createIcons?.();
      sharePhone?.focus();
    };

    const closeShareModal = () => {
      shareModal.hidden = true;
      document.body.classList.remove("workspace-modal-open");
      setShareStatus("");
    };

    sharePhone?.addEventListener("input", syncWhatsAppHref);

    shareCopy?.addEventListener("click", () => {
      copyText(baseUrl, "Link copied.");
    });

    shareCopyMessage?.addEventListener("click", () => {
      copyText(buildMessage(), "Message copied.");
    });

    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-ax-share-open]")) {
        event.preventDefault();
        openShareModal();
        return;
      }
      if (event.target.closest("[data-ax-share-close]")) {
        event.preventDefault();
        closeShareModal();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !shareModal.hidden) closeShareModal();
    });

    syncMessagePreview();
    syncWhatsAppHref();
  }
})();
