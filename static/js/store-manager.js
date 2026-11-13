(function () {
  "use strict";

  var root = document.querySelector("[data-sm-app]");
  if (!root) return;

  var csrf =
    (root.querySelector("input[name=csrfmiddlewaretoken]") || {}).value ||
    (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] ||
    "";

  var urls = {
    items: root.getAttribute("data-items-url"),
    employees: root.getAttribute("data-employees-url"),
    stockIn: root.getAttribute("data-stock-in-url"),
    stockOut: root.getAttribute("data-stock-out-url"),
    report: root.getAttribute("data-report-url"),
    feed: root.getAttribute("data-feed-url"),
  };

  var home = root.querySelector("[data-sm-home]");
  var feedEl = root.querySelector("[data-sm-feed]");
  var feedEmpty = root.querySelector("[data-sm-feed-empty]");
  var feedPanel = root.querySelector('[data-sm-panel="feed"]');
  var formPanel = root.querySelector('[data-sm-panel="form"]');
  var reportPanel = root.querySelector('[data-sm-panel="report"]');
  var formTitle = root.querySelector("[data-sm-form-title]");
  var submitBtn = root.querySelector("[data-sm-submit]");
  var submitLabel = root.querySelector("[data-sm-submit-label]");
  var statusEl = root.querySelector("[data-sm-form-status]");
  var counterpartLabel = root.querySelector("[data-sm-counterpart-label]");
  var empQuery = root.querySelector("[data-sm-employee-query]");
  var empId = root.querySelector("[data-sm-employee-id]");
  var empResults = root.querySelector("[data-sm-employee-results]");
  var noteInput = root.querySelector("[data-sm-note]");
  var cartItems = root.querySelector("[data-sm-cart-items]");
  var cartEmpty = root.querySelector("[data-sm-cart-empty]");
  var cartCount = root.querySelector("[data-sm-cart-count]");
  var itemInput = root.querySelector("[data-sm-item-input]");
  var itemSuggest = root.querySelector("[data-sm-item-suggest]");
  var qtyInput = root.querySelector("[data-sm-qty]");
  var haveEl = root.querySelector("[data-sm-have]");
  var addBtn = root.querySelector("[data-sm-add]");
  var reportItemResults = root.querySelector("[data-sm-report-item-results]");

  var mode = "in";
  var cart = [];
  var pickedItem = null;
  var empTimer = null;
  var itemTimer = null;
  var reportItemTimer = null;
  var range = "all";

  function paintTheme() {
    var themeBtn = root.querySelector("[data-theme-toggle]");
    var dark = document.documentElement.getAttribute("data-theme") === "dark";
    if (!themeBtn) return;
    var darkIcon = themeBtn.querySelector("[data-icon-dark]");
    var lightIcon = themeBtn.querySelector("[data-icon-light]");
    if (darkIcon) darkIcon.hidden = dark;
    if (lightIcon) lightIcon.hidden = !dark;
  }

  var themeBtn = root.querySelector("[data-theme-toggle]");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var dark = document.documentElement.getAttribute("data-theme") === "dark";
      var next = dark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try {
        localStorage.setItem("myshop-theme", next);
      } catch (e) {}
      paintTheme();
    });
  }
  paintTheme();

  function icons() {
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  function show(panel) {
    if (home) home.hidden = !!panel;
    if (feedPanel) feedPanel.hidden = panel !== "feed";
    if (formPanel) formPanel.hidden = panel !== "form";
    if (reportPanel) reportPanel.hidden = panel !== "report";
    root.classList.toggle("is-stock-open", !!panel);
    root.classList.toggle("is-chat-open", panel === "feed");
    if (panel === "feed") loadFeed();
    icons();
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function fetchJSON(url, options) {
    options = options || {};
    var headers = options.headers || {};
    headers["X-Requested-With"] = "XMLHttpRequest";
    if (options.body) {
      headers["Content-Type"] = "application/json";
      headers["X-CSRFToken"] = csrf;
    }
    return fetch(url, {
      method: options.method || "GET",
      headers: headers,
      body: options.body || undefined,
      credentials: "same-origin",
    }).then(function (resp) {
      return resp.json().then(function (data) {
        if (!resp.ok || data.ok === false) {
          var err = new Error(data.error || "Request failed");
          err.status = resp.status;
          throw err;
        }
        return data;
      });
    });
  }

  function setStatus(msg, kind) {
    if (!statusEl) return;
    if (!msg) {
      statusEl.hidden = true;
      statusEl.textContent = "";
      statusEl.className = "wa-stock__error";
      return;
    }
    statusEl.hidden = false;
    statusEl.textContent = msg;
    statusEl.className =
      "wa-stock__error" + (kind === "ok" ? " is-ok" : "");
  }

  /* -------- WhatsApp-style feed -------- */

  function renderStoreCard(card) {
    var s = card && card.store_stock;
    if (!s) return "";
    var isOut = s.mode === "out";
    var icon = isOut ? "package-minus" : "package-plus";
    var headCls = isOut ? "wa-card__head--out" : "wa-card__head--in";
    var rows = (s.items || [])
      .map(function (item, idx) {
        return (
          "<tr><td class=\"n\">" +
          (idx + 1) +
          "</td><td>" +
          escapeHtml(item.name) +
          '</td><td class="n">' +
          escapeHtml(item.qty) +
          '</td><td class="n">' +
          escapeHtml(item.balance || "—") +
          "</td></tr>"
        );
      })
      .join("");
    var footParts = [
      (s.counterpart_label || "By") + " " + (s.counterpart || "—"),
      s.recorded_by ? "Recorded by " + s.recorded_by : "",
      s.note || "",
    ].filter(Boolean);
    return (
      '<div class="wa-card">' +
      '<div class="wa-card__head ' +
      headCls +
      '">' +
      '<i data-lucide="' +
      icon +
      '"></i><span>' +
      escapeHtml(s.title || (isOut ? "Store stock out" : "Store stock in")) +
      "</span></div>" +
      '<div class="wa-card__tablewrap"><table class="wa-table"><thead><tr>' +
      "<th>#</th><th>Item</th><th class=\"n\">Qty</th><th class=\"n\">Bal</th>" +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table></div>" +
      '<div class="wa-card__foot">' +
      escapeHtml(footParts.join(" · ")) +
      "</div></div>"
    );
  }

  function bubbleHtml(msg) {
    var cardHtml = renderStoreCard(msg.card);
    return (
      '<div class="wa-bubble-row is-in" data-sm-day="' +
      escapeAttr(msg.day || "") +
      '">' +
      '<div class="wa-bubble is-in">' +
      (cardHtml || "<p>" + escapeHtml(msg.body || "") + "</p>") +
      '<div class="wa-bubble__meta"><span>' +
      escapeHtml(msg.when || "") +
      "</span></div>" +
      "</div></div>"
    );
  }

  function dayChipHtml(day) {
    return (
      '<div class="wa-day" data-sm-day-chip="' +
      escapeAttr(day) +
      '"><span>' +
      escapeHtml(day) +
      "</span></div>"
    );
  }

  function nowStamp() {
    var d = new Date();
    var hh = String(d.getHours()).padStart(2, "0");
    var mm = String(d.getMinutes()).padStart(2, "0");
    return { when: hh + ":" + mm, day: "Today" };
  }

  function appendFeedMessage(msg, scroll) {
    if (!feedEl) return;
    if (feedEmpty) feedEmpty.hidden = true;
    var day = msg.day || "";
    if (day) {
      var lastChip = feedEl.querySelector("[data-sm-day-chip]:last-of-type");
      var lastBubble = feedEl.querySelector(".wa-bubble-row:last-of-type");
      var prevDay = lastBubble
        ? lastBubble.getAttribute("data-sm-day")
        : lastChip
          ? lastChip.getAttribute("data-sm-day-chip")
          : "";
      if (day !== prevDay) {
        var chipWrap = document.createElement("div");
        chipWrap.innerHTML = dayChipHtml(day);
        feedEl.appendChild(chipWrap.firstChild);
      }
    }
    var wrap = document.createElement("div");
    wrap.innerHTML = bubbleHtml(msg);
    feedEl.appendChild(wrap.firstChild);
    icons();
    if (scroll !== false) {
      feedEl.scrollTop = feedEl.scrollHeight;
    }
  }

  function loadFeed() {
    if (!urls.feed || !feedEl) return;
    fetchJSON(urls.feed)
      .then(function (data) {
        var messages = data.messages || [];
        feedEl.querySelectorAll(".wa-bubble-row, .wa-day").forEach(function (n) {
          n.remove();
        });
        if (!messages.length) {
          if (feedEmpty) feedEmpty.hidden = false;
          return;
        }
        if (feedEmpty) feedEmpty.hidden = true;
        // API returns newest-first; show oldest at top like WhatsApp.
        messages
          .slice()
          .reverse()
          .forEach(function (msg) {
            appendFeedMessage(msg, false);
          });
        feedEl.scrollTop = feedEl.scrollHeight;
      })
      .catch(function () {
        if (feedEmpty) {
          feedEmpty.hidden = false;
          feedEmpty.innerHTML =
            "<span>Could not load store messages.</span>";
        }
      });
  }

  function configureForm(nextMode) {
    mode = nextMode === "out" ? "out" : "in";
    formTitle.textContent = mode === "in" ? "Store Stock In" : "Store Stock Out";
    if (counterpartLabel) {
      counterpartLabel.textContent =
        mode === "in" ? "Who delivered" : "Employee who received";
    }
    if (empQuery) {
      empQuery.placeholder =
        mode === "in"
          ? "Search who delivered…"
          : "Search who received…";
    }
    submitLabel.textContent =
      mode === "in" ? "Record stock in" : "Record stock out";
    formPanel.classList.toggle("sm-stock--out", mode === "out");
    clearEmployee();
    clearPicker();
    cart = [];
    renderCart();
    if (noteInput) noteInput.value = "";
    setStatus("");
  }

  function clearEmployee() {
    if (empId) empId.value = "";
    if (empQuery) empQuery.value = "";
    hideSuggest(empResults);
  }

  function selectEmployee(id, name) {
    empId.value = String(id);
    empQuery.value = name;
    hideSuggest(empResults);
  }

  function clearPicker() {
    pickedItem = null;
    if (itemInput) itemInput.value = "";
    if (qtyInput) qtyInput.value = "";
    if (haveEl) haveEl.value = "";
    hideSuggest(itemSuggest);
  }

  var mqNarrow = window.matchMedia("(max-width: 639px)");
  var sugMask = null;

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

  function clearSuggestPos(el) {
    el.classList.remove("is-float");
    el.style.left = el.style.top = el.style.bottom =
      el.style.width = el.style.maxHeight = el.style.visibility = "";
    restoreSuggestHome(el);
  }

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
    ensureSugMask().hidden = !on;
  }

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
    var clipEl = home.closest(".wa-stock__body") || home.closest(".wa-stock");
    if (!mqNarrow.matches || !clipEl || box.hidden) {
      clearSuggestPos(box);
      return;
    }
    var anchorEl = home.querySelector("input") || home;
    try {
      anchorEl.scrollIntoView({ block: "center", inline: "nearest" });
    } catch (err) {
      try { anchorEl.scrollIntoView(true); } catch (e2) {}
    }
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

  function hideSuggest(el) {
    if (!el) return;
    el.hidden = true;
    el.innerHTML = "";
    clearSuggestPos(el);
    if (!root.querySelector(".wa-stock__suggest.is-float:not([hidden])")) {
      setSugMask(false);
    }
  }

  function hideAllSuggests(except) {
    [empResults, itemSuggest, reportItemResults].forEach(function (el) {
      if (!el || (except && el === except)) return;
      if (!el.hidden) hideSuggest(el);
    });
  }

  function suggestBarHtml() {
    return (
      '<div class="wa-stock__sugbar">' +
      '<span class="wa-stock__sughint">Suggestions</span>' +
      '<button type="button" class="wa-stock__sugx" data-sm-sug-close ' +
      'aria-label="Close suggestions">' +
      '<i data-lucide="x"></i><span>Close</span></button></div>'
    );
  }

  function renderSuggestButtons(el, rows, emptyLabel, onPick) {
    if (!el) return;
    hideAllSuggests(el);
    if (!rows.length) {
      el.innerHTML =
        suggestBarHtml() +
        '<div class="s-empty">' +
        escapeHtml(emptyLabel) +
        "</div>";
      el.hidden = false;
      floatSuggest(el);
      setSugMask(el.classList.contains("is-float"));
      icons();
      return;
    }
    el.innerHTML =
      suggestBarHtml() +
      rows
        .map(function (row) {
          return (
            '<button type="button" data-id="' +
            escapeAttr(row.id) +
            '" data-name="' +
            escapeAttr(row.name) +
            '"' +
            (row.store_qty != null
              ? ' data-store-qty="' + escapeAttr(row.store_qty) + '"'
              : "") +
            (row.category
              ? ' data-category="' + escapeAttr(row.category) + '"'
              : "") +
            ">" +
            '<span class="s-name">' +
            escapeHtml(row.name) +
            "</span>" +
            '<span class="s-meta">' +
            (row.category ? escapeHtml(row.category) : "") +
            (row.store_qty != null
              ? ' · <span class="s-have">store ' +
                escapeHtml(row.store_qty) +
                "</span>"
              : "") +
            "</span>" +
            "</button>"
          );
        })
        .join("");
    el.hidden = false;
    floatSuggest(el);
    setSugMask(el.classList.contains("is-float"));
    icons();
    el.querySelectorAll("button[data-id]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        onPick({
          id: Number(btn.getAttribute("data-id")),
          name: btn.getAttribute("data-name") || "",
          category: btn.getAttribute("data-category") || "",
          store_qty: btn.getAttribute("data-store-qty") || "0",
        });
      });
    });
  }

  function renderCart() {
    if (!cartItems) return;
    if (!cart.length) {
      cartItems.innerHTML = "";
      if (cartEmpty) cartEmpty.hidden = false;
      if (cartCount) cartCount.hidden = true;
      return;
    }
    if (cartEmpty) cartEmpty.hidden = true;
    if (cartCount) {
      cartCount.hidden = false;
      cartCount.textContent = String(cart.length);
    }
    cartItems.innerHTML = cart
      .map(function (line, idx) {
        return (
          '<div class="wa-stock-line" data-cart-idx="' +
          idx +
          '">' +
          '<div class="wa-stock-line__name">' +
          escapeHtml(line.name) +
          '<span class="wa-stock-line__have"> · store ' +
          escapeHtml(line.store_qty || "0") +
          "</span>" +
          "</div>" +
          '<input type="number" min="0" step="any" inputmode="decimal" value="' +
          escapeAttr(line.quantity) +
          '" data-cart-qty aria-label="Qty">' +
          '<button type="button" class="wa-stock-line__remove" data-cart-remove aria-label="Remove">' +
          '<i data-lucide="x"></i>' +
          "</button>" +
          "</div>"
        );
      })
      .join("");
    icons();
  }

  function pickItem(item) {
    pickedItem = item;
    itemInput.value = item.name;
    hideSuggest(itemSuggest);
    if (haveEl) haveEl.value = "Store " + (item.store_qty || "0");
    if (qtyInput) {
      qtyInput.focus();
      qtyInput.select();
    }
  }

  function addToCart() {
    setStatus("");
    if (!pickedItem) {
      setStatus("Search and pick an item first.");
      return;
    }
    var qty = String((qtyInput && qtyInput.value) || "").trim();
    if (!qty || Number(qty) <= 0) {
      setStatus("Enter a quantity greater than zero.");
      return;
    }
    if (mode === "out") {
      var have = Number(pickedItem.store_qty || 0);
      if (Number(qty) > have) {
        setStatus(
          "Store only has " + pickedItem.store_qty + " available."
        );
        return;
      }
    }
    var existing = cart.find(function (line) {
      return line.item_id === pickedItem.id;
    });
    if (existing) {
      existing.quantity = String(
        (Number(existing.quantity) || 0) + (Number(qty) || 0)
      );
      existing.store_qty = pickedItem.store_qty;
    } else {
      cart.push({
        item_id: pickedItem.id,
        name: pickedItem.name,
        category: pickedItem.category || "",
        store_qty: pickedItem.store_qty || "0",
        quantity: qty,
      });
    }
    clearPicker();
    renderCart();
    if (itemInput) itemInput.focus();
  }

  root.querySelectorAll("[data-sm-open]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var which = btn.getAttribute("data-sm-open");
      if (which === "feed") {
        show("feed");
        return;
      }
      if (which === "report") {
        show("report");
        return;
      }
      configureForm(which);
      show("form");
      if (itemInput) itemInput.focus();
    });
  });

  root.querySelectorAll("[data-sm-back]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      show(null);
    });
  });

  if (empQuery) {
    empQuery.addEventListener("input", function () {
      clearTimeout(empTimer);
      if (empId) empId.value = "";
      var q = empQuery.value.trim();
      if (q.length < 1) {
        hideSuggest(empResults);
        return;
      }
      empTimer = setTimeout(function () {
        fetchJSON(
          urls.employees + "?q=" + encodeURIComponent(q) + "&limit=12"
        )
          .then(function (data) {
            renderSuggestButtons(
              empResults,
              (data.employees || []).map(function (p) {
                return { id: p.id, name: p.name };
              }),
              "No matching staff",
              function (row) {
                selectEmployee(row.id, row.name);
              }
            );
          })
          .catch(function () {
            hideAllSuggests(empResults);
            empResults.innerHTML =
              suggestBarHtml() +
              '<div class="s-empty">Search failed</div>';
            empResults.hidden = false;
            floatSuggest(empResults);
            setSugMask(empResults.classList.contains("is-float"));
            icons();
          });
      }, 200);
    });
  }

  if (itemInput) {
    itemInput.addEventListener("input", function () {
      clearTimeout(itemTimer);
      pickedItem = null;
      if (haveEl) haveEl.value = "";
      var q = itemInput.value.trim();
      if (q.length < 1) {
        hideSuggest(itemSuggest);
        return;
      }
      itemTimer = setTimeout(function () {
        fetchJSON(urls.items + "?q=" + encodeURIComponent(q))
          .then(function (data) {
            renderSuggestButtons(
              itemSuggest,
              (data.items || []).slice(0, 12),
              "No matching items",
              pickItem
            );
          })
          .catch(function () {
            hideAllSuggests(itemSuggest);
            itemSuggest.innerHTML =
              suggestBarHtml() +
              '<div class="s-empty">Search failed</div>';
            itemSuggest.hidden = false;
            floatSuggest(itemSuggest);
            setSugMask(itemSuggest.classList.contains("is-float"));
            icons();
          });
      }, 200);
    });
    itemInput.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") {
        ev.preventDefault();
        if (pickedItem) addToCart();
      }
    });
  }

  if (qtyInput) {
    qtyInput.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") {
        ev.preventDefault();
        addToCart();
      }
    });
  }

  if (addBtn) addBtn.addEventListener("click", addToCart);

  if (cartItems) {
    cartItems.addEventListener("input", function (ev) {
      var row = ev.target.closest("[data-cart-idx]");
      if (!row || !ev.target.matches("[data-cart-qty]")) return;
      var idx = Number(row.getAttribute("data-cart-idx"));
      if (!cart[idx]) return;
      cart[idx].quantity = String(ev.target.value || "").trim();
    });
    cartItems.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-cart-remove]");
      if (!btn) return;
      var row = btn.closest("[data-cart-idx]");
      if (!row) return;
      var idx = Number(row.getAttribute("data-cart-idx"));
      cart.splice(idx, 1);
      renderCart();
    });
  }

  if (submitBtn) {
    submitBtn.addEventListener("click", function () {
      setStatus("");
      var receivedBy = empId && empId.value;
      if (!receivedBy) {
        setStatus(
          mode === "in"
            ? "Select who delivered the stock."
            : "Select the employee who received the stock."
        );
        return;
      }
      var lines = cart
        .filter(function (line) {
          return line.item_id && Number(line.quantity) > 0;
        })
        .map(function (line) {
          return { item_id: line.item_id, quantity: String(line.quantity) };
        });
      if (!lines.length) {
        setStatus("Add at least one item to the cart.");
        return;
      }
      submitBtn.disabled = true;
      var url = mode === "in" ? urls.stockIn : urls.stockOut;
      fetchJSON(url, {
        method: "POST",
        body: JSON.stringify({
          received_by_id: Number(receivedBy),
          note: (noteInput && noteInput.value) || "",
          lines: lines,
        }),
      })
        .then(function (data) {
          var stamp = nowStamp();
          appendFeedMessage({
            body: data.body || "",
            card: data.card || null,
            when: stamp.when,
            day: stamp.day,
          });
          cart = [];
          renderCart();
          clearEmployee();
          clearPicker();
          if (noteInput) noteInput.value = "";
          setStatus("");
          show(null);
        })
        .catch(function (err) {
          setStatus(err.message || "Could not save.");
        })
        .finally(function () {
          submitBtn.disabled = false;
        });
    });
  }

  /* -------- report -------- */
  var reportRows = root.querySelector("[data-sm-report-rows]");
  var totalIn = root.querySelector("[data-sm-total-in]");
  var totalOut = root.querySelector("[data-sm-total-out]");
  var rowCount = root.querySelector("[data-sm-row-count]");
  var storeQtyWrap = root.querySelector("[data-sm-store-qty-wrap]");
  var storeQtyEl = root.querySelector("[data-sm-store-qty]");
  var typeSelect = root.querySelector("[data-sm-type]");
  var customFields = root.querySelector("[data-sm-custom-fields]");
  var fromInput = root.querySelector("[data-sm-from]");
  var toInput = root.querySelector("[data-sm-to]");
  var reportItemQuery = root.querySelector("[data-sm-report-item-query]");
  var reportItemId = root.querySelector("[data-sm-report-item-id]");

  root.querySelectorAll("[data-sm-range]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      root.querySelectorAll("[data-sm-range]").forEach(function (b) {
        b.classList.toggle("is-on", b === btn);
      });
      range = btn.getAttribute("data-sm-range") || "all";
      if (customFields) customFields.hidden = range !== "custom";
    });
  });

  function selectReportItem(item) {
    if (!item || !item.id) {
      reportItemId.value = "";
      if (reportItemQuery) reportItemQuery.value = "";
    } else {
      reportItemId.value = String(item.id);
      if (reportItemQuery) reportItemQuery.value = item.name || "";
    }
    hideSuggest(reportItemResults);
  }

  function loadReport() {
    var params = new URLSearchParams();
    params.set("range", range || "all");
    params.set("type", (typeSelect && typeSelect.value) || "all");
    if (reportItemId && reportItemId.value) {
      params.set("item_id", reportItemId.value);
    }
    if (range === "custom") {
      if (fromInput && fromInput.value) params.set("from", fromInput.value);
      if (toInput && toInput.value) params.set("to", toInput.value);
    }
    reportRows.innerHTML = '<p class="sm-report-empty">Loading…</p>';
    fetchJSON(urls.report + "?" + params.toString())
      .then(function (data) {
        if (totalIn) totalIn.textContent = data.total_in || "0";
        if (totalOut) totalOut.textContent = data.total_out || "0";
        if (rowCount) rowCount.textContent = String(data.row_count || 0);
        if (storeQtyWrap && storeQtyEl) {
          if (data.current_store_qty !== "" && data.current_store_qty != null) {
            storeQtyWrap.hidden = false;
            storeQtyEl.textContent = data.current_store_qty;
          } else {
            storeQtyWrap.hidden = true;
          }
        }
        var rows = data.rows || [];
        if (!rows.length) {
          reportRows.innerHTML =
            '<p class="sm-report-empty">No movements for these filters.</p>';
          return;
        }
        reportRows.innerHTML = rows
          .map(function (row) {
            return (
              '<article class="sm-move sm-move--' +
              escapeAttr(row.movement_type) +
              '">' +
              '<div class="sm-move__top">' +
              "<strong>" +
              escapeHtml(row.item_name) +
              "</strong>" +
              '<span class="sm-move__badge">' +
              escapeHtml(row.movement_label) +
              "</span>" +
              "</div>" +
              '<div class="sm-move__meta">' +
              "<span>" +
              escapeHtml(row.when) +
              "</span>" +
              "<span>Qty " +
              escapeHtml(row.quantity) +
              "</span>" +
              "<span>Bal " +
              escapeHtml(row.balance_after) +
              "</span>" +
              "</div>" +
              '<div class="sm-move__people">' +
              "<span>By " +
              escapeHtml(row.recorded_by) +
              "</span>" +
              "<span>" +
              escapeHtml(row.counterpart_label) +
              " " +
              escapeHtml(row.counterpart) +
              "</span>" +
              "</div>" +
              (row.note
                ? '<p class="sm-move__note">' + escapeHtml(row.note) + "</p>"
                : "") +
              "</article>"
            );
          })
          .join("");
        if (data.truncated) {
          reportRows.insertAdjacentHTML(
            "beforeend",
            '<p class="sm-report-empty">Showing first 500 rows.</p>'
          );
        }
      })
      .catch(function (err) {
        reportRows.innerHTML =
          '<p class="sm-report-empty">' +
          escapeHtml(err.message || "Could not load report.") +
          "</p>";
      });
  }

  var goBtn = root.querySelector("[data-sm-report-go]");
  if (goBtn) goBtn.addEventListener("click", loadReport);

  if (reportItemQuery) {
    reportItemQuery.addEventListener("input", function () {
      clearTimeout(reportItemTimer);
      reportItemId.value = "";
      var q = reportItemQuery.value.trim();
      if (!q) {
        hideSuggest(reportItemResults);
        return;
      }
      reportItemTimer = setTimeout(function () {
        fetchJSON(urls.items + "?q=" + encodeURIComponent(q))
          .then(function (data) {
            var rows = (data.items || []).slice(0, 12);
            var withClear = [
              { id: "", name: "All items", store_qty: null, category: "" },
            ].concat(rows);
            renderSuggestButtons(
              reportItemResults,
              withClear,
              "No items",
              function (row) {
                if (!row.id) {
                  selectReportItem(null);
                  return;
                }
                selectReportItem(row);
              }
            );
          })
          .catch(function () {
            hideAllSuggests(reportItemResults);
            reportItemResults.innerHTML =
              suggestBarHtml() +
              '<div class="s-empty">Search failed</div>';
            reportItemResults.hidden = false;
            floatSuggest(reportItemResults);
            setSugMask(reportItemResults.classList.contains("is-float"));
            icons();
          });
      }, 200);
    });
  }

  root.addEventListener("click", function (ev) {
    if (ev.target.closest("[data-sm-sug-close]")) {
      ev.preventDefault();
      hideAllSuggests();
      return;
    }
    function tappedOwnField(box, sel) {
      if (!box || box.hidden) return true;
      if (box.contains(ev.target)) return true;
      var home = suggestHome(box);
      if (home && home.contains(ev.target)) return true;
      if (sel && ev.target.closest(sel)) return true;
      return false;
    }
    if (empResults && !tappedOwnField(empResults, "[data-sm-employee-root]")) {
      hideSuggest(empResults);
    }
    if (itemSuggest && !tappedOwnField(itemSuggest, ".wa-stock__add")) {
      hideSuggest(itemSuggest);
    }
    if (
      reportItemResults &&
      !tappedOwnField(reportItemResults, "[data-sm-report-item-root]")
    ) {
      hideSuggest(reportItemResults);
    }
  });

  root.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") hideAllSuggests();
  });

  root.querySelectorAll(".wa-stock__body").forEach(function (body) {
    body.addEventListener("scroll", repositionFloatSuggests, { passive: true });
  });
  window.addEventListener("resize", repositionFloatSuggests);
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", repositionFloatSuggests);
    window.visualViewport.addEventListener("scroll", repositionFloatSuggests);
  }

  loadFeed();
  icons();
})();
