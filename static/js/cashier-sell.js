/* Cashier dashboard "Sell" popup: live item search → cart → sale/credit/quotation
 * checkout against the MY-SHOP checkout endpoint. A lighter sibling of the shop
 * floor cart (my-shop.js) for role dashboards — no floor unlock needed; the
 * staff ID in the payload authorises the write.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-sell-root]");
  if (!root) return;

  const $ = (sel) => root.querySelector(sel);
  const $$ = (sel) => Array.from(root.querySelectorAll(sel));

  const catalogUrl = root.getAttribute("data-catalog-api") || "";
  const checkoutUrl = root.getAttribute("data-checkout-url") || "";
  const verifyUrl = root.getAttribute("data-verify-login-url") || "";
  const clientLookupUrl = root.getAttribute("data-client-lookup-url") || "";
  const serialSearchUrl = root.getAttribute("data-serial-search-url") || "";

  const discountEnabled = root.getAttribute("data-pos-discount") === "1";
  const stockTracking = root.getAttribute("data-pos-stock-tracking") === "1";
  const clientDataEnabled = root.getAttribute("data-pos-client-data") === "1";
  const requireClientPhone = root.getAttribute("data-pos-require-client-phone") === "1";
  const requireClientName = root.getAttribute("data-pos-require-client-name") === "1";
  const taxEnabled = root.getAttribute("data-pos-tax") === "1";
  const taxPercent = Number(root.getAttribute("data-pos-tax-percent") || "0") || 0;
  const creditWhatsapp = root.getAttribute("data-credit-whatsapp") === "1";
  const defaultPayment = root.getAttribute("data-default-payment") || "";

  const searchInput = $("[data-sell-search]");
  const resultsEl = $("[data-sell-results]");
  const linesEl = $("[data-sell-lines]");
  const emptyEl = $("[data-sell-empty]");
  const countEl = $("[data-sell-count]");
  const clearBtn = $("[data-sell-clear]");
  const totalRowEl = $("[data-sell-total-row]");
  const totalEl = $("[data-sell-total]");
  const form = $("[data-sell-checkout]");
  const whatsappWrap = $("[data-sell-whatsapp-wrap]");
  const whatsappInput = $("[data-sell-whatsapp]");
  const creditDueWrap = $("[data-sell-credit-due-wrap]");
  const creditDueInput = $("[data-sell-credit-due]");
  const paymentWrap = $("[data-sell-payment-wrap]");
  const splitWrap = $("[data-sell-split]");
  const cashInput = $("[data-sell-cash]");
  const mpesaInput = $("[data-sell-mpesa]");
  const cardInput = $("[data-sell-card]");
  const clientWrap = $("[data-sell-client]");
  const clientHeading = $("[data-sell-client-heading]");
  const clientNote = $("[data-sell-client-note]");
  const clientPhoneLabel = $("[data-sell-client-phone-label]");
  const clientNameLabel = $("[data-sell-client-name-label]");
  const clientPhoneInput = $("[data-sell-client-phone]");
  const clientNameInput = $("[data-sell-client-name]");
  const suggestEl = $("[data-sell-client-suggest]");
  const clientHint = $("[data-sell-client-hint]");
  const loginInput = $("[data-sell-login]");
  const loginStatus = $("[data-sell-login-status]");
  const submitBtn = $("[data-sell-submit]");
  const submitLabel = $("[data-sell-submit-label]");
  const statusEl = $("[data-sell-status]");
  const resultEl = $("[data-sell-result]");
  const resultTitle = $("[data-sell-result-title]");
  const resultReceipt = $("[data-sell-result-receipt]");
  const resultPrint = $("[data-sell-result-print]");
  const resultWhatsapp = $("[data-sell-result-whatsapp]");
  const resultNew = $("[data-sell-result-new]");

  const csrfToken = () => {
    const input =
      root.querySelector("[name=csrfmiddlewaretoken]") ||
      document.querySelector("[name=csrfmiddlewaretoken]");
    if (input && input.value) return input.value;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  };

  const apiFetch = (url, options = {}) => {
    const token = csrfToken();
    return fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: {
        ...(token ? { "X-CSRFToken": token } : {}),
        ...(options.headers || {}),
      },
    });
  };

  const getJson = async (url, params) => {
    const full = new URL(url, window.location.origin);
    Object.entries(params || {}).forEach(([key, value]) => {
      if (Array.isArray(value)) {
        value.forEach((entry) => full.searchParams.append(key, entry));
      } else {
        full.searchParams.set(key, value);
      }
    });
    try {
      const res = await apiFetch(full.toString());
      return await res.json();
    } catch (_) {
      return { ok: false };
    }
  };

  const postJson = async (url, payload) => {
    try {
      const res = await apiFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      return await res.json();
    } catch (_) {
      return { ok: false, error: "Network error — try again." };
    }
  };

  const postForm = async (url, fields) => {
    const body = new URLSearchParams(fields || {});
    try {
      const res = await apiFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
      return await res.json();
    } catch (_) {
      return { ok: false };
    }
  };

  const money = (value) => {
    const num = Number(value || 0);
    return `KSh ${num.toLocaleString("en-KE", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  };
  const qtyLabel = (value) => {
    const num = Number(value || 0);
    return num % 1 === 0 ? String(num) : num.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  };

  const setStatus = (message, { error = false } = {}) => {
    if (!statusEl) return;
    statusEl.textContent = message || "";
    statusEl.classList.toggle("is-error", Boolean(error && message));
  };

  // ── Cart state ────────────────────────────────────────────────────────────
  const cart = new Map();

  const lineTotal = (line) => line.qty * line.price;
  const subtotal = () =>
    [...cart.values()].reduce((sum, line) => sum + lineTotal(line), 0);
  const cartTotal = () => {
    const sub = subtotal();
    const tax = taxEnabled ? (sub * taxPercent) / 100 : 0;
    return Math.round((sub + tax) * 100) / 100;
  };

  const currentKind = () => {
    const checked = root.querySelector("[data-sell-kind]:checked");
    if (checked) return checked.value;
    const hidden = root.querySelector("input[data-sell-kind][type='hidden']");
    return hidden ? hidden.value : "sale";
  };

  const selectedPayment = () => {
    const checked = root.querySelector("[data-sell-pay]:checked");
    return checked ? checked.value : defaultPayment;
  };

  const paymentChannels = (method) =>
    ({
      cash: ["cash"],
      mpesa: ["mpesa"],
      card: ["card"],
      both: ["cash", "mpesa"],
      cash_card: ["cash", "card"],
      mpesa_card: ["mpesa", "card"],
      cash_mpesa_card: ["cash", "mpesa", "card"],
    })[method] || [];

  const serialsNeeded = (line) => line.trackSerial && currentKind() !== "quotation";

  // ── Rendering ─────────────────────────────────────────────────────────────
  const KIND_LABELS = { sale: "sale", credit: "credit sale", quotation: "quotation", gift: "gift" };

  const updateCartSummary = () => {
    const size = cart.size;
    if (emptyEl) emptyEl.hidden = size > 0;
    if (countEl) {
      countEl.hidden = !size;
      countEl.textContent = String(size);
    }
    if (clearBtn) clearBtn.hidden = !size;
    if (totalRowEl) totalRowEl.hidden = !size;
    if (totalEl) {
      totalEl.textContent = money(cartTotal());
      totalEl.title = taxEnabled ? `Includes ${taxPercent}% tax` : "";
    }
    if (submitBtn) submitBtn.disabled = !size;
    if (submitLabel) {
      submitLabel.textContent = `Complete ${KIND_LABELS[currentKind()] || "sale"}`;
    }
  };

  const renderCart = () => {
    if (!linesEl) return;
    linesEl.innerHTML = "";
    const lines = [...cart.values()];
    updateCartSummary();

    lines.forEach((line) => {
      const li = document.createElement("li");
      li.className = "sell-line";
      li.dataset.itemId = String(line.id);

      const head = document.createElement("div");
      head.className = "sell-line-head";
      const name = document.createElement("p");
      name.className = "sell-line-name";
      name.textContent = line.name;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "sell-line-remove";
      remove.setAttribute("aria-label", `Remove ${line.name}`);
      remove.innerHTML = '<i data-lucide="x" aria-hidden="true"></i>';
      remove.addEventListener("click", () => {
        cart.delete(line.id);
        renderCart();
      });
      head.append(name, remove);

      const mid = document.createElement("div");
      mid.className = "sell-line-mid";

      if (discountEnabled) {
        const priceLabel = document.createElement("label");
        priceLabel.className = "sell-line-price";
        const priceInput = document.createElement("input");
        priceInput.type = "text";
        priceInput.inputMode = "decimal";
        priceInput.value = String(line.price);
        priceInput.setAttribute("aria-label", `Unit price for ${line.name}`);
        priceInput.addEventListener("change", () => {
          const next = Number(priceInput.value || 0);
          line.price = Number.isFinite(next) && next >= 0 ? next : line.listPrice;
          priceInput.value = String(line.price);
          renderCart();
        });
        priceLabel.append(priceInput);
        mid.append(priceLabel);
      } else {
        const price = document.createElement("span");
        price.className = "sell-line-price-static";
        price.textContent = money(line.price);
        mid.append(price);
      }

      const qtyWrap = document.createElement("div");
      qtyWrap.className = "sell-line-qty";
      const minus = document.createElement("button");
      minus.type = "button";
      minus.textContent = "−";
      minus.setAttribute("aria-label", "Decrease quantity");
      const qtyInput = document.createElement("input");
      qtyInput.type = "text";
      qtyInput.inputMode = "decimal";
      qtyInput.value = qtyLabel(line.qty);
      qtyInput.setAttribute("aria-label", `Quantity for ${line.name}`);
      const plus = document.createElement("button");
      plus.type = "button";
      plus.textContent = "+";
      plus.setAttribute("aria-label", "Increase quantity");
      const setQty = (next) => {
        line.qty = Math.max(0, next);
        if (line.qty <= 0 && !serialsNeeded(line)) {
          cart.delete(line.id);
        }
        renderCart();
      };
      minus.addEventListener("click", () => setQty(line.qty - 1));
      plus.addEventListener("click", () => setQty(line.qty + 1));
      qtyInput.addEventListener("change", () => {
        const next = Number(qtyInput.value || 0);
        setQty(Number.isFinite(next) && next >= 0 ? next : line.qty || 1);
      });
      if (serialsNeeded(line)) {
        qtyInput.disabled = true;
        minus.disabled = true;
        plus.disabled = true;
      }
      qtyWrap.append(minus, qtyInput, plus);
      mid.append(qtyWrap);

      const total = document.createElement("strong");
      total.className = "sell-line-total";
      total.textContent = money(lineTotal(line));
      mid.append(total);
      li.append(head, mid);

      if (line.trackSerial) {
        const serialBtn = document.createElement("button");
        serialBtn.type = "button";
        serialBtn.className = "sell-line-serials-toggle";
        const updateLabel = () => {
          serialBtn.textContent = serialsNeeded(line)
            ? `Serials ${line.serials.length}/${qtyLabel(line.qty)} — pick`
            : `Serials (${line.serials.length})`;
        };
        updateLabel();
        const panel = document.createElement("div");
        panel.className = "sell-line-serial-panel";
        panel.hidden = true;
        serialBtn.addEventListener("click", () => {
          panel.hidden = !panel.hidden;
          if (!panel.hidden && !panel.dataset.loaded) {
            panel.dataset.loaded = "1";
            loadSerials(line, panel, updateLabel);
          }
        });
        if (serialsNeeded(line) && !line.serials.length && !line.qty) {
          // Fresh line — open the picker right away.
          panel.hidden = false;
          panel.dataset.loaded = "1";
          loadSerials(line, panel, updateLabel);
        }
        li.append(serialBtn, panel);
      }

      linesEl.append(li);
    });
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  // ── Serials ───────────────────────────────────────────────────────────────
  const loadSerials = async (line, panel, onChange) => {
    panel.innerHTML = "";
    const search = document.createElement("input");
    search.type = "search";
    search.className = "sell-serial-search";
    search.placeholder = "Filter serials…";
    search.setAttribute("aria-label", `Search serials for ${line.name}`);
    const list = document.createElement("div");
    list.className = "sell-serial-list";
    list.innerHTML = "<p class='sell-serial-note'>Loading…</p>";
    panel.append(search, list);

    let token = 0;
    const fetchSerials = async (q) => {
      const my = ++token;
      const data = await getJson(serialSearchUrl, {
        item_id: line.id,
        q,
        limit: 80,
      });
      if (my !== token) return;
      renderList(data.results || []);
    };
    const renderList = (serials) => {
      list.innerHTML = "";
      if (!serials.length) {
        list.innerHTML = "<p class='sell-serial-note'>No serials in stock.</p>";
        return;
      }
      serials.forEach((serial) => {
        const label = document.createElement("label");
        label.className = "sell-serial-option";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = line.serials.includes(serial);
        box.addEventListener("change", () => {
          if (box.checked && !line.serials.includes(serial)) {
            line.serials.push(serial);
          } else if (!box.checked) {
            line.serials = line.serials.filter((s) => s !== serial);
          }
          if (serialsNeeded(line)) line.qty = line.serials.length;
          onChange();
          // Update this line in place — a full render would drop the open
          // serial panel mid-pick.
          const li = panel.closest("li");
          const qtyField = li?.querySelector(".sell-line-qty input");
          if (qtyField) qtyField.value = qtyLabel(line.qty);
          const totalField = li?.querySelector(".sell-line-total");
          if (totalField) totalField.textContent = money(lineTotal(line));
          updateCartSummary();
        });
        const text = document.createElement("span");
        text.textContent = serial;
        label.append(box, text);
        list.append(label);
      });
    };
    let timer = null;
    search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => fetchSerials(search.value.trim()), 250);
    });
    await fetchSerials("");
  };

  // ── Item search ───────────────────────────────────────────────────────────
  let searchToken = 0;
  let searchPage = 1;
  let searchQuery = "";
  let searchMoreBtn = null;

  const addToCart = (item) => {
    const existing = cart.get(item.id);
    if (existing) {
      if (!serialsNeeded(existing)) existing.qty += 1;
    } else {
      cart.set(item.id, {
        id: item.id,
        name: item.name,
        category: item.category || "",
        listPrice: Number(item.price || 0),
        price: Number(item.price || 0),
        minPrice: Number(item.min_price || 0),
        qty: item.track_serial && currentKind() !== "quotation" ? 0 : 1,
        stock: Number(item.stock || 0),
        tracked: Boolean(item.sale_stock_tracked),
        trackSerial: Boolean(item.track_serial),
        serials: [],
      });
    }
    renderCart();
    setStatus("");
  };

  const renderResults = (items, { append = false } = {}) => {
    if (!resultsEl) return;
    if (!append) resultsEl.innerHTML = "";
    if (searchMoreBtn) {
      searchMoreBtn.remove();
      searchMoreBtn = null;
    }
    if (!items.length && !append) {
      resultsEl.innerHTML =
        '<div class="buy-stock-simple-empty buy-stock-simple-empty-card"><span class="buy-stock-simple-empty-icon" aria-hidden="true"><i data-lucide="search-x"></i></span><p>No matching items.</p></div>';
      if (window.lucide?.createIcons) window.lucide.createIcons();
      return;
    }
    items.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "sell-result-item";
      const main = document.createElement("span");
      main.className = "sell-result-item-main";
      const name = document.createElement("strong");
      name.textContent = item.name;
      const meta = document.createElement("small");
      const bits = [];
      if (item.category) bits.push(item.category);
      if (item.track_serial) bits.push("serial");
      meta.textContent = bits.join(" · ");
      main.append(name, meta);
      const side = document.createElement("span");
      side.className = "sell-result-item-side";
      const price = document.createElement("strong");
      price.textContent = money(item.price);
      side.append(price);
      if (stockTracking && item.sale_stock_tracked && !item.stock_hidden) {
        const stock = document.createElement("small");
        stock.textContent = `${qtyLabel(item.stock)} in stock`;
        side.append(stock);
      }
      btn.append(main, side);
      btn.addEventListener("click", () => addToCart(item));
      resultsEl.append(btn);
    });
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const runSearch = async (page = 1) => {
    const my = ++searchToken;
    const data = await getJson(catalogUrl, {
      q: searchQuery,
      page,
      page_size: 48,
    });
    if (my !== searchToken) return;
    if (!data.ok) {
      if (!resultsEl) return;
      resultsEl.innerHTML =
        '<div class="buy-stock-simple-empty buy-stock-simple-empty-card"><p>Catalog unavailable.</p></div>';
      return;
    }
    searchPage = data.page || page;
    renderResults(data.items || [], { append: page > 1 });
    if (data.has_more && resultsEl) {
      searchMoreBtn = document.createElement("button");
      searchMoreBtn.type = "button";
      searchMoreBtn.className = "btn btn--ghost btn--sm sell-results-more";
      searchMoreBtn.textContent = "Load more";
      searchMoreBtn.addEventListener("click", () => runSearch(searchPage + 1));
      resultsEl.append(searchMoreBtn);
    }
  };

  let searchTimer = null;
  searchInput?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchQuery = searchInput.value.trim();
    if (!searchQuery) {
      searchToken += 1;
      if (resultsEl) {
        resultsEl.innerHTML =
          '<div class="buy-stock-simple-empty buy-stock-simple-empty-card"><span class="buy-stock-simple-empty-icon" aria-hidden="true"><i data-lucide="package-search"></i></span><p>Type a name to add items</p></div>';
        if (window.lucide?.createIcons) window.lucide.createIcons();
      }
      return;
    }
    searchTimer = setTimeout(() => runSearch(1), 250);
  });

  clearBtn?.addEventListener("click", () => {
    cart.clear();
    renderCart();
  });

  // ── Kind / payment visibility ─────────────────────────────────────────────
  const syncKindUI = () => {
    const kind = currentKind();
    if (whatsappWrap) whatsappWrap.hidden = kind !== "quotation";
    if (creditDueWrap) creditDueWrap.hidden = kind !== "credit";
    if (paymentWrap) paymentWrap.hidden = kind !== "sale";
    if (kind !== "sale" && splitWrap) splitWrap.hidden = true;
    syncSplitUI();

    if (clientWrap) {
      const required =
        requireClientPhone ||
        requireClientName ||
        (kind !== "sale" && kind !== "gift");
      clientWrap.classList.toggle("is-required", required);
      if (clientHeading) {
        clientHeading.innerHTML = required
          ? 'Link client <span class="shop-serial-required" aria-hidden="true">*</span>'
          : "Link client";
      }
      if (clientNote) {
        if (requireClientPhone && requireClientName) {
          clientNote.textContent = "Client phone and full name are required.";
        } else if (requireClientPhone) {
          clientNote.textContent = "Client phone is required.";
        } else if (requireClientName) {
          clientNote.textContent = "Client full name is required.";
        } else if (kind === "sale" || kind === "gift") {
          clientNote.textContent = "Optional. Enter a name, a phone number, or both.";
        } else {
          clientNote.textContent = "Enter a name, a phone number, or both.";
        }
      }
      const mark = (el, base, required) => {
        if (!el) return;
        el.innerHTML = required
          ? `${base} <span class="shop-serial-required" aria-hidden="true">*</span>`
          : `${base} <em>(optional)</em>`;
      };
      mark(clientPhoneLabel, "Client phone", requireClientPhone);
      mark(clientNameLabel, "Client full name", requireClientName);
    }

    // Serial requirement changed (quotation ⇄ sale/credit): re-render lines.
    renderCart();
  };

  const syncSplitUI = () => {
    if (!splitWrap) return;
    const method = selectedPayment();
    const channels = paymentChannels(method);
    const isSplit = currentKind() === "sale" && channels.length > 1;
    splitWrap.hidden = !isSplit;
    if (!isSplit) return;
    const wrap = { cash: "[data-sell-split-cash]", mpesa: "[data-sell-split-mpesa]", card: "[data-sell-split-card]" };
    Object.entries(wrap).forEach(([channel, sel]) => {
      const el = $(sel);
      if (el) el.hidden = !channels.includes(channel);
    });
    // Prefill the first channel with the total so the split sums correctly.
    const inputs = { cash: cashInput, mpesa: mpesaInput, card: cardInput };
    const first = channels[0];
    if (inputs[first] && !inputs[first].dataset.touched) {
      inputs[first].value = cartTotal().toFixed(2);
    }
    channels.slice(1).forEach((channel) => {
      if (inputs[channel] && !inputs[channel].dataset.touched) {
        inputs[channel].value = "";
      }
    });
  };

  $$("[data-sell-kind]").forEach((input) =>
    input.addEventListener("change", syncKindUI)
  );
  $$("[data-sell-pay]").forEach((input) =>
    input.addEventListener("change", () => {
      [cashInput, mpesaInput, cardInput].forEach((el) => {
        if (el) delete el.dataset.touched;
      });
      syncSplitUI();
    })
  );
  [cashInput, mpesaInput, cardInput].forEach((el) => {
    el?.addEventListener("input", () => {
      el.dataset.touched = "1";
    });
  });

  // ── Client lookup ─────────────────────────────────────────────────────────
  let suggestToken = 0;
  const hideSuggest = () => {
    if (suggestEl) suggestEl.hidden = true;
  };
  const applyClient = (client) => {
    if (clientNameInput) clientNameInput.value = client.full_name || "";
    if (clientPhoneInput && client.phone_number) {
      clientPhoneInput.value = client.phone_number;
    }
    if (clientHint) {
      clientHint.textContent = "Registered client selected.";
    }
    hideSuggest();
  };

  const searchClients = async (query) => {
    const my = ++suggestToken;
    const data = await getJson(clientLookupUrl, { name: query });
    if (my !== suggestToken || !suggestEl) return;
    const results = data.results || [];
    suggestEl.innerHTML = "";
    if (!results.length) {
      hideSuggest();
      return;
    }
    results.forEach((client) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "shop-cart-client-suggest-option";
      btn.setAttribute("role", "option");
      const strong = document.createElement("strong");
      strong.textContent = client.full_name || "Client";
      btn.append(strong);
      if (client.phone_number) {
        const small = document.createElement("small");
        small.textContent = client.phone_number;
        btn.append(small);
      }
      btn.addEventListener("mousedown", (event) => {
        event.preventDefault();
        applyClient(client);
      });
      suggestEl.append(btn);
    });
    suggestEl.hidden = false;
  };

  let suggestTimer = null;
  clientNameInput?.addEventListener("input", () => {
    clearTimeout(suggestTimer);
    const query = clientNameInput.value.trim();
    if (query.length < 2) {
      hideSuggest();
      return;
    }
    suggestTimer = setTimeout(() => searchClients(query), 250);
  });
  clientNameInput?.addEventListener("blur", () => {
    window.setTimeout(hideSuggest, 150);
  });
  clientNameInput?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideSuggest();
  });

  let phoneLookupToken = 0;
  const lookupPhone = async () => {
    const phone = (clientPhoneInput?.value || "").trim();
    if (phone.length < 9) return;
    const my = ++phoneLookupToken;
    const data = await getJson(clientLookupUrl, { phone });
    if (my !== phoneLookupToken) return;
    if (data.found && data.results?.length) {
      const client = data.results[0];
      if (clientNameInput && !clientNameInput.value.trim()) {
        clientNameInput.value = client.full_name || "";
      }
      if (clientHint) {
        clientHint.textContent = `Registered client — ${client.full_name || ""}`.trim();
      }
    } else if (clientHint && phone) {
      clientHint.textContent = "New client — will be saved at checkout.";
    }
  };
  clientPhoneInput?.addEventListener("change", lookupPhone);
  clientPhoneInput?.addEventListener("blur", lookupPhone);

  // ── Staff ID verify ───────────────────────────────────────────────────────
  let loginToken = 0;
  const verifyLogin = async () => {
    const code = (loginInput?.value || "").trim();
    if (!verifyUrl) return;
    if (code.length !== 6) {
      if (loginStatus) loginStatus.textContent = "";
      return;
    }
    const my = ++loginToken;
    const data = await postForm(verifyUrl, { login_code: code });
    if (my !== loginToken || !loginStatus) return;
    if (data.ok) {
      loginStatus.classList.remove("is-error");
      loginStatus.textContent = `Authorised: ${data.name}`;
    } else {
      loginStatus.textContent = data.error || "Invalid staff ID.";
      loginStatus.classList.add("is-error");
    }
  };
  let loginTimer = null;
  loginInput?.addEventListener("input", () => {
    clearTimeout(loginTimer);
    loginTimer = setTimeout(verifyLogin, 300);
  });

  // ── Checkout ──────────────────────────────────────────────────────────────
  const newCheckoutClientId = () =>
    window.crypto?.randomUUID
      ? window.crypto.randomUUID()
      : `sell-${Date.now()}-${Math.random().toString(16).slice(2)}`;

  const focusClientFields = () => {
    clientPhoneInput?.scrollIntoView({ block: "center", behavior: "smooth" });
    (requireClientPhone ? clientPhoneInput : clientNameInput)?.focus();
  };

  const submitCheckout = async (event) => {
    event.preventDefault();
    if (!checkoutUrl || !cart.size) return;
    const kind = currentKind();
    const lines = [...cart.values()];

    for (const line of lines) {
      if (serialsNeeded(line) && !line.serials.length) {
        setStatus(`Pick serial number(s) for “${line.name}”.`, { error: true });
        return;
      }
      if (serialsNeeded(line) && line.serials.length !== line.qty) {
        setStatus(
          `Pick ${qtyLabel(line.qty)} serial number(s) for “${line.name}”.`,
          { error: true }
        );
        return;
      }
      if (line.qty <= 0) {
        setStatus(`“${line.name}” needs a quantity.`, { error: true });
        return;
      }
    }

    const name = (clientNameInput?.value || "").trim();
    const phone = (clientPhoneInput?.value || "").trim();
    if (clientDataEnabled) {
      if (requireClientPhone && !phone) {
        setStatus("Enter the client phone number before completing.", { error: true });
        focusClientFields();
        return;
      }
      if (requireClientName && !name) {
        setStatus("Enter the client full name before completing.", { error: true });
        focusClientFields();
        return;
      }
      if (kind === "credit" || kind === "quotation") {
        if (kind === "credit" && creditWhatsapp && !phone) {
          setStatus(
            "Enter the client phone number so the credit sale can be sent on WhatsApp.",
            { error: true }
          );
          focusClientFields();
          return;
        }
        if (!phone && !name) {
          setStatus(
            "Enter a client name, a phone number, or both for credit and quotation.",
            { error: true }
          );
          focusClientFields();
          return;
        }
      }
    }

    const payload = {
      kind,
      dashboard_popup: true,
      client_id: newCheckoutClientId(),
      client_name: clientDataEnabled ? name.toUpperCase() : "",
      client_phone: clientDataEnabled ? phone : "",
      login_code: (loginInput?.value || "").trim(),
      share_whatsapp: Boolean(
        clientDataEnabled && kind === "quotation" && whatsappInput?.checked
      ),
      lines: lines.map((line) => ({
        id: line.id,
        qty: line.qty,
        price: discountEnabled ? line.price : line.listPrice || line.price,
        serials: serialsNeeded(line) ? line.serials : [],
      })),
    };

    if (kind === "sale") {
      const method = selectedPayment();
      if (!method) {
        setStatus("Choose an enabled payment method.", { error: true });
        return;
      }
      payload.payment_method = method;
      const channels = paymentChannels(method);
      if (channels.length > 1) {
        const amounts = {
          cash: Number(cashInput?.value || 0),
          mpesa: Number(mpesaInput?.value || 0),
          card: Number(cardInput?.value || 0),
        };
        channels.forEach((channel) => {
          payload[`${channel}_amount`] = String(amounts[channel] || 0);
        });
        const paid = channels.reduce((sum, c) => sum + (amounts[c] || 0), 0);
        if (Math.abs(paid - cartTotal()) > 0.01) {
          setStatus(
            `Split amounts must add up to ${money(cartTotal())}.`,
            { error: true }
          );
          return;
        }
      }
    }

    if (kind === "credit") {
      const due = (creditDueInput?.value || "").trim();
      if (due) payload.credit_due_date = due;
    }

    setStatus("Completing…");
    if (submitBtn) submitBtn.disabled = true;
    const data = await postJson(checkoutUrl, payload);
    if (submitBtn) submitBtn.disabled = !cart.size;
    if (!data.ok) {
      const errors = Array.isArray(data.errors) ? data.errors : [];
      const message =
        data.error === "session_expired"
          ? data.message || "Your session expired. Sign in again."
          : data.error || errors[0] || "Checkout failed.";
      setStatus(message, { error: true });
      return;
    }

    cart.clear();
    renderCart();
    hideSuggest();
    if (clientHint) clientHint.textContent = "";
    if (clientNameInput) clientNameInput.value = "";
    if (clientPhoneInput) clientPhoneInput.value = "";
    setStatus("");

    if (resultTitle) resultTitle.textContent = data.message || "Completed.";
    if (resultReceipt) resultReceipt.textContent = data.receipt_text || "";
    if (resultWhatsapp) {
      if (data.whatsapp_url) {
        resultWhatsapp.href = data.whatsapp_url;
        resultWhatsapp.hidden = false;
      } else {
        resultWhatsapp.hidden = true;
      }
    }
    if (form) form.hidden = true;
    if (resultEl) resultEl.hidden = false;
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  form?.addEventListener("submit", submitCheckout);

  resultPrint?.addEventListener("click", () => {
    const text = resultReceipt?.textContent || "";
    if (!text) return;
    const win = window.open("", "_blank", "width=420,height=640");
    if (!win) return;
    const escaped = text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    win.document.write(
      `<!doctype html><html><head><title>Receipt</title>` +
        `<style>body{font-family:monospace;margin:16px}pre{white-space:pre-wrap;font-size:12px}</style>` +
        `</head><body><pre>${escaped}</pre></body></html>`
    );
    win.document.close();
    win.focus();
    win.print();
  });

  resultNew?.addEventListener("click", () => {
    if (resultEl) resultEl.hidden = true;
    if (form) form.hidden = false;
    if (resultReceipt) resultReceipt.textContent = "";
    searchInput?.focus();
  });

  // Fresh state each time the modal opens.
  const modal = root.closest("[data-modal]");
  modal?.addEventListener("sell-modal:open", () => {
    if (resultEl && !resultEl.hidden) {
      resultEl.hidden = true;
      if (form) form.hidden = false;
      if (resultReceipt) resultReceipt.textContent = "";
    }
    syncKindUI();
    searchInput?.focus();
  });

  syncKindUI();
  renderCart();
})();
