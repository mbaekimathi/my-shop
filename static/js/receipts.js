(function () {
  "use strict";

  var root = document.querySelector("[data-rcpt-app]");
  if (!root) return;

  var csrf =
    (document.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
  var urls = {
    list: root.dataset.listUrl,
    detailBase: root.dataset.detailBase || "",
    confirm: root.dataset.confirmUrl,
    cancel: root.dataset.cancelUrl,
    returnUrl: root.dataset.returnUrl,
  };

  var els = {
    list: root.querySelector("[data-rcpt-list]"),
    actions: root.querySelector("[data-rcpt-actions]"),
    cartPanel: root.querySelector("[data-rcpt-cart-panel]"),
    cartToggle: root.querySelector("[data-rcpt-cart-toggle]"),
    cartBadge: root.querySelector("[data-rcpt-cart-badge]"),
    selcount: root.querySelector("[data-rcpt-selcount]"),
    confirmBtn: root.querySelector("[data-rcpt-confirm]"),
    cancelBtn: root.querySelector("[data-rcpt-cancel]"),
    returnBtn: root.querySelector("[data-rcpt-return]"),
    detail: root.querySelector("[data-rcpt-detail]"),
    detailTitle: root.querySelector("[data-rcpt-detail-title]"),
    detailBody: root.querySelector("[data-rcpt-detail-body]"),
    detailClose: root.querySelector("[data-rcpt-detail-close]"),
    day: root.querySelector("[data-rcpt-day]"),
    from: root.querySelector("[data-rcpt-from]"),
    to: root.querySelector("[data-rcpt-to]"),
    month: root.querySelector("[data-rcpt-month]"),
    year: root.querySelector("[data-rcpt-year]"),
    status: root.querySelector("[data-rcpt-status]"),
    kind: root.querySelector("[data-rcpt-kind]"),
    q: root.querySelector("[data-rcpt-q]"),
    go: root.querySelector("[data-rcpt-go]"),
    modes: root.querySelectorAll("[data-period-mode]"),
    codeModal: root.querySelector("[data-rcpt-code-modal]"),
    codeTitle: root.querySelector("[data-rcpt-code-title]"),
    codeHint: root.querySelector("[data-rcpt-code-hint]"),
    codeInput: root.querySelector("[data-rcpt-code]"),
    codeOk: root.querySelector("[data-rcpt-code-ok]"),
    codeCancel: root.querySelector("[data-rcpt-code-cancel]"),
    returnModal: root.querySelector("[data-rcpt-return-modal]"),
    returnBody: root.querySelector("[data-rcpt-return-body]"),
    returnReason: root.querySelector("[data-rcpt-return-reason]"),
    returnCode: root.querySelector("[data-rcpt-return-code]"),
    returnSubmit: root.querySelector("[data-rcpt-return-submit]"),
    toast: root.querySelector("[data-wa-toast]"),
  };

  var state = {
    mode: "day",
    receipts: [],
    selected: {},
    pendingAction: null,
    returnDetails: [],
  };

  function refreshIcons() {
    if (window.lucide && window.lucide.createIcons) {
      window.lucide.createIcons();
    }
  }

  function showToast(msg) {
    if (!els.toast) return;
    els.toast.textContent = msg;
    els.toast.hidden = false;
    clearTimeout(showToast._t);
    showToast._t = setTimeout(function () {
      els.toast.hidden = true;
    }, 3200);
  }

  function todayISO() {
    var d = new Date();
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + m + "-" + day;
  }

  function monthISO() {
    var d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
  }

  function buildYears() {
    var y = new Date().getFullYear();
    var opts = "";
    for (var i = y; i >= y - 9; i--) {
      opts += '<option value="' + i + '">' + i + "</option>";
    }
    els.year.innerHTML = opts;
  }

  function syncPeriodFields() {
    var mode = state.mode;
    els.day.hidden = mode !== "day";
    els.from.hidden = mode !== "period";
    els.to.hidden = mode !== "period";
    els.month.hidden = mode !== "month";
    els.year.hidden = mode !== "year";
    els.modes.forEach(function (btn) {
      btn.classList.toggle("is-on", btn.dataset.periodMode === mode);
    });
  }

  function detailUrl(id) {
    return (urls.detailBase || "").replace(/\/0\/?$/, "/" + id + "/");
  }

  function money(v) {
    var n = Number(v);
    if (!isFinite(n)) n = 0;
    return "KSh " + n.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function selectedIds() {
    return Object.keys(state.selected)
      .filter(function (k) {
        return state.selected[k];
      })
      .map(function (k) {
        return Number(k);
      });
  }

  function selectedRows() {
    var ids = selectedIds();
    return state.receipts.filter(function (r) {
      return ids.indexOf(r.id) >= 0;
    });
  }

  function setCartOpen(open) {
    if (!els.cartPanel || !els.cartToggle) return;
    els.cartPanel.hidden = !open;
    els.cartToggle.classList.toggle("is-open", open);
    els.cartToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) refreshIcons();
  }

  function syncActions() {
    var rows = selectedRows();
    var n = rows.length;
    var wasHidden = els.actions.hidden;
    els.actions.hidden = n === 0;
    if (els.cartBadge) els.cartBadge.textContent = String(n);
    if (els.selcount) els.selcount.textContent = String(n);
    if (n === 0) {
      setCartOpen(false);
    } else if (wasHidden && n > 0) {
      // First selection: show cart closed; user taps to open.
      setCartOpen(false);
      refreshIcons();
    }
    var allConfirm = n > 0 && rows.every(function (r) {
      return r.can_confirm;
    });
    var allCancel = n > 0 && rows.every(function (r) {
      return r.can_cancel;
    });
    var anyReturn = rows.some(function (r) {
      return r.can_return;
    });
    els.confirmBtn.disabled = !allConfirm;
    els.cancelBtn.disabled = !allCancel;
    els.returnBtn.disabled = !anyReturn;
  }

  function renderList() {
    if (!state.receipts.length) {
      els.list.innerHTML =
        '<p class="rcpt-empty">No receipts for this filter.</p>';
      refreshIcons();
      syncActions();
      return;
    }
    els.list.innerHTML = state.receipts
      .map(function (r) {
        var checked = state.selected[r.id] ? " checked" : "";
        var selClass = state.selected[r.id] ? " is-selected" : "";
        var activeClass =
          state.activeId === r.id ? " is-active" : "";
        return (
          '<article class="rcpt-row' +
          selClass +
          activeClass +
          '" data-rcpt-id="' +
          r.id +
          '" role="listitem">' +
          '<label class="rcpt-check" onclick="event.stopPropagation()">' +
          '<input type="checkbox" data-rcpt-check value="' +
          r.id +
          '"' +
          checked +
          ">" +
          "</label>" +
          '<button type="button" class="rcpt-row__main" data-rcpt-open="' +
          r.id +
          '">' +
          '<span class="rcpt-row__top">' +
          "<strong>" +
          escapeHtml(r.receipt_number) +
          "</strong>" +
          '<span class="rcpt-badge rcpt-badge--' +
          escapeHtml(r.status) +
          '">' +
          escapeHtml(r.status_label) +
          "</span>" +
          "</span>" +
          '<span class="rcpt-row__meta">' +
          escapeHtml(r.kind_label) +
          " · " +
          escapeHtml(r.client_name || "Walk-in") +
          " · " +
          escapeHtml(r.created_label) +
          "</span>" +
          '<span class="rcpt-row__total">' +
          money(r.total) +
          "</span>" +
          "</button></article>"
        );
      })
      .join("");
    refreshIcons();
    syncActions();
  }

  function loadList() {
    var params = new URLSearchParams();
    params.set("mode", state.mode);
    if (state.mode === "day") params.set("day", els.day.value || todayISO());
    if (state.mode === "period") {
      params.set("from", els.from.value || todayISO());
      params.set("to", els.to.value || todayISO());
    }
    if (state.mode === "month") params.set("month", els.month.value || monthISO());
    if (state.mode === "year") {
      params.set("year", els.year.value || String(new Date().getFullYear()));
    }
    var st = els.status.value;
    if (st && st !== "all") params.set("status", st);
    var kind = els.kind.value;
    if (kind && kind !== "all") params.set("kind", kind);
    var q = (els.q.value || "").trim();
    if (q) params.set("q", q);

    els.list.innerHTML = '<p class="rcpt-empty">Loading…</p>';
    fetch(urls.list + "?" + params.toString(), {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || "Failed to load");
        state.receipts = data.receipts || [];
        var keep = {};
        state.receipts.forEach(function (r) {
          if (state.selected[r.id]) keep[r.id] = true;
        });
        state.selected = keep;
        renderList();
      })
      .catch(function (err) {
        els.list.innerHTML =
          '<p class="rcpt-empty">' + escapeHtml(err.message) + "</p>";
        showToast(err.message);
      });
  }

  function openDetail(id) {
    fetch(detailUrl(id), {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || "Not found");
        var r = data.receipt;
        state.activeId = r.id;
        els.detailTitle.textContent = r.receipt_number;
        var lines = (r.lines || [])
          .map(function (l) {
            var serials =
              (l.remaining_serial_numbers || []).length
                ? "<small>S/N: " +
                  escapeHtml((l.remaining_serial_numbers || []).join(", ")) +
                  "</small>"
                : "";
            return (
              "<li><span>" +
              escapeHtml(l.item_name) +
              " × " +
              escapeHtml(l.remaining_quantity) +
              "</span><span>" +
              money(l.remaining_total) +
              "</span>" +
              serials +
              "</li>"
            );
          })
          .join("");
        els.detailBody.innerHTML =
          '<div class="rcpt-detail__summary">' +
          '<p class="rcpt-detail__meta">' +
          escapeHtml(r.kind_label) +
          " · " +
          escapeHtml(r.status_label) +
          " · " +
          escapeHtml(r.created_label) +
          "</p>" +
          "<p>" +
          escapeHtml(r.client_name || "Walk-in") +
          (r.client_phone
            ? " · " + escapeHtml(r.client_phone)
            : "") +
          "</p>" +
          "<p>" +
          escapeHtml(r.payment_label) +
          (r.cashier ? " · " + escapeHtml(r.cashier) : "") +
          "</p>" +
          "<strong>" +
          money(r.total) +
          "</strong>" +
          "</div>" +
          '<p class="rcpt-detail__section-title">Items</p>' +
          '<ul class="rcpt-lines">' +
          (lines || "<li><span>No lines</span><span></span></li>") +
          "</ul>";
        els.detail.hidden = false;
        renderList();
        refreshIcons();
      })
      .catch(function (err) {
        showToast(err.message);
      });
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
        Accept: "application/json",
      },
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!data.ok) throw new Error(data.error || "Request failed");
        return data;
      });
    });
  }

  function openCodeModal(action) {
    state.pendingAction = action;
    els.codeTitle.textContent =
      action === "confirm" ? "Confirm receipts" : "Cancel receipts";
    els.codeHint.textContent =
      action === "confirm"
        ? "Enter a shop member’s staff code to confirm the selected pending sales."
        : "Enter a shop member’s staff code to cancel and restock the selected pending sales.";
    els.codeInput.value = "";
    els.codeModal.hidden = false;
    refreshIcons();
    els.codeInput.focus();
  }

  function runPendingAction(code) {
    var action = state.pendingAction;
    var ids = selectedIds();
    if (!action || !ids.length) return;
    var url = action === "confirm" ? urls.confirm : urls.cancel;
    postJSON(url, { ids: ids, staff_code: code })
      .then(function (data) {
        showToast(data.message || "Done");
        state.selected = {};
        els.codeModal.hidden = true;
        loadList();
        els.detail.hidden = true;
      })
      .catch(function (err) {
        showToast(err.message);
      });
  }

  function openReturnModal() {
    var rows = selectedRows().filter(function (r) {
      return r.can_return;
    });
    if (!rows.length) {
      showToast("No returnable receipts selected.");
      return;
    }
    els.returnReason.value = "";
    els.returnCode.value = "";
    els.returnBody.innerHTML = "<p class=\"rcpt-empty\">Loading lines…</p>";
    els.returnModal.hidden = false;

    Promise.all(
      rows.map(function (r) {
        return fetch(detailUrl(r.id), {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        }).then(function (res) {
          return res.json();
        });
      })
    )
      .then(function (payloads) {
        state.returnDetails = [];
        var html = "";
        payloads.forEach(function (data) {
          if (!data.ok || !data.receipt) return;
          var r = data.receipt;
          state.returnDetails.push(r);
          var lines = (r.returnable_lines || []).map(function (l) {
            var serialBlock = "";
            if (l.track_serial && (l.remaining_serial_numbers || []).length) {
              serialBlock =
                '<div class="rcpt-serials" data-return-serials="' +
                l.id +
                '">' +
                (l.remaining_serial_numbers || [])
                  .map(function (s) {
                    return (
                      '<label class="rcpt-serial"><input type="checkbox" value="' +
                      escapeHtml(s) +
                      '"> ' +
                      escapeHtml(s) +
                      "</label>"
                    );
                  })
                  .join("") +
                "</div>";
            }
            return (
              '<div class="rcpt-return-line" data-receipt-id="' +
              r.id +
              '" data-line-id="' +
              l.id +
              '" data-track-serial="' +
              (l.track_serial ? "1" : "0") +
              '" data-max="' +
              escapeHtml(l.remaining_quantity) +
              '">' +
              "<label><input type=\"checkbox\" data-return-pick checked> " +
              escapeHtml(l.item_name) +
              "</label>" +
              '<input type="number" min="0" step="0.001" max="' +
              escapeHtml(l.remaining_quantity) +
              '" value="' +
              escapeHtml(l.remaining_quantity) +
              '" data-return-qty ' +
              (l.track_serial ? "hidden" : "") +
              ">" +
              serialBlock +
              "</div>"
            );
          });
          if (!lines.length) {
            html +=
              '<section class="rcpt-return-group"><h4>' +
              escapeHtml(r.receipt_number) +
              '</h4><p class="rcpt-empty">Nothing left to return.</p></section>';
          } else {
            html +=
              '<section class="rcpt-return-group"><h4>' +
              escapeHtml(r.receipt_number) +
              "</h4>" +
              lines.join("") +
              "</section>";
          }
        });
        els.returnBody.innerHTML =
          html || '<p class="rcpt-empty">Nothing to return.</p>';
      })
      .catch(function (err) {
        showToast(err.message);
        els.returnModal.hidden = true;
      });
  }

  function submitReturn() {
    var reason = (els.returnReason.value || "").trim();
    var code = (els.returnCode.value || "").trim();
    if (!reason) {
      showToast("Enter a return reason.");
      return;
    }
    if (!code) {
      showToast("Enter a staff code.");
      return;
    }
    var byReceipt = {};
    els.returnBody.querySelectorAll(".rcpt-return-line").forEach(function (row) {
      var pick = row.querySelector("[data-return-pick]");
      if (!pick || !pick.checked) return;
      var rid = Number(row.dataset.receiptId);
      var lid = Number(row.dataset.lineId);
      var track = row.dataset.trackSerial === "1";
      var entry = { line_id: lid };
      if (track) {
        var serials = [];
        row.querySelectorAll("[data-return-serials] input:checked").forEach(
          function (cb) {
            serials.push(cb.value);
          }
        );
        if (!serials.length) return;
        entry.serial_numbers = serials;
        entry.quantity = String(serials.length);
      } else {
        var qtyEl = row.querySelector("[data-return-qty]");
        var qty = qtyEl ? qtyEl.value : "0";
        if (!Number(qty)) return;
        entry.quantity = qty;
      }
      if (!byReceipt[rid]) byReceipt[rid] = [];
      byReceipt[rid].push(entry);
    });
    var receipts = Object.keys(byReceipt).map(function (id) {
      return { id: Number(id), lines: byReceipt[id] };
    });
    if (!receipts.length) {
      showToast("Select at least one item to return.");
      return;
    }
    postJSON(urls.returnUrl, {
      staff_code: code,
      reason: reason,
      receipts: receipts,
    })
      .then(function (data) {
        showToast(data.message || "Returned");
        els.returnModal.hidden = true;
        state.selected = {};
        loadList();
        els.detail.hidden = true;
      })
      .catch(function (err) {
        showToast(err.message);
      });
  }

  // Events
  els.modes.forEach(function (btn) {
    btn.addEventListener("click", function () {
      state.mode = btn.dataset.periodMode;
      syncPeriodFields();
    });
  });
  els.go.addEventListener("click", loadList);
  els.q.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      loadList();
    }
  });

  els.list.addEventListener("change", function (e) {
    var cb = e.target.closest("[data-rcpt-check]");
    if (!cb) return;
    var id = Number(cb.value);
    if (cb.checked) state.selected[id] = true;
    else delete state.selected[id];
    var row = els.list.querySelector('[data-rcpt-id="' + id + '"]');
    if (row) row.classList.toggle("is-selected", !!state.selected[id]);
    syncActions();
    refreshIcons();
  });

  els.list.addEventListener("click", function (e) {
    var open = e.target.closest("[data-rcpt-open]");
    if (open) openDetail(Number(open.dataset.rcptOpen));
  });

  els.detailClose.addEventListener("click", function () {
    els.detail.hidden = true;
    state.activeId = null;
    renderList();
  });

  if (els.cartToggle) {
    els.cartToggle.addEventListener("click", function (e) {
      e.stopPropagation();
      if (els.actions.hidden) return;
      setCartOpen(els.cartPanel && els.cartPanel.hidden);
    });
  }

  document.addEventListener("click", function (e) {
    if (!els.actions || els.actions.hidden) return;
    if (!e.target.closest("[data-rcpt-actions]")) setCartOpen(false);
  });

  els.confirmBtn.addEventListener("click", function () {
    setCartOpen(false);
    openCodeModal("confirm");
  });
  els.cancelBtn.addEventListener("click", function () {
    setCartOpen(false);
    openCodeModal("cancel");
  });
  els.returnBtn.addEventListener("click", function () {
    setCartOpen(false);
    openReturnModal();
  });

  els.codeCancel.addEventListener("click", function () {
    els.codeModal.hidden = true;
  });
  els.codeOk.addEventListener("click", function () {
    runPendingAction((els.codeInput.value || "").trim());
  });
  els.codeInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      runPendingAction((els.codeInput.value || "").trim());
    }
  });

  root.querySelectorAll("[data-rcpt-return-close]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      els.returnModal.hidden = true;
    });
  });
  els.returnSubmit.addEventListener("click", submitReturn);

  // Theme toggle (same key as chat)
  var themeBtn = root.querySelector("[data-theme-toggle]");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var cur =
        document.documentElement.getAttribute("data-theme") === "dark"
          ? "light"
          : "dark";
      document.documentElement.setAttribute("data-theme", cur);
      try {
        localStorage.setItem("myshop-theme", cur);
      } catch (e) {}
    });
  }

  // Boot
  els.day.value = todayISO();
  els.from.value = todayISO();
  els.to.value = todayISO();
  els.month.value = monthISO();
  buildYears();
  syncPeriodFields();
  refreshIcons();
  loadList();
})();
