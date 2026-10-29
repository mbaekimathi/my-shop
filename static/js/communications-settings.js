(() => {
  const root = document.querySelector("[data-comms-settings]");
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

  function showFormMessage(form, text, isError) {
    const el = form.querySelector("[data-comms-message]");
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
    el.classList.toggle("is-error", Boolean(isError));
    el.classList.toggle("is-ok", Boolean(text) && !isError);
  }

  function setStatus(selector, configured, hint, okLabel, badLabel) {
    const el = root.querySelector(selector);
    if (!el) return;
    if (configured) {
      el.innerHTML = `<span class="daraja-status-pill is-ok">${okLabel || "API configured"}</span>`;
    } else {
      el.innerHTML = `<span class="daraja-status-pill">${badLabel || "Not configured"}</span><em>${hint}</em>`;
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
      throw new Error(data.error || "Could not save setting.");
    }
    return data;
  }

  root.querySelectorAll("[data-comms-sms-provider]").forEach((input) => {
    input.addEventListener("change", () => {
      root.querySelectorAll(".daraja-env-option").forEach((option) => {
        const radio = option.querySelector("[data-comms-sms-provider]");
        option.classList.toggle("is-active", Boolean(radio?.checked));
      });
    });
  });

  root.querySelectorAll("[data-comms-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const kind = form.getAttribute("data-comms-form");
      const submitBtn = form.querySelector('button[type="submit"]');
      showFormMessage(form, "");
      if (submitBtn) submitBtn.disabled = true;
      try {
        const data = await postSettings(new URLSearchParams(new FormData(form)));
        if (kind === "twilio" || kind === "whatsapp") {
          setStatus(
            "[data-comms-whatsapp-status]",
            Boolean(data.has_twilio_credentials || data.has_whatsapp_credentials),
            "Enter Account SID, Auth Token, and a From number, then save."
          );
        } else if (kind === "message") {
          setStatus(
            "[data-comms-message-status]",
            Boolean(data.message_from_name),
            "Enter a sender name, then save."
          );
        } else if (kind === "sms") {
          setStatus(
            "[data-comms-sms-status]",
            Boolean(data.has_sms_credentials),
            "Enter API key and sender ID, then save.",
            "Connected",
            "Not connected"
          );
        }
        showFormMessage(form, data.message || "API settings saved.");
      } catch (error) {
        showFormMessage(form, error.message || "Could not save.", true);
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  });

  root.querySelectorAll("[data-comms-channel-toggle]").forEach((input) => {
    input.addEventListener("change", async () => {
      const form = input.closest("form");
      if (!form) return;
      const stateLabel = form.querySelector("[data-comms-channel-state]");
      input.disabled = true;
      showFormMessage(form, "");
      try {
        const body = new URLSearchParams(new FormData(form));
        body.set("enabled", input.checked ? "1" : "0");
        const data = await postSettings(body);
        if (stateLabel) stateLabel.textContent = input.checked ? "On" : "Off";
        const status = root.querySelector("[data-comms-sms-channel-status]");
        if (status) {
          status.innerHTML = input.checked
            ? '<span class="daraja-status-pill is-ok">SMS channel on</span><em>Triggered drafts and broadcasts can send text messages.</em>'
            : '<span class="daraja-status-pill">SMS channel off</span><em>Nothing sends SMS while this is off — drafts stay queued or skipped.</em>';
        }
        showFormMessage(form, data.message || "Saved.");
      } catch (error) {
        input.checked = !input.checked;
        showFormMessage(form, error.message || "Could not save.", true);
      } finally {
        input.disabled = false;
      }
    });
  });

  root.querySelectorAll("[data-comms-test-connection]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const form = root.querySelector('[data-comms-form="sms"]');
      if (!form) return;
      btn.disabled = true;
      showFormMessage(form, "Testing connection…");
      try {
        const body = new URLSearchParams(new FormData(form));
        body.set("action", "test_sms_connection");
        const data = await postSettings(body);
        if (data.connected) {
          setStatus("[data-comms-sms-status]", true, "", "Connected");
        } else if (!data.skipped) {
          setStatus(
            "[data-comms-sms-status]",
            false,
            "Check the credentials and try again.",
            "Connected",
            "Connection failed"
          );
        }
        showFormMessage(form, data.message || "", !data.connected && !data.skipped);
      } catch (error) {
        showFormMessage(form, error.message || "Could not test connection.", true);
      } finally {
        btn.disabled = false;
      }
    });
  });
})();
