(() => {
  const modal = document.querySelector("[data-developer-payment-modal]");
  if (!modal) return;

  function readCookie(name) {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) {
      return decodeURIComponent(parts.pop().split(";").shift() || "");
    }
    return "";
  }

  const allowDismiss = modal.getAttribute("data-allow-dismiss") === "1";
  const stkReady = modal.getAttribute("data-stk-ready") === "1";
  const dismissUrl = modal.getAttribute("data-dismiss-url") || "";
  const stkInitiateUrl = modal.getAttribute("data-stk-initiate-url") || "";
  const stkStatusTemplate =
    modal.getAttribute("data-stk-status-url-template") || "";
  const form = modal.querySelector("[data-developer-payment-form]");
  const phoneInput = modal.querySelector("[data-developer-payment-phone]");
  const payBtn = modal.querySelector("[data-developer-payment-pay]");
  const errorsEl = modal.querySelector("[data-developer-payment-errors]");
  const statusEl = modal.querySelector("[data-developer-payment-status]");
  const csrfToken =
    form?.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    readCookie("csrftoken") ||
    "";

  let pollTimer = null;

  function setOpen(open) {
    modal.hidden = !open;
    modal.setAttribute("aria-hidden", open ? "false" : "true");
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
  }

  function setError(text) {
    if (!errorsEl) return;
    errorsEl.hidden = !text;
    errorsEl.textContent = text || "";
  }

  function setStatus(text) {
    if (!statusEl) return;
    statusEl.hidden = !text;
    statusEl.textContent = text || "";
  }

  async function dismiss() {
    if (!allowDismiss) return;
    setError("");
    try {
      if (dismissUrl) {
        await fetch(dismissUrl, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            Accept: "application/json",
            "X-CSRFToken": csrfToken,
          },
        });
      }
    } catch (_err) {
      // Still close locally if the snooze request fails.
    }
    setOpen(false);
  }

  modal.querySelectorAll("[data-developer-payment-dismiss]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.preventDefault();
      dismiss();
    });
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden && allowDismiss) {
      dismiss();
    }
  });

  async function pollStatus(paymentId) {
    const statusUrl = stkStatusTemplate.replace(
      "00000000-0000-0000-0000-000000000000",
      paymentId
    );
    try {
      const response = await fetch(statusUrl, {
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        setStatus(data.error || "Could not check payment status.");
        return false;
      }
      if (data.success || data.status === "success") {
        setStatus("Payment received — thank you. Refreshing…");
        setTimeout(() => {
          setOpen(false);
          window.location.reload();
        }, 900);
        return true;
      }
      if (data.failed || ["failed", "cancelled", "expired"].includes(data.status)) {
        setError(data.result_desc || data.error || "That payment didn’t go through. You can try again.");
        setStatus("");
        if (payBtn) payBtn.disabled = !stkReady;
        return true;
      }
      setStatus("Waiting on your phone — enter the M-Pesa PIN when prompted.");
      return false;
    } catch (_err) {
      setStatus("Still waiting for M-Pesa confirmation…");
      return false;
    }
  }

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!stkReady) {
      setError("M-Pesa isn’t ready yet. Please ask an admin to finish setup.");
      return;
    }
    const phone = (phoneInput?.value || "").trim();
    if (!phone) {
      setError("Enter the M-Pesa number that should receive the prompt.");
      return;
    }
    setError("");
    setStatus("Sending a secure M-Pesa prompt…");
    if (payBtn) payBtn.disabled = true;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    const body = new FormData();
    body.set("phone", phone);
    try {
      const response = await fetch(stkInitiateUrl, {
        method: "POST",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          Accept: "application/json",
          "X-CSRFToken": csrfToken,
        },
        body,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        setError(data.error || "Couldn’t start M-Pesa just now. Please try again.");
        setStatus("");
        if (payBtn) payBtn.disabled = false;
        return;
      }
      const paymentId = data.id || data.payment_id || data.public_id;
      if (!paymentId) {
        setError("Payment started, but we didn’t get a reference back. Try again.");
        if (payBtn) payBtn.disabled = false;
        return;
      }
      setStatus("Check your phone and enter your M-Pesa PIN.");
      pollTimer = setInterval(async () => {
        const done = await pollStatus(paymentId);
        if (done && pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      }, 2500);
    } catch (_err) {
      setError("Network hiccup. Check your connection and try again.");
      setStatus("");
      if (payBtn) payBtn.disabled = false;
    }
  });

  if (!modal.hidden) {
    document.body.classList.add("workspace-modal-open");
  }

  window.lucide?.createIcons?.();
})();
