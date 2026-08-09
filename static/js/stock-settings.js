(() => {
  const root = document.querySelector("[data-stock-settings]");
  if (!root) return;
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
