(() => {
  const panel = document.querySelector("[data-item-catalog-api]");
  if (!panel) return;

  const apiUrl = panel.getAttribute("data-item-catalog-api") || "";
  const root = panel.querySelector("[data-item-catalog-root]");
  const moreWrap = panel.querySelector("[data-item-catalog-more-wrap]");
  const moreBtn = panel.querySelector("[data-item-catalog-more]");
  const searchInput = panel.querySelector("[data-item-search]");
  const noResults = panel.querySelector("[data-item-no-results]");
  const visibleCountEl = panel.querySelector("[data-item-visible-count]");
  const csrf =
    document.querySelector("[data-item-csrf]")?.value ||
    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    "";

  if (!apiUrl || !root) return;

  let pageSize = Number(panel.dataset.itemCatalogPageSize || 48) || 48;
  pageSize = Math.min(Math.max(pageSize, 12), 96);

  let totalCount = Number(panel.dataset.itemTotal || 0) || 0;
  let nextPage = 1;
  let hasMore = false;
  let activeQuery = "";
  let inFlight = 0;
  let searchTimer = 0;
  let catalogShops = [];
  let shopColumnsOpen = false;
  const groupEls = new Map();
  const canEdit = panel.dataset.canEdit !== "0";
  const canToggleSuspend = panel.dataset.canToggleSuspend !== "0";
  const canDelete = panel.dataset.canDelete !== "0";

  const escapeHtml = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const updateCountLabel = (visible, query) => {
    if (!visibleCountEl) return;
    if (query) {
      visibleCountEl.textContent = `${visible} of ${totalCount} item${
        totalCount === 1 ? "" : "s"
      }`;
      visibleCountEl.hidden = false;
    } else {
      visibleCountEl.textContent = `${totalCount} item${totalCount === 1 ? "" : "s"}`;
      visibleCountEl.hidden = false;
    }
  };

  const syncShopColumnsUi = () => {
    panel.classList.toggle("item-panel--shop-cols-open", shopColumnsOpen);
    const label = shopColumnsOpen ? "Hide prices" : "View prices";
    const hint = shopColumnsOpen
      ? "Hide shop prices"
      : "View this item’s price at every shop";
    panel.querySelectorAll("[data-toggle-shop-cols]").forEach((btn) => {
      const icon = btn.querySelector("[data-lucide]");
      const text = btn.querySelector("[data-shop-cols-label]");
      btn.setAttribute("aria-pressed", shopColumnsOpen ? "true" : "false");
      btn.setAttribute("aria-label", label);
      btn.setAttribute("title", hint);
      if (text) text.textContent = label;
      if (icon) {
        icon.setAttribute("data-lucide", shopColumnsOpen ? "eye-off" : "eye");
      }
    });
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const shopPriceHeadHtml = () =>
    `<th scope="col" class="item-col--price">Shop prices</th>`;

  const shopHeaderCells = () =>
    catalogShops
      .map(
        (shop) =>
          `<th scope="col" class="item-col--shop" title="${escapeHtml(
            shop.name || ""
          )}">${escapeHtml(shop.name || "")}</th>`
      )
      .join("");

  const ensureGroup = (category) => {
    const key = category || "Uncategorised";
    let section = groupEls.get(key);
    if (section) return section.querySelector("tbody");

    section = document.createElement("section");
    section.className = "item-category-group";
    section.setAttribute("data-item-category-group", "");
    section.innerHTML = `
      <header class="item-category-head">
        <h3 class="item-category-title"></h3>
        <span class="item-category-count" data-category-count>0</span>
      </header>
      <div class="item-table-wrap">
        <table class="item-table item-table--dense">
          <thead>
            <tr>
              <th scope="col" class="item-col--item">Item</th>
              <th scope="col" class="item-col--range">Price range</th>
              ${shopPriceHeadHtml()}
              ${shopHeaderCells()}
              <th scope="col" class="item-col--status">Status</th>
              <th scope="col" class="item-col--actions">Actions</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>`;
    section.querySelector(".item-category-title").textContent = key;
    root.appendChild(section);
    groupEls.set(key, section);
    return section.querySelector("tbody");
  };

  const refreshGroupCounts = () => {
    groupEls.forEach((section) => {
      const count = section.querySelectorAll("[data-item-row]").length;
      const countEl = section.querySelector("[data-category-count]");
      if (countEl) countEl.textContent = String(count);
      section.hidden = count === 0;
    });
  };

  const priceByShopId = (item) => {
    const map = {};
    const rows = Array.isArray(item.shop_price_rows) ? item.shop_price_rows : [];
    rows.forEach((row) => {
      map[String(row.shop_id)] = row.price;
    });
    return map;
  };

  const buildShopPriceCells = (item) => {
    const viewBtn = `<td class="item-cell--price" data-label="Shop prices">
      <button
        type="button"
        class="item-shop-cols-toggle item-shop-cols-toggle--row"
        data-toggle-shop-cols
        aria-pressed="${shopColumnsOpen ? "true" : "false"}"
        title="${
          shopColumnsOpen
            ? "Hide shop prices"
            : "View this item’s price at every shop"
        }"
        aria-label="${shopColumnsOpen ? "Hide prices" : "View prices"}"
      >
        <i data-lucide="${
          shopColumnsOpen ? "eye-off" : "eye"
        }" aria-hidden="true"></i>
        <span data-shop-cols-label>${
          shopColumnsOpen ? "Hide prices" : "View prices"
        }</span>
      </button>
    </td>`;
    if (!catalogShops.length) return viewBtn;
    const prices = priceByShopId(item);
    const shopCells = catalogShops
      .map((shop) => {
        const price = prices[String(shop.id)] || item.shop_price || "0.00";
        return `<td class="item-cell--shop" data-label="${escapeHtml(
          shop.name || "Shop"
        )}"><span class="item-shop-price-value">KSh ${escapeHtml(
          price
        )}</span></td>`;
      })
      .join("");
    return `${viewBtn}${shopCells}`;
  };

  const buildRow = (item) => {
    const name = String(item.name || "");
    const category = String(item.category || "");
    const description = String(item.description || "");
    const descShort =
      description.length > 56 ? `${description.slice(0, 53).trimEnd()}…` : description;
    const imageUrl = String(item.image_url || "");
    const shopPrices = JSON.stringify(item.shop_prices || {});
    const tr = document.createElement("tr");
    if (item.is_suspended) tr.className = "item-row--suspended";
    tr.setAttribute("data-item-row", "");
    tr.setAttribute(
      "data-search-text",
      `${name} ${category} ${description}`.toLowerCase()
    );

    const thumb = imageUrl
      ? `<img class="item-thumb" src="${escapeHtml(imageUrl)}" alt="" width="32" height="32">`
      : `<span class="item-thumb item-thumb--empty" aria-hidden="true"><i data-lucide="package"></i></span>`;

    tr.innerHTML = `
      <td class="item-cell--item" data-label="Item">
        <div class="item-person">
          ${thumb}
          <div class="item-person-copy">
            <strong>${escapeHtml(name)}</strong>
            ${descShort ? `<small>${escapeHtml(descShort)}</small>` : ""}
          </div>
        </div>
      </td>
      <td class="item-cell--range" data-label="Price range">
        <span class="item-price-range">
          <span class="item-price-range-bound item-price-range-bound--min">
            <span class="item-price-range-label">Min</span>
            <span class="item-price-range-amount">KSh ${escapeHtml(
              item.minimum_selling_price || "0.00"
            )}</span>
          </span>
          <span class="item-price-range-sep" aria-hidden="true"></span>
          <span class="item-price-range-bound item-price-range-bound--max">
            <span class="item-price-range-label">Max</span>
            <span class="item-price-range-amount">KSh ${escapeHtml(
              item.maximum_selling_price || "0.00"
            )}</span>
          </span>
        </span>
      </td>
      ${buildShopPriceCells(item)}
      <td class="item-cell--status" data-label="Status">
        <div class="item-status-icons" role="list">
          <span
            class="item-status-icon item-status-icon--${
              item.is_suspended ? "suspended" : "active"
            }"
            role="listitem"
            title="${item.is_suspended ? "Suspended" : "Active"}"
            aria-label="${item.is_suspended ? "Suspended" : "Active"}"
          >
            <i data-lucide="${
              item.is_suspended ? "circle-pause" : "circle-check"
            }" aria-hidden="true"></i>
          </span>
          ${
            item.track_serial
              ? `<span
            class="item-status-icon item-status-icon--serial"
            role="listitem"
            title="Serial tracking"
            aria-label="Serial tracking"
          >
            <i data-lucide="hash" aria-hidden="true"></i>
          </span>`
              : ""
          }
        </div>
      </td>
      <td class="item-cell--actions" data-label="Actions">
        <div class="item-actions">
          ${
            canEdit
              ? `<button
            class="item-action-icon"
            type="button"
            title="Edit item"
            aria-label="Edit ${escapeHtml(name)}"
            data-edit-item
            data-item-id="${item.id}"
            data-category="${escapeHtml(category)}"
            data-name="${escapeHtml(name)}"
            data-description="${escapeHtml(description)}"
            data-minimum-selling-price="${escapeHtml(item.minimum_selling_price || "")}"
            data-maximum-selling-price="${escapeHtml(item.maximum_selling_price || "")}"
            data-shop-price="${escapeHtml(item.shop_price || "")}"
            data-pricing-mode="${escapeHtml(item.pricing_mode || "single")}"
            data-shop-prices="${escapeHtml(shopPrices)}"
            data-track-serial-number="${item.track_serial ? "1" : "0"}"
            data-image-url="${escapeHtml(imageUrl)}"
          >
            <i data-lucide="pencil" aria-hidden="true"></i>
          </button>`
              : ""
          }
          ${
            canToggleSuspend
              ? `<form class="item-inline-form" method="post">
            <input type="hidden" name="csrfmiddlewaretoken" value="${escapeHtml(csrf)}">
            <input type="hidden" name="action" value="toggle_suspend">
            <input type="hidden" name="item_id" value="${item.id}">
            <button
              class="item-action-icon"
              type="submit"
              title="${item.is_suspended ? "Unsuspend item" : "Suspend item"}"
              aria-label="${item.is_suspended ? "Unsuspend" : "Suspend"} ${escapeHtml(name)}"
            >
              <i data-lucide="${item.is_suspended ? "play" : "pause"}" aria-hidden="true"></i>
            </button>
          </form>`
              : ""
          }
          ${
            canDelete
              ? `<form class="item-inline-form" method="post" data-confirm-delete data-item-name="${escapeHtml(
                  name
                )}">
            <input type="hidden" name="csrfmiddlewaretoken" value="${escapeHtml(csrf)}">
            <input type="hidden" name="action" value="delete">
            <input type="hidden" name="item_id" value="${item.id}">
            <button
              class="item-action-icon item-action-icon--danger"
              type="submit"
              title="Delete item"
              aria-label="Delete ${escapeHtml(name)}"
            >
              <i data-lucide="trash-2" aria-hidden="true"></i>
            </button>
          </form>`
              : ""
          }
        </div>
      </td>`;
    return tr;
  };

  const appendItems = (items, { replace }) => {
    if (replace) {
      groupEls.clear();
      root.innerHTML = "";
    }
    items.forEach((item) => {
      const tbody = ensureGroup(item.category);
      tbody.appendChild(buildRow(item));
    });
    refreshGroupCounts();
  };

  const cacheKeyFor = (page, q) =>
    `item-catalog:p${page}:s${pageSize}:q${String(q || "").toLowerCase()}`;

  const applyCatalogData = (data, { page, q, append }) => {
    totalCount = Number(data.total || 0);
    panel.dataset.itemTotal = String(totalCount);
    hasMore = Boolean(data.has_more);
    nextPage = data.next_page || null;
    activeQuery = String(data.q || q || "");
    if (Array.isArray(data.shops)) {
      catalogShops = data.shops;
    }

    appendItems(Array.isArray(data.items) ? data.items : [], { replace: !append });

    const visible = root.querySelectorAll("[data-item-row]").length;
    if (noResults) noResults.hidden = visible > 0 || (!activeQuery && totalCount === 0);
    if (root) root.hidden = visible === 0 && Boolean(activeQuery);
    if (moreWrap) moreWrap.hidden = !hasMore;
    updateCountLabel(visible || totalCount, Boolean(activeQuery));
    syncShopColumnsUi();
  };

  const fetchPage = async ({ page, q, append }) => {
    const seq = ++inFlight;
    if (!append) {
      root.innerHTML =
        '<div class="dashboard-placeholder" data-item-catalog-loading><i data-lucide="loader-circle" aria-hidden="true"></i><p>Loading items…</p></div>';
    }
    if (moreBtn) moreBtn.disabled = true;

    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (q) params.set("q", q);
    const cacheKey = cacheKeyFor(page, q);

    try {
      let data = null;
      let fromCache = false;
      const online = typeof navigator === "undefined" || navigator.onLine;

      if (online) {
        const response = await fetch(`${apiUrl}?${params.toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          throw new Error(data.error || "Catalog load failed");
        }
        try {
          const store = await import("./offline/store.js");
          await store.cacheSet(cacheKey, data, 60 * 60 * 12);
        } catch (_cacheErr) {
          /* cache optional */
        }
      } else {
        const store = await import("./offline/store.js");
        data = await store.cacheGet(cacheKey);
        fromCache = Boolean(data?.ok);
        if (!fromCache) {
          throw new Error("offline_catalog_miss");
        }
      }

      if (seq !== inFlight) return;
      applyCatalogData(data, { page, q, append });
      if (fromCache) {
        panel.setAttribute("data-catalog-from-cache", "1");
      } else {
        panel.removeAttribute("data-catalog-from-cache");
      }

      if (
        online &&
        !append &&
        !fromCache &&
        hasMore &&
        nextPage &&
        typeof navigator !== "undefined" &&
        navigator.onLine
      ) {
        const warmPage = nextPage;
        const warmQ = activeQuery;
        const warmKey = cacheKeyFor(warmPage, warmQ);
        const warmParams = new URLSearchParams({
          page: String(warmPage),
          page_size: String(pageSize),
        });
        if (warmQ) warmParams.set("q", warmQ);
        fetch(`${apiUrl}?${warmParams.toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        })
          .then((res) => res.json().catch(() => ({})))
          .then(async (warmData) => {
            if (!warmData?.ok) return;
            try {
              const store = await import("./offline/store.js");
              await store.cacheSet(warmKey, warmData, 60 * 60 * 12);
            } catch (_err) {
              /* optional */
            }
          })
          .catch(() => {});
      }
    } catch (_error) {
      if (seq !== inFlight) return;
      try {
        const store = await import("./offline/store.js");
        const cached = await store.cacheGet(cacheKey);
        if (seq === inFlight && cached?.ok) {
          applyCatalogData(cached, { page, q, append });
          panel.setAttribute("data-catalog-from-cache", "1");
          return;
        }
      } catch (_cacheErr) {
        /* fall through */
      }
      if (!append) {
        const offline = typeof navigator !== "undefined" && !navigator.onLine;
        root.innerHTML = offline
          ? '<div class="dashboard-placeholder"><p>Offline — open Item Management online once to cache the catalog.</p></div>'
          : '<div class="dashboard-placeholder"><p>Could not load items. Try again.</p></div>';
      }
      if (moreWrap) moreWrap.hidden = true;
    } finally {
      if (seq === inFlight) {
        if (moreBtn) moreBtn.disabled = false;
      }
    }
  };

  const reload = (q = "") => {
    groupEls.clear();
    nextPage = 1;
    hasMore = false;
    fetchPage({ page: 1, q, append: false });
  };

  moreBtn?.addEventListener("click", () => {
    if (!hasMore || !nextPage) return;
    fetchPage({ page: nextPage, q: activeQuery, append: true });
  });

  searchInput?.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      reload(String(searchInput.value || "").trim());
    }, 220);
  });
  searchInput?.addEventListener("search", () => {
    window.clearTimeout(searchTimer);
    reload(String(searchInput.value || "").trim());
  });

  panel.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-toggle-shop-cols]");
    if (!toggle || !panel.contains(toggle)) return;
    event.preventDefault();
    shopColumnsOpen = !shopColumnsOpen;
    syncShopColumnsUi();
  });

  syncShopColumnsUi();
  reload("");
})();
