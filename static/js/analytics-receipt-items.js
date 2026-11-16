/**
 * Analytics receipt items popup — eye icon in the Items column opens a
 * small modal listing the receipt's items (name, qty, price, total).
 */
(function () {
  "use strict";

  const modal = document.querySelector("[data-ax-items-modal]");
  if (!modal) return;

  const docEl = modal.querySelector("[data-ax-items-doc]");
  const tableEl = modal.querySelector("[data-ax-items-table]");
  const listEl = modal.querySelector("[data-ax-items-list]");
  const emptyEl = modal.querySelector("[data-ax-items-empty]");

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
      modal.querySelector(".workspace-icon-btn")?.focus?.();
    }, 40);
  };

  const closeModal = () => {
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
  };

  const renderItems = (items) => {
    const list = Array.isArray(items) ? items : [];
    if (emptyEl) emptyEl.hidden = list.length > 0;
    if (tableEl) tableEl.hidden = list.length === 0;
    if (!listEl) return;
    listEl.innerHTML = "";
    list.forEach((item) => {
      const tr = document.createElement("tr");
      [
        item?.name || "Item",
        item?.qty || "—",
        item?.unit || "—",
        item?.total || "—",
      ].forEach((text) => {
        const td = document.createElement("td");
        td.textContent = text;
        tr.appendChild(td);
      });
      listEl.appendChild(tr);
    });
  };

  document.querySelectorAll("[data-ax-items-open]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      let items = [];
      try {
        items = JSON.parse(trigger.getAttribute("data-items") || "[]");
      } catch (_) {
        items = [];
      }
      if (docEl) {
        docEl.textContent = trigger.getAttribute("data-doc") || "Items";
      }
      renderItems(items);
      openModal();
    });
  });

  modal.querySelectorAll("[data-ax-items-close]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.preventDefault();
      closeModal();
    });
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });
})();
