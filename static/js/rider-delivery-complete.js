(() => {
  const csrf =
    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith("csrftoken="))
      ?.slice("csrftoken=".length) ||
    "";

  const setStatus = (el, text, { error = false } = {}) => {
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
    el.classList.toggle("is-error", Boolean(error));
  };

  const pollStk = async (statusUrl, { onUpdate, onDone, onFail }) => {
    let tries = 0;
    const maxTries = 45;
    while (tries < maxTries) {
      tries += 1;
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      try {
        const response = await fetch(statusUrl, {
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          credentials: "same-origin",
        });
        const data = await response.json();
        if (!data?.ok) {
          onFail?.(data?.error || "Could not check STK status.");
          return;
        }
        onUpdate?.(data);
        if (data.success) {
          onDone?.(data);
          return;
        }
        if (data.failed) {
          onFail?.(data.result_desc || "STK payment failed or was cancelled.");
          return;
        }
      } catch {
        /* keep polling through transient errors */
      }
    }
    onFail?.("Timed out waiting for M-Pesa confirmation.");
  };

  const modeLabels = {
    unpaid: "Mark delivered · unpaid",
    manual: "Mark delivered · paid",
    stk: "Mark delivered · paid",
  };

  const setPayMode = (flow, mode) => {
    const form = flow.closest("form[data-rider-complete]");
    const submit = form?.querySelector("[data-complete-submit]");
    const refInput = form?.querySelector("[data-payment-reference]");
    const atInput = form?.querySelector("[data-payment-at]");
    const amountField = form?.querySelector("[data-payment-amount]");

    flow.querySelectorAll("[data-pay-mode]").forEach((btn) => {
      const active = btn.getAttribute("data-pay-mode") === mode;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    flow.querySelectorAll("[data-pay-panel]").forEach((panel) => {
      const active = panel.getAttribute("data-pay-panel") === mode;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
    flow.dataset.mode = mode;
    if (submit) {
      submit.textContent = modeLabels[mode] || "Mark delivered";
      submit.disabled = mode === "stk" && !flow.dataset.stkPaid;
    }

    if (mode === "unpaid") {
      if (refInput) refInput.value = "";
      if (atInput) atInput.value = "";
      if (amountField) amountField.removeAttribute("required");
      if (refInput) refInput.removeAttribute("required");
    } else if (mode === "manual") {
      if (refInput) refInput.setAttribute("required", "required");
      if (amountField) amountField.setAttribute("required", "required");
      delete flow.dataset.stkPaid;
      if (submit) submit.disabled = false;
    } else if (mode === "stk") {
      if (refInput) refInput.removeAttribute("required");
      if (amountField) amountField.removeAttribute("required");
      if (submit) submit.disabled = !flow.dataset.stkPaid;
    }
  };

  document.querySelectorAll("[data-rider-pay-flow]").forEach((flow) => {
    setPayMode(flow, "unpaid");
    flow.querySelectorAll("[data-pay-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        setPayMode(flow, btn.getAttribute("data-pay-mode") || "unpaid");
      });
    });

    const form = flow.closest("form[data-rider-complete]");
    form?.addEventListener("submit", (event) => {
      const mode = flow.dataset.mode || "unpaid";
      const refInput = form.querySelector("[data-payment-reference]");
      if (mode === "unpaid" && refInput) {
        refInput.value = "";
      }
      if (mode === "manual" && refInput && !(refInput.value || "").trim()) {
        event.preventDefault();
        refInput.focus();
        refInput.reportValidity?.();
      }
      if (mode === "stk" && !flow.dataset.stkPaid) {
        event.preventDefault();
      }
    });
  });

  document.querySelectorAll("[data-rider-stk]").forEach((root) => {
    const sendBtn = root.querySelector("[data-stk-send]");
    const statusEl = root.querySelector("[data-stk-status]");
    const phoneInput = root.querySelector("[data-stk-phone]");
    const amountInput = root.querySelector("[data-stk-amount]");
    const form = root.closest("form[data-rider-complete]");
    const flow = form?.querySelector("[data-rider-pay-flow]");
    const refInput = form?.querySelector("[data-payment-reference]");
    const atInput = form?.querySelector("[data-payment-at]");
    const amountField = form?.querySelector("[data-payment-amount]");
    const submit = form?.querySelector("[data-complete-submit]");
    if (!sendBtn) return;

    sendBtn.addEventListener("click", async () => {
      const url = sendBtn.getAttribute("data-stk-url") || "";
      if (!url) return;
      sendBtn.disabled = true;
      if (flow) delete flow.dataset.stkPaid;
      if (submit) submit.disabled = true;
      setStatus(statusEl, "Sending STK prompt…");
      const body = new URLSearchParams();
      body.set("phone", phoneInput?.value || "");
      body.set("amount", amountInput?.value || "");
      try {
        const response = await fetch(url, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrf,
            "Content-Type": "application/x-www-form-urlencoded",
          },
          credentials: "same-origin",
          body: body.toString(),
        });
        const data = await response.json();
        if (!data?.ok) {
          setStatus(statusEl, data?.error || "STK prompt failed.", { error: true });
          sendBtn.disabled = false;
          return;
        }
        setStatus(
          statusEl,
          data.message || "Ask the customer to enter their M-Pesa PIN…"
        );
        const statusUrl = `${url.replace(/\/?$/, "/")}${encodeURIComponent(data.id)}/`;
        await pollStk(statusUrl, {
          onUpdate: (payload) => {
            if (payload.pending) {
              setStatus(statusEl, "Waiting for M-Pesa confirmation…");
            }
          },
          onDone: (payload) => {
            const receipt = (payload.mpesa_receipt_number || "").trim();
            if (refInput && receipt) refInput.value = receipt;
            if (amountField && payload.amount) amountField.value = payload.amount;
            if (atInput) {
              const now = new Date();
              const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
                .toISOString()
                .slice(0, 16);
              atInput.value = local;
            }
            if (flow) flow.dataset.stkPaid = "1";
            if (submit) {
              submit.disabled = false;
              submit.textContent = modeLabels.stk;
            }
            setStatus(
              statusEl,
              receipt
                ? `Paid · Ref ${receipt}. Tap Mark delivered · paid.`
                : "Payment confirmed. Tap Mark delivered · paid."
            );
            sendBtn.disabled = false;
          },
          onFail: (message) => {
            setStatus(statusEl, message, { error: true });
            sendBtn.disabled = false;
          },
        });
      } catch {
        setStatus(statusEl, "Could not send STK prompt.", { error: true });
        sendBtn.disabled = false;
      }
    });
  });
})();
