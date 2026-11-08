(() => {
  const root = document.querySelector("[data-ax-confirm-receipts]");
  if (!root) return;

  const detailUrlTemplate = root.getAttribute("data-receipt-detail-url") || "";
  const confirmUrlTemplate = root.getAttribute("data-receipt-confirm-url") || "";
  const cancelUrlTemplate = root.getAttribute("data-receipt-cancel-url") || "";
  const rowsUrl = root.getAttribute("data-receipt-rows-url") || "";
  const asyncRows = root.getAttribute("data-async-rows") === "1";

  const card = root.querySelector("[data-ax-confirm-card]");
  const liveSearch = card?.querySelector("[data-ax-confirm-live-search]");
  const selectBar = card?.querySelector("[data-ax-confirm-select-bar]");
  const selectCount = card?.querySelector("[data-ax-confirm-select-count]");
  const selectAll = card?.querySelector("[data-ax-confirm-select-all]");
  const clearSelectedBtn = card?.querySelector("[data-ax-confirm-clear-selected]");
  const confirmSelectedBtn = card?.querySelector("[data-ax-confirm-confirm-selected]");
  const cancelSelectedBtn = card?.querySelector("[data-ax-confirm-cancel-selected]");
  const table = card?.querySelector("[data-ax-confirm-table]");
  const tbody = card?.querySelector("[data-ax-confirm-rows]");
  const loadingEl = card?.querySelector("[data-ax-confirm-loading]");
  const emptyEl = card?.querySelector("[data-ax-confirm-empty]");
  const noResults = card?.querySelector("[data-ax-confirm-no-results]");
  const errorEl = card?.querySelector("[data-ax-confirm-error]");
  const footnote = root.querySelector("[data-ax-confirm-footnote]");
  const periodEl = root.querySelector("[data-ax-confirm-period]");
  const filtersForm = root.querySelector("[data-analytics-filters]");

  const modal = document.querySelector("[data-confirm-receipt-modal]");
  const modalBody = modal?.querySelector("[data-confirm-receipt-modal-body]");
  const modalTitle = modal?.querySelector("[data-confirm-receipt-modal-title]");
  const modalActions = modal?.querySelector("[data-confirm-receipt-modal-actions]");
  const confirmBtn = modal?.querySelector("[data-confirm-receipt-confirm]");
  const cancelBtn = modal?.querySelector("[data-confirm-receipt-cancel]");

  let detailSeq = 0;
  let rowsSeq = 0;
  let currentDetail = null;
  let currentShopId = null;
  let busy = false;
  let searchTimer = 0;
  let countLabel = "receipt";

  const getCsrf = () =>
    root.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const money = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value ?? "0");
    return Math.round(n).toLocaleString(undefined, {
      maximumFractionDigits: 0,
    });
  };

  const refreshIcons = () => {
    if (typeof window.lucide?.createIcons === "function") {
      window.lucide.createIcons();
    }
  };

  const urlFor = (template, shopId, receiptId) => {
    let seen = 0;
    return String(template || "").replace(/\/0(?=\/|$)/g, () => {
      seen += 1;
      if (seen === 1) return `/${shopId}`;
      if (seen === 2) return `/${receiptId}`;
      return "/0";
    });
  };

  const statusClass = (status) => {
    if (status === "cancelled") return "is-cancelled";
    if (status === "partial_return") return "is-partial";
    if (status === "confirmed") return "is-confirmed";
    return "is-pending";
  };

  const pluralize = (count, label) =>
    `${count} ${label}${count === 1 ? "" : "s"}`;

  const allRows = () =>
    [...(tbody?.querySelectorAll(".ax-receipt-row[data-receipt-id]") || [])];

  const rowCheckbox = (row) => row.querySelector("[data-ax-confirm-select-row]");

  const isActionable = (row) =>
    row.getAttribute("data-can-confirm") === "1" ||
    row.getAttribute("data-can-cancel") === "1";

  const selectedRows = () =>
    allRows().filter((row) => Boolean(rowCheckbox(row)?.checked));

  const setListState = ({ loading = false, error = false, empty = false } = {}) => {
    if (loadingEl) loadingEl.hidden = !loading;
    if (errorEl) errorEl.hidden = !error;
    if (emptyEl) emptyEl.hidden = !empty;
    if (noResults) noResults.hidden = true;
    if (table) table.hidden = loading || error || empty;
  };

  const syncSelectionBar = () => {
    const selected = selectedRows();
    const count = selected.length;
    const canConfirm = selected.some(
      (row) => row.getAttribute("data-can-confirm") === "1"
    );
    const canCancel = selected.some(
      (row) => row.getAttribute("data-can-cancel") === "1"
    );

    if (selectBar) selectBar.hidden = count === 0;
    if (selectCount) selectCount.textContent = `${count} selected`;
    if (clearSelectedBtn) clearSelectedBtn.disabled = busy || count === 0;
    if (confirmSelectedBtn) confirmSelectedBtn.disabled = busy || !canConfirm;
    if (cancelSelectedBtn) cancelSelectedBtn.disabled = busy || !canCancel;

    allRows().forEach((row) => {
      row.classList.toggle("is-selected", Boolean(rowCheckbox(row)?.checked));
    });

    if (selectAll) {
      const actionable = allRows().filter(
        (row) => isActionable(row) && !row.hidden
      );
      const checked = actionable.filter((row) => rowCheckbox(row)?.checked);
      selectAll.disabled = actionable.length === 0;
      selectAll.checked =
        actionable.length > 0 && checked.length === actionable.length;
      selectAll.indeterminate =
        checked.length > 0 && checked.length < actionable.length;
    }
  };

  const clearLiveSearch = ({ focus = true } = {}) => {
    if (!liveSearch) return;
    if (!liveSearch.value) {
      if (focus) liveSearch.focus();
      return;
    }
    liveSearch.value = "";
    filterRows();
    if (focus) liveSearch.focus();
  };

  const filterRows = () => {
    const query = (liveSearch?.value || "").trim().toLowerCase();
    const tokens = query.split(/\s+/).filter(Boolean);
    let visible = 0;
    const rows = allRows();
    rows.forEach((row) => {
      const haystack = (
        row.getAttribute("data-search-text") ||
        row.textContent ||
        ""
      ).toLowerCase();
      const match =
        !tokens.length || tokens.every((token) => haystack.includes(token));
      row.hidden = !match;
      if (match) visible += 1;
    });
    const hasRows = rows.length > 0;
    if (noResults) noResults.hidden = !hasRows || visible > 0 || !query;
    if (table) table.hidden = !hasRows || (visible === 0 && Boolean(query));
    if (emptyEl) emptyEl.hidden = hasRows || Boolean(query);
    syncSelectionBar();
  };

  const clearSelection = () => {
    allRows().forEach((row) => {
      const input = rowCheckbox(row);
      if (input) input.checked = false;
    });
    syncSelectionBar();
  };

  const visibleActionableRows = () =>
    allRows().filter((row) => !row.hidden && isActionable(row));

  const pickBestSearchMatch = () => {
    const query = (liveSearch?.value || "").trim().toLowerCase();
    if (!query) return null;
    const candidates = visibleActionableRows();
    if (!candidates.length) return null;
    const exact = candidates.find(
      (row) =>
        (row.getAttribute("data-receipt-number") || "").toLowerCase() === query
    );
    if (exact) return exact;
    if (candidates.length === 1) return candidates[0];
    return null;
  };

  const selectRow = (row, { clearSearch = true } = {}) => {
    const input = rowCheckbox(row);
    if (!input || input.disabled) return false;
    input.checked = true;
    syncSelectionBar();
    if (clearSearch && liveSearch?.value.trim()) {
      clearLiveSearch({ focus: true });
    }
    return true;
  };

  const renderRowHtml = (row) => {
    const actionable = Boolean(row.can_confirm || row.can_cancel);
    const number = escapeHtml(row.number || "");
    const status = escapeHtml(row.status || "");
    const statusLabel = escapeHtml(row.status_label || "");
    const when = escapeHtml(row.when || "");
    const searchText = escapeHtml(row.search_text || row.number || "");
    const selectCell = actionable
      ? `<td class="ax-col-select" data-label="Select">
  <label class="ax-select-row">
    <span class="visually-hidden">Select ${number}</span>
    <input type="checkbox" data-ax-confirm-select-row aria-label="Select ${number}">
  </label>
</td>`
      : `<td class="ax-col-select" data-label="Select"><span class="ax-select-na" aria-hidden="true">—</span></td>`;

    return `<tr
  class="ax-receipt-row${actionable ? " is-selectable" : ""}"
  data-receipt-id="${Number(row.receipt_id) || 0}"
  data-shop-id="${Number(row.shop_id) || 0}"
  data-receipt-source="${escapeHtml(row.source || "pos")}"
  data-receipt-number="${number}"
  data-can-confirm="${row.can_confirm ? "1" : "0"}"
  data-can-cancel="${row.can_cancel ? "1" : "0"}"
  data-search-text="${searchText}"
  tabindex="0"
>
  ${selectCell}
  <td class="ax-cell--primary" data-label="Receipt">${number}</td>
  <td data-label="Status">
    <span class="shop-receipt-status ${statusClass(status)}">${statusLabel}</span>
  </td>
  <td class="ax-confirm-when" data-label="When">${when}</td>
</tr>`;
  };

  const updateFootnote = (total) => {
    if (!footnote) return;
    footnote.textContent = `Showing ${pluralize(
      total,
      countLabel
    )} · pending first, then partial return, cancelled, confirmed`;
  };

  const renderRows = (rows) => {
    if (!tbody) return;
    tbody.innerHTML = rows.map(renderRowHtml).join("");
    const total = rows.length;
    updateFootnote(total);
    if (!total) {
      setListState({ empty: true });
      syncSelectionBar();
      return;
    }
    setListState({});
    filterRows();
  };

  const rowsQueryUrl = () => {
    if (!rowsUrl) return "";
    const params = new URLSearchParams(
      filtersForm ? new FormData(filtersForm) : window.location.search
    );
    // Keep status default aligned with server (pending) when omitted.
    if (!params.has("status")) params.set("status", "pending");
    if (!params.has("kind")) params.set("kind", "sale");
    const qs = params.toString();
    return qs ? `${rowsUrl}?${qs}` : rowsUrl;
  };

  const loadRows = async () => {
    if (!rowsUrl) {
      setListState({ empty: allRows().length === 0 });
      syncSelectionBar();
      return;
    }
    const seq = ++rowsSeq;
    setListState({ loading: true });
    if (selectAll) {
      selectAll.checked = false;
      selectAll.indeterminate = false;
      selectAll.disabled = true;
    }
    try {
      const res = await fetch(rowsQueryUrl(), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await res.json().catch(() => ({}));
      if (seq !== rowsSeq) return;
      if (!res.ok || !data.ok) {
        setListState({ error: true });
        if (footnote) footnote.textContent = "Could not load receipts.";
        return;
      }
      countLabel = data.count_label || "receipt";
      if (periodEl && data.report_period_label) {
        periodEl.textContent = data.report_period_label;
      }
      renderRows(Array.isArray(data.rows) ? data.rows : []);
    } catch (_err) {
      if (seq !== rowsSeq) return;
      setListState({ error: true });
      if (footnote) footnote.textContent = "Could not load receipts.";
    }
  };

  const closeModal = () => {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("workspace-modal-open");
    currentDetail = null;
    currentShopId = null;
    busy = false;
  };

  const openModal = () => {
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("workspace-modal-open");
    refreshIcons();
  };

  const setActionBusy = (on) => {
    busy = Boolean(on);
    if (confirmBtn) confirmBtn.disabled = busy || !currentDetail?.receipt?.can_confirm;
    if (cancelBtn) cancelBtn.disabled = busy || !currentDetail?.receipt?.can_cancel;
    syncSelectionBar();
  };

  const renderDetail = (payload) => {
    const receipt = payload.receipt || {};
    currentDetail = payload;
    if (modalTitle) {
      modalTitle.textContent = receipt.receipt_number || "Receipt";
    }
    if (modalActions) modalActions.hidden = false;
    if (confirmBtn) {
      confirmBtn.hidden = !receipt.can_confirm;
      confirmBtn.disabled = !receipt.can_confirm;
    }
    if (cancelBtn) {
      cancelBtn.hidden = !receipt.can_cancel;
      cancelBtn.disabled = !receipt.can_cancel;
    }

    const lines = receipt.lines || [];
    const itemsHtml = lines
      .map((line) => {
        const serials = (line.serial_numbers || [])
          .map((s) => `<code>${escapeHtml(s)}</code>`)
          .join(" ");
        const returnedNote =
          line.returned_quantity > 0
            ? `<span class="shop-receipt-muted">Returned ${line.returned_quantity} of ${line.quantity}</span>`
            : "";
        return `<tr>
  <td class="shop-receipt-item-primary" data-label="Item">
    <strong>${escapeHtml(line.item_name)}</strong>
    ${returnedNote}
    ${serials ? `<div class="shop-receipt-serials">${serials}</div>` : ""}
  </td>
  <td data-label="Qty">${line.remaining_quantity}<span class="shop-receipt-muted"> / ${line.quantity}</span></td>
  <td data-label="Price">KSh ${escapeHtml(money(line.unit_price))}</td>
  <td data-label="Total">KSh ${escapeHtml(money(line.remaining_total))}</td>
</tr>`;
      })
      .join("");

    if (modalBody) {
      modalBody.innerHTML = `
<div class="shop-receipt-detail">
  <div class="shop-receipt-summary">
    <div class="shop-receipt-summary-badges">
      <span class="shop-receipt-kind shop-receipt-kind--${escapeHtml(
        (receipt.kind || "").replace(/\s+/g, "_")
      )}">${escapeHtml(receipt.kind_label || "")}</span>
      <span class="shop-receipt-status ${statusClass(
        receipt.status
      )}">${escapeHtml(receipt.status_label || "")}</span>
    </div>
    <p class="shop-receipt-summary-when"><strong>${escapeHtml(
      receipt.created_label || "—"
    )}</strong></p>
    <p class="shop-receipt-muted">Cashier: ${escapeHtml(
      receipt.cashier || "—"
    )}</p>
  </div>

  <div class="shop-receipt-card">
    <h3>Payment &amp; client</h3>
    <dl>
      <div><dt>Payment</dt><dd>${escapeHtml(
        receipt.payment_label || "—"
      )}</dd></div>
      <div><dt>Client</dt><dd>${escapeHtml(
        receipt.client_name || "Walk-in"
      )}</dd></div>
      <div><dt>Phone</dt><dd>${escapeHtml(
        receipt.client_phone || "—"
      )}</dd></div>
      <div><dt>Shop</dt><dd>${escapeHtml(receipt.shop_name || "—")}</dd></div>
      <div class="is-total"><dt>Total</dt><dd>KSh ${escapeHtml(
        money(receipt.total)
      )}</dd></div>
    </dl>
  </div>

  <div class="shop-receipt-card shop-receipt-card--items">
    <h3>Items</h3>
    ${
      lines.length
        ? `<div class="shop-receipt-items-wrap">
  <table class="shop-receipt-items">
    <thead>
      <tr>
        <th>Item</th>
        <th>Qty</th>
        <th>Price</th>
        <th>Total</th>
      </tr>
    </thead>
    <tbody>${itemsHtml}</tbody>
  </table>
</div>`
        : `<p class="shop-receipt-muted">No line items on this receipt.</p>`
    }
  </div>
</div>`;
    }
    refreshIcons();
  };

  const loadDetail = async (shopId, receiptId, source) => {
    if (!detailUrlTemplate || !shopId || !receiptId) return;
    const seq = ++detailSeq;
    currentShopId = shopId;
    if (modalTitle) modalTitle.textContent = "Receipt";
    if (modalActions) modalActions.hidden = true;
    if (modalBody) {
      modalBody.innerHTML = `<p class="shop-receipts-status">Loading receipt…</p>`;
    }
    openModal();
    try {
      const sourceKey = encodeURIComponent(source || "pos");
      const res = await fetch(
        `${urlFor(detailUrlTemplate, shopId, receiptId)}?source=${sourceKey}`,
        {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        }
      );
      const data = await res.json().catch(() => ({}));
      if (seq !== detailSeq) return;
      if (!res.ok || !data.ok) {
        if (modalBody) {
          modalBody.innerHTML = `<p class="shop-receipts-status is-error">${escapeHtml(
            data.error || "Could not load receipt."
          )}</p>`;
        }
        return;
      }
      renderDetail(data);
    } catch (_err) {
      if (seq !== detailSeq) return;
      if (modalBody) {
        modalBody.innerHTML = `<p class="shop-receipts-status is-error">Could not load receipt.</p>`;
      }
    }
  };

  const postAction = async (template, successFallback) => {
    if (busy || !currentDetail?.receipt?.id || !currentShopId || !template) return;
    if ((currentDetail?.receipt?.source || "pos") !== "pos") {
      window.alert("Only sales receipts can be confirmed or cancelled here.");
      return;
    }
    setActionBusy(true);
    try {
      const res = await fetch(
        urlFor(template, currentShopId, currentDetail.receipt.id),
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-CSRFToken": getCsrf(),
          },
          credentials: "same-origin",
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        window.alert(data.error || "Action failed.");
        setActionBusy(false);
        return;
      }
      closeModal();
      window.location.reload();
    } catch (_err) {
      window.alert(successFallback || "Action failed.");
      setActionBusy(false);
    }
  };

  const postReceiptAction = async (row, template) => {
    const receiptId = Number(row.getAttribute("data-receipt-id") || 0);
    const shopId = Number(row.getAttribute("data-shop-id") || 0);
    const source = row.getAttribute("data-receipt-source") || "pos";
    if (!receiptId || !shopId || !template) {
      return { ok: false, error: "Missing receipt." };
    }
    if (source !== "pos") {
      return {
        ok: false,
        error: "Only sales receipts can be confirmed or cancelled here.",
      };
    }
    try {
      const res = await fetch(urlFor(template, shopId, receiptId), {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": getCsrf(),
        },
        credentials: "same-origin",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        return {
          ok: false,
          error: data.error || "Action failed.",
          number: row.getAttribute("data-receipt-number") || String(receiptId),
        };
      }
      return { ok: true };
    } catch (_err) {
      return {
        ok: false,
        error: "Action failed.",
        number: row.getAttribute("data-receipt-number") || String(receiptId),
      };
    }
  };

  const runBulkAction = async (mode) => {
    if (busy) return;
    const selected = selectedRows();
    const targets =
      mode === "confirm"
        ? selected.filter((row) => row.getAttribute("data-can-confirm") === "1")
        : selected.filter((row) => row.getAttribute("data-can-cancel") === "1");
    if (!targets.length) return;

    if (mode === "cancel") {
      const label =
        targets.length === 1
          ? targets[0].getAttribute("data-receipt-number") || "this receipt"
          : `${targets.length} receipts`;
      if (
        !window.confirm(
          `Cancel ${label}? Sale and credit items will be restocked.`
        )
      ) {
        return;
      }
    }

    const template =
      mode === "confirm" ? confirmUrlTemplate : cancelUrlTemplate;
    setActionBusy(true);
    const failures = [];
    for (const row of targets) {
      const result = await postReceiptAction(row, template);
      if (!result.ok) {
        failures.push(
          `${result.number || "Receipt"}: ${result.error || "failed"}`
        );
      }
    }
    if (failures.length) {
      window.alert(
        `${mode === "confirm" ? "Confirm" : "Cancel"} finished with errors:\n${failures.join(
          "\n"
        )}`
      );
    }
    window.location.reload();
  };

  const openRowDetail = (row) => {
    const receiptId = Number(row.getAttribute("data-receipt-id") || 0);
    const shopId = Number(row.getAttribute("data-shop-id") || 0);
    const source = row.getAttribute("data-receipt-source") || "pos";
    if (!receiptId || !shopId) return;
    loadDetail(shopId, receiptId, source);
  };

  card?.addEventListener("click", (event) => {
    const row = event.target.closest(".ax-receipt-row[data-receipt-id]");
    if (!row || !card.contains(row)) return;
    if (event.target.closest(".ax-col-select, .ax-select-row, input")) return;
    openRowDetail(row);
  });

  card?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest(".ax-receipt-row[data-receipt-id]");
    if (!row || !card.contains(row)) return;
    if (event.target.closest("input, label")) return;
    event.preventDefault();
    openRowDetail(row);
  });

  card?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;

    if (target.matches("[data-ax-confirm-select-all]")) {
      const checked = target.checked;
      visibleActionableRows().forEach((row) => {
        const input = rowCheckbox(row);
        if (input) input.checked = checked;
      });
      syncSelectionBar();
      if (checked && liveSearch?.value.trim()) {
        clearLiveSearch({ focus: true });
      }
      return;
    }

    if (target.matches("[data-ax-confirm-select-row]")) {
      syncSelectionBar();
      if (target.checked && liveSearch?.value.trim()) {
        clearLiveSearch({ focus: true });
      }
    }
  });

  liveSearch?.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(filterRows, 120);
  });
  liveSearch?.addEventListener("search", filterRows);
  liveSearch?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const match = pickBestSearchMatch();
    if (!match) return;
    selectRow(match, { clearSearch: true });
  });

  clearSelectedBtn?.addEventListener("click", () => {
    if (busy) return;
    clearSelection();
  });

  confirmSelectedBtn?.addEventListener("click", () => {
    runBulkAction("confirm");
  });

  cancelSelectedBtn?.addEventListener("click", () => {
    runBulkAction("cancel");
  });

  modal
    ?.querySelectorAll("[data-confirm-receipt-modal-close]")
    .forEach((el) => el.addEventListener("click", closeModal));

  confirmBtn?.addEventListener("click", () => {
    if (!currentDetail?.receipt?.can_confirm) return;
    postAction(confirmUrlTemplate, "Could not confirm receipt.");
  });

  cancelBtn?.addEventListener("click", () => {
    if (!currentDetail?.receipt?.can_cancel) return;
    const number = currentDetail?.receipt?.receipt_number || "this receipt";
    if (
      !window.confirm(
        `Cancel ${number}? Sale and credit items will be restocked.`
      )
    ) {
      return;
    }
    postAction(cancelUrlTemplate, "Could not cancel receipt.");
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal && !modal.hidden) {
      closeModal();
    }
  });

  if (asyncRows) {
    loadRows();
  } else {
    syncSelectionBar();
  }
})();
