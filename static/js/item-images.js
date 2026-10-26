(() => {
  const form = document.querySelector("[data-item-images-form]");
  if (!form) return;

  const fileInput = form.querySelector("[data-image-input]");
  if (fileInput?.disabled) return;

  const previewWrap = form.querySelector("[data-image-preview-wrap]");
  const previewImg = form.querySelector("[data-image-preview]");
  const nameEl = form.querySelector("[data-image-name]");
  const submitBtn = form.querySelector("[data-image-submit]");
  const countEl = form.querySelector("[data-selected-count]");
  const scopeRadios = [...form.querySelectorAll("[data-scope-radio]")];
  const categorySelect = form.querySelector("[data-category-select]");

  let previewUrl = "";

  const checks = () => [...form.querySelectorAll("[data-item-check]")];
  const visibleChecks = () =>
    checks().filter((box) => !box.closest("[data-item-row]")?.hidden);
  const scope = () =>
    scopeRadios.find((radio) => radio.checked)?.value || "items";
  const selectedCount = () => checks().filter((box) => box.checked).length;

  const syncGroupToggles = () => {
    form.querySelectorAll("[data-group-check]").forEach((btn) => {
      const group = btn.closest("[data-item-category-group]");
      const groupChecks = [
        ...(group?.querySelectorAll("[data-item-check]") || []),
      ].filter((box) => !box.closest("[data-item-row]")?.hidden);
      btn.textContent =
        groupChecks.length && groupChecks.every((box) => box.checked)
          ? "Clear"
          : "Select all";
    });
  };

  const sync = () => {
    const isCategory = scope() === "category";
    form.classList.toggle("is-scope-category", isCategory);
    if (categorySelect) categorySelect.disabled = !isCategory;

    const count = selectedCount();
    if (countEl) {
      countEl.textContent = isCategory
        ? "Whole category"
        : `${count} selected`;
    }
    if (submitBtn) {
      const ready = isCategory
        ? Boolean(categorySelect?.value)
        : count > 0;
      submitBtn.disabled = !(fileInput?.files?.length && ready);
    }
    syncGroupToggles();
  };

  fileInput?.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = "";
    }
    if (file) {
      previewUrl = URL.createObjectURL(file);
      if (previewImg) previewImg.src = previewUrl;
      if (previewWrap) previewWrap.hidden = false;
      if (nameEl) nameEl.textContent = file.name;
    } else {
      if (previewImg) previewImg.removeAttribute("src");
      if (previewWrap) previewWrap.hidden = true;
      if (nameEl) nameEl.textContent = "Choose an image";
    }
    sync();
  });

  form.addEventListener("change", (event) => {
    if (
      event.target.matches(
        "[data-item-check], [data-scope-radio], [data-category-select]"
      )
    ) {
      sync();
    }
  });

  form
    .querySelector("[data-check-visible]")
    ?.addEventListener("click", () => {
      visibleChecks().forEach((box) => {
        box.checked = true;
      });
      sync();
    });

  form
    .querySelector("[data-clear-checks]")
    ?.addEventListener("click", () => {
      checks().forEach((box) => {
        box.checked = false;
      });
      sync();
    });

  form.querySelectorAll("[data-group-check]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const group = btn.closest("[data-item-category-group]");
      const groupChecks = [
        ...(group?.querySelectorAll("[data-item-check]") || []),
      ].filter((box) => !box.closest("[data-item-row]")?.hidden);
      const allChecked =
        groupChecks.length > 0 && groupChecks.every((box) => box.checked);
      groupChecks.forEach((box) => {
        box.checked = !allChecked;
      });
      sync();
    });
  });

  // Keep the "Select all" labels honest as search hides/shows rows.
  new MutationObserver(syncGroupToggles).observe(form, {
    attributes: true,
    subtree: true,
    attributeFilter: ["hidden"],
  });

  sync();
})();
