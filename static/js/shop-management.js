(() => {
  const body = document.body;

  const setModalOpen = (modal, open) => {
    if (!modal) return;
    modal.hidden = !open;
    modal.setAttribute("aria-hidden", open ? "false" : "true");
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
    if (open) {
      const firstField = modal.querySelector("input:not([type=hidden]):not([type=password]), textarea, select");
      firstField?.focus();
      if (window.initUppercaseInputs) window.initUppercaseInputs(modal);
      if (window.lucide?.createIcons) window.lucide.createIcons();
    }
  };

  const bindPasswordMatch = (form) => {
    if (!form || form.dataset.passwordMatchBound === "true") return;
    const password = form.querySelector("[data-password]");
    const passwordConfirm = form.querySelector("[data-password-confirm]");
    const matchStatus = form.querySelector("[data-password-match]");
    if (!password || !passwordConfirm) return;

    form.dataset.passwordMatchBound = "true";

    const setMatchStatus = (state, message) => {
      if (!matchStatus) return;
      matchStatus.textContent = message || "";
      matchStatus.className = "field-hint";
      if (state === "ok") matchStatus.classList.add("field-hint--ok");
      if (state === "error") matchStatus.classList.add("field-hint--error");
    };

    const checkPasswordMatch = () => {
      const a = password.value || "";
      const b = passwordConfirm.value || "";

      if (!a && !b) {
        setMatchStatus("", "");
        password.classList.remove("is-invalid");
        passwordConfirm.classList.remove("is-invalid");
        return true;
      }

      if (a && a.length < 6) {
        setMatchStatus("error", "Password must be at least 6 characters.");
        return false;
      }

      if (b && a !== b) {
        setMatchStatus("error", "Password and confirm password do not match.");
        password.classList.add("is-invalid");
        passwordConfirm.classList.add("is-invalid");
        return false;
      }

      if (a && b && a === b) {
        setMatchStatus("ok", "Passwords match.");
        password.classList.remove("is-invalid");
        passwordConfirm.classList.remove("is-invalid");
        return true;
      }

      setMatchStatus("", "");
      return true;
    };

    password.addEventListener("input", checkPasswordMatch);
    passwordConfirm.addEventListener("input", checkPasswordMatch);
  };

  document.querySelectorAll(".workspace-modal-form").forEach(bindPasswordMatch);

  document.querySelectorAll(".workspace-modal").forEach((modal) => {
    const name = modal.dataset.modal;
    if (!name) return;

    document.querySelectorAll(`[data-modal-open="${name}"]`).forEach((trigger) => {
      trigger.addEventListener("click", () => {
        if (name === "register-shop") {
          modal.querySelectorAll('input[type="password"]').forEach((input) => {
            input.value = "";
          });
          const matchStatus = modal.querySelector("[data-password-match]");
          if (matchStatus) {
            matchStatus.textContent = "";
            matchStatus.className = "field-hint";
          }
        }
        setModalOpen(modal, true);
      });
    });

    modal.querySelectorAll("[data-modal-close]").forEach((el) => {
      el.addEventListener("click", () => setModalOpen(modal, false));
    });

    if (!modal.hidden) body.classList.add("workspace-modal-open");
  });

  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".workspace-modal:not([hidden])").forEach((modal) => {
      setModalOpen(modal, false);
    });
  });

  const editModal = document.querySelector('[data-modal="edit-shop"]');
  const editForm = editModal?.querySelector("[data-edit-form]");
  const shopIdInput = editForm?.querySelector("[data-edit-shop-id]");
  const imageWrap = editForm?.querySelector("[data-current-image-wrap]");
  const currentImage = editForm?.querySelector("[data-current-image]");
  const removeImage = editForm?.querySelector("[data-remove-image]");

  const setField = (name, value) => {
    const field = editForm?.querySelector(`[name="${name}"]`);
    if (field) field.value = value ?? "";
  };

  document.querySelectorAll("[data-edit-shop]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!editForm) return;

      const dataset = button.dataset;
      if (shopIdInput) shopIdInput.value = dataset.shopId || "";
      setField("name", dataset.name);
      setField("location", dataset.location);
      setField("email", dataset.email);
      setField("phone_number", dataset.phoneNumber);
      setField("login_code", dataset.loginCode);
      setField("password", "");
      setField("password_confirm", "");

      const matchStatus = editForm.querySelector("[data-password-match]");
      if (matchStatus) {
        matchStatus.textContent = "";
        matchStatus.className = "field-hint";
      }

      if (window.initUppercaseInputs) window.initUppercaseInputs(editForm);

      const fileInput = editForm.querySelector('input[type="file"][name="image"]');
      if (fileInput) fileInput.value = "";
      if (removeImage) removeImage.checked = false;

      if (dataset.imageUrl && imageWrap && currentImage) {
        currentImage.src = dataset.imageUrl;
        imageWrap.hidden = false;
      } else if (imageWrap) {
        imageWrap.hidden = true;
        if (currentImage) currentImage.removeAttribute("src");
      }

      setModalOpen(editModal, true);
    });
  });

  document.querySelectorAll("[data-confirm-delete]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const name = form.dataset.shopName || "this shop";
      if (!window.confirm(`Delete “${name}”? This cannot be undone.`)) {
        event.preventDefault();
      }
    });
  });

  const searchInput = document.querySelector("[data-shop-search]");
  const rows = Array.from(document.querySelectorAll("[data-shop-row]"));
  const noResults = document.querySelector("[data-shop-no-results]");
  const visibleCount = document.querySelector("[data-shop-visible-count]");
  const total = Number(document.querySelector("[data-shop-total]")?.dataset.shopTotal || rows.length);

  const updateSearch = () => {
    const query = (searchInput?.value || "").trim().toLowerCase();
    let shown = 0;
    rows.forEach((row) => {
      const haystack = row.dataset.searchText || "";
      const match = !query || haystack.includes(query);
      row.hidden = !match;
      if (match) shown += 1;
    });
    if (noResults) noResults.hidden = shown > 0 || rows.length === 0;
    if (visibleCount) {
      visibleCount.textContent = query
        ? `${shown} of ${total} shop${total === 1 ? "" : "s"}`
        : `${total} shop${total === 1 ? "" : "s"}`;
    }
  };

  searchInput?.addEventListener("input", updateSearch);
})();
