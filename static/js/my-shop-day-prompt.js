(() => {
  const modal = document.querySelector("[data-shop-day-modal]");
  const form = modal?.querySelector("[data-shop-day-modal-form]");
  if (!modal || !form) return;

  const SNOOZE_MS = 30 * 60 * 1000;
  const mode = modal.getAttribute("data-mode") || "open";
  const toggleUrl = modal.getAttribute("data-day-toggle-url") || "";
  const verifyUrl = modal.getAttribute("data-verify-login-url") || "";
  const codeInput = form.querySelector("[data-day-login-code]");
  const stockInput = form.querySelector("[data-stock-confirmed]");
  const statusEl = form.querySelector("[data-day-status]");
  const submitBtn = form.querySelector("[data-day-submit]");
  const errorsEl = modal.querySelector("[data-shop-day-errors]");

  let verified = false;
  let timer = null;
  let reopenTimer = null;
  let seq = 0;

  const getCsrf = () =>
    form.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const dismissKey = () => {
    const today = new Date().toISOString().slice(0, 10);
    const shopId =
      modal.getAttribute("data-shop-id") ||
      document.querySelector("[data-shop-id]")?.getAttribute("data-shop-id") ||
      "";
    return `shop-day-prompt-dismissed:${shopId}:${today}:${mode}`;
  };

  const getDismissedAt = () => {
    try {
      const raw = sessionStorage.getItem(dismissKey());
      if (!raw) return null;
      const ts = Number(raw);
      return Number.isFinite(ts) ? ts : null;
    } catch (_) {
      return null;
    }
  };

  const isSnoozed = () => {
    const dismissedAt = getDismissedAt();
    if (!dismissedAt) return false;
    return Date.now() - dismissedAt < SNOOZE_MS;
  };

  const snoozeRemainingMs = () => {
    const dismissedAt = getDismissedAt();
    if (!dismissedAt) return 0;
    return Math.max(0, SNOOZE_MS - (Date.now() - dismissedAt));
  };

  const clearDismiss = () => {
    try {
      sessionStorage.removeItem(dismissKey());
    } catch (_) {
      /* ignore */
    }
  };

  const resetForm = () => {
    form.querySelectorAll(
      '[name="cash_amount"], [name="mpesa_amount"], [name="credit_amount"]'
    ).forEach((input) => {
      input.value = "";
    });
    if (stockInput) stockInput.checked = false;
    if (codeInput) codeInput.value = "";
    verified = false;
    showErrors([]);
    setStatus("");
    syncSubmit();
  };

  const setOpen = (open) => {
    modal.hidden = !open;
    modal.setAttribute("aria-hidden", open ? "false" : "true");
    document.body.classList.toggle("workspace-modal-open", open);
    if (open) {
      resetForm();
      if (window.lucide?.createIcons) window.lucide.createIcons();
      window.setTimeout(() => codeInput?.focus(), 40);
    }
  };

  const showErrors = (messages) => {
    if (!errorsEl) return;
    const items = (messages || []).filter(Boolean);
    if (!items.length) {
      errorsEl.hidden = true;
      errorsEl.innerHTML = "";
      return;
    }
    errorsEl.hidden = false;
    errorsEl.innerHTML = items.map((text) => `<p>${text}</p>`).join("");
  };

  const setStatus = (message, { ok = false, error = false } = {}) => {
    if (!statusEl) return;
    statusEl.textContent =
      message ||
      `Enter an active staff member’s 6-digit ID to ${
        mode === "close" ? "close" : "open"
      } the shop.`;
    statusEl.classList.toggle("is-ok", ok);
    statusEl.classList.toggle("is-error", error);
  };

  const syncSubmit = () => {
    const stockOk = Boolean(stockInput?.checked);
    if (submitBtn) submitBtn.disabled = !(verified && stockOk);
  };

  const verifyCode = async () => {
    const code = (codeInput?.value || "").trim();
    const current = ++seq;
    if (code.length < 6) {
      verified = false;
      setStatus(
        code.length
          ? `Enter ${6 - code.length} more digit${6 - code.length === 1 ? "" : "s"}.`
          : ""
      );
      syncSubmit();
      return false;
    }
    if (!/^\d{6}$/.test(code)) {
      verified = false;
      setStatus("Staff ID must be exactly 6 digits.", { error: true });
      syncSubmit();
      return false;
    }
    if (!verifyUrl) {
      verified = false;
      setStatus("Verification is unavailable. Refresh and try again.", {
        error: true,
      });
      syncSubmit();
      return false;
    }

    try {
      const body = new URLSearchParams({ login_code: code });
      const response = await fetch(verifyUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": getCsrf(),
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
        body,
      });
      const data = await response.json().catch(() => ({}));
      if (current !== seq) return false;
      if (!response.ok || !data.ok) {
        verified = false;
        setStatus(data.error || "Not a valid active staff ID.", { error: true });
        syncSubmit();
        return false;
      }
      verified = true;
      setStatus(
        `Verified: ${data.name || "staff"} (${data.employee_id || code}).`,
        { ok: true }
      );
      syncSubmit();
      return true;
    } catch (_) {
      if (current !== seq) return false;
      verified = false;
      setStatus("Could not verify staff ID. Try again.", { error: true });
      syncSubmit();
      return false;
    }
  };

  codeInput?.addEventListener("input", () => {
    verified = false;
    syncSubmit();
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      verifyCode();
    }, 220);
  });
  codeInput?.addEventListener("blur", () => {
    verifyCode();
  });
  stockInput?.addEventListener("change", syncSubmit);

  const balanceFields = () =>
    ["cash_amount", "mpesa_amount", "credit_amount"].map((name) => ({
      name,
      input: form.querySelector(`[name="${name}"]`),
      label:
        name === "cash_amount"
          ? "cash"
          : name === "mpesa_amount"
            ? "M-Pesa"
            : "credit",
    }));

  const missingBalances = () =>
    balanceFields()
      .filter(({ input }) => !(input?.value || "").trim())
      .map(({ label }) => `Enter the ${label} balance.`);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showErrors([]);

    const balanceErrors = missingBalances();
    if (balanceErrors.length) {
      showErrors(balanceErrors);
      balanceFields()
        .find(({ input }) => !(input?.value || "").trim())
        ?.input?.focus();
      return;
    }
    if (!stockInput?.checked) {
      setStatus("Confirm that stock is up to date first.", { error: true });
      return;
    }
    if (!verified) {
      const ok = await verifyCode();
      if (!ok) return;
    }
    if (!toggleUrl) {
      showErrors(["Save action is unavailable. Refresh and try again."]);
      return;
    }

    if (submitBtn) submitBtn.disabled = true;

    try {
      const body = new FormData(form);
      const response = await fetch(toggleUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
        body,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        const errors = Array.isArray(data.errors)
          ? data.errors
          : [data.error || "Could not save balances."];
        showErrors(errors);
        if (submitBtn) submitBtn.disabled = false;
        syncSubmit();
        return;
      }
      clearDismiss();
      window.clearTimeout(reopenTimer);
      window.location.reload();
    } catch (_) {
      showErrors(["Could not save balances. Try again."]);
      if (submitBtn) submitBtn.disabled = false;
      syncSubmit();
    }
  });

  const dismissModal = () => {
    try {
      sessionStorage.setItem(dismissKey(), String(Date.now()));
    } catch (_) {
      /* ignore */
    }
    setOpen(false);
    scheduleReopen();
  };

  document.querySelectorAll('[data-modal-close="shop-day"]').forEach((el) => {
    el.addEventListener("click", dismissModal);
  });

  const scheduleReopen = () => {
    window.clearTimeout(reopenTimer);
    if (modal.getAttribute("data-auto-open") !== "1") return;

    const remaining = snoozeRemainingMs();
    if (remaining <= 0) {
      if (!isSnoozed()) setOpen(true);
      return;
    }

    reopenTimer = window.setTimeout(() => {
      if (modal.getAttribute("data-auto-open") === "1") {
        setOpen(true);
      }
    }, remaining);
  };

  const shouldAutoOpen = () => {
    if (modal.getAttribute("data-auto-open") !== "1") return false;
    return !isSnoozed();
  };

  syncSubmit();

  if (shouldAutoOpen()) {
    setOpen(true);
  } else {
    scheduleReopen();
  }
})();
