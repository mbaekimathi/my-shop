(() => {
  const root = document.querySelector("[data-comms]");
  if (!root) return;

  const MODE_KEY = "myshop.comms.mode";

  const api = {
    status: root.getAttribute("data-api-status"),
    logout: root.getAttribute("data-api-logout"),
    recipients: root.getAttribute("data-api-recipients"),
    preview: root.getAttribute("data-api-preview"),
    send: root.getAttribute("data-api-send"),
    campaign: root.getAttribute("data-api-campaign"),
    inbox: root.getAttribute("data-api-inbox"),
    analytics: root.getAttribute("data-api-analytics"),
  };

  const companyName = root.getAttribute("data-company-name") || "MY-SHOP";

  const csrf =
    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const els = {
    pill: root.querySelector("[data-comms-status-pill]"),
    phone: root.querySelector("[data-comms-phone]"),
    error: root.querySelector("[data-comms-bridge-error]"),
    help: root.querySelector("[data-comms-bridge-help]"),
    helpText: root.querySelector("[data-comms-bridge-help-text]"),
    helpCmd: root.querySelector("[data-comms-bridge-cmd]"),
    helpNote: root.querySelector("[data-comms-bridge-note]"),
    connectHelp: root.querySelector("[data-comms-connect-help]"),
    qrWrap: root.querySelector("[data-comms-qr-wrap]"),
    qr: root.querySelector("[data-comms-qr]"),
    refresh: root.querySelector("[data-comms-refresh]"),
    logout: root.querySelector("[data-comms-logout]"),
    audienceSelect: root.querySelector("[data-comms-audience-select]"),
    waTypeField: root.querySelector("[data-comms-wa-type-field]"),
    waType: root.querySelector("[data-comms-wa-type]"),
    systemPanel: root.querySelector("[data-comms-system-panel]"),
    itemsField: root.querySelector("[data-comms-items-field]"),
    txField: root.querySelector("[data-comms-tx-field]"),
    itemSelect: root.querySelector("[data-comms-item-select]"),
    minTransactions: root.querySelector("[data-comms-min-transactions]"),
    pickLabels: [...root.querySelectorAll("[data-comms-pick-label]")],
    search: root.querySelector("[data-comms-search]"),
    lastPurchase: root.querySelector("[data-comms-last-purchase]"),
    count: root.querySelector("[data-comms-count]"),
    poolCount: root.querySelector("[data-comms-pool-count]"),
    countNote: root.querySelector("[data-comms-count-note]"),
    matchBanner: root.querySelector("[data-comms-match-banner]"),
    matchTitle: root.querySelector("[data-comms-match-title]"),
    modalCount: root.querySelector("[data-comms-modal-count]"),
    picked: root.querySelector("[data-comms-picked]"),
    recipients: root.querySelector("[data-comms-recipients]"),
    selectAll: root.querySelector("[data-comms-select-all]"),
    selectNone: root.querySelector("[data-comms-select-none]"),
    openContacts: [...root.querySelectorAll("[data-comms-open-contacts]")],
    contactsModal: root.querySelector("[data-comms-contacts-modal]"),
    contactsDone: root.querySelector("[data-comms-contacts-done]"),
    contactsClose: [...root.querySelectorAll("[data-comms-contacts-close]")],
    chatSub: root.querySelector("[data-comms-chat-sub]"),
    body: root.querySelector("[data-comms-body]"),
    image: root.querySelector("[data-comms-image]"),
    imageName: root.querySelector("[data-comms-image-name]"),
    preview: root.querySelector("[data-comms-preview]"),
    previewFor: root.querySelector("[data-comms-preview-for]"),
    chatAvatar: root.querySelector("[data-comms-chat-avatar]"),
    send: root.querySelector("[data-comms-send]"),
    sendHint: root.querySelector("[data-comms-send-hint]"),
    campaigns: root.querySelector("[data-comms-campaigns]"),
    analytics: root.querySelector("[data-comms-analytics]"),
    historyBody: root.querySelector("[data-comms-history-body]"),
    historyDrawer: root.querySelector("[data-comms-history-drawer]"),
    historyFab: root.querySelector("[data-comms-history-fab]"),
    historyBadge: root.querySelector("[data-comms-history-badge]"),
    historyClose: [...root.querySelectorAll("[data-comms-history-close]")],
    sideTabs: [...root.querySelectorAll("[data-comms-side-tab]")],
    sidePanels: [...root.querySelectorAll("[data-comms-side-panel]")],
    inbox: root.querySelector("[data-comms-inbox]"),
    log: root.querySelector("[data-comms-log]"),
    logBody: root.querySelector("[data-comms-log-body]"),
    logTitle: root.querySelector("[data-comms-log-title]"),
    logClose: root.querySelector("[data-comms-log-close]"),
    modeBtns: [...root.querySelectorAll("[data-comms-mode]")],
    steps: [...root.querySelectorAll("[data-comms-step]")],
  };

  let connected = false;
  let bridgeStatus = "disconnected";
  let hasMessage = false;
  let pollTimer = null;
  let campaignPoll = null;
  let debounceTimer = null;
  let searchTimer = null;
  let previewClientId = null;
  let historyOpen = false;
  let sideTab = "inbox";
  let replyUnread = Number(els.historyBadge?.textContent || 0) || 0;
  let audienceType = "sale";
  let selectedGroups = new Set(["groups"]);
  let selectedItems = new Set();
  let poolRecipients = [];
  let selectedIds = new Set();
  let selectAllMode = false;
  let lastPoolCount = null;
  let matchPulseTimer = null;

  const friendlyBridgeError = (raw) => {
    const text = String(raw || "");
    if (
      /10061|ECONNREFUSED|actively refused|unreachable|Failed to fetch|NetworkError|helper is not running/i.test(
        text
      )
    ) {
      return "Twilio is not configured. Save credentials in Settings.";
    }
    return text;
  };

  const statusLabel = (status) => {
    if (status === "connected") return "Ready";
    return "Not configured";
  };

  const humanStatus = (status) => {
    const map = {
      connected: "Ready",
      disconnected: "Not configured",
      qr_pending: "Not configured",
      queued: "Queued",
      sending: "Sending",
      done: "Done",
      cancelled: "Cancelled",
      draft: "Draft",
      pending: "Waiting",
      sent: "Sent",
      failed: "Failed",
      manual_review: "Needs review",
    };
    return map[status] || status;
  };

  const getMode = () => root.getAttribute("data-mode") || "simple";

  function setMode(mode, { refresh = true } = {}) {
    const next = mode === "advanced" ? "advanced" : "simple";
    root.setAttribute("data-mode", next);
    root.classList.toggle("is-simple", next === "simple");
    root.classList.toggle("is-advanced", next === "advanced");
    els.modeBtns.forEach((btn) => {
      btn.classList.toggle("is-active", btn.getAttribute("data-comms-mode") === next);
    });
    try {
      localStorage.setItem(MODE_KEY, next);
    } catch (_) {
      /* ignore */
    }
    if (next === "simple") {
      if (refresh && audienceType !== "whatsapp") scheduleAudienceRefresh();
    } else if (refresh && audienceType !== "whatsapp") {
      scheduleAudienceRefresh();
    }
  }

  function selectedItemIds() {
    const value = els.itemSelect?.value || "";
    return value ? [value] : [];
  }

  function renderItemOptions(items) {
    if (!els.itemSelect) return;
    const current = els.itemSelect.value || "";
    const options = [`<option value="">Any item</option>`];
    (items || []).forEach((item) => {
      const value = String(item.value);
      options.push(
        `<option value="${escapeHtml(value)}">${escapeHtml(item.label)} (${
          item.count
        })</option>`
      );
    });
    els.itemSelect.innerHTML = options.join("");
    if (current && [...els.itemSelect.options].some((o) => o.value === current)) {
      els.itemSelect.value = current;
    } else {
      els.itemSelect.value = "";
    }
    selectedItems = new Set(selectedItemIds());
  }

  function renderWaTypeOptions(groups) {
    if (!els.waType) return;
    const current = els.waType.value || "groups";
    const byValue = Object.fromEntries(
      (groups || []).map((g) => [g.value, g])
    );
    const rows = [
      { value: "groups", label: "Groups" },
      { value: "contacts", label: "Contacts" },
    ];
    els.waType.innerHTML = rows
      .map((row) => {
        const count = byValue[row.value]?.count;
        const suffix = count == null ? "" : ` (${count})`;
        return `<option value="${row.value}">${row.label}${suffix}</option>`;
      })
      .join("");
    els.waType.value = current === "contacts" ? "contacts" : "groups";
    selectedGroups = new Set([els.waType.value]);
  }

  function syncAudienceChrome() {
    const isWa = audienceType === "whatsapp";
    root.classList.toggle("is-whatsapp", isWa);
    root.classList.toggle("is-system", !isWa);
    if (els.systemPanel) els.systemPanel.hidden = isWa;
    if (els.waTypeField) els.waTypeField.hidden = !isWa;
    const pickText = "New message";
    const pickHint = isWa ? "Tap to choose contacts" : "Tap to choose clients";
    els.pickLabels.forEach((btn) => {
      btn.textContent = pickText;
    });
    els.openContacts.forEach((btn) => {
      if (btn.hasAttribute("data-comms-pick-label")) return;
      btn.setAttribute("title", pickText);
      btn.setAttribute("aria-label", pickText);
    });
    if (els.chatSub && !selectedCount()) {
      els.chatSub.textContent = pickHint;
    }
  }

  function filters(includeSelection = false) {
    const useSystemFilters = audienceType !== "whatsapp";
    const base = {
      audience_type: audienceType,
      categories: audienceType === "whatsapp" ? [...selectedGroups] : [],
      item_ids: useSystemFilters ? selectedItemIds() : [],
      min_transactions: useSystemFilters ? els.minTransactions?.value || "" : "",
      last_purchase_days: useSystemFilters ? els.lastPurchase?.value || "" : "",
      search: els.search?.value || "",
    };
    if (includeSelection && !selectAllMode) {
      if (audienceType === "whatsapp") {
        base.destinations = [...selectedIds];
      } else {
        base.client_ids = [...selectedIds];
      }
    }
    return base;
  }

  function recipientKey(r) {
    if (audienceType === "whatsapp") {
      return r.destination_key || r.chat_id || r.phone || "";
    }
    return String(r.client_id || "");
  }

  function selectedCount() {
    if (selectAllMode) return poolRecipients.length;
    return poolRecipients.filter((r) => selectedIds.has(recipientKey(r))).length;
  }

  function initialsOf(name) {
    const parts = String(name || "")
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
  }

  function toneOf(name) {
    const text = String(name || "");
    let hash = 0;
    for (let i = 0; i < text.length; i++) hash = (hash + text.charCodeAt(i) * (i + 1)) % 6;
    return String((hash % 6) + 1);
  }

  function setChatHeader(name) {
    const label = name || "Message preview";
    if (els.previewFor) els.previewFor.textContent = label;
    if (els.chatAvatar) {
      els.chatAvatar.textContent = initialsOf(name || "M");
      els.chatAvatar.setAttribute("data-tone", toneOf(name || "M"));
    }
  }

  async function fetchJson(url, options = {}) {
    const res = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || `Request failed (${res.status})`);
      err.data = data;
      throw err;
    }
    return data;
  }

  function setStep(num, state) {
    const step = els.steps.find((el) => el.getAttribute("data-comms-step") === String(num));
    if (step) step.dataset.state = state;
  }

  function updateSteps() {
    const picks = selectedCount();
    setStep(1, connected ? "done" : bridgeStatus === "qr_pending" ? "active" : "todo");
    setStep(2, picks > 0 ? "done" : connected ? "active" : "todo");
    setStep(3, hasMessage ? "done" : picks > 0 ? "active" : "todo");
    const ready = connected && picks > 0 && hasMessage;
    setStep(4, ready ? "active" : "todo");
  }

  function applyBridge(data) {
    bridgeStatus = data.status || "disconnected";
    connected = bridgeStatus === "connected";

    if (els.pill) {
      els.pill.dataset.status = bridgeStatus;
      els.pill.textContent = statusLabel(bridgeStatus);
    }
    if (els.phone) {
      els.phone.textContent = data.wa_phone
        ? `+${String(data.wa_phone).replace(/^\+/, "")}`
        : "Save Twilio in Settings";
    }
    root.classList.toggle("is-linked", connected);
    if (els.logout) els.logout.hidden = bridgeStatus === "disconnected";

    const rawError = data.last_error || "";
    const friendly = friendlyBridgeError(rawError);
    const unreachable = /helper is not running|10061|ECONNREFUSED|unreachable/i.test(
      `${rawError} ${friendly}`
    );

    if (els.help) els.help.hidden = connected;
    if (els.helpCmd) {
      if (data.cmd) {
        els.helpCmd.hidden = false;
        els.helpCmd.textContent = data.cmd;
      } else {
        els.helpCmd.hidden = true;
        els.helpCmd.textContent = "";
      }
    }
    if (els.helpNote && data.note) els.helpNote.textContent = data.note;
    if (els.helpText) {
      if (bridgeStatus === "qr_pending") {
        els.helpText.textContent = "QR is ready — scan it with your phone.";
      } else if (unreachable || bridgeStatus === "disconnected") {
        if (data.mode === "local-auto" || data.autostart === true) {
          els.helpText.textContent =
            data.help_text ||
            (data.last_error && /starting/i.test(data.last_error)
              ? data.last_error
              : "Starting WhatsApp helper automatically…");
        } else {
          els.helpText.textContent =
            data.help_text ||
            (data.mode === "remote"
              ? "Cannot reach the remote WhatsApp helper yet."
              : "WhatsApp helper is not running yet. Start it once, then come back here.");
        }
      } else {
        els.helpText.textContent = friendly || "Waiting for WhatsApp…";
      }
    }
    if (els.connectHelp) {
      if (connected) {
        els.connectHelp.textContent =
          "Twilio is configured and ready to send.";
      } else if (bridgeStatus === "qr_pending") {
        els.connectHelp.textContent =
          "Save Twilio credentials in Settings.";
      } else {
        els.connectHelp.textContent =
          data.connect_help ||
          "Open Settings → Twilio and save Account SID, Auth Token, and a From number.";
      }
    }

    if (els.error) {
      const showRaw = Boolean(friendly) && !unreachable && bridgeStatus !== "connected";
      els.error.hidden = !showRaw;
      els.error.textContent = showRaw ? friendly : "";
    }

    const showQr = bridgeStatus === "qr_pending" && data.qr_data_url;
    if (els.qrWrap) els.qrWrap.hidden = !showQr;
    if (els.qr && data.qr_data_url) els.qr.src = data.qr_data_url;

    syncSendButton();
    updateSteps();
    if (typeof data.reply_unread_count === "number") {
      setReplyBadge(data.reply_unread_count);
    }
  }

  async function refreshStatus() {
    try {
      const data = await fetchJson(api.status);
      applyBridge(data);
    } catch (err) {
      applyBridge({
        status: "disconnected",
        last_error: err.message || "Bridge unreachable",
      });
    }
    // Live-fetch replies from WhatsApp on every poll.
    if (!api.inbox) return;
    if (sideTab === "inbox") {
      refreshInbox({ markRead: false });
    } else {
      try {
        const inbox = await fetchJson(api.inbox);
        setReplyBadge(inbox.unread_count || 0);
      } catch (_) {
        /* ignore — status poll already ran */
      }
    }
  }

  async function logoutBridge() {
    if (
      !window.confirm(
        `Disconnect WhatsApp from ${companyName}? You will need to scan the QR again.`
      )
    ) {
      return;
    }
    try {
      const data = await fetchJson(api.logout, {
        method: "POST",
        headers: { "X-CSRFToken": csrf },
      });
      applyBridge(data);
    } catch (err) {
      if (els.sendHint) els.sendHint.textContent = friendlyBridgeError(err.message);
    }
  }

  function syncSendButton() {
    hasMessage =
      Boolean((els.body?.value || "").trim()) || Boolean(els.image?.files?.length);
    const picks = selectedCount();
    if (!els.send) return;
    els.send.disabled = !(connected && picks > 0 && hasMessage);
    if (els.sendHint) {
      if (!connected) {
        els.sendHint.textContent = "Save Twilio in Settings first (step 1).";
      } else if (picks <= 0) {
        els.sendHint.textContent = "Select at least one contact in this group.";
      } else if (!hasMessage) {
        els.sendHint.textContent = "Type a message or add a photo.";
      } else {
        els.sendHint.textContent = `Ready — ${picks} ${picks === 1 ? "person" : "people"}`;
      }
    }
    updateSteps();
  }

  function renderRecipients() {
    if (!els.recipients) return;
    if (!poolRecipients.length) {
      const emptyMsg =
        audienceType === "whatsapp"
          ? selectedGroups.has("groups")
            ? "No WhatsApp groups loaded. Connect WhatsApp, then try again."
            : "No WhatsApp contacts loaded. Connect WhatsApp, then try again."
          : "No clients match these filters. Try another audience or clear filters.";
      els.recipients.innerHTML = `<p class="comms-empty">${emptyMsg}</p>`;
      return;
    }
    els.recipients.innerHTML = poolRecipients
      .map((r) => {
        const key = recipientKey(r);
        const checked = selectAllMode || selectedIds.has(key) ? "checked" : "";
        const active = previewClientId === key ? " is-active" : "";
        let subtitle = r.phone || "";
        if (audienceType === "whatsapp" && r.destination_type === "group") {
          subtitle = "Group";
        } else if (!subtitle && r.chat_id) {
          subtitle = r.chat_id;
        }
        const name = r.full_name || "Contact";
        return `<label class="comms-recipient${active}" data-client-id="${escapeHtml(key)}">
          <input type="checkbox" data-client-check value="${escapeHtml(key)}" ${checked}>
          <span class="comms-avatar" data-tone="${toneOf(name)}" aria-hidden="true">${escapeHtml(
            initialsOf(name)
          )}</span>
          <span class="comms-recipient-main">
            <strong>${escapeHtml(name)}</strong>
            <span>${escapeHtml(subtitle || "No phone")}</span>
          </span>
        </label>`;
      })
      .join("");
  }

  function renderPicked() {
    if (!els.picked) return;
    const picks = selectAllMode
      ? poolRecipients
      : poolRecipients.filter((r) => selectedIds.has(recipientKey(r)));
    if (!picks.length) {
      els.picked.innerHTML = "";
      if (els.chatSub) els.chatSub.textContent = "Tap to choose contacts";
      return;
    }
    const shown = picks.slice(0, 6);
    const extra = picks.length - shown.length;
    els.picked.innerHTML =
      shown
        .map((r) => {
          const name = r.full_name || "Contact";
          return `<span class="comms-picked-chip">
            <span class="comms-avatar" data-tone="${toneOf(name)}" aria-hidden="true">${escapeHtml(
              initialsOf(name)
            )}</span>
            <span>${escapeHtml(name)}</span>
          </span>`;
        })
        .join("") +
      (extra > 0 ? `<span class="comms-picked-more">+${extra} more</span>` : "");
    if (els.chatSub) {
      if (picks.length === 1) {
        const one = picks[0];
        els.chatSub.textContent =
          one.destination_type === "group"
            ? "Group"
            : one.phone || one.chat_id || "1 contact";
      } else {
        els.chatSub.textContent = `${picks.length} contacts selected`;
      }
    }
  }

  function pulseMatchBanner() {
    if (!els.matchBanner) return;
    els.matchBanner.classList.remove("is-pulse");
    // Restart CSS animation
    void els.matchBanner.offsetWidth;
    els.matchBanner.classList.add("is-pulse");
    clearTimeout(matchPulseTimer);
    matchPulseTimer = setTimeout(() => {
      els.matchBanner?.classList.remove("is-pulse");
    }, 750);
  }

  function updateMatchBanner(pool, picks) {
    if (!els.matchBanner) return;
    const isWa = audienceType === "whatsapp";
    const noun = isWa ? (pool === 1 ? "contact" : "contacts") : pool === 1 ? "client" : "clients";
    let state = "idle";
    let title = isWa ? "No contacts yet" : "No clients yet";

    if (pool <= 0) {
      state = "empty";
      title = isWa ? "No contacts match" : "No clients match";
    } else if (picks > 0) {
      state = "ready";
      title =
        picks === pool
          ? `All ${pool} ${noun} selected`
          : `${picks} of ${pool} ${noun} selected`;
    } else {
      state = "found";
      title = pool === 1 ? `1 ${noun} found` : `${pool} ${noun} found`;
    }

    els.matchBanner.setAttribute("data-state", state);
    if (els.matchTitle) els.matchTitle.textContent = title;

    if (lastPoolCount !== null && pool > 0 && pool !== lastPoolCount) {
      pulseMatchBanner();
    } else if (lastPoolCount === 0 && pool > 0) {
      pulseMatchBanner();
    } else if (lastPoolCount === null && pool > 0) {
      pulseMatchBanner();
    }
    lastPoolCount = pool;
  }

  function updateCounts() {
    const picks = selectedCount();
    const pool = poolRecipients.length;
    if (els.count) els.count.textContent = String(picks);
    if (els.poolCount) els.poolCount.textContent = String(pool);
    if (els.modalCount) els.modalCount.textContent = String(picks);
    if (els.countNote) {
      if (!pool) {
        els.countNote.textContent = "Try another filter or clear Item / Transactions.";
      } else if (selectAllMode) {
        els.countNote.textContent = "All matching people are selected.";
      } else if (picks <= 0) {
        els.countNote.textContent = "Open the list and choose who to message.";
      } else {
        els.countNote.textContent = "";
      }
    }
    updateMatchBanner(pool, picks);
    renderPicked();
    syncSendButton();
  }

  function openContactsModal() {
    if (!els.contactsModal) return;
    els.contactsModal.hidden = false;
    document.body.classList.add("workspace-modal-open");
    refreshRecipients();
    const search = els.search;
    if (search) {
      setTimeout(() => search.focus(), 40);
    }
  }

  function closeContactsModal() {
    if (!els.contactsModal) return;
    els.contactsModal.hidden = true;
    document.body.classList.remove("workspace-modal-open");
    refreshPreview();
  }

  function setAudienceOptionCount(value, count) {
    const opt = els.audienceSelect?.querySelector(`option[value="${value}"]`);
    if (!opt) return;
    const label = opt.getAttribute("data-label") || opt.value;
    const shown = count == null ? "—" : String(count);
    opt.textContent = `${label} — ${shown}`;
  }

  function setAudience(type) {
    audienceType = type || "sale";
    selectedGroups =
      audienceType === "whatsapp"
        ? new Set([els.waType?.value === "contacts" ? "contacts" : "groups"])
        : new Set();
    selectedItems = new Set();
    if (els.itemSelect) els.itemSelect.value = "";
    if (els.minTransactions) els.minTransactions.value = "";
    if (els.lastPurchase) els.lastPurchase.value = "";
    selectAllMode = false;
    selectedIds = new Set();
    lastPoolCount = null;
    previewClientId = null;
    if (els.audienceSelect && els.audienceSelect.value !== audienceType) {
      els.audienceSelect.value = audienceType;
    }
    syncAudienceChrome();
    // Shop lists get Advanced helpers; WhatsApp stays Simple.
    if (audienceType === "whatsapp") {
      setMode("simple", { refresh: false });
    } else {
      setMode("advanced", { refresh: false });
    }
    scheduleAudienceRefresh();
  }

  async function refreshRecipients() {
    const params = new URLSearchParams();
    const f = filters(false);
    params.set("audience_type", f.audience_type);
    (f.categories || []).forEach((c) => params.append("categories", c));
    (f.item_ids || []).forEach((id) => params.append("item_ids", id));
    if (f.min_transactions) params.set("min_transactions", f.min_transactions);
    if (f.last_purchase_days) params.set("last_purchase_days", f.last_purchase_days);
    if (f.search) params.set("search", f.search);

    if (els.countNote && audienceType === "whatsapp") {
      els.countNote.textContent = selectedGroups.has("groups")
        ? "Loading groups from WhatsApp…"
        : "Loading contacts from WhatsApp…";
    }

    try {
      const data = await fetchJson(`${api.recipients}?${params}`);
      poolRecipients = data.recipients || [];
      const keyOf = (r) =>
        audienceType === "whatsapp"
          ? r.destination_key || r.chat_id || r.phone
          : String(r.client_id);

      if (selectAllMode) {
        selectedIds = new Set(poolRecipients.map(keyOf));
      } else {
        const poolSet = new Set(poolRecipients.map(keyOf));
        selectedIds = new Set([...selectedIds].filter((id) => poolSet.has(id)));
      }
      if (audienceType === "whatsapp") {
        renderWaTypeOptions(data.groups || []);
      } else {
        renderItemOptions(data.items || []);
      }
      renderRecipients();
      updateCounts();

      (data.audience_summary || []).forEach((row) => {
        if (row.value === "whatsapp") {
          setAudienceOptionCount(
            "whatsapp",
            data.count != null ? data.count : row.count
          );
        } else {
          setAudienceOptionCount(row.value, row.count ?? 0);
        }
      });
      if (audienceType === "whatsapp") {
        setAudienceOptionCount("whatsapp", data.count ?? 0);
        if (data.bridge_error && els.countNote) {
          els.countNote.textContent = data.bridge_error;
        }
      }

      refreshPreview();
    } catch (err) {
      poolRecipients = [];
      selectedIds = new Set();
      renderRecipients();
      updateCounts();
      if (els.sendHint) els.sendHint.textContent = friendlyBridgeError(err.message);
      if (els.countNote) els.countNote.textContent = friendlyBridgeError(err.message);
    }
  }

  async function refreshPreview() {
    const body = els.body?.value || "";
    if (!body.trim()) {
      if (els.preview) {
        els.preview.textContent = "Your personalized message will appear here.";
      }
      setChatHeader("");
      return;
    }
    try {
      const data = await fetchJson(api.preview, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrf,
        },
        body: JSON.stringify({
          body,
          filters: {
            ...filters(false),
            ...(audienceType === "whatsapp"
              ? {
                  destinations: previewClientId
                    ? [previewClientId]
                    : [...selectedIds].slice(0, 1),
                }
              : {
                  client_ids: previewClientId
                    ? [Number(previewClientId)]
                    : [...selectedIds].slice(0, 1).map(Number),
                }),
          },
          client_id:
            audienceType === "whatsapp" ? null : Number(previewClientId) || null,
          destination_key:
            audienceType === "whatsapp" ? previewClientId || null : null,
        }),
      });
      if (els.preview) {
        els.preview.textContent =
          data.preview || "Your personalized message will appear here.";
      }
      setChatHeader(data.recipient?.full_name || "");
    } catch (_) {
      /* ignore preview errors while typing */
    }
  }

  function scheduleAudienceRefresh() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => refreshRecipients(), 250);
  }

  async function sendNow() {
    if (els.send?.disabled) return;
    const picks = selectedCount();
    const confirmMsg = `Send this WhatsApp message to ${picks} ${
      picks === 1 ? "person" : "people"
    } in ${audienceType}?\n\nMessages go out one by one with short pauses.`;
    if (!window.confirm(confirmMsg)) return;

    els.send.disabled = true;
    if (els.sendHint) els.sendHint.textContent = "Queuing messages…";

    const form = new FormData();
    form.append("body", els.body?.value || "");
    form.append("audience_type", audienceType);
    (filters(false).categories || []).forEach((c) => form.append("categories", c));
    (filters(false).item_ids || []).forEach((id) => form.append("item_ids", String(id)));
    form.append("min_transactions", filters(false).min_transactions || "");
    form.append("last_purchase_days", filters(false).last_purchase_days || "");
    if (!selectAllMode) {
      if (audienceType === "whatsapp") {
        [...selectedIds].forEach((id) => form.append("destinations", String(id)));
      } else {
        [...selectedIds].forEach((id) => form.append("client_ids", String(id)));
      }
    } else if (audienceType === "whatsapp") {
      poolRecipients.forEach((r) =>
        form.append("destinations", String(r.destination_key || r.chat_id || r.phone))
      );
    } else {
      poolRecipients.forEach((r) => form.append("client_ids", String(r.client_id)));
    }
    if (els.image?.files?.[0]) form.append("image", els.image.files[0]);

    try {
      const data = await fetchJson(api.send, {
        method: "POST",
        headers: { "X-CSRFToken": csrf },
        body: form,
      });
      if (els.sendHint) {
        els.sendHint.textContent = `Sending to ${data.campaign.recipient_count} people…`;
      }
      setSideTab("analytics");
      await refreshAnalytics();
      prependCampaign(data.campaign);
      openCampaignLog(data.campaign);
      startCampaignPoll(data.campaign.id);
      updateHistoryBadge();
    } catch (err) {
      if (els.sendHint) els.sendHint.textContent = friendlyBridgeError(err.message);
    } finally {
      syncSendButton();
    }
  }

  function campaignUrl(id) {
    return (api.campaign || "").replace("{id}", String(id));
  }

  function prependCampaign(campaign) {
    const list =
      els.analytics?.querySelector("[data-comms-campaigns]") || els.campaigns;
    if (!list) return;
    const empty = list.querySelector(".comms-empty");
    empty?.remove();
    const article = document.createElement("article");
    article.className = "comms-campaign";
    article.dataset.campaignId = String(campaign.id);
    article.innerHTML = `
      <div>
        <strong>Send #${campaign.id}</strong>
        <span class="comms-pill" data-status="${escapeHtml(campaign.status)}">${escapeHtml(
          humanStatus(campaign.status)
        )}</span>
      </div>
      <p>${escapeHtml((campaign.body_template || "").slice(0, 80))}</p>
      <p class="comms-meta">${campaign.sent_count}/${campaign.recipient_count} delivered · ${
      campaign.failed_count
    } need attention</p>
      <button type="button" class="comms-btn comms-btn--ghost" data-comms-open-campaign="${
        campaign.id
      }">View details</button>
    `;
    list.prepend(article);
  }

  function renderLog(campaign) {
    if (!els.log || !els.logBody) return;
    els.log.hidden = false;
    if (els.logTitle) {
      els.logTitle.textContent = `Send #${campaign.id} · ${humanStatus(campaign.status)}`;
    }
    const rows = campaign.messages || [];
    els.logBody.innerHTML = rows
      .map((m) => {
        const when = m.sent_at || m.updated_at || m.created_at || "";
        return `<tr>
          <td>${escapeHtml(m.client_name || "—")}</td>
          <td>${escapeHtml(m.phone || "")}</td>
          <td><span class="comms-pill" data-status="${escapeHtml(m.status)}">${escapeHtml(
            humanStatus(m.status)
          )}</span></td>
          <td class="comms-advanced-only">${m.attempt_count || 0}</td>
          <td>${escapeHtml(when ? when.replace("T", " ").slice(0, 19) : "")}</td>
        </tr>`;
      })
      .join("");

    const card =
      els.analytics?.querySelector(`[data-campaign-id="${campaign.id}"]`) ||
      els.campaigns?.querySelector(`[data-campaign-id="${campaign.id}"]`);
    if (card) {
      const pill = card.querySelector(".comms-pill");
      if (pill) {
        pill.dataset.status = campaign.status;
        pill.textContent = humanStatus(campaign.status);
      }
      const meta = card.querySelector(".comms-meta");
      if (meta) {
        meta.textContent = `${campaign.sent_count}/${campaign.recipient_count} delivered · ${campaign.failed_count} need attention`;
      }
    }

    if (els.sendHint && ["sending", "queued"].includes(campaign.status)) {
      els.sendHint.textContent = `Sending… ${campaign.sent_count}/${campaign.recipient_count}`;
    }
    if (els.sendHint && campaign.status === "done") {
      els.sendHint.textContent =
        campaign.failed_count > 0
          ? `Finished — ${campaign.sent_count} sent, ${campaign.failed_count} need review.`
          : `Finished — all ${campaign.sent_count} delivered.`;
    }
  }

  async function openCampaignLog(campaignOrId) {
    const id = typeof campaignOrId === "object" ? campaignOrId.id : campaignOrId;
    if (typeof campaignOrId === "object") {
      renderLog(campaignOrId);
      return;
    }
    try {
      const data = await fetchJson(campaignUrl(id));
      renderLog(data.campaign);
    } catch (err) {
      if (els.sendHint) els.sendHint.textContent = friendlyBridgeError(err.message);
    }
  }

  function startCampaignPoll(id) {
    clearInterval(campaignPoll);
    campaignPoll = setInterval(async () => {
      try {
        const data = await fetchJson(campaignUrl(id));
        renderLog(data.campaign);
        if (["done", "cancelled"].includes(data.campaign.status)) {
          clearInterval(campaignPoll);
          campaignPoll = null;
        }
      } catch (_) {
        /* keep polling */
      }
    }, 3000);
  }

  function setReplyBadge(count) {
    replyUnread = Math.max(0, Number(count) || 0);
    if (!els.historyBadge) return;
    if (replyUnread > 0) {
      els.historyBadge.hidden = false;
      els.historyBadge.textContent = String(replyUnread);
    } else {
      els.historyBadge.hidden = true;
      els.historyBadge.textContent = "0";
    }
  }

  function setSideTab(tab) {
    sideTab = tab === "analytics" ? "analytics" : "inbox";
    els.sideTabs.forEach((btn) => {
      const active = btn.getAttribute("data-comms-side-tab") === sideTab;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    els.sidePanels.forEach((panel) => {
      const active = panel.getAttribute("data-comms-side-panel") === sideTab;
      panel.classList.toggle("is-active", active);
      panel.hidden = !active;
    });
    if (sideTab === "inbox") {
      refreshInbox({ markRead: true });
    } else {
      refreshAnalytics();
    }
  }

  function formatReplyTime(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return "";
      return d.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (_) {
      return "";
    }
  }

  function renderInbox(threads) {
    if (!els.inbox) return;
    if (!threads?.length) {
      els.inbox.innerHTML = `<p class="comms-empty">No replies yet</p>`;
      return;
    }
    els.inbox.innerHTML = threads
      .map((t) => {
        const name = t.full_name || t.phone || "Client";
        const unread = Number(t.unread_count || 0) > 0;
        return `<article class="comms-inbox-row${unread ? " is-unread" : ""}">
          <span class="comms-avatar" data-tone="${toneOf(name)}" aria-hidden="true">${escapeHtml(
            initialsOf(name)
          )}</span>
          <div class="comms-inbox-main">
            <div class="comms-inbox-top">
              <strong>${escapeHtml(name)}</strong>
              <time>${escapeHtml(formatReplyTime(t.last_at))}</time>
            </div>
            <p class="comms-inbox-phone">${escapeHtml(t.phone || "")}</p>
            <p class="comms-inbox-preview">${escapeHtml(t.body || "")}</p>
          </div>
          ${
            Number(t.reply_count || 0) > 1
              ? `<span class="comms-inbox-count">${Number(t.reply_count)}</span>`
              : unread
                ? `<span class="comms-inbox-dot" aria-label="Unread"></span>`
                : ""
          }
        </article>`;
      })
      .join("");
  }

  async function refreshInbox({ markRead = false } = {}) {
    if (!api.inbox) return;
    try {
      const url = markRead ? `${api.inbox}?mark_read=1` : api.inbox;
      const data = await fetchJson(
        url,
        markRead ? { method: "POST", headers: { "X-CSRFToken": csrf } } : undefined
      );
      renderInbox(data.threads || []);
      setReplyBadge(data.unread_count || 0);
    } catch (err) {
      if (els.inbox) {
        els.inbox.innerHTML = `<p class="comms-empty">${escapeHtml(
          friendlyBridgeError(err.message || "Could not load inbox.")
        )}</p>`;
      }
    }
  }

  function renderAnalytics(data) {
    if (!els.analytics) return;
    const s = data.summary || {};
    const reasons = data.fail_reasons || [];
    const campaigns = data.campaigns || [];
    const reasonHtml = reasons.length
      ? `<ul class="comms-fail-list">${reasons
          .map(
            (r) =>
              `<li><span>${escapeHtml(r.reason)}</span><em>${Number(
                r.count || 0
              )}</em></li>`
          )
          .join("")}</ul>`
      : `<p class="comms-empty">No failed sends yet.</p>`;
    const campaignHtml = campaigns.length
      ? campaigns
          .map((c) => {
            return `<article class="comms-campaign" data-campaign-id="${c.id}">
              <div>
                <strong>Send #${c.id}</strong>
                <span class="comms-pill" data-status="${escapeHtml(c.status)}">${escapeHtml(
                  humanStatus(c.status)
                )}</span>
              </div>
              <p>${escapeHtml(c.body_preview || "")}</p>
              <p class="comms-meta">${Number(c.sent_count || 0)} delivered · ${Number(
                c.failed_count || 0
              )} failed · ${Number(c.replied_count || 0)} replied</p>
              <button type="button" class="comms-btn comms-btn--ghost" data-comms-open-campaign="${
                c.id
              }">View details</button>
            </article>`;
          })
          .join("")
      : `<p class="comms-empty">No messages sent yet.</p>`;

    els.analytics.innerHTML = `
      <div class="comms-analytics-grid">
        <div class="comms-stat"><strong>${Number(s.sent || 0)}</strong><span>Delivered</span></div>
        <div class="comms-stat"><strong>${Number(s.failed || 0)}</strong><span>Failed</span></div>
        <div class="comms-stat"><strong>${Number(s.replied || 0)}</strong><span>Replied</span></div>
        <div class="comms-stat"><strong>${Number(s.reply_rate || 0)}%</strong><span>Reply rate</span></div>
      </div>
      <div class="comms-analytics-block">
        <h4>Failure reasons</h4>
        ${reasonHtml}
      </div>
      <div class="comms-analytics-block">
        <h4>Recent sends</h4>
        <div class="comms-campaigns" data-comms-campaigns>${campaignHtml}</div>
      </div>`;
  }

  async function refreshAnalytics() {
    if (!api.analytics || !els.analytics) return;
    try {
      const data = await fetchJson(api.analytics);
      renderAnalytics(data);
    } catch (err) {
      els.analytics.innerHTML = `<p class="comms-empty">${escapeHtml(
        err.message || "Could not load analytics."
      )}</p>`;
    }
  }

  function updateHistoryBadge() {
    setReplyBadge(replyUnread);
  }

  function setHistoryOpen(open) {
    // Legacy no-op: inbox lives in the sidebar now.
    historyOpen = Boolean(open);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  els.modeBtns.forEach((btn) => {
    btn.addEventListener("click", () => setMode(btn.getAttribute("data-comms-mode")));
  });

  els.lastPurchase?.addEventListener("change", () => {
    if (audienceType === "whatsapp") return;
    selectAllMode = false;
    selectedIds = new Set();
    if (getMode() !== "advanced") setMode("advanced", { refresh: false });
    scheduleAudienceRefresh();
  });

  els.itemSelect?.addEventListener("change", () => {
    if (audienceType === "whatsapp") return;
    selectedItems = new Set(selectedItemIds());
    selectAllMode = false;
    selectedIds = new Set();
    if (getMode() !== "advanced") setMode("advanced", { refresh: false });
    scheduleAudienceRefresh();
  });

  els.minTransactions?.addEventListener("change", () => {
    if (audienceType === "whatsapp") return;
    selectAllMode = false;
    selectedIds = new Set();
    if (getMode() !== "advanced") setMode("advanced", { refresh: false });
    scheduleAudienceRefresh();
  });

  els.waType?.addEventListener("change", () => {
    if (audienceType !== "whatsapp") return;
    selectedGroups = new Set([
      els.waType.value === "contacts" ? "contacts" : "groups",
    ]);
    selectAllMode = false;
    selectedIds = new Set();
    scheduleAudienceRefresh();
  });

  els.audienceSelect?.addEventListener("change", () => {
    setAudience(els.audienceSelect.value);
  });

  els.selectAll?.addEventListener("click", () => {
    selectAllMode = true;
    selectedIds = new Set(poolRecipients.map(recipientKey));
    renderRecipients();
    updateCounts();
    refreshPreview();
  });

  els.selectNone?.addEventListener("click", () => {
    selectAllMode = false;
    selectedIds = new Set();
    renderRecipients();
    updateCounts();
  });

  els.openContacts.forEach((btn) => {
    btn.addEventListener("click", () => openContactsModal());
  });
  els.contactsClose.forEach((btn) => {
    btn.addEventListener("click", () => closeContactsModal());
  });
  els.contactsDone?.addEventListener("click", () => closeContactsModal());

  els.recipients?.addEventListener("change", (event) => {
    const box = event.target.closest("[data-client-check]");
    if (!box) return;
    const id = box.value;
    selectAllMode = false;
    if (box.checked) selectedIds.add(id);
    else selectedIds.delete(id);
    updateCounts();
  });

  els.recipients?.addEventListener("click", (event) => {
    if (event.target.closest("[data-client-check]")) return;
    const row = event.target.closest("[data-client-id]");
    if (!row) return;
    previewClientId = row.getAttribute("data-client-id");
    els.recipients.querySelectorAll(".comms-recipient").forEach((el) => {
      el.classList.toggle(
        "is-active",
        el.getAttribute("data-client-id") === previewClientId
      );
    });
    const name = row.querySelector("strong")?.textContent || "";
    setChatHeader(name);
    refreshPreview();
  });

  els.refresh?.addEventListener("click", () => refreshStatus());
  els.logout?.addEventListener("click", () => logoutBridge());
  els.lastPurchase?.addEventListener("change", scheduleAudienceRefresh);
  els.search?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => refreshRecipients(), 300);
  });
  els.body?.addEventListener("input", () => {
    if (els.body) {
      els.body.style.height = "auto";
      els.body.style.height = `${Math.min(els.body.scrollHeight, 120)}px`;
    }
    syncSendButton();
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(refreshPreview, 200);
  });
  els.image?.addEventListener("change", () => {
    const file = els.image?.files?.[0];
    if (els.imageName) {
      if (file) {
        els.imageName.hidden = false;
        els.imageName.textContent = `Attached: ${file.name}`;
      } else {
        els.imageName.hidden = true;
        els.imageName.textContent = "";
      }
    }
    syncSendButton();
  });
  els.send?.addEventListener("click", () => sendNow());
  els.logClose?.addEventListener("click", () => {
    if (els.log) els.log.hidden = true;
  });
  els.sideTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      setSideTab(btn.getAttribute("data-comms-side-tab") || "inbox");
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (els.contactsModal && !els.contactsModal.hidden) {
      closeContactsModal();
    }
  });

  root.querySelectorAll("[data-comms-insert]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const token = btn.getAttribute("data-comms-insert") || "";
      if (!els.body || !token) return;
      const start = els.body.selectionStart ?? els.body.value.length;
      const end = els.body.selectionEnd ?? start;
      const value = els.body.value;
      els.body.value = value.slice(0, start) + token + value.slice(end);
      els.body.focus();
      const pos = start + token.length;
      els.body.setSelectionRange(pos, pos);
      syncSendButton();
      refreshPreview();
    });
  });

  els.analytics?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-comms-open-campaign]");
    if (!btn) return;
    const id = btn.getAttribute("data-comms-open-campaign");
    openCampaignLog(id);
    startCampaignPoll(id);
  });

  let savedMode = "simple";
  try {
    savedMode = localStorage.getItem(MODE_KEY) || "simple";
  } catch (_) {
    savedMode = "simple";
  }
  // Start on WhatsApp (Simple). Switching to shop lists turns on Advanced helpers.
  setMode(savedMode === "advanced" ? "advanced" : "simple", { refresh: false });
  setAudience("sale");
  setSideTab("inbox");

  refreshStatus();
  pollTimer = setInterval(refreshStatus, 3500);
  window.addEventListener("beforeunload", () => {
    clearInterval(pollTimer);
    clearInterval(campaignPoll);
  });
})();
