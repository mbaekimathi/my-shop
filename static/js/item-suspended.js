(function () {
  "use strict";

  document.querySelectorAll("[data-confirm-delete]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const name = form.dataset.itemName || "this item";
      if (!window.confirm(`Delete “${name}”? This cannot be undone.`)) {
        event.preventDefault();
      }
    });
  });
})();
