(() => {
  const form = document.querySelector("[data-company-profile-form]");
  if (!form) return;

  const bindLogoUpload = (root) => {
    const input = root.querySelector("[data-logo-input]");
    const image = root.querySelector("[data-logo-image]");
    const placeholder = root.querySelector("[data-logo-placeholder]");
    const removeBtn = root.querySelector("[data-logo-remove]");

    input?.addEventListener("change", () => {
      const file = input.files?.[0];
      if (!file || !image) return;
      const url = URL.createObjectURL(file);
      image.src = url;
      image.hidden = false;
      image.alt = "Selected image";
      if (placeholder) placeholder.hidden = true;
      if (removeBtn) removeBtn.checked = false;
    });

    removeBtn?.closest("label")?.addEventListener("click", () => {
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
  };

  form.querySelectorAll("[data-logo-upload]").forEach(bindLogoUpload);
})();
