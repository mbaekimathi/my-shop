/**
 * Online stock take — live variance + autosave (separate from Stock count).
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
  const filledEl = root.querySelector("[data-online-take-filled]");
  const matchEl = root.querySelector("[data-online-take-match]");
  const excessEl = root.querySelector("[data-online-take-excess]");
  const shortEl = root.querySelector("[data-online-take-short]");
  const shopId = root.getAttribute("data-selected-shop-id") || "";
  const csrfInput = root.querySelector("input[name=csrfmiddlewaretoken]");
  const SAVE_DELAY_MS = 450;
  const pendingTimers = new Map();
  const lastSaved = new Map();

  const readCookie = (name) => {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) {
      return decodeURIComponent(parts.pop().split(";").shift() || "");
    }
    return "";
  };

  const csrf = csrfInput?.value || readCookie("csrftoken") || "";

  const setStatus = (message, { error = false } = {}) => {
    if (!statusEl) return;
    statusEl.textContent = message || "";
    statusEl.classList.toggle("is-error", Boolean(error));
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

  const syncRow = (row) => {
    const input = row.querySelector("[data-online-take-qty]");
    if (!input) return;
    const result = varianceFor(
      input.value.trim(),
      row.getAttribute("data-system-qty")
    );
    applyVariance(row, result);
    row.classList.toggle("is-filled", input.value.trim() !== "");
  };

  const syncSummary = () => {
    if (!list) return;
    const rows = [...list.querySelectorAll("[data-online-take-row]")];
    let filled = 0;
    let match = 0;
    let excess = 0;
    let short = 0;
    rows.forEach((row) => {
      const input = row.querySelector("[data-online-take-qty]");
      const value = input?.value.trim() || "";
      if (!value) return;
      filled += 1;
      const result = varianceFor(value, row.getAttribute("data-system-qty"));
      if (result.kind === "match") match += 1;
      if (result.kind === "excess") excess += 1;
      if (result.kind === "short") short += 1;
    });
    if (filledEl) filledEl.textContent = String(filled);
    if (matchEl) matchEl.textContent = String(match);
    if (excessEl) excessEl.textContent = String(excess);
    if (shortEl) shortEl.textContent = String(short);
  };

  const saveRow = async (row) => {
    const input = row.querySelector("[data-online-take-qty]");
    const itemId = row.getAttribute("data-item-id");
    if (!input || !itemId || !baseUrl || !shopId) return;
    const qty = input.value.trim();
    if (lastSaved.get(itemId) === qty) return;

    setStatus("Saving…");
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
      if (data.line?.system_qty != null) {
        row.setAttribute("data-system-qty", String(data.line.system_qty));
        const systemEl = row.querySelector("[data-online-take-system]");
        if (systemEl) systemEl.textContent = String(data.line.system_qty);
      }
      syncRow(row);
      syncSummary();
      setStatus(`Saved ${data.line?.saved_at || ""}`.trim());
    } catch (error) {
      setStatus(error?.message || "Could not save this count.", { error: true });
    } finally {
      row.classList.remove("is-saving");
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

  searchInput?.addEventListener("input", () => {
    const q = (searchInput.value || "").trim().toLowerCase();
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
  });

  list?.addEventListener("input", (event) => {
    const input = event.target.closest("[data-online-take-qty]");
    if (!input) return;
    const row = input.closest("[data-online-take-row]");
    if (!row) return;
    syncRow(row);
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

  list?.querySelectorAll("[data-online-take-row]").forEach((row) => {
    const input = row.querySelector("[data-online-take-qty]");
    if (input) lastSaved.set(row.getAttribute("data-item-id"), input.value.trim());
    syncRow(row);
  });
  syncSummary();
})();
