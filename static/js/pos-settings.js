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

  function syncTaxRow(enabled) {
    if (!taxRow) return;
    taxRow.hidden = !enabled;
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
      throw new Error(data.error || "Could not save setting.");
    }
    return data;
  }

  root.querySelectorAll("[data-pos-toggle]").forEach((input) => {
    setStateLabel(input, input.checked);
    if (input.dataset.field === "enable_tax") {
      syncTaxRow(input.checked);
    }

    input.addEventListener("change", async () => {
      const enabled = input.checked;
      const previous = !enabled;
      setStateLabel(input, enabled);
      if (input.dataset.field === "enable_tax") {
        syncTaxRow(enabled);
      }
      input.disabled = true;
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
        if (input.dataset.field === "enable_tax") {
          syncTaxRow(Boolean(data.enabled));
        }
        syncEnabledCount();
      } catch (error) {
        input.checked = previous;
        setStateLabel(input, previous);
        if (input.dataset.field === "enable_tax") {
          syncTaxRow(previous);
        }
        syncEnabledCount();
        window.alert(error.message || "Could not save setting.");
      } finally {
        input.disabled = false;
        input.closest(".perm-switch")?.classList.remove("is-saving");
      }
    });
  });

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
})();
