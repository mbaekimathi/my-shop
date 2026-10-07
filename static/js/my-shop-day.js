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
  const tillRoot = root.querySelector("[data-till-live]");
  const closeCashInput = form.querySelector("[data-close-cash-amount]");
  const closeMpesaInput = form.querySelector("[data-close-mpesa-amount]");

  let verified = false;
  let timer = null;
  let seq = 0;
  let tillPollTimer = null;

  const getCsrf = () =>
    form.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const formatMoney = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "0";
    return String(Math.round(n));
  };

  const setText = (selector, value) => {
    const el = tillRoot?.querySelector(selector);
    if (el) el.textContent = formatMoney(value);
  };

  const setRefundRow = (rowSel, valueSel, value) => {
    const amount = Number(value) || 0;
    const row = tillRoot?.querySelector(rowSel);
    if (row) row.hidden = amount <= 0;
    setText(valueSel, amount);
  };

  const applyTill = (till) => {
    if (!till || !tillRoot) return;
    setText("[data-till-total]", till.expected_total);
    setText("[data-till-cash]", till.expected_cash);
    setText("[data-till-mpesa]", till.expected_mpesa);
    setText("[data-till-opening]", till.opening_total);
    setText("[data-till-cash-sales]", till.cash_sales);
    setText("[data-till-mpesa-sales]", till.mpesa_sales);
    setRefundRow(
      "[data-till-cash-refunds-row]",
      "[data-till-cash-refunds]",
      till.cash_refunds
    );
    setRefundRow(
      "[data-till-mpesa-refunds-row]",
      "[data-till-mpesa-refunds]",
      till.mpesa_refunds
    );
    setText("[data-till-expenses]", till.expenses_paid);
    setText("[data-till-drawings]", till.drawings_paid);
    setText("[data-till-drawings-cash]", till.drawings_cash);
    setText("[data-till-drawings-mpesa]", till.drawings_mpesa);
    setText("[data-till-suppliers]", till.suppliers_paid);

    // Keep close-form suggestions in sync when the user has not typed over them.
    if (
      closeCashInput &&
      (!closeCashInput.dataset.userEdited || closeCashInput.dataset.userEdited === "0")
    ) {
      closeCashInput.value = formatMoney(till.expected_cash);
    }
    if (
      closeMpesaInput &&
      (!closeMpesaInput.dataset.userEdited ||
        closeMpesaInput.dataset.userEdited === "0")
    ) {
      closeMpesaInput.value = formatMoney(till.expected_mpesa);
    }
  };

  const fetchTill = async () => {
    if (!tillRoot) return null;
    try {
      const response = await fetch(`${window.location.pathname}?format=json`, {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) return null;
      if (data.till) applyTill(data.till);
      return data.till || null;
    } catch (_) {
      return null;
    }
  };

  closeCashInput?.addEventListener("input", () => {
    closeCashInput.dataset.userEdited = "1";
  });
  closeMpesaInput?.addEventListener("input", () => {
    closeMpesaInput.dataset.userEdited = "1";
  });

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
    const missing = ["cash_amount", "mpesa_amount"].filter((name) => {
      const input = form.querySelector(`[name="${name}"]`);
      if (!input) return false;
      return !(input.value || "").trim();
    });
    if (missing.length) {
      event.preventDefault();
      const label = missing[0] === "cash_amount" ? "cash" : "M-Pesa";
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
      event.preventDefault();
      if (!amountOk()) {
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
        const ok = await verifyOneCode(drawingCode, "staff");
        if (!ok) return;
      }
      if (!drawerVerified) {
        const ok = await verifyOneCode(drawerCode, "drawer");
        if (!ok) return;
      }
      if (
        staffEmployeeId &&
        drawerEmployeeId &&
        staffEmployeeId === drawerEmployeeId
      ) {
        setDrawingStatus(
          "Employee code and person drawing code must be different.",
          { error: true }
        );
        return;
      }
      if (drawingSubmit) drawingSubmit.disabled = true;
      setDrawingStatus("Recording drawing…");
      try {
        const body = new FormData(drawingForm);
        const response = await fetch(window.location.pathname, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": getCsrf(),
          },
          credentials: "same-origin",
          body,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          const err =
            (Array.isArray(data.errors) && data.errors[0]) ||
            data.error ||
            "Could not record drawing.";
          setDrawingStatus(err, { error: true });
          syncDrawingSubmit();
          return;
        }
        if (data.till) applyTill(data.till);
        if (drawingCashAmount) drawingCashAmount.value = "";
        if (drawingMpesaAmount) drawingMpesaAmount.value = "";
        if (drawingAmount) drawingAmount.value = "";
        if (drawingCode) drawingCode.value = "";
        if (drawerCode) drawerCode.value = "";
        staffVerified = false;
        drawerVerified = false;
        staffName = "";
        drawerName = "";
        staffEmployeeId = "";
        drawerEmployeeId = "";
        setDrawingStatus(data.message || "Drawing recorded. Till updated.", {
          ok: true,
        });
        syncDrawingSubmit();
      } catch (_) {
        setDrawingStatus("Could not record drawing. Try again.", {
          error: true,
        });
        syncDrawingSubmit();
      }
    });

    syncDrawingSubmit();
    if ((drawingCode?.value || "").trim().length === 6) {
      verifyOneCode(drawingCode, "staff");
    }
    if ((drawerCode?.value || "").trim().length === 6) {
      verifyOneCode(drawerCode, "drawer");
    }
  }

  if (tillRoot) {
    fetchTill();
    tillPollTimer = window.setInterval(fetchTill, 12000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") fetchTill();
    });
    window.addEventListener("beforeunload", () => {
      if (tillPollTimer) window.clearInterval(tillPollTimer);
    });
  }

  if (window.lucide?.createIcons) window.lucide.createIcons();
})();
