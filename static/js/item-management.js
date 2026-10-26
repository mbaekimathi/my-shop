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
          modal.querySelectorAll("[data-shop-sold-input]").forEach((input) => {
            input.checked = true;
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
  const removeImageWrap = editForm?.querySelector("[data-remove-image-wrap]");
  const editImageInput = editForm?.querySelector("[data-edit-image-input]");
  let editImageObjectUrl = "";
  let editOriginalImageUrl = "";

  const revokeEditImagePreview = () => {
    if (editImageObjectUrl) {
      URL.revokeObjectURL(editImageObjectUrl);
      editImageObjectUrl = "";
    }
  };

  const showCurrentImage = (url) => {
    if (!imageWrap || !currentImage) return;
    if (url) {
      currentImage.src = url;
      imageWrap.hidden = false;
      if (removeImageWrap) removeImageWrap.hidden = false;
    } else {
      currentImage.removeAttribute("src");
      imageWrap.hidden = true;
      if (removeImageWrap) removeImageWrap.hidden = true;
    }
  };

  const resetEditImageControls = (imageUrl = "") => {
    revokeEditImagePreview();
    editOriginalImageUrl = imageUrl || "";
    if (editImageInput) editImageInput.value = "";
    if (removeImage) removeImage.checked = false;
    showCurrentImage(editOriginalImageUrl);
  };

  if (editImageInput) {
    editImageInput.addEventListener("change", () => {
      const file = editImageInput.files?.[0];
      if (!file) {
        showCurrentImage(editOriginalImageUrl);
        return;
      }
      if (removeImage) removeImage.checked = false;
      revokeEditImagePreview();
      editImageObjectUrl = URL.createObjectURL(file);
      showCurrentImage(editImageObjectUrl);
    });
  }

  if (removeImage) {
    removeImage.addEventListener("change", () => {
      if (!removeImage.checked) {
        if (editImageInput?.files?.length) {
          revokeEditImagePreview();
          editImageObjectUrl = URL.createObjectURL(editImageInput.files[0]);
          showCurrentImage(editImageObjectUrl);
        } else {
          showCurrentImage(editOriginalImageUrl);
        }
        return;
      }
      if (editImageInput) editImageInput.value = "";
      revokeEditImagePreview();
      showCurrentImage("");
      if (removeImageWrap) removeImageWrap.hidden = false;
    });
  }

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

  const parseShopPriceMap = (prices) => {
    try {
      return prices ? JSON.parse(prices) : {};
    } catch (_err) {
      return {};
    }
  };

  const shopPricesDiffer = (prices) => {
    const map = parseShopPriceMap(prices);
    const values = Object.values(map).filter((value) => String(value || "").trim() !== "");
    if (values.length < 2) return false;
    const first = String(values[0]);
    return values.some((value) => String(value) !== first);
  };

  const setShopPrices = (prices) => {
    const map = parseShopPriceMap(prices);
    editForm?.querySelectorAll("[data-shop-price-input]").forEach((input) => {
      const shopId = input.dataset.shopId;
      if (Object.prototype.hasOwnProperty.call(map, shopId)) {
        input.value = map[shopId];
      } else {
        input.value = "";
      }
    });
  };

  const setSoldShops = (soldShops) => {
    const map = parseShopPriceMap(soldShops);
    editForm?.querySelectorAll("[data-shop-sold-input]").forEach((input) => {
      const shopId = input.dataset.shopId;
      input.checked = Object.prototype.hasOwnProperty.call(map, shopId)
        ? map[shopId] === true || map[shopId] === "1" || map[shopId] === "true"
        : true;
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
    const useIndividual =
      dataset.pricingMode === "individual" || shopPricesDiffer(dataset.shopPrices);
    setPricingMode(useIndividual ? "individual" : "single");
    setShopPrices(dataset.shopPrices);
    setSoldShops(dataset.soldShops);
    setCheckbox(
      "track_serial_number",
      dataset.trackSerialNumber === "1" || dataset.trackSerialNumber === "true"
    );

    if (window.initUppercaseInputs) window.initUppercaseInputs(editForm);

    resetEditImageControls(dataset.imageUrl || "");
    clearEditErrors();

    setModalOpen(editModal, true);
  };

  // Progressive catalog rows are created after bind — use delegation.
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-edit-item]");
    if (!button) return;
    openEditFromButton(button);
  });

  // In-place item editing — save via fetch so the catalog keeps its
  // search, sort, loaded pages, and scroll position.
  const clearEditErrors = () => {
    editModal?.querySelector(".workspace-modal-errors")?.remove();
  };

  const showEditErrors = (errors) => {
    if (!editModal || !editForm) return;
    let list = editModal.querySelector(".workspace-modal-errors");
    if (!list) {
      list = document.createElement("ul");
      list.className = "form-alert-list workspace-modal-errors";
      editForm.parentNode.insertBefore(list, editForm);
    }
    list.innerHTML = "";
    errors.forEach((message) => {
      const li = document.createElement("li");
      li.className = "form-alert";
      li.textContent = message;
      list.appendChild(li);
    });
    list.scrollIntoView({ block: "nearest" });
  };

  let editSubmitting = false;

  const postEditForm = () =>
    // NB: editForm.action is shadowed by the <input name="action"> control —
    // use getAttribute, which reads the real attribute (null when absent).
    fetch(editForm.getAttribute("action") || window.location.href, {
      method: "POST",
      body: new FormData(editForm),
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    });

  editForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    if (editSubmitting) return;
    editSubmitting = true;
    const submitBtn = editForm.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    clearEditErrors();

    // Retry once on a dropped connection (e.g. a dev-server restart mid-save).
    // Edits are idempotent — the same payload is replayed unchanged.
    const attempt = (retriesLeft) =>
      postEditForm().catch((networkErr) => {
        if (retriesLeft > 0) return attempt(retriesLeft - 1);
        throw networkErr;
      });

    attempt(1)
      .then(async (response) => {
        if (response.redirected) {
          window.location.reload();
          return;
        }
        const data = await response.json().catch(() => null);
        if (response.ok && data?.ok) {
          setModalOpen(editModal, false);
          document.dispatchEvent(
            new CustomEvent("item-catalog:item-updated", {
              detail: { item: data.item },
            })
          );
          if (window.showWorkspaceToast) {
            window.showWorkspaceToast(
              data.message || "Item updated successfully.",
              "success"
            );
          }
          import("./offline/catalog-cache.js")
            .then((mod) =>
              mod.notifyCatalogChanged?.({
                scopes: ["all"],
                reason: "item_edit",
                softReload: false,
              })
            )
            .catch(() => {});
          return;
        }
        if (data === null) {
          console.error("Item edit failed:", response.status, response);
          showEditErrors([
            `Could not save changes — the server returned an error (HTTP ${response.status}). Try again.`,
          ]);
          return;
        }
        if (data?.code === "item_missing") {
          // The row was stale (item deleted elsewhere) — drop it from the
          // catalog so the user can't keep editing a phantom.
          document.dispatchEvent(
            new CustomEvent("item-catalog:item-removed", {
              detail: { id: data.item_id || itemIdInput?.value },
            })
          );
        }
        const errors =
          Array.isArray(data?.errors) && data.errors.length
            ? data.errors
            : [data?.error || "Could not save changes. Try again."];
        showEditErrors(errors);
      })
      .catch(() => {
        showEditErrors([
          "Could not reach the server. Check your connection and try again.",
        ]);
      })
      .finally(() => {
        editSubmitting = false;
        if (submitBtn) submitBtn.disabled = false;
      });
  });

  const deleteModal = document.querySelector('[data-modal="delete-item"]');
  const deleteTitleEl = deleteModal?.querySelector("[data-delete-title]");
  const deleteLeadEl = deleteModal?.querySelector("[data-delete-lead]");
  const deleteDetailEl = deleteModal?.querySelector("[data-delete-detail]");
  const deleteKicker = deleteModal?.querySelector("[data-delete-kicker]");
  const deleteStockWrap = deleteModal?.querySelector("[data-delete-stock-wrap]");
  const deleteStockList = deleteModal?.querySelector("[data-delete-stock-list]");
  const deleteConfirmBtn = deleteModal?.querySelector("[data-delete-confirm]");
  const deleteConfirmLabel = deleteModal?.querySelector(
    "[data-delete-confirm-label]"
  );
  let pendingDeleteForm = null;
  let pendingDeleteSuspend = false;

  const submitDeleteForm = (form, suspend) => {
    if (suspend) {
      const actionInput = form.querySelector('input[name="action"]');
      if (actionInput) actionInput.value = "suspend";
    }
    form.dataset.deleteConfirmed = "1";
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.submit();
  };

  const openDeleteConfirm = (form) => {
    const name = form.dataset.itemName || "this item";
    if (!deleteModal) {
      if (window.confirm(`Delete “${name}”? This cannot be undone.`)) {
        submitDeleteForm(form, false);
      }
      return;
    }
    let stockRows = [];
    try {
      stockRows = JSON.parse(form.dataset.stockRows || "[]");
    } catch (_err) {
      stockRows = [];
    }
    if (!Array.isArray(stockRows)) stockRows = [];

    const hasStock = stockRows.length > 0;
    const isSuspended = form.dataset.itemSuspended === "1";
    const willSuspend = hasStock && !isSuspended;

    pendingDeleteForm = form;
    pendingDeleteSuspend = willSuspend;

    if (deleteTitleEl) {
      deleteTitleEl.textContent = willSuspend ? "Suspend item" : "Delete item";
    }
    if (deleteLeadEl) {
      deleteLeadEl.textContent = willSuspend
        ? `“${name}” still has stock on hand.`
        : `Delete “${name}”? This cannot be undone.`;
    }
    if (deleteKicker) deleteKicker.hidden = !hasStock;
    if (deleteStockWrap && deleteStockList) {
      deleteStockList.innerHTML = "";
      stockRows.forEach((row) => {
        const li = document.createElement("li");
        li.className = "item-delete-stock-row";
        const shop = document.createElement("span");
        shop.textContent = row.shop_name || "Shop";
        const qty = document.createElement("strong");
        qty.textContent = String(row.quantity ?? "0");
        li.append(shop, qty);
        deleteStockList.appendChild(li);
      });
      if (deleteDetailEl) {
        deleteDetailEl.textContent = willSuspend
          ? "Suspend it instead — it disappears from sales, serials, and shop catalogues, and can be restored later."
          : "This item still has stock on hand — deleting it will discard:";
      }
      deleteStockWrap.hidden = !hasStock;
    }
    if (deleteConfirmLabel) {
      deleteConfirmLabel.textContent = willSuspend
        ? "Suspend item"
        : hasStock
        ? "Delete anyway"
        : "Delete item";
    }
    // lucide swaps <i> for <svg> on render — re-query the icon each open.
    deleteConfirmBtn
      ?.querySelector("[data-lucide]")
      ?.setAttribute("data-lucide", willSuspend ? "pause" : "trash-2");
    setModalOpen(deleteModal, true);
    deleteConfirmBtn?.focus();
  };

  deleteConfirmBtn?.addEventListener("click", () => {
    const form = pendingDeleteForm;
    const suspend = pendingDeleteSuspend;
    pendingDeleteForm = null;
    pendingDeleteSuspend = false;
    setModalOpen(deleteModal, false);
    if (form) submitDeleteForm(form, suspend);
  });

  document.addEventListener("submit", (event) => {
    const form = event.target.closest?.("[data-confirm-delete]");
    if (!form) return;
    if (form.dataset.deleteConfirmed === "1") return;
    event.preventDefault();
    openDeleteConfirm(form);
  });

  // Flush offline catalogs before item register/edit/delete navigates away.
  document.addEventListener(
    "submit",
    (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      // The edit form saves in place via fetch — it flushes catalogs itself.
      if (form.matches("[data-edit-form]")) return;
      if (form.dataset.catalogFlushed === "1") return;
      const action = String(
        form.querySelector('input[name="action"]')?.value || ""
      ).toLowerCase();
      if (
        !["register", "edit", "delete", "toggle_suspend", "suspend", "unsuspend"].includes(action)
      ) {
        return;
      }
      event.preventDefault();
      form.dataset.catalogFlushed = "1";
      import("./offline/catalog-cache.js")
        .then((mod) =>
          mod.notifyCatalogChanged?.({
            scopes: ["all"],
            reason: `item_${action}`,
            softReload: true,
          })
        )
        .catch(() => {})
        .finally(() => {
          if (typeof form.requestSubmit === "function") form.requestSubmit();
          else form.submit();
        });
    },
    true
  );

})();
