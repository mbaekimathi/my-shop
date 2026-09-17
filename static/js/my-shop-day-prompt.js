(() => {
  const modal = document.querySelector("[data-shop-day-modal]");
  if (!modal) return;

  const toggleUrl = modal.getAttribute("data-day-toggle-url") || "";
  const redirectOnly = modal.getAttribute("data-redirect-only") === "1";
  const compulsory = modal.getAttribute("data-compulsory") === "1";

  const syncBodyLock = () => {
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
  };

  const setOpen = (open) => {
    modal.hidden = !open;
    modal.setAttribute("aria-hidden", open ? "false" : "true");
    syncBodyLock();
    if (open && window.lucide?.createIcons) window.lucide.createIcons();
  };

  const goToDayPage = () => {
    if (!toggleUrl) return;
    window.location.assign(toggleUrl);
  };

  modal.querySelectorAll("[data-shop-day-go]").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (!toggleUrl) return;
      event.preventDefault();
      goToDayPage();
    });
  });

  // Compulsory: no dismiss / Escape — staff must open or close on /day/.
  if (compulsory) {
    modal.querySelectorAll('[data-modal-close="shop-day"]').forEach((el) => {
      el.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        goToDayPage();
      });
    });
    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || modal.hidden) return;
      event.preventDefault();
      goToDayPage();
    });
  }

  if (modal.getAttribute("data-auto-open") === "1") {
    setOpen(true);
    // Redirect-only prompts should land on the day page quickly.
    if (redirectOnly && toggleUrl) {
      window.setTimeout(() => {
        if (!modal.hidden) goToDayPage();
      }, 1200);
    }
  } else {
    setOpen(false);
  }
})();
