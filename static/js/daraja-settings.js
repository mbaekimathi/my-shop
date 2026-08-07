(() => {
  const root = document.querySelector("[data-daraja-settings]");
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

  const form = root.querySelector("[data-daraja-form]");
  const stkToggle = root.querySelector("[data-daraja-stk-toggle]");
  const stkState = root.querySelector("[data-daraja-stk-state]");
  const statusEl = root.querySelector("[data-daraja-status]");
  const messageEl = root.querySelector("[data-daraja-message]");
  const stkMessageEl = root.querySelector("[data-daraja-stk-message]");
  const readyHint = root.querySelector("[data-daraja-ready-hint]");
  const saveBtn = root.querySelector("[data-daraja-save]");

  function setMessage(text, { error = false } = {}) {
    if (!messageEl) return;
    messageEl.hidden = !text;
    messageEl.textContent = text || "";
    messageEl.classList.toggle("is-error", Boolean(error));
    messageEl.classList.toggle("is-ok", Boolean(text) && !error);
  }

  function setStkMessage(text, { error = false } = {}) {
    if (!stkMessageEl) return;
    stkMessageEl.hidden = !text;
    stkMessageEl.textContent = text || "";
    stkMessageEl.classList.toggle("is-error", Boolean(error));
    stkMessageEl.classList.toggle("is-ok", Boolean(text) && !error);
  }

  function setStkLabel(enabled) {
    if (stkState) stkState.textContent = enabled ? "Enabled" : "Disabled";
    stkToggle?.closest(".perm-switch")?.classList.toggle("is-denied", !enabled);
  }

  function renderStatus(data) {
    if (!statusEl) return;
    const env = data.environment_label || data.environment || "Daraja";
    if (data.credentials_valid) {
      statusEl.innerHTML = `<span class="daraja-status-pill is-ok">Verified · ${env}</span>`;
    } else if (data.last_error) {
      statusEl.innerHTML = `<span class="daraja-status-pill is-bad">Not verified</span><em>${data.last_error}</em>`;
    } else {
      statusEl.innerHTML =
        `<span class="daraja-status-pill">Not configured</span><em>Enter credentials and save to verify.</em>`;
    }
    root.dataset.darajaCanEnableStk =
      data.credentials_valid && data.has_callback_base ? "1" : "0";
    if (readyHint) {
      if (data.is_ready_for_stk) {
        readyHint.textContent =
          "Ready — STK Push can run on shop checkout and client account pay.";
      } else if (!data.has_callback_base) {
        readyHint.textContent =
          "STK stays off until a public HTTPS callback is available. Keep ngrok http 8000 running — it is detected automatically even on localhost.";
      } else if (data.credentials_valid && !data.enable_stk_push) {
        readyHint.textContent =
          "Credentials verified. Turn this on to activate STK Push.";
      } else if (!data.credentials_valid) {
        readyHint.textContent =
          "Save and verify credentials below before enabling.";
      } else {
        readyHint.textContent = "STK Push is not ready yet.";
      }
    }
    const callbackInput = root.querySelector("[data-daraja-callback-base]");
    if (callbackInput && data.callback_base_url) {
      callbackInput.value = data.callback_base_url;
    }
    const fullEl = root.querySelector("[data-daraja-callback-full]");
    const hintEm = root.querySelector(".daraja-field-hint");
    if (data.callback_url && hintEm) {
      if (fullEl) {
        fullEl.textContent = data.callback_url;
      } else {
        const code = document.createElement("code");
        code.setAttribute("data-daraja-callback-full", "");
        code.textContent = data.callback_url;
        hintEm.appendChild(document.createTextNode(" Full callback path: "));
        hintEm.appendChild(code);
      }
    }
  }

  async function postAction(body) {
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
      const error = new Error(data.error || "Could not save Daraja settings.");
      error.payload = data;
      throw error;
    }
    return data;
  }

  root.querySelectorAll("[data-daraja-environment]").forEach((input) => {
    input.addEventListener("change", () => {
      root.querySelectorAll(".daraja-env-option").forEach((option) => {
        option.classList.toggle(
          "is-active",
          option.querySelector("input")?.checked
        );
      });
    });
  });

  if (stkToggle) {
    setStkLabel(stkToggle.checked);
    stkToggle.addEventListener("change", async () => {
      const enabled = stkToggle.checked;
      const previous = !enabled;
      setStkLabel(enabled);
      setStkMessage("");
      if (
        enabled &&
        root.dataset.darajaCanEnableStk === "0" &&
        !root.dataset.darajaForceToggle
      ) {
        stkToggle.checked = previous;
        setStkLabel(previous);
        setStkMessage(
          "Start ngrok with: ngrok http 8000 — then refresh this page and enable STK.",
          { error: true }
        );
        return;
      }
      stkToggle.disabled = true;
      stkToggle.closest(".perm-switch")?.classList.add("is-saving");
      try {
        const data = await postAction(
          new URLSearchParams({
            action: "toggle_stk_push",
            enabled: enabled ? "1" : "0",
          })
        );
        stkToggle.checked = Boolean(data.enable_stk_push);
        setStkLabel(Boolean(data.enable_stk_push));
        renderStatus(data);
        setStkMessage(
          data.enable_stk_push ? "STK Push enabled." : "STK Push disabled."
        );
      } catch (err) {
        stkToggle.checked = previous;
        setStkLabel(previous);
        if (err.payload) renderStatus(err.payload);
        setStkMessage(err.message || "Could not update STK Push.", {
          error: true,
        });
      } finally {
        stkToggle.disabled = false;
        stkToggle.closest(".perm-switch")?.classList.remove("is-saving");
      }
    });
  }

  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setMessage("Verifying with Safaricom…");
      if (saveBtn) saveBtn.disabled = true;
      const body = new URLSearchParams(new FormData(form));
      body.set("action", "save_daraja_credentials");
      try {
        const data = await postAction(body);
        renderStatus(data);
        if (stkToggle) {
          stkToggle.checked = Boolean(data.enable_stk_push);
          setStkLabel(Boolean(data.enable_stk_push));
        }
        ["consumer_key", "consumer_secret", "passkey"].forEach((name) => {
          const input = form.querySelector(`[name="${name}"]`);
          if (input) {
            input.value = "";
            input.required = false;
            if (name === "consumer_key") {
              input.placeholder = "Saved — enter a new key to replace";
            } else if (name === "consumer_secret") {
              input.placeholder = "Saved — enter a new secret to replace";
            } else {
              input.placeholder = "Saved — enter a new passkey to replace";
            }
          }
        });
        setMessage(data.message || "Daraja credentials verified and saved.");
      } catch (err) {
        if (err.payload) renderStatus(err.payload);
        setMessage(err.message || "Verification failed.", { error: true });
      } finally {
        if (saveBtn) saveBtn.disabled = false;
      }
    });
  }
})();
