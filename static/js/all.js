/* All-shops assistant — company-wide chat for staff (admin/manager/IT).
   Stateless: POST /api/all/chat/ → {reply} bubbles. */
(function () {
  "use strict";

  var appShell = document.querySelector(".wa-app");
  var stage = document.getElementById("allStage");
  var form = document.getElementById("allForm");
  var input = document.getElementById("allInput");
  var csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
  var sugs = document.getElementById("allSugs");
  var allChatUrl = (appShell && appShell.dataset.allChatUrl) || "/chatbot/api/all/chat/";
  var receiptItemsBase =
    (appShell && appShell.dataset.receiptItemsBase) ||
    "/chatbot/api/receipts/0/items/";

  function urlFor(base, id) {
    return base.replace("/0/", "/" + id + "/");
  }

  // Keep the app locked to the visible viewport; undo Android keyboard pan.
  function syncViewportHeight() {
    if (!appShell) return;
    var vv = window.visualViewport;
    var h = vv ? vv.height : window.innerHeight;
    var top = vv ? vv.offsetTop : 0;
    var left = vv ? vv.offsetLeft : 0;
    if (h) appShell.style.setProperty("--wa-vvh", Math.round(h) + "px");
    appShell.style.transform = (top || left)
      ? "translate(" + Math.round(left) + "px," + Math.round(top) + "px)"
      : "";
    if (window.scrollX || window.scrollY) window.scrollTo(0, 0);
    var kb = !!(vv && (window.innerHeight - vv.height > 120 || top > 0));
    appShell.classList.toggle("is-kb", kb);
  }

  syncViewportHeight();
  window.addEventListener("resize", syncViewportHeight);
  window.addEventListener("orientationchange", syncViewportHeight);
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", syncViewportHeight);
    window.visualViewport.addEventListener("scroll", syncViewportHeight);
  }

  var period = document.getElementById("allPeriod");
  var periodTopic = document.getElementById("allPeriodTopic");
  var dateDay = document.getElementById("allDateDay");
  var dateFrom = document.getElementById("allDateFrom");
  var dateTo = document.getElementById("allDateTo");
  var monthInput = document.getElementById("allMonth");
  var yearSel = document.getElementById("allYear");
  var periodGo = document.getElementById("allPeriodGo");
  var periodClose = document.getElementById("allPeriodClose");
  var receiptScope = document.getElementById("allReceiptScope");
  var receiptStatusSel = document.getElementById("allReceiptStatus");
  var botTopic = "";
  var periodMode = "day";
  var RECEIPT_STATUS_TOPICS = { sales: 1, items: 1, summary: 1 };
  var receiptStatusDefault = "pending";
  try {
    var rsEl = document.getElementById("all-init-receipt-status");
    if (rsEl) receiptStatusDefault = JSON.parse(rsEl.textContent);
  } catch (e) {}
  if (receiptStatusDefault !== "confirmed" && receiptStatusDefault !== "pending") {
    receiptStatusDefault = "pending";
  }
  var receiptStatus = receiptStatusDefault;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return {
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[c];
    });
  }

  function fmt(t) {
    var h = esc(t);
    h = h.replace(
      /%%items\|([0-9-]+)\|([0-9-]+)(?:\|(pending|confirmed))?%%/g,
      '<a class="wa-link" href="#" data-bitems="$1:$2:$3">View items sold →</a>'
    );
    h = h.replace(/\*([^*\n]+)\*/g, "<strong>$1</strong>");
    h = h.replace(/_([^_\n]+)_/g, "<em>$1</em>");
    h = h.replace(/~([^~\n]+)~/g, "<s>$1</s>");
    return h.replace(/\n/g, "<br>");
  }

  // ---------------------------------------------------------------
  // Cards — same wa-card widgets the shop assistant renders
  // ---------------------------------------------------------------

  function refreshIcons() {
    if (window.lucide && window.lucide.createIcons) {
      window.lucide.createIcons();
    }
  }

  function statCard(icon, title, rows, foot) {
    return (
      '<div class="wa-card"><div class="wa-card__head wa-card__head--in">' +
      '<i data-lucide="' + icon + '"></i><span>' + esc(title) +
      "</span></div>" +
      '<div class="wa-stats">' +
      rows.map(function (r) {
        return (
          '<div class="wa-stats__row' + (r[2] ? " is-total" : "") + '">' +
          "<span>" + esc(r[0]) + "</span>" +
          "<b>" + esc(String(r[1])) + "</b></div>"
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
      '<i data-lucide="' + icon + '"></i><span>' + esc(title) +
      "</span></div>" +
      '<div class="wa-card__tablewrap"><table class="wa-table' +
      (tableCls ? " " + tableCls : "") + '"><thead><tr>' +
      head.map(function (h) {
        var hc = ((h[1] ? "n" : "") + " " + (h[2] || "")).trim();
        return "<th" + (hc ? ' class="' + hc + '"' : "") + ">" + esc(h[0]) +
          "</th>";
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
      esc(r.customer) + "</button>" +
      '<div class="wa-phone" hidden>' +
      (phone
        ? '<i data-lucide="phone"></i><a class="wa-link" href="tel:' +
          esc(phone.replace(/[^+\d]/g, "")) + '">' + esc(phone) + "</a>"
        : '<span class="wa-phone__none">No phone on file</span>') +
      "</div>"
    );
  }

  function creditReceiptCell(r) {
    return (
      '<span class="wa-rcpt">' + esc(r.receipt) +
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
            it.serials.map(esc).join(" · ") + "</div></td></tr>"
          : "";
      return (
        '<tr><td class="n">' + (i + 1) + "</td><td>" +
        esc(it.name) + '</td><td class="n">' + esc(it.qty) +
        '</td><td class="n">' + esc(it.price) + '</td><td class="n">' +
        esc(it.total) + "</td></tr>" + sn
      );
    }).join("");
    var rc = data.receipt || {};
    return (
      '<div class="wa-ritems__head">Receipt ' +
      esc(rc.number || "") + " · " + esc(rc.customer || "") +
      "</div>" +
      '<table class="wa-table wa-table--mini"><thead><tr><th>#</th>' +
      "<th>Item</th><th class=\"n\">Qty</th><th class=\"n\">Price</th>" +
      '<th class="n">Total</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="5">No lines on this receipt.</td></tr>') +
      "</tbody></table>" +
      '<div class="wa-ritems__foot"><b>' + esc(rc.total || "") +
      "</b> · paid " + esc(rc.paid || "") + "</div>"
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
    fetch(urlFor(receiptItemsBase, rid), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (!resp.ok) {
          box.innerHTML = '<span class="wa-ritems__err">' +
            esc(resp.error || "Could not load items.") + "</span>";
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

  function cardHtml(card) {
    if (card.sales) {
      var c = card.sales;
      var itemsLink = c.start && c.end
        ? '<a class="wa-link" href="#" data-bitems="' +
          esc(c.start + ":" + c.end + ":" + (c.receipt_status || "")) +
          '">View items sold →</a>'
        : "";
      return statCard("trending-up", "Sales — all shops · " + c.label, [
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
        u.label.slice(1) + " at all shops", [
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
          creditCustCell(r) + "</td><td>" + esc(r.shop || "") +
          "</td><td>" + creditReceiptCell(r) +
          '</td><td class="n">' + esc(r.balance) + '</td><td class="n">' +
          esc(r.due || "—") + "</td></tr>";
      }).join("");
      return tableCard("hand-coins", "Outstanding credit — all shops" +
        (cr.label ? " · " + cr.label : ""), [
        ["#", 0, "wa-cnum"], ["Customer"], ["Shop"], ["Receipt"],
        ["Balance", 1], ["Due", 1]
      ], crows, "<b>" + esc(cr.total) + "</b> across " + cr.count +
        " receipts", "wa-table--credits");
    }
    if (card.expenses) {
      var ex = card.expenses;
      var erows = (ex.rows || []).map(function (r, i) {
        return '<tr><td class="n">' + (i + 1) + "</td><td>" +
          esc(r.name) + "</td><td>" + esc(r.shop || "") +
          "</td><td>" + esc(r.cat) +
          '</td><td class="n">' + esc(r.amount) + '</td><td class="n">' +
          esc(r.status || "") + "</td></tr>";
      }).join("");
      return tableCard("receipt", "Expenses — all shops · " + ex.label, [
        ["#"], ["Item"], ["Shop"], ["Category"], ["Amount", 1], ["Status", 1]
      ], erows, "Total <b>" + esc(ex.total) + "</b>" +
        (ex.unpaid !== "KSh 0.00"
          ? " · unpaid " + esc(ex.unpaid) : ""));
    }
    if (card.movement && card.movement.groups) {
      var mv = card.movement;
      var nlines = 0;
      var secs = (mv.groups || []).map(function (g) {
        nlines += (g.items || []).length;
        var badge =
          g.kind === "in" ? "+IN" : g.kind === "out" ? "−OUT" :
          (g.kind === "tout" ? "→ " : "← ") + g.head;
        var meta = [g.status, g.when].filter(Boolean).join(" · ");
        var lines = (g.items || []).map(function (r, i) {
          return '<tr><td class="n">' + (i + 1) + "</td><td>" +
            esc(r.item) + '</td><td class="n">' + esc(r.qty) +
            "</td></tr>";
        }).join("");
        return (
          '<div class="wa-mvg"><div class="wa-mvg__head">' +
          '<span class="wa-mv--' + g.kind + '">' + esc(badge) +
          "</span>" +
          (meta ? '<span class="wa-mvg__meta">' + esc(meta) +
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
        esc(mv.label) + "</span></div>" +
        '<div class="wa-card__tablewrap">' + secs + "</div>" +
        '<div class="wa-card__foot">' + (mv.groups || []).length +
        " movements · " + nlines + " lines</div></div>"
      );
    }
    if (card.items_sold) {
      var it = card.items_sold;
      var rows = (it.items || []).map(function (r, i) {
        return (
          '<tr><td class="n">' + (i + 1) + "</td>" +
          "<td>" + esc(r.name) + "</td>" +
          '<td class="n">' + esc(r.qty) + "</td>" +
          '<td class="n">' + esc(r.total) + "</td></tr>"
        );
      }).join("");
      return (
        '<div class="wa-card"><div class="wa-card__head wa-card__head--in">' +
        '<i data-lucide="list"></i><span>Items sold — all shops · ' +
        esc(it.label || "") + "</span></div>" +
        '<div class="wa-card__tablewrap"><table class="wa-table">' +
        '<thead><tr><th>#</th><th>Item</th><th class="n">Qty</th>' +
        '<th class="n">Amount</th></tr></thead><tbody>' +
        rows + "</tbody></table></div>" +
        '<div class="wa-card__foot">' + (it.items || []).length +
        " items</div></div>"
      );
    }
    return "";
  }

  function bubble(text, mine, card) {
    var row = document.createElement("div");
    row.className = "wa-bubble-row " + (mine ? "is-out" : "is-in");
    var inner = card ? cardHtml(card) : "<p>" + fmt(text) + "</p>";
    row.innerHTML =
      '<div class="wa-bubble ' + (mine ? "is-out" : "is-in") + '">' +
      inner + "</div>";
    stage.appendChild(row);
    stage.scrollTop = stage.scrollHeight;
    refreshIcons();
  }

  function send(text) {
    text = (text || "").trim();
    if (!text) return;
    bubble(text, true);
    input.value = "";
    fetch(allChatUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify({ text: text })
    })
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (resp.ok) {
          bubble(resp.reply, false, resp.card);
        } else {
          bubble(resp.error || "Error", false);
        }
      })
      .catch(function () {
        bubble("Connection problem — try again.", false);
      });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    send(input.value);
  });

  stage.addEventListener("click", function (e) {
    var cust = e.target.closest("[data-custphone]");
    if (cust) {
      var ph = cust.parentNode.querySelector(".wa-phone");
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
    if (!bitems) return;
    e.preventDefault();
    var rng = bitems.dataset.bitems.split(":");
    send(
      "items " + rng[0] + " to " + rng[1] +
      (rng[2] ? " " + rng[2] : "")
    );
  });

  // ------------------------------------------------------------------
  // Period picker — Day / Period / Month / Year from the calendar
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
    var p = iso.split("-");
    return parseInt(p[2], 10) + " " +
           MONTHS_SHORT[parseInt(p[1], 10) - 1] + " " + p[0];
  }

  function topicNeedsReceiptStatus(topic) {
    return !!RECEIPT_STATUS_TOPICS[(topic || "").toLowerCase()];
  }

  function setReceiptStatus(status) {
    if (status !== "pending" && status !== "confirmed") return;
    receiptStatus = status;
    if (receiptStatusSel) receiptStatusSel.value = status;
  }

  function setPeriodMode(mode) {
    periodMode = mode;
    period.querySelectorAll("[data-period-mode]").forEach(function (b) {
      b.classList.toggle("is-on", b.dataset.periodMode === mode);
    });
    dateDay.hidden = mode !== "day";
    dateFrom.hidden = dateTo.hidden = mode !== "period";
    monthInput.hidden = mode !== "month";
    yearSel.hidden = mode !== "year";
  }

  function openPeriodPicker(topic) {
    var now = new Date();
    botTopic = topic;
    periodTopic.textContent = topic;
    dateDay.value = todayISO();
    dateFrom.value = "";
    dateTo.value = "";
    monthInput.value = now.getFullYear() + "-" +
      (now.getMonth() + 1 < 10 ? "0" : "") + (now.getMonth() + 1);
    yearSel.value = String(now.getFullYear());
    setPeriodMode("day");
    if (receiptScope) receiptScope.hidden = !topicNeedsReceiptStatus(topic);
    setReceiptStatus(receiptStatusDefault);
    period.hidden = false;
    dateDay.focus();
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  function sendPeriod() {
    var text = null;
    if (periodMode === "day") {
      if (!dateDay.value) return;
      text = botTopic + " " + isoToText(dateDay.value);
    } else if (periodMode === "period") {
      var from = dateFrom.value, to = dateTo.value;
      if (!from && !to) return;
      if (from && to && to < from) { var tmp = from; from = to; to = tmp; }
      text = botTopic + " " + isoToText(from || to);
      if (from && to && from !== to) text += " to " + isoToText(to);
    } else if (periodMode === "month") {
      if (!monthInput.value) return;
      var ym = monthInput.value.split("-");
      var y = parseInt(ym[0], 10), m = parseInt(ym[1], 10);
      var last = new Date(y, m, 0).getDate();
      text = botTopic + " 1 " + MONTHS_SHORT[m - 1] + " " + y +
             " to " + last + " " + MONTHS_SHORT[m - 1] + " " + y;
    } else if (periodMode === "year") {
      text = botTopic + " 1 Jan " + parseInt(yearSel.value, 10) +
             " to 31 Dec " + parseInt(yearSel.value, 10);
    }
    if (!text) return;
    if (topicNeedsReceiptStatus(botTopic)) {
      text += " " + receiptStatus;
    }
    dateFrom.value = "";
    dateTo.value = "";
    period.hidden = true;
    send(text);
  }

  sugs.addEventListener("click", function (e) {
    var b = e.target.closest("[data-sug]");
    if (b) openPeriodPicker(b.dataset.sug);
  });

  period.addEventListener("click", function (e) {
    var b = e.target.closest("[data-period-mode]");
    if (b) setPeriodMode(b.dataset.periodMode);
  });
  if (receiptStatusSel) {
    receiptStatusSel.addEventListener("change", function () {
      setReceiptStatus(receiptStatusSel.value);
    });
  }
  period.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.target.closest("button")) {
      e.preventDefault();
      sendPeriod();
    } else if (e.key === "Escape") {
      period.hidden = true;
      input.focus();
    }
  });
  // Live send: picking a complete value for the active mode sends it —
  // day on date pick, month on month pick, year on year pick, and a
  // period once both from + to dates are chosen.
  // Receipt status is choose-only — user taps the arrow to apply.
  period.addEventListener("change", function (e) {
    if (e.target && e.target.matches && e.target.matches("#allReceiptStatus, [data-wa-receipt-status]")) {
      return;
    }
    if (periodMode === "day" && !dateDay.value) return;
    if (periodMode === "month" && !monthInput.value) return;
    if (periodMode === "year" && !yearSel.value) return;
    if (periodMode === "period" && !(dateFrom.value && dateTo.value)) return;
    sendPeriod();
  });
  periodGo.addEventListener("click", sendPeriod);
  periodClose.addEventListener("click", function () {
    period.hidden = true;
  });

  // ------------------------------------------------------------------
  // Stock action FABs — pick a shop, then jump into its chat view with
  // the matching panel open (?fab= is handled by chatbot.js).
  // ------------------------------------------------------------------

  var fabs = document.querySelector("[data-all-fabs]");
  var fabMenu = document.getElementById("allFabMenu");
  var fabTitle = document.getElementById("allFabTitle");
  var fabForm = document.getElementById("allFabForm");
  var fabShop = document.getElementById("allFabShop");
  var fabMode = document.getElementById("allFabMode");
  var FAB_LABELS = {
    sale: "New sale", stockin: "Stock in", transfer: "Transfer stock",
    trade: "Trade", trade_out: "Trade out", trade_in: "Trade in",
    expense: "Register expense", receipts: "Receipts"
  };
  var fabMore = fabs ? fabs.querySelector("[data-fab-more]") : null;
  var fabOthersToggle = fabs
    ? fabs.querySelector("[data-fab-others-toggle]")
    : null;

  function setOthersOpen(open) {
    if (!fabMore || !fabOthersToggle) return;
    fabMore.hidden = !open;
    fabOthersToggle.setAttribute("aria-expanded", open ? "true" : "false");
    fabOthersToggle.classList.toggle("is-open", open);
    if (open && window.lucide && window.lucide.createIcons) {
      window.lucide.createIcons();
    }
  }

  function openShopPicker(mode) {
    setOthersOpen(false);
    fabMode.value = mode;
    fabTitle.textContent =
      (FAB_LABELS[mode] || "Action") + " — pick a shop";
    fabMenu.hidden = false;
    if (window.lucide && window.lucide.createIcons) {
      window.lucide.createIcons();
    }
  }

  if (fabs && fabMenu && fabForm) {
    fabs.addEventListener("click", function (e) {
      var othersToggle = e.target.closest("[data-fab-others-toggle]");
      if (othersToggle) {
        e.stopPropagation();
        fabMenu.hidden = true;
        setOthersOpen(fabMore && fabMore.hidden);
        return;
      }
      var b = e.target.closest("[data-fab]");
      if (!b) return;
      openShopPicker(b.dataset.fab);
    });

    fabMenu.addEventListener("click", function (e) {
      var b = e.target.closest("[data-shop-pick]");
      if (!b) return;
      fabShop.value = b.dataset.shopPick;
      fabForm.submit();
    });

    document.addEventListener("click", function (e) {
      if (!e.target.closest("[data-fab-others]")) setOthersOpen(false);
      if (fabMenu.hidden) return;
      if (!e.target.closest("#allFabMenu") &&
          !e.target.closest("[data-all-fabs]")) {
        fabMenu.hidden = true;
      }
    });
  }

  (function buildYearOptions() {
    var y = new Date().getFullYear();
    var opts = "";
    for (var i = y; i >= y - 9; i--) {
      opts += '<option value="' + i + '">' + i + "</option>";
    }
    yearSel.innerHTML = opts;
  })();

  // Back — prefer previous page; fall back to shop chat when this
  // tab has no in-app history (e.g. opened directly / PWA cold start).
  var backBtn = document.querySelector("[data-all-back]");
  if (backBtn) {
    backBtn.addEventListener("click", function () {
      var fallback = backBtn.getAttribute("data-fallback") || "/chatbot/chat/";
      var canGoBack = false;
      if (document.referrer) {
        try {
          canGoBack = new URL(document.referrer).origin === location.origin;
        } catch (e) {}
      }
      if (canGoBack && history.length > 1) {
        history.back();
        return;
      }
      location.href = fallback;
    });
  }

  // Theme toggle
  var themeBtn = document.querySelector("[data-theme-toggle]");
  function paint() {
    var dark = document.documentElement.getAttribute("data-theme") === "dark";
    if (themeBtn) {
      themeBtn.querySelector("[data-icon-dark]").hidden = dark;
      themeBtn.querySelector("[data-icon-light]").hidden = !dark;
    }
  }
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var dark = document.documentElement.getAttribute("data-theme") === "dark";
      var next = dark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("myshop-theme", next); } catch (e) {}
      paint();
    });
  }
  paint();

  if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
})();
