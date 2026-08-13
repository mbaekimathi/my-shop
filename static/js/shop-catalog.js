(() => {
  const floor = document.querySelector(".shop-floor[data-catalog-api]");
  if (!floor) return;

  const apiUrl = floor.getAttribute("data-catalog-api") || "";
  const root = floor.querySelector("[data-catalog-root]");
  const searchInput = floor.querySelector("[data-item-search]");
  const noResults = floor.querySelector("[data-item-no-results]");
  const visibleCountEl = floor.querySelector("[data-item-visible-count]");
  const checkoutEnabled = floor.dataset.posCheckout === "1";

  if (!apiUrl || !root) return;

  let pageSize = Number(floor.dataset.catalogPageSize || 120) || 120;
  pageSize = Math.min(Math.max(pageSize, 24), 240);

  let totalCount = Number(floor.dataset.itemTotal || 0) || 0;
  let currentPage = 0;
  let nextPage = 1;
  let hasMore = false;
  let activeQuery = "";
  let inFlight = 0;
  let searchTimer = 0;
  let backgroundLoad = 0;
  const groupEls = new Map();

  const money = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "0.00";
    return n.toFixed(2);
  };

  const escapeHtml = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const updateCountLabel = (visible, query) => {
    if (!visibleCountEl) return;
    const total = totalCount;
    if (query) {
      visibleCountEl.textContent = `${visible} of ${total} item${total === 1 ? "" : "s"}`;
      visibleCountEl.hidden = false;
    } else {
      visibleCountEl.textContent = `${total} item${total === 1 ? "" : "s"}`;
      visibleCountEl.hidden = visibleCountEl.hasAttribute("data-count-hide-idle");
    }
  };

  const ensureGroup = (category) => {
    const key = category || "Uncategorised";
    let section = groupEls.get(key);
    if (section) return section;

    section = document.createElement("section");
    section.className = "shop-floor-category";
    section.setAttribute("data-item-category-group", "");
    section.setAttribute("data-animate", "rise");
    section.style.setProperty("--stagger", String(groupEls.size));
    section.innerHTML = `
      <header class="shop-floor-category-head">
        <h3></h3>
        <div class="shop-floor-category-tools">
          <div class="shop-floor-scroll-controls" data-category-scroll-controls>
            <button
              type="button"
              class="shop-floor-scroll-btn"
              data-category-scroll="prev"
              aria-label="Scroll category left"
            >
              <i data-lucide="chevron-left" aria-hidden="true"></i>
            </button>
            <button
              type="button"
              class="shop-floor-scroll-btn"
              data-category-scroll="next"
              aria-label="Scroll category right"
            >
              <i data-lucide="chevron-right" aria-hidden="true"></i>
            </button>
          </div>
          <span data-category-count>0</span>
        </div>
      </header>
      <div class="shop-floor-scroll-rail">
        <button
          type="button"
          class="shop-floor-scroll-btn shop-floor-scroll-btn--edge"
          data-category-scroll="prev"
          aria-label="Scroll category left"
        >
          <i data-lucide="chevron-left" aria-hidden="true"></i>
        </button>
        <div class="shop-floor-grid" data-category-scroll-track></div>
        <button
          type="button"
          class="shop-floor-scroll-btn shop-floor-scroll-btn--edge"
          data-category-scroll="next"
          aria-label="Scroll category right"
        >
          <i data-lucide="chevron-right" aria-hidden="true"></i>
        </button>
      </div>
    `;
    section.querySelector("h3").textContent = key;
    root.appendChild(section);
    groupEls.set(key, section);
    return section;
  };

  const refreshGroupCounts = () => {
    groupEls.forEach((section) => {
      const count = section.querySelectorAll("[data-item-row]").length;
      const countEl = section.querySelector("[data-category-count]");
      if (countEl) countEl.textContent = String(count);
      section.hidden = count === 0;
    });
  };

  const buildCard = (item) => {
    const stock = Math.max(0, Math.floor(Number(item.stock) || 0));
    const price = money(item.price);
    const minPrice = money(item.min_price);
    const desc = String(item.description || "");
    const descShort = desc.length > 90 ? `${desc.slice(0, 87).trimEnd()}...` : desc;
    const name = String(item.name || "");
    const category = String(item.category || "");
    const imageUrl = String(item.image_url || "");
    const trackSerial = item.track_serial ? "1" : "0";
    const out = stock <= 0;

    const article = document.createElement("article");
    article.className = "shop-floor-item";
    article.setAttribute("data-item-row", "");
    article.setAttribute("data-cart-item", "");
    article.setAttribute("data-item-id", String(item.id));
    article.setAttribute("data-item-name", name);
    article.setAttribute("data-item-category", category);
    article.setAttribute("data-item-description", desc);
    article.setAttribute("data-item-price", price);
    article.setAttribute("data-item-min-price", minPrice);
    article.setAttribute("data-item-list-price", price);
    article.setAttribute("data-item-stock", String(stock));
    article.setAttribute("data-item-track-serial", trackSerial);
    if (imageUrl) article.setAttribute("data-item-image", imageUrl);
    article.setAttribute(
      "data-search-text",
      `${name} ${category}`.toLowerCase()
    );

    const media = imageUrl
      ? `<img src="${escapeHtml(imageUrl)}" alt="" loading="lazy" width="320" height="220">`
      : `<span class="shop-floor-item-fallback"><i data-lucide="package" aria-hidden="true"></i></span>`;

    const descHtml = descShort
      ? `<span class="shop-floor-item-desc">${escapeHtml(descShort)}</span>`
      : category
        ? `<span class="shop-floor-item-desc">${escapeHtml(category)}</span>`
        : "";

    const actions = checkoutEnabled
      ? `
      <div class="shop-floor-item-actions" data-cart-controls>
        <button
          type="button"
          class="btn btn--ghost btn--sm shop-floor-add"
          data-cart-add
          ${out ? "disabled" : ""}
        >
          <i data-lucide="plus" aria-hidden="true"></i>
          <span class="shop-floor-add-label">${out ? "Out of stock" : "Add to cart"}</span>
          <span class="shop-floor-add-short">${out ? "Out" : "Add"}</span>
        </button>
        <div class="shop-floor-qty" data-cart-qty-wrap hidden>
          <button type="button" class="shop-floor-qty-btn" data-cart-qty="dec" aria-label="Decrease quantity">
            <i data-lucide="minus" aria-hidden="true"></i>
          </button>
          <input
            type="number"
            class="shop-floor-qty-input"
            data-cart-qty-input
            min="1"
            max="${stock}"
            value="1"
            inputmode="numeric"
            aria-label="Quantity in cart"
          >
          <button type="button" class="shop-floor-qty-btn" data-cart-qty="inc" aria-label="Increase quantity">
            <i data-lucide="plus" aria-hidden="true"></i>
          </button>
        </div>
      </div>`
      : "";

    article.innerHTML = `
      <button
        type="button"
        class="shop-floor-item-open"
        data-cart-preview
        aria-label="View ${escapeHtml(name)}"
      >
        <span class="shop-floor-item-media">${media}</span>
        <span class="shop-floor-item-copy">
          <span class="shop-floor-item-title">${escapeHtml(name)}</span>
          ${descHtml}
        </span>
      </button>
      <div class="shop-floor-item-meta">
        <span class="shop-floor-stock${out ? " is-empty" : ""}">
          <span class="shop-floor-stock-label">Stock</span>
          <span class="shop-floor-stock-value">${stock}</span>
        </span>
        <span class="shop-floor-price">KSh ${escapeHtml(price)}</span>
      </div>
      ${actions}
    `;
    return article;
  };

  const setLoading = (on) => {
    let loader = root.querySelector("[data-catalog-loading]");
    if (on) {
      if (!loader) {
        loader = document.createElement("div");
        loader.className = "dashboard-placeholder shop-floor-catalog-loading";
        loader.setAttribute("data-catalog-loading", "");
        loader.innerHTML =
          '<i data-lucide="loader-circle" aria-hidden="true"></i><p>Loading catalog…</p>';
        root.prepend(loader);
      }
      loader.hidden = false;
    } else if (loader) {
      loader.remove();
    }
  };

  const notifyRendered = () => {
    document.dispatchEvent(
      new CustomEvent("shop-catalog:rendered", {
        detail: { total: totalCount, visible: root.querySelectorAll("[data-item-row]").length },
      })
    );
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const yieldToBrowser = () =>
    new Promise((resolve) => {
      if (typeof window.requestIdleCallback === "function") {
        window.requestIdleCallback(() => resolve(), { timeout: 120 });
      } else {
        window.setTimeout(resolve, 0);
      }
    });

  const appendItems = async (items, { replace, seq }) => {
    if (replace) {
      groupEls.clear();
      root.innerHTML = "";
    }
    const list = Array.isArray(items) ? items : [];
    const chunkSize = 24;
    for (let i = 0; i < list.length; i += 1) {
      if (seq != null && seq !== inFlight) return false;
      const item = list[i];
      const section = ensureGroup(item.category);
      const grid = section.querySelector(".shop-floor-grid");
      grid.appendChild(buildCard(item));
      if ((i + 1) % chunkSize === 0) {
        await yieldToBrowser();
      }
    }
    refreshGroupCounts();
    return true;
  };

  const shopId = floor.dataset.shopId || "0";
  const cacheKeyFor = (page, q) =>
    `shop-catalog:${shopId}:p${page}:s${pageSize}:q${String(q || "").toLowerCase()}`;

  const tokensFrom = (query) =>
    String(query || "")
      .toLowerCase()
      .trim()
      .split(/\s+/)
      .filter(Boolean);

  const hasRenderedItems = () =>
    Boolean(root.querySelector("[data-item-row]"));

  const filterRenderedItems = (query) => {
    const tokens = tokensFrom(query);
    const hasQuery = tokens.length > 0;
    let visible = 0;
    root.querySelectorAll("[data-item-category-group]").forEach((section) => {
      const categoryLabel =
        section.querySelector(".shop-floor-category-head h3")?.textContent || "";
      let groupVisible = 0;
      section.querySelectorAll("[data-item-row]").forEach((row) => {
        const haystack = `${row.dataset.searchText || ""} ${categoryLabel}`.toLowerCase();
        const match = !hasQuery || tokens.every((token) => haystack.includes(token));
        row.hidden = !match;
        if (match) {
          groupVisible += 1;
          visible += 1;
        }
      });
      section.hidden = groupVisible === 0;
      const countEl = section.querySelector("[data-category-count]");
      if (countEl) countEl.textContent = String(groupVisible);
    });
    if (noResults) noResults.hidden = visible > 0 || !hasQuery;
    if (root) root.hidden = visible === 0 && hasQuery;
    updateCountLabel(hasQuery ? visible : totalCount, hasQuery);
    floor.toggleAttribute("data-catalog-local-filter", hasQuery);
  };

  const syncUiAfterRender = () => {
    const visible = root.querySelectorAll("[data-item-row]").length;
    if (noResults) noResults.hidden = visible > 0 || (!activeQuery && totalCount === 0);
    if (root) root.hidden = visible === 0 && Boolean(activeQuery);
    updateCountLabel(visible || totalCount, Boolean(activeQuery));
    notifyRendered();
  };

  const fetchCatalogPage = async ({ page, q }) => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (q) params.set("q", q);
    const cacheKey = cacheKeyFor(page, q);
    const online = typeof navigator === "undefined" || navigator.onLine;

    let data = null;
    let fromCache = false;

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
      if (!fromCache && q) {
        data = await store.cacheGet(cacheKeyFor(page, ""));
        fromCache = Boolean(data?.ok);
        if (fromCache) data = { ...data, localFilter: q };
      }
      if (!fromCache) {
        throw new Error("offline_catalog_miss");
      }
    }

    return { data, fromCache, cacheKey };
  };

  const loadRemainingPages = async ({ seq, q }) => {
    const loadId = ++backgroundLoad;
    while (seq === inFlight && loadId === backgroundLoad && hasMore && nextPage) {
      await yieldToBrowser();
      if (seq !== inFlight || loadId !== backgroundLoad) return;

      const page = nextPage;
      try {
        const { data, fromCache } = await fetchCatalogPage({ page, q });
        if (seq !== inFlight || loadId !== backgroundLoad) return;

        totalCount = Number(data.total || totalCount);
        floor.dataset.itemTotal = String(totalCount);
        currentPage = Number(data.page || page);
        hasMore = Boolean(data.has_more);
        nextPage = data.next_page || null;

        const ok = await appendItems(data.items, { replace: false, seq });
        if (!ok || seq !== inFlight || loadId !== backgroundLoad) return;

        if (fromCache) floor.setAttribute("data-catalog-from-cache", "1");
        else floor.removeAttribute("data-catalog-from-cache");
        syncUiAfterRender();
      } catch (_error) {
        // Keep what is already on screen; stop background fill.
        hasMore = false;
        nextPage = null;
        return;
      }
    }
  };

  const fetchPage = async ({ page, q, append }) => {
    const seq = ++inFlight;
    backgroundLoad += 1;
    setLoading(!append);

    try {
      let result;
      try {
        result = await fetchCatalogPage({ page, q });
      } catch (_error) {
        if (seq !== inFlight) return;
        try {
          const store = await import("./offline/store.js");
          const cached = await store.cacheGet(cacheKeyFor(page, q));
          if (seq === inFlight && cached?.ok) {
            result = { data: cached, fromCache: true };
          } else if (q) {
            const unfiltered = await store.cacheGet(cacheKeyFor(page, ""));
            if (seq === inFlight && unfiltered?.ok) {
              result = {
                data: { ...unfiltered, localFilter: q },
                fromCache: true,
              };
            } else {
              throw _error;
            }
          } else {
            throw _error;
          }
        } catch (_cacheErr) {
          throw _error;
        }
      }

      if (seq !== inFlight) return;

      const { data, fromCache } = result;
      totalCount = Number(data.total || 0);
      floor.dataset.itemTotal = String(totalCount);
      currentPage = Number(data.page || page);
      hasMore = Boolean(data.has_more);
      nextPage = data.next_page || null;
      activeQuery = String(data.localFilter || data.q || q || "");

      const ok = await appendItems(data.items, { replace: !append, seq });
      if (!ok || seq !== inFlight) return;

      if (fromCache) floor.setAttribute("data-catalog-from-cache", "1");
      else floor.removeAttribute("data-catalog-from-cache");

      const localFilter = String(data.localFilter || q || "");
      const offline = typeof navigator !== "undefined" && !navigator.onLine;
      if (offline && localFilter) {
        filterRenderedItems(localFilter);
      } else {
        syncUiAfterRender();
      }

      // First paint is done — keep fetching remaining pages in the background.
      if (hasMore && nextPage && !offline) {
        loadRemainingPages({ seq, q: activeQuery });
      }
    } catch (_error) {
      if (seq !== inFlight) return;
      if (!append) {
        if (hasRenderedItems()) {
          filterRenderedItems(activeQuery);
          return;
        }
        const offline = typeof navigator !== "undefined" && !navigator.onLine;
        root.innerHTML = offline
          ? '<div class="dashboard-placeholder"><p>Offline — open this shop online once to cache the catalog.</p></div>'
          : '<div class="dashboard-placeholder"><p>Could not load catalog. Try again.</p></div>';
      }
    } finally {
      if (seq === inFlight) {
        setLoading(false);
      }
    }
  };

  const reload = (q = "") => {
    const offline = typeof navigator !== "undefined" && !navigator.onLine;
    if (offline && hasRenderedItems()) {
      filterRenderedItems(q);
      activeQuery = q;
      return;
    }
    if (!offline) {
      floor.removeAttribute("data-catalog-local-filter");
      root.querySelectorAll("[data-item-row]").forEach((row) => {
        row.hidden = false;
      });
    }
    groupEls.clear();
    currentPage = 0;
    nextPage = 1;
    hasMore = false;
    fetchPage({ page: 1, q, append: false });
  };

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

  reload("");
})();
