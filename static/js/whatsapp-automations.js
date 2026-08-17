(() => {
  const root = document.querySelector("[data-wa-auto]");
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

  const messageEl = root.querySelector("[data-wa-message]");
  const countEls = root.querySelectorAll("[data-wa-people-count]");
  const peopleBody = root.querySelector("[data-wa-people-body]");
  const peopleEmpty = root.querySelector("[data-wa-people-empty]");
  const peopleMore = root.querySelector("[data-wa-people-more]");
  const peopleSearch = root.querySelector("[data-wa-people-search]");
  const selectedCountEl = root.querySelector("[data-wa-selected-count]");
  const sendBtn = root.querySelector("[data-wa-send-website]");
  const sendCatalogueBtn = root.querySelector("[data-wa-send-catalogue]");
  const itemSearch = root.querySelector("[data-wa-item-search]");
  const itemsBody = root.querySelector("[data-wa-items-body]");
  const itemsEmpty = root.querySelector("[data-wa-items-empty]");
  const selectedItemCountEl = root.querySelector("[data-wa-selected-item-count]");
  const dependsEl = root.querySelector("[data-wa-depends]");
  const sendsEl = root.querySelector("[data-wa-sends]");
  const twilioReady = root.dataset.twilioReady === "1";
  const campaignLabels = {
    queued: "Queued",
    sending: "Sending",
    done: "Done",
    cancelled: "Cancelled",
    draft: "Draft",
  };
  const messageLabels = {
    queued: "Queued",
    sent: "Sent",
    delivered: "Delivered",
    viewed: "Viewed",
    failed: "Failed",
    cancelled: "Cancelled",
  };

  let pollTimer = 0;
  let openSendIds = new Set();

  function refreshIcons() {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  }

  function showMessage(text, isError) {
    if (!messageEl) return;
    messageEl.hidden = !text;
    messageEl.textContent = text || "";
    messageEl.classList.toggle("is-error", Boolean(isError));
    messageEl.classList.toggle("is-ok", Boolean(text) && !isError);
  }

  function setStateLabel(input, enabled) {
    const label = input.closest(".perm-switch")?.querySelector(".perm-switch-state");
    if (label) label.textContent = enabled ? "On" : "Off";
  }

  function setCount(value) {
    countEls.forEach((el) => {
      el.textContent = String(value);
    });
  }

  function selectedClientIds() {
    return [...(peopleBody?.querySelectorAll("[data-wa-pick]:checked") || [])]
      .map((input) => input.value)
      .filter(Boolean);
  }

  function visiblePickInputs() {
    return [...(peopleBody?.querySelectorAll(".wa-person") || [])]
      .filter((row) => !row.hidden)
      .map((row) => row.querySelector("[data-wa-pick]"))
      .filter(Boolean);
  }

  function selectedItemIds() {
    return [...(itemsBody?.querySelectorAll("[data-wa-item-pick]:checked") || [])]
      .map((input) => input.value)
      .filter(Boolean);
  }

  function visibleItemInputs() {
    return [...(itemsBody?.querySelectorAll(".wa-item") || [])]
      .filter((row) => !row.hidden)
      .map((row) => row.querySelector("[data-wa-item-pick]"))
      .filter(Boolean);
  }

  function syncSelection() {
    peopleBody?.querySelectorAll(".wa-person").forEach((row) => {
      const box = row.querySelector("[data-wa-pick]");
      row.classList.toggle("is-checked", Boolean(box?.checked));
    });
    itemsBody?.querySelectorAll(".wa-item").forEach((row) => {
      const box = row.querySelector("[data-wa-item-pick]");
      row.classList.toggle("is-checked", Boolean(box?.checked));
    });
    const selected = selectedClientIds().length;
    const selectedItems = selectedItemIds().length;
    if (selectedCountEl) selectedCountEl.textContent = String(selected);
    if (selectedItemCountEl) selectedItemCountEl.textContent = String(selectedItems);
    if (sendBtn) {
      const label = sendBtn.querySelector("span") || sendBtn;
      if (sendBtn.querySelector("span[data-wa-send-label]")) {
        sendBtn.querySelector("[data-wa-send-label]").textContent =
          selected ? `Send to ${selected}` : "Send shop website now";
      }
    }
    if (sendCatalogueBtn) {
      const label = sendCatalogueBtn.querySelector("[data-wa-send-label]");
      if (label) {
        label.textContent =
          selectedItems && selected
            ? `Send ${selectedItems} item(s) to ${selected}`
            : "Send selected items now";
      }
    }
  }

  function setVisibleSelection(checked) {
    visiblePickInputs().forEach((input) => {
      input.checked = checked;
    });
    syncSelection();
  }

  function syncDepends() {
    const master = root.querySelector('[data-field="enable_automations"]');
    const on = Boolean(master?.checked);
    root.dataset.automationsOn = on ? "1" : "0";
    dependsEl?.classList.toggle("is-off", !on);
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function initials(name) {
    const parts = String(name || "")
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
  }

  function formatPhone(phone) {
    const digits = String(phone || "").replace(/\D/g, "");
    if (digits.startsWith("254") && digits.length === 12) {
      return `+254 ${digits.slice(3, 6)} ${digits.slice(6, 9)} ${digits.slice(9)}`;
    }
    if (digits.startsWith("1") && digits.length === 11) {
      return `+${digits[0]} ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7)}`;
    }
    return phone || "";
  }

  function personRow(name, phone, extra = "", options = {}) {
    const safeName = name || "Customer";
    const selectable = Boolean(options.selectable);
    const clientId = options.clientId || "";
    const checked = options.checked !== false;
    const check = selectable
      ? `<input class="wa-person__check" type="checkbox" name="client_ids" value="${escapeHtml(
          String(clientId)
        )}" ${checked ? "checked" : ""} data-wa-pick aria-label="Select ${escapeHtml(safeName)}">`
      : "";
    return `<li class="wa-person${selectable ? "" : " wa-person--static"}${
      selectable && checked ? " is-checked" : ""
    }" data-search="${escapeHtml(`${safeName} ${phone}`.toLowerCase())}">
      ${check}
      <span class="wa-person__avatar" aria-hidden="true">${escapeHtml(initials(safeName))}</span>
      <span class="wa-person__meta">
        <strong>${escapeHtml(safeName)}</strong>
        <em>${escapeHtml(formatPhone(phone))}</em>
      </span>
      ${extra}
    </li>`;
  }

  function filterPeople() {
    const query = (peopleSearch?.value || "").trim().toLowerCase();
    const rows = peopleBody?.querySelectorAll(".wa-person") || [];
    let visible = 0;
    rows.forEach((row) => {
      const hay = row.getAttribute("data-search") || "";
      const match = !query || hay.includes(query);
      row.hidden = !match;
      if (match) visible += 1;
    });
    if (peopleEmpty && !peopleBody?.querySelector(".wa-person")) {
      peopleEmpty.hidden = false;
    } else if (peopleEmpty) {
      peopleEmpty.hidden = visible > 0;
      if (query && visible === 0) {
        peopleEmpty.hidden = false;
        peopleEmpty.textContent = "No names or numbers match that search.";
      } else if (visible === 0) {
        peopleEmpty.textContent = "No matching customers with a phone number.";
      }
    }
    syncSelection();
  }

  function renderPeople(data) {
    const recipients = Array.isArray(data.recipients) ? data.recipients : [];
    const count = data.recipient_count != null ? Number(data.recipient_count) : recipients.length;
    setCount(count);
    if (peopleBody) {
      peopleBody.innerHTML = recipients
        .map((person) =>
          personRow(person.full_name, person.phone, "", {
            selectable: true,
            clientId: person.client_id,
            checked: true,
          })
        )
        .join("");
    }
    if (peopleMore) {
      const shown = recipients.length;
      if (count > shown) {
        peopleMore.hidden = false;
        peopleMore.textContent = `Showing ${shown} of ${count}.`;
      } else {
        peopleMore.hidden = true;
      }
    }
    filterPeople();
  }

  function sendSummary(send) {
    const parts = [];
    if (send.viewed_count) parts.push(`${send.viewed_count} viewed`);
    if (send.delivered_count) parts.push(`${send.delivered_count} delivered`);
    if (send.sent_count) parts.push(`${send.sent_count} sent`);
    if (send.pending_count) parts.push(`${send.pending_count} queued`);
    if (send.cancelled_count) parts.push(`${send.cancelled_count} cancelled`);
    if (send.failed_count) parts.push(`${send.failed_count} failed`);
    if (!parts.length) parts.push(`${send.recipient_count || 0} people`);
    return parts.join(" · ");
  }

  function renderSends(sends) {
    if (!sendsEl) return;
    const rows = Array.isArray(sends) ? sends : [];
    if (!rows.length) {
      sendsEl.innerHTML = `<p class="wa-auto__sends-empty">${escapeHtml(
        root.dataset.sendsEmpty ||
          "No sends yet. Choose an audience, then send the shop website."
      )}</p>`;
      stopPoll();
      return;
    }
    sendsEl.innerHTML = rows
      .map((send) => {
        const people = Array.isArray(send.messages) ? send.messages : [];
        const open =
          openSendIds.has(String(send.id)) ||
          send.status === "queued" ||
          send.status === "sending" ||
          Number(send.failed_count || 0) > 0;
        const peopleRows = people.length
          ? people
              .map((person) =>
                personRow(
                  person.client_name,
                  person.phone,
                  `<span class="wa-person__status">
                    <span class="wa-auto__pill" data-status="${escapeHtml(
                      person.display_status || person.status || ""
                    )}">${escapeHtml(
                    messageLabels[person.display_status] ||
                      person.display_status ||
                      person.status
                  )}</span>
                    ${
                      person.error &&
                      (person.display_status === "failed" || person.status === "failed")
                        ? `<em class="wa-person__error">${escapeHtml(person.error)}</em>`
                        : ""
                    }
                  </span>`,
                  { selectable: false }
                )
              )
              .join("")
          : `<p class="wa-auto__people-empty">No recipients logged.</p>`;
        const tools = [
          send.can_cancel
            ? `<button class="btn btn--ghost" type="button" data-wa-cancel="${send.id}">Cancel send</button>`
            : "",
          send.can_retry
            ? `<button class="btn btn--ghost" type="button" data-wa-retry="${send.id}">Retry failed</button>`
            : "",
        ]
          .filter(Boolean)
          .join("");
        return `<article class="wa-auto__send${open ? " is-open" : ""}" data-send-id="${send.id}">
          <button class="wa-auto__send-head" type="button" data-wa-toggle-send="${send.id}" aria-expanded="${open ? "true" : "false"}">
            <div class="wa-auto__send-copy">
              <strong>Send #${send.id}</strong>
              <p>${escapeHtml(send.body_preview || "Shop website")}</p>
              <p>${escapeHtml(sendSummary(send))}</p>
            </div>
            <span class="wa-auto__send-status" data-status="${escapeHtml(send.status || "")}">${escapeHtml(
              campaignLabels[send.status] || send.status || ""
            )}</span>
          </button>
          <div class="wa-auto__send-body">
            ${tools ? `<div class="wa-auto__send-tools">${tools}</div>` : ""}
            <div class="wa-auto__people">
              <ul class="wa-people">${peopleRows}</ul>
            </div>
          </div>
        </article>`;
      })
      .join("");
    if (rows.some((send) => send.status === "queued" || send.status === "sending") ||
        rows.some((send) =>
          (send.messages || []).some((person) =>
            ["queued", "sent"].includes(person.display_status || person.status || "")
          )
        )) {
      startPoll();
    } else {
      stopPoll();
    }
  }

  async function post(body) {
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
      throw new Error(data.error || "Could not save.");
    }
    return data;
  }

  function startPoll() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      post(new URLSearchParams({ action: "refresh_sends" }))
        .then((data) => renderSends(data.sends))
        .catch(() => {});
    }, 4000);
  }

  function stopPoll() {
    if (!pollTimer) return;
    window.clearInterval(pollTimer);
    pollTimer = 0;
  }

  root.querySelectorAll("[data-wa-toggle]").forEach((input) => {
    setStateLabel(input, input.checked);
    input.addEventListener("change", async () => {
      const enabled = input.checked;
      const previous = !enabled;
      setStateLabel(input, enabled);
      if (input.dataset.field === "enable_automations") syncDepends();
      showMessage("");
      input.disabled = true;
      try {
        await post(
          new URLSearchParams({
            action: "toggle_automation",
            field: input.dataset.field || "",
            enabled: enabled ? "1" : "0",
          })
        );
        showMessage("Saved.");
      } catch (error) {
        input.checked = previous;
        setStateLabel(input, previous);
        if (input.dataset.field === "enable_automations") syncDepends();
        showMessage(error.message || "Could not save.", true);
      } finally {
        input.disabled = false;
      }
    });
  });

  const audienceForm = root.querySelector("[data-wa-audience]");
  let previewTimer = 0;

  async function previewAudience() {
    if (!audienceForm) return;
    const params = new URLSearchParams(new FormData(audienceForm));
    params.set("action", "preview_audience");
    params.delete("client_ids");
    const data = await post(params);
    renderPeople(data);
  }

  audienceForm?.querySelectorAll("select").forEach((select) => {
    select.addEventListener("change", () => {
      window.clearTimeout(previewTimer);
      previewTimer = window.setTimeout(() => {
        previewAudience().catch((error) => {
          showMessage(error.message || "Could not load people.", true);
        });
      }, 200);
    });
  });

  peopleSearch?.addEventListener("input", filterPeople);

  function filterItems() {
    const query = (itemSearch?.value || "").trim().toLowerCase();
    const rows = itemsBody?.querySelectorAll(".wa-item") || [];
    let visible = 0;
    rows.forEach((row) => {
      const hay = row.getAttribute("data-search") || "";
      const match = !query || hay.includes(query);
      row.hidden = !match;
      if (match) visible += 1;
    });
    if (itemsEmpty && !itemsBody?.querySelector(".wa-item")) {
      itemsEmpty.hidden = false;
    } else if (itemsEmpty) {
      itemsEmpty.hidden = visible > 0;
      if (query && visible === 0) {
        itemsEmpty.hidden = false;
        itemsEmpty.textContent = "No items match that search.";
      } else if (visible === 0) {
        itemsEmpty.textContent = "No items to share yet. Register items in Item management.";
      }
    }
    syncSelection();
  }

  function setVisibleItems(checked) {
    visibleItemInputs().forEach((input) => {
      input.checked = checked;
    });
    syncSelection();
  }

  peopleBody?.addEventListener("change", (event) => {
    if (event.target.matches("[data-wa-pick]")) syncSelection();
  });

  peopleBody?.addEventListener("click", (event) => {
    const row = event.target.closest(".wa-person");
    if (!row || !peopleBody.contains(row) || row.classList.contains("wa-person--static")) return;
    if (event.target.closest("[data-wa-pick]")) return;
    const box = row.querySelector("[data-wa-pick]");
    if (!box) return;
    box.checked = !box.checked;
    syncSelection();
  });

  root.querySelector("[data-wa-select-all]")?.addEventListener("click", () => {
    setVisibleSelection(true);
  });
  root.querySelector("[data-wa-select-none]")?.addEventListener("click", () => {
    setVisibleSelection(false);
  });

  itemSearch?.addEventListener("input", filterItems);
  itemsBody?.addEventListener("change", (event) => {
    if (event.target.matches("[data-wa-item-pick]")) syncSelection();
  });
  itemsBody?.addEventListener("click", (event) => {
    const row = event.target.closest(".wa-item");
    if (!row || !itemsBody.contains(row)) return;
    if (event.target.closest("[data-wa-item-pick], a, img")) return;
    const box = row.querySelector("[data-wa-item-pick]");
    if (!box) return;
    box.checked = !box.checked;
    syncSelection();
  });
  root.querySelector("[data-wa-items-all]")?.addEventListener("click", () => {
    setVisibleItems(true);
  });
  root.querySelector("[data-wa-items-none]")?.addEventListener("click", () => {
    setVisibleItems(false);
  });

  audienceForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    showMessage("");
    const submitBtn = audienceForm.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    try {
      const params = new URLSearchParams(new FormData(audienceForm));
      params.delete("client_ids");
      const data = await post(params);
      renderPeople(data);
      showMessage(data.message || "Audience saved.");
    } catch (error) {
      showMessage(error.message || "Could not save audience.", true);
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  root.querySelector("[data-wa-send-website]")?.addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    showMessage("");
    if (!twilioReady) {
      showMessage("Save Twilio in Settings first.", true);
      return;
    }
    const ids = selectedClientIds();
    if (!ids.length) {
      showMessage("Select at least one person to send to.", true);
      return;
    }
    btn.disabled = true;
    try {
      if (audienceForm) {
        const saveParams = new URLSearchParams(new FormData(audienceForm));
        saveParams.delete("client_ids");
        await post(saveParams);
      }
      const sendParams = new URLSearchParams({ action: "send_website" });
      ids.forEach((id) => sendParams.append("client_ids", id));
      const data = await post(sendParams);
      if (data.campaign?.id) openSendIds.add(String(data.campaign.id));
      if (data.sends) renderSends(data.sends);
      showMessage(data.message || "Queued.");
    } catch (error) {
      showMessage(error.message || "Could not send.", true);
    } finally {
      btn.disabled = false;
    }
  });

  sendCatalogueBtn?.addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    showMessage("");
    if (!twilioReady) {
      showMessage("Save Twilio in Settings first.", true);
      return;
    }
    const itemIds = selectedItemIds();
    const ids = selectedClientIds();
    if (!itemIds.length) {
      showMessage("Select at least one item to share.", true);
      return;
    }
    if (!ids.length) {
      showMessage("Select at least one person to send to.", true);
      return;
    }
    btn.disabled = true;
    try {
      if (audienceForm) {
        const saveParams = new URLSearchParams(new FormData(audienceForm));
        saveParams.delete("client_ids");
        await post(saveParams);
      }
      const sendParams = new URLSearchParams({ action: "send_catalogue" });
      ids.forEach((id) => sendParams.append("client_ids", id));
      itemIds.forEach((id) => sendParams.append("item_ids", id));
      const data = await post(sendParams);
      if (data.campaign?.id) openSendIds.add(String(data.campaign.id));
      if (data.sends) renderSends(data.sends);
      showMessage(data.message || "Queued.");
    } catch (error) {
      showMessage(error.message || "Could not send.", true);
    } finally {
      btn.disabled = false;
    }
  });

  sendsEl?.addEventListener("click", async (event) => {
    const toggle = event.target.closest("[data-wa-toggle-send]");
    if (toggle) {
      const card = toggle.closest(".wa-auto__send");
      const id = String(toggle.getAttribute("data-wa-toggle-send") || "");
      const open = !card?.classList.contains("is-open");
      card?.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) openSendIds.add(id);
      else openSendIds.delete(id);
      return;
    }
    const retryBtn = event.target.closest("[data-wa-retry]");
    if (retryBtn) {
      const id = retryBtn.getAttribute("data-wa-retry");
      retryBtn.disabled = true;
      try {
        const data = await post(
          new URLSearchParams({ action: "retry_failed", campaign_id: id || "" })
        );
        if (id) openSendIds.add(String(id));
        renderSends(data.sends);
        showMessage(data.message || "Retrying failed messages.");
      } catch (error) {
        showMessage(error.message || "Could not retry.", true);
        retryBtn.disabled = false;
      }
      return;
    }
    const btn = event.target.closest("[data-wa-cancel]");
    if (!btn) return;
    const id = btn.getAttribute("data-wa-cancel");
    if (
      !window.confirm(
        "Stop unsent messages for this send? Messages already handed to Twilio cannot be recalled."
      )
    ) {
      return;
    }
    btn.disabled = true;
    try {
      const data = await post(
        new URLSearchParams({ action: "cancel_campaign", campaign_id: id || "" })
      );
      renderSends(data.sends);
      showMessage(data.message || "Send cancelled.");
    } catch (error) {
      showMessage(error.message || "Could not cancel.", true);
      btn.disabled = false;
    }
  });

  const initialNode = document.getElementById("wa-auto-sends-data");
  try {
    renderSends(initialNode ? JSON.parse(initialNode.textContent || "[]") : []);
  } catch (error) {
    renderSends([]);
  }
  peopleBody?.querySelectorAll(".wa-person").forEach((row) => {
    const name = row.querySelector("strong")?.textContent || "";
    const avatar = row.querySelector(".wa-person__avatar");
    const phone = row.querySelector("em");
    if (avatar) avatar.textContent = initials(name);
    if (phone) phone.textContent = formatPhone(phone.textContent);
  });
  syncDepends();
  filterPeople();
  filterItems();
  syncSelection();
  refreshIcons();
})();
