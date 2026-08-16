(() => {
  const root = document.querySelector("[data-shop-day]");
  const form = root?.querySelector("[data-shop-day-form]");
  if (!root || !form) return;

  const codeInput = form.querySelector("[data-day-login-code]");
  const stockInput = form.querySelector("[data-stock-confirmed]");
  const statusEl = form.querySelector("[data-day-status]");
  const submitBtn = form.querySelector("[data-day-submit]");
  const verifyUrl = root.getAttribute("data-verify-login-url") || "";
  const mode = root.getAttribute("data-mode") || "open";

  let verified = false;
  let timer = null;
  let seq = 0;

  const getCsrf = () =>
    form.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

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

  form.addEventListener("submit", async (event) => {
    const missing = ["cash_amount", "mpesa_amount", "credit_amount"].filter(
      (name) => !(form.querySelector(`[name="${name}"]`)?.value || "").trim()
    );
    if (missing.length) {
      event.preventDefault();
      const label =
        missing[0] === "cash_amount"
          ? "cash"
          : missing[0] === "mpesa_amount"
            ? "M-Pesa"
            : "credit";
      setStatus(`Enter the ${label} balance.`, { error: true });
      form.querySelector(`[name="${missing[0]}"]`)?.focus();
      return;
    }
    if (!stockInput?.checked) {
      event.preventDefault();
      setStatus("Confirm that stock is up to date first.", { error: true });
      return;
    }
    if (!verified) {
      event.preventDefault();
      const ok = await verifyCode();
      if (!ok) return;
    }
    if (submitBtn) submitBtn.disabled = true;
  });

  syncSubmit();
  if ((codeInput?.value || "").trim().length === 6) {
    verifyCode();
  }

  const drawingForm = root.querySelector("[data-shop-day-drawing-form]");
  if (drawingForm) {
    const drawingCode = drawingForm.querySelector("[data-drawing-login-code]");
    const drawingAmount = drawingForm.querySelector("[data-drawing-amount]");
    const drawingStatus = drawingForm.querySelector("[data-drawing-status]");
    const drawingSubmit = drawingForm.querySelector("[data-drawing-submit]");
    let drawingVerified = false;
    let drawingTimer = null;
    let drawingSeq = 0;

    const setDrawingStatus = (message, { ok = false, error = false } = {}) => {
      if (!drawingStatus) return;
      drawingStatus.textContent =
        message ||
        "Enter an active staff member’s 6-digit ID to authorise the drawing.";
      drawingStatus.classList.toggle("is-ok", ok);
      drawingStatus.classList.toggle("is-error", error);
    };

    const syncDrawingSubmit = () => {
      const amountOk = Boolean((drawingAmount?.value || "").trim());
      if (drawingSubmit) drawingSubmit.disabled = !(drawingVerified && amountOk);
    };

    const verifyDrawingCode = async () => {
      const code = (drawingCode?.value || "").trim();
      const current = ++drawingSeq;
      if (code.length < 6) {
        drawingVerified = false;
        setDrawingStatus(
          code.length
            ? `Enter ${6 - code.length} more digit${6 - code.length === 1 ? "" : "s"}.`
            : ""
        );
        syncDrawingSubmit();
        return false;
      }
      if (!/^\d{6}$/.test(code)) {
        drawingVerified = false;
        setDrawingStatus("Staff ID must be exactly 6 digits.", { error: true });
        syncDrawingSubmit();
        return false;
      }
      if (!verifyUrl) {
        drawingVerified = false;
        setDrawingStatus("Verification is unavailable. Refresh and try again.", {
          error: true,
        });
        syncDrawingSubmit();
        return false;
      }
      try {
        const body = new URLSearchParams({ login_code: code });
        const response = await fetch(verifyUrl, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-CSRFToken": getCsrf(),
          },
          credentials: "same-origin",
          body,
        });
        const data = await response.json().catch(() => ({}));
        if (current !== drawingSeq) return false;
        if (!response.ok || !data.ok) {
          drawingVerified = false;
          setDrawingStatus(data.error || "Not a valid active staff ID.", {
            error: true,
          });
          syncDrawingSubmit();
          return false;
        }
        drawingVerified = true;
        setDrawingStatus(
          `Verified: ${data.name || "staff"} (${data.employee_id || code}).`,
          { ok: true }
        );
        syncDrawingSubmit();
        return true;
      } catch (_) {
        if (current !== drawingSeq) return false;
        drawingVerified = false;
        setDrawingStatus("Could not verify staff ID. Try again.", {
          error: true,
        });
        syncDrawingSubmit();
        return false;
      }
    };

    drawingCode?.addEventListener("input", () => {
      drawingVerified = false;
      syncDrawingSubmit();
      window.clearTimeout(drawingTimer);
      drawingTimer = window.setTimeout(() => {
        verifyDrawingCode();
      }, 220);
    });
    drawingCode?.addEventListener("blur", () => {
      verifyDrawingCode();
    });
    drawingAmount?.addEventListener("input", syncDrawingSubmit);

    drawingForm.addEventListener("submit", async (event) => {
      if (!(drawingAmount?.value || "").trim()) {
        event.preventDefault();
        setDrawingStatus("Enter the drawing amount.", { error: true });
        drawingAmount?.focus();
        return;
      }
      if (!drawingVerified) {
        event.preventDefault();
        const ok = await verifyDrawingCode();
        if (!ok) return;
      }
      if (drawingSubmit) drawingSubmit.disabled = true;
    });

    syncDrawingSubmit();
    if ((drawingCode?.value || "").trim().length === 6) {
      verifyDrawingCode();
    }
  }

  if (window.lucide?.createIcons) window.lucide.createIcons();
})();
