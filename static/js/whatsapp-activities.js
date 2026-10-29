(() => {
  const root = document.querySelector("[data-wa-activities]");
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
  const feedEl = root.querySelector("[data-wa-feed]");
  const feedCountEl = root.querySelector("[data-wa-feed-count]");
  const tabButtons = Array.from(root.querySelectorAll("[data-wa-channel]"));
  const waitingSummaryEl = document.querySelector("[data-wa-summary-waiting]");
  const scheduledSummaryEl = document.querySelector("[data-wa-summary-scheduled]");
  const historySummaryEl = document.querySelector("[data-wa-summary-history]");
  const statusFilterEl = root.querySelector("[data-wa-filter-status]");
  const typeFilterEl = root.querySelector("[data-wa-filter-type]");
  const shopFilterEl = root.querySelector("[data-wa-filter-shop]");
  const dayFilterEl = root.querySelector("[data-wa-filter-day]");
  const clearFiltersEl = root.querySelector("[data-wa-filter-clear]");

  let pollTimer = 0;
  let activeChannel = tabButtons[0]?.dataset.waChannel || "";
  let lastPayload = null;
  const filters = {
    status: statusFilterEl?.value || "",
    type: typeFilterEl?.value || "",
    shop: shopFilterEl?.value || "",
    day: dayFilterEl?.value || "",
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function refreshIcons() {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  }

  function plural(count, one, many) {
    const n = Number(count || 0);
    return `${n} ${n === 1 ? one : many}`;
  }

  function detailHref(row) {
    if (row.detail_url) return row.detail_url;
    const base = window.location.pathname.replace(/\/?$/, "/");
    return `${base}${row.campaign_id}/`;
  }

  function channels(payload) {
    return Array.isArray(payload?.channels) ? payload.channels : [];
  }

  function channelFeed(payload, slug) {
    const match = channels(payload).find((chan) => chan.slug === slug);
    return match || channels(payload)[0] || null;
  }

  function filtersActive() {
    return Boolean(filters.status || filters.type || filters.shop || filters.day);
  }

  function feedCountLabel(channel) {
    if (!channel) return "";
    const parts = [plural(channel.total, "message", "messages")];
    if (filtersActive()) {
      parts.push(`${plural(channel.shown, "match", "matches")}`);
    } else {
      if (Number(channel.waiting || 0) > 0) parts.push(`${channel.waiting} waiting`);
      if (Number(channel.failed || 0) > 0) parts.push(`${channel.failed} failed`);
      if (Number(channel.total || 0) > Number(channel.shown || 0)) {
        parts.push(`showing latest ${channel.shown}`);
      }
    }
    return parts.join(" · ");
  }

  function messageRow(row) {
    const status = row.display_status || row.status || "";
    const error =
      row.error && (status === "failed" || row.status === "failed")
        ? `<em class="wa-person__error">${escapeHtml(row.error)}</em>`
        : "";
    return `<tr class="wa-act-row" data-message-id="${row.id}">
      <td data-label="To">
        <a class="wa-act-toggle" href="${escapeHtml(detailHref(row))}">
          <strong>${escapeHtml(row.client_name || "Customer")}</strong>
          <span>${escapeHtml(row.phone || "")}</span>
        </a>
      </td>
      <td data-label="Message">
        <a class="wa-act-toggle" href="${escapeHtml(detailHref(row))}">
          <strong>${escapeHtml(row.kind_label || "Message")}</strong>
          <span>${escapeHtml(row.body_preview || row.body || "Message")}</span>
        </a>
      </td>
      <td data-label="Shop">${escapeHtml(row.shop_label || "All shops")}</td>
      <td data-label="Status">
        <span class="wa-auto__send-status" data-status="${escapeHtml(status)}">${escapeHtml(
          row.status_label || status
        )}</span>
        ${error}
      </td>
      <td data-label="When">${escapeHtml(row.when_label || row.created_label || "")}</td>
    </tr>`;
  }

  function renderFeed(payload) {
    if (!feedEl) return;
    const channel = channelFeed(payload, activeChannel);
    if (!channel || !channel.messages.length) {
      const emptyText = filtersActive()
        ? "No messages match the selected filters."
        : root.dataset.feedEmpty ||
          "Nothing on this channel yet. Queued and sent messages will show up here.";
      feedEl.innerHTML = `<p class="wa-act-empty">${escapeHtml(emptyText)}</p>`;
    } else {
      feedEl.innerHTML = `<table class="staff-table staff-table--dense wa-act-table">
        <thead>
          <tr>
            <th>To</th>
            <th>Message</th>
            <th>Shop</th>
            <th>Status</th>
            <th>When</th>
          </tr>
        </thead>
        <tbody>${channel.messages.map(messageRow).join("")}</tbody>
      </table>`;
    }
    if (feedCountEl) feedCountEl.textContent = feedCountLabel(channel);
  }

  function renderTabs(payload) {
    tabButtons.forEach((button) => {
      const slug = button.dataset.waChannel;
      const channel = channelFeed(payload, slug);
      const label = button.querySelector("span");
      if (label && channel) {
        const badges = [];
        if (Number(channel.waiting || 0) > 0) badges.push(`${channel.waiting} waiting`);
        if (Number(channel.failed || 0) > 0) badges.push(`${channel.failed} failed`);
        label.textContent = badges.length
          ? `${channel.label} · ${badges.join(" · ")}`
          : channel.label;
      }
      const isActive = slug === activeChannel;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
  }

  function needsPoll(payload) {
    const pending = payload?.pending || [];
    const history = payload?.history || [];
    if (
      [...pending, ...history].some(
        (send) =>
          send.status === "queued" ||
          send.status === "sending" ||
          send.status === "draft" ||
          Number(send.pending_count || 0) > 0
      )
    ) {
      return true;
    }
    return channels(payload).some((channel) =>
      (channel.messages || []).some(
        (row) => row.status === "pending" || row.display_status === "queued"
      )
    );
  }

  function renderActivities(payload) {
    const data =
      payload && typeof payload === "object"
        ? payload
        : { pending: [], history: [], channels: [], summary: {} };
    lastPayload = data;
    renderTabs(data);
    renderFeed(data);
    const summary = data.summary || {};
    if (waitingSummaryEl) waitingSummaryEl.textContent = String(summary.waiting_messages || 0);
    if (scheduledSummaryEl) scheduledSummaryEl.textContent = String(summary.scheduled_batches || 0);
    if (historySummaryEl) historySummaryEl.textContent = String(summary.history_batches || 0);
    refreshIcons();
    if (needsPoll(data)) startPoll();
    else stopPoll();
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
      throw new Error(data.error || "Could not update.");
    }
    return data;
  }

  function filterParams() {
    const params = new URLSearchParams({ action: "refresh_sends" });
    if (filters.status) params.set("status", filters.status);
    if (filters.type) params.set("type", filters.type);
    if (filters.shop) params.set("shop", filters.shop);
    if (filters.day) params.set("day", filters.day);
    return params;
  }

  function refreshActivities() {
    return post(filterParams())
      .then((data) => renderActivities(data.activities))
      .catch(() => {});
  }

  function startPoll() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      refreshActivities();
    }, 4000);
  }

  function stopPoll() {
    if (!pollTimer) return;
    window.clearInterval(pollTimer);
    pollTimer = 0;
  }

  function syncFilterUrl() {
    const url = new URL(window.location.href);
    ["status", "type", "shop", "day"].forEach((key) => {
      if (filters[key]) url.searchParams.set(key, filters[key]);
      else url.searchParams.delete(key);
    });
    window.history.replaceState({}, "", url);
  }

  function updateClearButton() {
    if (clearFiltersEl) clearFiltersEl.hidden = !filtersActive();
  }

  function readFilters() {
    filters.status = statusFilterEl?.value || "";
    filters.type = typeFilterEl?.value || "";
    filters.shop = shopFilterEl?.value || "";
    filters.day = dayFilterEl?.value || "";
  }

  [statusFilterEl, typeFilterEl, shopFilterEl, dayFilterEl].forEach((el) => {
    if (!el) return;
    el.addEventListener("change", () => {
      readFilters();
      updateClearButton();
      syncFilterUrl();
      refreshActivities();
    });
  });

  if (clearFiltersEl) {
    clearFiltersEl.addEventListener("click", () => {
      if (statusFilterEl) statusFilterEl.value = "";
      if (typeFilterEl) typeFilterEl.value = "";
      if (shopFilterEl) shopFilterEl.value = "";
      if (dayFilterEl) dayFilterEl.value = "";
      readFilters();
      updateClearButton();
      syncFilterUrl();
      refreshActivities();
    });
  }

  tabButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activeChannel = button.dataset.waChannel || activeChannel;
      renderTabs(lastPayload || {});
      renderFeed(lastPayload || {});
    });
  });

  const initialNode = document.getElementById("wa-activities-data");
  try {
    renderActivities(initialNode ? JSON.parse(initialNode.textContent || "{}") : {});
  } catch (error) {
    renderActivities({ pending: [], history: [], channels: [], summary: {} });
  }
})();

