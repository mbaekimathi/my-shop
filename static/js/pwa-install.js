(() => {
  const DISMISS_KEY = "myshop-pwa-install-dismissed";
  const INSTALLED_KEY = "myshop-pwa-install-installed";
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

  const isInstalled = () => {
    try {
      return localStorage.getItem(INSTALLED_KEY) === "1";
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

  const setInstalled = () => {
    try {
      localStorage.setItem(INSTALLED_KEY, "1");
      localStorage.removeItem(DISMISS_KEY);
    } catch (_err) {
      /* ignore */
    }
  };

  const setOpen = (root, open) => {
    const panel = root.querySelector("[data-pwa-install-panel]");
    const toggleBtn = root.querySelector("[data-pwa-install-toggle]");
    root.classList.toggle("is-open", open);
    if (panel) panel.hidden = !open;
    if (toggleBtn) toggleBtn.setAttribute("aria-expanded", open ? "true" : "false");
  };

  const hideBanners = () => {
    roots.forEach((root) => {
      setOpen(root, false);
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

  const markInstalledAndHide = () => {
    setInstalled();
    alreadyInstalled = true;
    hideAll();
  };

  const showBanners = () => {
    roots.forEach((root) => {
      root.hidden = false;
      root.setAttribute("aria-hidden", "false");
      setOpen(root, false);
      requestAnimationFrame(() => root.classList.add("is-visible"));
    });
  };

  const showCtas = () => {
    ctaButtons.forEach((btn) => {
      btn.hidden = false;
    });
  };

  let deferredPrompt = null;
  let canInstallNative = false;
  let alreadyInstalled = isInstalled();

  if (isStandalone() || alreadyInstalled) {
    markInstalledAndHide();
    return;
  }

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
          markInstalledAndHide();
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
      setOpen(root, true);
      iosHint.hidden = false;
      iosHint.classList.add("is-pulse");
      window.setTimeout(() => iosHint.classList.remove("is-pulse"), 900);
      return;
    }

    // Desktop / unsupported: open the panel if present.
    if (root) {
      showBanners();
      setOpen(root, true);
    }
  };

  const maybeShow = () => {
    if (alreadyInstalled || isInstalled() || isStandalone()) {
      markInstalledAndHide();
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
    if (alreadyInstalled || isInstalled()) {
      markInstalledAndHide();
      return;
    }
    deferredPrompt = event;
    canInstallNative = true;
    maybeShow();
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    canInstallNative = false;
    markInstalledAndHide();
  });

  roots.forEach((root) => {
    root.querySelectorAll("[data-pwa-install-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        setOpen(root, !root.classList.contains("is-open"));
      });
    });

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
      const root = roots.find((node) => !node.hidden) || roots[0];
      if (root && deferredPrompt) {
        promptInstall(root);
        return;
      }
      if (root) {
        showBanners();
        setOpen(root, true);
        return;
      }
      promptInstall();
    });
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-pwa-install-cta]")) return;
    roots.forEach((root) => {
      if (root.hidden || !root.classList.contains("is-open")) return;
      if (root.contains(event.target)) return;
      setOpen(root, false);
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    roots.forEach((root) => {
      if (root.classList.contains("is-open")) setOpen(root, false);
    });
  });

  if (navigator.getInstalledRelatedApps) {
    navigator
      .getInstalledRelatedApps()
      .then((apps) => {
        if (Array.isArray(apps) && apps.length) {
          markInstalledAndHide();
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
    if (isStandalone()) markInstalledAndHide();
  };
  if (typeof mq.addEventListener === "function") {
    mq.addEventListener("change", onMode);
  } else if (typeof mq.addListener === "function") {
    mq.addListener(onMode);
  }
})();
