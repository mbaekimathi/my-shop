/**
 * Online stock take — variance vs book qty frozen at count time + autosave.
 * Floating cart shows filled counts; polls other devices' updates live.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-online-stock-take]");
  if (!root) return;

  const shopSelect = root.querySelector("[data-online-take-shop]");
  const baseUrl = root.getAttribute("data-online-stock-take-url") || "";
  const list = root.querySelector("[data-online-take-list]");
  const searchInput = root.querySelector("[data-online-take-search]");
  const statusEl = root.querySelector("[data-online-take-save-status]");
  const liveStatusEl = root.querySelector("[data-online-take-live-status]");
  const filledEl = root.querySelector("[data-online-take-filled]");
  const matchEl = root.querySelector("[data-online-take-match]");
  const excessEl = root.querySelector("[data-online-take-excess]");
  const shortEl = root.querySelector("[data-online-take-short]");
  const shopId = root.getAttribute("data-selected-shop-id") || "";
  const csrfInput = root.querySelector("input[name=csrfmiddlewaretoken]");
  const SAVE_DELAY_MS = 450;
  const POLL_MS = 3000;
  const pendingTimers = new Map();
  const lastSaved = new Map();
  /** Most-recently counted item ids first (for cart ordering). */
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

  const findRow = (itemId) => {
    if (!list || itemId == null || itemId === "") return null;
    return list.querySelector(
      `[data-online-take-row][data-item-id="${CSS.escape(String(itemId))}"]`
    );
  };

  const isRowDirty = (row) => {
    const itemId = row?.getAttribute("data-item-id") || "";
    const input = row?.querySelector("[data-online-take-qty]");
    if (!input || !itemId) return false;
    if (pendingTimers.has(itemId)) return true;
    if (document.activeElement === input) return true;
    return input.value.trim() !== String(lastSaved.get(itemId) ?? "");
  };

  const flashRemoteRow = (row) => {
    if (!row) return;
    row.classList.add("is-remote-flash");
    window.setTimeout(() => row.classList.remove("is-remote-flash"), 1200);
  };

  const applySystemQty = (row, systemQty, liveSystemQty) => {
    if (!row) return;
    if (liveSystemQty != null && liveSystemQty !== "") {
      row.setAttribute("data-live-system-qty", String(liveSystemQty));
    }
    if (systemQty != null && systemQty !== "") {
      row.setAttribute("data-system-qty", String(systemQty));
      const systemEl = row.querySelector("[data-online-take-system]");
      if (systemEl) systemEl.textContent = String(systemQty);
    }
  };

  const restoreLiveSystemQty = (row) => {
    const live = row?.getAttribute("data-live-system-qty");
    if (live == null || live === "") return;
    applySystemQty(row, live, live);
  };

  const applyRemoteLine = (line) => {
    const itemId = String(line?.item_id ?? "");
    const row = findRow(itemId);
    if (!row || isRowDirty(row)) return false;
    const input = row.querySelector("[data-online-take-qty]");
    if (!input) return false;

    const nextQty = String(line.physical_qty ?? "").trim();
    const nextBy = String(line.updated_by_label || "").trim();
    const prevQty = String(lastSaved.get(itemId) ?? "");
    const prevBy =
      row.querySelector("[data-online-take-by]")?.textContent?.trim() || "";
    const same =
      prevQty === nextQty &&
      (prevBy === nextBy || (!nextBy && (prevBy === "—" || !prevBy)));

    applySystemQty(row, line.system_qty, line.live_system_qty);
    if (line.category) {
      row.setAttribute("data-category-name", line.category);
    }

    if (!same) {
      input.value = nextQty;
      lastSaved.set(itemId, nextQty);
      syncRow(row);
      applyCountedBy(row, nextQty ? nextBy : "");
      touchFilledOrder(itemId, nextQty !== "");
      flashRemoteRow(row);
      return true;
    }

    syncRow(row);
    return false;
  };

  const clearRemoteLine = (itemId) => {
    const row = findRow(itemId);
    if (!row || isRowDirty(row)) return false;
    const input = row.querySelector("[data-online-take-qty]");
    if (!input) return false;
    const prevQty = String(lastSaved.get(itemId) ?? "");
    if (!prevQty && !input.value.trim()) return false;
    input.value = "";
    lastSaved.set(itemId, "");
    restoreLiveSystemQty(row);
    syncRow(row);
    applyCountedBy(row, "");
    touchFilledOrder(itemId, false);
    flashRemoteRow(row);
    return true;
  };

  const applySyncPayload = (data, { fromPoll = false } = {}) => {
    if (!data?.ok || !list) return;
    const revision = String(data.revision || "");
    if (revision && revision === syncRevision && fromPoll) return;

    const lines = Array.isArray(data.lines) ? data.lines : null;
    let changed = false;

    if (lines) {
      const remoteIds = new Set(
        lines.map((line) => String(line.item_id)).filter(Boolean)
      );
      lines.forEach((line) => {
        if (applyRemoteLine(line)) changed = true;
      });
      lastSaved.forEach((qty, itemId) => {
        if (!qty || remoteIds.has(String(itemId))) return;
        if (clearRemoteLine(itemId)) changed = true;
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

  const applyVariance = (row, result) => {
    const badge = row.querySelector("[data-online-take-variance]");
    if (!badge) return;
    badge.classList.remove("is-empty", "is-match", "is-excess", "is-short");
    badge.classList.add(`is-${result.kind}`);
    row.classList.remove("is-match", "is-excess", "is-short");
    if (result.kind !== "empty") {
      row.classList.add(`is-${result.kind}`);
    }
    if (result.kind === "empty") {
      badge.textContent = "—";
    } else if (result.kind === "match") {
      badge.innerHTML = `<strong>${result.label}</strong>`;
    } else {
      badge.innerHTML = `<strong>${result.label}</strong> <span>(${result.delta})</span>`;
    }
  };

  const applyCountedBy = (row, label) => {
    const byEl = row.querySelector("[data-online-take-by]");
    if (!byEl) return;
    const name = String(label || "").trim();
    byEl.textContent = name || "—";
    if (name) {
      byEl.removeAttribute("data-empty");
    } else {
      byEl.setAttribute("data-empty", "");
    }
    const item = row.querySelector(".online-take-item strong");
    const category = row.getAttribute("data-category-name") || "";
    const itemName = item?.textContent?.trim() || "";
    row.setAttribute(
      "data-search",
      `${itemName} ${category} ${name}`.trim().toLowerCase()
    );
  };

  const syncRow = (row) => {
    const input = row.querySelector("[data-online-take-qty]");
    if (!input) return;
    const result = varianceFor(
      input.value.trim(),
      row.getAttribute("data-system-qty")
    );
    applyVariance(row, result);
    row.classList.toggle("is-filled", input.value.trim() !== "");
    if (!input.value.trim()) applyCountedBy(row, "");
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

  const renderCart = (rows) => {
    if (!cartLines) return;
    if (!rows.length) {
      cartLines.innerHTML = `
        <li class="online-take-cart-empty" data-online-take-cart-empty>
          No counted items yet. Enter a physical count to add it here.
        </li>
      `;
      return;
    }

    const html = rows
      .map((row) => {
        const itemId = row.getAttribute("data-item-id") || "";
        const input = row.querySelector("[data-online-take-qty]");
        const qty = input?.value.trim() || "";
        const name =
          row.querySelector(".online-take-item strong")?.textContent?.trim() ||
          "Item";
        const category =
          row.getAttribute("data-category-name") ||
          row.querySelector(".online-take-item em")?.textContent?.trim() ||
          "";
        const countedBy =
          row.querySelector("[data-online-take-by]")?.textContent?.trim() || "";
        const byLabel =
          countedBy && countedBy !== "—" ? countedBy : "";
        const result = varianceFor(qty, row.getAttribute("data-system-qty"));
        return `
          <li
            class="online-take-cart-line is-${escapeHtml(result.kind)}"
            data-online-take-cart-line
            data-item-id="${escapeHtml(itemId)}"
          >
            <button type="button" data-online-take-cart-jump data-item-id="${escapeHtml(itemId)}">
              <span class="online-take-cart-line-main">
                <strong>${escapeHtml(name)}</strong>
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

  const touchFilledOrder = (itemId, filled) => {
    if (!itemId) return;
    const idx = recentFilledIds.indexOf(itemId);
    if (idx >= 0) recentFilledIds.splice(idx, 1);
    if (filled) recentFilledIds.unshift(itemId);
  };

  const orderedFilledRows = (rowById) => {
    const ordered = [];
    const seen = new Set();
    recentFilledIds.forEach((id) => {
      const row = rowById.get(id);
      if (!row || seen.has(id)) return;
      ordered.push(row);
      seen.add(id);
    });
    rowById.forEach((row, id) => {
      if (seen.has(id)) return;
      ordered.push(row);
      seen.add(id);
    });
    return ordered;
  };

  const syncSummary = () => {
    if (!list) return;
    const rows = [...list.querySelectorAll("[data-online-take-row]")];
    let filled = 0;
    let match = 0;
    let excess = 0;
    let short = 0;
    const rowById = new Map();
    const liveIds = new Set();
    rows.forEach((row) => {
      const input = row.querySelector("[data-online-take-qty]");
      const value = input?.value.trim() || "";
      const itemId = row.getAttribute("data-item-id") || "";
      if (!value) return;
      filled += 1;
      if (itemId) {
        liveIds.add(itemId);
        rowById.set(itemId, row);
      }
      const result = varianceFor(value, row.getAttribute("data-system-qty"));
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

    if (cartBadge) {
      cartBadge.textContent = String(filled);
      cartBadge.hidden = filled === 0;
    }
    if (cartHeading) {
      cartHeading.textContent = `${filled} item${filled === 1 ? "" : "s"}`;
    }
    renderCart(orderedFilledRows(rowById));
    if (filled === 0) setCartOpen(false);
  };

  const saveRow = async (row) => {
    const input = row.querySelector("[data-online-take-qty]");
    const itemId = row.getAttribute("data-item-id");
    if (!input || !itemId || !baseUrl || !shopId) return;
    const qty = input.value.trim();
    if (lastSaved.get(itemId) === qty) {
      syncSummary();
      return;
    }

    setStatus("Saving…");
    savingCount += 1;
    row.classList.add("is-saving");
    const body = new URLSearchParams({
      mode: "online-stock-take",
      shop_id: shopId,
      item_id: itemId,
      qty,
      csrfmiddlewaretoken: csrf,
    });
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
      lastSaved.set(itemId, qty);
      if (qty === "") {
        applySystemQty(
          row,
          data.line?.system_qty ?? data.line?.live_system_qty,
          data.line?.live_system_qty
        );
        if (
          data.line?.system_qty == null &&
          data.line?.live_system_qty == null
        ) {
          restoreLiveSystemQty(row);
        }
      } else {
        applySystemQty(row, data.line?.system_qty, data.line?.live_system_qty);
      }
      if (data.line?.category) {
        row.setAttribute("data-category-name", data.line.category);
      }
      syncRow(row);
      applyCountedBy(
        row,
        qty !== "" ? data.line?.updated_by_label || "" : ""
      );
      touchFilledOrder(itemId, qty !== "");
      if (data.line?.revision) syncRevision = String(data.line.revision);
      syncSummary();
      setStatus(`Saved ${data.line?.saved_at || ""}`.trim());
    } catch (error) {
      setStatus(error?.message || "Could not save this count.", { error: true });
    } finally {
      savingCount = Math.max(0, savingCount - 1);
      row.classList.remove("is-saving");
    }
  };

  const stopPolling = () => {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const pollRemoteCounts = async () => {
    if (!baseUrl || !shopId || document.hidden || pollInFlight) return;
    pollInFlight = true;
    if (savingCount === 0) setLiveStatus("Syncing", { state: "syncing" });
    try {
      const url = new URL(baseUrl, window.location.origin);
      url.searchParams.set("shop_id", shopId);
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

  const queueSave = (row) => {
    const itemId = row.getAttribute("data-item-id");
    if (!itemId) return;
    const existing = pendingTimers.get(itemId);
    if (existing) window.clearTimeout(existing);
    pendingTimers.set(
      itemId,
      window.setTimeout(() => {
        pendingTimers.delete(itemId);
        saveRow(row);
      }, SAVE_DELAY_MS)
    );
  };

  const jumpToRow = (itemId) => {
    if (!itemId || !list) return;
    const row = findRow(itemId);
    if (!row) return;
    revealRowInFullList(row, { keepFocus: true });
  };

  shopSelect?.addEventListener("change", () => {
    if (!baseUrl) return;
    const nextShopId = (shopSelect.value || "").trim();
    const url = new URL(baseUrl, window.location.origin);
    if (nextShopId) {
      url.searchParams.set("shop_id", nextShopId);
    } else {
      url.searchParams.delete("shop_id");
    }
    window.location.assign(url.toString());
  });

  const applySearchFilter = (rawQuery) => {
    const q = String(rawQuery || "")
      .trim()
      .toLowerCase();
    list?.querySelectorAll("[data-online-take-category]").forEach((section) => {
      let visible = 0;
      section.querySelectorAll("[data-online-take-row]").forEach((row) => {
        const hay = row.getAttribute("data-search") || "";
        const show = !q || hay.includes(q);
        row.hidden = !show;
        if (show) visible += 1;
      });
      section.hidden = Boolean(q) && visible === 0;
    });
    return q;
  };

  const revealRowInFullList = (row, { keepFocus = true } = {}) => {
    if (!row) return;
    const input = row.querySelector("[data-online-take-qty]");
    const hadSearch = Boolean((searchInput?.value || "").trim());
    if (hadSearch && searchInput) {
      searchInput.value = "";
      applySearchFilter("");
      returnSearchAfterQty = true;
    }
    row.hidden = false;
    const section = row.closest("[data-online-take-category]");
    if (section) section.hidden = false;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("is-cart-focus");
    window.setTimeout(() => row.classList.remove("is-cart-focus"), 1400);
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
    const row = input.closest("[data-online-take-row]");
    if (!row) return;
    revealRowInFullList(row, { keepFocus: true });
  });

  list?.addEventListener("input", (event) => {
    const input = event.target.closest("[data-online-take-qty]");
    if (!input) return;
    const row = input.closest("[data-online-take-row]");
    if (!row) return;
    // Cover tap/type paths that skip focusin quirks on some mobile browsers.
    if ((searchInput?.value || "").trim()) {
      revealRowInFullList(row, { keepFocus: true });
    }
    syncRow(row);
    touchFilledOrder(
      row.getAttribute("data-item-id"),
      input.value.trim() !== ""
    );
    syncSummary();
    queueSave(row);
  });

  list?.addEventListener("change", (event) => {
    const input = event.target.closest("[data-online-take-qty]");
    if (!input) return;
    const row = input.closest("[data-online-take-row]");
    if (!row) return;
    const itemId = row.getAttribute("data-item-id");
    if (itemId && pendingTimers.has(itemId)) {
      window.clearTimeout(pendingTimers.get(itemId));
      pendingTimers.delete(itemId);
    }
    saveRow(row);
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
    jumpToRow(jump.getAttribute("data-item-id"));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && cart?.classList.contains("is-open")) {
      setCartOpen(false);
    }
  });

  list?.querySelectorAll("[data-online-take-row]").forEach((row) => {
    const input = row.querySelector("[data-online-take-qty]");
    const itemId = row.getAttribute("data-item-id");
    const qty = input?.value.trim() || "";
    if (itemId) lastSaved.set(itemId, qty);
    if (qty) touchFilledOrder(itemId, true);
    syncRow(row);
  });
  // Server already sorts filled_lines newest-first; seed that order on load.
  if (cartLines) {
    const seeded = [
      ...cartLines.querySelectorAll("[data-online-take-cart-line][data-item-id]"),
    ]
      .map((line) => line.getAttribute("data-item-id"))
      .filter(Boolean);
    if (seeded.length) {
      recentFilledIds.length = 0;
      recentFilledIds.push(...seeded);
    }
  }
  syncSummary();

  if (shopId && baseUrl) {
    pollTimer = window.setInterval(pollRemoteCounts, POLL_MS);
    window.setTimeout(pollRemoteCounts, 1200);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) pollRemoteCounts();
    });
    window.addEventListener("beforeunload", stopPolling);
  }

  if (typeof window.lucide?.createIcons === "function") {
    window.lucide.createIcons();
  }
})();