(() => {
  const root = document.querySelector("[data-wa-activity-detail]");
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
  const messageEl = document.querySelector("[data-wa-message]");
  const kindEl = root.querySelector("[data-wa-kind]");
  const timingEl = document.querySelector("[data-wa-timing]");
  const progressEl = document.querySelector("[data-wa-progress]");
  const statusEl = document.querySelector("[data-wa-status]");
  const peopleCountEl = document.querySelector("[data-wa-people-count]");
  const recipientCountEl = document.querySelector("[data-wa-recipient-count]");
  const actionsEl = document.querySelector("[data-wa-actions]");
  const bodyEl = document.querySelector("[data-wa-body]");
  const peopleEl = document.querySelector("[data-wa-people]");
  const campaignLabels = {
    queued: "Queued",
    sending: "Sending",
    done: "Sent",
    cancelled: "Cancelled",
    draft: "Scheduled",
  };
  const messageLabels = {
    queued: "Waiting",
    pending: "Waiting",
    sent: "Sent",
    delivered: "Delivered",
    viewed: "Viewed",
    failed: "Failed",
    cancelled: "Cancelled",
  };

  let pollTimer = 0;

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

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

  function plural(count, one, many) {
    const n = Number(count || 0);
    return `${n} ${n === 1 ? one : many}`;
  }

  function personStatus(person) {
    return person.display_status || person.status || "";
  }

  function peopleRows(send) {
    const people = Array.isArray(send.messages) ? send.messages : [];
    if (!people.length) {
      return `<p class="wa-act-empty">No recipients logged for this send.</p>`;
    }
    return `<table class="staff-table staff-table--dense wa-act-people">
      <thead>
        <tr>
          <th>Person</th>
          <th>Phone</th>
          <th>Channel</th>
          <th>Status</th>
          <th>When</th>
          <th>Message sent</th>
        </tr>
      </thead>
      <tbody>
        ${people
          .map((person) => {
            const status = personStatus(person);
            const error =
              person.error && (status === "failed" || person.status === "failed")
                ? `<em class="wa-person__error">${escapeHtml(person.error)}</em>`
                : "";
            const body = String(person.body || send.body_template || "").trim();
            return `<tr>
              <td data-label="Person"><strong>${escapeHtml(person.client_name || "Contact")}</strong></td>
              <td data-label="Phone">${escapeHtml(person.phone || "")}</td>
              <td data-label="Channel">${escapeHtml(person.channel_label || "WhatsApp")}</td>
              <td data-label="Status">
                <span class="wa-auto__pill" data-status="${escapeHtml(status)}">${escapeHtml(
                  person.status_label || messageLabels[status] || status
                )}</span>
                ${error}
              </td>
              <td data-label="When">${escapeHtml(person.when_label || "")}</td>
              <td data-label="Message sent"><pre class="wa-act-person-body">${escapeHtml(body)}</pre></td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table>`;
  }

  function actionButtons(send) {
    const tools = [
      send.can_cancel
        ? `<button class="btn btn--ghost" type="button" data-wa-cancel="${send.id}">Cancel</button>`
        : "",
      send.can_retry
        ? `<button class="btn btn--ghost" type="button" data-wa-retry="${send.id}">Retry failed</button>`
        : "",
    ].filter(Boolean);
    return tools.join("");
  }

  function needsPoll(send) {
    if (!send) return false;
    if (["queued", "sending", "draft"].includes(send.status)) return true;
    if (Number(send.pending_count || 0) > 0) return true;
    return (send.messages || []).some((person) =>
      ["queued", "pending", "sent"].includes(personStatus(person))
    );
  }

  function renderCampaign(send) {
    if (!send || typeof send !== "object") return;
    if (kindEl) kindEl.textContent = send.kind_label || "WhatsApp send";
    if (timingEl) timingEl.textContent = send.timing_label || send.created_label || "";
    if (progressEl) progressEl.textContent = send.progress_label || "";
    if (statusEl) {
      statusEl.textContent = send.status_label || campaignLabels[send.status] || send.status || "";
      statusEl.setAttribute("data-status", send.status || "");
    }
    if (peopleCountEl) peopleCountEl.textContent = plural(send.recipient_count, "recipient", "recipients");
    if (recipientCountEl) recipientCountEl.textContent = plural((send.messages || []).length, "person", "people");
    if (bodyEl) bodyEl.textContent = String(send.body_template || "").trim() || "No message text was saved.";
    if (actionsEl) actionsEl.innerHTML = actionButtons(send);
    if (peopleEl) peopleEl.innerHTML = peopleRows(send);
    refreshIcons();
    if (needsPoll(send)) startPoll();
    else stopPoll();
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
      throw new Error(data.error || "Could not update.");
    }
    return data;
  }

  async function fetchCampaign() {
    const response = await fetch(window.location.pathname, {
      method: "GET",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Could not refresh.");
    }
    return data.campaign;
  }

  function startPoll() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      fetchCampaign()
        .then((campaign) => renderCampaign(campaign))
        .catch(() => {});
    }, 4000);
  }

  function stopPoll() {
    if (!pollTimer) return;
    window.clearInterval(pollTimer);
    pollTimer = 0;
  }

  root.addEventListener("click", async (event) => {
    const cancelBtn = event.target.closest("[data-wa-cancel]");
    const retryBtn = event.target.closest("[data-wa-retry]");
    if (!cancelBtn && !retryBtn) return;

    const action = cancelBtn ? "cancel_campaign" : "retry_failed";
    const campaignId = (cancelBtn || retryBtn).dataset.waCancel || (cancelBtn || retryBtn).dataset.waRetry;
    const button = cancelBtn || retryBtn;
    button.disabled = true;
    showMessage("");
    try {
      const data = await post(
        new URLSearchParams({ action, campaign_id: String(campaignId || "") })
      );
      showMessage(data.message || "Updated.");
      renderCampaign(data.campaign);
    } catch (error) {
      showMessage(error.message || "Could not update.", true);
    } finally {
      button.disabled = false;
    }
  });

  const initialNode = document.getElementById("wa-activity-data");
  try {
    const send = initialNode ? JSON.parse(initialNode.textContent || "{}") : {};
    if (needsPoll(send)) startPoll();
  } catch (error) {
    /* Keep the server-rendered detail if the payload cannot be parsed. */
  }
})();
