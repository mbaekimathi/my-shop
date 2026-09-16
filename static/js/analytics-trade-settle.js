(() => {
  const root = document.querySelector("[data-trade-settle]");
  if (!root) return;

  const settleUrl = root.getAttribute("data-settle-url") || "";
  const catalogUrl = root.getAttribute("data-catalog-url") || "";
  const canSettle = root.getAttribute("data-can-settle") === "1";
  if (!canSettle || !settleUrl) return;

  const paymentForm = root.querySelector("[data-trade-payment-form]");
  const exchangeForm = root.querySelector("[data-trade-exchange-form]");
  const statusEl = root.querySelector("[data-trade-status]");
  const balanceEl = root.querySelector("[data-trade-balance]");
  const paidEl = root.querySelector("[data-trade-paid]");
  const exchangeEl = root.querySelector("[data-trade-exchange]");
  const itemSearch = root.querySelector("[data-trade-item-search]");
  const itemSuggest = root.querySelector("[data-trade-item-suggest]");
  const itemIdInput = root.querySelector("[data-trade-item-id]");
  const itemLabel = root.querySelector("[data-trade-item-label]");
  const qtyInput = root.querySelector("[data-trade-exchange-qty]");
  const priceInput = root.querySelector("[data-trade-exchange-price]");
  const exchangeValueEl = root.querySelector("[data-trade-exchange-value]");
  const exchangeBalanceEl = root.querySelector("[data-trade-exchange-balance]");

  let balance = Number(root.getAttribute("data-balance") || 0);
  let searchTimer = 0;
  let busy = false;

  function csrfToken() {
    return (
      root.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
      document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
      ""
    );
  }

  function money(value) {
    const amount = Number(value || 0);
    return `KSh ${amount.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function setStatus(message, { ok = false, error = false } = {}) {
    if (!statusEl) return;
    statusEl.hidden = !message;
    statusEl.textContent = message || "";
    statusEl.classList.toggle("is-ok", Boolean(ok));
    statusEl.classList.toggle("is-error", Boolean(error));
  }

  function syncChoiceClasses(selector) {
    root.querySelectorAll(selector).forEach((label) => {
      const input = label.querySelector("input");
      label.classList.toggle("is-active", Boolean(input?.checked));
    });
  }

  function syncModes() {
    const mode =
      root.querySelector("[data-trade-mode]:checked")?.value || "payment";
    if (paymentForm) paymentForm.hidden = mode !== "payment";
    if (exchangeForm) exchangeForm.hidden = mode !== "exchange";
    syncChoiceClasses(".td-tab");
    setStatus("");
  }

  function syncExchangePreview() {
    const qty = Math.max(0, Number(qtyInput?.value || 0));
    const price = Math.max(0, Number(priceInput?.value || 0));
    const value = qty * price;
    if (exchangeValueEl) exchangeValueEl.textContent = money(value);
    if (exchangeBalanceEl) {
      exchangeBalanceEl.textContent = money(Math.max(0, balance - value));
    }
  }

  function hideSuggest() {
    if (!itemSuggest) return;
    itemSuggest.hidden = true;
    itemSuggest.innerHTML = "";
  }

  function selectItem(item) {
    if (itemIdInput) itemIdInput.value = String(item.id || "");
    if (itemSearch) itemSearch.value = item.name || "";
    if (itemLabel) {
      itemLabel.hidden = false;
      itemLabel.textContent = item.name || "";
    }
    hideSuggest();
  }

  async function searchItems(query) {
    if (!catalogUrl || query.length < 1) {
      hideSuggest();
      return;
    }
    try {
      const url = new URL(catalogUrl, window.location.origin);
      url.searchParams.set("q", query);
      url.searchParams.set("limit", "12");
      const response = await fetch(url.toString(), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      const items = Array.isArray(data.items)
        ? data.items
        : Array.isArray(data.results)
          ? data.results
          : [];
      if (!itemSuggest) return;
      itemSuggest.innerHTML = "";
      if (!items.length) {
        itemSuggest.hidden = true;
        return;
      }
      items.slice(0, 12).forEach((item) => {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = item.name || item.item_name || `Item #${item.id}`;
        btn.addEventListener("click", () =>
          selectItem({
            id: item.id || item.item_id,
            name: item.name || item.item_name,
          })
        );
        li.appendChild(btn);
        itemSuggest.appendChild(li);
      });
      itemSuggest.hidden = false;
    } catch (_error) {
      hideSuggest();
    }
  }

  async function postSettle(form) {
    if (busy) return null;
    busy = true;
    const submit = form.querySelector(".td-submit, [type=submit]");
    if (submit) submit.disabled = true;
    setStatus("Saving…");
    try {
      const body = new FormData(form);
      const response = await fetch(settleUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfToken(),
        },
        body,
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not settle this trade.");
      }
      return data;
    } finally {
      busy = false;
      if (submit) submit.disabled = false;
    }
  }

  root.querySelectorAll("[data-trade-mode]").forEach((input) => {
    input.addEventListener("change", syncModes);
  });
  root.querySelectorAll('input[name="payment_method"]').forEach((input) => {
    input.addEventListener("change", () => syncChoiceClasses(".td-method"));
  });
  syncModes();
  syncChoiceClasses(".td-method");

  qtyInput?.addEventListener("input", syncExchangePreview);
  priceInput?.addEventListener("input", syncExchangePreview);
  syncExchangePreview();

  itemSearch?.addEventListener("input", () => {
    if (itemIdInput) itemIdInput.value = "";
    if (itemLabel) itemLabel.hidden = true;
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(
      () => searchItems((itemSearch.value || "").trim()),
      220
    );
  });

  document.addEventListener("click", (event) => {
    if (!itemSuggest || itemSuggest.hidden) return;
    if (event.target.closest("[data-trade-item-search], [data-trade-item-suggest]")) {
      return;
    }
    hideSuggest();
  });

  paymentForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const data = await postSettle(paymentForm);
      if (!data) return;
      setStatus(data.message || "Payment recorded.", { ok: true });
      if (balanceEl) balanceEl.textContent = data.balance_label || money(data.balance);
      if (paidEl && data.amount_paid != null) paidEl.textContent = money(data.amount_paid);
      window.setTimeout(() => window.location.reload(), 400);
    } catch (error) {
      setStatus(error.message || "Could not record payment.", { error: true });
    }
  });

  exchangeForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!itemIdInput?.value) {
      setStatus("Select an item to stock in.", { error: true });
      itemSearch?.focus();
      return;
    }
    try {
      const data = await postSettle(exchangeForm);
      if (!data) return;
      setStatus(data.message || "Exchange recorded.", { ok: true });
      if (balanceEl) balanceEl.textContent = data.balance_label || money(data.balance);
      if (exchangeEl && data.exchange_value != null) {
        exchangeEl.textContent = money(data.exchange_value);
      }
      window.setTimeout(() => window.location.reload(), 400);
    } catch (error) {
      setStatus(error.message || "Could not record exchange.", { error: true });
    }
  });
})();
