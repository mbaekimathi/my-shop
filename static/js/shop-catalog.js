(() => {
  const floor = document.querySelector(".shop-floor[data-catalog-api]");
  if (!floor) return;

  const apiUrl = floor.getAttribute("data-catalog-api") || "";
  const root = floor.querySelector("[data-catalog-root]");
  const searchInput = floor.querySelector("[data-item-search]");
  const noResults = floor.querySelector("[data-item-no-results]");
  const visibleCountEl = floor.querySelector("[data-item-visible-count]");
  const checkoutEnabled = floor.dataset.posCheckout === "1";
  const stockTrackingEnabled = floor.dataset.posStockTracking !== "0";
  const stockLimited =
    stockTrackingEnabled && floor.dataset.posBlockNegative !== "0";

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
  // Catalog snapshots are only read while offline or after a network error,
  // so they get the long offline TTL — a 15-minute expiry would empty the
  // till's catalog mid-outage.
  let catalogCacheTtl = 60 * 60 * 12;
  import("./offline/catalog-cache.js")
    .then((mod) => {
      if (mod?.CATALOG_CACHE_OFFLINE_TTL_SECONDS) {
        catalogCacheTtl = mod.CATALOG_CACHE_OFFLINE_TTL_SECONDS;
      }
    })
    .catch(() => {});

  const money = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "0.00";
    return n.toFixed(2);
  };

  const roundQty = (value) => {
    const num = Number(value);
    if (!Number.isFinite(num)) return 0;
    return Math.round(num * 1000) / 1000;
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
    const stock = Math.max(0, roundQty(item.stock));
    const hideStock = Boolean(item.stock_hidden);
    const price = money(item.price);
    const minPrice = money(item.min_price);
    const desc = String(item.description || "");
    const descShort = desc.length > 90 ? `${desc.slice(0, 87).trimEnd()}...` : desc;
    const name = String(item.name || "");
    const category = String(item.category || "");
    const imageUrl = String(item.image_url || "");
    const trackSerial = item.track_serial ? "1" : "0";
    const saleTracked = item.sale_stock_tracked !== false;
    const out = stockLimited && saleTracked && stock <= 0;

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
    article.setAttribute(
      "data-item-discount-min-qty",
      String(Math.max(0, Math.floor(Number(item.discount_min_qty) || 0)))
    );
    article.setAttribute(
      "data-item-discount-amount",
      money(item.discount_amount)
    );
    article.setAttribute(
      "data-item-avg-buy-qty",
      String(Math.max(1, Math.floor(Number(item.avg_buy_qty) || 1)))
    );
    article.setAttribute("data-item-stock-base", String(stock));
    article.setAttribute("data-item-stock", String(stock));
    if (hideStock) article.setAttribute("data-stock-hidden", "1");
    article.setAttribute("data-item-track-serial", trackSerial);
    article.setAttribute("data-item-sale-tracked", saleTracked ? "1" : "0");
    if (imageUrl) article.setAttribute("data-item-image", imageUrl);
    article.setAttribute(
      "data-search-text",
      `${name} ${category}`.toLowerCase()
    );

    const imgOnError =
      "this.onerror=null;this.replaceWith(Object.assign(document.createElement('span'),{className:'shop-floor-item-fallback',innerHTML:'<i data-lucide=\\'package\\' aria-hidden=\\'true\\'></i>'}));window.lucide&&window.lucide.createIcons&&window.lucide.createIcons();";
    const media = imageUrl
      ? `<img src="${escapeHtml(imageUrl)}" alt="" loading="lazy" width="320" height="220" onerror="${imgOnError}">`
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
            type="text"
            class="shop-floor-qty-input"
            data-cart-qty-input
            min="0.001"
            step="0.001"
            ${stockLimited && saleTracked && !hideStock && stock > 0 ? `max="${stock}"` : ""}
            value="1"
            inputmode="decimal"
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
        ${
          stockTrackingEnabled && saleTracked && !hideStock
            ? `<span class="shop-floor-stock${out ? " is-empty" : ""}">
          <span class="shop-floor-stock-label">Stock</span>
          <span class="shop-floor-stock-value">${stock}</span>
        </span>`
            : ""
        }
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

  const syncStaleCatalogHint = () => {
    let hint = floor.querySelector("[data-catalog-stale-banner]");
    if (!floor.hasAttribute("data-catalog-from-cache")) {
      hint?.remove();
      return;
    }
    if (!hint) {
      hint = document.createElement("p");
      hint.className = "shop-floor-catalog-stale-hint";
      hint.setAttribute("data-catalog-stale-banner", "");
      hint.textContent =
        "Cached catalog — stock and prices update when you reconnect.";
      root.parentNode?.insertBefore(hint, root);
    }
  };

  const notifyRendered = () => {
    syncStaleCatalogHint();
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

  const reduceMotion = () =>
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Pin the first match just under the sticky toolbar so a search never
  // leaves the user scrolled past their results — the section (with its
  // category header) starts below the floating search, never under it.
  // The jump is instant — a long smooth scroll can be cancelled mid-glide
  // and land halfway — with a delayed correction once layout settles.
  const scrollResultsToTop = () => {
    const jump = () => {
      const firstRow = root.querySelector("[data-item-row]:not([hidden])");
      const first =
        firstRow?.closest("[data-item-category-group]") ||
        firstRow ||
        (noResults && !noResults.hidden ? noResults : null);
      if (!first) return;
      // Reveal the first match inside its horizontal category rail.
      if (firstRow) {
        const track = firstRow.closest("[data-category-scroll-track]");
        if (track && track.scrollWidth > track.clientWidth) {
          const c = firstRow.getBoundingClientRect();
          const t = track.getBoundingClientRect();
          if (c.left < t.left + 4 || c.right > t.right - 4) {
            track.scrollLeft += c.left - t.left - 8;
          }
        }
      }
      const toolbar = floor.querySelector(".shop-floor-toolbar");
      const minTop =
        (toolbar ? toolbar.getBoundingClientRect().bottom : 0) + 8;
      const delta = first.getBoundingClientRect().top - minTop;
      if (Math.abs(delta) > 2) window.scrollTo({ top: window.scrollY + delta, behavior: "auto" });
    };
    jump();
    const settle = window.scrollY;
    window.setTimeout(() => {
      // Skip if the user grabbed the scroll in the meantime.
      if (Math.abs(window.scrollY - settle) < 4) jump();
    }, 220);
  };

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
    if (hasQuery) {
      scrollResultsToTop();
      if (!String(searchInput?.value || "").trim()) {
        const btn = ensureShowAllButton();
        if (btn) btn.hidden = false;
      }
    }
  };

  const syncUiAfterRender = () => {
    const visible = root.querySelectorAll("[data-item-row]").length;
    if (noResults) noResults.hidden = visible > 0 || (!activeQuery && totalCount === 0);
    if (root) root.hidden = visible === 0 && Boolean(activeQuery);
    updateCountLabel(visible || totalCount, Boolean(activeQuery));
    notifyRendered();
  };

  const loadCatalogFromCache = async (page, q) => {
    const store = await import("./offline/store.js");
    let data = await store.cacheGet(cacheKeyFor(page, q));
    let fromCache = Boolean(data?.ok);
    if (!fromCache && q) {
      data = await store.cacheGet(cacheKeyFor(page, ""));
      fromCache = Boolean(data?.ok);
      if (fromCache) data = { ...data, localFilter: q };
    }
    if (!fromCache) {
      throw new Error("offline_catalog_miss");
    }
    return { data, fromCache };
  };

  const fetchCatalogPage = async ({ page, q }) => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (q) params.set("q", q);
    const cacheKey = cacheKeyFor(page, q);
    const { isAppOnline } = await import("./offline/net.js");
    const online = await isAppOnline();

    let data = null;
    let fromCache = false;

    if (online) {
      try {
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
          await store.cacheSet(cacheKey, data, catalogCacheTtl);
        } catch (_cacheErr) {
          /* cache optional */
        }
      } catch (_networkErr) {
        ({ data, fromCache } = await loadCatalogFromCache(page, q));
      }
    } else {
      ({ data, fromCache } = await loadCatalogFromCache(page, q));
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

        const moreItems = Array.isArray(data.items) ? data.items : [];
        const ok = await appendItems(moreItems, { replace: false, seq });
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

      const firstItems = Array.isArray(data.items) ? data.items : [];
      // Fade the old catalog out before the swap so the collapse isn't a
      // visible jump; new sections fade/rise back in.
      if (!append && hasRenderedItems() && !reduceMotion()) {
        root.classList.add("is-swapping");
        await new Promise((resolve) => window.setTimeout(resolve, 140));
        if (seq !== inFlight) return;
      }
      const ok = await appendItems(firstItems, { replace: !append, seq });
      if (!ok || seq !== inFlight) return;

      if (fromCache) floor.setAttribute("data-catalog-from-cache", "1");
      else floor.removeAttribute("data-catalog-from-cache");

      const localFilter = String(data.localFilter || q || "");
      const { isAppOnline } = await import("./offline/net.js");
      const offline = !(await isAppOnline());
      if (offline && localFilter) {
        filterRenderedItems(localFilter);
      } else {
        syncUiAfterRender();
        if (!append && activeQuery) {
          scrollResultsToTop();
          // Results arrived after the box was already cleared (picked an
          // item mid-flight) — offer the way back to the full catalog.
          if (!String(searchInput?.value || "").trim()) {
            const btn = ensureShowAllButton();
            if (btn) btn.hidden = false;
          }
        }
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
        const { isAppOnline } = await import("./offline/net.js");
        const offline = !(await isAppOnline());
        root.innerHTML = offline
          ? '<div class="dashboard-placeholder"><p>Offline — open this shop online once to cache the catalog.</p></div>'
          : '<div class="dashboard-placeholder"><p>Could not load catalog. Try again.</p></div>';
      }
    } finally {
      if (seq === inFlight) {
        root.classList.remove("is-swapping");
        setLoading(false);
      }
    }
  };

  const reload = async (q = "") => {
    const { isAppOnline } = await import("./offline/net.js");
    const offline = !(await isAppOnline());
    if (offline && hasRenderedItems()) {
      if (!reduceMotion()) {
        root.classList.add("is-swapping");
        await new Promise((resolve) => window.setTimeout(resolve, 140));
      }
      filterRenderedItems(q);
      activeQuery = q;
      root.classList.remove("is-swapping");
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

  // After picking a searched item (open or add to cart), restore the full
  // catalog but keep that card at the same viewport spot — clear the box,
  // don't lose the place.
  const placeCardAtSpot = (card, keepTop) => {
    if (!card || keepTop == null) return;
    const rootEl = document.documentElement;
    const prev = rootEl.style.scrollBehavior;
    rootEl.style.scrollBehavior = "auto";
    // Reveal it inside its horizontal category rail if it scrolled away.
    const track = card.closest("[data-category-scroll-track]");
    if (track && track.scrollWidth > track.clientWidth) {
      const c = card.getBoundingClientRect();
      const t = track.getBoundingClientRect();
      if (c.left < t.left + 4 || c.right > t.right - 4) {
        track.scrollLeft +=
          c.left + c.width / 2 - (t.left + t.width / 2);
      }
    }
    const toolbar = floor.querySelector(".shop-floor-toolbar");
    const minTop =
      (toolbar ? toolbar.getBoundingClientRect().bottom : 0) + 6;
    const delta =
      card.getBoundingClientRect().top - Math.max(keepTop, minTop);
    if (Math.abs(delta) > 2) window.scrollBy(0, delta);
    rootEl.style.scrollBehavior = prev;
  };

  // Picking an item from a live search keeps the filtered list on screen —
  // no reload — so the user can keep working with the card. The box is
  // cleared for the next search; "Show all" restores the full catalog.
  let showAllBtn = null;
  const hideShowAllButton = () => {
    if (showAllBtn) showAllBtn.hidden = true;
  };

  const ensureShowAllButton = () => {
    if (showAllBtn) return showAllBtn;
    const toolbar = floor.querySelector(".shop-floor-toolbar");
    if (!toolbar) return null;
    showAllBtn = document.createElement("button");
    showAllBtn.type = "button";
    showAllBtn.className = "btn btn--ghost btn--sm shop-floor-show-all";
    showAllBtn.setAttribute("data-catalog-show-all", "");
    showAllBtn.innerHTML =
      '<i data-lucide="layout-grid" aria-hidden="true"></i><span>Show all</span>';
    showAllBtn.hidden = true;
    showAllBtn.addEventListener("click", () => {
      hideShowAllButton();
      if (floor.hasAttribute("data-catalog-local-filter")) {
        // Rows are only hidden — reveal them and keep the viewport put.
        const anchor = root.querySelector("[data-item-row]:not([hidden])");
        const keepTop = anchor ? anchor.getBoundingClientRect().top : null;
        filterRenderedItems("");
        activeQuery = "";
        placeCardAtSpot(anchor, keepTop);
        return;
      }
      activeQuery = "";
      reload("");
    });
    const anchor =
      visibleCountEl?.parentNode === toolbar ? visibleCountEl : null;
    if (anchor?.nextSibling) toolbar.insertBefore(showAllBtn, anchor.nextSibling);
    else toolbar.appendChild(showAllBtn);
    if (window.lucide?.createIcons) window.lucide.createIcons();
    return showAllBtn;
  };

  floor.addEventListener("click", (event) => {
    const trigger = event.target.closest(
      "[data-cart-preview], [data-cart-add]"
    );
    if (!trigger) return;
    const card = trigger.closest("[data-item-row]");
    if (!card || !searchInput) return;
    const filtering =
      Boolean(String(activeQuery || "").trim()) ||
      floor.hasAttribute("data-catalog-local-filter");
    window.clearTimeout(searchTimer);
    searchInput.value = "";
    if (!filtering) return;
    const btn = ensureShowAllButton();
    if (btn) btn.hidden = false;
  });

  searchInput?.addEventListener("input", () => {
    hideShowAllButton();
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      reload(String(searchInput.value || "").trim());
    }, 220);
  });
  searchInput?.addEventListener("search", () => {
    hideShowAllButton();
    window.clearTimeout(searchTimer);
    reload(String(searchInput.value || "").trim());
  });

  import("./offline/catalog-cache.js")
    .then((mod) => {
      mod.onCatalogInvalidate?.((detail) => {
        const scopes = detail?.scopes || ["all"];
        if (!scopes.includes("all") && !scopes.includes("shop")) return;
        if (detail?.shopId && String(detail.shopId) !== String(shopId)) return;

        const updates = Array.isArray(detail?.stockUpdates)
          ? detail.stockUpdates
          : [];
        if (updates.length) {
          updates.forEach((row) => {
            const id = String(row?.id || "");
            if (!id) return;
            const qty = Math.max(0, roundQty(row.quantity));
            root
              .querySelectorAll(
                `[data-cart-item][data-item-id="${CSS.escape(id)}"]`
              )
              .forEach((card) => {
                card.setAttribute("data-item-stock-base", String(qty));
                card.setAttribute("data-item-stock", String(qty));
                const valueEl = card.querySelector(".shop-floor-stock-value");
                const hidden = card.getAttribute("data-stock-hidden") === "1";
                if (valueEl && !hidden) valueEl.textContent = String(qty);
                const stockWrap = card.querySelector(".shop-floor-stock");
                if (stockWrap) {
                  if (hidden) stockWrap.hidden = true;
                  stockWrap.classList.toggle("is-empty", qty <= 0);
                }
                card.classList.toggle(
                  "is-out",
                  stockLimited &&
                    (card.getAttribute("data-item-sale-tracked") || "1") !==
                      "0" &&
                    qty <= 0
                );
              });
          });
          notifyRendered();
        }

        if (detail?.softReload === false) return;
        if (detail?.mode === "sale") return;
        import("./offline/net.js")
          .then(({ isAppOnline }) => isAppOnline())
          .then((online) => {
            if (online) reload(activeQuery);
          })
          .catch(() => {});
      });
    })
    .catch(() => {});

  reload("");
})();
