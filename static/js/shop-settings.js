(() => {
  const root = document.querySelector("[data-shop-settings]");
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

  // Shared by pos-settings.js / receipt-settings.js: after a settings save the
  // response carries `custom_fields` — show/hide the "Custom" tags live.
  function syncCustomTags(fields) {
    if (!Array.isArray(fields)) return;
    const custom = new Set(fields);
    root.querySelectorAll("[data-custom-tag]").forEach((tag) => {
      const names = (tag.dataset.customTag || "")
        .split(",")
        .map((name) => name.trim())
        .filter(Boolean);
      tag.hidden = !names.some((name) => custom.has(name));
    });
  }
  window.myshopSyncCustomTags = syncCustomTags;

  const overrideInput = root.querySelector("[data-shop-override]");
  const body = root.querySelector("[data-shop-settings-body]");
  const statusEl = root.querySelector("[data-shop-override-status]");
  const modeLabel = root.querySelector("[data-shop-mode-label]");
  const shopName =
    document.querySelector(".stock-kicker")?.textContent?.split("·")[0]?.trim() ||
    "this shop";

  function setOverrideUi(enabled) {
    root.classList.toggle("is-using-defaults", !enabled);
    if (body) {
      if (enabled) {
        body.removeAttribute("inert");
      } else {
        body.setAttribute("inert", "");
      }
      body.querySelectorAll("input, select, textarea, button").forEach((el) => {
        if (el.hasAttribute("data-shop-override")) return;
        el.disabled = !enabled;
      });
    }
    const state = overrideInput
      ?.closest(".perm-switch")
      ?.querySelector(".perm-switch-state");
    if (state) state.textContent = enabled ? "On" : "Off";
    if (statusEl) {
      statusEl.textContent = enabled
        ? `Custom settings are active for ${shopName}.`
        : "Using company defaults.";
    }
    if (modeLabel) modeLabel.textContent = enabled ? "Custom" : "Default";
  }

  async function postSettings(params) {
    const response = await fetch(window.location.pathname, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrfToken,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams(params),
      credentials: "same-origin",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Could not save setting.");
    }
    return data;
  }

  if (!overrideInput) return;

  setOverrideUi(overrideInput.checked);

  overrideInput.addEventListener("change", async () => {
    const enabled = overrideInput.checked;
    const previous = !enabled;
    setOverrideUi(enabled);
    overrideInput.disabled = true;
    overrideInput.closest(".perm-switch")?.classList.add("is-saving");

    try {
      const data = await postSettings({
        action: "set_shop_override",
        enabled: enabled ? "1" : "0",
      });
      setOverrideUi(Boolean(data.override_enabled));
      overrideInput.checked = Boolean(data.override_enabled);
      // Reload so form values match seeded company defaults when turning on.
      window.location.reload();
    } catch (error) {
      overrideInput.checked = previous;
      setOverrideUi(previous);
      window.alert(error.message || "Could not save setting.");
    } finally {
      overrideInput.disabled = false;
      overrideInput.closest(".perm-switch")?.classList.remove("is-saving");
    }
  });

  const agentBtn = root.querySelector("[data-print-agent-generate]");
  if (agentBtn) {
    agentBtn.addEventListener("click", async () => {
      agentBtn.disabled = true;
      try {
        await postSettings({ action: "print_agent_token" });
        // Reload to show the new token and the setup block.
        window.location.reload();
      } catch (error) {
        window.alert(error.message || "Could not save token.");
        agentBtn.disabled = false;
      }
    });
  }
})();
