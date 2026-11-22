(() => {
  const panel = document.querySelector("[data-item-catalog-api]");
  const apiUrl = panel?.getAttribute("data-item-catalog-api") || "";

  const escapeHtml = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const money = (value) =>
    `KSh ${Number(value || 0).toLocaleString("en-KE", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;

  const resolvePrice = (item) => {
    const shopPrice = Number(item?.shop_price || 0);
    if (shopPrice > 0) return shopPrice;
    return Number(item?.minimum_selling_price || 0);
  };

  const formFor = (root) => root.closest("form");

  const checkedType = (form) =>
    form?.querySelector('[name="item_type"]:checked')?.value || "single";

  // Single-item fields are disabled while a combo is built so hidden
  // `required` inputs don't block submission and don't post stale values.
  const syncType = (form) => {
    if (!form) return;
    const isCombo = checkedType(form) === "combo";
    const comboFields = form.querySelector("[data-combo-fields]");
    if (comboFields) {
      comboFields.hidden = !isCombo;
      comboFields
        .querySelectorAll("input")
        .forEach((input) => (input.disabled = !isCombo));
    }
    form.querySelectorAll("[data-single-item-fields]").forEach((el) => {
      el.hidden = isCombo;
      el.querySelectorAll("input, textarea, select").forEach(
        (input) => (input.disabled = isCombo)
      );
    });
    form.querySelectorAll("[data-combo-item-fields]").forEach((el) => {
      el.hidden = !isCombo;
    });
    if (isCombo) {
      // Combo selling prices are optional — blank sells at the items'
      // total (each shop's floor), so none of the price inputs is required.
      form
        .querySelectorAll("[data-single-shop-price], [data-shop-price-input]")
        .forEach((input) => (input.required = false));
    } else {
      const singlePrice = form.querySelector("[data-single-shop-price]");
      if (singlePrice) singlePrice.placeholder = "";
      // Re-apply the pricing-panel state (required/disabled per mode).
      const pricing = form.querySelector('[data-pricing-mode]:checked');
      pricing?.dispatchEvent(new Event("change", { bubbles: true }));
    }
  };

  const selectedIds = (root) =>
    new Set(
      [...root.querySelectorAll('[name="combo_item_id"]')].map((input) =>
        String(input.value || "")
      )
    );

  const MAX_QTY = 9999.9999;

  const comboRows = (root) => [
    ...root.querySelectorAll("[data-combo-row]"),
  ];
  const rowPrice = (row) => Number(row.dataset.itemPrice || 0) || 0;
  const rowQty = (row) =>
    Number(row.querySelector("[data-combo-qty]")?.value) || 0;
  const rowContrib = (row) => rowPrice(row) * rowQty(row);

  const syncTotal = (root) => {
    const totalEl = root.querySelector("[data-combo-total]");
    const emptyEl = root.querySelector("[data-combo-empty]");
    let total = 0;
    root.querySelectorAll("[data-combo-row]").forEach((row) => {
      const price = Number(row.dataset.itemPrice || 0);
      const qty =
        Number(row.querySelector("[data-combo-qty]")?.value || 0) || 0;
      total += price * qty;
      const contribInput = row.querySelector("[data-combo-contrib]");
      if (contribInput && contribInput !== document.activeElement) {
        contribInput.value = (price * qty).toFixed(2);
      }
    });
    if (totalEl) totalEl.textContent = money(total);
    if (emptyEl) {
      emptyEl.hidden = Boolean(
        root.querySelectorAll("[data-combo-row]").length
      );
    }
    // In combo mode the total is the minimum — mirror it into the single
    // shop-price placeholder so "blank = sell at the total" is visible.
    const form = formFor(root);
    const singleInput = form?.querySelector("[data-single-shop-price]");
    if (singleInput && checkedType(form) === "combo") {
      singleInput.placeholder =
        total > 0 ? total.toFixed(2) : "items' total";
    }
  };

  const addRow = (root, { id, name, price, qty }) => {
    const rows = root.querySelector("[data-combo-rows]");
    if (!rows || !id) return;
    if (selectedIds(root).has(String(id))) return;
    const row = document.createElement("div");
    row.className = "item-combo-row";
    row.setAttribute("data-combo-row", "");
    row.dataset.itemPrice = String(price ?? 0);
    const qtyValue =
      qty === undefined || qty === null || qty === "" ? "1" : String(qty);
    row.innerHTML = `
      <input type="hidden" name="combo_item_id" value="${escapeHtml(id)}">
      <input type="hidden" name="combo_item_name" value="${escapeHtml(name)}">
      <input type="hidden" name="combo_item_price" value="${escapeHtml(
        String(price ?? "")
      )}">
      <div class="item-combo-row-main">
        <span class="item-combo-row-name">${escapeHtml(name)}</span>
        <span class="item-combo-row-price">${money(price)}</span>
      </div>
      <label class="item-combo-row-qty">
        <input
          type="number"
          name="combo_qty"
          min="0.0001"
          max="9999.9999"
          step="any"
          value="${escapeHtml(qtyValue)}"
          data-combo-qty
          required
          aria-label="Portions of ${escapeHtml(name)} per combo"
        >
        <span aria-hidden="true">×</span>
      </label>
      <label class="item-combo-row-contrib">
        <span aria-hidden="true">KSh</span>
        <input
          type="number"
          min="0"
          step="0.01"
          data-combo-contrib
          aria-label="Portion price for ${escapeHtml(name)}"
          ${Number(price) > 0 ? "" : "disabled"}
        >
      </label>
      <button
        type="button"
        class="item-combo-row-remove"
        data-combo-remove
        aria-label="Remove ${escapeHtml(name)} from combo"
      >
        <i data-lucide="x" aria-hidden="true"></i>
      </button>`;
    rows.appendChild(row);
    if (window.lucide?.createIcons) window.lucide.createIcons();
    syncTotal(root);
  };

  const resetCombo = (form) => {
    if (!form) return;
    const root = form.querySelector("[data-combo-root]");
    if (root) {
      root.querySelector("[data-combo-rows]")?.replaceChildren();
      const results = root.querySelector("[data-combo-results]");
      if (results) {
        results.hidden = true;
        results.innerHTML = "";
      }
      const search = root.querySelector("[data-combo-search]");
      if (search) search.value = "";
      syncTotal(root);
    }
    const single = form.querySelector('[name="item_type"][value="single"]');
    if (single) {
      single.checked = true;
      single.dispatchEvent(new Event("change", { bubbles: true }));
    }
    syncType(form);
  };

  const renderResults = (root, items) => {
    const box = root.querySelector("[data-combo-results]");
    if (!box) return;
    const picked = selectedIds(root);
    if (!items.length) {
      box.innerHTML =
        '<p class="item-combo-result-empty">No matching items.</p>';
      box.hidden = false;
      return;
    }
    box.innerHTML = items
      .map((item) => {
        const id = String(item.id || "");
        const name = String(item.name || "");
        const category = String(item.category || "");
        const disabled =
          picked.has(id) || item.is_combo || item.track_serial;
        const note = picked.has(id)
          ? "Added"
          : item.is_combo
            ? "Combo"
            : item.track_serial
              ? "Serial-tracked"
              : money(resolvePrice(item));
        const noteClass = disabled
          ? "item-combo-result-tag"
          : "item-combo-result-price";
        return `<button
          type="button"
          class="item-combo-result"
          data-combo-add
          data-id="${escapeHtml(id)}"
          data-name="${escapeHtml(name)}"
          data-price="${escapeHtml(String(resolvePrice(item)))}"
          ${disabled ? "disabled" : ""}
        >
          <span class="item-combo-result-main">
            <span class="item-combo-result-name">${escapeHtml(name)}</span>
            ${category ? `<small class="item-combo-result-cat">${escapeHtml(category)}</small>` : ""}
          </span>
          <span class="${noteClass}">${escapeHtml(note)}</span>
        </button>`;
      })
      .join("");
    box.hidden = false;
  };

  const setupPicker = (root) => {
    const search = root.querySelector("[data-combo-search]");
    const results = root.querySelector("[data-combo-results]");
    if (!search || !results || !apiUrl) return;
    let timer = 0;
    let seq = 0;

    const runSearch = async () => {
      const q = search.value.trim();
      const mySeq = ++seq;
      if (!q) {
        results.hidden = true;
        results.innerHTML = "";
        return;
      }
      try {
        const params = new URLSearchParams({
          q,
          page: "1",
          page_size: "8",
          sort: "name",
        });
        const response = await fetch(`${apiUrl}?${params.toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const data = await response.json().catch(() => ({}));
        if (mySeq !== seq) return;
        renderResults(
          root,
          response.ok && data.ok && Array.isArray(data.items)
            ? data.items
            : []
        );
      } catch (_err) {
        if (mySeq !== seq) return;
        results.hidden = true;
      }
    };

    search.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(runSearch, 250);
    });
    search.addEventListener("focus", () => {
      if (search.value.trim() && results.innerHTML) results.hidden = false;
    });
    search.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        results.hidden = true;
      }
    });

    results.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-combo-add]");
      if (!btn || btn.disabled) return;
      addRow(root, {
        id: btn.dataset.id,
        name: btn.dataset.name,
        price: btn.dataset.price,
      });
      search.value = "";
      results.hidden = true;
      search.focus();
    });

    document.addEventListener("click", (event) => {
      if (!root.contains(event.target)) results.hidden = true;
    });
  };

  const bindRoot = (root) => {
    if (root.dataset.comboBound === "1") return;
    root.dataset.comboBound = "1";
    const form = formFor(root);
    if (!form) return;

    form.querySelectorAll('[name="item_type"]').forEach((radio) => {
      radio.addEventListener("change", () => syncType(form));
    });

    root.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-combo-remove]");
      if (!btn) return;
      btn.closest("[data-combo-row]")?.remove();
      syncTotal(root);
    });

    root.addEventListener("input", (event) => {
      if (event.target.matches("[data-combo-contrib]")) {
        // Typing a portion price back-solves that row's qty (clamped to
        // the max) — everything else stays untouched.
        const edited = event.target.closest("[data-combo-row]");
        const price = edited ? rowPrice(edited) : 0;
        const qtyInput = edited?.querySelector("[data-combo-qty]");
        if (edited && qtyInput && price > 0) {
          const contrib = Math.max(0, Number(event.target.value) || 0);
          qtyInput.value = String(
            Math.min(MAX_QTY, Math.round((contrib / price) * 10000) / 10000)
          );
        }
      }
      syncTotal(root);
    });

    setupPicker(root);
    syncType(form);
    syncTotal(root);
  };

  document.querySelectorAll("[data-combo-root]").forEach(bindRoot);

  // A fresh register intent clears any previously picked combo rows; the
  // "Register item combo" sidebar action also pre-selects the combo type.
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest?.(
      '[data-modal-open="register-item"], [data-modal-open="register-item-combo"]'
    );
    if (!trigger) return;
    const form = document.querySelector(
      '[data-modal="register-item"] form'
    );
    resetCombo(form);
    const title = document.getElementById("register-item-title");
    if (title) {
      title.textContent =
        trigger.dataset.modalOpen === "register-item-combo"
          ? "Register item combo"
          : "Register item";
    }
    if (trigger.dataset.modalOpen === "register-item-combo" && form) {
      const radio = form.querySelector(
        '[name="item_type"][value="combo"]'
      );
      if (radio) radio.checked = true;
      syncType(form);
    }
  });

  // The edit modal is populated from the row's dataset — fill combo state
  // after item-management.js has set the plain fields.
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-edit-item]");
    if (!button) return;
    const form = document.querySelector(
      '[data-modal="edit-item"] [data-edit-form]'
    );
    if (!form) return;
    const root = form.querySelector("[data-combo-root]");
    const isCombo = button.dataset.isCombo === "1";
    const radio = form.querySelector(
      `[name="item_type"][value="${isCombo ? "combo" : "single"}"]`
    );
    if (radio && !radio.checked) radio.checked = true;
    syncType(form);
    if (!root) return;
    root.querySelector("[data-combo-rows]")?.replaceChildren();
    let components = [];
    try {
      components = JSON.parse(button.dataset.comboComponents || "[]");
    } catch (_err) {
      components = [];
    }
    if (!Array.isArray(components)) components = [];
    components.forEach((comp) => {
      addRow(root, {
        id: comp.id,
        name: comp.name,
        price: comp.price,
        qty: comp.qty,
      });
    });
    syncTotal(root);
  });
})();
