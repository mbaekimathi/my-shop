/*
 * Compulsory clock-in check widget.
 *
 * Loaded on workspace pages (portal + shop floor) when POS settings require
 * clock-in and an active employee session exists. Polls the clock status
 * endpoint; once the open entry passes the 6-hour mark it checks whether the
 * employee is still interacting:
 *   - interacting  → silently confirms and compliments them (top-right toast)
 *   - idle ≥ 30min → top-right confirm card with a countdown; unanswered,
 *                    the system clocks them out (distinct from a manual out)
 */
(function () {
  const root = document.querySelector("[data-clock-status-url]");
  if (!root) return;

  const urls = {
    status: root.dataset.clockStatusUrl,
    confirm: root.dataset.clockConfirmUrl,
    prompt: root.dataset.clockPromptUrl,
    autoOut: root.dataset.clockAutoOutUrl,
  };
  if (!urls.status || !urls.confirm || !urls.autoOut) return;

  const POLL_MS = 60 * 1000;
  const IDLE_LIMIT_MS = 30 * 60 * 1000;
  const PROMPT_GRACE_MS = 30 * 60 * 1000;

  let lastActivity = Date.now();
  let promptCard = null;
  let promptTimer = null;
  let promptDeadline = 0;
  let promptStamped = false;
  let checkDone = false;
  let autoOutNotified = false;

  const ACTIVITY_EVENTS = ["pointerdown", "keydown", "touchstart", "scroll", "click"];
  const markActivity = () => {
    lastActivity = Date.now();
  };
  ACTIVITY_EVENTS.forEach((name) =>
    window.addEventListener(name, markActivity, { capture: true, passive: true })
  );
  const idleMs = () => Date.now() - lastActivity;

  const csrfToken = () => {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  };

  const postJson = async (url) => {
    try {
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": csrfToken(),
          "X-Requested-With": "XMLHttpRequest",
          Accept: "application/json",
        },
      });
      if (!response.ok) return null;
      return await response.json().catch(() => null);
    } catch (_err) {
      return null;
    }
  };

  const stack = () => {
    let el = document.querySelector("[data-clock-toast-stack]");
    if (!el) {
      el = document.createElement("div");
      el.className = "clock-toast-stack";
      el.setAttribute("data-clock-toast-stack", "");
      document.body.appendChild(el);
    }
    return el;
  };

  const ENCOURAGEMENTS = [
    "Great work today — keep it up!",
    "You're doing an amazing job!",
    "Six hours strong — impressive hustle!",
    "Keep shining — your effort shows!",
    "Still going strong. Well done!",
  ];

  const toast = ({ emoji, title, text, tone }) => {
    const card = document.createElement("div");
    card.className = `clock-toast clock-toast--${tone || "success"}`;
    card.setAttribute("role", "status");
    card.innerHTML = `
      <span class="clock-toast__emoji" aria-hidden="true"></span>
      <div class="clock-toast__body">
        <strong class="clock-toast__title"></strong>
        <p class="clock-toast__text"></p>
      </div>`;
    card.querySelector(".clock-toast__emoji").textContent = emoji || "😊";
    card.querySelector(".clock-toast__title").textContent = title || "";
    card.querySelector(".clock-toast__text").textContent = text || "";
    stack().appendChild(card);
    window.setTimeout(() => {
      card.classList.add("is-hiding");
      window.setTimeout(() => card.remove(), 260);
    }, 8000);
  };

  const compliment = () => {
    const text =
      ENCOURAGEMENTS[Math.floor(Math.random() * ENCOURAGEMENTS.length)];
    toast({ emoji: "😊", title: "Keep it up!", text });
  };

  const autoOutToast = () => {
    toast({
      emoji: "👋",
      title: "Clocked out",
      text: "You were clocked out by the system after 30 minutes away.",
      tone: "warn",
    });
  };

  const dismissPrompt = () => {
    if (promptTimer) {
      window.clearInterval(promptTimer);
      promptTimer = null;
    }
    if (promptCard) {
      promptCard.remove();
      promptCard = null;
    }
  };

  const remainingLabel = () => {
    const left = Math.max(0, promptDeadline - Date.now());
    const minutes = Math.floor(left / 60000);
    const seconds = Math.floor((left % 60000) / 1000);
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
  };

  const showPrompt = () => {
    if (promptCard) return;
    promptDeadline = Date.now() + PROMPT_GRACE_MS;
    promptCard = document.createElement("div");
    promptCard.className = "clock-toast clock-toast--warn clock-prompt";
    promptCard.setAttribute("role", "alert");
    promptCard.innerHTML = `
      <span class="clock-toast__emoji" aria-hidden="true">⏰</span>
      <div class="clock-toast__body">
        <strong class="clock-toast__title">Still working?</strong>
        <p class="clock-toast__text">
          You've been away for a while. Confirm you're still clocked in —
          the system will clock you out in
          <strong data-clock-countdown></strong>.
        </p>
        <button type="button" class="clock-prompt__btn" data-clock-confirm>
          I'm still clocked in
        </button>
      </div>`;
    const countdownEl = promptCard.querySelector("[data-clock-countdown]");
    const updateCountdown = () => {
      if (countdownEl) countdownEl.textContent = remainingLabel();
    };
    updateCountdown();
    promptCard
      .querySelector("[data-clock-confirm]")
      .addEventListener("click", async () => {
        await postJson(urls.confirm);
        checkDone = true;
        dismissPrompt();
        compliment();
      });
    stack().appendChild(promptCard);

    if (!promptStamped && urls.prompt) {
      promptStamped = true;
      postJson(urls.prompt).then((data) => {
        const iso = data && data.prompt_deadline;
        const parsed = iso ? Date.parse(iso) : NaN;
        if (!Number.isNaN(parsed)) {
          promptDeadline = parsed;
          updateCountdown();
        }
      });
    }

    promptTimer = window.setInterval(async () => {
      updateCountdown();
      if (Date.now() >= promptDeadline) {
        dismissPrompt();
        const data = await postJson(urls.autoOut);
        if (data && data.auto_clocked_out) {
          autoOutNotified = true;
          autoOutToast();
        }
      }
    }, 1000);
  };

  const tick = async () => {
    let data;
    try {
      const sep = urls.status.includes("?") ? "&" : "?";
      const response = await fetch(
        `${urls.status}${sep}active=${idleMs() < IDLE_LIMIT_MS ? "1" : "0"}`,
        {
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
        }
      );
      if (!response.ok) return;
      data = await response.json().catch(() => null);
    } catch (_err) {
      return;
    }
    if (!data || data.ok === false) return;

    if (!data.clocked_in) {
      dismissPrompt();
      promptStamped = false;
      checkDone = false;
      if (data.auto_clocked_out && !autoOutNotified) {
        autoOutNotified = true;
        autoOutToast();
      }
      return;
    }
    autoOutNotified = false;

    if (data.needs_check) {
      if (idleMs() < IDLE_LIMIT_MS) {
        const hadPrompt = Boolean(promptCard);
        dismissPrompt();
        if (!checkDone) {
          checkDone = true;
          await postJson(urls.confirm);
          compliment();
        }
      } else {
        showPrompt();
      }
    }
  };

  tick();
  window.setInterval(tick, POLL_MS);
})();
