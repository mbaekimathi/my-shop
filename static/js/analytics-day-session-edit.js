/**
 * Cashiers report day-session "Edit session" popup — corrects the recorded
 * opening float and counted closing. Plain POST form; this file only handles
 * opening / closing the modal.
 */
(function () {
  "use strict";

  const modal = document.querySelector("[data-modal='edit-day-session']");
  if (!modal) return;

  const openModal = () => {
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("workspace-modal-open");
    try {
      window.lucide?.createIcons?.();
    } catch (_) {
      /* ignore */
    }
    window.setTimeout(() => {
      modal.querySelector("input")?.focus();
    }, 40);
  };

  const closeModal = () => {
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
  };

  document
    .querySelectorAll('[data-modal-open="edit-day-session"]')
    .forEach((trigger) => {
      trigger.addEventListener("click", (event) => {
        event.preventDefault();
        openModal();
      });
    });

  modal.querySelectorAll("[data-modal-close]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.preventDefault();
      closeModal();
    });
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });
})();
