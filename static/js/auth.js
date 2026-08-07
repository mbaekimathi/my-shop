(() => {
  const refreshIcons = () => {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    const wrap = button.closest(".password-field");
    const input = wrap?.querySelector("input");
    const openIcon = button.querySelector("[data-eye-open]");
    const closedIcon = button.querySelector("[data-eye-closed]");
    if (!input) return;

    button.addEventListener("click", () => {
      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      button.setAttribute("aria-label", showing ? "Show password" : "Hide password");
      if (openIcon) openIcon.hidden = !showing;
      if (closedIcon) closedIcon.hidden = showing;
      refreshIcons();
    });
  });

  refreshIcons();
})();
