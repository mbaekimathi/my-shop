(() => {
  const modal = document.querySelector("[data-shop-low-stock-modal]");
  if (!modal) return;

  const shopId = modal.getAttribute("data-shop-id") || "";
  const force = modal.getAttribute("data-force") === "1";
  const sellingPage = modal.getAttribute("data-selling-page") === "1";

  const dismissKey = () => {
    const today = new Date().toISOString().slice(0, 10);
    return `shop-low-stock-dismissed:${shopId}:${today}`;
  };

  const isDismissed = () => {
    try {
      return Boolean(sessionStorage.getItem(dismissKey()));
    } catch (_) {
      return false;
    }
  };

  const markDismissed = () => {
    try {
      sessionStorage.setItem(dismissKey(), String(Date.now()));
    } catch (_) {
      /* ignore */
    }
  };

  const clearDismissed = () => {
    try {
      sessionStorage.removeItem(dismissKey());
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

  if (force) {
    clearDismissed();
    setOpen(true);
    return;
  }

  if (sellingPage && !isDismissed() && !dayModalOpen()) {
    setOpen(true);
  }
})();
