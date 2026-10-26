(() => {
  const modal = document.querySelector("[data-shop-switch-modal]");
  if (!modal) return;

  const switchUrl = modal.dataset.switchUrl || "";
  if (!switchUrl) return;

  const lead = modal.querySelector("[data-shop-switch-lead]");
  const errorBox = modal.querySelector("[data-shop-switch-error]");
  const errorText = modal.querySelector("[data-shop-switch-error-text]");
  const loading = modal.querySelector("[data-shop-switch-loading]");
  const emptyEl = modal.querySelector("[data-shop-switch-empty]");
  const list = modal.querySelector("[data-shop-switch-list]");
  const form = modal.querySelector("[data-shop-switch-form]");
  const passwordInput = modal.querySelector("[data-shop-switch-password]");
  const passwordLabel = modal.querySelector("[data-shop-switch-password-label]");
  const passwordToggle = modal.querySelector(
    "[data-shop-switch-password-toggle]"
  );
  const submitBtn = modal.querySelector("[data-shop-switch-submit]");

  function readCookie(name) {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) {
      return decodeURIComponent(parts.pop().split(";").shift() || "");
    }
    return "";
  }

  const csrfToken = () =>
    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    readCookie("csrftoken") ||
    "";

  let state = { passwordless: false, currentShopId: "", shops: [] };
  let selectedShopId = "";
  let busy = false;

  const refreshIcons = () => {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const showError = (message) => {
    if (!errorBox || !errorText) return;
    errorText.textContent = message || "";
    errorBox.hidden = !message;
  };

  const setBusy = (on) => {
    busy = on;
    list
      ?.querySelectorAll("button")
      .forEach((btn) => {
        btn.disabled = on || btn.dataset.shopSwitchPick === state.currentShopId;
      });
    if (submitBtn) submitBtn.disabled = on;
  };

  const buildCard = (shop) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "shop-select-card";
    card.dataset.shopSwitchPick = shop.id;

    const isCurrent = shop.id === state.currentShopId;
    if (isCurrent) {
      card.classList.add("is-selected", "is-current");
      card.disabled = true;
    }

    const media = document.createElement("span");
    media.className = "shop-select-card-media";
    media.setAttribute("aria-hidden", "true");
    if (shop.image_url) {
      const img = document.createElement("img");
      img.src = shop.image_url;
      img.alt = "";
      img.width = 56;
      img.height = 56;
      media.appendChild(img);
    } else {
      const icon = document.createElement("i");
      icon.setAttribute("data-lucide", "store");
      media.appendChild(icon);
    }

    const body = document.createElement("span");
    body.className = "shop-select-card-body";

    const name = document.createElement("span");
    name.className = "shop-select-card-name";
    name.textContent = shop.name || "Shop";
    body.appendChild(name);

    if (isCurrent) {
      const pill = document.createElement("span");
      pill.className = "shop-select-hours-pill is-trading";
      pill.textContent = "Current";
      body.appendChild(pill);
    }

    const meta = document.createElement("span");
    meta.className = "shop-select-card-meta";

    if (shop.location) {
      const loc = document.createElement("span");
      loc.className = "shop-select-loc";
      const locIcon = document.createElement("i");
      locIcon.setAttribute("data-lucide", "map-pin");
      locIcon.setAttribute("aria-hidden", "true");
      const locText = document.createElement("em");
      locText.textContent = shop.location;
      loc.append(locIcon, locText);
      meta.appendChild(loc);
    }

    if (shop.login_code) {
      const code = document.createElement("span");
      code.className = "shop-select-code";
      code.title = "Shop login code";
      const codeIcon = document.createElement("i");
      codeIcon.setAttribute("data-lucide", "hash");
      codeIcon.setAttribute("aria-hidden", "true");
      const codeText = document.createElement("em");
      codeText.textContent = shop.login_code;
      code.append(codeIcon, codeText);
      meta.appendChild(code);
    }

    body.appendChild(meta);

    const go = document.createElement("span");
    go.className = "shop-select-card-go";
    go.setAttribute("aria-hidden", "true");
    const markOff = document.createElement("i");
    markOff.setAttribute("data-lucide", "circle");
    markOff.className = "shop-select-mark shop-select-mark--off";
    const markOn = document.createElement("i");
    markOn.setAttribute("data-lucide", "check");
    markOn.className = "shop-select-mark shop-select-mark--on";
    go.append(markOff, markOn);

    card.append(media, body, go);
    return card;
  };

  const renderShops = () => {
    if (!list) return;
    list.innerHTML = "";
    const others = state.shops.filter(
      (shop) => shop.id !== state.currentShopId
    );
    if (loading) loading.hidden = true;
    if (!others.length) {
      list.hidden = true;
      if (emptyEl) emptyEl.hidden = false;
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    state.shops.forEach((shop) => list.appendChild(buildCard(shop)));
    list.hidden = false;
    refreshIcons();
  };

  const resetSelection = () => {
    selectedShopId = "";
    if (form) form.hidden = true;
    if (passwordInput) passwordInput.value = "";
  };

  const loadShops = async () => {
    showError("");
    resetSelection();
    if (loading) loading.hidden = false;
    if (emptyEl) emptyEl.hidden = true;
    if (list) {
      list.hidden = true;
      list.innerHTML = "";
    }
    try {
      const response = await fetch(switchUrl, {
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (response.status === 401 && data.login_url) {
        window.location.assign(data.login_url);
        return;
      }
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not load shops.");
      }
      state = {
        passwordless: Boolean(data.passwordless),
        currentShopId: String(data.current_shop_id || ""),
        shops: Array.isArray(data.shops) ? data.shops : [],
      };
      if (lead) {
        lead.textContent = state.passwordless
          ? "Choose a shop to switch to — no password needed."
          : "Choose a shop, then enter its password.";
      }
      renderShops();
    } catch (error) {
      if (loading) loading.hidden = true;
      showError(error.message || "Could not load shops.");
    }
  };

  const openModal = () => {
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("workspace-modal-open");
    modal.dispatchEvent(
      new CustomEvent("shop-switch-modal:open", { bubbles: true })
    );
    refreshIcons();
    loadShops();
  };

  const closeModal = () => {
    if (modal.hidden) return;
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    modal.dispatchEvent(
      new CustomEvent("shop-switch-modal:close", { bubbles: true })
    );
    showError("");
    resetSelection();
  };

  const submitSwitch = async (shopId, password) => {
    if (busy) return;
    setBusy(true);
    showError("");
    const body = new URLSearchParams({ shop_id: shopId });
    if (password !== undefined) body.set("password", password);
    try {
      const response = await fetch(switchUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfToken(),
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body,
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (response.status === 401 && data.login_url) {
        window.location.assign(data.login_url);
        return;
      }
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not switch shops.");
      }
      window.location.assign(data.redirect || window.location.href);
    } catch (error) {
      showError(error.message || "Could not switch shops.");
      setBusy(false);
      passwordInput?.focus();
    }
  };

  document
    .querySelectorAll("[data-shop-switch-open]")
    .forEach((trigger) => {
      trigger.addEventListener("click", (event) => {
        event.preventDefault();
        const menu = document.querySelector("[data-user-menu-panel]");
        if (menu) {
          menu.hidden = true;
          document
            .querySelector("[data-user-menu-trigger]")
            ?.setAttribute("aria-expanded", "false");
        }
        openModal();
      });
    });

  modal.addEventListener("click", (event) => {
    if (event.target.closest("[data-shop-switch-close]")) {
      event.preventDefault();
      closeModal();
      return;
    }
    const card = event.target.closest("[data-shop-switch-pick]");
    if (!card || !list?.contains(card)) return;
    const shopId = card.dataset.shopSwitchPick || "";
    if (!shopId || shopId === state.currentShopId || busy) return;
    if (state.passwordless) {
      submitSwitch(shopId);
      return;
    }
    list
      .querySelectorAll(".shop-select-card.is-selected:not(.is-current)")
      .forEach((el) => el.classList.remove("is-selected"));
    card.classList.add("is-selected");
    selectedShopId = shopId;
    const shop = state.shops.find((row) => row.id === shopId);
    if (passwordLabel) {
      passwordLabel.textContent = shop?.name
        ? `Password for ${shop.name}`
        : "Shop password";
    }
    if (form) form.hidden = false;
    showError("");
    window.setTimeout(() => passwordInput?.focus(), 40);
  });

  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!selectedShopId) {
      showError("Select a shop to open.");
      return;
    }
    submitSwitch(selectedShopId, passwordInput?.value || "");
  });

  passwordToggle?.addEventListener("click", () => {
    if (!passwordInput) return;
    const show = passwordInput.type === "password";
    passwordInput.type = show ? "text" : "password";
    passwordToggle.setAttribute(
      "aria-label",
      show ? "Hide password" : "Show password"
    );
    passwordToggle
      .querySelector("[data-eye-open]")
      ?.toggleAttribute("hidden", show);
    passwordToggle
      .querySelector("[data-eye-closed]")
      ?.toggleAttribute("hidden", !show);
  });
})();
