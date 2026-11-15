/**
 * Shelf stock popup — register a shelf item from the stock-management sidebar.
 * Opens the [data-modal="shelf-stock"] dialog; when the modal is absent (other
 * pages) the sidebar button navigates to the view page with ?shelf=1.
 */
(function () {
  "use strict";

  const modal = document.querySelector("[data-modal='shelf-stock']");

  // No modal on this page: sidebar action carries a data-href to navigate.
  if (!modal) {
    document.querySelectorAll('[data-modal-open="shelf-stock"]').forEach((trigger) => {
      trigger.addEventListener("click", (event) => {
        const href = trigger.getAttribute("data-href");
        if (!href) return;
        event.preventDefault();
        window.location.assign(href);
      });
    });
    return;
  }

  const form = modal.querySelector("[data-shelf-register-form]");
  const shopBoxes = Array.from(
    modal.querySelectorAll('input[name="shelf_shop_id"]')
  );
  const pageShopSelect = document.querySelector("[data-stock-shop-nav]");

  const openModal = () => {
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("workspace-modal-open");
    const pageShop = pageShopSelect && pageShopSelect.value;
    if (pageShop && shopBoxes.some((box) => box.value === pageShop)) {
      shopBoxes.forEach((box) => {
        box.checked = box.value === pageShop;
      });
    } else if (!shopBoxes.some((box) => box.checked)) {
      shopBoxes.forEach((box) => {
        box.checked = true;
      });
    }
    try {
      window.lucide?.createIcons?.();
    } catch (_) {
      /* ignore */
    }
    window.setTimeout(() => {
      modal.querySelector('input[name="name"]')?.focus();
    }, 40);
  };

  const closeModal = () => {
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
  };

  document.querySelectorAll('[data-modal-open="shelf-stock"]').forEach((trigger) => {
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

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });

  // Opened via the sidebar action: land on the view page with ?shelf=1.
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.get("shelf") === "1") {
      openModal();
      params.delete("shelf");
      const query = params.toString();
      const next = `${window.location.pathname}${query ? `?${query}` : ""}${
        window.location.hash || ""
      }`;
      window.history.replaceState({}, "", next);
    }
  } catch (_err) {
    /* ignore */
  }

  // Shelf rows live inside the page's outer stock form (nested forms are not
  // allowed), so shelf_action buttons submit that form directly.
  const layout = document.querySelector("[data-stock-layout]");
  if (layout) {
    layout.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) return;
      const shelfPanel = target.closest("[data-shelf-stock]");
      if (!shelfPanel) {
        // Enter in search/filters must not submit a shelf row.
        event.preventDefault();
        return;
      }
      event.preventDefault();
      target
        .closest("tr")
        ?.querySelector('button[name="shelf_action"][value^="in:"]')
        ?.click();
    });

    layout.addEventListener("submit", (event) => {
      const submitter = event.submitter;
      if (!submitter || submitter.name !== "shelf_action") return;
      const question = submitter.getAttribute("data-shelf-confirm");
      if (question && !window.confirm(question)) {
        event.preventDefault();
      }
    });
  }
})();
