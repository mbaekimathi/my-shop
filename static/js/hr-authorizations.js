(() => {
  const root = document.querySelector("[data-hr-auth-panel]");
  if (!root) return;

  const form = root.querySelector("[data-hr-auth-form]");
  const saveBtn = root.querySelector("[data-hr-auth-save]");
  const search = root.querySelector("[data-hr-auth-search]");
  const visibleEl = root.querySelector("[data-hr-auth-visible]");
  const emptyEl = root.querySelector("[data-hr-auth-no-results]");
  const cards = [...root.querySelectorAll("[data-hr-auth-card]")];

  const snapshot = (card) =>
    [...card.querySelectorAll(".hr-auth-chip-input:not(:disabled)")]
      .map((input) => `${input.value}:${input.checked ? "1" : "0"}`)
      .join("|");

  cards.forEach((card) => {
    card.dataset.initial = snapshot(card);
  });

  const countLabel = (n) => `${n} shop${n === 1 ? "" : "s"}`;

  const refreshCard = (card) => {
    const inputs = [...card.querySelectorAll(".hr-auth-chip-input")];
    let on = 0;
    inputs.forEach((input) => {
      const chip = input.closest(".hr-auth-chip");
      chip?.classList.toggle("is-on", input.checked);
      if (input.checked) on += 1;
    });
    const countEl = card.querySelector("[data-hr-auth-count]");
    if (countEl) countEl.textContent = countLabel(on);
    const dirty = snapshot(card) !== (card.dataset.initial || "");
    card.classList.toggle("is-dirty", dirty);
    return dirty;
  };

  const refreshSave = () => {
    const dirty = cards.some((card) => card.classList.contains("is-dirty"));
    if (saveBtn) saveBtn.disabled = !dirty;
  };

  const filterCards = () => {
    const q = (search?.value || "").trim().toLowerCase();
    let shown = 0;
    cards.forEach((card) => {
      const hay = (card.dataset.search || "").toLowerCase();
      const match = !q || hay.includes(q);
      card.hidden = !match;
      if (match) shown += 1;
    });
    if (visibleEl) {
      visibleEl.hidden = !q;
      visibleEl.textContent = `${shown} match${shown === 1 ? "" : "es"}`;
    }
    if (emptyEl) emptyEl.hidden = shown !== 0;
  };

  root.addEventListener("change", (event) => {
    const input = event.target.closest(".hr-auth-chip-input");
    if (!input) return;
    const card = input.closest("[data-hr-auth-card]");
    if (!card) return;
    refreshCard(card);
    refreshSave();
  });

  root.addEventListener("click", (event) => {
    const allBtn = event.target.closest("[data-hr-auth-all]");
    const noneBtn = event.target.closest("[data-hr-auth-none]");
    if (!allBtn && !noneBtn) return;
    event.preventDefault();
    const card = event.target.closest("[data-hr-auth-card]");
    if (!card) return;
    card.querySelectorAll(".hr-auth-chip-input:not(:disabled)").forEach((input) => {
      input.checked = Boolean(allBtn);
    });
    refreshCard(card);
    refreshSave();
  });

  search?.addEventListener("input", filterCards);

  form?.addEventListener("submit", () => {
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.classList.add("is-saving");
    }
  });

  cards.forEach((card) => refreshCard(card));
  refreshSave();
})();
