(() => {
  const root = document.querySelector("[data-developer-subscriptions]");
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

  const form = root.querySelector("[data-developer-form]");
  const messageEl = root.querySelector("[data-developer-message]");
  const saveBtn = root.querySelector("[data-developer-save]");

  function setMessage(text, { error = false } = {}) {
    if (!messageEl) return;
    messageEl.hidden = !text;
    messageEl.textContent = text || "";
    messageEl.classList.toggle("is-error", Boolean(error));
    messageEl.classList.toggle("is-ok", Boolean(text) && !error);
  }

  function syncRadioCards(attr) {
    root.querySelectorAll(`[${attr}]`).forEach((input) => {
      input.closest(".daraja-env-option")?.classList.toggle(
        "is-active",
        input.checked
      );
    });
  }

  root.querySelectorAll("[data-developer-cadence]").forEach((input) => {
    input.addEventListener("change", () => syncRadioCards("data-developer-cadence"));
  });
  root.querySelectorAll("[data-developer-location]").forEach((input) => {
    input.addEventListener("change", () => syncRadioCards("data-developer-location"));
  });

  root.querySelectorAll(".perm-switch-input").forEach((input) => {
    input.addEventListener("change", () => {
      const wrap = input.closest(".perm-switch");
      const state = wrap?.querySelector(".perm-switch-state");
      wrap?.classList.toggle("is-denied", !input.checked);
      if (!state) return;
      if (input.hasAttribute("data-developer-prompts-enabled")) {
        state.textContent = input.checked ? "Enabled" : "Disabled";
      }
      if (input.hasAttribute("data-developer-allow-dismiss")) {
        state.textContent = input.checked ? "Allowed" : "Hidden";
      }
    });
  });

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    setMessage("");
    if (saveBtn) saveBtn.disabled = true;
    const body = new FormData(form);
    if (!form.querySelector("[data-developer-prompts-enabled]")?.checked) {
      body.set("prompts_enabled", "0");
    }
    if (!form.querySelector("[data-developer-allow-dismiss]")?.checked) {
      body.set("allow_dismiss", "0");
    }
    try {
      const response = await fetch(window.location.pathname, {
        method: "POST",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          Accept: "application/json",
          "X-CSRFToken": csrfToken,
        },
        body,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        setMessage(data.error || "Could not save settings.", { error: true });
        return;
      }
      setMessage(data.message || "Saved.");
    } catch (_err) {
      setMessage("Network error. Try again.", { error: true });
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  });
})();
