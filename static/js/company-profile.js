(() => {
  const form = document.querySelector("[data-company-profile-form]");
  if (!form) return;

  const input = form.querySelector("[data-logo-input]");
  const image = form.querySelector("[data-logo-image]");
  const placeholder = form.querySelector("[data-logo-placeholder]");
  const removeBtn = form.querySelector("[data-logo-remove]");

  input?.addEventListener("change", () => {
    const file = input.files?.[0];
    if (!file || !image) return;
    const url = URL.createObjectURL(file);
    image.src = url;
    image.hidden = false;
    image.alt = "Selected company logo";
    if (placeholder) placeholder.hidden = true;
    if (removeBtn) removeBtn.checked = false;
  });

  removeBtn?.closest("label")?.addEventListener("click", (event) => {
    // Let the checkbox toggle, then clear the preview.
    requestAnimationFrame(() => {
      if (!removeBtn.checked) return;
      if (input) input.value = "";
      if (image) {
        image.hidden = true;
        image.removeAttribute("src");
        image.alt = "";
      }
      if (placeholder) placeholder.hidden = false;
      if (window.lucide?.createIcons) window.lucide.createIcons();
    });
  });
})();
