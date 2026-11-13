/* MY-CHATBOT — WhatsApp-style shop chat client.
   Polling transport (no websockets needed): one combined endpoint returns
   the conversation list + new messages for the open thread. */
(function () {
  "use strict";

  var root = document.querySelector("[data-wa-app]");
  if (!root) return;

  // ------------------------------------------------------------------
  // Setup
  // ------------------------------------------------------------------

  var URLS = {
    poll: root.dataset.pollUrl,
    open: root.dataset.openUrl,
    group: root.dataset.groupUrl,
    groupUpdate: root.dataset.groupUpdateBase,
    groupDelete: root.dataset.groupDeleteBase,
    send: root.dataset.sendBase,
    read: root.dataset.readBase,
    typing: root.dataset.typingBase,
    history: root.dataset.historyBase,
    transfer: root.dataset.transferBase,
    transfers: root.dataset.transfersUrl,
    respond: root.dataset.respondBase,
    pendingConfirm: root.dataset.pendingBase,
    quickStockin: root.dataset.quickStockinBase,
    stockIn: root.dataset.stockinUrl,
    sale: root.dataset.saleUrl,
    expense: root.dataset.expenseUrl,
    itemSearch: root.dataset.itemSearchUrl,
    supplierSearch: root.dataset.supplierSearchUrl,
    clientSearch: root.dataset.clientSearchUrl,
    serialSearch: root.dataset.serialSearchUrl,
    receiptItems: root.dataset.receiptItemsBase
  };

  function urlFor(base, id) {
    return base.replace("/0/", "/" + id + "/");
  }

  function readJson(id, fallback) {
    var el = document.getElementById(id);
    if (!el) return fallback;
    try { return JSON.parse(el.textContent); } catch (e) { return fallback; }
  }

  var me = readJson("chat-init-me", { id: 0, name: "" });
  var suggestions = readJson("chat-init-suggestions", []);
  var receiptStatusDefault = readJson("chat-init-receipt-status", "pending");
  if (receiptStatusDefault !== "confirmed" && receiptStatusDefault !== "pending") {
    receiptStatusDefault = "pending";
  }

  // ------------------------------------------------------------------
  // In-app history — Android / browser Back closes chat overlays
  // instead of exiting the installed PWA.
  // ------------------------------------------------------------------

  var navStack = [];
  var navQuiet = 0; // ignore popstate after programmatic history.go

  try {
    history.replaceState({ wa: "root" }, "", window.location.href);
  } catch (e) {}

  function pushNav(layer) {
    if (navStack[navStack.length - 1] === layer) return;
    navStack.push(layer);
    try {
      history.pushState({ wa: layer }, "", window.location.href);
    } catch (e) {}
  }

  function applyNavClose(layer) {
    if (layer === "scanner") closeScannerUi();
    else if (layer === "stock") closeStockUi();
    else if (layer === "groupSettings") hideGroupSettingsUi();
    else if (layer === "contacts") hideContactsUi();
    else if (layer === "chat") closeChatUi();
  }

  function requestNavClose(layer) {
    var idx = navStack.lastIndexOf(layer);
    if (idx < 0) {
      applyNavClose(layer);
      return;
    }
    // Close this layer and anything stacked above it (e.g. scanner
    // above stock), then rewind browser history to match.
    var steps = navStack.length - idx;
    while (navStack.length > idx) applyNavClose(navStack.pop());
    if (steps > 0) {
      navQuiet += steps;
      try {
        history.go(-steps);
      } catch (e) {
        navQuiet -= steps;
      }
    }
  }

  window.addEventListener("popstate", function () {
    if (navQuiet > 0) {
      navQuiet -= 1;
      return;
    }
    var layer = navStack.pop();
    if (layer) applyNavClose(layer);
  });

  var state = {
    conversations: readJson("chat-init-conversations", []),
    contacts: readJson("chat-init-contacts", []),
    activeId: null,
    activeKind: null,
    messages: [],
    lastMsgId: 0,
    oldestMsgId: 0,
    hasMore: false,
    peer: null,
    timer: null,
    pollDelay: 2500,
    pendingTags: {},
    filter: "",
    contactFilter: "",
    groupMode: false,
    groupSel: {},
    lastTypingSent: 0,
    botTopic: "",
    periodMode: "day",
    receiptStatus: receiptStatusDefault,
    stock: { mode: null, lines: [], picked: null, serials: [], submitting: false },
    rcvSerials: {},
    toastTimer: null,
    muted: false,
    lastSoundAt: 0
  };

  var els = {
    chats: root.querySelector("[data-wa-chats]"),
    search: root.querySelector("[data-wa-search]"),
    newBtn: root.querySelector("[data-wa-new]"),
    empty: root.querySelector("[data-wa-empty]"),
    thread: root.querySelector("[data-wa-thread]"),
    stage: root.querySelector("[data-wa-stage]"),
    loadMore: root.querySelector("[data-wa-load-more]"),
    headName: root.querySelector("[data-wa-head-name]"),
    headMeta: root.querySelector("[data-wa-head-meta]"),
    headAvatar: root.querySelector("[data-wa-head-avatar]"),
    back: root.querySelector("[data-wa-back]"),
    composer: root.querySelector("[data-wa-composer]"),
    input: root.querySelector("[data-wa-input]"),
    sendBtn: root.querySelector("[data-wa-send]"),
    emojiBtn: root.querySelector("[data-wa-emoji]"),
    emojiPop: root.querySelector("[data-wa-emoji-pop]"),
    file: root.querySelector("[data-wa-file]"),
    suggestionsBar: root.querySelector("[data-wa-suggestions]"),
    period: root.querySelector("[data-wa-period]"),
    periodTopic: root.querySelector("[data-wa-period-topic]"),
    dateDay: root.querySelector("[data-wa-date-day]"),
    dateFrom: root.querySelector("[data-wa-date-from]"),
    dateTo: root.querySelector("[data-wa-date-to]"),
    monthInput: root.querySelector("[data-wa-month]"),
    yearSel: root.querySelector("[data-wa-year]"),
    periodGo: root.querySelector("[data-wa-period-go]"),
    periodClose: root.querySelector("[data-wa-period-close]"),
    receiptScope: root.querySelector("[data-wa-receipt-scope]"),
    receiptStatusSel: root.querySelector("[data-wa-receipt-status]"),
    contacts: root.querySelector("[data-wa-contacts]"),
    contactList: root.querySelector("[data-wa-contact-list]"),
    contactSearch: root.querySelector("[data-wa-contact-search]"),
    contactsClose: root.querySelector("[data-wa-contacts-close]"),
    contactsTitle: root.querySelector("[data-wa-contacts-title]"),
    groupName: root.querySelector("[data-wa-groupname]"),
    groupFoot: root.querySelector("[data-wa-groupfoot]"),
    groupCreate: root.querySelector("[data-wa-groupcreate]"),
    groupCreateLabel: root.querySelector("[data-wa-groupcreate-label]"),
    groupSettingsBtn: root.querySelector("[data-wa-group-settings]"),
    groupPanel: root.querySelector("[data-wa-group-panel]"),
    groupPanelClose: root.querySelector("[data-wa-group-panel-close]"),
    groupRename: root.querySelector("[data-wa-group-rename]"),
    groupSave: root.querySelector("[data-wa-group-save]"),
    groupDelete: root.querySelector("[data-wa-group-delete]"),
    groupActivityHint: root.querySelector("[data-wa-group-activity-hint]"),
    themeToggle: root.querySelector("[data-theme-toggle]"),
    mute: root.querySelector("[data-wa-mute]"),
    plus: root.querySelector("[data-wa-plus]"),
    menu: root.querySelector("[data-wa-menu]"),
    stockPanel: root.querySelector("[data-stock-panel]"),
    stockBody: root.querySelector(".wa-stock__body"),
    stockTitle: root.querySelector("[data-stock-title]"),
    stockDestWrap: root.querySelector("[data-stock-dest-wrap]"),
    stockDest: root.querySelector("[data-stock-dest]"),
    stockItems: root.querySelector("[data-stock-items]"),
    stockCount: root.querySelector("[data-stock-count]"),
    stockEmpty: root.querySelector("[data-stock-empty]"),
    stockItemInput: root.querySelector("[data-stock-item-input]"),
    stockItemSuggest: root.querySelector("[data-stock-item-suggest]"),
    stockQty: root.querySelector("[data-stock-qty]"),
    stockSerialWrap: root.querySelector("[data-stock-serialwrap]"),
    stockSerial: root.querySelector("[data-stock-serial]"),
    stockSerials: root.querySelector("[data-stock-serials]"),
    stockSerialCount: root.querySelector("[data-stock-serialcount]"),
    stockSerialSuggest: root.querySelector("[data-stock-serial-suggest]"),
    stockScanBtn: root.querySelector("[data-stock-scanbtn]"),
    scanModal: root.querySelector("[data-scan-modal]"),
    scanReader: root.querySelector("[data-scan-reader]"),
    scanClose: root.querySelector("[data-scan-close]"),
    stockPrice: root.querySelector("[data-stock-price]"),
    stockAdd: root.querySelector("[data-stock-add]"),
    stockSupplier: root.querySelector("[data-stock-supplier]"),
    stockPayment: root.querySelector("[data-stock-payment]"),
    stockClient: root.querySelector("[data-stock-client]"),
    stockCheckout: root.querySelector("[data-stock-checkout]"),
    stockDetails: root.querySelector("[data-stock-details]"),
    stockDetailsToggle: root.querySelector("[data-stock-detailstoggle]"),
    stockDetailsToggleLabel: root.querySelector("[data-stock-detailstoggle-label]"),
    stockTradeKinds: root.querySelector("[data-stock-tradekinds]"),
    stockTradeHint: root.querySelector("[data-stock-tradehint]"),
    stockItemWrap: root.querySelector("[data-stock-itemwrap]"),
    stockExpense: root.querySelector("[data-stock-expense]"),
    stockItemAdd: root.querySelector("[data-stock-itemadd]"),
    stockListLabel: root.querySelector("[data-stock-listlabel]"),
    expenseCategory: root.querySelector("[data-expense-category]"),
    expenseName: root.querySelector("[data-expense-name]"),
    expenseAmount: root.querySelector("[data-expense-amount]"),
    expenseStatus: root.querySelector("[data-expense-status]"),
    expenseAdd: root.querySelector("[data-expense-add]"),
    stockKind: root.querySelector("[data-stock-kind]"),
    stockDueDate: root.querySelector("[data-stock-duedate]"),
    stockPayMethod: root.querySelector("[data-stock-paymethod]"),
    stockPaySplit: root.querySelector("[data-stock-paysplit]"),
    stockCashAmt: root.querySelector("[data-stock-cashamt]"),
    stockMpesaAmt: root.querySelector("[data-stock-mpesaamt]"),
    stockClientName: root.querySelector("[data-stock-clientname]"),
    stockClientPhone: root.querySelector("[data-stock-clientphone]"),
    stockStaffCode: root.querySelector("[data-stock-staffcode]"),
    stockCodeBox: root.querySelector("[data-stock-codebox]"),
    stockCodeHint: root.querySelector(".wa-stock__codehint"),
    stockClientNameSuggest: root.querySelector("[data-stock-clientname-suggest]"),
    stockClientPhoneSuggest: root.querySelector("[data-stock-clientphone-suggest]"),
    stockWhatsappWrap: root.querySelector("[data-stock-whatsapp-wrap]"),
    stockWhatsapp: root.querySelector("[data-stock-whatsapp]"),
    stockPhone: root.querySelector("[data-stock-phone]"),
    stockPhoneSuggest: root.querySelector("[data-stock-phone-suggest]"),
    stockSupname: root.querySelector("[data-stock-supname]"),
    stockNameSuggest: root.querySelector("[data-stock-name-suggest]"),
    stockError: root.querySelector("[data-stock-error]"),
    stockSubmit: root.querySelector("[data-stock-submit]"),
    stockTotal: root.querySelector("[data-stock-total]"),
    stockTotalVal: root.querySelector("[data-stock-total-val]"),
    stockSubmitLabel: root.querySelector("[data-stock-submit-label]"),
    stockClose: root.querySelector("[data-stock-close]"),
    toast: root.querySelector("[data-wa-toast]"),
    fabs: root.querySelector("[data-wa-fabs]")
  };

  function csrfToken() {
    var input = root.querySelector("input[name=csrfmiddlewaretoken]");
    if (input) return input.value;
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : "";
  }

  function post(url, payload, isForm) {
    var opts = {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken() },
      credentials: "same-origin"
    };
    if (isForm) {
      opts.body = payload;
    } else {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(payload || {});
    }
    return fetch(url, opts).then(function (r) { return r.json(); });
  }

  function refreshIcons() {
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  // ------------------------------------------------------------------
  // Formatting helpers
  // ------------------------------------------------------------------

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // WhatsApp-flavoured inline markup: *bold* _italic_ ~strike~ + links.
  function formatBody(text) {
    var html = escapeHtml(text);
    html = html.replace(
      /%%items\|([0-9-]+)\|([0-9-]+)(?:\|(pending|confirmed))?%%/g,
      '<a class="wa-link" href="#" data-bitems="$1:$2:$3">View items sold →</a>'
    );
    html = html.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
    html = html.replace(/\*([^*\n]+)\*/g, "<strong>$1</strong>");
    html = html.replace(/_([^_\n]+)_/g, "<em>$1</em>");
    html = html.replace(/~([^~\n]+)~/g, "<s>$1</s>");
    return html;
  }

  function pad2(n) { return n < 10 ? "0" + n : "" + n; }

  function timeOf(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  var MONTHS = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];

  function dayKey(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function dayLabel(iso) {
    var key = dayKey(iso);
    var today = dayKey(new Date().toISOString());
    var yesterday = dayKey(new Date(Date.now() - 864e5).toISOString());
    if (key === today) return "Today";
    if (key === yesterday) return "Yesterday";
    var d = new Date(iso);
    return d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear();
  }

  function listTime(iso) {
    var key = dayKey(iso);
    if (key === dayKey(new Date().toISOString())) return timeOf(iso);
    if (key === dayKey(new Date(Date.now() - 864e5).toISOString())) return "Yesterday";
    var d = new Date(iso);
    return pad2(d.getDate()) + "/" + pad2(d.getMonth() + 1) + "/" + d.getFullYear();
  }

  function lastSeenText(peer) {
    if (!peer) return "";
    if (peer.typing) return "typing…";
    if (peer.online) return "online";
    if (!peer.last_seen) return "";
    return "last seen " + dayLabel(peer.last_seen).toLowerCase() + " at " + timeOf(peer.last_seen);
  }

  function tickHtml(status, pending) {
    if (pending) return '<span class="wa-tick wa-tick--pending">🕓</span>';
    if (status === "read") return '<span class="wa-tick wa-tick--read">✓✓</span>';
    if (status === "delivered") return '<span class="wa-tick">✓✓</span>';
    return '<span class="wa-tick">✓</span>';
  }

  // ------------------------------------------------------------------
  // Conversation list
  // ------------------------------------------------------------------

  function sortedConversations() {
    var list = state.conversations.slice();
    list.sort(function (a, b) {
      if (a.bot !== b.bot) return a.bot ? -1 : 1; // assistant pinned first
      var at = a.last_message ? a.last_message.created_at : "";
      var bt = b.last_message ? b.last_message.created_at : "";
      return bt.localeCompare(at);
    });
    if (state.filter) {
      var q = state.filter.toLowerCase();
      list = list.filter(function (c) {
        return c.title.toLowerCase().indexOf(q) !== -1;
      });
    }
    return list;
  }

  function renderChatList() {
    var html = "";
    sortedConversations().forEach(function (c) {
      var last = c.last_message || {};
      var preview;
      if (c.typing) {
        preview = '<span class="wa-chat__preview is-typing">typing…</span>';
      } else {
        var tick = "";
        if (last.mine && last.status) {
          tick = tickHtml(last.status) + " ";
        }
        preview = '<span class="wa-chat__preview">' + tick +
          escapeHtml(last.preview || "Start chatting") + "</span>";
      }
      var avatar = c.bot
        ? '<span class="comms-avatar" data-tone="2"><i data-lucide="bot"></i></span>'
        : c.kind === "group"
        ? '<span class="comms-avatar" data-tone="' + c.tone +
          '"><i data-lucide="users"></i></span>'
        : '<span class="comms-avatar" data-tone="' + c.tone + '">' +
          escapeHtml(c.initials) + "</span>";
      html +=
        '<button type="button" class="wa-chat' +
        (c.id === state.activeId ? " is-active" : "") +
        (c.unread ? " is-unread" : "") +
        '" data-convo="' + c.id + '" role="listitem">' +
        avatar +
        '<span class="wa-chat__main">' +
        '<span class="wa-chat__top"><strong>' + escapeHtml(c.title) +
        "</strong><time>" + escapeHtml(last.created_at ? listTime(last.created_at) : "") +
        "</time></span>" +
        preview +
        "</span>" +
        (c.unread ? '<span class="wa-chat__badge">' + c.unread + "</span>" : "") +
        "</button>";
    });
    if (!html) {
      html = '<p class="wa-app__none">No chats yet — tap “New chat” to message a shop.</p>';
    }
    els.chats.innerHTML = html;
    refreshIcons();
  }

  // ------------------------------------------------------------------
  // Thread rendering
  // ------------------------------------------------------------------

  function setHead(convo) {
    if (!convo) return;
    els.headName.textContent = convo.title;
    if (convo.bot) {
      els.headMeta.textContent = convo.subtitle;
      els.headMeta.className = "";
      els.headAvatar.outerHTML =
        '<span class="comms-avatar" data-wa-head-avatar data-tone="2"><i data-lucide="bot"></i></span>';
      els.headAvatar = root.querySelector("[data-wa-head-avatar]");
    } else if (convo.kind === "group") {
      els.headMeta.textContent = convo.subtitle;
      els.headMeta.className = "";
      els.headAvatar.outerHTML =
        '<span class="comms-avatar" data-wa-head-avatar data-tone="' + convo.tone +
        '"><i data-lucide="users"></i></span>';
      els.headAvatar = root.querySelector("[data-wa-head-avatar]");
    } else {
      els.headAvatar.outerHTML =
        '<span class="comms-avatar" data-wa-head-avatar data-tone="' + convo.tone +
        '">' + escapeHtml(convo.initials) + "</span>";
      els.headAvatar = root.querySelector("[data-wa-head-avatar]");
    }
    if (els.groupSettingsBtn) {
      els.groupSettingsBtn.hidden = !(convo.kind === "group" && convo.can_manage !== false);
    }
    refreshIcons();
  }

  function updateHeadMeta() {
    if (!state.activeId) return;
    var convo = findConvo(state.activeId);
    if (!convo) return;
    if (convo.bot) {
      els.headMeta.textContent = convo.subtitle;
      els.headMeta.className = "";
      return;
    }
    if (convo.kind === "group") {
      var p = state.peer || {};
      els.headMeta.textContent = p.typing
        ? (p.typing_name ? p.typing_name + " is " : "") + "typing…"
        : convo.subtitle || "";
      els.headMeta.className = p.typing ? "is-online" : "";
      return;
    }
    var text = lastSeenText(state.peer);
    els.headMeta.textContent = text || convo.subtitle || "";
    els.headMeta.className = state.peer && (state.peer.online || state.peer.typing)
      ? "is-online" : "";
  }

  function statCard(icon, title, rows, foot) {
    return (
      '<div class="wa-card"><div class="wa-card__head wa-card__head--in">' +
      '<i data-lucide="' + icon + '"></i><span>' + escapeHtml(title) +
      "</span></div>" +
      '<div class="wa-stats">' +
      rows.map(function (r) {
        return (
          '<div class="wa-stats__row' + (r[2] ? " is-total" : "") + '">' +
          "<span>" + escapeHtml(r[0]) + "</span>" +
          "<b>" + escapeHtml(String(r[1])) + "</b></div>"
        );
      }).join("") +
      "</div>" +
      (foot ? '<div class="wa-card__foot">' + foot + "</div>" : "") +
      "</div>"
    );
  }

  function tableCard(icon, title, head, body, foot, tableCls) {
    return (
      '<div class="wa-card"><div class="wa-card__head wa-card__head--in">' +
      '<i data-lucide="' + icon + '"></i><span>' + escapeHtml(title) +
      "</span></div>" +
      '<div class="wa-card__tablewrap"><table class="wa-table' +
      (tableCls ? " " + tableCls : "") + '"><thead><tr>' +
      head.map(function (h) {
        var hc = ((h[1] ? "n" : "") + " " + (h[2] || "")).trim();
        return "<th" + (hc ? ' class="' + hc + '"' : "") + ">" +
          escapeHtml(h[0]) + "</th>";
      }).join("") +
      "</tr></thead><tbody>" + body + "</tbody></table></div>" +
      (foot ? '<div class="wa-card__foot">' + foot + "</div>" : "") +
      "</div>"
    );
  }

  // Credits card — tappable customer name reveals their number (tap-to-call),
  // the receipt's eye icon expands the bought lines in place.
  function creditCustCell(r) {
    var phone = (r.phone || "").trim();
    return (
      '<button class="wa-cust" type="button" data-custphone>' +
      escapeHtml(r.customer) + "</button>" +
      '<div class="wa-phone" hidden>' +
      (phone
        ? '<i data-lucide="phone"></i><a class="wa-link" href="tel:' +
          escapeHtml(phone.replace(/[^+\d]/g, "")) + '">' +
          escapeHtml(phone) + "</a>"
        : '<span class="wa-phone__none">No phone on file</span>') +
      "</div>"
    );
  }

  function creditReceiptCell(r) {
    return (
      '<span class="wa-rcpt">' + escapeHtml(r.receipt) +
      (r.rid
        ? '<button class="wa-eyebtn" type="button" data-ritems="' + r.rid +
          '" title="View items"><i data-lucide="eye"></i></button>'
        : "") +
      "</span>"
    );
  }

  var receiptItemsCache = {};

  function receiptItemsHtml(data) {
    var rows = (data.items || []).map(function (it, i) {
      var sn =
        it.serials && it.serials.length
          ? '<tr class="wa-tr--sn"><td></td><td colspan="4">' +
            '<div class="wa-card__sns">' +
            it.serials.map(escapeHtml).join(" · ") + "</div></td></tr>"
          : "";
      return (
        '<tr><td class="n">' + (i + 1) + "</td><td>" +
        escapeHtml(it.name) + '</td><td class="n">' + escapeHtml(it.qty) +
        '</td><td class="n">' + escapeHtml(it.price) + '</td><td class="n">' +
        escapeHtml(it.total) + "</td></tr>" + sn
      );
    }).join("");
    var rc = data.receipt || {};
    return (
      '<div class="wa-ritems__head">Receipt ' +
      escapeHtml(rc.number || "") + " · " + escapeHtml(rc.customer || "") +
      "</div>" +
      '<table class="wa-table wa-table--mini"><thead><tr><th>#</th>' +
      "<th>Item</th><th class=\"n\">Qty</th><th class=\"n\">Price</th>" +
      '<th class="n">Total</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="5">No lines on this receipt.</td></tr>') +
      "</tbody></table>" +
      '<div class="wa-ritems__foot"><b>' + escapeHtml(rc.total || "") +
      "</b> · paid " + escapeHtml(rc.paid || "") + "</div>"
    );
  }

  function toggleReceiptItems(btn) {
    var tr = btn.closest("tr");
    if (!tr || !tr.parentNode) return;
    var next = tr.nextElementSibling;
    if (next && next.classList.contains("wa-ritems")) {
      next.hidden = !next.hidden;
      return;
    }
    var rid = btn.dataset.ritems;
    var detail = document.createElement("tr");
    detail.className = "wa-ritems";
    var td = document.createElement("td");
    td.colSpan = 99;
    var box = document.createElement("div");
    box.className = "wa-ritems__box";
    td.appendChild(box);
    detail.appendChild(td);
    tr.parentNode.insertBefore(detail, tr.nextSibling);

    var cached = receiptItemsCache[rid];
    if (cached) {
      box.innerHTML = cached;
      return;
    }
    box.textContent = "Loading…";
    fetch(urlFor(URLS.receiptItems, rid), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (!resp.ok) {
          box.innerHTML = '<span class="wa-ritems__err">' +
            escapeHtml(resp.error || "Could not load items.") + "</span>";
          return;
        }
        var html = receiptItemsHtml(resp);
        receiptItemsCache[rid] = html;
        box.innerHTML = html;
        refreshIcons();
      })
      .catch(function () {
        box.innerHTML =
          '<span class="wa-ritems__err">Connection problem — try again.</span>';
      });
  }

  function cardHtml(card, mine, msgId) {
    var t = card.transfer, s = card.stock_in, it = card.items_sold;
    if (card.sale) {
      var sl = card.sale;
      var srows = "";
      (sl.items || []).forEach(function (i, idx) {
        var snHtml =
          i.serials && i.serials.length
            ? '<tr class="wa-tr--sn"><td></td><td colspan="4">' +
              '<div class="wa-card__sns">' +
              i.serials.map(escapeHtml).join(" · ") + "</div></td></tr>"
            : "";
        srows +=
          '<tr><td class="n">' + (idx + 1) + "</td><td>" + escapeHtml(i.name) +
          '</td><td class="n">' + escapeHtml(i.qty) + '</td><td class="n">' +
          escapeHtml(i.price) + '</td><td class="n">' + escapeHtml(i.total) +
          "</td></tr>" + snHtml;
      });
      return tableCard(sl.icon || "shopping-cart",
        (sl.label || "Sale") + " " + (sl.receipt || ""), [
        ["#"], ["Item"], ["Qty", 1], ["Price", 1], ["Total", 1]
      ], srows,
        "<b>" + escapeHtml(sl.total) + "</b>" +
          (sl.payment && sl.payment !== "none" ? " via " + escapeHtml(sl.payment) : "") +
          (sl.client ? " · " + escapeHtml(sl.client) : "") +
          (sl.due ? " · due " + escapeHtml(sl.due) : ""));
    }
    if (card.sales) {
      var c = card.sales;
      var itemsLink = c.start && c.end
        ? '<a class="wa-link" href="#" data-bitems="' +
          escapeHtml(c.start + ":" + c.end + ":" + (c.receipt_status || "")) +
          '">View items sold →</a>'
        : "";
      return statCard("trending-up", "Sales — " + c.label, [
        ["Total", c.total, 1],
        ["Receipts", c.receipts],
        ["Cash", c.cash],
        ["M-Pesa", c.mpesa],
        ["Credit issued", c.credit]
      ], itemsLink);
    }
    if (card.summary) {
      var u = card.summary;
      return statCard("clipboard-list", u.label.charAt(0).toUpperCase() +
        u.label.slice(1) + " at a glance", [
        ["Sales", u.sales, 1],
        ["Receipts", u.receipts],
        ["Expenses", u.expenses],
        ["Outstanding credit", u.credit]
      ]);
    }
    if (card.credits) {
      var cr = card.credits;
      var crows = (cr.rows || []).map(function (r, i) {
        return '<tr><td class="n wa-cnum">' + (i + 1) + "</td><td>" +
          creditCustCell(r) + "</td><td>" + creditReceiptCell(r) +
          '</td><td class="n">' + escapeHtml(r.balance) + '</td><td class="n">' +
          escapeHtml(r.due || "—") + "</td></tr>";
      }).join("");
      return tableCard("hand-coins", "Outstanding credit" +
        (cr.label ? " · " + cr.label : ""), [
        ["#", 0, "wa-cnum"], ["Customer"], ["Receipt"], ["Balance", 1],
        ["Due", 1]
      ], crows, "<b>" + escapeHtml(cr.total) + "</b> across " + cr.count +
        " receipts", "wa-table--credits");
    }
    if (card.expense) {
      var xp = card.expense;
      var xitems = xp.items || [];
      if (xitems.length > 1) {
        var xrows = xitems.map(function (r, i) {
          return "<tr><td class=\"n\">" + (i + 1) + "</td><td>" +
            escapeHtml(r.name) + "</td><td>" +
            escapeHtml(r.category || "") +
            "</td><td class=\"n\">" + escapeHtml(r.amount) + "</td></tr>";
        }).join("");
        return tableCard("wallet", "Expenses", [
          ["#"], ["Expense"], ["Category"], ["Amount", 1]
        ], xrows, "<b>KSh " + escapeHtml(xp.amount || "0.00") + "</b>" +
          (xp.status ? " · " + escapeHtml(xp.status) : "") +
          (xp.supplier ? " · " + escapeHtml(xp.supplier) : "") +
          (xp.shop ? " · " + escapeHtml(xp.shop) : ""));
      }
      return statCard("wallet", "Expense", [
        ["Expense", xp.name],
        ["Category", xp.category || "—"],
        ["Amount", "KSh " + (xp.amount || "0.00"), 1]
      ], (xp.status || "") +
        (xp.supplier ? " · " + escapeHtml(xp.supplier) : "") +
        (xp.shop ? " · " + escapeHtml(xp.shop) : ""));
    }
    if (card.expenses) {
      var ex = card.expenses;
      var erows = (ex.rows || []).map(function (r, i) {
        return "<tr><td class=\"n\">" + (i + 1) + "</td><td>" +
          escapeHtml(r.name) + "</td><td>" + escapeHtml(r.cat) +
          "</td><td class=\"n\">" + escapeHtml(r.amount) + "</td><td class=\"n\">" +
          escapeHtml(r.status || "") + "</td></tr>";
      }).join("");
      return tableCard("receipt", "Expenses — " + ex.label, [
        ["#"], ["Item"], ["Category"], ["Amount", 1], ["Status", 1]
      ], erows, "Total <b>" + escapeHtml(ex.total) + "</b>" +
        (ex.unpaid !== "0.00" && ex.unpaid !== "KSh 0.00"
          ? " · unpaid " + escapeHtml(ex.unpaid) : ""));
    }
    if (card.movement) {
      var mv = card.movement;
      if (mv.groups) {
        var nlines = 0;
        var secs = mv.groups.map(function (g) {
          nlines += (g.items || []).length;
          var badge =
            g.kind === "in" ? "+IN" : g.kind === "out" ? "−OUT" :
            (g.kind === "tout" ? "→ " : "← ") + g.head;
          var meta = [g.status, g.when].filter(Boolean).join(" · ");
          var lines = (g.items || []).map(function (r, i) {
            return "<tr><td class=\"n\">" + (i + 1) + "</td><td>" +
              escapeHtml(r.item) + "</td><td class=\"n\">" +
              escapeHtml(r.qty) + "</td></tr>";
          }).join("");
          return (
            '<div class="wa-mvg"><div class="wa-mvg__head">' +
            '<span class="wa-mv--' + g.kind + '">' + escapeHtml(badge) +
            "</span>" +
            (meta ? '<span class="wa-mvg__meta">' + escapeHtml(meta) +
              "</span>" : "") +
            "</div>" +
            '<table class="wa-table"><thead><tr><th>#</th><th>Item</th>' +
            '<th class="n">Qty</th></tr></thead><tbody>' + lines +
            "</tbody></table></div>"
          );
        }).join("");
        return (
          '<div class="wa-card"><div class="wa-card__head wa-card__head--in">' +
          '<i data-lucide="arrow-right-left"></i><span>Stock movement — ' +
          escapeHtml(mv.label) + "</span></div>" +
          '<div class="wa-card__tablewrap">' + secs + "</div>" +
          '<div class="wa-card__foot">' + mv.groups.length +
          " movements · " + nlines + " lines</div></div>"
        );
      }
      var mrows = (mv.rows || []).map(function (r) {
        var badge =
          r.kind === "in" ? "+IN" : r.kind === "out" ? "−OUT" :
          r.kind === "tout" ? "→ " + r.head : "← " + r.head;
        return "<tr><td class=\"wa-mv--" + r.kind + "\">" +
          escapeHtml(badge) + "</td><td>" + escapeHtml(r.item) +
          "</td><td class=\"n\">" + escapeHtml(r.qty) + "</td><td class=\"n\">" +
          escapeHtml(r.status || "") + "</td></tr>";
      }).join("");
      return tableCard("arrow-right-left", "Stock movement — " + mv.label, [
        ["Type"], ["Item"], ["Qty", 1], ["Status", 1]
      ], mrows, (mv.rows || []).length + " lines");
    }
    if (it) {
      var rows = (it.items || []).map(function (r, i) {
        return (
          "<tr><td class=\"n\">" + (i + 1) + "</td>" +
          "<td>" + escapeHtml(r.name) + "</td>" +
          "<td class=\"n\">" + escapeHtml(r.qty) + "</td>" +
          "<td class=\"n\">" + escapeHtml(r.total) + "</td></tr>"
        );
      }).join("");
      return (
        '<div class="wa-card"><div class="wa-card__head wa-card__head--in">' +
        '<i data-lucide="list"></i><span>Items sold — ' +
        escapeHtml(it.label || "") + "</span></div>" +
        '<div class="wa-card__tablewrap"><table class="wa-table">' +
        "<thead><tr><th>#</th><th>Item</th><th class=\"n\">Qty</th>" +
        "<th class=\"n\">Amount</th></tr></thead><tbody>" +
        rows + "</tbody></table></div>" +
        '<div class="wa-card__foot">' + (it.items || []).length +
        " items</div></div>"
      );
    }
    if (t) {
      var pending = t.status === "pending";
      var confirmRows = !mine && pending && !t.readonly;
      // Blind receiving: the server blanks the sent quantities until the
      // transfer is fulfilled — show "?" and never leak a serial count.
      var blind = !!t.blind;
      var rows = "";
      (t.items || []).forEach(function (i, idx) {
        var snTotal = i.serial_count || (i.serials || []).length;
        var expectsSn = snTotal > 0 || (blind && i.expect_serials);
        var rcvCell = "";
        var snHtml = "";
        if (confirmRows && expectsSn) {
          var done = ((state.rcvSerials[t.movement_id] || {})[i.line_id] || [])
            .length;
          rcvCell = '<span class="wa-card__rcv" data-snprog="' + i.line_id +
            '">' + done + (snTotal ? "/" + snTotal : "") + " SN</span>";
          // Serial values are masked by the server — the receiver scans each
          // unit; scanned serials show as green chips.
          var matched =
            (state.rcvSerials[t.movement_id] || {})[i.line_id] || [];
          snHtml =
            '<div class="wa-card__sns" data-snbox="' + i.line_id + '">' +
            matched.map(function (s) {
              return '<span class="wa-card__sn is-ok">' + escapeHtml(s) +
                "</span>";
            }).join("") +
            (snTotal - matched.length > 0
              ? '<span class="wa-card__sn is-pending">' +
                (snTotal - matched.length) + " left</span>"
              : "") +
            "</div>" +
            '<input class="wa-card__sninput" type="text" autocomplete="off" ' +
            'placeholder="Scan/type each SN, press Enter" data-rcvsn="' +
            t.movement_id + ":" + i.line_id + '">';
        } else if (confirmRows && i.line_id) {
          rcvCell =
            '<input class="wa-card__qty" type="number" min="0" step="any" ' +
            'placeholder="0" data-rcv="' + i.line_id + '">';
        } else if (snTotal) {
          rcvCell = snTotal + " SN";
          if (i.serials && i.serials.length) {
            snHtml = '<div class="wa-card__sns">' +
              i.serials.map(escapeHtml).join(" · ") + "</div>";
          }
        }
        rows +=
          '<tr><td class="n">' + (idx + 1) + "</td><td>" + escapeHtml(i.name) +
          '</td><td class="n">' + (blind ? "?" : escapeHtml(i.qty)) +
          '</td><td class="n">' +
          rcvCell + "</td></tr>" +
          (snHtml
            ? '<tr class="wa-tr--sn"><td></td><td colspan="3">' + snHtml +
              "</td></tr>"
            : "");
      });
      var foot;
      if (t.status === "fulfilled") {
        foot = '<div class="wa-card__foot is-in">Fulfilled — stock moved' +
          (t.responded_by ? " · by " + escapeHtml(t.responded_by) : "") +
          ".</div>";
      } else if (t.status === "declined") {
        foot = '<div class="wa-card__foot">Declined' +
          (t.responded_by ? " by " + escapeHtml(t.responded_by) : "") +
          (t.response_note ? ": " + escapeHtml(t.response_note) : "") + "</div>";
      } else if (mine || t.readonly) {
        foot = '<div class="wa-card__foot">To ' + escapeHtml(t.to_shop) +
          " · awaiting their confirmation</div>";
      } else {
        foot =
          '<div class="wa-card__foot">Incoming from ' +
          escapeHtml(t.from_shop) +
          " — type the amount you received and your staff code, then " +
          "Accept. A wrong amount won't confirm.</div>" +
          '<input class="wa-card__sninput" type="password" inputmode="numeric" ' +
          'maxlength="6" autocomplete="off" spellcheck="false" ' +
          'placeholder="Your 6-digit staff code" data-tcode="' +
          t.movement_id + '">' +
          '<div class="wa-card__actions" data-tcard="' + t.movement_id + '">' +
          '<button type="button" class="wa-card__btn wa-card__btn--ok" data-taccept="' +
          t.movement_id + '">Accept</button>' +
          '<button type="button" class="wa-card__btn" data-tdecline="' +
          t.movement_id + '">Decline</button></div>' +
          '<div class="wa-card__decline" data-treasonwrap="' + t.movement_id +
          '" hidden><input type="text" placeholder="Reason for declining…" ' +
          'data-treason="' + t.movement_id + '">' +
          '<button type="button" class="wa-card__btn" data-tsenddecline="' +
          t.movement_id + '">Send</button></div>' +
          '<div class="wa-card__err" data-terr="' + t.movement_id + '" hidden></div>';
      }
      return '<div class="wa-card" data-tcard="' + t.movement_id + '">' +
        '<div class="wa-card__head wa-card__head--' +
        (mine ? "out" : "in") + '"><i data-lucide="arrow-right-left"></i>' +
        "<span>Stock transfer · " +
        escapeHtml(mine ? t.to_shop : t.from_shop) +
        (mine ? "" : " → you") + "</span></div>" +
        '<div class="wa-card__tablewrap"><table class="wa-table"><thead><tr>' +
        "<th>#</th><th>Item</th><th class=\"n\">Qty</th><th class=\"n\">" +
        (confirmRows ? "Received" : "SN") + "</th></tr></thead><tbody>" +
        rows + "</tbody></table></div>" + foot + "</div>";
    }
    if (s) {
      var srows = "";
      (s.items || []).forEach(function (i, idx) {
        var snHtml =
          i.serials && i.serials.length
            ? '<tr class="wa-tr--sn"><td></td><td colspan="3">' +
              '<div class="wa-card__sns">' +
              i.serials.map(escapeHtml).join(" · ") + "</div></td></tr>"
            : "";
        srows +=
          '<tr><td class="n">' + (idx + 1) + "</td><td>" + escapeHtml(i.name) +
          '</td><td class="n">' + escapeHtml(i.qty) + '</td><td class="n">' +
          escapeHtml(i.price || "—") + "</td></tr>" + snHtml;
      });
      return '<div class="wa-card"><div class="wa-card__head wa-card__head--in">' +
        '<i data-lucide="package-plus"></i><span>Stock received</span></div>' +
        '<div class="wa-card__tablewrap"><table class="wa-table"><thead><tr>' +
        "<th>#</th><th>Item</th><th class=\"n\">Qty</th><th class=\"n\">Price</th>" +
        "</tr></thead><tbody>" + srows + "</tbody></table></div>" +
        '<div class="wa-card__foot">From ' +
        escapeHtml(s.supplier || "supplier") + " · " +
        escapeHtml(s.shop || "") + "</div></div>";
    }
    if (card.pending_action) {
      // A request posted by non-member staff — a shop member's staff code
      // executes it. The confirmed card replaces this one on the server.
      var pa = card.pending_action;
      var prows = (pa.items || []).map(function (i, idx) {
        var snHtml =
          i.serials && i.serials.length
            ? '<tr class="wa-tr--sn"><td></td><td colspan="3">' +
              '<div class="wa-card__sns">' +
              i.serials.map(escapeHtml).join(" · ") + "</div></td></tr>"
            : "";
        return '<tr><td class="n">' + (idx + 1) + "</td><td>" +
          escapeHtml(i.name) + '</td><td class="n">' + escapeHtml(i.qty) +
          '</td><td class="n">' + escapeHtml(i.note || "") + "</td></tr>" +
          snHtml;
      }).join("");
      var pfoot;
      if (pa.status === "pending") {
        var shopGate = pa.shop_id && String(pa.shop_id) !== String(me.id);
        pfoot =
          '<div class="wa-card__foot">' + escapeHtml(pa.detail || "") +
          (pa.detail && pa.total ? " · " : "") +
          (pa.total ? "<b>" + escapeHtml(pa.total) + "</b>" : "") + "</div>" +
          '<div class="wa-card__foot">Pending confirmation' +
          (pa.requested_by ? " · requested by " +
            escapeHtml(pa.requested_by) : "") +
          (pa.shop_name
            ? " — a member of " + escapeHtml(pa.shop_name) +
              " enters their staff code to complete it."
            : " — a member of this shop enters their staff code to complete it.") +
          "</div>" +
          (shopGate
            ? ""
            : '<input class="wa-card__sninput" type="password" inputmode="numeric" ' +
              'maxlength="6" autocomplete="off" spellcheck="false" ' +
              'placeholder="Shop member staff code" data-pacode="' + msgId + '">' +
              '<div class="wa-card__actions">' +
              '<button type="button" class="wa-card__btn wa-card__btn--ok" ' +
              'data-paconfirm="' + msgId + '">Confirm</button></div>' +
              '<div class="wa-card__err" data-paerr="' + msgId + '" hidden></div>');
      } else {
        pfoot = '<div class="wa-card__foot is-in">' +
          escapeHtml(pa.status || "") + "</div>";
      }
      return '<div class="wa-card"><div class="wa-card__head ' +
        'wa-card__head--in"><i data-lucide="hourglass"></i><span>' +
        escapeHtml(pa.title || "Request") +
        " — pending confirmation</span></div>" +
        '<div class="wa-card__tablewrap"><table class="wa-table"><thead><tr>' +
        "<th>#</th><th>Item</th><th class=\"n\">Qty</th><th class=\"n\"></th>" +
        "</tr></thead><tbody>" + prows + "</tbody></table></div>" +
        pfoot + "</div>";
    }
    return "";
  }

  function messageHtml(m) {
    if (m.kind === "system") {
      return '<div class="wa-sys" data-msg="' + m.id + '"><span>' +
        escapeHtml(m.body) + "</span></div>";
    }
    var side = m.mine ? "is-out" : "is-in";
    var inner;
    if (m.meta && (m.meta.transfer || m.meta.stock_in || m.meta.items_sold ||
        m.meta.sales || m.meta.summary || m.meta.credits || m.meta.expenses ||
        m.meta.movement || m.meta.sale || m.meta.pending_action ||
        m.meta.expense)) {
      inner = cardHtml(m.meta, m.mine, m.id);
      if (m.meta.confirmed && m.meta.confirmed.by) {
        inner += '<div class="wa-card__confirmed">✓ Confirmed by ' +
          escapeHtml(m.meta.confirmed.by) + "</div>";
      }
      if (m.meta.by) {
        // Activity-feed cards name the staff member who ran the action.
        inner += '<div class="wa-card__confirmed">by ' +
          escapeHtml(m.meta.by) + "</div>";
      }
    } else if (m.kind === "image" && m.image_url) {
      inner = '<img class="wa-bubble__photo" src="' + escapeHtml(m.image_url) +
        '" alt="Photo" loading="lazy">';
      if (m.body) inner += "<p>" + formatBody(m.body) + "</p>";
    } else {
      inner = "<p>" + formatBody(m.body) + "</p>";
    }
    if (!m.bot && state.activeKind === "group" && m.sender_name) {
      inner = '<span class="wa-bubble__sender" data-stone="' +
        ((m.sender_id % 6) + 1) + '">' + escapeHtml(m.sender_name) +
        "</span>" + inner;
    }
    return '<div class="wa-bubble-row ' + side + '" data-msg="' + m.id + '">' +
      '<div class="wa-bubble ' + side + '">' +
      inner +
      '<span class="wa-bubble__meta">' + timeOf(m.created_at) +
      (m.mine ? " " + tickHtml(m.status, m.pending) : "") +
      "</span></div></div>";
  }

  function renderStage() {
    var html = "";
    var lastDay = "";
    state.messages.forEach(function (m) {
      var day = dayLabel(m.created_at);
      if (day !== lastDay) {
        html += '<div class="wa-day"><span>' + day + "</span></div>";
        lastDay = day;
      }
      html += messageHtml(m);
    });
    // Keep the load-more button (first child) then replace the rest.
    var keep = els.loadMore;
    els.stage.innerHTML = "";
    els.stage.appendChild(keep);
    els.loadMore.hidden = !state.hasMore;
    els.stage.insertAdjacentHTML("beforeend", html);
    refreshIcons();
  }

  function scrollBottom(smooth) {
    requestAnimationFrame(function () {
      els.stage.scrollTo({
        top: els.stage.scrollHeight,
        behavior: smooth ? "smooth" : "auto"
      });
    });
  }

  function nearBottom() {
    var gap = els.stage.scrollHeight - els.stage.scrollTop - els.stage.clientHeight;
    return gap < 140;
  }

  function findConvo(id) {
    for (var i = 0; i < state.conversations.length; i++) {
      if (state.conversations[i].id === id) return state.conversations[i];
    }
    return null;
  }

  // ------------------------------------------------------------------
  // Open / send / read / typing
  // ------------------------------------------------------------------

  function closeChatUi() {
    hideGroupSettingsUi();
    root.classList.remove("is-chat-open");
    state.activeId = null;
    state.activeKind = null;
    els.thread.hidden = true;
    els.empty.hidden = false;
    if (els.groupSettingsBtn) els.groupSettingsBtn.hidden = true;
    renderChatList();
  }

  function openConversation(id) {
    var convo = findConvo(id);
    if (!convo) return;
    state.activeId = id;
    state.activeKind = convo.kind;
    state.messages = [];
    state.lastMsgId = 0;
    state.hasMore = false;
    state.peer = null;
    els.empty.hidden = true;
    els.thread.hidden = false;
    root.classList.add("is-chat-open");
    setHead(convo);
    els.stage.innerHTML = "";
    els.stage.appendChild(els.loadMore);
    els.loadMore.hidden = true;
    els.suggestionsBar.hidden = convo.kind !== "bot";
    els.period.hidden = true;
    // Re-use the contacts history entry when opening a chat from the
    // picker so Back returns to the list in one step.
    if (navStack[navStack.length - 1] === "contacts") {
      navStack[navStack.length - 1] = "chat";
      try {
        history.replaceState({ wa: "chat" }, "", window.location.href);
      } catch (e) {}
    } else {
      pushNav("chat");
    }
    pollNow().then(function () { scrollBottom(false); });
    markRead();
    els.input.focus();
  }

  function openFromResponse(resp) {
    var convo = resp.conversation;
    var existing = findConvo(convo.id);
    if (!existing) state.conversations.push(convo);
    if (resp.active) {
      state.peer = resp.active.peer;
      state.messages = resp.active.messages || [];
      state.lastMsgId = state.messages.length
        ? state.messages[state.messages.length - 1].id : 0;
    }
    renderChatList();
    hideContactsUi();
    openConversation(convo.id);
  }

  function openByTarget(target) {
    post(URLS.open, { target: String(target) }).then(function (resp) {
      if (!resp.ok) return;
      openFromResponse(resp);
    });
  }

  function markRead() {
    if (!state.activeId || !state.messages.length) return;
    var last = state.messages[state.messages.length - 1];
    post(urlFor(URLS.read, state.activeId), { up_to_id: last.id });
  }

  var sendQueue = Promise.resolve();

  function sendText(body) {
    body = (body || "").trim();
    if (!body || !state.activeId) return;
    var tag = "m" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    var optimistic = {
      id: "tmp-" + tag, kind: "text", mine: true, bot: false,
      body: body, created_at: new Date().toISOString(),
      status: "sent", pending: true
    };
    state.messages.push(optimistic);
    renderStage();
    scrollBottom(true);
    els.input.value = "";
    autosize();

    sendQueue = sendQueue.then(function () {
      return post(urlFor(URLS.send, state.activeId), {
        body: body, client_tag: tag
      }).then(function (resp) {
        if (!resp.ok) {
          optimistic.failed = true;
          optimistic.body = body + "\n⚠ " + (resp.error || "Could not send");
          renderStage();
          return;
        }
        replaceTemp(optimistic.id, resp.message);
        if (resp.reply) appendIfNew(resp.reply);
      });
    });
  }

  function sendImage(file) {
    if (!file || !state.activeId) return;
    var fd = new FormData();
    fd.append("image", file);
    sendQueue = sendQueue.then(function () {
      return post(urlFor(URLS.send, state.activeId), fd, true)
        .then(function (resp) {
          if (resp.ok && resp.message) appendIfNew(resp.message);
          if (resp.ok && resp.reply) appendIfNew(resp.reply);
        });
    });
  }

  function replaceTemp(tempId, message) {
    for (var i = 0; i < state.messages.length; i++) {
      if (state.messages[i].id === tempId) {
        state.messages[i] = message;
        renderStage();
        if (message.id > state.lastMsgId) state.lastMsgId = message.id;
        return;
      }
    }
    appendIfNew(message);
  }

  function appendIfNew(message) {
    for (var i = 0; i < state.messages.length; i++) {
      if (state.messages[i].id === message.id) return;
    }
    var stick = nearBottom();
    state.messages.push(message);
    renderStage();
    if (message.id > state.lastMsgId) state.lastMsgId = message.id;
    if (message.mine || stick) scrollBottom(true);
    if (!message.mine) markRead();
  }

  // ------------------------------------------------------------------
  // Polling
  // ------------------------------------------------------------------

  function pollNow() {
    var params = "";
    // after_id=0 means a full thread load (just opened) — those are old
    // messages, so only incremental fetches may fire notifications.
    var incremental = state.lastMsgId > 0;
    if (state.activeId) {
      params = "?conversation_id=" + state.activeId + "&after_id=" + state.lastMsgId;
    }
    return fetch(URLS.poll + params, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (!resp.ok) {
          // Open group was deleted by another member — drop it and go back.
          if (
            state.activeId &&
            resp.error &&
            /not found/i.test(String(resp.error))
          ) {
            var gone = state.activeId;
            state.conversations = state.conversations.filter(function (c) {
              return c.id !== gone;
            });
            hideGroupSettingsUi();
            if (navStack[navStack.length - 1] === "groupSettings") navStack.pop();
            if (navStack[navStack.length - 1] === "chat") requestNavClose("chat");
            else closeChatUi();
            showToast("This chat is no longer available.");
          }
          return;
        }
        state.pollDelay = 2500;
        var wasUnread = {};
        state.conversations.forEach(function (c) { wasUnread[c.id] = c.unread; });
        state.conversations = resp.conversations || [];
        if (resp.active) {
          state.peer = resp.active.peer;
          var added = false;
          (resp.active.messages || []).forEach(function (m) {
            var exists = state.messages.some(function (x) {
              return x.id === m.id;
            });
            if (!exists) {
              state.messages.push(m);
              added = true;
              if (incremental && !m.mine && m.kind !== "system") {
                notifyThreadMessage(m);
              }
            }
            if (typeof m.id === "number" && m.id > state.lastMsgId) {
              state.lastMsgId = m.id;
            }
          });
          // Refresh tick states on my recent messages.
          (resp.active.messages || []).forEach(function (m) {
            for (var i = 0; i < state.messages.length; i++) {
              if (state.messages[i].id === m.id) {
                state.messages[i].status = m.status;
              }
            }
          });
          // Peer read-state can advance even without new messages.
          updatePeerReadStates();
          if (added) {
            var stick = nearBottom();
            renderStage();
            if (stick) scrollBottom(true);
            markRead();
          }
          updateHeadMeta();
        }
        // Chats other than the open one only expose an unread count —
        // a bump means at least one new incoming message landed.
        state.conversations.forEach(function (c) {
          if (c.id !== state.activeId && c.unread > (wasUnread[c.id] || 0)) {
            notifyConversation(c);
          }
        });
        updateTitleBadge();
        renderChatList();
        if (state.activeId) {
          var active = findConvo(state.activeId);
          if (active && els.headName && els.headName.textContent !== active.title) {
            setHead(active);
          }
        }
      })
      .catch(function () {
        state.pollDelay = Math.min(state.pollDelay * 2, 15000);
      });
  }

  function updatePeerReadStates() {
    if (!state.peer || typeof state.peer.read_up_to !== "number") return;
    var changed = false;
    state.messages.forEach(function (m) {
      if (m.mine && !m.pending && m.status !== "read" &&
          typeof m.id === "number" && m.id <= state.peer.read_up_to) {
        m.status = "read";
        changed = true;
      }
    });
    if (changed) renderStage();
  }

  function schedule() {
    clearTimeout(state.timer);
    var delay = document.hidden ? 8000 : state.pollDelay;
    state.timer = setTimeout(function () {
      pollNow().finally(schedule);
    }, delay);
  }

  // ------------------------------------------------------------------
  // Notifications — a light pop for chat, a triple chime for stock
  // events (transfer, stock-in, sale, pending confirmations), plus an
  // unread count in the tab title and an OS notification while hidden.
  // ------------------------------------------------------------------

  // Card meta keys that flag a business event rather than conversation.
  var ALERT_CARDS = {
    transfer: 1, stock_in: 1, sale: 1, pending_action: 1, expense: 1
  };
  var audioCtx = null;
  var baseTitle = document.title;

  function ensureAudio() {
    if (audioCtx) {
      if (audioCtx.state === "suspended") {
        try { audioCtx.resume(); } catch (e) {}
      }
      return;
    }
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    try { audioCtx = new AC(); } catch (e) {}
  }

  function tone(freq, offset, dur, type, vol) {
    var t = audioCtx.currentTime + offset;
    var osc = audioCtx.createOscillator();
    var gain = audioCtx.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(vol, t + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(t);
    osc.stop(t + dur + 0.05);
  }

  // Chat ping: two quick high blips. Stock alert: a lower triple chime
  // so a transfer or stock-in is unmistakable across the counter.
  function playSound(cls) {
    if (!audioCtx) return;
    if (cls === "stock") {
      tone(659.25, 0, 0.16, "triangle", 0.22);
      tone(523.25, 0.14, 0.16, "triangle", 0.22);
      tone(783.99, 0.28, 0.26, "triangle", 0.24);
    } else {
      tone(1174.66, 0, 0.09, "sine", 0.16);
      tone(1567.98, 0.08, 0.12, "sine", 0.14);
    }
  }

  function classifyMeta(meta) {
    meta = meta || {};
    for (var key in ALERT_CARDS) {
      if (meta[key]) return "stock";
    }
    return "chat";
  }

  function classifyCard(card) {
    return ALERT_CARDS[card] ? "stock" : "chat";
  }

  function updateTitleBadge() {
    var total = 0;
    state.conversations.forEach(function (c) { total += c.unread || 0; });
    document.title = total ? "(" + total + ") " + baseTitle : baseTitle;
  }

  function notify(opts) {
    // Sounds are throttled so a burst of messages doesn't machine-gun.
    var now = Date.now();
    if (!state.muted && now - state.lastSoundAt > 350) {
      ensureAudio();
      if (audioCtx) {
        state.lastSoundAt = now;
        playSound(opts.cls);
      }
    }
    // A toast for messages landing in a chat you're not looking at.
    if (opts.toast) showToast(opts.title + " — " + opts.body);
    // An OS notification while the tab is in the background.
    if (document.hidden && "Notification" in window &&
        Notification.permission === "granted") {
      try {
        var n = new Notification(opts.title, {
          body: opts.body,
          tag: "mychat-" + (opts.convoId || "app"),
          icon: "/icon-192.png?rounded=1&v=2",
          badge: "/icon-192.png?rounded=1&v=2"
        });
        n.onclick = function () {
          window.focus();
          if (opts.convoId) openConversation(opts.convoId);
          n.close();
        };
      } catch (e) {}
    }
  }

  // A message that arrived inside the open thread.
  function notifyThreadMessage(m) {
    var convo = findConvo(state.activeId);
    var title = convo ? convo.title : "New message";
    if (convo && convo.kind === "group" && m.sender_name) {
      title = m.sender_name + " · " + convo.title;
    }
    notify({
      cls: classifyMeta(m.meta),
      title: title,
      body: m.body || (m.kind === "image" ? "Photo" : "New message"),
      convoId: state.activeId
    });
  }

  // An unread-count bump on a conversation that isn't open.
  function notifyConversation(c) {
    var last = c.last_message || {};
    var body = last.preview || "New message";
    if (c.unread > 1) body = "(" + c.unread + ") " + body;
    notify({
      cls: classifyCard(last.card),
      title: c.title,
      body: body,
      convoId: c.id,
      toast: !document.hidden
    });
  }

  // ------------------------------------------------------------------
  // Contacts panel
  // ------------------------------------------------------------------

  function renderContacts() {
    var html = "";
    var q = state.contactFilter.toLowerCase();
    if (!state.groupMode) {
      html +=
        '<button type="button" class="wa-chat" data-newgroup>' +
        '<span class="comms-avatar" data-tone="2"><i data-lucide="users"></i></span>' +
        '<span class="wa-chat__main"><span class="wa-chat__top"><strong>' +
        "New group</strong></span>" +
        '<span class="wa-chat__preview">Chat with several shops at once' +
        "</span></span></button>";
    }
    state.contacts
      .filter(function (s) {
        return !q || s.name.toLowerCase().indexOf(q) !== -1 ||
          s.location.toLowerCase().indexOf(q) !== -1;
      })
      .forEach(function (s) {
        var check = "";
        if (state.groupMode) {
          check =
            '<span class="wa-chat__check' +
            (state.groupSel[s.id] ? " is-on" : "") + '">' +
            '<i data-lucide="' +
            (state.groupSel[s.id] ? "check-circle-2" : "circle") +
            '"></i></span>';
        }
        html += '<button type="button" class="wa-chat" data-contact="' + s.id + '">' +
          '<span class="comms-avatar" data-tone="' + s.tone + '">' +
          escapeHtml(s.initials) + "</span>" +
          '<span class="wa-chat__main"><span class="wa-chat__top"><strong>' +
          escapeHtml(s.name) + "</strong></span>" +
          '<span class="wa-chat__preview">' + escapeHtml(s.location) +
          "</span></span>" + check + "</button>";
      });
    els.contactList.innerHTML = html ||
      '<p class="wa-app__none">No other shops are registered.</p>';
    refreshIcons();
  }

  function groupCount() {
    var n = 0;
    for (var k in state.groupSel) if (state.groupSel[k]) n++;
    return n;
  }

  function syncGroupUi() {
    if (els.groupFoot) els.groupFoot.hidden = !state.groupMode;
    if (els.contactsTitle) {
      els.contactsTitle.textContent = state.groupMode ? "New group" : "New chat";
    }
    if (els.groupCreateLabel) {
      var n = groupCount();
      els.groupCreateLabel.textContent = n
        ? "Create group (" + n + ")"
        : "Create group";
    }
  }

  function enterGroupMode() {
    state.groupMode = true;
    state.groupSel = {};
    syncGroupUi();
    renderContacts();
    if (els.groupName) els.groupName.focus();
  }

  function leaveGroupMode() {
    if (!state.groupMode) return;
    state.groupMode = false;
    state.groupSel = {};
    if (els.groupName) els.groupName.value = "";
    syncGroupUi();
  }

  function showContacts() {
    leaveGroupMode();
    renderContacts();
    els.contacts.hidden = false;
    els.contactSearch.value = "";
    state.contactFilter = "";
    pushNav("contacts");
    els.contactSearch.focus();
  }

  function hideContactsUi() {
    els.contacts.hidden = true;
    leaveGroupMode();
  }

  function hideContacts() {
    if (els.contacts.hidden) return;
    requestNavClose("contacts");
  }

  function createGroup() {
    var name = (els.groupName.value || "").trim();
    if (!name) {
      els.groupName.focus();
      showToast("Give the group a name first.");
      return;
    }
    var ids = [];
    for (var k in state.groupSel) {
      if (state.groupSel[k]) ids.push(parseInt(k, 10));
    }
    if (!ids.length) {
      showToast("Pick at least one shop to add.");
      return;
    }
    els.groupCreate.disabled = true;
    post(URLS.group, { name: name, members: ids })
      .then(function (resp) {
        if (!resp.ok) {
          showToast(resp.error || "Could not create the group.");
          return;
        }
        openFromResponse(resp);
      })
      .finally(function () {
        els.groupCreate.disabled = false;
      });
  }

  function showGroupSettings() {
    var convo = findConvo(state.activeId);
    if (!convo || convo.kind !== "group" || !els.groupPanel) return;
    if (els.groupRename) els.groupRename.value = convo.title || "";
    if (els.groupActivityHint) {
      els.groupActivityHint.hidden = !convo.activity;
    }
    els.groupPanel.hidden = false;
    pushNav("groupSettings");
    if (els.groupRename) els.groupRename.focus();
    refreshIcons();
  }

  function hideGroupSettingsUi() {
    if (els.groupPanel) els.groupPanel.hidden = true;
  }

  function hideGroupSettings() {
    if (!els.groupPanel || els.groupPanel.hidden) return;
    requestNavClose("groupSettings");
  }

  function saveGroupName() {
    if (!state.activeId || !els.groupRename) return;
    var name = (els.groupRename.value || "").trim();
    if (!name) {
      els.groupRename.focus();
      showToast("Give the group a name first.");
      return;
    }
    if (els.groupSave) els.groupSave.disabled = true;
    post(urlFor(URLS.groupUpdate, state.activeId), { name: name })
      .then(function (resp) {
        if (!resp.ok) {
          showToast(resp.error || "Could not rename the group.");
          return;
        }
        var updated = resp.conversation;
        var existing = findConvo(updated.id);
        if (existing) {
          existing.title = updated.title;
          existing.subtitle = updated.subtitle;
          existing.initials = updated.initials;
          existing.can_manage = updated.can_manage;
          existing.activity = updated.activity;
        } else {
          state.conversations.push(updated);
        }
        if (resp.active && resp.active.messages) {
          resp.active.messages.forEach(function (m) {
            var exists = state.messages.some(function (x) { return x.id === m.id; });
            if (!exists) {
              state.messages.push(m);
              if (typeof m.id === "number" && m.id > state.lastMsgId) {
                state.lastMsgId = m.id;
              }
            }
          });
          renderStage();
          scrollBottom(true);
        }
        setHead(findConvo(updated.id) || updated);
        renderChatList();
        showToast("Group renamed.");
        hideGroupSettings();
      })
      .finally(function () {
        if (els.groupSave) els.groupSave.disabled = false;
      });
  }

  function deleteGroup() {
    if (!state.activeId) return;
    var convo = findConvo(state.activeId);
    var label = (convo && convo.title) || "this group";
    if (!window.confirm('Delete "' + label + '" for every shop? This cannot be undone.')) {
      return;
    }
    var id = state.activeId;
    if (els.groupDelete) els.groupDelete.disabled = true;
    post(urlFor(URLS.groupDelete, id), {})
      .then(function (resp) {
        if (!resp.ok) {
          showToast(resp.error || "Could not delete the group.");
          return;
        }
        state.conversations = state.conversations.filter(function (c) {
          return c.id !== id;
        });
        hideGroupSettingsUi();
        // Drop the settings nav entry, then leave the chat.
        if (navStack[navStack.length - 1] === "groupSettings") {
          navStack.pop();
        }
        if (state.activeId === id) {
          if (navStack[navStack.length - 1] === "chat") {
            requestNavClose("chat");
          } else {
            closeChatUi();
          }
        } else {
          renderChatList();
        }
        showToast("Group deleted.");
      })
      .finally(function () {
        if (els.groupDelete) els.groupDelete.disabled = false;
      });
  }

  // ------------------------------------------------------------------
  // Emoji + suggestions + history
  // ------------------------------------------------------------------

  var EMOJIS = ("😀 😁 😂 🤣 😊 😍 😘 🙂 😉 🤔 👍 👎 🙏 👏 💪 🎉 ✨ ❤️ 🔥 " +
    "😢 😮 😅 🤝 ✅ ❌ ⚠️ 📦 💰 🧾 🏪 📊 📈 ⏰ 🚚 🤖").split(" ");

  function buildEmojiPop() {
    els.emojiPop.innerHTML = EMOJIS.map(function (e) {
      return '<button type="button" data-emoji="' + e + '">' + e + "</button>";
    }).join("");
  }

  function buildSuggestions() {
    els.suggestionsBar.innerHTML = suggestions.map(function (s) {
      return '<button type="button" class="wa-chip" data-suggest="' +
        escapeHtml(s) + '">' + escapeHtml(s) + "</button>";
    }).join("");
  }

  function loadOlder() {
    if (!state.activeId || !state.messages.length) return;
    var first = state.messages[0];
    var oldest = typeof first.id === "number" ? first.id : 0;
    if (!oldest) return;
    fetch(urlFor(URLS.history, state.activeId) + "?before_id=" + oldest,
      { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (!resp.ok) return;
        var prevHeight = els.stage.scrollHeight;
        state.messages = resp.messages.concat(state.messages);
        state.hasMore = resp.has_more;
        renderStage();
        els.stage.scrollTop = els.stage.scrollHeight - prevHeight;
      });
  }

  function autosize() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 120) + "px";
  }

  // ------------------------------------------------------------------
  // Stock transfer / stock-in panel
  // ------------------------------------------------------------------

  function showToast(text) {
    clearTimeout(state.toastTimer);
    els.toast.textContent = text;
    els.toast.hidden = false;
    state.toastTimer = setTimeout(function () {
      els.toast.hidden = true;
    }, 3200);
  }

  function stockError(text) {
    if (!text) {
      els.stockError.hidden = true;
      els.stockError.textContent = "";
      return;
    }
    els.stockError.textContent = text;
    els.stockError.hidden = false;
  }

  function stockMode() { return state.stock.mode; }

  function pricedMode() {
    var m = stockMode();
    return m === "sale" || m === "stockin" || m === "trade" || m === "quickin";
  }

  function expenseMode() {
    return stockMode() === "expense";
  }

  function expenseCategoryLabel(value) {
    if (!els.expenseCategory) return value || "";
    var opts = els.expenseCategory.options;
    for (var i = 0; i < opts.length; i++) {
      if (opts[i].value === value) return opts[i].textContent;
    }
    return value || "";
  }

  function tradeKind() {
    var checked = els.stockTradeKinds
      ? els.stockTradeKinds.querySelector("[data-trade-kind]:checked")
      : null;
    return checked ? checked.value : (me.trade_out ? "trade_out" : "trade_in");
  }

  function syncTradeKind() {
    var isIn = tradeKind() === "trade_in";
    if (els.stockTradeHint) {
      els.stockTradeHint.textContent = isIn
        ? "The client brings the items — enter what you owe per unit."
        : "The client takes the items now — they owe you the total.";
    }
    els.stockPrice.placeholder = isIn
      ? "Amount owed per unit (required)"
      : "Sell price (optional — defaults to shop price)";
    els.stockSubmitLabel.textContent =
      isIn ? "Complete trade in" : "Complete trade out";
    els.stockEmpty.textContent = isIn
      ? "Add the items the client is bringing."
      : "Add the items the client is taking.";
  }

  // The footer's payment/client/supplier block collapses so the items
  // list keeps room on small screens — the toggle reopens it for entry.
  function setStockDetails(open) {
    if (!els.stockDetails || !els.stockDetailsToggle) return;
    els.stockDetails.hidden = !open;
    els.stockDetailsToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (els.stockCheckout) els.stockCheckout.classList.toggle("is-open", !!open);
    if (open) syncDetailsToggleLabel();
  }

  function syncDetailsToggleLabel() {
    if (!els.stockDetailsToggleLabel || !state.stock) return;
    var mode = state.stock.mode;
    if (mode === "stockin" || mode === "expense") {
      var sname = (els.stockSupname.value || "").trim();
      var sphone = (els.stockPhone.value || "").trim();
      if (sname && sphone) {
        els.stockDetailsToggleLabel.textContent = "Supplier · " + sname;
      } else if (sname || sphone) {
        els.stockDetailsToggleLabel.textContent =
          "Supplier · " + (sname || sphone);
      } else {
        els.stockDetailsToggleLabel.textContent = "Supplier (required)";
      }
      return;
    }
    if (mode === "sale") {
      els.stockDetailsToggleLabel.textContent = "Payment & client";
      return;
    }
    if (mode === "trade") {
      els.stockDetailsToggleLabel.textContent = "Client details";
    }
  }

  function selectTradeKind(kind) {
    if (!els.stockTradeKinds || !kind) return;
    var radio = els.stockTradeKinds.querySelector(
      '[data-trade-kind][value="' + kind + '"]'
    );
    if (radio) radio.checked = true;
  }

  function openStock(mode) {
    var convo = findConvo(state.activeId);
    var preferredTradeKind = null;
    if (mode === "trade_in" || mode === "trade_out") {
      preferredTradeKind = mode;
      mode = "trade";
    }
    if (mode === "trade" && !me.trade_out && !me.trade_in) {
      showToast("Trade is disabled for this shop.");
      return;
    }
    if (preferredTradeKind === "trade_out" && !me.trade_out) {
      showToast("Trade out is disabled for this shop.");
      return;
    }
    if (preferredTradeKind === "trade_in" && !me.trade_in) {
      showToast("Trade in is disabled for this shop.");
      return;
    }
    state.stock = { mode: mode, lines: [], picked: null, serials: [], submitting: false };
    els.stockSerialWrap.hidden = true;
    els.stockSerial.value = "";
    els.stockSerials.innerHTML = "";
    els.stockSerialCount.textContent = "0";
    hideSuggest(els.stockSerialSuggest);
    els.stockQty.hidden = false;
    var titles = { transfer: "Transfer stock", stockin: "Stock in", sale: "New sale", trade: "Trade", expense: "Register expense", quickin: "Stock in" };
    var labels = { transfer: "Send transfer", stockin: "Save stock in", sale: "Complete sale", trade: "Complete trade", expense: "Save expense", quickin: "Send to chat" };
    els.stockTitle.textContent = titles[mode] || "Stock";
    els.stockSubmitLabel.textContent = labels[mode] || "Send";
    els.stockDestWrap.hidden = mode !== "transfer";
    els.stockSupplier.hidden = mode !== "stockin" && mode !== "expense";
    els.stockPayment.hidden = mode !== "sale";
    els.stockClient.hidden = mode !== "sale" && mode !== "trade";
    if (els.stockDetailsToggle) {
      var hasDetails = mode === "sale" || mode === "trade" ||
        mode === "stockin" || mode === "expense";
      els.stockDetailsToggle.hidden = !hasDetails;
      setStockDetails(false);
      syncDetailsToggleLabel();
    }
    if (els.stockTradeKinds) els.stockTradeKinds.hidden = mode !== "trade";
    els.stockExpense.hidden = mode !== "expense";
    // Expense reuses the parked-lines list; hide the item-search add block.
    if (els.stockItemWrap) els.stockItemWrap.hidden = false;
    if (els.stockItemAdd) els.stockItemAdd.hidden = mode === "expense";
    if (els.stockListLabel) {
      els.stockListLabel.textContent = mode === "expense" ? "Expenses" : "Items";
    }
    els.stockPrice.hidden = !pricedMode();
    els.stockPrice.placeholder = mode === "sale" || mode === "trade"
      ? "Sell price (optional — defaults to shop price)"
      : mode === "quickin"
      ? "Buying price"
      : "Buying price (optional — for average cost)";
    if (mode === "sale") {
      els.stockKind.value = "sale";
      els.stockPayMethod.value = "cash";
      els.stockPayMethod.hidden = false;
      els.stockPaySplit.hidden = true;
      els.stockDueDate.hidden = true;
      els.stockCashAmt.value = "";
      els.stockMpesaAmt.value = "";
      els.stockClientName.value = "";
      els.stockClientPhone.value = "";
      els.stockWhatsappWrap.hidden = true;
      els.stockWhatsapp.checked = true;
      els.stockClientName.placeholder = "Client name (optional)";
      els.stockClientPhone.placeholder = "Client phone (optional)";
      els.stockSubmitLabel.textContent = "Complete sale";
    }
    if (mode === "trade") {
      els.stockClientName.value = "";
      els.stockClientPhone.value = "";
      els.stockClientName.placeholder = "Client full name (required)";
      els.stockClientPhone.placeholder = "Client phone (required)";
      els.stockWhatsappWrap.hidden = true;
      if (preferredTradeKind) selectTradeKind(preferredTradeKind);
      syncTradeKind();
    }
    if (mode === "stockin" || mode === "expense") {
      els.stockPhone.value = "";
      els.stockSupname.value = "";
      hideSuggest(els.stockPhoneSuggest);
      hideSuggest(els.stockNameSuggest);
      syncDetailsToggleLabel();
    }
    if (mode === "expense") {
      els.expenseCategory.value = "";
      els.expenseName.value = "";
      els.expenseAmount.value = "";
      els.expenseStatus.value = "paid";
      // No items list competing for space — open supplier up front so
      // the required phone/name fields aren't buried under the toggle.
      setStockDetails(true);
    }
    els.stockStaffCode.value = "";
    els.stockCodeBox.hidden = false;
    // Member confirmation only gates stock-in and transfers — sales,
    // credits and quotations complete straight away. For staff sessions
    // the sale is recorded under the signed-in account, so the code is
    // optional (typing one re-attributes it to that employee).
    var memberConfirm = me.needs_member_confirm && mode !== "sale";
    var ownCode = me.staff_view && mode === "sale";
    // Staff acting on a shop they aren't allocated to send the request as
    // "pending confirmation" — a shop member completes it with their code,
    // on the card or right here.
    els.stockSubmit.hidden = mode === "quickin" ? false : !memberConfirm;
    if (memberConfirm) {
      els.stockSubmitLabel.textContent = "Send for confirmation";
    }
    els.stockStaffCode.placeholder = memberConfirm
      ? "Member's staff code (optional)"
      : ownCode
      ? "Staff code (optional — yours applies)"
      : mode === "quickin"
      ? "Staff code (optional — blank posts for confirmation)"
      : "Enter staff code to complete";
    els.stockCodeHint.textContent = memberConfirm
      ? "Sends as pending — a member of this shop confirms it with their " +
        "code, or enter their code here to complete it now"
      : ownCode
      ? "Completes under your staff account — enter a staff code to " +
        "record it under that person instead"
      : mode === "quickin"
      ? "Posts to this chat — a member confirms with their code, " +
        "or enter a code to stock in now"
      : (mode === "transfer" ? "Transfer sends"
         : mode === "stockin" ? "Stock in saves"
         : mode === "trade" ? "Trade completes"
         : mode === "expense" ? "Expense saves"
         : "Sale completes") +
        " when your code is verified";
    els.stockEmpty.textContent =
      mode === "transfer"
        ? "Add the items you are sending."
        : mode === "sale"
        ? "Add the items being sold."
        : mode === "trade"
        ? (tradeKind() === "trade_in"
          ? "Add the items the client is bringing."
          : "Add the items the client is taking.")
        : mode === "expense"
        ? "Add expenses with the form above, then enter supplier details."
        : "Add the items you received.";
    if (mode === "transfer") {
      els.stockDest.innerHTML = state.contacts
        .map(function (s) {
          return '<option value="' + s.id + '">' + escapeHtml(s.name) +
            " (" + escapeHtml(s.location) + ")</option>";
        })
        .join("");
      if (convo && convo.kind === "direct" && convo.peer_shop_id) {
        els.stockDest.value = String(convo.peer_shop_id);
      }
    }
    els.stockItemInput.value = "";
    els.stockQty.value = "";
    els.stockPrice.value = "";
    els.stockPhone.value = "";
    els.stockSupname.value = "";
    stockError("");
    renderStockLines();
    hideSuggest(els.stockItemSuggest);
    hideSuggest(els.stockPhoneSuggest);
    hideSuggest(els.stockNameSuggest);
    els.stockPanel.hidden = false;
    els.menu.hidden = true;
    pushNav("stock");
    setTimeout(function () {
      if (mode === "expense") els.expenseName.focus();
      else els.stockItemInput.focus();
    }, 60);
  }

  function closeStockUi() {
    els.stockPanel.hidden = true;
    state.stock.mode = null;
  }

  function closeStock() {
    if (els.stockPanel.hidden && els.scanModal.hidden) return;
    requestNavClose("stock");
  }

  function lineTotalText(l) {
    var q = l.serials ? l.serials.length : parseFloat(l.quantity) || 0;
    var p = parseFloat(l.buying_price) || 0;
    if (!q || !p) return "";
    return "KSh " + (q * p).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function renderStockLines() {
    var priced = pricedMode();
    var isExpense = expenseMode();
    var rows = state.stock.lines.map(function (line, i) {
      if (isExpense) {
        var amt = parseFloat(line.amount) || 0;
        return '<div class="wa-stock-line" data-line="' + i + '">' +
          '<div class="wa-stock-line__top">' +
          '<span class="wa-stock-line__name">' + escapeHtml(line.name) +
          '<small class="wa-stock-line__have"> · ' +
          escapeHtml(line.category_label || expenseCategoryLabel(line.category)) +
          "</small></span>" +
          '<button type="button" class="wa-stock-line__remove" ' +
          'data-line-remove="' + i +
          '" aria-label="Remove"><i data-lucide="x"></i></button></div>' +
          '<div class="wa-stock-line__ctrls">' +
          '<span class="wa-stock-line__op">KSh</span>' +
          '<input type="number" class="wa-stock-line__price" min="0" step="any" ' +
          'title="Amount" placeholder="0.00" value="' +
          escapeHtml(line.amount || "") + '" data-line-amount="' + i + '">' +
          '<span class="wa-stock-line__ltotal" data-line-ltotal="' + i + '">' +
          (amt > 0
            ? "KSh " + amt.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
              })
            : "") +
          "</span></div></div>";
      }
      var qtyCell = line.serials
        ? '<span class="wa-stock-line__have">' + line.serials.length +
          " SN</span>"
        : '<span class="wa-stock-line__stepper">' +
          '<button type="button" data-line-step="' + i + '" data-step="-1" ' +
          'aria-label="Decrease quantity"><i data-lucide="minus"></i></button>' +
          '<input type="number" min="0" step="any" value="' +
          escapeHtml(line.quantity) + '" data-line-qty="' + i + '" ' +
          'aria-label="Quantity">' +
          '<button type="button" data-line-step="' + i + '" data-step="1" ' +
          'aria-label="Increase quantity"><i data-lucide="plus"></i></button>' +
          "</span>";
      var priceBits = "";
      if (priced) {
        priceBits =
          '<span class="wa-stock-line__op">×</span>' +
          '<input type="number" class="wa-stock-line__price" min="0" step="any" ' +
          'title="' + (stockMode() === "sale"
            ? "Sell"
            : stockMode() === "trade"
            ? (tradeKind() === "trade_in" ? "Owed" : "Sell")
            : "Buying") + ' price" ' +
          'placeholder="price" value="' + escapeHtml(line.buying_price || "") +
          '" data-line-price="' + i + '">' +
          '<span class="wa-stock-line__op">=</span>' +
          '<span class="wa-stock-line__ltotal" data-line-ltotal="' + i + '">' +
          escapeHtml(lineTotalText(line)) + "</span>";
      }
      return '<div class="wa-stock-line" data-line="' + i + '">' +
        '<div class="wa-stock-line__top">' +
        '<span class="wa-stock-line__name">' + escapeHtml(line.name) +
        '</span><button type="button" class="wa-stock-line__remove" ' +
        'data-line-remove="' + i +
        '" aria-label="Remove"><i data-lucide="x"></i></button></div>' +
        '<div class="wa-stock-line__ctrls">' + qtyCell + priceBits +
        "</div></div>";
    }).join("");
    els.stockItems.innerHTML = rows;
    els.stockEmpty.hidden = state.stock.lines.length > 0;
    if (els.stockCount) {
      els.stockCount.hidden = !state.stock.lines.length;
      els.stockCount.textContent = String(state.stock.lines.length);
    }
    refreshStockTotal();
    refreshIcons();
  }

  function refreshStockTotal() {
    var lines = state.stock ? state.stock.lines : [];
    var isExpense = expenseMode();
    var priced = pricedMode();
    var has = (priced || isExpense) && lines.length > 0;
    els.stockTotal.hidden = !has;
    if (!has) return;
    var total = lines.reduce(function (sum, l) {
      if (isExpense) return sum + (parseFloat(l.amount) || 0);
      var q = l.serials ? l.serials.length : parseFloat(l.quantity) || 0;
      var p = parseFloat(l.buying_price) || 0;
      return sum + q * p;
    }, 0);
    els.stockTotalVal.textContent = "KSh " +
      total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function addExpenseLine() {
    var category = (els.expenseCategory.value || "").trim();
    var name = (els.expenseName.value || "").trim().toUpperCase();
    var amount = (els.expenseAmount.value || "").trim();
    if (!category) {
      stockError("Choose an expense category.");
      els.expenseCategory.focus();
      return;
    }
    if (!name) {
      stockError("Enter the expense name.");
      els.expenseName.focus();
      return;
    }
    if (!amount || isNaN(parseFloat(amount)) || parseFloat(amount) <= 0) {
      stockError("Enter an amount greater than zero.");
      els.expenseAmount.focus();
      return;
    }
    state.stock.lines.push({
      category: category,
      category_label: expenseCategoryLabel(category),
      name: name,
      amount: amount
    });
    els.expenseName.value = "";
    els.expenseAmount.value = "";
    stockError("");
    renderStockLines();
    els.expenseName.focus();
  }

  function suggestRow(html) {
    return '<button type="button" class="wa-stock__pick">' + html + "</button>";
  }

  // On phones the scrollable body/details clip dropdowns — pin them to
  // fixed coordinates so they float over the rest of the panel.
  var mqNarrow = window.matchMedia("(max-width: 639px)");
  var sugMask = null;

  function ensureSugMask() {
    if (sugMask) return sugMask;
    sugMask = document.createElement("button");
    sugMask.type = "button";
    sugMask.className = "wa-stock__sugmask";
    sugMask.setAttribute("aria-label", "Dismiss suggestions");
    sugMask.hidden = true;
    sugMask.addEventListener("click", function () {
      hideAllSuggests();
    });
    root.appendChild(sugMask);
    return sugMask;
  }

  function setSugMask(on) {
    var mask = ensureSugMask();
    mask.hidden = !on;
  }

  function suggestHome(box) {
    return box._sugHome || box.parentElement;
  }

  function rememberSuggestHome(box) {
    if (!box._sugHome) box._sugHome = box.parentElement;
  }

  function restoreSuggestHome(box) {
    var home = box._sugHome;
    if (home && box.parentElement !== home) home.appendChild(box);
  }

  function clearSuggestPos(box) {
    box.classList.remove("is-float");
    box.style.left = box.style.top = box.style.bottom =
      box.style.width = box.style.maxHeight = box.style.visibility = "";
    restoreSuggestHome(box);
  }

  // When the keyboard opens we translate the app root — that makes
  // position:fixed children use the root as containing block, so coords
  // must be relative to root's box, not the layout viewport.
  function floatOriginRect() {
    var cs = window.getComputedStyle(root);
    if (cs.transform && cs.transform !== "none") {
      return root.getBoundingClientRect();
    }
    return { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
  }

  function floatSuggest(box) {
    rememberSuggestHome(box);
    var home = suggestHome(box);
    var clipEl = home.closest(".wa-stock__body") ||
      home.closest(".wa-stock__details");
    if (!mqNarrow.matches || !clipEl || box.hidden) {
      clearSuggestPos(box);
      return;
    }
    var anchorEl = home.querySelector("input") || home;
    // Prefer room below the field so the list doesn't cover the next input.
    try {
      anchorEl.scrollIntoView({ block: "center", inline: "nearest" });
    } catch (err) {
      try { anchorEl.scrollIntoView(true); } catch (e2) {}
    }
    // Park on the app root so overflow:hidden on .wa-stock can't clip us
    // when the keyboard transform makes fixed use the root as CB.
    if (box.parentElement !== root) root.appendChild(box);
    var anchor = anchorEl.getBoundingClientRect();
    var crect = clipEl.getBoundingClientRect();
    var origin = floatOriginRect();
    var vv = window.visualViewport;
    var viewTop = vv ? vv.offsetTop : 0;
    var viewBottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
    box.style.visibility =
      anchor.bottom < crect.top || anchor.top > crect.bottom ? "hidden" : "";
    box.classList.add("is-float");
    box.style.left = Math.max(8, anchor.left - origin.left) + "px";
    box.style.width = Math.min(anchor.width, window.innerWidth - 16) + "px";
    var below = viewBottom - anchor.bottom - 8;
    var above = anchor.top - viewTop - 8;
    // Keep lists compact on soft keyboards so sibling fields stay reachable.
    var cap = Math.min(220, Math.max(120, Math.floor((viewBottom - viewTop) * 0.42)));
    if (below >= 120 || below >= above) {
      box.style.top = (anchor.bottom - origin.top + 4) + "px";
      box.style.bottom = "";
      box.style.maxHeight = Math.min(cap, Math.max(96, below)) + "px";
    } else {
      box.style.top = "";
      box.style.bottom = (origin.bottom - anchor.top + 4) + "px";
      box.style.maxHeight = Math.min(cap, Math.max(96, above)) + "px";
    }
  }

  function repositionFloatSuggests() {
    var open = root.querySelectorAll(".wa-stock__suggest.is-float:not([hidden])");
    open.forEach(floatSuggest);
    setSugMask(!!open.length && mqNarrow.matches);
  }

  function suggestBarHtml() {
    return (
      '<div class="wa-stock__sugbar">' +
      '<span class="wa-stock__sughint">Suggestions</span>' +
      '<button type="button" class="wa-stock__sugx" data-sugx ' +
      'aria-label="Close suggestions">' +
      '<i data-lucide="x"></i><span>Close</span></button></div>'
    );
  }

  function hideAllSuggests(except) {
    root.querySelectorAll(".wa-stock__suggest:not([hidden])").forEach(
      function (el) {
        if (except && el === except) return;
        hideSuggest(el);
      }
    );
  }

  function showSuggest(box, html) {
    hideAllSuggests(box);
    rememberSuggestHome(box);
    box.innerHTML = suggestBarHtml() + html;
    box.hidden = false;
    floatSuggest(box);
    setSugMask(box.classList.contains("is-float"));
    refreshIcons();
  }

  function hideSuggest(box) {
    if (!box) return;
    box.hidden = true;
    box.innerHTML = "";
    clearSuggestPos(box);
    if (!root.querySelector(".wa-stock__suggest.is-float:not([hidden])")) {
      setSugMask(false);
    }
  }

  var itemSearchTimer = null;
  var supplierSearchTimer = { phone: null, name: null };

  function searchItems(q, box, onPick) {
    clearTimeout(itemSearchTimer);
    if (!q || q.trim().length < 1) {
      hideSuggest(box);
      return;
    }
    itemSearchTimer = setTimeout(function () {
      fetch(URLS.itemSearch + "?q=" + encodeURIComponent(q.trim()),
        { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          if (!resp.ok) return;
          var html = (resp.items || []).map(function (it) {
            return (
              '<button type="button" data-item="' + it.id + '">' +
              '<span class="s-name">' + escapeHtml(it.name) + "</span>" +
              '<span class="s-meta s-have">' + escapeHtml(it.stock) +
              " in stock</span>" +
              '<span class="s-meta">' + escapeHtml(it.category) +
              " · KSh " + escapeHtml(it.price) + "</span>" +
              "</button>"
            );
          }).join("");
          if (!html) {
            html = '<div class="s-empty">No items match — check the name.</div>';
          }
          showSuggest(box, html);
          box.querySelectorAll("[data-item]").forEach(function (btn) {
            var it = resp.items.filter(function (x) {
              return String(x.id) === btn.dataset.item;
            })[0];
            btn.addEventListener("click", function () { onPick(it); });
          });
        })
        .catch(function () {});
    }, 220);
  }

  function searchSuppliers(q, box, onPick) {
    clearTimeout(supplierSearchTimer[box === els.stockPhoneSuggest ? "phone" : "name"]);
    var key = box === els.stockPhoneSuggest ? "phone" : "name";
    // Live from the first character — phone digits and short names both match.
    if (!q || !q.trim().length) {
      hideSuggest(box);
      return;
    }
    var kind = state.stock && state.stock.mode === "expense" ? "expense" : "stock";
    supplierSearchTimer[key] = setTimeout(function () {
      fetch(
        URLS.supplierSearch +
          "?q=" + encodeURIComponent(q.trim()) +
          "&kind=" + encodeURIComponent(kind),
        { credentials: "same-origin" }
      )
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          if (!resp.ok) return;
          // Ignore stale responses if the field changed while fetching.
          var current = key === "phone"
            ? els.stockPhone.value
            : els.stockSupname.value;
          if ((current || "").trim() !== q.trim()) return;
          var html = (resp.suppliers || []).map(function (s) {
            return (
              '<button type="button" data-sup="' + s.id + '">' +
              '<span class="s-name">' + escapeHtml(s.name) + "</span>" +
              '<span class="s-meta">' + escapeHtml(s.dial) + " " +
              escapeHtml(s.phone) + "</span></button>"
            );
          }).join("");
          if (!html) {
            html = '<div class="s-empty">New supplier — fill both fields.</div>';
          }
          showSuggest(box, html);
          box.querySelectorAll("[data-sup]").forEach(function (btn) {
            var s = resp.suppliers.filter(function (x) {
              return String(x.id) === btn.dataset.sup;
            })[0];
            btn.addEventListener("click", function () { onPick(s); });
          });
        })
        .catch(function () {});
    }, 160);
  }

  var clientSearchTimer = null;
  function searchClients(q, box, onPick) {
    clearTimeout(clientSearchTimer);
    if (!q || q.trim().length < 2) {
      hideSuggest(box);
      return;
    }
    clientSearchTimer = setTimeout(function () {
      fetch(URLS.clientSearch + "?q=" + encodeURIComponent(q.trim()),
        { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          if (!resp.ok) return;
          var html = (resp.clients || []).map(function (c) {
            return (
              '<button type="button" data-cli="' + c.id + '">' +
              '<span class="s-name">' + escapeHtml(c.name) + "</span>" +
              '<span class="s-meta">' + escapeHtml(c.phone || "") +
              "</span></button>"
            );
          }).join("");
          if (!html) {
            html = '<div class="s-empty">New client — will be saved.</div>';
          }
          showSuggest(box, html);
          box.querySelectorAll("[data-cli]").forEach(function (btn) {
            var c = resp.clients.filter(function (x) {
              return String(x.id) === btn.dataset.cli;
            })[0];
            btn.addEventListener("click", function () { onPick(c); });
          });
        })
        .catch(function () {});
    }, 220);
  }

  function renderSerialChips() {
    els.stockSerials.innerHTML = state.stock.serials.map(function (s, i) {
      return '<span class="wa-serial-chip">' + escapeHtml(s) +
        '<button type="button" data-serial-remove="' + i +
        '" aria-label="Remove">×</button></span>';
    }).join("");
    els.stockSerialCount.textContent = String(state.stock.serials.length);
  }

  function addSerial(serial) {
    serial = (serial || "").trim().toUpperCase();
    if (!serial) return false;
    if (state.stock.serials.indexOf(serial) !== -1) return "dup";
    state.stock.serials.push(serial);
    return true;
  }

  function pushSerial() {
    var raw = (els.stockSerial.value || "").toUpperCase();
    var dup = 0;
    raw.split(/[,\s;]+/).forEach(function (piece) {
      if (addSerial(piece) === "dup") dup++;
    });
    els.stockSerial.value = "";
    if (dup) {
      stockError(dup + " duplicate serial" + (dup > 1 ? "s" : "") + " skipped.");
    } else {
      stockError("");
    }
    renderSerialChips();
  }

  var serialSearchTimer = null;

  function searchSerials(q) {
    clearTimeout(serialSearchTimer);
    var box = els.stockSerialSuggest;
    // Suggestions only make sense for transfers — serials must already be
    // in stock at the sender. Stock-in serials are brand new.
    var picked = state.stock.picked;
    if (stockMode() !== "transfer" || !picked || !picked.track_serial) {
      hideSuggest(box);
      return;
    }
    serialSearchTimer = setTimeout(function () {
      var url = URLS.serialSearch + "?item_id=" + picked.id +
        "&q=" + encodeURIComponent((q || "").trim());
      fetch(url, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          if (!resp.ok) return;
          var rows = (resp.serials || []).filter(function (s) {
            return state.stock.serials.indexOf(s.serial) === -1;
          });
          var html = rows.map(function (s) {
            return '<button type="button" data-ser="' +
              escapeHtml(s.serial) + '"><span class="s-name">' +
              escapeHtml(s.serial) + "</span>" +
              '<span class="s-meta s-have">in stock</span></button>';
          }).join("");
          if (!html) {
            html = '<div class="s-empty">' +
              ((q || "").trim()
                ? "No matching serial in stock."
                : "Type to filter serials in stock.") + "</div>";
          }
          showSuggest(box, html);
          box.querySelectorAll("[data-ser]").forEach(function (btn) {
            btn.addEventListener("click", function () {
              addSerial(btn.dataset.ser);
              renderSerialChips();
              hideSuggest(box);
              els.stockSerial.value = "";
              els.stockSerial.focus();
            });
          });
        })
        .catch(function () {});
    }, 200);
  }

  function addStockLine() {
    var picked = state.stock.picked;
    if (!picked) {
      stockError("Pick an item from the suggestions first.");
      return;
    }
    var price = (els.stockPrice.value || "").trim();
    if (stockMode() === "trade" && tradeKind() === "trade_in" &&
        (!price || isNaN(parseFloat(price)) || parseFloat(price) <= 0)) {
      stockError('Enter the amount owed per unit for "' + picked.name + '".');
      els.stockPrice.focus();
      return;
    }
    if (stockMode() === "quickin" &&
        (!price || isNaN(parseFloat(price)) || parseFloat(price) <= 0)) {
      stockError('Enter the buying price for "' + picked.name + '".');
      els.stockPrice.focus();
      return;
    }
    var linePrice = pricedMode()
      ? (price ||
         (stockMode() === "quickin" ||
          (stockMode() === "trade" && tradeKind() === "trade_in")
          ? "" : picked.price) || "")
      : null;
    if (picked.track_serial) {
      if (!state.stock.serials.length) {
        stockError('"' + picked.name + '" is serial-tracked — scan or type its serial numbers.');
        els.stockSerial.focus();
        return;
      }
      state.stock.lines.push({
        item_id: picked.id,
        name: picked.name,
        stock: picked.stock,
        serials: state.stock.serials.slice(),
        quantity: String(state.stock.serials.length),
        buying_price: linePrice
      });
      state.stock.serials = [];
      renderSerialChips();
    } else {
      var qty = (els.stockQty.value || "").trim();
      if (!qty || isNaN(parseFloat(qty)) || parseFloat(qty) <= 0) {
        stockError("Enter a quantity greater than zero.");
        els.stockQty.focus();
        return;
      }
      state.stock.lines.push({
        item_id: picked.id,
        name: picked.name,
        stock: picked.stock,
        quantity: qty,
        buying_price: linePrice
      });
    }
    state.stock.picked = null;
    els.stockItemInput.value = "";
    els.stockQty.value = "";
    els.stockPrice.value = "";
    els.stockSerialWrap.hidden = true;
    els.stockQty.hidden = false;
    hideSuggest(els.stockSerialSuggest);
    stockError("");
    renderStockLines();
    els.stockItemInput.focus();
  }

  function submitStock() {
    if (state.stock.submitting) return;
    var mode = stockMode();
    // Park a filled expense draft if the user typed one and forgot Add.
    if (mode === "expense") {
      var draftName = (els.expenseName.value || "").trim();
      var draftAmount = (els.expenseAmount.value || "").trim();
      if (draftName || draftAmount || els.expenseCategory.value) {
        if (!els.expenseCategory.value || !draftName ||
            !draftAmount || isNaN(parseFloat(draftAmount)) ||
            parseFloat(draftAmount) <= 0) {
          stockError("Finish the expense draft or clear it, then tap Add.");
          if (!els.expenseCategory.value) els.expenseCategory.focus();
          else if (!draftName) els.expenseName.focus();
          else els.expenseAmount.focus();
          return;
        }
        addExpenseLine();
      }
    }
    if (!state.stock.lines.length) {
      stockError(mode === "expense"
        ? "Add at least one expense."
        : "Add at least one item with a quantity.");
      if (mode === "expense") els.expenseName.focus();
      return;
    }
    // Sync editable qty + price / expense amount inputs back into lines.
    els.stockItems.querySelectorAll("[data-line-qty]").forEach(function (inp) {
      var i = parseInt(inp.dataset.lineQty, 10);
      state.stock.lines[i].quantity = inp.value;
    });
    els.stockItems.querySelectorAll("[data-line-price]").forEach(function (inp) {
      var i = parseInt(inp.dataset.linePrice, 10);
      state.stock.lines[i].buying_price = inp.value;
    });
    els.stockItems.querySelectorAll("[data-line-amount]").forEach(function (inp) {
      var i = parseInt(inp.dataset.lineAmount, 10);
      if (state.stock.lines[i]) state.stock.lines[i].amount = inp.value;
    });
    var payload;
    if (mode === "sale") {
      var method = els.stockPayMethod.value;
      var kind = els.stockKind.value;
      payload = {
        kind: kind,
        lines: state.stock.lines.map(function (l) {
          return {
            item_id: l.item_id,
            quantity: l.quantity,
            serials: l.serials || [],
            buying_price: l.buying_price
          };
        }),
        payment_method: method,
        cash_amount: method === "both" ? els.stockCashAmt.value : null,
        mpesa_amount: method === "both" ? els.stockMpesaAmt.value : null,
        client_name: els.stockClientName.value,
        client_phone: els.stockClientPhone.value,
        share_whatsapp: kind === "quotation" && els.stockWhatsapp.checked,
        staff_code: els.stockStaffCode.value,
        credit_due_date: kind === "credit" ? els.stockDueDate.value : null
      };
    } else if (mode === "trade") {
      var tradeName = (els.stockClientName.value || "").trim();
      var tradePhone = (els.stockClientPhone.value || "").trim();
      if (!tradeName) {
        stockError("Enter the client full name.");
        setStockDetails(true);
        els.stockClientName.focus();
        return;
      }
      if (!tradePhone) {
        stockError("Enter the client phone number.");
        setStockDetails(true);
        els.stockClientPhone.focus();
        return;
      }
      payload = {
        kind: tradeKind(),
        lines: state.stock.lines.map(function (l) {
          return {
            item_id: l.item_id,
            quantity: l.quantity,
            serials: l.serials || [],
            buying_price: l.buying_price
          };
        }),
        client_name: tradeName,
        client_phone: tradePhone,
        staff_code: els.stockStaffCode.value
      };
    } else if (mode === "expense") {
      var ephone = (els.stockPhone.value || "").trim();
      var esname = (els.stockSupname.value || "").trim();
      for (var ei = 0; ei < state.stock.lines.length; ei++) {
        var eline = state.stock.lines[ei];
        var eamt = parseFloat(eline.amount);
        if (!eline.category || !(eline.name || "").trim() ||
            !eamt || isNaN(eamt) || eamt <= 0) {
          stockError("Every expense needs a category, name, and amount.");
          return;
        }
      }
      if (!ephone) {
        stockError("Enter the supplier's phone number.");
        setStockDetails(true);
        els.stockPhone.focus();
        return;
      }
      if (!esname) {
        stockError("Enter the supplier's name.");
        setStockDetails(true);
        els.stockSupname.focus();
        return;
      }
      payload = {
        lines: state.stock.lines.map(function (l) {
          return {
            category: l.category,
            name: l.name,
            amount: l.amount
          };
        }),
        payment_status: els.expenseStatus.value,
        supplier_name: esname,
        supplier_phone: ephone,
        staff_code: els.stockStaffCode.value
      };
    } else if (mode === "quickin") {
      payload = {
        staff_code: els.stockStaffCode.value,
        lines: state.stock.lines.map(function (l) {
          return {
            item_id: l.item_id,
            quantity: l.quantity,
            serials: l.serials || [],
            buying_price: l.buying_price
          };
        })
      };
    } else if (mode === "transfer") {
      payload = {
        destination_shop_id: parseInt(els.stockDest.value, 10),
        staff_code: els.stockStaffCode.value,
        lines: state.stock.lines.map(function (l) {
          return {
            item_id: l.item_id,
            quantity: l.quantity,
            serials: l.serials || []
          };
        })
      };
    } else {
      var phone = (els.stockPhone.value || "").trim();
      var sname = (els.stockSupname.value || "").trim();
      if (!phone) {
        stockError("Enter the supplier's phone number.");
        setStockDetails(true);
        els.stockPhone.focus();
        return;
      }
      if (!sname) {
        stockError("Enter the supplier's name.");
        setStockDetails(true);
        els.stockSupname.focus();
        return;
      }
      payload = {
        lines: state.stock.lines.map(function (l) {
          return {
            item_id: l.item_id,
            quantity: l.quantity,
            serials: l.serials || [],
            buying_price: l.buying_price
          };
        }),
        supplier_phone: phone,
        supplier_name: sname,
        staff_code: els.stockStaffCode.value
      };
    }
    state.stock.submitting = true;
    els.stockSubmit.disabled = true;
    stockError("");
    var url = mode === "quickin" ? urlFor(URLS.quickStockin, state.activeId)
      : mode === "transfer" ? URLS.transfers
      : (mode === "sale" || mode === "trade") ? URLS.sale
      : mode === "expense" ? URLS.expense : URLS.stockIn;
    post(url, payload)
      .then(function (resp) {
        if (!resp.ok) {
          stockError(resp.error || "Could not save. Try again.");
          els.stockStaffCode.focus();
          els.stockStaffCode.select();
          return;
        }
        closeStock();
        if (resp.whatsapp_url) {
          window.open(resp.whatsapp_url, "_blank", "noopener");
        }
        if (resp.message && resp.conversation_id === state.activeId) {
          appendIfNew(resp.message);
        }
        if (resp.pending) {
          showToast(mode === "quickin"
            ? "Posted — a member of your shop confirms with their code."
            : "Sent — pending a shop member's confirmation " +
              "(see your Assistant chat).");
        } else if (mode === "quickin") {
          showToast("Stock received — posted to the chat.");
        } else if (mode === "transfer") {
          showToast("Transfer sent — the shop is being notified.");
        } else if (mode === "sale") {
          showToast("Sale " + (resp.receipt || "") + " recorded — see your Assistant chat.");
        } else if (mode === "trade") {
          showToast("Trade " + (resp.receipt || "") + " recorded — see your Assistant chat.");
        } else if (mode === "expense") {
          showToast("Expense saved. See it in your Assistant chat.");
        } else {
          showToast("Stock in saved. See it in your Assistant chat.");
        }
        pollNow();
      })
      .catch(function () {
        stockError("Network error — check your connection and retry.");
      })
      .finally(function () {
        state.stock.submitting = false;
        els.stockSubmit.disabled = false;
      });
  }

  function sendTyping() {
    if (!state.activeId || state.activeKind === "bot") return;
    var now = Date.now();
    if (now - state.lastTypingSent < 2500) return;
    state.lastTypingSent = now;
    post(urlFor(URLS.typing, state.activeId), { typing: 1 });
  }

  // ------------------------------------------------------------------
  // Theme
  // ------------------------------------------------------------------

  function paintTheme() {
    var dark = document.documentElement.getAttribute("data-theme") === "dark";
    if (!els.themeToggle) return;
    var d = els.themeToggle.querySelector("[data-icon-dark]");
    var l = els.themeToggle.querySelector("[data-icon-light]");
    if (d) d.hidden = dark;
    if (l) l.hidden = !dark;
  }

  // ------------------------------------------------------------------
  // Events
  // ------------------------------------------------------------------

  els.chats.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-convo]");
    if (btn) openConversation(parseInt(btn.dataset.convo, 10));
  });

  els.search.addEventListener("input", function () {
    state.filter = els.search.value.trim();
    renderChatList();
  });

  els.newBtn.addEventListener("click", showContacts);
  els.contactsClose.addEventListener("click", hideContacts);

  els.contactSearch.addEventListener("input", function () {
    state.contactFilter = els.contactSearch.value.trim();
    renderContacts();
  });

  els.contactList.addEventListener("click", function (e) {
    var newGroup = e.target.closest("[data-newgroup]");
    if (newGroup) { enterGroupMode(); return; }
    var btn = e.target.closest("[data-contact]");
    if (!btn) return;
    if (state.groupMode) {
      var id = btn.dataset.contact;
      state.groupSel[id] = !state.groupSel[id];
      if (!state.groupSel[id]) delete state.groupSel[id];
      syncGroupUi();
      renderContacts();
      return;
    }
    openByTarget(btn.dataset.contact);
  });

  if (els.groupCreate) {
    els.groupCreate.addEventListener("click", createGroup);
  }
  if (els.groupName) {
    els.groupName.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); createGroup(); }
    });
  }

  if (els.groupSettingsBtn) {
    els.groupSettingsBtn.addEventListener("click", showGroupSettings);
  }
  if (els.groupPanelClose) {
    els.groupPanelClose.addEventListener("click", hideGroupSettings);
  }
  if (els.groupSave) {
    els.groupSave.addEventListener("click", saveGroupName);
  }
  if (els.groupDelete) {
    els.groupDelete.addEventListener("click", deleteGroup);
  }
  if (els.groupRename) {
    els.groupRename.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); saveGroupName(); }
    });
  }

  els.back.addEventListener("click", function () {
    requestNavClose("chat");
  });

  els.composer.addEventListener("submit", function (e) {
    e.preventDefault();
    sendText(els.input.value);
  });

  els.input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendText(els.input.value);
    }
  });

  els.input.addEventListener("input", function () {
    // "#" opens the quick stock-in flow — pick an item, qty + buy price,
    // and the card posts into the open chat for a member to confirm.
    if (els.input.value === "#" && state.activeId) {
      els.input.value = "";
      openStock("quickin");
      return;
    }
    autosize();
    sendTyping();
  });

  els.emojiBtn.addEventListener("click", function () {
    els.emojiPop.hidden = !els.emojiPop.hidden;
  });

  els.emojiPop.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-emoji]");
    if (!btn) return;
    els.input.value += btn.dataset.emoji;
    autosize();
    els.input.focus();
  });

  document.addEventListener("click", function (e) {
    if (!e.target.closest("[data-wa-emoji-pop]") &&
        !e.target.closest("[data-wa-emoji]")) {
      els.emojiPop.hidden = true;
    }
  });

  els.file.addEventListener("change", function () {
    if (els.file.files && els.file.files[0]) {
      sendImage(els.file.files[0]);
      els.file.value = "";
    }
  });

  // Plus menu → photo / transfer / stock in
  els.plus.addEventListener("click", function () {
    els.menu.hidden = !els.menu.hidden;
  });

  els.menu.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-menu]");
    if (!btn) return;
    els.menu.hidden = true;
    var action = btn.dataset.menu;
    if (action === "photo") {
      els.file.click();
    } else {
      openStock(action);
    }
  });

  document.addEventListener("click", function (e) {
    if (!e.target.closest("[data-wa-menu]") && !e.target.closest("[data-wa-plus]")) {
      els.menu.hidden = true;
    }
  });

  els.stockClose.addEventListener("click", closeStock);

  function setOthersOpen(open) {
    if (!els.fabs) return;
    var more = els.fabs.querySelector("[data-fab-more]");
    var toggle = els.fabs.querySelector("[data-fab-others-toggle]");
    if (!more || !toggle) return;
    more.hidden = !open;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.classList.toggle("is-open", open);
    if (open) refreshIcons();
  }

  if (els.fabs) {
    els.fabs.addEventListener("click", function (e) {
      var othersToggle = e.target.closest("[data-fab-others-toggle]");
      if (othersToggle) {
        e.stopPropagation();
        var more = els.fabs.querySelector("[data-fab-more]");
        setOthersOpen(more && more.hidden);
        return;
      }
      var btn = e.target.closest("[data-fab]");
      if (btn) {
        setOthersOpen(false);
        if (btn.dataset.fab === "receipts") {
          var receiptsUrl = root.dataset.receiptsUrl || "/chatbot/chat/receipts/";
          window.location.href = receiptsUrl;
          return;
        }
        openStock(btn.dataset.fab);
      }
    });

    document.addEventListener("click", function (e) {
      if (!e.target.closest("[data-fab-others]")) setOthersOpen(false);
    });
  }

  els.stockItemInput.addEventListener("input", function () {
    state.stock.picked = null;
    searchItems(els.stockItemInput.value, els.stockItemSuggest, function (item) {
      state.stock.picked = item;
      els.stockItemInput.value = item.name;
      hideSuggest(els.stockItemSuggest);
      // Autofill the sell/buy price from the item's shop price — editable.
      // Trade in is different: the owed amount is negotiated, so it stays
      // blank until typed (the server requires it anyway).
      if (!els.stockPrice.hidden) {
        var skipAutofill =
          stockMode() === "trade" && tradeKind() === "trade_in";
        els.stockPrice.value =
          !skipAutofill && item.price != null ? item.price : "";
      }
      if (item.track_serial) {
        els.stockQty.hidden = true;
        els.stockSerialWrap.hidden = false;
        hideSuggest(els.stockSerialSuggest);
        state.stock.serials = [];
        renderSerialChips();
        els.stockSerial.focus();
      } else {
        els.stockSerialWrap.hidden = true;
        els.stockQty.hidden = false;
        hideSuggest(els.stockSerialSuggest);
        els.stockQty.focus();
      }
    });
  });

  els.stockQty.addEventListener("keydown", function (e) {
    if (e.key === "Enter") addStockLine();
  });

  els.stockSerial.addEventListener("input", function () {
    searchSerials(els.stockSerial.value);
  });

  els.stockSerial.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      var first = els.stockSerialSuggest.hidden
        ? null
        : els.stockSerialSuggest.querySelector("[data-ser]");
      if (first && els.stockSerial.value.trim()) {
        addSerial(first.dataset.ser);
        renderSerialChips();
        hideSuggest(els.stockSerialSuggest);
        els.stockSerial.value = "";
      } else {
        pushSerial();
        hideSuggest(els.stockSerialSuggest);
      }
    }
  });

  els.stockSerials.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-serial-remove]");
    if (!btn) return;
    state.stock.serials.splice(parseInt(btn.dataset.serialRemove, 10), 1);
    renderSerialChips();
  });

  // Camera barcode scanning (html5-qrcode, vendored)
  var scanner = null;

  function closeScannerUi() {
    els.scanModal.hidden = true;
    if (scanner) {
      scanner.stop().catch(function () {}).then(function () {
        try { scanner.clear(); } catch (e) {}
        scanner = null;
      });
    }
  }

  function closeScanner() {
    if (els.scanModal.hidden) return;
    requestNavClose("scanner");
  }

  function openScanner() {
    if (typeof Html5Qrcode === "undefined") {
      showToast("Camera scanning isn't available in this browser.");
      return;
    }
    els.scanModal.hidden = false;
    pushNav("scanner");
    refreshIcons();
    scanner = new Html5Qrcode("wa-scan-reader", { verbose: false });
    var config = {
      fps: 10,
      qrbox: { width: 260, height: 120 },
      formatsToSupport: [
        Html5QrcodeSupportedFormats.CODE_128,
        Html5QrcodeSupportedFormats.CODE_39,
        Html5QrcodeSupportedFormats.EAN_13,
        Html5QrcodeSupportedFormats.EAN_8,
        Html5QrcodeSupportedFormats.QR_CODE,
        Html5QrcodeSupportedFormats.DATA_MATRIX
      ]
    };
    scanner
      .start(
        { facingMode: "environment" },
        config,
        function (decoded) {
          var added = addSerial(decoded);
          renderSerialChips();
          showToast(added === "dup"
            ? "Duplicate serial skipped."
            : "Added " + decoded.toUpperCase());
        },
        function () { /* per-frame miss — ignore */ }
      )
      .catch(function () {
        showToast("Could not open the camera — check permissions.");
        closeScanner();
      });
  }

  els.stockScanBtn.addEventListener("click", openScanner);
  els.scanClose.addEventListener("click", closeScanner);

  els.stockAdd.addEventListener("click", addStockLine);
  if (els.expenseAdd) {
    els.expenseAdd.addEventListener("click", addExpenseLine);
  }
  if (els.expenseName) {
    els.expenseName.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        addExpenseLine();
      }
    });
  }
  if (els.expenseAmount) {
    els.expenseAmount.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        addExpenseLine();
      }
    });
  }

  els.stockItems.addEventListener("click", function (e) {
    var step = e.target.closest("[data-line-step]");
    if (step) {
      var si = parseInt(step.dataset.lineStep, 10);
      var line = state.stock.lines[si];
      if (!line) return;
      var next =
        (parseFloat(line.quantity) || 0) + parseFloat(step.dataset.step, 10);
      if (next <= 0) {
        state.stock.lines.splice(si, 1);
        renderStockLines();
        return;
      }
      line.quantity = String(next);
      var qinp =
        els.stockItems.querySelector('[data-line-qty="' + si + '"]');
      if (qinp) qinp.value = line.quantity;
      var stot =
        els.stockItems.querySelector('[data-line-ltotal="' + si + '"]');
      if (stot) stot.textContent = lineTotalText(line);
      refreshStockTotal();
      return;
    }
    var btn = e.target.closest("[data-line-remove]");
    if (!btn) return;
    state.stock.lines.splice(parseInt(btn.dataset.lineRemove, 10), 1);
    renderStockLines();
  });

  function pickSupplier(s) {
    els.stockPhone.value = s.dial + " " + s.phone;
    els.stockSupname.value = s.name;
    hideSuggest(els.stockPhoneSuggest);
    hideSuggest(els.stockNameSuggest);
    syncDetailsToggleLabel();
  }

  els.stockPhone.addEventListener("input", function () {
    syncDetailsToggleLabel();
    searchSuppliers(els.stockPhone.value, els.stockPhoneSuggest, pickSupplier);
  });
  els.stockPhone.addEventListener("focus", function () {
    if ((els.stockPhone.value || "").trim()) {
      searchSuppliers(els.stockPhone.value, els.stockPhoneSuggest, pickSupplier);
    }
  });

  els.stockSupname.addEventListener("input", function () {
    syncDetailsToggleLabel();
    searchSuppliers(els.stockSupname.value, els.stockNameSuggest, pickSupplier);
  });
  els.stockSupname.addEventListener("focus", function () {
    if ((els.stockSupname.value || "").trim()) {
      searchSuppliers(els.stockSupname.value, els.stockNameSuggest, pickSupplier);
    }
  });

  els.stockClientName.addEventListener("input", function () {
    searchClients(els.stockClientName.value, els.stockClientNameSuggest,
      function (c) {
        els.stockClientName.value = c.name;
        if (!els.stockClientPhone.value.trim()) {
          els.stockClientPhone.value = c.phone || "";
        }
        hideSuggest(els.stockClientNameSuggest);
      });
  });

  els.stockClientPhone.addEventListener("input", function () {
    searchClients(els.stockClientPhone.value, els.stockClientPhoneSuggest,
      function (c) {
        els.stockClientPhone.value = c.phone || "";
        if (!els.stockClientName.value.trim()) {
          els.stockClientName.value = c.name;
        }
        hideSuggest(els.stockClientPhoneSuggest);
      });
  });

  // Suggestion dropdowns float over the next input — dismiss via Close,
  // the dim mask, Escape, or a tap outside the active field.
  // Listen on root: floated suggests are reparented out of the stock panel.
  root.addEventListener("click", function (e) {
    var x = e.target.closest("[data-sugx]");
    if (!x || !root.contains(x)) return;
    e.preventDefault();
    var box = x.closest(".wa-stock__suggest");
    if (box) hideSuggest(box);
  });
  root.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    hideAllSuggests();
  });
  document.addEventListener("pointerdown", function (e) {
    if (e.target.closest && e.target.closest(".wa-stock__sugmask")) return;
    root.querySelectorAll(".wa-stock__suggest:not([hidden])").forEach(
      function (box) {
        // Taps inside the dropdown or its own field keep it open.
        if (box.contains(e.target)) return;
        var home = suggestHome(box);
        if (home && home.contains(e.target)) return;
        hideSuggest(box);
      }
    );
  }, true);

  if (els.stockTradeKinds) {
    els.stockTradeKinds.addEventListener("change", function (e) {
      if (e.target.matches("[data-trade-kind]")) syncTradeKind();
    });
  }
  els.stockPayMethod.addEventListener("change", function () {
    els.stockPaySplit.hidden = els.stockPayMethod.value !== "both";
  });
  els.stockKind.addEventListener("change", function () {
    var k = els.stockKind.value;
    els.stockPayMethod.hidden = k !== "sale";
    els.stockPaySplit.hidden = k !== "sale" || els.stockPayMethod.value !== "both";
    els.stockDueDate.hidden = k !== "credit";
    els.stockWhatsappWrap.hidden = k !== "quotation";
    els.stockClientName.placeholder =
      k === "sale" ? "Client name (optional)" : "Client name or phone required";
    els.stockSubmitLabel.textContent =
      k === "sale" ? "Complete sale" : k === "credit" ? "Save credit" : "Save quotation";
  });
  els.stockStaffCode.addEventListener("input", function () {
    if (els.stockStaffCode.value.trim().length === 6) submitStock();
  });
  els.stockStaffCode.addEventListener("keydown", function (e) {
    if (e.key === "Enter") submitStock();
  });
  // Live qty/price/amount edits update the per-line and grand totals.
  els.stockItems.addEventListener("input", function (e) {
    var q = e.target.closest("[data-line-qty]");
    if (q) {
      var qi = parseInt(q.dataset.lineQty, 10);
      if (state.stock.lines[qi]) state.stock.lines[qi].quantity = q.value;
    }
    var p = e.target.closest("[data-line-price]");
    if (p) {
      var pi = parseInt(p.dataset.linePrice, 10);
      if (state.stock.lines[pi]) state.stock.lines[pi].buying_price = p.value;
    }
    var a = e.target.closest("[data-line-amount]");
    if (a) {
      var ai = parseInt(a.dataset.lineAmount, 10);
      if (state.stock.lines[ai]) state.stock.lines[ai].amount = a.value;
      var atot = els.stockItems.querySelector('[data-line-ltotal="' + ai + '"]');
      if (atot) {
        var amt = parseFloat(a.value) || 0;
        atot.textContent = amt > 0
          ? "KSh " + amt.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2
            })
          : "";
      }
      refreshStockTotal();
      return;
    }
    if (!q && !p) return;
    var i = parseInt((q || p).dataset.lineQty || (q || p).dataset.linePrice, 10);
    var tot = els.stockItems.querySelector('[data-line-ltotal="' + i + '"]');
    if (tot && state.stock.lines[i]) {
      tot.textContent = lineTotalText(state.stock.lines[i]);
    }
    refreshStockTotal();
  });

  els.stockSubmit.addEventListener("click", submitStock);
  if (els.stockDetailsToggle) {
    els.stockDetailsToggle.addEventListener("click", function () {
      var opening = els.stockDetails.hidden;
      setStockDetails(opening);
      repositionFloatSuggests();
      if (opening && state.stock &&
          (state.stock.mode === "stockin" || state.stock.mode === "expense")) {
        setTimeout(function () {
          if (!(els.stockPhone.value || "").trim()) els.stockPhone.focus();
          else if (!(els.stockSupname.value || "").trim()) els.stockSupname.focus();
        }, 40);
      }
    });
  }
  if (els.stockBody) {
    els.stockBody.addEventListener("scroll", repositionFloatSuggests,
      { passive: true });
  }
  if (els.stockDetails) {
    els.stockDetails.addEventListener("scroll", repositionFloatSuggests,
      { passive: true });
  }
  window.addEventListener("resize", repositionFloatSuggests);
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", repositionFloatSuggests);
    window.visualViewport.addEventListener("scroll", repositionFloatSuggests);
  }

  // ------------------------------------------------------------------
  // Bot period picker — Day / Period / Month / Year from the calendar
  // ------------------------------------------------------------------

  var MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function todayISO() {
    var n = new Date();
    var m = n.getMonth() + 1, d = n.getDate();
    return n.getFullYear() + "-" + (m < 10 ? "0" : "") + m +
           "-" + (d < 10 ? "0" : "") + d;
  }

  function isoToText(iso) {
    // "2026-10-31" → "31 Oct 2026" — the assistant parses D Mon YYYY.
    var p = iso.split("-");
    return parseInt(p[2], 10) + " " +
           MONTHS_SHORT[parseInt(p[1], 10) - 1] + " " + p[0];
  }

  function buildYearOptions() {
    var y = new Date().getFullYear();
    var opts = "";
    for (var i = y; i >= y - 9; i--) {
      opts += '<option value="' + i + '">' + i + "</option>";
    }
    els.yearSel.innerHTML = opts;
  }

  var RECEIPT_STATUS_TOPICS = { sales: 1, items: 1, summary: 1 };

  function topicNeedsReceiptStatus(topic) {
    return !!RECEIPT_STATUS_TOPICS[(topic || "").toLowerCase()];
  }

  function setReceiptStatus(status) {
    if (status !== "pending" && status !== "confirmed") return;
    state.receiptStatus = status;
    if (els.receiptStatusSel) els.receiptStatusSel.value = status;
  }

  function setPeriodMode(mode) {
    state.periodMode = mode;
    els.period.querySelectorAll("[data-period-mode]").forEach(function (b) {
      b.classList.toggle("is-on", b.dataset.periodMode === mode);
    });
    els.dateDay.hidden = mode !== "day";
    els.dateFrom.hidden = els.dateTo.hidden = mode !== "period";
    els.monthInput.hidden = mode !== "month";
    els.yearSel.hidden = mode !== "year";
  }

  function openPeriodPicker(topic) {
    var now = new Date();
    state.botTopic = topic;
    els.periodTopic.textContent = topic;
    els.dateDay.value = todayISO();
    els.dateFrom.value = "";
    els.dateTo.value = "";
    els.monthInput.value = now.getFullYear() + "-" +
      (now.getMonth() + 1 < 10 ? "0" : "") + (now.getMonth() + 1);
    els.yearSel.value = String(now.getFullYear());
    setPeriodMode("day");
    var showScope = topicNeedsReceiptStatus(topic);
    if (els.receiptScope) els.receiptScope.hidden = !showScope;
    setReceiptStatus(receiptStatusDefault);
    els.period.hidden = false;
    els.dateDay.focus();
    refreshIcons();
  }

  els.suggestionsBar.addEventListener("click", function (e) {
    var chip = e.target.closest("[data-suggest]");
    if (!chip) return;
    // Tap a topic chip → period bar (single day / period / month / year
    // picked from the calendar). Go sends e.g. "sales 1 Jan 2025 to 31 Dec 2025".
    var topic = chip.dataset.suggest.replace(/\s*\(.*?\)\s*/g, "").trim();
    openPeriodPicker(topic);
  });

  els.period.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-period-mode]");
    if (btn) setPeriodMode(btn.dataset.periodMode);
  });

  if (els.receiptStatusSel) {
    els.receiptStatusSel.addEventListener("change", function () {
      setReceiptStatus(els.receiptStatusSel.value);
    });
  }

  els.period.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.target.closest("button")) {
      e.preventDefault();
      sendPeriodRange();
    } else if (e.key === "Escape") {
      els.period.hidden = true;
      els.input.focus();
    }
  });

  // Live send: picking a complete value for the active mode sends it —
  // day on date pick, month on month pick, year on year pick, and a
  // period once both from + to dates are chosen.
  // Receipt status is choose-only — user taps the arrow to apply.
  els.period.addEventListener("change", function (e) {
    if (e.target && e.target.matches && e.target.matches("[data-wa-receipt-status]")) {
      return;
    }
    var m = state.periodMode;
    if (m === "day" && !els.dateDay.value) return;
    if (m === "month" && !els.monthInput.value) return;
    if (m === "year" && !els.yearSel.value) return;
    if (m === "period" && !(els.dateFrom.value && els.dateTo.value)) return;
    sendPeriodRange();
  });

  function sendPeriodRange() {
    var text = null;
    var mode = state.periodMode;
    if (mode === "day") {
      if (!els.dateDay.value) return;
      text = state.botTopic + " " + isoToText(els.dateDay.value);
    } else if (mode === "period") {
      var from = els.dateFrom.value, to = els.dateTo.value;
      if (!from && !to) return;
      if (from && to && to < from) { var tmp = from; from = to; to = tmp; }
      text = state.botTopic + " " + isoToText(from || to);
      if (from && to && from !== to) text += " to " + isoToText(to);
    } else if (mode === "month") {
      if (!els.monthInput.value) return;
      var ym = els.monthInput.value.split("-");
      var y = parseInt(ym[0], 10), m = parseInt(ym[1], 10);
      var last = new Date(y, m, 0).getDate();
      text = state.botTopic + " 1 " + MONTHS_SHORT[m - 1] + " " + y +
             " to " + last + " " + MONTHS_SHORT[m - 1] + " " + y;
    } else if (mode === "year") {
      text = state.botTopic + " 1 Jan " + parseInt(els.yearSel.value, 10) +
             " to 31 Dec " + parseInt(els.yearSel.value, 10);
    }
    if (!text) return;
    if (topicNeedsReceiptStatus(state.botTopic)) {
      text += " " + state.receiptStatus;
    }
    els.dateFrom.value = "";
    els.dateTo.value = "";
    sendText(text);
    els.period.hidden = true;
  }

  els.periodGo.addEventListener("click", sendPeriodRange);
  els.periodClose.addEventListener("click", function () {
    els.period.hidden = true;
  });

  els.loadMore.addEventListener("click", loadOlder);

  function confirmPending(msgId) {
    var inp = root.querySelector('[data-pacode="' + msgId + '"]');
    var code = inp ? inp.value.trim() : "";
    post(urlFor(URLS.pendingConfirm, msgId), { staff_code: code })
      .then(function (resp) {
        var err = root.querySelector('[data-paerr="' + msgId + '"]');
        if (!resp.ok) {
          if (err) {
            err.textContent = resp.error || "Could not confirm.";
            err.hidden = false;
          }
          if (inp) { inp.focus(); inp.select(); }
          return;
        }
        // The card becomes the real receipt/movement card in place.
        for (var i = 0; i < state.messages.length; i++) {
          if (state.messages[i].id === resp.message.id) {
            state.messages[i] = resp.message;
            break;
          }
        }
        renderStage();
        if (resp.card && resp.card_conversation_id === state.activeId) {
          appendIfNew(resp.card);
        }
        if (resp.note) appendIfNew(resp.note);
        if (resp.whatsapp_url) {
          window.open(resp.whatsapp_url, "_blank", "noopener");
        }
        scrollBottom(true);
        showToast("Confirmed — the transaction went through.");
        pollNow();
      })
      .catch(function () {
        var err = root.querySelector('[data-paerr="' + msgId + '"]');
        if (err) {
          err.textContent = "Network error — try again.";
          err.hidden = false;
        }
      });
  }

  function respondTransfer(movementId, decision) {
    var payload = { decision: decision };
    // Both accept and decline need a member's staff code — the movement
    // records which employee responded.
    var codeInp = root.querySelector('[data-tcode="' + movementId + '"]');
    payload.staff_code = codeInp ? codeInp.value.trim() : "";
    if (decision === "accept") {
      var received = {};
      root.querySelectorAll('[data-rcv]').forEach(function (inp) {
        if (inp.closest('[data-tcard]') &&
            inp.closest('[data-tcard]').dataset.tcard === String(movementId)) {
          received[inp.dataset.rcv] = inp.value;
        }
      });
      payload.received = received;
      payload.serials = state.rcvSerials[movementId] || {};
    } else {
      var box = root.querySelector('[data-treason="' + movementId + '"]');
      payload.reason = box ? box.value : "";
    }
    post(urlFor(URLS.respond, movementId), payload).then(function (resp) {
      var err = root.querySelector('[data-terr="' + movementId + '"]');
      if (!resp.ok) {
        if (err) {
          err.textContent = resp.error || "Could not respond.";
          err.hidden = false;
        }
        return;
      }
      delete state.rcvSerials[movementId];
      // Update the stored card so the status persists.
      if (resp.card_message_id && resp.card_meta) {
        for (var i = 0; i < state.messages.length; i++) {
          if (state.messages[i].id === resp.card_message_id) {
            state.messages[i].meta = resp.card_meta;
            break;
          }
        }
      }
      if (resp.message) appendIfNew(resp.message);
      renderStage();
      scrollBottom(true);
      showToast(decision === "accept"
        ? "Transfer confirmed — stock moved to your shop."
        : "Transfer declined.");
    });
  }

  els.stage.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    var paInp = e.target.closest("[data-pacode]");
    if (paInp) {
      e.preventDefault();
      confirmPending(paInp.dataset.pacode);
      return;
    }
    var tcode = e.target.closest("[data-tcode]");
    if (tcode) {
      e.preventDefault();
      respondTransfer(tcode.dataset.tcode, "accept");
      return;
    }
    var treason = e.target.closest("[data-treason]");
    if (treason) {
      e.preventDefault();
      respondTransfer(treason.dataset.treason, "decline");
      return;
    }
    var inp = e.target.closest("[data-rcvsn]");
    if (!inp) return;
    e.preventDefault();
    var parts = inp.dataset.rcvsn.split(":");
    var movId = parts[0], lineId = parts[1];
    var serial = (inp.value || "").trim().toUpperCase();
    inp.value = "";
    if (!serial) return;
    // Expected serials are masked — just collect scanned serials as chips;
    // the server validates the match on Accept.
    var expectedCount = null;
    for (var i = 0; i < state.messages.length; i++) {
      var mt = state.messages[i].meta && state.messages[i].meta.transfer;
      if (mt && String(mt.movement_id) === String(movId)) {
        (mt.items || []).forEach(function (it) {
          if (String(it.line_id) === lineId) {
            expectedCount =
              it.serial_count || (it.serials || []).length || null;
          }
        });
      }
    }
    var done = state.rcvSerials[movId] = state.rcvSerials[movId] || {};
    var list = done[lineId] = done[lineId] || [];
    var errBox = root.querySelector('[data-terr="' + movId + '"]');
    if (list.indexOf(serial) !== -1) return;
    if (expectedCount !== null && list.length >= expectedCount) {
      if (errBox) {
        errBox.textContent = "All serials for this line are already scanned.";
        errBox.hidden = false;
      }
      return;
    }
    list.push(serial);
    var box = root.querySelector('[data-snbox="' + lineId + '"]');
    if (box) {
      var pendingChip = box.querySelector(".is-pending");
      if (pendingChip) pendingChip.remove();
      box.insertAdjacentHTML(
        "beforeend",
        '<span class="wa-card__sn is-ok">' + escapeHtml(serial) + "</span>"
      );
      if (expectedCount !== null && list.length < expectedCount) {
        box.insertAdjacentHTML(
          "beforeend",
          '<span class="wa-card__sn is-pending">' +
            (expectedCount - list.length) + " left</span>"
        );
      }
    }
    var prog = root.querySelector('[data-snprog="' + lineId + '"]');
    if (prog) {
      prog.textContent = expectedCount !== null
        ? list.length + "/" + expectedCount + " SN"
        : list.length + " SN";
    }
    if (errBox) errBox.hidden = true;
  });

  els.stage.addEventListener("click", function (e) {
    var custBtn = e.target.closest("[data-custphone]");
    if (custBtn) {
      var ph = custBtn.parentNode.querySelector(".wa-phone");
      if (ph) ph.hidden = !ph.hidden;
      refreshIcons();
      return;
    }

    var eye = e.target.closest("[data-ritems]");
    if (eye) {
      e.preventDefault();
      toggleReceiptItems(eye);
      return;
    }

    var bitems = e.target.closest("[data-bitems]");
    if (bitems) {
      e.preventDefault();
      var rng = bitems.dataset.bitems.split(":");
      sendText(
        "items " + rng[0] + " to " + rng[1] +
        (rng[2] ? " " + rng[2] : "")
      );
      return;
    }

    var img = e.target.closest(".wa-bubble__photo");
    if (img && img.src) window.open(img.src, "_blank");

    var acc = e.target.closest("[data-taccept]");
    if (acc) { respondTransfer(acc.dataset.taccept, "accept"); return; }

    var dec = e.target.closest("[data-tdecline]");
    if (dec) {
      var wrap = root.querySelector('[data-treasonwrap="' + dec.dataset.tdecline + '"]');
      if (wrap) wrap.hidden = !wrap.hidden;
      return;
    }

    var sendDec = e.target.closest("[data-tsenddecline]");
    if (sendDec) {
      respondTransfer(sendDec.dataset.tsenddecline, "decline");
      return;
    }

    var paConfirm = e.target.closest("[data-paconfirm]");
    if (paConfirm) {
      confirmPending(paConfirm.dataset.paconfirm);
    }
  });

  // Staff code entry on a pending card or an incoming transfer: the 6th
  // digit submits (pending card confirms, transfer accepts).
  els.stage.addEventListener("input", function (e) {
    var inp = e.target.closest("[data-pacode]");
    if (inp && inp.value.trim().length === 6) {
      confirmPending(inp.dataset.pacode);
      return;
    }
    var tcode = e.target.closest("[data-tcode]");
    if (tcode && tcode.value.trim().length === 6) {
      respondTransfer(tcode.dataset.tcode, "accept");
    }
  });

  if (els.themeToggle) {
    els.themeToggle.addEventListener("click", function () {
      var dark = document.documentElement.getAttribute("data-theme") === "dark";
      var next = dark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("myshop-theme", next); } catch (e) {}
      paintTheme();
    });
  }

  // Notification sound mute — persisted across visits.
  function paintMute() {
    if (!els.mute) return;
    var on = els.mute.querySelector("[data-bell-on]");
    var off = els.mute.querySelector("[data-bell-off]");
    if (on) on.hidden = state.muted;
    if (off) off.hidden = !state.muted;
    els.mute.setAttribute("aria-pressed", state.muted ? "true" : "false");
    els.mute.title = state.muted
      ? "Unmute notification sounds"
      : "Mute notification sounds";
  }
  try { state.muted = localStorage.getItem("mychat-muted") === "1"; } catch (e) {}
  if (els.mute) {
    els.mute.addEventListener("click", function () {
      state.muted = !state.muted;
      try {
        localStorage.setItem("mychat-muted", state.muted ? "1" : "0");
      } catch (e) {}
      paintMute();
      if (!state.muted) {
        ensureAudio();
        playSound("chat");
      }
    });
  }

  // Browsers gate audio and Notification permission behind a user
  // gesture — unlock both on the first tap or keypress.
  var permissionAsked = false;
  function firstGesture() {
    ensureAudio();
    if (!permissionAsked) {
      permissionAsked = true;
      if ("Notification" in window &&
          Notification.permission === "default") {
        try { Notification.requestPermission(); } catch (e) {}
      }
    }
  }
  document.addEventListener("pointerdown", firstGesture);
  document.addEventListener("keydown", firstGesture);

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) pollNow();
  });

  // Keep the app locked to the *visible* viewport. Android Chrome often
  // pans the layout when the keyboard opens (offsetTop > 0) while we also
  // shrink --wa-vvh — that combination leaves a black strip under the
  // stock panel. Undo the pan and pin height to visualViewport.
  function syncViewportHeight() {
    var vv = window.visualViewport;
    var h = vv ? vv.height : window.innerHeight;
    var top = vv ? vv.offsetTop : 0;
    var left = vv ? vv.offsetLeft : 0;
    if (h) root.style.setProperty("--wa-vvh", Math.round(h) + "px");
    root.style.transform = (top || left)
      ? "translate(" + Math.round(left) + "px," + Math.round(top) + "px)"
      : "";
    if (window.scrollX || window.scrollY) window.scrollTo(0, 0);
    if (document.documentElement.scrollTop) {
      document.documentElement.scrollTop = 0;
    }
    if (document.body.scrollTop) document.body.scrollTop = 0;
    // Soft keyboard open — relax stock min-heights that fight the shorter pane.
    var kb = !!(vv && (window.innerHeight - vv.height > 120 || top > 0));
    root.classList.toggle("is-kb", kb);
  }

  syncViewportHeight();
  window.addEventListener("resize", syncViewportHeight);
  window.addEventListener("orientationchange", syncViewportHeight);
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", syncViewportHeight);
    window.visualViewport.addEventListener("scroll", syncViewportHeight);
  }

  // Stock panel inputs: browser scroll-into-view also causes the black
  // gap — cancel document scroll and keep the focused field inside the
  // panel's own scroll areas instead.
  if (els.stockPanel) {
    els.stockPanel.addEventListener("focusin", function (e) {
      var target = e.target;
      setTimeout(function () {
        syncViewportHeight();
        if (!target || !els.stockPanel.contains(target)) return;
        try {
          target.scrollIntoView({ block: "nearest", inline: "nearest" });
        } catch (err) {
          target.scrollIntoView(false);
        }
        syncViewportHeight();
      }, 50);
      setTimeout(syncViewportHeight, 300);
    });
    els.stockPanel.addEventListener("focusout", function () {
      setTimeout(syncViewportHeight, 50);
      setTimeout(syncViewportHeight, 300);
    });
  }

  // ------------------------------------------------------------------
  // Boot
  // ------------------------------------------------------------------

  buildEmojiPop();
  buildSuggestions();
  buildYearOptions();
  renderChatList();
  paintTheme();
  paintMute();
  updateTitleBadge();
  refreshIcons();
  schedule();

  // Deep link from the all-shops page: /chat/?fab=sale|stockin|transfer|
  // trade|trade_in|trade_out|expense opens that stock panel immediately.
  var fabParam = "";
  try {
    fabParam = new URLSearchParams(window.location.search).get("fab") || "";
  } catch (e) {}
  if (fabParam === "sale" || fabParam === "stockin" ||
      fabParam === "transfer" || fabParam === "trade" ||
      fabParam === "trade_in" || fabParam === "trade_out" ||
      fabParam === "expense") {
    openStock(fabParam);
    try {
      window.history.replaceState(null, "", window.location.pathname);
    } catch (e) {}
  }
})();
