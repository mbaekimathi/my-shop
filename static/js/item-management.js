(() => {
  const body = document.body;

  const setModalOpen = (modal, open) => {
    if (!modal) return;
    modal.hidden = !open;
    modal.setAttribute("aria-hidden", open ? "false" : "true");
    const anyOpen = document.querySelector('.workspace-modal:not([hidden])');
    body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
    if (open) {
      const firstField = modal.querySelector("input:not([type=hidden]), textarea, select");
      firstField?.focus();
      if (window.initUppercaseInputs) window.initUppercaseInputs(modal);
      if (window.lucide?.createIcons) window.lucide.createIcons();
    }
  };

  const syncPricingMode = (root) => {
    if (!root) return;
    const selected = root.querySelector('input[name="pricing_mode"]:checked');
    const mode = selected?.value || "single";
    const isEditForm = Boolean(root.closest("[data-edit-form]"));
    root.querySelectorAll("[data-pricing-panel]").forEach((panel) => {
      const isActive = panel.dataset.pricingPanel === mode;
      panel.hidden = !isActive;
      panel.querySelectorAll("input").forEach((input) => {
        input.disabled = !isActive;
      });
    });

    const singleInput = root.querySelector("[data-single-shop-price]");
    if (singleInput && !singleInput.disabled) {
      singleInput.required = true;
    }

    root.querySelectorAll("[data-shop-price-input]").forEach((input) => {
      input.required = mode === "individual" && !input.disabled;
    });

    const fillValue = singleInput?.value?.trim() || "";

    // Only pre-fill blank per-shop prices when registering a new item.
    if (mode === "individual" && fillValue && !isEditForm) {
      root.querySelectorAll("[data-shop-price-input]").forEach((input) => {
        if (!input.value) input.value = fillValue;
      });
    }
  };

  const bindPricingMode = (root) => {
    if (!root || root.dataset.pricingBound === "1") return;
    root.dataset.pricingBound = "1";
    root.querySelectorAll("[data-pricing-mode]").forEach((radio) => {
      radio.addEventListener("change", () => syncPricingMode(root));
    });
    syncPricingMode(root);
  };

  document.querySelectorAll("[data-shop-pricing]").forEach(bindPricingMode);

  document.querySelectorAll(".workspace-modal").forEach((modal) => {
    const name = modal.dataset.modal;
    if (!name) return;

    document.querySelectorAll(`[data-modal-open="${name}"]`).forEach((trigger) => {
      trigger.addEventListener("click", () => {
        if (name === "register-item") {
          const trackSerial = modal.querySelector('input[type="checkbox"][name="track_serial_number"]');
          if (trackSerial) trackSerial.checked = false;
          const singleMode = modal.querySelector('input[name="pricing_mode"][value="single"]');
          if (singleMode) {
            singleMode.checked = true;
            syncPricingMode(modal.querySelector("[data-shop-pricing]"));
          }
          modal.querySelectorAll("[data-shop-price-input]").forEach((input) => {
            input.value = "";
          });
          const shopPrice = modal.querySelector("[data-single-shop-price]");
          if (shopPrice) shopPrice.value = "";
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

  const editModal = document.querySelector('[data-modal="edit-item"]');
  const editForm = editModal?.querySelector("[data-edit-form]");
  const itemIdInput = editForm?.querySelector("[data-edit-item-id]");
  const imageWrap = editForm?.querySelector("[data-current-image-wrap]");
  const currentImage = editForm?.querySelector("[data-current-image]");
  const removeImage = editForm?.querySelector("[data-remove-image]");

  const setField = (name, value) => {
    const field = editForm?.querySelector(`[name="${name}"]`);
    if (field) field.value = value ?? "";
  };

  const setCheckbox = (name, checked) => {
    const field = editForm?.querySelector(`input[type="checkbox"][name="${name}"]`);
    if (field) field.checked = Boolean(checked);
  };

  const setPricingMode = (mode) => {
    const root = editForm?.querySelector("[data-shop-pricing]");
    if (!root) return;
    const radio = root.querySelector(`input[name="pricing_mode"][value="${mode}"]`);
    if (radio) radio.checked = true;
    syncPricingMode(root);
  };

  const setShopPrices = (prices) => {
    let map = {};
    try {
      map = prices ? JSON.parse(prices) : {};
    } catch (_err) {
      map = {};
    }
    editForm?.querySelectorAll("[data-shop-price-input]").forEach((input) => {
      const shopId = input.dataset.shopId;
      if (Object.prototype.hasOwnProperty.call(map, shopId)) {
        input.value = map[shopId];
      } else {
        input.value = "";
      }
    });
  };

  const openEditFromButton = (button) => {
    if (!editForm || !button) return;

    const dataset = button.dataset;
    if (itemIdInput) itemIdInput.value = dataset.itemId || "";
    setField("category", dataset.category);
    setField("name", dataset.name);
    setField("description", dataset.description);
    setField("minimum_selling_price", dataset.minimumSellingPrice);
    setField("shop_price", dataset.shopPrice);
    setPricingMode(dataset.pricingMode === "individual" ? "individual" : "single");
    setShopPrices(dataset.shopPrices);
    setCheckbox(
      "track_serial_number",
      dataset.trackSerialNumber === "1" || dataset.trackSerialNumber === "true"
    );

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
  };

  // Progressive catalog rows are created after bind — use delegation.
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-edit-item]");
    if (!button) return;
    openEditFromButton(button);
  });

  document.addEventListener("submit", (event) => {
    const form = event.target.closest?.("[data-confirm-delete]");
    if (!form) return;
    const name = form.dataset.itemName || "this item";
    if (!window.confirm(`Delete “${name}”? This cannot be undone.`)) {
      event.preventDefault();
    }
  });

})();
