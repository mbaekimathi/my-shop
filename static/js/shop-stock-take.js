(() => {
  const modal = document.querySelector("[data-stock-take-modal]");
  if (!modal) return;

  const payloadEl = document.getElementById("stock-take-payload");
  let payload = { lines: [] };
  try {
    payload = JSON.parse(payloadEl?.textContent || "{}");
  } catch (_error) {
    payload = { lines: [] };
  }

  const form = modal.querySelector("[data-stock-take-submit-form]");
  const entryNodes = modal.querySelectorAll("[data-stock-take-entry]");
  const onePanel = modal.querySelector("[data-stock-take-one]");
  const allPanel = modal.querySelector("[data-stock-take-all]");
  const resultPanel = modal.querySelector("[data-stock-take-result]");
  const resultList = modal.querySelector("[data-stock-take-result-list]");
  const errorEl = modal.querySelector("[data-stock-take-error]");
  const submitBtn = modal.querySelector("[data-stock-take-submit]");
  const countdownEl = modal.querySelector("[data-stock-take-countdown]");
  const url = modal.getAttribute("data-stock-take-submit-url") || "";
  const assignmentId =
    modal.getAttribute("data-assignment-id") ||
    String(payload.assignment_id || "");
  const deadlineRaw = modal.getAttribute("data-deadline") || "";
  const storageKey = `stock-take-snooze:${assignmentId}`;
  const prefKey = "stock-take-snooze-pref";
  const modePrefKey = "stock-take-mode-pref";
  const amountInput = modal.querySelector("[data-stock-take-snooze-amount]");
  const unitSelect = modal.querySelector("[data-stock-take-snooze-unit]");
  const oneQty = modal.querySelector("[data-stock-take-one-qty]");
  const oneSubmit = modal.querySelector("[data-stock-take-one-submit]");
  const oneNext = modal.querySelector("[data-stock-take-one-next]");
  const oneCard = modal.querySelector("[data-stock-take-one-card]");
  const oneFlash = modal.querySelector("[data-stock-take-one-flash]");
  const deadlineAt = deadlineRaw ? Date.parse(deadlineRaw) : NaN;
  let completed = false;
  let countdownTimer = 0;
  let mode = "one";
  let oneIndex = 0;
  let awaitingNext = false;
  const lines = Array.isArray(payload.lines) ? payload.lines.slice() : [];
  const MIN_SNOOZE_MS = 60 * 1000;
  const MAX_SNOOZE_MS = 48 * 60 * 60 * 1000;

  const ICON_OK =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
  const ICON_BAD =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';

  const readCookie = (name) => {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) {
      return decodeURIComponent(parts.pop().split(";").shift() || "");
    }
    return "";
  };

  const csrf =
    form?.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    readCookie("csrftoken") ||
    "";

  const showError = (message) => {
    if (!errorEl) return;
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
  };

  const pad = (value) => String(value).padStart(2, "0");

  const formatRemaining = (ms) => {
    const totalSec = Math.max(0, Math.floor(ms / 1000));
    const days = Math.floor(totalSec / 86400);
    const hours = Math.floor((totalSec % 86400) / 3600);
    const minutes = Math.floor((totalSec % 3600) / 60);
    const seconds = totalSec % 60;
    if (days > 0) {
      return `${days}d ${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
    }
    return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
  };

  const syncCountdown = () => {
    if (!countdownEl || !Number.isFinite(deadlineAt) || completed) return;
    const remaining = deadlineAt - Date.now();
    if (remaining <= 0) {
      countdownEl.textContent = "Deadline passed — submit as soon as you can";
      countdownEl.classList.add("is-overdue");
      return;
    }
    countdownEl.classList.remove("is-overdue");
    countdownEl.textContent = `Time left ${formatRemaining(remaining)}`;
  };

  const snoozeUntil = () => {
    try {
      const raw = localStorage.getItem(storageKey);
      const until = Number(raw || 0);
      return Number.isFinite(until) ? until : 0;
    } catch (_error) {
      return 0;
    }
  };

  const readPref = () => {
    try {
      const raw = localStorage.getItem(prefKey);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (_error) {
      return null;
    }
  };

  const savePref = (amount, unit) => {
    try {
      localStorage.setItem(prefKey, JSON.stringify({ amount, unit }));
    } catch (_error) {
      // ignore
    }
  };

  const applyPref = () => {
    const pref = readPref();
    if (!pref || !amountInput || !unitSelect) return;
    const amount = Math.max(1, Math.min(999, Number(pref.amount) || 3));
    const unit = pref.unit === "minutes" ? "minutes" : "hours";
    amountInput.value = String(amount);
    unitSelect.value = unit;
  };

  const resolveSnoozeMs = () => {
    const amount = Math.max(1, Math.min(999, Number(amountInput?.value) || 0));
    const unit = unitSelect?.value === "minutes" ? "minutes" : "hours";
    if (!amount) {
      showError("Enter how long to wait before the reminder.");
      amountInput?.focus();
      return null;
    }
    const ms =
      unit === "minutes" ? amount * 60 * 1000 : amount * 60 * 60 * 1000;
    if (ms < MIN_SNOOZE_MS) {
      showError("Reminder must be at least 1 minute.");
      amountInput?.focus();
      return null;
    }
    if (ms > MAX_SNOOZE_MS) {
      showError("Reminder can be at most 48 hours.");
      amountInput?.focus();
      return null;
    }
    savePref(amount, unit);
    showError("");
    return ms;
  };

  const setSnooze = (ms) => {
    const until = Date.now() + ms;
    try {
      localStorage.setItem(storageKey, String(until));
    } catch (_error) {
      // ignore
    }
    return until;
  };

  const clearSnooze = () => {
    try {
      localStorage.removeItem(storageKey);
    } catch (_error) {
      // ignore
    }
  };

  let wakeTimer = 0;
  const shouldShowNow = () => Date.now() >= snoozeUntil();

  const openModal = () => {
    modal.hidden = false;
    syncCountdown();
  };

  const scheduleWake = () => {
    if (wakeTimer) window.clearTimeout(wakeTimer);
    const wakeIn = Math.max(0, snoozeUntil() - Date.now());
    wakeTimer = window.setTimeout(() => {
      if (!completed && shouldShowNow()) openModal();
    }, Math.min(wakeIn + 50, MAX_SNOOZE_MS));
  };

  const dismissModal = () => {
    if (completed) {
      modal.hidden = true;
      modal.remove();
      return;
    }
    const ms = resolveSnoozeMs();
    if (ms == null) return;
    setSnooze(ms);
    modal.hidden = true;
    scheduleWake();
  };

  applyPref();

  [amountInput, unitSelect].forEach((el) => {
    el?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      dismissModal();
    });
  });

  const unansweredIndexes = () =>
    lines
      .map((line, index) => ({ line, index }))
      .filter(({ line }) => !line.answered)
      .map(({ index }) => index);

  const currentOneIndex = () => {
    const open = unansweredIndexes();
    if (!open.length) return lines.length ? lines.length - 1 : 0;
    return open[0];
  };

  const setMode = (next) => {
    mode = next === "all" ? "all" : "one";
    try {
      localStorage.setItem(modePrefKey, mode);
    } catch (_error) {
      // ignore
    }
    modal.querySelectorAll("[data-stock-take-mode]").forEach((btn) => {
      const active = btn.getAttribute("data-stock-take-mode") === mode;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (onePanel) onePanel.hidden = mode !== "one";
    if (allPanel) allPanel.hidden = mode !== "all";
    showError("");
    if (mode === "one") renderOneItem();
  };

  const renderOneItem = () => {
    if (!lines.length) return;
    oneIndex = currentOneIndex();
    const line = lines[oneIndex];
    const answered = lines.filter((row) => row.answered).length;
    const total = lines.length;
    const progress = modal.querySelector("[data-stock-take-one-progress]");
    const bar = modal.querySelector("[data-stock-take-one-bar]");
    const nameEl = modal.querySelector("[data-stock-take-one-name]");
    const catEl = modal.querySelector("[data-stock-take-one-category]");

    if (progress) {
      progress.textContent = line.answered
        ? `Done ${answered} of ${total}`
        : `Item ${Math.min(answered + 1, total)} of ${total}`;
    }
    if (bar) {
      bar.style.width = `${total ? Math.round((100 * answered) / total) : 0}%`;
    }
    if (nameEl) nameEl.textContent = line.name || "Item";
    if (catEl) catEl.textContent = line.category || "Uncategorised";

    awaitingNext = false;
    if (oneCard) oneCard.hidden = Boolean(line.answered && unansweredIndexes().length === 0);
    if (oneFlash) oneFlash.hidden = true;
    if (oneNext) oneNext.hidden = true;
    if (oneQty) {
      oneQty.value = "";
      oneQty.disabled = Boolean(line.answered && unansweredIndexes().length === 0);
      oneQty.focus();
    }
    if (oneSubmit) {
      oneSubmit.disabled = false;
      oneSubmit.textContent = "Check this item";
    }

    if (!unansweredIndexes().length && lines.every((row) => row.answered)) {
      // All already answered from a previous session — request final isn't needed;
      // show a prompt to open score via all-mode or keep waiting. Prefer fetching
      // nothing; user can use all-at-once if somehow stuck. Normally complete
      // assignment would not be pending.
    }
  };

  const animateNumber = (el, target, duration = 900) => {
    if (!el) return;
    const end = Number(target) || 0;
    const start = 0;
    const began = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - began) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = String(Math.round(start + (end - start) * eased));
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };

  const hideEntry = () => {
    entryNodes.forEach((node) => node.setAttribute("hidden", ""));
    modal
      .querySelectorAll("button[data-stock-take-dismiss]")
      .forEach((el) => el.setAttribute("hidden", ""));
  };

  const showResults = (result) => {
    if (!resultPanel || !result) return;
    completed = true;
    clearSnooze();
    if (countdownTimer) window.clearInterval(countdownTimer);
    if (wakeTimer) window.clearTimeout(wakeTimer);

    hideEntry();
    resultPanel.hidden = false;
    modal.classList.add("is-result");
    modal.classList.add(`is-grade-${result.grade || "fair"}`);

    const marksEl = modal.querySelector("[data-stock-take-score-marks]");
    const maxEl = modal.querySelector("[data-stock-take-score-max]");
    const ring = modal.querySelector("[data-stock-take-score-ring]");
    const gradeLabel = modal.querySelector("[data-stock-take-grade-label]");
    const headline = modal.querySelector("[data-stock-take-headline]");
    const summary = modal.querySelector("[data-stock-take-summary]");
    const correctEl = modal.querySelector("[data-stock-take-stat-correct]");
    const wrongEl = modal.querySelector("[data-stock-take-stat-wrong]");
    const percentEl = modal.querySelector("[data-stock-take-stat-percent]");

    if (maxEl) maxEl.textContent = `/ ${result.max_marks || 0}`;
    if (gradeLabel) gradeLabel.textContent = result.grade_label || "Marks";
    if (headline) headline.textContent = result.headline || "Results";
    if (summary) {
      summary.textContent = `${result.marks || 0} of ${
        result.max_marks || 0
      } items counted correctly.`;
    }
    if (ring) ring.style.setProperty("--score", String(result.percent || 0));
    animateNumber(marksEl, result.marks || 0);
    animateNumber(correctEl, result.correct ?? result.marks ?? 0, 700);
    animateNumber(wrongEl, result.wrong || 0, 700);
    if (percentEl) {
      const end = Number(result.percent) || 0;
      const began = performance.now();
      const tick = (now) => {
        const t = Math.min(1, (now - began) / 900);
        const eased = 1 - Math.pow(1 - t, 3);
        percentEl.textContent = `${Math.round(end * eased)}%`;
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }

    if (resultList) {
      resultList.innerHTML = "";
      (result.lines || []).forEach((line, index) => {
        const li = document.createElement("li");
        li.className = `stock-take-result-row ${
          line.match ? "is-match" : "is-miss"
        }`;
        li.style.setProperty("--delay", `${0.08 + index * 0.06}s`);
        li.innerHTML = `
          <span class="stock-take-result-icon">${
            line.match ? ICON_OK : ICON_BAD
          }</span>
          <span class="stock-take-result-item">
            <strong>${line.name || "Item"}</strong>
            <em>${line.category || "Uncategorised"} · ${
              line.match ? "Correct" : "Wrong"
            }</em>
          </span>
          <span class="stock-take-result-figures">
            <span><i>You</i><b>${line.counted_qty}</b></span>
          </span>
        `;
        resultList.appendChild(li);
      });
    }
  };

  const showOneFlash = (line, { complete, marksSoFar, total }) => {
    if (!oneFlash) return;
    awaitingNext = !complete;
    oneCard.hidden = true;
    oneFlash.hidden = false;
    oneFlash.classList.toggle("is-match", Boolean(line.match));
    oneFlash.classList.toggle("is-miss", !line.match);
    const icon = modal.querySelector("[data-stock-take-one-flash-icon]");
    const title = modal.querySelector("[data-stock-take-one-flash-title]");
    const detail = modal.querySelector("[data-stock-take-one-flash-detail]");
    if (icon) icon.innerHTML = line.match ? ICON_OK : ICON_BAD;
    if (title) {
      title.textContent = line.match ? "Correct!" : "Not a match";
    }
    if (detail) {
      detail.textContent = complete
        ? `Final marks ${marksSoFar}/${total}`
        : `Your count ${line.counted_qty} · Marks so far ${marksSoFar}/${total}`;
    }
    if (oneNext) {
      oneNext.hidden = complete;
      oneNext.textContent = unansweredIndexes().length ? "Next item" : "See score";
    }
  };

  const postForm = async (body) => {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrf,
      },
      body,
      credentials: "same-origin",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Could not submit stock take.");
    }
    return data;
  };

  modal.querySelectorAll("[data-stock-take-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (completed || awaitingNext) return;
      setMode(btn.getAttribute("data-stock-take-mode"));
    });
  });

  oneSubmit?.addEventListener("click", async () => {
    if (!url || completed || awaitingNext) return;
    const line = lines[currentOneIndex()];
    if (!line || line.answered) return;
    const qty = (oneQty?.value || "").trim();
    if (qty === "") {
      showError("Enter a quantity for this item.");
      oneQty?.focus();
      return;
    }
    showError("");
    oneSubmit.disabled = true;
    oneSubmit.textContent = "Checking…";
    try {
      const body = new URLSearchParams({
        assignment_id: assignmentId,
        submit_mode: "one",
        count_id: String(line.count_id),
        qty,
        csrfmiddlewaretoken: csrf,
      });
      const data = await postForm(body);
      line.answered = true;
      line.counted_qty = data.line?.counted_qty || qty;
      const marksSoFar = data.marks_so_far || 0;
      const total = data.total_count || lines.length;
      showOneFlash(data.line || line, {
        complete: Boolean(data.complete),
        marksSoFar,
        total,
      });
      if (data.complete && data.result) {
        window.setTimeout(() => showResults(data.result), 700);
      }
    } catch (error) {
      showError(error.message || "Could not check this item.");
      oneSubmit.disabled = false;
      oneSubmit.textContent = "Check this item";
    }
  });

  oneQty?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    oneSubmit?.click();
  });

  oneNext?.addEventListener("click", () => {
    if (completed) return;
    awaitingNext = false;
    renderOneItem();
  });

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!url || completed) return;
    showError("");
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Checking…";
    }
    try {
      const body = new URLSearchParams(new FormData(form));
      body.set("submit_mode", "all");
      const data = await postForm(body);
      showResults(data.result || {});
    } catch (error) {
      showError(error.message || "Could not submit stock take.");
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Submit all";
      }
    }
  });

  modal.querySelectorAll("[data-stock-take-dismiss]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.preventDefault();
      dismissModal();
    });
  });

  modal.querySelector("[data-stock-take-done]")?.addEventListener("click", () => {
    dismissModal();
  });

  let preferredMode = "one";
  try {
    preferredMode = localStorage.getItem(modePrefKey) || "one";
  } catch (_error) {
    preferredMode = "one";
  }
  setMode(preferredMode);

  syncCountdown();
  countdownTimer = window.setInterval(syncCountdown, 1000);

  const forceOpen = modal.getAttribute("data-stock-take-force-open") === "1";
  if (forceOpen || shouldShowNow()) {
    openModal();
  } else {
    modal.hidden = true;
    scheduleWake();
  }
})();
