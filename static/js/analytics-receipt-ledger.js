(function () {
  const receiptModal = document.querySelector("[data-ax-receipt-modal]");
  const payModal = document.querySelector("[data-ax-pay-modal]");

  function creditPaymentsUrl() {
    const section = document.querySelector("[data-ax-section='client-account']");
    return (
      section?.getAttribute("data-client-credit-payments-url") ||
      payModal?.getAttribute("data-client-credit-payments-url") ||
      document
        .querySelector("[data-ax-receipt-manage-modal]")
        ?.getAttribute("data-client-credit-payments-url") ||
      ""
    );
  }

  function redirectAfterPayment(delayMs = 450) {
    const url = creditPaymentsUrl();
    window.setTimeout(() => {
      if (url) {
        window.location.assign(url);
      } else {
        window.location.reload();
      }
    }, delayMs);
  }

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
      const payBy = button.getAttribute("data-receipt-pay-by") || "";
      if (titleEl) titleEl.textContent = number;
      if (metaEl) {
        metaEl.textContent = [
          shop,
          total ? `Total ${total}` : "",
          due ? `Due ${due}` : "",
          payBy ? `Pay by ${payBy}` : "",
        ]
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
    const receiptIdInput = payModal.querySelector("[data-ax-pay-receipt-id]");
    const payTitleEl = payModal.querySelector("[data-ax-pay-title]");
    const payHintEl = payModal.querySelector("[data-ax-pay-hint]");
    const payDueEl = payModal.querySelector("[data-ax-pay-due]");
    const payBalanceEl = payModal.querySelector("[data-ax-pay-balance]");
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

    function openPayModal(source) {
      const accountDueRaw = payModal.getAttribute("data-balance-raw") || "";
      const accountDueLabel = payModal.getAttribute("data-balance") || "";
      const receiptId = (source?.getAttribute?.("data-receipt-id") || "").trim();
      const payAll = !receiptId || source?.getAttribute?.("data-pay-all") === "1";
      const dueRaw = payAll
        ? accountDueRaw
        : source?.getAttribute?.("data-receipt-due-raw") || accountDueRaw;
      const dueLabel = payAll
        ? accountDueLabel
        : source?.getAttribute?.("data-receipt-due") || accountDueLabel;
      const receiptNumber = source?.getAttribute?.("data-receipt-number") || "receipt";

      if (receiptIdInput) receiptIdInput.value = payAll ? "" : receiptId;
      if (payTitleEl) payTitleEl.textContent = payAll ? "Pay all" : `Pay ${receiptNumber}`;
      const dueNumber = Number.parseFloat(String(dueRaw || "0").replace(/,/g, "")) || 0;
      if (payHintEl) {
        if (dueNumber <= 0) {
          payHintEl.textContent = payAll
            ? "No outstanding balance for this filter."
            : "This receipt is fully paid.";
        } else {
          payHintEl.textContent = payAll
            ? "Clears from earliest receipt to latest."
            : "Pays this receipt only.";
        }
      }
      if (payDueEl) payDueEl.textContent = dueLabel;
      if (payBalanceEl) payBalanceEl.textContent = dueLabel;
      if (payAmount) {
        payAmount.value = "";
        if (dueNumber > 0) payAmount.max = String(dueNumber);
        else payAmount.removeAttribute("max");
        payAmount.focus();
      }
      if (paySubmit) paySubmit.disabled = dueNumber <= 0;
      if (stkIdInput) stkIdInput.value = "";
      const cash = payModal.querySelector('[data-ax-pay-method][value="cash"]');
      if (cash) cash.checked = true;
      syncMethodUi();
      if (dueNumber <= 0) {
        setPayStatus(
          payAll
            ? "No outstanding balance to pay."
            : "This receipt has no balance due.",
          { error: true }
        );
      } else {
        setPayStatus("");
      }
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
      if (receiptIdInput) receiptIdInput.value = "";
      if (paySubmit) paySubmit.disabled = false;
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
        redirectAfterPayment();
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
      const payOpen = event.target.closest("[data-ax-pay-open]");
      if (payOpen) {
        event.preventDefault();
        openPayModal(payOpen);
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

  const manageModal = document.querySelector("[data-ax-receipt-manage-modal]");
  if (manageModal) {
    const manageForm = manageModal.querySelector("[data-ax-receipt-manage-form]");
    const manageTitle = manageModal.querySelector("[data-ax-receipt-manage-title]");
    const manageMeta = manageModal.querySelector("[data-ax-receipt-manage-meta]");
    const manageId = manageModal.querySelector("[data-ax-receipt-manage-id]");
    const manageDueDate = manageModal.querySelector("[data-ax-receipt-manage-due-date]");
    const manageDueLabel = manageModal.querySelector("[data-ax-receipt-manage-due-label]");
    const managePayWrap = manageModal.querySelector("[data-ax-receipt-manage-pay-wrap]");
    const manageAmount = manageModal.querySelector("[data-ax-receipt-manage-amount]");
    const managePhoneRow = manageModal.querySelector("[data-ax-receipt-manage-phone-row]");
    const managePhone = manageModal.querySelector("[data-ax-receipt-manage-phone]");
    const manageStkId = manageModal.querySelector("[data-ax-receipt-manage-stk-id]");
    const manageStatus = manageModal.querySelector("[data-ax-receipt-manage-status]");
    const managePaySubmit = manageModal.querySelector("[data-ax-receipt-manage-pay-submit]");
    const manageSaveDate = manageModal.querySelector("[data-ax-receipt-manage-save-date]");
    const manageMethodInputs = manageModal.querySelectorAll("[data-ax-receipt-manage-method]");
    const payUrl = manageModal.getAttribute("data-pay-url") || "";
    const stkInitiateUrl = manageModal.getAttribute("data-stk-initiate-url") || "";
    const stkStatusTemplate =
      manageModal.getAttribute("data-stk-status-url-template") || "";
    const receiptUpdateTemplate =
      manageModal.getAttribute("data-receipt-update-url-template") || "";
    const stkReady = manageModal.getAttribute("data-stk-ready") === "1";
    const canPayAccount = manageModal.getAttribute("data-can-pay-account") === "1";
    const defaultPhone = manageModal.getAttribute("data-client-phone") || "";

    let activeManageButton = null;

    function manageCsrf() {
      return (
        manageForm?.querySelector("[name=csrfmiddlewaretoken]")?.value ||
        document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
        document.cookie
          .split("; ")
          .find((row) => row.startsWith("csrftoken="))
          ?.split("=")[1] ||
        ""
      );
    }

    function manageSelectedMethod() {
      const checked = manageModal.querySelector("[data-ax-receipt-manage-method]:checked");
      return checked?.value || "cash";
    }

    function syncManageMethodUi() {
      const method = manageSelectedMethod();
      manageModal.querySelectorAll(".ax-pay-method").forEach((label) => {
        const input = label.querySelector("input");
        label.classList.toggle("is-active", Boolean(input?.checked));
      });
      if (managePhoneRow) managePhoneRow.hidden = method !== "mpesa";
    }

    function setManageStatus(message, { error = false } = {}) {
      if (!manageStatus) return;
      manageStatus.hidden = !message;
      manageStatus.textContent = message || "";
      manageStatus.className = error
        ? "ax-pay-status is-error"
        : "ax-pay-status is-ok";
    }

    function updateLedgerRowFromManage(data) {
      if (!activeManageButton) return;
      const rowId = activeManageButton.getAttribute("data-receipt-id");
      const row = document.querySelector(`[data-ax-receipt-row][data-row-id="credit-${rowId}"]`);
      if (!row) return;

      if (data.pay_by) {
        const payByCell = row.querySelector("[data-label='Pay by']");
        if (payByCell) {
          const overdue = Boolean(data.pay_by_overdue);
          payByCell.innerHTML = overdue
            ? `<span class="ax-pay-by is-overdue">${data.pay_by}</span>`
            : data.pay_by;
        }
        activeManageButton.setAttribute("data-receipt-pay-by", data.pay_by);
        if (data.pay_by_raw) {
          activeManageButton.setAttribute("data-receipt-pay-by-raw", data.pay_by_raw);
        }
        const viewBtn = row.querySelector("[data-ax-receipt-view]");
        if (viewBtn) viewBtn.setAttribute("data-receipt-pay-by", data.pay_by);
        const haystack = row.getAttribute("data-search-text") || "";
        if (data.pay_by && !haystack.includes(data.pay_by)) {
          row.setAttribute("data-search-text", `${haystack} ${data.pay_by}`.trim());
        }
      }

      if (data.receipt_due !== undefined) {
        const dueCell = row.querySelector("[data-label='Due']");
        if (dueCell) dueCell.textContent = data.receipt_due;
        activeManageButton.setAttribute("data-receipt-due", data.receipt_due);
        if (data.receipt_due_raw) {
          activeManageButton.setAttribute("data-receipt-due-raw", data.receipt_due_raw);
        }
        const viewBtn = row.querySelector("[data-ax-receipt-view]");
        if (viewBtn) viewBtn.setAttribute("data-receipt-due", data.receipt_due);
        if (manageDueLabel) manageDueLabel.textContent = data.receipt_due;
        if (manageAmount && data.receipt_due_raw) {
          manageAmount.max = data.receipt_due_raw;
        }
        const dueLeft = Number(data.receipt_due_raw || 0);
        const canPayRow = dueLeft > 0;
        activeManageButton.setAttribute(
          "data-receipt-can-pay",
          canPayRow ? "1" : "0"
        );
        if (managePayWrap) {
          managePayWrap.hidden = !canPayAccount || !canPayRow;
        }
        if (!canPayRow && manageAmount) manageAmount.value = "";
      }

      if (data.receipt_status) {
        const statusCell = row.querySelector("[data-label='Status'] .ax-status");
        if (statusCell) {
          statusCell.textContent = data.receipt_status;
          statusCell.className = `ax-status is-${data.receipt_status_tone || "neutral"}`;
        }
      }

      if (data.account_balance) {
        const balanceEl = document.querySelector("[data-ax-account-balance]");
        if (balanceEl) balanceEl.textContent = data.account_balance;
      }
    }

    function closeManageModal() {
      manageModal.hidden = true;
      activeManageButton = null;
      if (manageForm) manageForm.reset();
      if (manageId) manageId.value = "";
      if (manageStkId) manageStkId.value = "";
      if (managePayWrap) managePayWrap.hidden = true;
      setManageStatus("");
      syncModalOpen();
    }

    function openManageModal(button) {
      activeManageButton = button;
      const number = button.getAttribute("data-receipt-number") || "Receipt";
      const shop = button.getAttribute("data-receipt-shop") || "";
      const total = button.getAttribute("data-receipt-total") || "";
      const due = button.getAttribute("data-receipt-due") || "";
      const payBy = button.getAttribute("data-receipt-pay-by") || "";
      const payByRaw = button.getAttribute("data-receipt-pay-by-raw") || "";
      const dueRaw = button.getAttribute("data-receipt-due-raw") || "";
      const receiptId = button.getAttribute("data-receipt-id") || "";
      const canPayRow = button.getAttribute("data-receipt-can-pay") === "1";

      if (manageTitle) manageTitle.textContent = number;
      if (manageMeta) {
        manageMeta.textContent = [shop, total ? `Total ${total}` : "", due ? `Due ${due}` : ""]
          .filter(Boolean)
          .join(" · ");
      }
      if (manageId) manageId.value = receiptId;
      if (manageDueDate) {
        manageDueDate.value = payByRaw;
      }
      if (manageDueLabel) manageDueLabel.textContent = due || "—";
      if (manageAmount) {
        manageAmount.value = "";
        manageAmount.max = dueRaw || "";
        manageAmount.required = canPayRow;
      }
      if (managePhone) managePhone.value = defaultPhone;
      if (manageStkId) manageStkId.value = "";
      const cash = manageModal.querySelector('[data-ax-receipt-manage-method][value="cash"]');
      if (cash) cash.checked = true;
      syncManageMethodUi();
      if (managePayWrap) {
        managePayWrap.hidden = !canPayAccount || !canPayRow;
      }
      setManageStatus("");
      manageModal.hidden = false;
      syncModalOpen();
      window.lucide?.createIcons?.();
      manageDueDate?.focus();
    }

    async function pollManageStk(paymentId) {
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
        setManageStatus(
          data.result_desc || "Waiting for customer to confirm on their phone…"
        );
      }
      throw new Error("Timed out waiting for M-Pesa confirmation.");
    }

    async function saveDueDate() {
      if (!receiptUpdateTemplate || !manageId?.value) return;
      const dueDate = (manageDueDate?.value || "").trim();
      if (!dueDate) {
        setManageStatus("Enter a payment due date.", { error: true });
        return;
      }
      if (manageSaveDate) manageSaveDate.disabled = true;
      try {
        const url = receiptUpdateTemplate.replace("__ID__", manageId.value);
        const body = new URLSearchParams({ credit_due_date: dueDate });
        const response = await fetch(url, {
          method: "POST",
          headers: {
            "X-CSRFToken": manageCsrf(),
            Accept: "application/json",
          },
          body,
          credentials: "same-origin",
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          setManageStatus(data.error || "Could not update due date.", { error: true });
          return;
        }
        updateLedgerRowFromManage(data);
        setManageStatus(data.message || "Due date updated.");
      } catch (err) {
        setManageStatus(err.message || "Could not update due date.", { error: true });
      } finally {
        if (manageSaveDate) manageSaveDate.disabled = false;
      }
    }

    async function submitReceiptPayment(event) {
      event.preventDefault();
      if (!manageForm || !payUrl || !manageId?.value) return;
      const amount = (manageAmount?.value || "").trim();
      if (!amount) {
        setManageStatus("Enter an amount to pay.", { error: true });
        return;
      }
      const method = manageSelectedMethod();
      if (managePaySubmit) managePaySubmit.disabled = true;
      if (manageStkId) manageStkId.value = "";

      try {
        if (method === "mpesa") {
          if (!stkReady || !stkInitiateUrl) {
            throw new Error("STK Push is not enabled in Daraja settings.");
          }
          const phone = (managePhone?.value || "").trim();
          if (!phone) {
            throw new Error("Enter the client phone for M-Pesa STK Push.");
          }
          setManageStatus("Sending M-Pesa STK Push…");
          const body = new URLSearchParams({
            kind: manageForm.querySelector("[name=kind]")?.value || "",
            account_id: manageForm.querySelector("[name=account_id]")?.value || "",
            receipt_id: manageId.value,
            amount,
            phone,
          });
          const startRes = await fetch(stkInitiateUrl, {
            method: "POST",
            headers: {
              "X-CSRFToken": manageCsrf(),
              Accept: "application/json",
            },
            body,
            credentials: "same-origin",
          });
          const startData = await startRes.json().catch(() => ({}));
          if (!startRes.ok || !startData.ok || !startData.id) {
            throw new Error(startData.error || "Could not start M-Pesa STK Push.");
          }
          setManageStatus("Waiting for customer to confirm on their phone…");
          const confirmed = await pollManageStk(startData.id);
          if (manageStkId) manageStkId.value = confirmed.id;
          setManageStatus(
            confirmed.mpesa_receipt_number
              ? `M-Pesa confirmed (${confirmed.mpesa_receipt_number}). Applying…`
              : "M-Pesa confirmed. Applying…"
          );
        } else {
          setManageStatus("Recording payment…");
        }

        const body = new FormData(manageForm);
        body.set("payment_method", method);
        const response = await fetch(payUrl, {
          method: "POST",
          headers: {
            "X-CSRFToken": manageCsrf(),
            Accept: "application/json",
          },
          body,
          credentials: "same-origin",
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          setManageStatus(data.error || "Payment failed.", { error: true });
          return;
        }
        setManageStatus(data.message || "Payment recorded.");
        redirectAfterPayment();
      } catch (err) {
        setManageStatus(err.message || "Payment failed.", { error: true });
      } finally {
        if (managePaySubmit) managePaySubmit.disabled = false;
      }
    }

    manageMethodInputs.forEach((input) => {
      input.addEventListener("change", syncManageMethodUi);
    });

    if (manageSaveDate) {
      manageSaveDate.addEventListener("click", (event) => {
        event.preventDefault();
        saveDueDate();
      });
    }

    if (manageForm) {
      manageForm.addEventListener("submit", submitReceiptPayment);
    }

    document.addEventListener("click", (event) => {
      const manageBtn = event.target.closest("[data-ax-receipt-manage]");
      if (manageBtn) {
        event.preventDefault();
        openManageModal(manageBtn);
        return;
      }
      if (event.target.closest("[data-ax-receipt-manage-close]")) {
        event.preventDefault();
        closeManageModal();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !manageModal.hidden) {
        closeManageModal();
      }
    });

    syncManageMethodUi();
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
