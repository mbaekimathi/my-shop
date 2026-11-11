(() => {
  const root = document.querySelector("[data-rider-alerts]");
  if (!root) return;

  const statusUrl = root.getAttribute("data-status-url") || "";
  const homeUrl = root.getAttribute("data-home-url") || "/rider/";
  if (!statusUrl) return;

  const STORAGE_KEY = "myshop.rider.openDeliveryIds";
  const AUDIO_UNLOCK_KEY = "myshop.rider.audioUnlocked";
  const POLL_MS = 8000;
  const POLL_HIDDEN_MS = 20000;

  let knownIds = [];
  let seeded = false;
  let pollTimer = null;
  let audioCtx = null;
  let audioUnlocked = false;

  try {
    audioUnlocked = sessionStorage.getItem(AUDIO_UNLOCK_KEY) === "1";
  } catch {
    /* ignore */
  }

  const readKnown = () => {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      return [];
    }
  };

  const writeKnown = (ids) => {
    knownIds = ids.map(String);
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(knownIds));
    } catch {
      /* ignore quota */
    }
  };

  const markAudioUnlocked = () => {
    audioUnlocked = true;
    try {
      sessionStorage.setItem(AUDIO_UNLOCK_KEY, "1");
    } catch {
      /* ignore */
    }
  };

  const ensureAudio = () => {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!audioCtx) audioCtx = new Ctx();
    if (audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => {});
    }
    return audioCtx;
  };

  const tone = (ctx, { freq, start, duration, gain = 0.08 }) => {
    const osc = ctx.createOscillator();
    const amp = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    amp.gain.setValueAtTime(0.0001, start);
    amp.gain.exponentialRampToValueAtTime(gain, start + 0.02);
    amp.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    osc.connect(amp);
    amp.connect(ctx.destination);
    osc.start(start);
    osc.stop(start + duration + 0.02);
  };

  const playDeliveryChime = ({ assigned = false } = {}) => {
    const ctx = ensureAudio();
    if (!ctx || !audioUnlocked) return;
    const now = ctx.currentTime;
    if (assigned) {
      tone(ctx, { freq: 880, start: now, duration: 0.16, gain: 0.1 });
      tone(ctx, { freq: 1175, start: now + 0.14, duration: 0.18, gain: 0.1 });
      tone(ctx, { freq: 1397, start: now + 0.3, duration: 0.22, gain: 0.09 });
    } else {
      tone(ctx, { freq: 740, start: now, duration: 0.18, gain: 0.09 });
      tone(ctx, { freq: 988, start: now + 0.16, duration: 0.22, gain: 0.09 });
    }
  };

  const unlockAudio = () => {
    const ctx = ensureAudio();
    if (!ctx) return;
    markAudioUnlocked();
    // Quiet tick so mobile browsers keep the context live.
    tone(ctx, { freq: 440, start: ctx.currentTime, duration: 0.03, gain: 0.0008 });
  };

  ["pointerdown", "keydown", "touchstart"].forEach((eventName) => {
    window.addEventListener(
      eventName,
      () => {
        if (!audioUnlocked) unlockAudio();
      },
      { passive: true, once: false }
    );
  });

  if (audioUnlocked) {
    // Resume context after a soft reload when already unlocked this session.
    window.setTimeout(() => ensureAudio(), 200);
  }

  const notifyBrowser = (title, body) => {
    if (!("Notification" in window)) return;
    if (Notification.permission === "granted") {
      try {
        new Notification(title, {
          body,
          tag: "rider-delivery-alert",
          renotify: true,
        });
      } catch {
        /* ignore */
      }
    } else if (Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  };

  const pushToast = (message) => {
    const host =
      document.querySelector("[data-workspace-toast]") ||
      document.querySelector(".workspace-toast");
    if (!host) return;
    let list = host.querySelector(".message-list, .workspace-messages");
    if (!list) {
      list = document.createElement("ul");
      list.className = "message-list workspace-messages";
      host.appendChild(list);
    }
    const li = document.createElement("li");
    li.className = "workspace-toast__item workspace-toast__item--success";
    li.setAttribute("data-toast-duration", "4500");
    li.innerHTML = `<span class="workspace-toast__text"></span>`;
    li.querySelector(".workspace-toast__text").textContent = message;
    list.appendChild(li);
    window.setTimeout(() => li.remove(), 4500);
  };

  const onNewDeliveries = (rows) => {
    const assigned = rows.some((row) => row.assigned);
    const sample = rows[0] || {};
    const shop = sample.shop || "a shop";
    const count = rows.length;
    let message;
    if (assigned && count === 1) {
      message = `You were allocated a delivery from ${shop}.`;
    } else if (assigned) {
      message = `${count} deliveries were allocated to you, including from ${shop}.`;
    } else if (count === 1) {
      message = `New open delivery from ${shop}.`;
    } else {
      message = `${count} new open deliveries, including from ${shop}.`;
    }

    playDeliveryChime({ assigned });
    notifyBrowser(assigned ? "Delivery allocated" : "New delivery", message);
    pushToast(message);

    const onHome =
      window.location.pathname === "/rider/" ||
      window.location.pathname === "/rider";
    if (onHome) {
      window.setTimeout(() => {
        window.location.reload();
      }, 700);
    }
  };

  const poll = async () => {
    try {
      const response = await fetch(statusUrl, {
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
      });
      if (response.status === 401 || response.status === 403) {
        stopPolling();
        return;
      }
      if (!response.ok) return;
      const data = await response.json();
      if (!data?.ok) return;
      const open = Array.isArray(data.open) ? data.open : [];
      const ids = open.map((row) => String(row.id));

      if (!seeded) {
        writeKnown(ids.length ? ids : readKnown());
        seeded = true;
        return;
      }

      const fresh = open.filter((row) => !knownIds.includes(String(row.id)));
      writeKnown(ids);
      if (fresh.length) onNewDeliveries(fresh);
    } catch {
      /* ignore transient poll errors */
    }
  };

  const schedule = () => {
    if (pollTimer) window.clearInterval(pollTimer);
    const ms = document.hidden ? POLL_HIDDEN_MS : POLL_MS;
    pollTimer = window.setInterval(poll, ms);
  };

  const stopPolling = () => {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  document.addEventListener("visibilitychange", () => {
    schedule();
    if (!document.hidden) poll();
  });

  knownIds = readKnown();
  schedule();
  window.setTimeout(poll, 900);

  // Expose for debugging on rider devices.
  root.dataset.riderAlertsReady = "1";
  root.dataset.homeUrl = homeUrl;
})();
