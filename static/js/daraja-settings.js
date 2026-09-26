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
  const nexusForm = root.querySelector("[data-nexus-form]");
  const stkToggle = root.querySelector("[data-daraja-stk-toggle]");
  const stkState = root.querySelector("[data-daraja-stk-state]");
  const statusEl = root.querySelector("[data-daraja-status]");
  const nexusStatusEl = root.querySelector("[data-nexus-status]");
  const messageEl = root.querySelector("[data-daraja-message]");
  const nexusMessageEl = root.querySelector("[data-nexus-message]");
  const stkMessageEl = root.querySelector("[data-daraja-stk-message]");
  const readyHint = root.querySelector("[data-daraja-ready-hint]");
  const saveBtn = root.querySelector("[data-daraja-save]");
  const nexusSaveBtn = root.querySelector("[data-nexus-save]");
  const darajaPanel = root.querySelector('[data-stk-panel="daraja"]');
  const nexusPanel = root.querySelector('[data-stk-panel="nexus"]');
  const providerSection = root.querySelector("[data-stk-provider-section]");
  const followSections = root.querySelectorAll("[data-stk-follow-section]");
  const envHiddenInput = root.querySelector("[data-daraja-environment-value]");
  const providerPicker = root.querySelector("[data-stk-provider-picker]");
  const providerIntro = root.querySelector("[data-stk-provider-intro]");
  const sandboxDarajaNote = root.querySelector("[data-stk-sandbox-daraja-note]");

  function selectedEnvironment() {
    return root.querySelector("[data-daraja-environment]:checked")?.value || "";
  }

  function isProductionEnv(data) {
    if (data && data.environment) {
      return data.environment === "production";
    }
    return selectedEnvironment() === "production";
  }

  function updateEnvironmentLabels(label) {
    const text = label || "this environment";
    root.querySelectorAll("[data-daraja-env-label]").forEach((el) => {
      el.textContent = text;
    });
  }

  function syncEnvironmentHidden() {
    const value = selectedEnvironment();
    if (envHiddenInput && value) {
      envHiddenInput.value = value;
    }
  }

  function revealFollowSections() {
    followSections.forEach((section) => {
      section.hidden = false;
    });
  }

  function revealAfterEnvironment() {
    syncSandboxProviderUI(null);
    if (providerSection && selectedEnvironment() && isProductionEnv(null)) {
      providerSection.hidden = false;
    }
    const hasProvider =
      root.querySelector("[data-stk-provider]:checked") ||
      selectedEnvironment() === "sandbox";
    if (hasProvider) {
      revealFollowSections();
    }
  }

  /** Instant UI when Sandbox/Production or provider changes (no server wait). */
  function applyClientSelection() {
    syncEnvironmentHidden();
    syncSandboxProviderUI(null);
    revealAfterEnvironment();
    syncProviderPanels(null);
    const label = root
      .querySelector("[data-daraja-environment]:checked")
      ?.closest(".daraja-env-option")
      ?.querySelector("strong")?.textContent;
    if (label) updateEnvironmentLabels(label);
    const snapshot = {
      environment: selectedEnvironment(),
      stk_provider:
        root.querySelector("[data-stk-provider]:checked")?.value || "daraja",
      uses_nexus_stk: usesNexus(null),
      nexus_stk_allowed: isProductionEnv(null),
      credentials_valid: root.dataset.darajaCredentialsValid === "1",
      nexus_key_verified: root.dataset.darajaNexusVerified === "1",
      has_callback_base: root.dataset.darajaHasCallback === "1",
      enable_stk_push: Boolean(stkToggle?.checked),
      is_ready_for_stk: false,
    };
    renderReadyHint(snapshot);
    root.dataset.darajaCanEnableStk = canEnableStk(snapshot) ? "1" : "0";
  }

  function mergeServerState(data) {
    if (!data) return;
    if (typeof data.credentials_valid === "boolean") {
      root.dataset.darajaCredentialsValid = data.credentials_valid ? "1" : "0";
    }
    if (typeof data.nexus_key_verified === "boolean") {
      root.dataset.darajaNexusVerified = data.nexus_key_verified ? "1" : "0";
    }
    if (typeof data.has_callback_base === "boolean") {
      root.dataset.darajaHasCallback = data.has_callback_base ? "1" : "0";
    }
    renderStatus(data);
  }

  function usesNexus(data) {
    if (data) {
      if (data.nexus_stk_allowed === false || data.environment === "sandbox") {
        return false;
      }
      return (
        data.uses_nexus_stk === true ||
        data.stk_provider === "nexus" ||
        data.stk_provider === "nexus_rushtech"
      );
    }
    if (!isProductionEnv(null)) return false;
    return Boolean(
      root.querySelector('[data-stk-provider][value="nexus"]:checked') ||
        root.querySelector('[data-stk-provider][value="nexus_rushtech"]:checked')
    );
  }

  function syncSandboxProviderUI(data) {
    const production = isProductionEnv(data);
    if (providerPicker) providerPicker.hidden = !production;
    if (providerSection && !production) providerSection.hidden = true;
    if (sandboxDarajaNote) sandboxDarajaNote.hidden = true;
    if (providerIntro) providerIntro.hidden = true;
    const darajaInput = root.querySelector('[data-stk-provider][value="daraja"]');
    const nexusInput = root.querySelector('[data-stk-provider][value="nexus"]');
    if (nexusInput) nexusInput.disabled = !production;
    if (!production && darajaInput) {
      darajaInput.checked = true;
      darajaInput.closest(".daraja-env-option")?.classList.add("is-active");
      nexusInput?.closest(".daraja-env-option")?.classList.remove("is-active");
    }
  }

  function isHostedDeploy() {
    return root.dataset.darajaHosted === "1";
  }

  function canEnableStk(data) {
    if (usesNexus(data)) {
      return Boolean(data.nexus_key_verified);
    }
    return Boolean(data.credentials_valid && data.has_callback_base);
  }

  function stkEnableBlockMessage(data) {
    if (usesNexus(data || null)) {
      return "Save and verify your Nexus collection API key first.";
    }
    const snap = data || {
      credentials_valid: root.dataset.darajaCredentialsValid === "1",
      has_callback_base: root.dataset.darajaHasCallback === "1",
    };
    if (!snap.credentials_valid) {
      return "Save and verify Daraja credentials below first.";
    }
    if (isHostedDeploy()) {
      return (
        "Use your live HTTPS domain (callback is auto-detected when you open this page), " +
        "or set DARAJA_CALLBACK_BASE_URL or PUBLIC_SITE_URL in .env, refresh, then enable STK."
      );
    }
    return (
      "Local dev: ngrok http 8000, open the ngrok HTTPS link, refresh. " +
      "Production on hosting: use your live HTTPS domain — ngrok is not required."
    );
  }

  function setMessage(text, { error = false } = {}) {
    if (!messageEl) return;
    messageEl.hidden = !text;
    messageEl.textContent = text || "";
    messageEl.classList.toggle("is-error", Boolean(error));
    messageEl.classList.toggle("is-ok", Boolean(text) && !error);
  }

  function setNexusMessage(text, { error = false } = {}) {
    if (!nexusMessageEl) return;
    nexusMessageEl.hidden = !text;
    nexusMessageEl.textContent = text || "";
    nexusMessageEl.classList.toggle("is-error", Boolean(error));
    nexusMessageEl.classList.toggle("is-ok", Boolean(text) && !error);
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

  function syncProviderPanels(data) {
    const nexus = data
      ? usesNexus(data)
      : Boolean(
          root.querySelector('[data-stk-provider][value="nexus"]:checked') ||
            root.querySelector('[data-stk-provider][value="nexus_rushtech"]:checked')
        );
    if (darajaPanel) darajaPanel.hidden = nexus;
    if (nexusPanel) nexusPanel.hidden = !nexus;
    root.querySelectorAll("[data-stk-provider]").forEach((input) => {
      const option = input.closest(".daraja-env-option");
      if (option) {
        option.classList.toggle("is-active", input.checked);
      }
    });
  }

  function renderNexusStatus(data) {
    if (!nexusStatusEl) return;
    if (data.nexus_key_verified) {
      nexusStatusEl.innerHTML = `<span class="daraja-status-pill is-ok">Verified</span>`;
    } else if (usesNexus(data) && data.last_error) {
      nexusStatusEl.innerHTML = `<span class="daraja-status-pill is-bad">Not verified</span><em>${data.last_error}</em>`;
    } else {
      nexusStatusEl.innerHTML = `<span class="daraja-status-pill">Not set</span>`;
    }
    const collectionInput = root.querySelector("[data-nexus-collection-id]");
    if (collectionInput && data.nexus_collection_id) {
      collectionInput.value = data.nexus_collection_id;
    }
  }

  function renderReadyHint(data) {
    if (!readyHint) return;
    if (data.is_ready_for_stk) {
      readyHint.textContent = "Ready.";
    } else if (!usesNexus(data) && !data.has_callback_base) {
      readyHint.textContent = isHostedDeploy()
        ? "Needs your live HTTPS domain (or DARAJA_CALLBACK_BASE_URL in .env)."
        : "Needs public HTTPS (hosted domain for Production, or ngrok locally).";
    } else if (!usesNexus(data) && !data.credentials_valid) {
      readyHint.textContent = "Save credentials below.";
    } else if (usesNexus(data) && !data.nexus_key_verified) {
      readyHint.textContent = "Save Nexus key below.";
    } else {
      readyHint.textContent = "Configure below. Shop cart: Settings → POS.";
    }
  }

  function renderStatus(data) {
    const env = data.environment_label || data.environment || "Daraja";
    if (statusEl) {
      if (data.credentials_valid) {
        statusEl.innerHTML = `<span class="daraja-status-pill is-ok">Verified</span>`;
      } else if (data.last_error && !usesNexus(data)) {
        statusEl.innerHTML = `<span class="daraja-status-pill is-bad">Not verified</span><em>${data.last_error}</em>`;
      } else {
        statusEl.innerHTML = `<span class="daraja-status-pill">Not set</span>`;
      }
    }
    root.dataset.darajaCanEnableStk = canEnableStk(data) ? "1" : "0";
    renderReadyHint(data);
    renderNexusStatus(data);
    updateEnvironmentLabels(data.environment_label);
    syncEnvironmentHidden();
    syncSandboxProviderUI(data);
    revealAfterEnvironment();
    syncProviderPanels(data);
    const callbackInput = root.querySelector("[data-daraja-callback-base]");
    if (callbackInput && data.callback_base_url) {
      callbackInput.value = data.callback_base_url;
    const fullEl = root.querySelector("[data-daraja-callback-full]");
    if (fullEl && data.callback_url) {
      fullEl.textContent = data.callback_url;
      fullEl.hidden = false;
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

  let envSaveTimer = 0;
  let envSaveInFlight = null;

  function queueEnvironmentSave(environment) {
    window.clearTimeout(envSaveTimer);
    envSaveTimer = window.setTimeout(() => {
      if (envSaveInFlight) envSaveInFlight.abort();
      const controller = new AbortController();
      envSaveInFlight = controller;
      fetch(window.location.pathname, {
        method: "POST",
        signal: controller.signal,
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfToken,
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({
          action: "save_daraja_environment",
          environment,
        }),
        credentials: "same-origin",
      })
        .then((response) => response.json().then((data) => ({ response, data })))
        .then(({ response, data }) => {
          if (!response.ok || !data.ok) {
            const error = new Error(data.error || "Could not save environment.");
            error.payload = data;
            throw error;
          }
          mergeServerState(data);
        })
        .catch((err) => {
          if (err.name === "AbortError") return;
          if (err.payload) mergeServerState(err.payload);
          setStkMessage(err.message || "Could not save environment.", {
            error: true,
          });
        })
        .finally(() => {
          if (envSaveInFlight === controller) envSaveInFlight = null;
        });
    }, 120);
  }

  root.querySelectorAll("[data-daraja-environment]").forEach((input) => {
    input.addEventListener("change", () => {
      const picker = input.closest(".daraja-env-picker");
      picker?.querySelectorAll(".daraja-env-option").forEach((option) => {
        option.classList.toggle(
          "is-active",
          option.querySelector("input")?.checked
        );
      });
      applyClientSelection();
      queueEnvironmentSave(input.value);
    });
  });

  root.querySelectorAll("[data-stk-provider]").forEach((input) => {
    input.addEventListener("change", async () => {
      if (!input.checked) return;
      if (!selectedEnvironment()) {
        setStkMessage("Choose Sandbox or Production first.", { error: true });
        input.checked = false;
        return;
      }
      if (
        (input.value === "nexus" || input.value === "nexus_rushtech") &&
        !isProductionEnv(null)
      ) {
        setStkMessage(
          "Sandbox uses Safaricom Daraja test credentials only.",
          { error: true }
        );
        input.checked = false;
        const daraja = root.querySelector('[data-stk-provider][value="daraja"]');
        if (daraja) daraja.checked = true;
        syncProviderPanels(null);
        return;
      }
      applyClientSelection();
      setStkMessage("");
      postAction(
        new URLSearchParams({
          action: "save_stk_provider",
          stk_provider: input.value,
        })
      )
        .then((data) => {
          mergeServerState(data);
          if (stkToggle) {
            stkToggle.checked = Boolean(data.enable_stk_push);
            setStkLabel(Boolean(data.enable_stk_push));
          }
        })
        .catch((err) => {
          if (err.payload) mergeServerState(err.payload);
          setStkMessage(err.message || "Could not switch STK provider.", {
            error: true,
          });
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
        setStkMessage(stkEnableBlockMessage(null), { error: true });
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
      syncEnvironmentHidden();
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

  applyClientSelection();

  if (nexusForm) {
    nexusForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      setNexusMessage("Verifying with Nexus collections…");
      if (nexusSaveBtn) nexusSaveBtn.disabled = true;
      const body = new URLSearchParams(new FormData(nexusForm));
      body.set("action", "save_nexus_credentials");
      try {
        const data = await postAction(body);
        renderStatus(data);
        if (stkToggle) {
          stkToggle.checked = Boolean(data.enable_stk_push);
          setStkLabel(Boolean(data.enable_stk_push));
        }
        const keyInput = nexusForm.querySelector("[name=nexus_api_key]");
        if (keyInput) {
          keyInput.value = "";
          keyInput.required = false;
          keyInput.placeholder = "Saved — enter a new key to replace";
        }
        setNexusMessage(
          data.message || "Nexus collection API key verified and saved."
        );
      } catch (err) {
        if (err.payload) renderStatus(err.payload);
        setNexusMessage(err.message || "Verification failed.", { error: true });
      } finally {
        if (nexusSaveBtn) nexusSaveBtn.disabled = false;
      }
    });
  }
})();
