(() => {
  const root = document.querySelector("[data-wa-contacts]");
  if (!root) return;

  const contactSearch = root.querySelector("[data-wa-contact-search]");
  const groupSearch = root.querySelector("[data-wa-group-search]");
  const contactEmpty = root.querySelector("[data-wa-contact-empty]");
  const contactNone = root.querySelector("[data-wa-contact-none]");
  const groupEmpty = root.querySelector("[data-wa-group-empty]");
  const groupNone = root.querySelector("[data-wa-group-none]");

  const filterRows = (rows, query, emptyEl, noneEl, hasAny) => {
    const needle = (query || "").trim().toLowerCase();
    let visible = 0;
    rows.forEach((row) => {
      const hay = row.getAttribute("data-search") || "";
      const match = !needle || hay.includes(needle);
      row.hidden = !match;
      if (match) visible += 1;
    });
    if (emptyEl) emptyEl.hidden = hasAny || Boolean(needle);
    if (noneEl) noneEl.hidden = !hasAny || visible > 0 || !needle;
  };

  const contactRows = [...root.querySelectorAll("[data-wa-contact-row]")];
  const groupRows = [...root.querySelectorAll("[data-wa-group-row]")];

  contactSearch?.addEventListener("input", () => {
    filterRows(
      contactRows,
      contactSearch.value,
      contactEmpty,
      contactNone,
      contactRows.length > 0
    );
  });
  groupSearch?.addEventListener("input", () => {
    filterRows(
      groupRows,
      groupSearch.value,
      groupEmpty,
      groupNone,
      groupRows.length > 0
    );
  });

  const openModal = (name) => {
    const modal = document.querySelector(`[data-modal="${name}"]`);
    if (!modal) return;
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("workspace-modal-open");
    if (window.lucide?.createIcons) window.lucide.createIcons();
    const focusEl = modal.querySelector("input:not([type=hidden]), textarea, select");
    window.setTimeout(() => focusEl?.focus(), 40);
  };

  const closeModal = (name) => {
    const modal = document.querySelector(`[data-modal="${name}"]`);
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
  };

  ["add-contact", "create-group", "join-group"].forEach((name) => {
    document.querySelectorAll(`[data-modal-open="${name}"]`).forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        openModal(name);
      });
    });
    document.querySelectorAll(`[data-modal-close="${name}"]`).forEach((el) => {
      el.addEventListener("click", () => closeModal(name));
    });
  });

  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    ["add-contact", "create-group", "join-group"].forEach(closeModal);
  });

  document.querySelectorAll("[data-wa-member-search]").forEach((input) => {
    const box = input.closest(".field")?.querySelector("[data-wa-member-pick]");
    if (!box) return;
    const rows = [...box.querySelectorAll("[data-search]")];
    input.addEventListener("input", () => {
      const needle = input.value.trim().toLowerCase();
      rows.forEach((row) => {
        const hay = row.getAttribute("data-search") || "";
        row.hidden = Boolean(needle) && !hay.includes(needle);
      });
    });
  });
})();
