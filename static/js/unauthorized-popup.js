(() => {
  const AUTH_RE =
    /not authoris|not authoriz|do not have permission|don't have permission|cannot complete|cannot change|cannot (open|close|register|return|access)|access denied|forbidden|you are not allocated/i;
  const AUTO_MS = 7200;

  const popup = document.querySelector("[data-unauthorized-popup]");
  if (!popup) return;

  const detailEl = popup.querySelector("[data-unauthorized-detail]");
  const clockEl = popup.querySelector("[data-unauthorized-clock]");
  const progressEl = popup.querySelector("[data-unauthorized-progress]");
  const closeEls = popup.querySelectorAll("[data-unauthorized-close]");
  const dialog = popup.querySelector(".unauth-popup__dialog");

  let hideTimer = 0;
  let clockTimer = 0;
  let progressRaf = 0;
  let openedAt = 0;
  let lastFocus = null;

  const refreshIcons = () => {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const formatClock = () => {
    try {
      return new Intl.DateTimeFormat(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(new Date());
    } catch (_err) {
      return new Date().toTimeString().slice(0, 8);
    }
  };

  const tickClock = () => {
    if (clockEl) clockEl.textContent = formatClock();
  };

  const stopLive = () => {
    window.clearTimeout(hideTimer);
    window.clearInterval(clockTimer);
    if (progressRaf) window.cancelAnimationFrame(progressRaf);
    hideTimer = 0;
    clockTimer = 0;
    progressRaf = 0;
    if (progressEl) progressEl.style.transform = "scaleX(1)";
  };

  const runProgress = () => {
    if (!progressEl) return;
    const frame = () => {
      const elapsed = Date.now() - openedAt;
      const remaining = Math.max(0, 1 - elapsed / AUTO_MS);
      progressEl.style.transform = `scaleX(${remaining})`;
      if (remaining > 0 && !popup.hidden) {
        progressRaf = window.requestAnimationFrame(frame);
      }
    };
    progressRaf = window.requestAnimationFrame(frame);
  };

  const close = () => {
    if (popup.hidden) return;
    popup.classList.add("is-hiding");
    stopLive();
    window.setTimeout(() => {
      popup.hidden = true;
      popup.classList.remove("is-open", "is-hiding");
      popup.setAttribute("aria-hidden", "true");
      document.body.classList.remove("unauth-popup-open");
      if (lastFocus?.focus) lastFocus.focus();
    }, 280);
  };

  const show = (message) => {
    const text = String(message || "").trim() ||
      "You do not have permission to perform this action.";
    if (detailEl) detailEl.textContent = text;
    lastFocus = document.activeElement;
    stopLive();
    popup.hidden = false;
    popup.classList.remove("is-hiding");
    popup.classList.add("is-open");
    popup.setAttribute("aria-hidden", "false");
    document.body.classList.add("unauth-popup-open");
    if (dialog) {
      dialog.classList.remove("is-entering");
      void dialog.offsetWidth;
      dialog.classList.add("is-entering");
    }
    openedAt = Date.now();
    tickClock();
    clockTimer = window.setInterval(tickClock, 1000);
    runProgress();
    refreshIcons();
    popup.querySelector("[data-unauthorized-close].unauth-popup__action")?.focus();
    hideTimer = window.setTimeout(close, AUTO_MS);
  };

  const isUnauthorizedText = (text) => AUTH_RE.test(String(text || ""));

  closeEls.forEach((el) => {
    el.addEventListener("click", (event) => {
      event.preventDefault();
      close();
    });
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !popup.hidden) {
      event.preventDefault();
      close();
    }
  });

  const seeds = [...document.querySelectorAll("[data-unauthorized-seed]")];
  if (seeds.length) {
    show(seeds[0].textContent);
    seeds.forEach((el) => el.remove());
  }

  const toast = document.querySelector("[data-workspace-toast]");
  if (toast) {
    const items = [...toast.querySelectorAll(".workspace-toast__item")];
    const unauthorizedItems = items.filter((item) =>
      isUnauthorizedText(item.querySelector(".workspace-toast__text")?.textContent)
    );
    if (unauthorizedItems.length) {
      if (!seeds.length) {
        show(unauthorizedItems[0].querySelector(".workspace-toast__text")?.textContent);
      }
      unauthorizedItems.forEach((item) => item.remove());
      if (!toast.querySelector(".workspace-toast__item")) toast.remove();
    }
  }

  window.MyshopUnauthorized = {
    show,
    close,
    isUnauthorizedText,
  };
})();
