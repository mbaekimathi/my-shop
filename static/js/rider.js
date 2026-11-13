(function () {
  "use strict";

  var root = document.querySelector("[data-rider-app]");
  if (!root) return;

  var csrf =
    (root.querySelector("input[name=csrfmiddlewaretoken]") || {}).value ||
    (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] ||
    "";

  var urls = {
    board: root.getAttribute("data-board-url"),
    status: root.getAttribute("data-status-url"),
    accept: root.getAttribute("data-accept-url"),
    complete: root.getAttribute("data-complete-url"),
  };

  var feedEl = root.querySelector("[data-rider-feed]");
  var feedEmpty = root.querySelector("[data-rider-feed-empty]");
  var panel = root.querySelector('[data-rider-panel="complete"]');
  var summaryEl = root.querySelector("[data-rider-complete-summary]");
  var titleEl = root.querySelector("[data-rider-complete-title]");
  var saleFields = root.querySelector("[data-rider-sale-fields]");
  var creditFields = root.querySelector("[data-rider-credit-fields]");
  var receiverInput = root.querySelector("[data-rider-receiver]");
  var payRefInput = root.querySelector("[data-rider-payment-ref]");
  var payAtInput = root.querySelector("[data-rider-payment-at]");
  var payAmountInput = root.querySelector("[data-rider-payment-amount]");
  var submitBtn = root.querySelector("[data-rider-complete-submit]");
  var submitLabel = root.querySelector("[data-rider-complete-label]");
  var statusEl = root.querySelector("[data-rider-form-status]");

  var tab = "open";
  var board = { open: [], active: [], completed: [] };
  var completing = null;
  var payMode = "unpaid";
  var pollTimer = null;
  var knownOpenIds = {};

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

  function urlFor(template, id) {
    return String(template || "").replace("__ID__", String(id));
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

  function showPanel(open) {
    if (panel) panel.hidden = !open;
    root.classList.toggle("is-stock-open", !!open);
    icons();
  }

  function setTab(next) {
    tab = next;
    root.querySelectorAll("[data-rider-tab]").forEach(function (btn) {
      var on = btn.getAttribute("data-rider-tab") === tab;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    renderFeed();
  }

  function updateCounts() {
    ["open", "active", "completed"].forEach(function (key) {
      var el = root.querySelector('[data-rider-count="' + key + '"]');
      if (el) el.textContent = String((board[key] || []).length);
    });
  }

  function metaRow(label, valueHtml) {
    if (!valueHtml) return "";
    return (
      '<div class="rider-card__row"><span>' +
      escapeHtml(label) +
      "</span><strong>" +
      valueHtml +
      "</strong></div>"
    );
  }

  function renderCard(job) {
    var headIcon =
      job.board === "active"
        ? "navigation"
        : job.board === "completed"
          ? "check-circle-2"
          : "package";
    var headCls =
      job.board === "active"
        ? "wa-card__head--active"
        : job.board === "completed"
          ? "wa-card__head--done"
          : "wa-card__head--open";

    var rows = "";
    rows += metaRow("Shop", escapeHtml(job.shop_name));
    rows += metaRow("Type", escapeHtml(job.kind_label));
    rows += metaRow("Location", escapeHtml(job.location_text));
    rows += metaRow("Total", "KSh " + escapeHtml(job.total));
    if (job.route_label) rows += metaRow("Route", escapeHtml(job.route_label));

    if (job.board !== "open") {
      if (job.client_name) rows += metaRow("Client", escapeHtml(job.client_name));
      if (job.client_phone) {
        rows += metaRow(
          "Phone",
          '<a href="tel:' +
            escapeAttr(job.client_phone) +
            '">' +
            escapeHtml(job.client_phone) +
            "</a>"
        );
      }
      if (job.expected_at && job.board === "active") {
        rows += metaRow("Due by", escapeHtml(formatTime(job.expected_at)));
      }
      if (job.receiver_name && job.board === "completed") {
        rows += metaRow("Received by", escapeHtml(job.receiver_name));
      }
      if (job.payment_reference) {
        rows += metaRow("Payment ref", escapeHtml(job.payment_reference));
      }
      if (job.variance_label) {
        rows += metaRow("Timing", escapeHtml(job.variance_label));
      }
    } else {
      rows +=
        '<p class="rider-card__note">Accept to unlock client name and phone.</p>';
    }

    var actions = "";
    if (job.board === "open") {
      actions =
        '<div class="wa-card__actions">' +
        '<button type="button" class="wa-card__btn wa-card__btn--ok" data-rider-accept="' +
        escapeAttr(job.id) +
        '">Accept delivery</button>' +
        "</div>";
    } else if (job.board === "active") {
      actions =
        '<div class="wa-card__actions">' +
        '<button type="button" class="wa-card__btn wa-card__btn--ok" data-rider-complete="' +
        escapeAttr(job.id) +
        '">Complete</button>';
      if (job.maps_url) {
        actions +=
          '<a class="wa-card__btn" href="' +
          escapeAttr(job.maps_url) +
          '" target="_blank" rel="noopener noreferrer">Open map</a>';
      }
      actions += "</div>";
    } else if (job.maps_url) {
      actions =
        '<div class="wa-card__actions">' +
        '<a class="wa-card__btn" href="' +
        escapeAttr(job.maps_url) +
        '" target="_blank" rel="noopener noreferrer">Open map</a>' +
        "</div>";
    }

    return (
      '<article class="wa-bubble wa-bubble--bot rider-msg" data-delivery-id="' +
      escapeAttr(job.id) +
      '">' +
      '<div class="wa-card">' +
      '<div class="wa-card__head ' +
      headCls +
      '">' +
      '<i data-lucide="' +
      headIcon +
      '" aria-hidden="true"></i>' +
      "<span>" +
      escapeHtml(job.receipt_number || "Delivery") +
      " · " +
      escapeHtml(job.status_label) +
      "</span>" +
      "</div>" +
      '<div class="rider-card__meta">' +
      rows +
      "</div>" +
      actions +
      "</div>" +
      "</article>"
    );
  }

  function formatTime(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      return iso;
    }
  }

  function emptyCopy() {
    if (tab === "active") {
      return "No active runs. Accept an open request to start a delivery.";
    }
    if (tab === "completed") {
      return "No completed deliveries yet.";
    }
    return "Queue is clear. New delivery requests will show up here.";
  }

  function renderFeed() {
    updateCounts();
    var rows = board[tab] || [];
    if (!rows.length) {
      feedEl.innerHTML =
        '<div class="wa-sys" data-rider-feed-empty><span>' +
        escapeHtml(emptyCopy()) +
        "</span></div>";
      icons();
      return;
    }
    feedEl.innerHTML = rows.map(renderCard).join("");
    icons();
    feedEl.scrollTop = feedEl.scrollHeight;
  }

  function applyBoard(data) {
    board.open = data.open || [];
    board.active = data.active || [];
    board.completed = data.completed || [];
    knownOpenIds = {};
    board.open.forEach(function (job) {
      knownOpenIds[job.id] = true;
    });
    renderFeed();
  }

  function loadBoard() {
    return fetchJSON(urls.board).then(applyBoard);
  }

  function findJob(id) {
    var lists = [board.open, board.active, board.completed];
    for (var i = 0; i < lists.length; i++) {
      for (var j = 0; j < lists[i].length; j++) {
        if (String(lists[i][j].id) === String(id)) return lists[i][j];
      }
    }
    return null;
  }

  function setPayMode(mode) {
    payMode = mode;
    root.querySelectorAll("[data-pay-mode]").forEach(function (btn) {
      var on = btn.getAttribute("data-pay-mode") === mode;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    root.querySelectorAll("[data-pay-panel]").forEach(function (panelEl) {
      panelEl.hidden = panelEl.getAttribute("data-pay-panel") !== mode;
    });
    if (submitLabel) {
      submitLabel.textContent =
        mode === "paid" ? "Mark delivered · paid" : "Mark delivered · unpaid";
    }
  }

  function openComplete(job) {
    completing = job;
    setStatus("");
    if (titleEl) {
      titleEl.textContent = "Complete · " + (job.receipt_number || "Delivery");
    }
    if (summaryEl) {
      summaryEl.textContent =
        (job.shop_name || "Shop") +
        " · " +
        (job.location_text || "") +
        " · KSh " +
        (job.total || "0.00");
    }
    var isSale = job.kind === "sale";
    var isCredit = job.kind === "credit";
    if (saleFields) saleFields.hidden = !isSale;
    if (creditFields) creditFields.hidden = !isCredit;
    if (receiverInput) {
      receiverInput.value = job.client_name || "";
    }
    if (payRefInput) payRefInput.value = "";
    if (payAtInput) payAtInput.value = "";
    if (payAmountInput) {
      payAmountInput.value = String(job.amount_due || "").replace(/,/g, "") || "";
    }
    setPayMode("unpaid");
    if (submitLabel && isSale) submitLabel.textContent = "Mark delivered";
    showPanel(true);
  }

  function acceptJob(id) {
    var btn = root.querySelector('[data-rider-accept="' + id + '"]');
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Accepting…";
    }
    return fetchJSON(urlFor(urls.accept, id), {
      method: "POST",
      body: "{}",
    })
      .then(function () {
        tab = "active";
        return loadBoard().then(function () {
          setTab("active");
        });
      })
      .catch(function (err) {
        if (btn) {
          btn.disabled = false;
          btn.textContent = "Accept delivery";
        }
        window.alert(err.message || "Could not accept delivery");
      });
  }

  function submitComplete() {
    if (!completing) return;
    var payload = {};
    if (completing.kind === "sale") {
      payload.receiver_name = (receiverInput && receiverInput.value) || "";
    } else if (completing.kind === "credit" && payMode === "paid") {
      payload.payment_reference = (payRefInput && payRefInput.value) || "";
      payload.payment_at = (payAtInput && payAtInput.value) || "";
      payload.payment_amount = (payAmountInput && payAmountInput.value) || "";
      if (!payload.payment_reference.trim()) {
        setStatus("Enter a payment reference for paid delivery.");
        return;
      }
    }
    if (submitBtn) submitBtn.disabled = true;
    setStatus("Saving…", "ok");
    fetchJSON(urlFor(urls.complete, completing.id), {
      method: "POST",
      body: JSON.stringify(payload),
    })
      .then(function (data) {
        setStatus(data.message || "Delivered.", "ok");
        showPanel(false);
        completing = null;
        tab = "completed";
        return loadBoard().then(function () {
          setTab("completed");
        });
      })
      .catch(function (err) {
        setStatus(err.message || "Could not complete delivery");
      })
      .finally(function () {
        if (submitBtn) submitBtn.disabled = false;
      });
  }

  function pollStatus() {
    if (!urls.status) return;
    fetchJSON(urls.status)
      .then(function (data) {
        var rows = data.open || [];
        var fresh = false;
        rows.forEach(function (row) {
          if (!knownOpenIds[row.id]) fresh = true;
        });
        if (fresh || rows.length !== (board.open || []).length) {
          loadBoard();
        }
      })
      .catch(function () {});
  }

  root.querySelectorAll("[data-rider-tab]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setTab(btn.getAttribute("data-rider-tab"));
    });
  });

  root.querySelectorAll("[data-pay-mode]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setPayMode(btn.getAttribute("data-pay-mode"));
    });
  });

  var backBtn = root.querySelector("[data-rider-back]");
  if (backBtn) {
    backBtn.addEventListener("click", function () {
      showPanel(false);
      completing = null;
      setStatus("");
    });
  }

  if (submitBtn) {
    submitBtn.addEventListener("click", submitComplete);
  }

  feedEl.addEventListener("click", function (ev) {
    var acceptBtn = ev.target.closest("[data-rider-accept]");
    if (acceptBtn) {
      acceptJob(acceptBtn.getAttribute("data-rider-accept"));
      return;
    }
    var completeBtn = ev.target.closest("[data-rider-complete]");
    if (completeBtn) {
      var job = findJob(completeBtn.getAttribute("data-rider-complete"));
      if (job) openComplete(job);
    }
  });

  loadBoard()
    .catch(function (err) {
      feedEl.innerHTML =
        '<div class="wa-sys"><span>' +
        escapeHtml(err.message || "Could not load deliveries") +
        "</span></div>";
    })
    .finally(function () {
      pollTimer = window.setInterval(pollStatus, 8000);
    });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) loadBoard().catch(function () {});
  });

  // Pin height to the visible viewport so the complete-panel submit
  // button stays above browser chrome / the home indicator.
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
})();
