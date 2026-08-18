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
  let openSendIds = new Set();

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
    const parts = [];
    if (send.pending_count) parts.push(`${send.pending_count} waiting`);
    if (send.viewed_count) parts.push(`${send.viewed_count} viewed`);
    if (send.delivered_count) parts.push(`${send.delivered_count} delivered`);
    if (send.sent_count) parts.push(`${send.sent_count} sent`);
    if (send.failed_count) parts.push(`${send.failed_count} failed`);
    if (send.cancelled_count) parts.push(`${send.cancelled_count} cancelled`);
    if (!parts.length) parts.push(plural(send.recipient_count, "person", "people"));
    return parts.join(" · ");
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

  function peopleRows(send) {
    const people = Array.isArray(send.messages) ? send.messages : [];
    if (!people.length) {
      return `<p class="wa-act-empty wa-act-empty--nested">No recipients logged.</p>`;
    }
    return `<div class="staff-table-wrap"><table class="staff-table staff-table--dense wa-act-people">
      <thead>
        <tr>
          <th>Person</th>
          <th>Phone</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        ${people
          .map((person) => {
            const status = person.display_status || person.status || "";
            const error =
              person.error && (status === "failed" || person.status === "failed")
                ? `<em class="wa-person__error">${escapeHtml(person.error)}</em>`
                : "";
            return `<tr>
              <td data-label="Person"><strong>${escapeHtml(person.client_name || "Contact")}</strong></td>
              <td data-label="Phone">${escapeHtml(person.phone || "")}</td>
              <td data-label="Status">
                <span class="wa-auto__pill" data-status="${escapeHtml(status)}">${escapeHtml(
                  messageLabels[status] || status
                )}</span>
                ${error}
              </td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table></div>`;
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
            const open = openSendIds.has(String(send.id));
            return `<tr class="wa-act-row${open ? " is-open" : ""}" data-send-id="${send.id}">
              <td data-label="What">
                <button class="wa-act-toggle" type="button" data-wa-toggle-send="${send.id}" aria-expanded="${
                  open ? "true" : "false"
                }">
                  <strong>${escapeHtml(kindLabel(send))}</strong>
                  <span>${escapeHtml(previewText(send))}</span>
                </button>
              </td>
              <td data-label="When">${escapeHtml(whenLabel(send))}</td>
              <td data-label="People">${escapeHtml(sendProgress(send))}</td>
              <td data-label="Status">
                <span class="wa-auto__send-status" data-status="${escapeHtml(send.status || "")}">${escapeHtml(
                  statusLabel(send)
                )}</span>
              </td>
              <td data-label="Actions">${actionButtons(send)}</td>
            </tr>
            <tr class="wa-act-detail${open ? " is-open" : ""}" data-send-detail="${send.id}">
              <td colspan="5">${peopleRows(send)}</td>
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
        Number(send.pending_count || 0) > 0 ||
        (send.messages || []).some((person) =>
          ["queued", "pending", "sent"].includes(person.display_status || person.status || "")
        )
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
    const toggle = event.target.closest("[data-wa-toggle-send]");
    if (toggle) {
      const id = String(toggle.dataset.waToggleSend || "");
      if (openSendIds.has(id)) openSendIds.delete(id);
      else openSendIds.add(id);
      const row = root.querySelector(`[data-send-id="${id}"]`);
      const detail = root.querySelector(`[data-send-detail="${id}"]`);
      const open = openSendIds.has(id);
      row?.classList.toggle("is-open", open);
      detail?.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      return;
    }

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
