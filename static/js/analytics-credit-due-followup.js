(() => {
  const modal = document.querySelector("[data-ax-credit-due-modal]");
  if (!modal) return;

  const dueDate = (modal.getAttribute("data-due-date") || "").trim();
  if (!dueDate) return;

  const storageKey = `credit-due-followup-dismiss:${dueDate}`;
  const closeEls = modal.querySelectorAll(
    "[data-ax-credit-due-close], [data-ax-credit-due-dismiss]"
  );
  const dismissBtn = modal.querySelector("[data-ax-credit-due-dismiss]");

  const isDismissed = () => {
    try {
      return localStorage.getItem(storageKey) === "1";
    } catch (_error) {
      return false;
    }
  };

  const dismissToday = () => {
    try {
      localStorage.setItem(storageKey, "1");
    } catch (_error) {
      // ignore quota / private mode
    }
  };

  const syncBody = (open) => {
    document.body.classList.toggle("ax-credit-due-open", open);
  };

  const close = () => {
    modal.hidden = true;
    syncBody(false);
  };

  const open = () => {
    if (isDismissed()) return;
    modal.hidden = false;
    syncBody(true);
    window.lucide?.createIcons?.();
    dismissBtn?.focus();
  };

  closeEls.forEach((el) => {
    el.addEventListener("click", () => {
      if (el.hasAttribute("data-ax-credit-due-dismiss")) {
        dismissToday();
      }
      close();
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) {
      close();
    }
  });

  // Show after paint so the credits page is ready.
  window.setTimeout(open, 350);
})();
