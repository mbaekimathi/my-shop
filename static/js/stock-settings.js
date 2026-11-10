(() => {
  const root = document.querySelector("[data-stock-settings]");
  if (!root) return;

  const visibilityForm = root.querySelector("[data-stock-visibility-form]");
  if (visibilityForm) {
    const panels = visibilityForm.querySelectorAll("[data-hide-panel]");
    const modeInputs = visibilityForm.querySelectorAll(
      "input[name=hide_stock_mode]"
    );
    const categoryChecks = visibilityForm.querySelectorAll(
      "input[name=hide_stock_categories]"
    );
    const itemChecks = visibilityForm.querySelectorAll(
      "input[name=hide_stock_item_ids]"
    );

    const syncPanelCount = (panel, checkedCount) => {
      if (!panel) return;
      const head = panel.querySelector(".stock-visibility-panel-head");
      if (!head) return;
      let countEl = head.querySelector("[data-hide-selected-count]");
      if (checkedCount > 0) {
        if (!countEl) {
          countEl = document.createElement("p");
          countEl.className = "stock-visibility-panel-count";
          countEl.setAttribute("data-hide-selected-count", "");
          head.appendChild(countEl);
        }
        countEl.textContent = `${checkedCount} selected`;
      } else if (countEl) {
        countEl.remove();
      }
    };

    const clearChecks = (inputs) => {
      inputs.forEach((input) => {
        input.checked = false;
      });
    };

    const syncPanels = ({ clearUnused = false } = {}) => {
      const mode =
        visibilityForm.querySelector("input[name=hide_stock_mode]:checked")
          ?.value || "show";

      // One mode at a time: drop picks that do not belong to the active mode.
      if (clearUnused) {
        if (mode === "show" || mode === "all") {
          clearChecks(categoryChecks);
          clearChecks(itemChecks);
        } else if (mode === "category") {
          clearChecks(itemChecks);
        } else if (mode === "items") {
          clearChecks(categoryChecks);
        }
      }

      panels.forEach((panel) => {
        const key = panel.getAttribute("data-hide-panel");
        panel.hidden = key !== mode;
        if (key === "category") {
          syncPanelCount(
            panel,
            visibilityForm.querySelectorAll(
              "input[name=hide_stock_categories]:checked"
            ).length
          );
        }
        if (key === "items") {
          syncPanelCount(
            panel,
            visibilityForm.querySelectorAll(
              "input[name=hide_stock_item_ids]:checked"
            ).length
          );
        }
      });

      modeInputs.forEach((input) => {
        input.closest(".stock-visibility-mode")?.classList.toggle(
          "is-selected",
          input.checked
        );
      });
    };

    modeInputs.forEach((input) =>
      input.addEventListener("change", () => syncPanels({ clearUnused: true }))
    );
    [...categoryChecks, ...itemChecks].forEach((input) => {
      input.addEventListener("change", () => syncPanels());
    });

    const search = visibilityForm.querySelector("[data-hide-item-search]");
    search?.addEventListener("input", () => {
      const query = search.value.trim().toLowerCase();
      visibilityForm.querySelectorAll("[data-hide-item]").forEach((row) => {
        const hay = (row.getAttribute("data-search") || "").toLowerCase();
        row.hidden = Boolean(query) && !hay.includes(query);
      });
    });
    syncPanels();
  }

  if (root.getAttribute("data-can-edit") !== "1") return;

  const readCookie = (name) => {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) {
      return decodeURIComponent(parts.pop().split(";").shift() || "");
    }
    return "";
  };

  const csrfToken =
    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    readCookie("csrftoken") ||
    "";

  const enabledCountEl = root.querySelector("[data-stock-settings-enabled-count]");

  const syncEnabledCount = () => {
    if (!enabledCountEl) return;
    enabledCountEl.textContent = String(
      root.querySelectorAll("[data-stock-setting-toggle]:checked").length
    );
  };

  const setStateLabel = (input, enabled) => {
    const label = input.closest(".perm-switch")?.querySelector(".perm-switch-state");
    if (label) label.textContent = enabled ? "On" : "Off";
    input.closest(".perm-switch")?.classList.toggle("is-denied", !enabled);
  };

  const postSettings = async (body) => {
    const response = await fetch(window.location.href, {
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
      throw new Error(data.error || "Could not save setting.");
    }
    return data;
  };

  root.querySelectorAll("[data-stock-setting-toggle]").forEach((input) => {
    setStateLabel(input, input.checked);
    input.addEventListener("change", async () => {
      const enabled = input.checked;
      const previous = !enabled;
      setStateLabel(input, enabled);
      input.disabled = true;
      input.closest(".perm-switch")?.classList.add("is-saving");

      const body = new URLSearchParams({
        mode: "settings",
        action: "toggle_stock_setting",
        field: input.dataset.field || "",
        enabled: enabled ? "1" : "0",
        ajax: "1",
      });

      try {
        const data = await postSettings(body);
        const next = Boolean(data.enabled);
        input.checked = next;
        setStateLabel(input, next);
        syncEnabledCount();
      } catch (error) {
        input.checked = previous;
        setStateLabel(input, previous);
        syncEnabledCount();
        window.alert(error.message || "Could not save setting.");
      } finally {
        input.disabled = false;
        input.closest(".perm-switch")?.classList.remove("is-saving");
      }
    });
  });

  syncEnabledCount();
})();
