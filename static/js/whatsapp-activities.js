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
  const pendingEl = root.querySelector("[data-wa-pending]");
  const historyEl = root.querySelector("[data-wa-history]");
  const messageEl = root.querySelector("[data-wa-message]");
  const pendingCountEl = root.querySelector("[data-wa-pending-count]");
  const historyCountEl = root.querySelector("[data-wa-history-count]");
  const waitingSummaryEl = document.querySelector("[data-wa-summary-waiting]");
  const scheduledSummaryEl = document.querySelector("[data-wa-summary-scheduled]");
  const historySummaryEl = document.querySelector("[data-wa-summary-history]");

  const campaignLabels = {
    queued: "Queued",
    sending: "Sending",
    done: "Sent",
    cancelled: "Cancelled",
    draft: "Scheduled",
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

  function sendProgress(send) {
    return send.progress_label || plural(send.recipient_count, "person", "people");
  }

  function statusLabel(send) {
    return send.status_label || campaignLabels[send.status] || send.status || "";
  }

  function kindLabel(send) {
    return send.kind_label || "WhatsApp send";
  }

  function whenLabel(send) {
    return send.timing_label || send.send_after_label || send.created_label || "";
  }

  function previewText(send) {
    const text = String(send.body_preview || "").trim();
    return text || "WhatsApp message";
  }

  function detailHref(send) {
    if (send.detail_url) return send.detail_url;
    const base = window.location.pathname.replace(/\/?$/, "/");
    return `${base}${send.id}/`;
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
    return tools.length ? `<div class="wa-act-tools">${tools.join("")}</div>` : "—";
  }

  function renderTable(target, rows, emptyText) {
    if (!target) return;
    if (!rows.length) {
      target.innerHTML = `<p class="wa-act-empty">${escapeHtml(emptyText)}</p>`;
      return;
    }
    target.innerHTML = `<table class="staff-table staff-table--dense wa-act-table">
      <thead>
        <tr>
          <th>What</th>
          <th>When</th>
          <th>People</th>
          <th>Status</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map((send) => {
            return `<tr class="wa-act-row" data-send-id="${send.id}">
              <td data-label="What">
                <a class="wa-act-toggle" href="${escapeHtml(detailHref(send))}">
                  <strong>${escapeHtml(kindLabel(send))}</strong>
                  <span>${escapeHtml(previewText(send))}</span>
                </a>
              </td>
              <td data-label="When">${escapeHtml(whenLabel(send))}</td>
              <td data-label="People">${escapeHtml(sendProgress(send))}</td>
              <td data-label="Status">
                <span class="wa-auto__send-status" data-status="${escapeHtml(send.status || "")}">${escapeHtml(
                  statusLabel(send)
                )}</span>
              </td>
              <td data-label="Actions">${actionButtons(send)}</td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table>`;
  }

  function setCount(el, count, one, many) {
    if (!el) return;
    el.textContent = plural(count, one, many);
  }

  function needsPoll(payload) {
    const pending = payload?.pending || [];
    const history = payload?.history || [];
    const rows = [...pending, ...history];
    return rows.some(
      (send) =>
        send.status === "queued" ||
        send.status === "sending" ||
        send.status === "draft" ||
        Number(send.pending_count || 0) > 0
    );
  }

  function renderActivities(payload) {
    const data = payload && typeof payload === "object" ? payload : { pending: [], history: [], summary: {} };
    const pending = Array.isArray(data.pending) ? data.pending : [];
    const history = Array.isArray(data.history) ? data.history : [];
    const summary = data.summary || {};

    renderTable(
      pendingEl,
      pending,
      root.dataset.pendingEmpty ||
        "Nothing waiting. Scheduled item shares and queued messages will show up here."
    );
    renderTable(
      historyEl,
      history,
      root.dataset.historyEmpty ||
        "No completed sends yet. Share items, then check back here."
    );

    setCount(pendingCountEl, pending.length, "batch", "batches");
    setCount(historyCountEl, history.length, "send", "sends");
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

  function startPoll() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      post(new URLSearchParams({ action: "refresh_sends" }))
        .then((data) => renderActivities(data.activities))
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
      renderActivities(data.activities);
    } catch (error) {
      showMessage(error.message || "Could not update.", true);
    } finally {
      button.disabled = false;
    }
  });

  const initialNode = document.getElementById("wa-activities-data");
  try {
    renderActivities(initialNode ? JSON.parse(initialNode.textContent || "{}") : {});
  } catch (error) {
    renderActivities({ pending: [], history: [], summary: {} });
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
