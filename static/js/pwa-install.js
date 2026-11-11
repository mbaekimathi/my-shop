(() => {
  const DISMISS_KEY = "myshop-pwa-install-dismissed";
  const DISMISS_MS = 1000 * 60 * 60 * 24 * 14; // 14 days

  const roots = Array.from(document.querySelectorAll("[data-pwa-install]"));
  const ctaButtons = Array.from(document.querySelectorAll("[data-pwa-install-cta]"));
  if (!roots.length && !ctaButtons.length) return;

  const isStandalone = () => {
    if (window.matchMedia("(display-mode: standalone)").matches) return true;
    if (window.matchMedia("(display-mode: minimal-ui)").matches) return true;
    if (window.navigator.standalone === true) return true;
    if (document.referrer.startsWith("android-app://")) return true;
    return false;
  };

  const isIos = () =>
    /iphone|ipad|ipod/i.test(window.navigator.userAgent) ||
    (window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1);

  const isDismissed = () => {
    try {
      const raw = localStorage.getItem(DISMISS_KEY);
      if (!raw) return false;
      const until = Number(raw);
      if (!Number.isFinite(until)) return false;
      return Date.now() < until;
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

  const hideBanners = () => {
    roots.forEach((root) => {
      root.hidden = true;
      root.setAttribute("aria-hidden", "true");
      root.classList.remove("is-visible");
    });
  };

  const hideCtas = () => {
    ctaButtons.forEach((btn) => {
      btn.hidden = true;
    });
  };

  const hideAll = () => {
    hideBanners();
    hideCtas();
  };

  const showBanners = () => {
    roots.forEach((root) => {
      root.hidden = false;
      root.setAttribute("aria-hidden", "false");
      requestAnimationFrame(() => root.classList.add("is-visible"));
    });
  };

  const showCtas = () => {
    ctaButtons.forEach((btn) => {
      btn.hidden = false;
    });
  };

  if (isStandalone()) {
    hideAll();
    return;
  }

  let deferredPrompt = null;
  let canInstallNative = false;
  let alreadyInstalled = false;

  const syncBannerButtons = () => {
    roots.forEach((root) => {
      const installBtn = root.querySelector("[data-pwa-install-btn]");
      const iosHint = root.querySelector("[data-pwa-ios-hint]");
      if (installBtn) {
        installBtn.hidden = isIos() && !canInstallNative;
      }
      if (iosHint) {
        iosHint.hidden = !(isIos() && !canInstallNative);
      }
    });
  };

  const promptInstall = async (fromRoot) => {
    if (deferredPrompt) {
      try {
        deferredPrompt.prompt();
        const choice = await deferredPrompt.userChoice;
        deferredPrompt = null;
        canInstallNative = false;
        if (choice?.outcome === "accepted") {
          hideAll();
        } else {
          setDismissed();
          hideBanners();
        }
      } catch (_err) {
        /* ignore prompt failures */
      }
      return;
    }

    const root =
      fromRoot ||
      roots.find((node) => !node.hidden) ||
      roots[0];
    const iosHint = root?.querySelector("[data-pwa-ios-hint]");
    if (iosHint) {
      showBanners();
      iosHint.hidden = false;
      iosHint.classList.add("is-pulse");
      window.setTimeout(() => iosHint.classList.remove("is-pulse"), 900);
      return;
    }

    // Desktop / unsupported: scroll banner into view if present.
    if (root) {
      showBanners();
      root.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  };

  const maybeShow = () => {
    if (alreadyInstalled || isStandalone()) {
      hideAll();
      return;
    }

    const eligible = canInstallNative || isIos();
    if (!eligible) {
      hideBanners();
      hideCtas();
      return;
    }

    syncBannerButtons();
    showCtas();
    if (!isDismissed()) {
      showBanners();
    } else {
      hideBanners();
    }
  };

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    canInstallNative = true;
    maybeShow();
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    canInstallNative = false;
    alreadyInstalled = true;
    hideAll();
  });

  roots.forEach((root) => {
    root.querySelectorAll("[data-pwa-dismiss]").forEach((btn) => {
      btn.addEventListener("click", () => {
        setDismissed();
        hideBanners();
      });
    });

    root.querySelectorAll("[data-pwa-install-btn]").forEach((btn) => {
      btn.addEventListener("click", () => {
        promptInstall(root);
      });
    });
  });

  ctaButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      promptInstall();
    });
  });

  if (navigator.getInstalledRelatedApps) {
    navigator
      .getInstalledRelatedApps()
      .then((apps) => {
        if (Array.isArray(apps) && apps.length) {
          alreadyInstalled = true;
          hideAll();
          return;
        }
        maybeShow();
      })
      .catch(() => maybeShow());
  } else {
    window.setTimeout(maybeShow, 700);
  }

  const mq = window.matchMedia("(display-mode: standalone)");
  const onMode = () => {
    if (isStandalone()) hideAll();
  };
  if (typeof mq.addEventListener === "function") {
    mq.addEventListener("change", onMode);
  } else if (typeof mq.addListener === "function") {
    mq.addListener(onMode);
  }
})();
