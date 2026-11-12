(() => {
  const DISMISS_KEY = "myshop-web-push-dismissed";
  const ENABLED_KEY = "myshop-web-push-enabled";
  const DISMISS_MS = 1000 * 60 * 60 * 24 * 7;

  const root = document.querySelector("[data-web-push]");
  const workspace = document.querySelector("[data-workspace][data-auth-required]");
  if (!workspace && !root) return;
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    if (root) root.hidden = true;
    return;
  }

  const vapidUrl =
    (root && root.getAttribute("data-vapid-url")) ||
    (workspace && workspace.getAttribute("data-web-push-vapid-url")) ||
    "/employees/api/web-push/vapid-public-key/";
  const subscribeUrl =
    (root && root.getAttribute("data-subscribe-url")) ||
    (workspace && workspace.getAttribute("data-web-push-subscribe-url")) ||
    "/employees/api/web-push/subscribe/";

  const csrfToken = () => {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  };

  const isDismissed = () => {
    try {
      const until = Number(localStorage.getItem(DISMISS_KEY) || "");
      return Number.isFinite(until) && Date.now() < until;
    } catch (_err) {
      return false;
    }
  };

  const isEnabled = () => {
    try {
      return localStorage.getItem(ENABLED_KEY) === "1";
    } catch (_err) {
      return false;
    }
  };

  const setDismissed = () => {
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now() + DISMISS_MS));
    } catch (_err) {
      /* ignore */
    }
  };

  const setEnabled = () => {
    try {
      localStorage.setItem(ENABLED_KEY, "1");
      localStorage.removeItem(DISMISS_KEY);
    } catch (_err) {
      /* ignore */
    }
  };

  const panel = root?.querySelector("[data-web-push-panel]");
  const toggleBtn = root?.querySelector("[data-web-push-toggle]");

  const setOpen = (open) => {
    if (!root) return;
    root.classList.toggle("is-open", open);
    if (panel) panel.hidden = !open;
    if (toggleBtn) toggleBtn.setAttribute("aria-expanded", open ? "true" : "false");
  };

  const hidePrompt = () => {
    if (!root) return;
    setOpen(false);
    root.hidden = true;
    root.classList.remove("is-visible");
  };

  const showPrompt = () => {
    if (!root || isDismissed() || isEnabled()) return;
    if (Notification.permission === "granted") return;
    if (Notification.permission === "denied") return;
    root.hidden = false;
    setOpen(false);
    requestAnimationFrame(() => root.classList.add("is-visible"));
  };

  const urlBase64ToUint8Array = (base64String) => {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = window.atob(base64);
    const output = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) output[i] = raw.charCodeAt(i);
    return output;
  };

  const fetchPublicKey = async () => {
    const response = await fetch(vapidUrl, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return "";
    const data = await response.json();
    return data?.ok && data.publicKey ? String(data.publicKey) : "";
  };

  const postSubscription = async (subscription) => {
    const json = subscription.toJSON();
    const response = await fetch(subscribeUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify({
        endpoint: json.endpoint,
        keys: json.keys || {},
      }),
    });
    if (!response.ok) return false;
    const data = await response.json().catch(() => ({}));
    return Boolean(data?.ok);
  };

  const markEnabledAndHide = () => {
    setEnabled();
    hidePrompt();
  };

  const ensureSubscription = async () => {
    if (Notification.permission !== "granted") return false;
    markEnabledAndHide();
    const publicKey = await fetchPublicKey();
    if (!publicKey) return false;

    const registration = await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    }
    return await postSubscription(subscription);
  };

  const enableAlerts = async () => {
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        if (permission === "denied") hidePrompt();
        return false;
      }
      markEnabledAndHide();
      return await ensureSubscription();
    } catch (_err) {
      return false;
    }
  };

  toggleBtn?.addEventListener("click", () => {
    setOpen(!root.classList.contains("is-open"));
  });

  root?.querySelectorAll("[data-web-push-enable]").forEach((btn) => {
    btn.addEventListener("click", () => {
      enableAlerts();
    });
  });

  root?.querySelectorAll("[data-web-push-dismiss]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setDismissed();
      hidePrompt();
    });
  });

  document.addEventListener("click", (event) => {
    if (!root || root.hidden || !root.classList.contains("is-open")) return;
    if (root.contains(event.target)) return;
    setOpen(false);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!root || !root.classList.contains("is-open")) return;
    setOpen(false);
  });

  // If already granted / previously enabled, keep the prompt hidden and refresh the subscription.
  if (Notification.permission === "granted" || isEnabled()) {
    markEnabledAndHide();
    if (Notification.permission === "granted") {
      navigator.serviceWorker.ready
        .then(() => ensureSubscription())
        .catch(() => {});
    }
  } else if (Notification.permission === "default") {
    window.setTimeout(showPrompt, 1200);
  } else {
    hidePrompt();
  }
})();
