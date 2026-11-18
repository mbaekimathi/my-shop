(() => {
  const root = document.querySelector("[data-pos-settings]");
  if (!root) return;

  function readCookie(name) {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) {
      return decodeURIComponent(parts.pop().split(";").shift() || "");
    }
    return "";
  }

  const csrfToken =
    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    readCookie("csrftoken") ||
    "";

  const enabledCountEl = root.querySelector("[data-pos-enabled-count]");
  const isCompanyPos = root.hasAttribute("data-company-pos-settings");
  const filterInput = root.querySelector("[data-pos-filter]");
  const filterEmptyEl = root.querySelector("[data-pos-filter-empty]");
  const groupSections = Array.from(root.querySelectorAll("[data-pos-group]"));
  const jumpLinks = Array.from(root.querySelectorAll("[data-pos-jump]"));

  function applyPosFilter() {
    if (!filterInput || !groupSections.length) return;
    const query = (filterInput.value || "").trim().toLowerCase();
    let anyVisible = false;
    groupSections.forEach((section) => {
      const groupTitle = (section.dataset.groupTitle || "").toLowerCase();
      let visibleRows = 0;
      section.querySelectorAll(".pos-settings-row").forEach((row) => {
        const haystack = `${groupTitle} ${row.textContent}`.toLowerCase();
        const match = !query || haystack.includes(query);
        row.classList.toggle("is-filtered-out", !match);
        if (match && !row.hidden) visibleRows += 1;
      });
      const empty = Boolean(query) && visibleRows === 0;
      section.classList.toggle("is-filtered-empty", empty);
      if (visibleRows > 0) anyVisible = true;
    });
    jumpLinks.forEach((link) => {
      const target = root.querySelector(`#pos-group-${link.dataset.posJump}`);
      link.classList.toggle(
        "is-empty",
        Boolean(query) &&
          Boolean(target) &&
          target.classList.contains("is-filtered-empty")
      );
    });
    if (filterEmptyEl) {
      filterEmptyEl.hidden = !(query && !anyVisible);
    }
  }

  if (filterInput) {
    filterInput.addEventListener("input", applyPosFilter);
    filterInput.addEventListener("search", applyPosFilter);
  }
  const stkToggle = root.querySelector("[data-stk-shop-toggle]");
  const stkState = root.querySelector("[data-stk-shop-state]");
  const stkHint = root.querySelector("[data-stk-shop-hint]");
  const stkMessage = root.querySelector("[data-stk-shop-message]");

  function syncEnabledCount() {
    if (!enabledCountEl) return;
    const on = root.querySelectorAll("[data-pos-toggle]:checked").length;
    enabledCountEl.textContent = String(on);
  }

  function setStateLabel(input, enabled) {
    const label = input.closest(".perm-switch")?.querySelector(".perm-switch-state");
    if (label) {
      label.textContent = enabled ? "On" : "Off";
    }
    input.closest(".perm-switch")?.classList.toggle("is-denied", !enabled);
  }

  const taxRow = root.querySelector("[data-tax-percent-row]");
  const taxInput = root.querySelector("[data-tax-percent-input]");
  const deliveryRow = root.querySelector("[data-delivery-amount-row]");
  const deliveryInput = root.querySelector("[data-delivery-amount-input]");
  const employeeTimeoutRow = root.querySelector("[data-employee-timeout-row]");
  const employeeTimeoutInput = root.querySelector(
    "[data-employee-timeout-input]"
  );
  const shopTimeoutRow = root.querySelector("[data-shop-timeout-row]");
  const shopTimeoutInput = root.querySelector("[data-shop-timeout-input]");
  const clientDataDependents = root.querySelectorAll("[data-client-data-dependent]");
  const openCloseDependents = root.querySelectorAll("[data-open-close-dependent]");
  const openCloseCompulsory = root.querySelector(
    '[data-pos-toggle][data-field="enable_open_close"]'
  );
  const openCloseModeInputs = Array.from(
    root.querySelectorAll("[data-open-close-mode-toggle]")
  );

  function syncTaxRow(enabled) {
    if (!taxRow) return;
    taxRow.hidden = !enabled;
    applyPosFilter();
  }

  function syncDeliveryRow(enabled) {
    if (!deliveryRow) return;
    deliveryRow.hidden = !enabled;
    applyPosFilter();
  }

  function syncEmployeeTimeoutRow(enabled) {
    if (!employeeTimeoutRow) return;
    employeeTimeoutRow.hidden = !enabled;
    applyPosFilter();
  }

  function syncShopTimeoutRow(enabled) {
    if (!shopTimeoutRow) return;
    shopTimeoutRow.hidden = !enabled;
    applyPosFilter();
  }

  function syncClientDataDependents(enabled) {
    clientDataDependents.forEach((row) => {
      row.hidden = !enabled;
    });
    applyPosFilter();
  }

  function openCloseMode() {
    const setInput = openCloseModeInputs.find(
      (el) => el.dataset.mode === "set"
    );
    return setInput && setInput.checked ? "set" : "anytime";
  }

  function syncOpenCloseDependents() {
    const compulsoryOn = Boolean(openCloseCompulsory?.checked);
    const setOn = openCloseMode() === "set";
    openCloseDependents.forEach((row) => {
      row.hidden = row.hasAttribute("data-open-close-times")
        ? !(compulsoryOn && setOn)
        : !compulsoryOn;
    });
    applyPosFilter();
  }

  function syncEmployeePermLinks(field, enabled) {
    if (!field) return;
    root.querySelectorAll(
      `[data-pos-employee-link][data-pos-field="${field}"]`
    ).forEach((link) => {
      link.hidden = !enabled;
    });
  }

  function setStkMessage(text, { error = false } = {}) {
    if (!stkMessage) return;
    stkMessage.hidden = !text;
    stkMessage.textContent = text || "";
    stkMessage.classList.toggle("is-error", Boolean(error));
    stkMessage.classList.toggle("is-ok", Boolean(text) && !error);
  }

  function setStkToggleLabel(enabled) {
    if (stkState) stkState.textContent = enabled ? "On" : "Off";
    stkToggle?.closest(".perm-switch")?.classList.toggle("is-denied", !enabled);
  }

  function optimisticMpesaStkHint() {
    if (!isCompanyPos || !stkHint) return;
    const mpesa =
      root.querySelector('[data-pos-toggle][data-field="enable_mpesa"]')?.checked ||
      root.querySelector('[data-pos-toggle][data-field="enable_cash_mpesa"]')
        ?.checked;
    if (!mpesa) {
      stkHint.textContent = "Enable M-Pesa below.";
      root.dataset.stkCanEnable = "0";
      return;
    }
    if (root.dataset.stkCanEnable === "1") {
      stkHint.textContent = stkToggle?.checked
        ? "Active on cart."
        : "Ready to enable.";
    }
  }

  function renderStkShopStatus(data) {
    if (!data || !isCompanyPos) return;
    if (typeof data.stk_can_enable_on_pos === "boolean") {
      root.dataset.stkCanEnable = data.stk_can_enable_on_pos ? "1" : "0";
    }
    if (stkToggle && typeof data.enable_stk_push === "boolean") {
      stkToggle.checked = data.enable_stk_push;
      setStkToggleLabel(data.enable_stk_push);
    }
    if (!stkHint) return;
    if (data.stk_ready_for_shop) {
      stkHint.textContent = "Active on cart.";
    } else if (!data.mpesa_checkout_enabled) {
      stkHint.textContent = "Enable M-Pesa below.";
    } else if (!data.stk_credentials_ready) {
      stkHint.textContent = "Set up Daraja first.";
    } else {
      stkHint.textContent = "Ready to enable.";
    }
  }

  async function postSettings(body) {
    const response = await fetch(window.location.pathname, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrfToken,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body,
      credentials: "same-origin",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      const error = new Error(data.error || "Could not save setting.");
      error.payload = data;
      throw error;
    }
    if (typeof window.myshopSyncCustomTags === "function") {
      window.myshopSyncCustomTags(data.custom_fields);
    }
    return data;
  }

  root.querySelectorAll("[data-pos-toggle]").forEach((input) => {
    setStateLabel(input, input.checked);
    if (input.dataset.field === "enable_tax") {
      syncTaxRow(input.checked);
    }
    if (input.dataset.field === "enable_cart_delivery") {
      syncDeliveryRow(input.checked);
    }
    if (input.dataset.field === "enable_employee_session_timeout") {
      syncEmployeeTimeoutRow(input.checked);
    }
    if (input.dataset.field === "enable_shop_session_timeout") {
      syncShopTimeoutRow(input.checked);
    }
    if (input.dataset.field === "enable_client_data") {
      syncClientDataDependents(input.checked);
    }
    if (input.dataset.field === "enable_open_close") {
      syncOpenCloseDependents();
    }
    syncEmployeePermLinks(input.dataset.field, input.checked);

    input.addEventListener("change", async () => {
      const enabled = input.checked;
      const previous = !enabled;
      const stockModeInputs = input.hasAttribute("data-stock-mode-toggle")
        ? Array.from(root.querySelectorAll("[data-stock-mode-toggle]"))
        : [];
      const stockModeBefore = stockModeInputs.map((el) =>
        el === input ? previous : el.checked
      );
      if (stockModeInputs.length) {
        if (!enabled) {
          // One stock mode must stay on; switch to another to change it.
          input.checked = true;
          setStateLabel(input, true);
          return;
        }
        stockModeInputs.forEach((el) => {
          if (el !== input) {
            el.checked = false;
            setStateLabel(el, false);
          }
        });
      }
      setStateLabel(input, enabled);
      if (input.dataset.field === "enable_tax") {
        syncTaxRow(enabled);
      }
      if (input.dataset.field === "enable_cart_delivery") {
        syncDeliveryRow(enabled);
      }
      if (input.dataset.field === "enable_employee_session_timeout") {
        syncEmployeeTimeoutRow(enabled);
      }
      if (input.dataset.field === "enable_shop_session_timeout") {
        syncShopTimeoutRow(enabled);
      }
      if (input.dataset.field === "enable_client_data") {
        syncClientDataDependents(enabled);
      }
      if (input.dataset.field === "enable_open_close") {
        syncOpenCloseDependents();
      }
      syncEmployeePermLinks(input.dataset.field, enabled);
      if (
        isCompanyPos &&
        (input.dataset.field === "enable_mpesa" ||
          input.dataset.field === "enable_cash_mpesa")
      ) {
        optimisticMpesaStkHint();
      }
      input.closest(".perm-switch")?.classList.add("is-saving");

      const body = new URLSearchParams({
        action: "toggle_pos_setting",
        field: input.dataset.field || "",
        enabled: enabled ? "1" : "0",
      });

      try {
        const data = await postSettings(body);
        setStateLabel(input, Boolean(data.enabled));
        input.checked = Boolean(data.enabled);
        if (stockModeInputs.length && typeof data.stock_mode === "string") {
          stockModeInputs.forEach((el) => {
            el.checked = el.dataset.field === data.stock_mode;
            setStateLabel(el, el.checked);
          });
        }
        if (input.dataset.field === "enable_tax") {
          syncTaxRow(Boolean(data.enabled));
        }
        if (input.dataset.field === "enable_cart_delivery") {
          syncDeliveryRow(Boolean(data.enabled));
        }
        if (input.dataset.field === "enable_employee_session_timeout") {
          syncEmployeeTimeoutRow(Boolean(data.enabled));
        }
        if (input.dataset.field === "enable_shop_session_timeout") {
          syncShopTimeoutRow(Boolean(data.enabled));
        }
        if (input.dataset.field === "enable_client_data") {
          syncClientDataDependents(Boolean(data.enabled));
        }
        if (input.dataset.field === "enable_open_close") {
          syncOpenCloseDependents();
        }
        syncEnabledCount();
        if (
          isCompanyPos &&
          (input.dataset.field === "enable_mpesa" ||
            input.dataset.field === "enable_cash_mpesa")
        ) {
          renderStkShopStatus(data);
        }
      } catch (error) {
        input.checked = previous;
        setStateLabel(input, previous);
        stockModeInputs.forEach((el, index) => {
          el.checked = stockModeBefore[index];
          setStateLabel(el, el.checked);
        });
        if (input.dataset.field === "enable_tax") {
          syncTaxRow(previous);
        }
        if (input.dataset.field === "enable_cart_delivery") {
          syncDeliveryRow(previous);
        }
        if (input.dataset.field === "enable_employee_session_timeout") {
          syncEmployeeTimeoutRow(previous);
        }
        if (input.dataset.field === "enable_shop_session_timeout") {
          syncShopTimeoutRow(previous);
        }
        if (input.dataset.field === "enable_client_data") {
          syncClientDataDependents(previous);
        }
        if (input.dataset.field === "enable_open_close") {
          syncOpenCloseDependents();
        }
        syncEmployeePermLinks(input.dataset.field, previous);
        syncEnabledCount();
        window.alert(error.message || "Could not save setting.");
      } finally {
        input.closest(".perm-switch")?.classList.remove("is-saving");
      }
    });
  });

  openCloseModeInputs.forEach((input) => {
    setStateLabel(input, input.checked);
    input.addEventListener("change", async () => {
      if (!input.checked) {
        // One schedule mode must stay on; switch to the other to change it.
        input.checked = true;
        setStateLabel(input, true);
        return;
      }
      const before = openCloseModeInputs.map((el) => el.checked);
      openCloseModeInputs.forEach((el) => {
        if (el !== input) {
          el.checked = false;
          setStateLabel(el, false);
        }
      });
      setStateLabel(input, true);
      syncOpenCloseDependents();
      input.closest(".perm-switch")?.classList.add("is-saving");
      try {
        const data = await postSettings(
          new URLSearchParams({
            action: "set_open_close_mode",
            mode: input.dataset.mode || "",
          })
        );
        const setOn = data.open_close_mode === "set";
        openCloseModeInputs.forEach((el) => {
          el.checked = (el.dataset.mode === "set") === setOn;
          setStateLabel(el, el.checked);
        });
        syncOpenCloseDependents();
      } catch (error) {
        openCloseModeInputs.forEach((el, index) => {
          el.checked = before[index];
          setStateLabel(el, el.checked);
        });
        syncOpenCloseDependents();
        window.alert(error.message || "Could not save setting.");
      } finally {
        input.closest(".perm-switch")?.classList.remove("is-saving");
      }
    });
  });
  syncOpenCloseDependents();

  if (isCompanyPos && stkToggle) {
    setStkToggleLabel(stkToggle.checked);
    stkToggle.addEventListener("change", async () => {
      const enabled = stkToggle.checked;
      const previous = !enabled;
      setStkToggleLabel(enabled);
      setStkMessage("");
      if (enabled && root.dataset.stkCanEnable === "0") {
        stkToggle.checked = previous;
        setStkToggleLabel(previous);
        setStkMessage(
          stkHint?.textContent ||
            "Turn on M-Pesa and configure Daraja settings first.",
          { error: true }
        );
        return;
      }
      stkToggle.closest(".perm-switch")?.classList.add("is-saving");
      try {
        const data = await postSettings(
          new URLSearchParams({
            action: "toggle_shop_stk_push",
            enabled: enabled ? "1" : "0",
          })
        );
        renderStkShopStatus(data);
        setStkMessage(
          data.enable_stk_push
            ? "STK Push enabled for shop checkout."
            : "STK Push disabled on shop checkout."
        );
      } catch (error) {
        stkToggle.checked = previous;
        setStkToggleLabel(previous);
        if (error.payload) renderStkShopStatus(error.payload);
        setStkMessage(error.message || "Could not update STK Push.", {
          error: true,
        });
      } finally {
        stkToggle.closest(".perm-switch")?.classList.remove("is-saving");
      }
    });
  }

  if (taxInput) {
    let taxTimer = 0;
    let lastSaved = taxInput.value;

    const saveTaxPercent = async () => {
      const value = (taxInput.value || "").trim();
      if (!value || value === lastSaved) return;
      taxInput.classList.add("is-saving");
      try {
        const data = await postSettings(
          new URLSearchParams({
            action: "set_tax_percent",
            tax_percent: value,
          })
        );
        lastSaved = String(Math.round(Number(data.tax_percent)));
        taxInput.value = lastSaved;
        taxInput.classList.remove("is-error");
      } catch (error) {
        taxInput.classList.add("is-error");
        window.alert(error.message || "Could not save tax percentage.");
        taxInput.value = lastSaved;
      } finally {
        taxInput.classList.remove("is-saving");
      }
    };

    taxInput.addEventListener("change", saveTaxPercent);
    taxInput.addEventListener("blur", saveTaxPercent);
    taxInput.addEventListener("input", () => {
      window.clearTimeout(taxTimer);
      taxTimer = window.setTimeout(saveTaxPercent, 600);
    });
  }

  if (deliveryInput) {
    let deliveryTimer = 0;
    let lastSaved = deliveryInput.value;

    const saveDeliveryAmount = async () => {
      const value = (deliveryInput.value || "").trim();
      if (value === lastSaved) return;
      deliveryInput.classList.add("is-saving");
      try {
        const data = await postSettings(
          new URLSearchParams({
            action: "set_delivery_amount",
            delivery_amount: value,
          })
        );
        lastSaved = String(Math.round(Number(data.delivery_amount)));
        deliveryInput.value = lastSaved;
        deliveryInput.classList.remove("is-error");
      } catch (error) {
        deliveryInput.classList.add("is-error");
        window.alert(error.message || "Could not save delivery amount.");
        deliveryInput.value = lastSaved;
      } finally {
        deliveryInput.classList.remove("is-saving");
      }
    };

    deliveryInput.addEventListener("change", saveDeliveryAmount);
    deliveryInput.addEventListener("blur", saveDeliveryAmount);
    deliveryInput.addEventListener("input", () => {
      window.clearTimeout(deliveryTimer);
      deliveryTimer = window.setTimeout(saveDeliveryAmount, 600);
    });
  }

  function attachTimeoutSaver(input, action, postField, responseKey) {
    if (!input) return;
    let timer = 0;
    let lastSaved = input.value;

    const save = async () => {
      const value = (input.value || "").trim();
      if (!value || value === lastSaved) return;
      input.classList.add("is-saving");
      try {
        const body = new URLSearchParams({ action });
        body.set(postField, value);
        const data = await postSettings(body);
        lastSaved = String(parseInt(data[responseKey], 10));
        input.value = lastSaved;
        input.classList.remove("is-error");
      } catch (error) {
        input.classList.add("is-error");
        window.alert(error.message || "Could not save session timeout.");
        input.value = lastSaved;
      } finally {
        input.classList.remove("is-saving");
      }
    };

    input.addEventListener("change", save);
    input.addEventListener("blur", save);
    input.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(save, 600);
    });
  }

  attachTimeoutSaver(
    employeeTimeoutInput,
    "set_employee_session_timeout",
    "employee_timeout_minutes",
    "employee_session_timeout_minutes"
  );
  attachTimeoutSaver(
    shopTimeoutInput,
    "set_shop_session_timeout",
    "shop_timeout_minutes",
    "shop_session_timeout_minutes"
  );
})();
