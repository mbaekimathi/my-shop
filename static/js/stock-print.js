/**
 * Print stock popup — choose items / prices / stock, then open printable sheet.
 */
(function () {
  "use strict";

  const modal = document.querySelector("[data-modal='print-stock']");
  if (!modal) return;

  const form = modal.querySelector("[data-stock-print-form]");
  const printUrl = modal.getAttribute("data-stock-print-url") || "";
  const shopsPanel = modal.querySelector("[data-stock-print-shops]");
  const statusEl = modal.querySelector("[data-stock-print-status]");
  const shopSelect = document.querySelector("[data-stock-shop-nav]");

  const setStatus = (message, { error = false } = {}) => {
    if (!statusEl) return;
    statusEl.hidden = !message;
    statusEl.textContent = message || "";
    statusEl.classList.toggle("is-error", Boolean(error));
  };

  const selectedLayout = () =>
    form?.querySelector('input[name="print_layout"]:checked')?.value || "items";

  const syncShopsVisibility = () => {
    const layout = selectedLayout();
    const needsShops = layout === "prices" || layout === "stock";
    if (shopsPanel) shopsPanel.hidden = !needsShops;
  };

  const preselectShopsFromPage = () => {
    const current = (shopSelect?.value || "").trim();
    const boxes = [...(modal.querySelectorAll('[data-stock-print-shop]') || [])];
    if (!boxes.length) return;
    if (current) {
      boxes.forEach((box) => {
        box.checked = String(box.value) === current;
      });
      return;
    }
    // All shops page: leave none checked so user chooses deliberately for price/stock.
    boxes.forEach((box) => {
      box.checked = false;
    });
  };

  const openModal = () => {
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("workspace-modal-open");
    preselectShopsFromPage();
    syncShopsVisibility();
    setStatus("");
    try {
      window.lucide?.createIcons?.();
    } catch (_) {
      /* ignore */
    }
    window.setTimeout(() => {
      form?.querySelector('input[name="print_layout"]:checked')?.focus();
    }, 40);
  };

  const closeModal = () => {
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
    setStatus("");
  };

  document.querySelectorAll('[data-modal-open="print-stock"]').forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      openModal();
    });
  });

  modal.querySelectorAll("[data-modal-close], [data-stock-print-close]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.preventDefault();
      closeModal();
    });
  });

  form?.addEventListener("change", (event) => {
    if (event.target?.name === "print_layout") syncShopsVisibility();
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });

  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!printUrl) {
      setStatus("Print URL is missing.", { error: true });
      return;
    }

    const layout = selectedLayout();
    const params = new URLSearchParams();
    params.set("layout", layout);
    params.set("auto", "1");

    if (layout === "prices" || layout === "stock") {
      const shopIds = [...modal.querySelectorAll("[data-stock-print-shop]:checked")].map(
        (el) => el.value
      );
      if (!shopIds.length) {
        setStatus("Select at least one shop.", { error: true });
        return;
      }
      shopIds.forEach((id) => params.append("shop_id", id));
    }

    const url = `${printUrl}?${params.toString()}`;
    const popup = window.open(url, "stock-print", "noopener,noreferrer,width=920,height=720");
    if (!popup) {
      setStatus("Allow pop-ups to print stock.", { error: true });
      return;
    }
    closeModal();
  });
})();
