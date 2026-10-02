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
      (name) => {
        const input = form.querySelector(`[name="${name}"]`);
        if (!input) return false;
        return !(input.value || "").trim();
      }
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
    const drawerCode = drawingForm.querySelector("[data-drawing-drawer-code]");
    const drawingCashAmount = drawingForm.querySelector(
      "[data-drawing-cash-amount]"
    );
    const drawingMpesaAmount = drawingForm.querySelector(
      "[data-drawing-mpesa-amount]"
    );
    const drawingAmount = drawingForm.querySelector("[data-drawing-amount]");
    const drawingStatus = drawingForm.querySelector("[data-drawing-status]");
    const drawingSubmit = drawingForm.querySelector("[data-drawing-submit]");
    let staffVerified = false;
    let drawerVerified = false;
    let staffName = "";
    let drawerName = "";
    let staffEmployeeId = "";
    let drawerEmployeeId = "";
    let staffTimer = null;
    let drawerTimer = null;
    let staffSeq = 0;
    let drawerSeq = 0;

    const parseAmount = (input) => {
      const raw = (input?.value || "").trim();
      if (!raw) return 0;
      const value = Number(raw);
      return Number.isFinite(value) ? value : NaN;
    };

    const drawingTotal = () => {
      const parts = [];
      if (drawingCashAmount) parts.push(parseAmount(drawingCashAmount));
      if (drawingMpesaAmount) parts.push(parseAmount(drawingMpesaAmount));
      if (!parts.length && drawingAmount) {
        parts.push(parseAmount(drawingAmount));
      }
      if (parts.some((value) => Number.isNaN(value) || value < 0)) return NaN;
      return parts.reduce((sum, value) => sum + value, 0);
    };

    const amountOk = () => {
      const total = drawingTotal();
      return Number.isFinite(total) && total > 0;
    };

    const setDrawingStatus = (message, { ok = false, error = false } = {}) => {
      if (!drawingStatus) return;
      drawingStatus.textContent =
        message ||
        "Enter both 6-digit codes — employee and person drawing.";
      drawingStatus.classList.toggle("is-ok", ok);
      drawingStatus.classList.toggle("is-error", error);
    };

    const syncDrawingSubmit = () => {
      const codesDistinct =
        staffEmployeeId &&
        drawerEmployeeId &&
        staffEmployeeId !== drawerEmployeeId;
      if (drawingSubmit) {
        drawingSubmit.disabled = !(
          staffVerified &&
          drawerVerified &&
          codesDistinct &&
          amountOk()
        );
      }
    };

    const refreshDrawingStatus = () => {
      const staffPartial = (drawingCode?.value || "").trim();
      const drawerPartial = (drawerCode?.value || "").trim();
      if (
        staffVerified &&
        drawerVerified &&
        staffEmployeeId &&
        drawerEmployeeId &&
        staffEmployeeId === drawerEmployeeId
      ) {
        setDrawingStatus(
          "Employee code and person drawing code must be different.",
          { error: true }
        );
        syncDrawingSubmit();
        return;
      }
      if (staffVerified && drawerVerified) {
        const cash = drawingCashAmount ? parseAmount(drawingCashAmount) : 0;
        const mpesa = drawingMpesaAmount ? parseAmount(drawingMpesaAmount) : 0;
        let channelNote = "funds";
        if (cash > 0 && mpesa > 0) channelNote = "cash + M-Pesa";
        else if (cash > 0) channelNote = "cash";
        else if (mpesa > 0) channelNote = "M-Pesa";
        setDrawingStatus(
          `Ready: ${staffName || "employee"} releases · ${
            drawerName || "person drawing"
          } takes the ${channelNote}.`,
          { ok: true }
        );
        syncDrawingSubmit();
        return;
      }
      if (!staffPartial && !drawerPartial) {
        setDrawingStatus("");
        syncDrawingSubmit();
        return;
      }
      const waiting = [];
      if (!staffVerified) waiting.push("employee code");
      if (!drawerVerified) waiting.push("person drawing code");
      setDrawingStatus(`Verify ${waiting.join(" and ")}.`);
      syncDrawingSubmit();
    };

    const verifyOneCode = async (input, role) => {
      const code = (input?.value || "").trim();
      const current =
        role === "staff" ? ++staffSeq : ++drawerSeq;
      if (role === "staff") {
        staffVerified = false;
        staffName = "";
        staffEmployeeId = "";
      } else {
        drawerVerified = false;
        drawerName = "";
        drawerEmployeeId = "";
      }

      if (code.length < 6) {
        refreshDrawingStatus();
        if (code.length) {
          setDrawingStatus(
            `Enter ${6 - code.length} more digit${
              6 - code.length === 1 ? "" : "s"
            } for ${
              role === "staff" ? "employee code" : "person drawing code"
            }.`
          );
        }
        return false;
      }
      if (!/^\d{6}$/.test(code)) {
        setDrawingStatus(
          `${
            role === "staff" ? "Employee code" : "Person drawing code"
          } must be exactly 6 digits.`,
          { error: true }
        );
        syncDrawingSubmit();
        return false;
      }
      if (!verifyUrl) {
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
        if (current !== (role === "staff" ? staffSeq : drawerSeq)) {
          return false;
        }
        if (!response.ok || !data.ok) {
          setDrawingStatus(
            data.error ||
              `Not a valid active ${
                role === "staff" ? "employee" : "person drawing"
              } code.`,
            { error: true }
          );
          syncDrawingSubmit();
          return false;
        }
        if (role === "staff") {
          staffVerified = true;
          staffName = data.name || "staff";
          staffEmployeeId = data.employee_id || code;
        } else {
          drawerVerified = true;
          drawerName = data.name || "drawer";
          drawerEmployeeId = data.employee_id || code;
        }
        refreshDrawingStatus();
        return true;
      } catch (_) {
        if (current !== (role === "staff" ? staffSeq : drawerSeq)) {
          return false;
        }
        setDrawingStatus(
          `Could not verify ${
            role === "staff" ? "staff" : "drawer"
          } ID. Try again.`,
          { error: true }
        );
        syncDrawingSubmit();
        return false;
      }
    };

    const scheduleVerify = (input, role) => {
      if (role === "staff") {
        staffVerified = false;
        staffName = "";
        staffEmployeeId = "";
      } else {
        drawerVerified = false;
        drawerName = "";
        drawerEmployeeId = "";
      }
      syncDrawingSubmit();
      if (role === "staff") {
        window.clearTimeout(staffTimer);
        staffTimer = window.setTimeout(() => {
          verifyOneCode(input, role);
        }, 220);
      } else {
        window.clearTimeout(drawerTimer);
        drawerTimer = window.setTimeout(() => {
          verifyOneCode(input, role);
        }, 220);
      }
    };

    drawingCode?.addEventListener("input", () => {
      scheduleVerify(drawingCode, "staff");
    });
    drawingCode?.addEventListener("blur", () => {
      verifyOneCode(drawingCode, "staff");
    });
    drawerCode?.addEventListener("input", () => {
      scheduleVerify(drawerCode, "drawer");
    });
    drawerCode?.addEventListener("blur", () => {
      verifyOneCode(drawerCode, "drawer");
    });
    drawingCashAmount?.addEventListener("input", () => {
      syncDrawingSubmit();
      if (staffVerified && drawerVerified) refreshDrawingStatus();
    });
    drawingMpesaAmount?.addEventListener("input", () => {
      syncDrawingSubmit();
      if (staffVerified && drawerVerified) refreshDrawingStatus();
    });
    drawingAmount?.addEventListener("input", syncDrawingSubmit);

    drawingForm.addEventListener("submit", async (event) => {
      if (!amountOk()) {
        event.preventDefault();
        const message =
          drawingCashAmount && drawingMpesaAmount
            ? "Enter a cash and/or M-Pesa amount to draw."
            : drawingMpesaAmount
              ? "Enter the M-Pesa amount drawn from the counter."
              : "Enter the cash amount drawn from the counter.";
        setDrawingStatus(message, { error: true });
        (drawingCashAmount || drawingMpesaAmount || drawingAmount)?.focus();
        return;
      }
      if (!staffVerified) {
        event.preventDefault();
        const ok = await verifyOneCode(drawingCode, "staff");
        if (!ok) return;
      }
      if (!drawerVerified) {
        event.preventDefault();
        const ok = await verifyOneCode(drawerCode, "drawer");
        if (!ok) return;
      }
      if (
        staffEmployeeId &&
        drawerEmployeeId &&
        staffEmployeeId === drawerEmployeeId
      ) {
        event.preventDefault();
        setDrawingStatus(
          "Employee code and person drawing code must be different.",
          { error: true }
        );
        return;
      }
      if (drawingSubmit) drawingSubmit.disabled = true;
    });

    syncDrawingSubmit();
    if ((drawingCode?.value || "").trim().length === 6) {
      verifyOneCode(drawingCode, "staff");
    }
    if ((drawerCode?.value || "").trim().length === 6) {
      verifyOneCode(drawerCode, "drawer");
    }
  }

  if (window.lucide?.createIcons) window.lucide.createIcons();
})();
