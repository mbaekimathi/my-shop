/**
 * Cashiers report day-session "Edit session" popup — a chooser step asks
 * what to correct (opening float, counted closing, or an expense), then a
 * plain POST form shows just that editor. This file only handles opening /
 * closing the modal and switching steps.
 */
(function () {
  "use strict";

  const modal = document.querySelector("[data-modal='edit-day-session']");
  if (!modal) return;

  const steps = modal.querySelectorAll("[data-edit-step]");
  const showStep = (name) => {
    steps.forEach((step) => {
      step.hidden = step.dataset.editStep !== name;
    });
    if (name !== "choose") {
      modal
        .querySelector(`[data-edit-step='${name}'] input:not([type='hidden'])`)
        ?.focus();
    }
  };

  const openModal = () => {
    showStep("choose");
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("workspace-modal-open");
    try {
      window.lucide?.createIcons?.();
    } catch (_) {
      /* ignore */
    }
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

  modal.querySelectorAll("[data-edit-goto]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      showStep(trigger.dataset.editGoto);
    });
  });

  modal.querySelectorAll("[data-edit-back]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      showStep("choose");
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
