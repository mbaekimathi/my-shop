/**
 * Online stock take — variance vs book qty frozen at count time + autosave.
 * One row per item with a count cell per shop; a shop filter narrows the
 * columns. Floating cart shows filled counts; polls other devices' updates.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-online-stock-take]");
  if (!root) return;

  const shopSelect = root.querySelector("[data-online-take-shop]");
  const dateInput = root.querySelector("[data-online-take-date]");
  const baseUrl = root.getAttribute("data-online-stock-take-url") || "";
  const list = root.querySelector("[data-online-take-list]");
  const searchInput = root.querySelector("[data-online-take-search]");
  const statusEl = root.querySelector("[data-online-take-save-status]");
  const liveStatusEl = root.querySelector("[data-online-take-live-status]");
  const filledEl = root.querySelector("[data-online-take-filled]");
  const matchEl = root.querySelector("[data-online-take-match]");
  const excessEl = root.querySelector("[data-online-take-excess]");
  const shortEl = root.querySelector("[data-online-take-short]");
  const progressEl = root.querySelector("[data-online-take-progress]");
  const emptySearchEl = root.querySelector("[data-online-take-empty-search]");
  const emptyTermEl = root.querySelector("[data-online-take-empty-term]");
  const shopFilter = root.getAttribute("data-selected-shop-id") || "";
  const isMerged = shopFilter === "";
  const countDate =
    root.getAttribute("data-online-count-date") ||
    dateInput?.value ||
    "";
  const csrfInput = root.querySelector("input[name=csrfmiddlewaretoken]");

  const buildPageUrl = ({ shopId: nextShopId, date: nextDate } = {}) => {
    const url = new URL(baseUrl, window.location.origin);
    const shopValue =
      nextShopId === undefined ? shopFilter : String(nextShopId || "").trim();
    const dateValue =
      nextDate === undefined
        ? countDate
        : String(nextDate || "").trim();
    if (shopValue) url.searchParams.set("shop_id", shopValue);
    else url.searchParams.delete("shop_id");
    if (dateValue) url.searchParams.set("date", dateValue);
    else url.searchParams.delete("date");
    return url;
  };
  const SAVE_DELAY_MS = 450;
  const POLL_MS = 3000;
  const pendingTimers = new Map();
  const lastSaved = new Map();
  /** Most-recently counted cell keys ("shop:item") first (cart ordering). */
  const recentFilledIds = [];
  let syncRevision = root.getAttribute("data-online-sync-revision") || "0:";
  let pollTimer = null;
  let pollInFlight = false;
  let savingCount = 0;
  let returnSearchAfterQty = false;

  // Keep the cart outside transformed workspace ancestors so fixed = viewport.
  let cart = document.querySelector("[data-online-take-cart]");
  if (cart && cart.parentElement !== document.body) {
    document.body.appendChild(cart);
  }
  const cartToggle = cart?.querySelector("[data-online-take-cart-toggle]");
  const cartPanel = cart?.querySelector("[data-online-take-cart-panel]");
  const cartClose = cart?.querySelector("[data-online-take-cart-close]");
  const cartBadge = cart?.querySelector("[data-online-take-cart-badge]");
  const cartHeading = cart?.querySelector("[data-online-take-cart-heading]");
  const cartLines = cart?.querySelector("[data-online-take-cart-lines]");
  const saveCountBtn = cart?.querySelector("[data-online-take-save-count]");
  const downloadLink = cart?.querySelector("[data-online-take-download]");
  const saveNote = cart?.querySelector("[data-online-take-save-note]");
  const savedLines = root.querySelector("[data-online-take-saved-lines]");

  const readCookie = (name) => {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) {
      return decodeURIComponent(parts.pop().split(";").shift() || "");
    }
    return "";
  };

  const csrf = csrfInput?.value || readCookie("csrftoken") || "";

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const setStatus = (message, { error = false } = {}) => {
    if (!statusEl) return;
    statusEl.textContent = message || "";
    statusEl.classList.toggle("is-error", Boolean(error));
  };

  const setLiveStatus = (message, { state = "live" } = {}) => {
    if (!liveStatusEl) return;
    liveStatusEl.innerHTML = `<span class="online-take-live-dot" aria-hidden="true"></span> ${escapeHtml(
      message
    )}`;
    liveStatusEl.classList.toggle("is-live", state === "live");
    liveStatusEl.classList.toggle("is-syncing", state === "syncing");
    liveStatusEl.classList.toggle("is-error", state === "error");
  };

  const cellKey = (shopId, itemId) => `${shopId}:${itemId}`;

  const splitCellKey = (key) => {
    const idx = String(key).indexOf(":");
    if (idx < 0) return [null, String(key)];
    return [String(key).slice(0, idx), String(key).slice(idx + 1)];
  };

  const findCell = (shopId, itemId) => {
    if (!list || itemId == null || itemId === "") return null;
    const row = list.querySelector(
      `[data-online-take-row][data-item-id="${CSS.escape(String(itemId))}"]`
    );
    if (!row) return null;
    if (shopId == null || shopId === "") {
      return row.querySelector("[data-online-take-cell]");
    }
    return row.querySelector(
      `[data-online-take-cell][data-shop-id="${CSS.escape(String(shopId))}"]`
    );
  };

  const findCellByKey = (key) => {
    const [shopId, itemId] = splitCellKey(key);
    return findCell(shopId, itemId);
  };

  const cellItemId = (cell) =>
    cell?.closest("[data-online-take-row]")?.getAttribute("data-item-id") || "";

  const keyForCell = (cell) =>
    cellKey(cell?.getAttribute("data-shop-id") || "", cellItemId(cell));

  const isCellDirty = (cell) => {
    const key = keyForCell(cell);
    const input = cell?.querySelector("[data-online-take-qty]");
    if (!input || !key) return false;
    if (pendingTimers.has(key)) return true;
    if (document.activeElement === input) return true;
    return input.value.trim() !== String(lastSaved.get(key) ?? "");
  };

  const flashRemoteCell = (cell) => {
    if (!cell) return;
    cell.classList.add("is-remote-flash");
    window.setTimeout(() => cell.classList.remove("is-remote-flash"), 1200);
  };

  const applySystemQty = (cell, systemQty, liveSystemQty) => {
    if (!cell) return;
    if (liveSystemQty != null && liveSystemQty !== "") {
      cell.setAttribute("data-live-system-qty", String(liveSystemQty));
    }
    if (systemQty != null && systemQty !== "") {
      cell.setAttribute("data-system-qty", String(systemQty));
      const systemEl = cell.querySelector("[data-online-take-system]");
      if (systemEl) systemEl.textContent = String(systemQty);
    }
  };

  const restoreLiveSystemQty = (cell) => {
    const live = cell?.getAttribute("data-live-system-qty");
    if (live == null || live === "") return;
    applySystemQty(cell, live, live);
  };

  const applyRemoteLine = (line) => {
    const shopId = line?.shop_id ?? "";
    const itemId = String(line?.item_id ?? "");
    const cell = findCell(shopId, itemId);
    if (!cell || isCellDirty(cell)) return false;
    const input = cell.querySelector("[data-online-take-qty]");
    if (!input) return false;

    const key = cellKey(shopId, itemId);
    const nextQty = String(line.physical_qty ?? "").trim();
    const nextBy = String(line.updated_by_label || "").trim();
    const prevQty = String(lastSaved.get(key) ?? "");
    const prevBy =
      cell.querySelector("[data-online-take-by]")?.textContent?.trim() || "";
    const same =
      prevQty === nextQty &&
      (prevBy === nextBy || (!nextBy && (prevBy === "—" || !prevBy)));

    applySystemQty(cell, line.system_qty, line.live_system_qty);
    const row = cell.closest("[data-online-take-row]");
    if (line.category && row) {
      row.setAttribute("data-category-name", line.category);
    }

    if (!same) {
      input.value = nextQty;
      lastSaved.set(key, nextQty);
      syncCell(cell);
      applyCountedBy(cell, nextQty ? nextBy : "");
      touchFilledOrder(key, nextQty !== "");
      flashRemoteCell(cell);
      return true;
    }

    syncCell(cell);
    return false;
  };

  const clearRemoteLine = (key) => {
    const cell = findCellByKey(key);
    if (!cell || isCellDirty(cell)) return false;
    const input = cell.querySelector("[data-online-take-qty]");
    if (!input) return false;
    const prevQty = String(lastSaved.get(key) ?? "");
    if (!prevQty && !input.value.trim()) return false;
    input.value = "";
    lastSaved.set(key, "");
    restoreLiveSystemQty(cell);
    syncCell(cell);
    applyCountedBy(cell, "");
    touchFilledOrder(key, false);
    flashRemoteCell(cell);
    return true;
  };

  const applySystemMap = (systemMap) => {
    if (!systemMap || typeof systemMap !== "object" || !list) return false;
    let changed = false;
    Object.entries(systemMap).forEach(([key, qty]) => {
      const cell = findCellByKey(key);
      if (!cell || isCellDirty(cell)) return;
      const input = cell.querySelector("[data-online-take-qty]");
      if (!input) return;
      const liveQty = String(qty ?? "");
      if (input.value.trim() !== "") {
        cell.setAttribute("data-live-system-qty", liveQty);
        return;
      }
      if ((cell.getAttribute("data-system-qty") || "") === liveQty) return;
      applySystemQty(cell, liveQty, liveQty);
      syncCell(cell);
      changed = true;
    });
    return changed;
  };

  const applySyncPayload = (data, { fromPoll = false } = {}) => {
    if (!data?.ok || !list) return;
    const revision = String(data.revision || "");
    let changed = applySystemMap(data.system);
    if (revision && revision === syncRevision && fromPoll) {
      if (changed) syncSummary();
      return;
    }

    const lines = Array.isArray(data.lines) ? data.lines : null;

    if (lines) {
      const remoteIds = new Set(
        lines
          .map((line) => cellKey(line.shop_id ?? "", line.item_id))
          .filter((key) => key !== ":")
      );
      lines.forEach((line) => {
        if (applyRemoteLine(line)) changed = true;
      });
      lastSaved.forEach((qty, key) => {
        if (!qty || remoteIds.has(String(key))) return;
        if (clearRemoteLine(key)) changed = true;
      });
    }

    if (revision) syncRevision = revision;
    if (changed) syncSummary();
  };

  const piecesWord = (absDiff) => (absDiff === 1 ? "piece" : "pieces");

  const formatQty = (value) => {
    if (!Number.isFinite(value)) return "";
    const rounded = Math.round(value * 1000) / 1000;
    const text = String(rounded);
    if (!text.includes(".")) return text;
    return text.replace(/\.?0+$/, "");
  };

  const varianceFor = (countedRaw, systemRaw) => {
    if (countedRaw === "" || countedRaw == null) {
      return { kind: "empty", label: "—", delta: "" };
    }
    const counted = Number(countedRaw);
    const system = Number(systemRaw);
    if (!Number.isFinite(counted) || !Number.isFinite(system)) {
      return { kind: "empty", label: "—", delta: "" };
    }
    const diff = counted - system;
    const abs = Math.abs(diff);
    if (diff === 0) {
      return { kind: "match", label: "Match", delta: "0" };
    }
    if (diff > 0) {
      return {
        kind: "excess",
        label: `Excess by ${formatQty(abs)} ${piecesWord(abs)}`,
        delta: formatQty(diff),
      };
    }
    return {
      kind: "short",
      label: `Short by ${formatQty(abs)} ${piecesWord(abs)}`,
      delta: formatQty(diff),
    };
  };

  const applyVariance = (cell, result) => {
    const badge = cell.querySelector("[data-online-take-variance]");
    if (!badge) return;
    badge.classList.remove("is-empty", "is-match", "is-excess", "is-short");
    badge.classList.add(`is-${result.kind}`);
    cell.classList.remove("is-match", "is-excess", "is-short");
    if (result.kind !== "empty") {
      cell.classList.add(`is-${result.kind}`);
    }
    if (result.kind === "empty") {
      badge.textContent = "—";
    } else if (result.kind === "match") {
      badge.innerHTML = `<strong>${result.label}</strong>`;
    } else {
      badge.innerHTML = `<strong>${result.label}</strong> <span>(${result.delta})</span>`;
    }
  };

  const applyCountedBy = (cell, label) => {
    const byEl = cell.querySelector("[data-online-take-by]");
    if (!byEl) return;
    const name = String(label || "").trim();
    byEl.textContent = name || "—";
    if (name) {
      byEl.removeAttribute("data-empty");
    } else {
      byEl.setAttribute("data-empty", "");
    }
    const row = cell.closest("[data-online-take-row]");
    if (!row) return;
    const itemName =
      row.querySelector(".online-take-item strong")?.textContent?.trim() || "";
    const category = row.getAttribute("data-category-name") || "";
    const countedNames = [
      ...row.querySelectorAll("[data-online-take-by]"),
    ]
      .map((el) => el.textContent?.trim() || "")
      .filter((text) => text && text !== "—");
    row.setAttribute(
      "data-search",
      `${itemName} ${category} ${countedNames.join(" ")}`
        .trim()
        .toLowerCase()
    );
  };

  const syncRowClasses = (row) => {
    if (!row) return;
    row.classList.remove("is-filled", "is-match", "is-excess", "is-short");
    const filledCells = [
      ...row.querySelectorAll("[data-online-take-cell]"),
    ].filter(
      (cell) =>
        (cell.querySelector("[data-online-take-qty]")?.value.trim() || "") !==
        ""
    );
    if (!filledCells.length) return;
    row.classList.add("is-filled");
    if (filledCells.length === 1) {
      const kind = ["match", "excess", "short"].find((k) =>
        filledCells[0].classList.contains(`is-${k}`)
      );
      if (kind) row.classList.add(`is-${kind}`);
    }
  };

  const syncCell = (cell) => {
    const input = cell.querySelector("[data-online-take-qty]");
    if (!input) return;
    const result = varianceFor(
      input.value.trim(),
      cell.getAttribute("data-system-qty")
    );
    applyVariance(cell, result);
    cell.classList.toggle("is-filled", input.value.trim() !== "");
    if (!input.value.trim()) applyCountedBy(cell, "");
    syncRowClasses(cell.closest("[data-online-take-row]"));
  };

  const setCartOpen = (open) => {
    const isOpen = Boolean(open);
    cart?.classList.toggle("is-open", isOpen);
    if (cartPanel) cartPanel.hidden = !isOpen;
    if (cartToggle) {
      cartToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
      cartToggle.classList.toggle("is-open", isOpen);
      cartToggle.title = isOpen ? "Hide counted items" : "Show counted items";
    }
  };

  const renderCart = (cells) => {
    if (!cartLines) return;
    if (!cells.length) {
      cartLines.innerHTML = `
        <li class="online-take-cart-empty" data-online-take-cart-empty>
          No counted items yet. Enter a physical count to add it here.
        </li>
      `;
      return;
    }

    const html = cells
      .map((cell) => {
        const key = keyForCell(cell);
        const row = cell.closest("[data-online-take-row]");
        const input = cell.querySelector("[data-online-take-qty]");
        const qty = input?.value.trim() || "";
        const name =
          row?.querySelector(".online-take-item strong")?.textContent?.trim() ||
          "Item";
        const shopName = cell.getAttribute("data-shop-name") || "";
        const category = row?.getAttribute("data-category-name") || "";
        const countedBy =
          cell.querySelector("[data-online-take-by]")?.textContent?.trim() ||
          "";
        const byLabel = countedBy && countedBy !== "—" ? countedBy : "";
        const result = varianceFor(qty, cell.getAttribute("data-system-qty"));
        return `
          <li
            class="online-take-cart-line is-${escapeHtml(result.kind)}"
            data-online-take-cart-line
            data-cell-key="${escapeHtml(key)}"
            data-shop-name="${escapeHtml(shopName)}"
          >
            <button type="button" data-online-take-cart-jump data-cell-key="${escapeHtml(key)}">
              <span class="online-take-cart-line-main">
                <strong>${escapeHtml(name)}</strong>
                ${
                  isMerged && shopName
                    ? `<em>${escapeHtml(shopName)}</em>`
                    : ""
                }
                ${category ? `<em>${escapeHtml(category)}</em>` : ""}
                ${byLabel ? `<em class="online-take-cart-by">By ${escapeHtml(byLabel)}</em>` : ""}
              </span>
              <span class="online-take-cart-line-meta">
                <b>${escapeHtml(qty)}</b>
                ${
                  result.kind !== "empty"
                    ? `<em>${escapeHtml(result.label)}</em>`
                    : ""
                }
              </span>
            </button>
          </li>
        `;
      })
      .join("");
    cartLines.innerHTML = html;
  };

  const touchFilledOrder = (key, filled) => {
    if (!key) return;
    const idx = recentFilledIds.indexOf(key);
    if (idx >= 0) recentFilledIds.splice(idx, 1);
    if (filled) recentFilledIds.unshift(key);
  };

  const orderedFilledCells = (cellByKey) => {
    const ordered = [];
    const seen = new Set();
    recentFilledIds.forEach((key) => {
      const cell = cellByKey.get(key);
      if (!cell || seen.has(key)) return;
      ordered.push(cell);
      seen.add(key);
    });
    cellByKey.forEach((cell, key) => {
      if (seen.has(key)) return;
      ordered.push(cell);
      seen.add(key);
    });
    return ordered;
  };

  const syncSummary = () => {
    if (!list) return;
    const cells = [...list.querySelectorAll("[data-online-take-cell]")];
    let filled = 0;
    let match = 0;
    let excess = 0;
    let short = 0;
    const cellByKey = new Map();
    const liveIds = new Set();
    cells.forEach((cell) => {
      const input = cell.querySelector("[data-online-take-qty]");
      const value = input?.value.trim() || "";
      const key = keyForCell(cell);
      if (!value) return;
      filled += 1;
      if (key) {
        liveIds.add(key);
        cellByKey.set(key, cell);
      }
      const result = varianceFor(value, cell.getAttribute("data-system-qty"));
      if (result.kind === "match") match += 1;
      if (result.kind === "excess") excess += 1;
      if (result.kind === "short") short += 1;
    });
    for (let i = recentFilledIds.length - 1; i >= 0; i -= 1) {
      if (!liveIds.has(recentFilledIds[i])) recentFilledIds.splice(i, 1);
    }
    if (filledEl) filledEl.textContent = String(filled);
    if (matchEl) matchEl.textContent = String(match);
    if (excessEl) excessEl.textContent = String(excess);
    if (shortEl) shortEl.textContent = String(short);
    if (progressEl) {
      const total = cells.length;
      progressEl.style.width = total
        ? `${Math.min(100, Math.round((filled / total) * 100))}%`
        : "0%";
    }

    if (cartBadge) {
      cartBadge.textContent = String(filled);
      cartBadge.hidden = filled === 0;
    }
    if (cartHeading) {
      cartHeading.textContent = `${filled} item${filled === 1 ? "" : "s"}`;
    }
    renderCart(orderedFilledCells(cellByKey));
    if (filled === 0) setCartOpen(false);
  };

  const saveCell = async (cell) => {
    const input = cell.querySelector("[data-online-take-qty]");
    const shopId = cell.getAttribute("data-shop-id") || "";
    const itemId = cellItemId(cell);
    const key = cellKey(shopId, itemId);
    if (!input || !itemId || !shopId || !baseUrl) return;
    const qty = input.value.trim();
    if (lastSaved.get(key) === qty) {
      syncSummary();
      return;
    }

    setStatus("Saving…");
    savingCount += 1;
    cell.classList.add("is-saving");
    const body = new URLSearchParams({
      mode: "online-stock-take",
      shop_id: shopId,
      item_id: itemId,
      qty,
      csrfmiddlewaretoken: csrf,
    });
    if (countDate) body.set("date", countDate);
    try {
      const response = await fetch(baseUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrf,
        },
        body,
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not save this count.");
      }
      lastSaved.set(key, qty);
      if (qty === "") {
        applySystemQty(
          cell,
          data.line?.system_qty ?? data.line?.live_system_qty,
          data.line?.live_system_qty
        );
        if (
          data.line?.system_qty == null &&
          data.line?.live_system_qty == null
        ) {
          restoreLiveSystemQty(cell);
        }
      } else {
        applySystemQty(
          cell,
          data.line?.system_qty,
          data.line?.live_system_qty
        );
      }
      const row = cell.closest("[data-online-take-row]");
      if (data.line?.category && row) {
        row.setAttribute("data-category-name", data.line.category);
      }
      syncCell(cell);
      applyCountedBy(
        cell,
        qty !== "" ? data.line?.updated_by_label || "" : ""
      );
      touchFilledOrder(key, qty !== "");
      // Only a single-shop sheet can adopt the per-shop revision; the merged
      // page re-syncs against the multi-shop revision on the next poll.
      if (!isMerged && data.line?.revision) {
        syncRevision = String(data.line.revision);
      }
      syncSummary();
      setStatus(`Saved ${data.line?.saved_at || ""}`.trim());
    } catch (error) {
      setStatus(error?.message || "Could not save this count.", { error: true });
    } finally {
      savingCount = Math.max(0, savingCount - 1);
      cell.classList.remove("is-saving");
    }
  };

  /** Push every debounced/typed count to the server before snapshot actions. */
  const flushPendingSaves = async () => {
    pendingTimers.forEach((timer) => window.clearTimeout(timer));
    pendingTimers.clear();
    if (!list) return;
    const cells = [...list.querySelectorAll("[data-online-take-cell]")];
    for (const cell of cells) {
      const input = cell.querySelector("[data-online-take-qty]");
      const key = keyForCell(cell);
      if (!input || !key) continue;
      if (input.value.trim() !== String(lastSaved.get(key) ?? "")) {
        await saveCell(cell);
      }
    }
  };

  const setSaveNote = (message, { error = false } = {}) => {
    if (!saveNote) return;
    saveNote.hidden = !message;
    saveNote.textContent = message || "";
    saveNote.classList.toggle("is-error", Boolean(error));
  };

  const renderSavedRow = (saved) => {
    const savedId = encodeURIComponent(String(saved.id));
    const byLabel = String(saved.saved_by_label || "").trim();
    const li = document.createElement("li");
    li.className = "online-take-saved-row";
    li.setAttribute("data-saved-id", String(saved.id));
    li.innerHTML = `
      <span class="online-take-saved-main">
        <strong>${escapeHtml(saved.count_date_label || "")}</strong>
        <em>${escapeHtml(saved.shop_name || "")}${
      byLabel ? ` · by ${escapeHtml(byLabel)}` : ""
    }</em>
      </span>
      <span class="online-take-saved-stats">
        <span class="ost-chip is-count">${escapeHtml(
      saved.filled_count ?? 0
    )}/${escapeHtml(saved.item_count ?? 0)} counted</span>
        <span class="ost-chip is-match">${escapeHtml(
      saved.match_count ?? 0
    )} match</span>
        <span class="ost-chip is-excess">${escapeHtml(
      saved.excess_count ?? 0
    )} excess</span>
        <span class="ost-chip is-short">${escapeHtml(
      saved.short_count ?? 0
    )} short</span>
      </span>
      <span class="online-take-saved-when">${escapeHtml(
        saved.saved_at_label || ""
      )}</span>
      <span class="online-take-saved-actions">
        <a class="online-take-saved-link" href="${escapeHtml(
          `${baseUrl}&shop_id=${encodeURIComponent(
            String(saved.shop_id || "")
          )}&date=${encodeURIComponent(saved.count_date || "")}`
        )}">
          <i data-lucide="folder-open" aria-hidden="true"></i><span>Open &amp; update</span>
        </a>
        <a class="online-take-saved-link" href="${escapeHtml(
          `${baseUrl}&saved=${savedId}&download=1`
        )}" download>
          <i data-lucide="download" aria-hidden="true"></i><span>Download</span>
        </a>
        <a class="online-take-saved-link" href="${escapeHtml(
          `${baseUrl}&saved=${savedId}&paper=a4&auto=1`
        )}" target="_blank" rel="noopener">
          <i data-lucide="printer" aria-hidden="true"></i><span>Print</span>
        </a>
      </span>`;
    return li;
  };

  const saveCount = async () => {
    if (!baseUrl || !saveCountBtn) return;
    saveCountBtn.disabled = true;
    setSaveNote("");
    setStatus("Saving count…");
    try {
      await flushPendingSaves();
      const body = new URLSearchParams({
        mode: "online-stock-take",
        action: "save_count",
        csrfmiddlewaretoken: csrf,
      });
      if (shopFilter) body.set("shop_id", shopFilter);
      if (countDate) body.set("date", countDate);
      const response = await fetch(baseUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrf,
        },
        body,
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not save this count.");
      }
      const savedRows = Array.isArray(data.saved)
        ? data.saved
        : data.saved
        ? [data.saved]
        : [];
      const totalFilled = savedRows.reduce(
        (total, saved) => total + Number(saved?.filled_count || 0),
        0
      );
      const itemsLabel =
        totalFilled === 1 ? "1 item" : `${totalFilled} items`;
      setStatus(
        savedRows.length > 1
          ? `Count saved · ${savedRows.length} shops`
          : `Count saved · ${itemsLabel}`
      );
      setSaveNote(
        `Saved ${
          savedRows[0]?.saved_at_label || ""
        } — kept under Saved counts below.`.trim()
      );
      if (savedLines) {
        savedLines
          .querySelector("[data-online-take-saved-empty]")
          ?.remove();
        savedRows.forEach((saved) => {
          if (saved?.id == null) return;
          const row = renderSavedRow(saved);
          const existing = savedLines.querySelector(
            `[data-saved-id="${CSS.escape(String(saved.id))}"]`
          );
          if (existing) existing.replaceWith(row);
          else savedLines.prepend(row);
        });
        if (typeof window.lucide?.createIcons === "function") {
          window.lucide.createIcons();
        }
      }
    } catch (error) {
      const message = error?.message || "Could not save this count.";
      setStatus(message, { error: true });
      setSaveNote(message, { error: true });
    } finally {
      saveCountBtn.disabled = false;
    }
  };

  const stopPolling = () => {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const pollRemoteCounts = async () => {
    if (!baseUrl || !list || document.hidden || pollInFlight) return;
    pollInFlight = true;
    if (savingCount === 0) setLiveStatus("Syncing", { state: "syncing" });
    try {
      const url = buildPageUrl();
      url.searchParams.set("sync", "1");
      const response = await fetch(url.toString(), {
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
      });
      if (response.status === 401 || response.status === 403) {
        stopPolling();
        setLiveStatus("Offline", { state: "error" });
        try {
          const data = await response.clone().json();
          if (
            data?.login_url &&
            (data.error === "session_expired" ||
              data.error === "auth_required" ||
              String(data.error || "")
                .toLowerCase()
                .includes("session"))
          ) {
            window.location.replace(data.login_url);
          }
        } catch (_err) {
          /* ignore non-json */
        }
        return;
      }
      if (!response.ok) {
        setLiveStatus("Retrying", { state: "error" });
        return;
      }
      const data = await response.json().catch(() => ({}));
      if (!data?.ok) {
        setLiveStatus("Retrying", { state: "error" });
        return;
      }
      applySyncPayload(data, { fromPoll: true });
      if (savingCount === 0) setLiveStatus("Live", { state: "live" });
    } catch (_err) {
      setLiveStatus("Retrying", { state: "error" });
    } finally {
      pollInFlight = false;
    }
  };

  const queueSave = (cell) => {
    const key = keyForCell(cell);
    if (!key || key === ":") return;
    const existing = pendingTimers.get(key);
    if (existing) window.clearTimeout(existing);
    pendingTimers.set(
      key,
      window.setTimeout(() => {
        pendingTimers.delete(key);
        saveCell(cell);
      }, SAVE_DELAY_MS)
    );
  };

  const jumpToCell = (key) => {
    if (!key || !list) return;
    const cell = findCellByKey(key);
    if (!cell) return;
    revealCellInFullList(cell, { keepFocus: true });
  };

  shopSelect?.addEventListener("change", () => {
    if (!baseUrl) return;
    const nextShopId = (shopSelect.value || "").trim();
    window.location.assign(
      buildPageUrl({ shopId: nextShopId, date: countDate }).toString()
    );
  });

  dateInput?.addEventListener("change", () => {
    if (!baseUrl) return;
    const nextDate = (dateInput.value || "").trim();
    window.location.assign(
      buildPageUrl({ shopId: shopFilter, date: nextDate }).toString()
    );
  });

  const applySearchFilter = (rawQuery) => {
    const q = String(rawQuery || "")
      .trim()
      .toLowerCase();
    let visible = 0;
    list?.querySelectorAll("[data-online-take-row]").forEach((row) => {
      const hay = row.getAttribute("data-search") || "";
      const show = !q || hay.includes(q);
      row.hidden = !show;
      if (show) visible += 1;
    });
    if (emptySearchEl) {
      emptySearchEl.hidden = !(q && visible === 0);
      if (emptyTermEl) {
        emptyTermEl.textContent = String(rawQuery || "").trim();
      }
    }
    return q;
  };

  const revealCellInFullList = (cell, { keepFocus = true } = {}) => {
    if (!cell) return;
    const row = cell.closest("[data-online-take-row]");
    const input = cell.querySelector("[data-online-take-qty]");
    const hadSearch = Boolean((searchInput?.value || "").trim());
    if (hadSearch && searchInput) {
      searchInput.value = "";
      applySearchFilter("");
      returnSearchAfterQty = true;
    }
    if (row) {
      row.hidden = false;
      row.classList.add("is-cart-focus");
      window.setTimeout(
        () => row.classList.remove("is-cart-focus"),
        1400
      );
    }
    if (emptySearchEl) emptySearchEl.hidden = true;
    (row || cell).scrollIntoView({ behavior: "smooth", block: "center" });
    if (keepFocus && input) {
      input.focus({ preventScroll: true });
    }
  };

  searchInput?.addEventListener("input", () => {
    applySearchFilter(searchInput.value);
  });

  // Selecting a filtered qty field clears search and jumps to the item in-list.
  list?.addEventListener("focusin", (event) => {
    const input = event.target.closest("[data-online-take-qty]");
    if (!input) return;
    if (!(searchInput?.value || "").trim()) return;
    const cell = input.closest("[data-online-take-cell]");
    if (!cell) return;
    revealCellInFullList(cell, { keepFocus: true });
  });

  list?.addEventListener("input", (event) => {
    const input = event.target.closest("[data-online-take-qty]");
    if (!input) return;
    const cell = input.closest("[data-online-take-cell]");
    if (!cell) return;
    // Cover tap/type paths that skip focusin quirks on some mobile browsers.
    if ((searchInput?.value || "").trim()) {
      revealCellInFullList(cell, { keepFocus: true });
    }
    syncCell(cell);
    touchFilledOrder(keyForCell(cell), input.value.trim() !== "");
    syncSummary();
    queueSave(cell);
  });

  list?.addEventListener("change", (event) => {
    const input = event.target.closest("[data-online-take-qty]");
    if (!input) return;
    const cell = input.closest("[data-online-take-cell]");
    if (!cell) return;
    const key = keyForCell(cell);
    if (key && pendingTimers.has(key)) {
      window.clearTimeout(pendingTimers.get(key));
      pendingTimers.delete(key);
    }
    saveCell(cell);
  });

  list?.addEventListener("focusout", (event) => {
    const input = event.target.closest("[data-online-take-qty]");
    if (!input || !returnSearchAfterQty || !searchInput) return;
    const next = event.relatedTarget;
    if (next && list.contains(next) && next.matches?.("[data-online-take-qty]")) {
      return;
    }
    returnSearchAfterQty = false;
    window.setTimeout(() => {
      searchInput.focus({ preventScroll: true });
      searchInput.select?.();
    }, 0);
  });

  cartToggle?.addEventListener("click", () => {
    const open = cartToggle.getAttribute("aria-expanded") !== "true";
    setCartOpen(open);
  });

  cartClose?.addEventListener("click", () => setCartOpen(false));

  cartLines?.addEventListener("click", (event) => {
    const jump = event.target.closest("[data-online-take-cart-jump]");
    if (!jump) return;
    jumpToCell(jump.getAttribute("data-cell-key"));
  });

  saveCountBtn?.addEventListener("click", () => {
    saveCount();
  });

  downloadLink?.addEventListener("click", async (event) => {
    event.preventDefault();
    const href = downloadLink.getAttribute("href") || "";
    await flushPendingSaves();
    if (href) window.location.assign(href);
  });

  // Flush typed-but-unsaved counts before the print sheet is fetched.
  cart?.querySelectorAll('[data-modal-open="print-receipts"]').forEach((btn) => {
    btn.addEventListener("click", () => {
      flushPendingSaves();
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && cart?.classList.contains("is-open")) {
      setCartOpen(false);
    }
  });

  list?.querySelectorAll("[data-online-take-cell]").forEach((cell) => {
    const input = cell.querySelector("[data-online-take-qty]");
    const key = keyForCell(cell);
    const qty = input?.value.trim() || "";
    if (key) lastSaved.set(key, qty);
    if (qty) touchFilledOrder(key, true);
    syncCell(cell);
  });
  // Server already sorts filled_lines newest-first; seed that order on load.
  if (cartLines) {
    const seeded = [
      ...cartLines.querySelectorAll("[data-online-take-cart-line][data-cell-key]"),
    ]
      .map((line) => line.getAttribute("data-cell-key"))
      .filter(Boolean);
    if (seeded.length) {
      recentFilledIds.length = 0;
      recentFilledIds.push(...seeded);
    }
  }
  syncSummary();

  if (list && baseUrl) {
    pollTimer = window.setInterval(pollRemoteCounts, POLL_MS);
    window.setTimeout(pollRemoteCounts, 1200);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) pollRemoteCounts();
    });
    // No beforeunload cleanup — it would block Chrome's back/forward cache.
  }

  if (typeof window.lucide?.createIcons === "function") {
    window.lucide.createIcons();
  }
})();
