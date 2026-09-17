(() => {
  const modal = document.querySelector("[data-shop-low-stock-modal]");
  if (!modal) return;

  const shopId = modal.getAttribute("data-shop-id") || "";
  const force = modal.getAttribute("data-force") === "1";
  const openSessionId = modal.getAttribute("data-open-session-id") || "";

  const dismissKey = () =>
    `shop-low-stock-dismissed:${shopId}:session:${openSessionId || "none"}`;

  const isDismissed = () => {
    if (!openSessionId) return false;
    try {
      return Boolean(sessionStorage.getItem(dismissKey()));
    } catch (_) {
      return false;
    }
  };

  const markDismissed = () => {
    if (!openSessionId) return;
    try {
      sessionStorage.setItem(dismissKey(), String(Date.now()));
    } catch (_) {
      /* ignore */
    }
  };

  const dayModalOpen = () => {
    const dayModal = document.querySelector("[data-shop-day-modal]");
    return Boolean(dayModal && !dayModal.hidden);
  };

  const setOpen = (open) => {
    modal.hidden = !open;
    modal.setAttribute("aria-hidden", open ? "false" : "true");
    document.body.classList.toggle("workspace-modal-open", open || dayModalOpen());
    if (open && window.lucide?.createIcons) window.lucide.createIcons();
  };

  const dismissModal = () => {
    markDismissed();
    setOpen(false);
  };

  document.querySelectorAll('[data-modal-close="shop-low-stock"]').forEach((el) => {
    el.addEventListener("click", dismissModal);
  });

  // Show only after a forced open (once per open session).
  if (force && !isDismissed() && !dayModalOpen()) {
    setOpen(true);
  }
})();
