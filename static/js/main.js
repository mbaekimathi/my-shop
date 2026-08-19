(() => {
  const THEME_KEY = "myshop-theme";

  const getTheme = () =>
    document.documentElement.getAttribute("data-theme") === "light"
      ? "light"
      : "dark";

  const setTheme = (theme) => {
    const next = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch (_err) {
      /* ignore storage failures */
    }
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) {
      themeColor.setAttribute("content", next === "light" ? "#eef4f2" : "#10282c");
    }
    const colorScheme = document.querySelector('meta[name="color-scheme"]');
    if (colorScheme) {
      colorScheme.setAttribute("content", next === "light" ? "light dark" : "dark light");
    }
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.setAttribute(
        "aria-label",
        next === "light" ? "Switch to dark mode" : "Switch to light mode"
      );
      btn.title = next === "light" ? "Dark mode" : "Light mode";
    });
  };

  document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setTheme(getTheme() === "dark" ? "light" : "dark");
      if (window.lucide?.createIcons) window.lucide.createIcons();
    });
  });

  setTheme(getTheme());

  const ready = () => document.body.classList.add("is-ready");

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ready, { once: true });
  } else {
    ready();
  }

  const initIcons = () => {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  };

  window.addEventListener("load", initIcons);
  setTimeout(initIcons, 50);
  setTimeout(initIcons, 300);

  const header = document.querySelector(".site-header");
  if (header && !header.classList.contains("site-header--solid")) {
    const onScroll = () => {
      header.classList.toggle("is-scrolled", window.scrollY > 24);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  const revealItems = document.querySelectorAll("[data-reveal]");
  if (revealItems.length && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.18, rootMargin: "0px 0px -8% 0px" }
    );
    revealItems.forEach((el) => observer.observe(el));
  } else {
    revealItems.forEach((el) => el.classList.add("is-visible"));
  }

  const mobileNav = document.querySelector("[data-mobile-nav]");
  const openBtn = document.querySelector("[data-nav-toggle]");
  const closeBtn = document.querySelector("[data-nav-close]");

  const setNav = (open) => {
    if (!mobileNav || !openBtn) return;
    mobileNav.hidden = !open;
    openBtn.setAttribute("aria-expanded", String(open));
    document.body.style.overflow = open ? "hidden" : "";
    initIcons();
  };

  openBtn?.addEventListener("click", () => setNav(true));
  closeBtn?.addEventListener("click", () => setNav(false));
  mobileNav?.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => setNav(false));
  });
})();
