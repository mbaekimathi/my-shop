/**
 * Online stock take page — shop select, live variance, submit counts.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-online-stock-take]");
  if (!root) return;

  const shopSelect = root.querySelector("[data-online-take-shop]");
  const baseUrl = root.getAttribute("data-online-stock-take-url") || "";
  const form = root.querySelector("[data-online-take-form]");
  const list = root.querySelector("[data-online-take-list]");
  const searchInput = root.querySelector("[data-online-take-search]");
  const progressEl = root.querySelector("[data-online-take-progress]");
  const errorEl = root.querySelector("[data-online-take-error]");
  const submitBtn = root.querySelector("[data-online-take-submit]");

  const readCookie = (name) => {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) {
      return decodeURIComponent(parts.pop().split(";").shift() || "");
    }
    return "";
  };

  const csrf =
    form?.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    readCookie("csrftoken") ||
    "";

  const showError = (message) => {
    if (!errorEl) return;
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
  };

  const piecesWord = (absDiff) => (absDiff === 1 ? "piece" : "pieces");

  const formatQty = (value) => {
    if (!Number.isFinite(value)) return "";
    const text = String(value);
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

  const syncRow = (row) => {
    const input = row.querySelector("[data-online-take-qty]");
    const badge = row.querySelector("[data-online-take-variance]");
    if (!input || !badge) return;
    const result = varianceFor(input.value.trim(), row.getAttribute("data-system-qty"));
    badge.classList.remove("is-empty", "is-match", "is-excess", "is-short");
    badge.classList.add(`is-${result.kind}`);
    if (result.kind === "empty") {
      badge.textContent = "—";
    } else if (result.kind === "match") {
      badge.innerHTML = `<strong>${result.label}</strong>`;
    } else {
      badge.innerHTML = `<strong>${result.label}</strong> <span>(${result.delta})</span>`;
    }
    row.classList.toggle("is-filled", input.value.trim() !== "");
  };

  const syncProgress = () => {
    if (!list || !progressEl) return;
    const rows = [...list.querySelectorAll("[data-online-take-row]")];
    const filled = rows.filter((row) => {
      const input = row.querySelector("[data-online-take-qty]");
      return input && input.value.trim() !== "";
    }).length;
    progressEl.textContent = `${filled} / ${rows.length} filled`;
  };

  const syncAll = () => {
    if (!list) return;
    list.querySelectorAll("[data-online-take-row]").forEach(syncRow);
    syncProgress();
  };

  shopSelect?.addEventListener("change", () => {
    const shopId = (shopSelect.value || "").trim();
    if (!baseUrl) return;
    const url = new URL(baseUrl, window.location.origin);
    if (shopId) {
      url.searchParams.set("shop_id", shopId);
    } else {
      url.searchParams.delete("shop_id");
    }
    window.location.assign(url.toString());
  });

  searchInput?.addEventListener("input", () => {
    const q = (searchInput.value || "").trim().toLowerCase();
    list?.querySelectorAll("[data-online-take-row]").forEach((row) => {
      const hay = row.getAttribute("data-search") || "";
      row.hidden = Boolean(q) && !hay.includes(q);
    });
  });

  list?.addEventListener("input", (event) => {
    const input = event.target.closest("[data-online-take-qty]");
    if (!input) return;
    const row = input.closest("[data-online-take-row]");
    if (!row) return;
    syncRow(row);
    syncProgress();
  });

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("");
    if (!form || !baseUrl) return;

    const rows = [...(list?.querySelectorAll("[data-online-take-row]") || [])];
    const missing = rows.find((row) => {
      const input = row.querySelector("[data-online-take-qty]");
      return !input || input.value.trim() === "";
    });
    if (missing) {
      showError("Enter a count for every item before submitting.");
      missing.querySelector("[data-online-take-qty]")?.focus();
      return;
    }

    if (submitBtn) {
      submitBtn.disabled = true;
    }

    const body = new FormData(form);
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
        throw new Error(data.error || "Could not submit stock count.");
      }
      const shopId = root.getAttribute("data-selected-shop-id") || "";
      const url = new URL(baseUrl, window.location.origin);
      if (shopId) url.searchParams.set("shop_id", shopId);
      window.location.assign(url.toString());
    } catch (error) {
      showError(error?.message || "Could not submit stock count.");
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  syncAll();
})();
