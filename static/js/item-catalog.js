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
  const sortButtons = panel.querySelectorAll("[data-item-sort]");
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
  let activeSort = "category";
  let inFlight = 0;
  let searchTimer = 0;
  let catalogShops = [];
  let shopColumnsOpen = false;
  const groupEls = new Map();
  let flatTbody = null;
  const canEdit = panel.dataset.canEdit !== "0";
  const canToggleSuspend = panel.dataset.canToggleSuspend !== "0";
  let catalogCacheTtl = 60 * 15;
  let lastFetchedPage = 0;
  let restoringScroll = false;
  const scrollStateKey = `item-catalog:scroll:${window.location.pathname}`;

  const saveScrollState = () => {
    if (restoringScroll) return;
    try {
      window.sessionStorage.setItem(
        scrollStateKey,
        JSON.stringify({
          y: Math.round(window.scrollY || window.pageYOffset || 0),
          pages: lastFetchedPage,
          q: activeQuery,
          sort: activeSort,
          t: Date.now(),
        })
      );
    } catch (_e) {
      /* storage optional */
    }
  };

  const readScrollState = () => {
    try {
      const state = JSON.parse(window.sessionStorage.getItem(scrollStateKey) || "null");
      if (!state || typeof state !== "object") return null;
      if (Date.now() - Number(state.t || 0) > 24 * 60 * 60 * 1000) return null;
      return state;
    } catch (_e) {
      return null;
    }
  };

  // <html> uses scroll-behavior:smooth — force instant jumps and keep
  // re-applying while late content (images, extra pages) grows the document.
  const applyScrollRestore = (y) => {
    const rootEl = document.documentElement;
    const jump = () => {
      const prev = rootEl.style.scrollBehavior;
      rootEl.style.scrollBehavior = "auto";
      window.scrollTo(0, y);
      rootEl.style.scrollBehavior = prev;
    };
    const finish = () => {
      restoringScroll = false;
      saveScrollState();
    };
    jump();
    let tries = 0;
    const timer = window.setInterval(() => {
      tries += 1;
      if (window.scrollY >= y - 8 || tries >= 14) {
        window.clearInterval(timer);
        window.removeEventListener("wheel", stopEarly);
        window.removeEventListener("touchstart", stopEarly);
        window.removeEventListener("keydown", stopEarly);
        finish();
        return;
      }
      jump();
    }, 150);
    const stopEarly = () => {
      window.clearInterval(timer);
      finish();
    };
    window.addEventListener("wheel", stopEarly, { once: true, passive: true });
    window.addEventListener("touchstart", stopEarly, {
      once: true,
      passive: true,
    });
    window.addEventListener("keydown", stopEarly, { once: true });
  };

  import("./offline/catalog-cache.js")
    .then((mod) => {
      if (mod?.CATALOG_CACHE_TTL_SECONDS) {
        catalogCacheTtl = mod.CATALOG_CACHE_TTL_SECONDS;
      }
    })
    .catch(() => {});

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

  const syncSortUi = () => {
    sortButtons.forEach((btn) => {
      const sort = btn.getAttribute("data-item-sort") || "category";
      const active = sort === activeSort;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
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

  const tableHeadHtml = ({ includeCategory }) => `
    <thead>
      <tr>
        <th scope="col" class="item-col--item">Item</th>
        ${
          includeCategory
            ? `<th scope="col" class="item-col--category">Category</th>`
            : ""
        }
        <th scope="col" class="item-col--range">Min price</th>
        ${shopPriceHeadHtml()}
        ${shopHeaderCells()}
        <th scope="col" class="item-col--status">Status</th>
        <th scope="col" class="item-col--actions">Actions</th>
      </tr>
    </thead>`;

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
          ${tableHeadHtml({ includeCategory: false })}
          <tbody></tbody>
        </table>
      </div>`;
    section.querySelector(".item-category-title").textContent = key;
    const after = [
      ...root.querySelectorAll("[data-item-category-group]"),
    ].find(
      (el) =>
        (
          el.querySelector(".item-category-title")?.textContent || ""
        ).localeCompare(key, undefined, { sensitivity: "base" }) > 0
    );
    root.insertBefore(section, after || null);
    groupEls.set(key, section);
    return section.querySelector("tbody");
  };

  const ensureFlatTable = () => {
    if (flatTbody) return flatTbody;
    const section = document.createElement("section");
    section.className = "item-category-group item-category-group--flat";
    section.setAttribute("data-item-flat-list", "");
    section.innerHTML = `
      <div class="item-table-wrap">
        <table class="item-table item-table--dense">
          ${tableHeadHtml({ includeCategory: true })}
          <tbody></tbody>
        </table>
      </div>`;
    root.appendChild(section);
    flatTbody = section.querySelector("tbody");
    return flatTbody;
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

  const buildRow = (item, { includeCategory }) => {
    const name = String(item.name || "");
    const category = String(item.category || "");
    const description = String(item.description || "");
    const descShort =
      description.length > 56 ? `${description.slice(0, 53).trimEnd()}…` : description;
    const imageUrl = String(item.image_url || "");
    const shopPrices = (() => {
      const rows = Array.isArray(item.shop_price_rows) ? item.shop_price_rows : [];
      if (rows.length) {
        const map = {};
        rows.forEach((row) => {
          map[String(row.shop_id)] = row.price;
        });
        return JSON.stringify(map);
      }
      return JSON.stringify(item.shop_prices || {});
    })();
    const tr = document.createElement("tr");
    if (item.is_suspended) tr.className = "item-row--suspended";
    tr.setAttribute("data-item-row", "");
    tr.setAttribute(
      "data-search-text",
      `${name} ${category} ${description}`.toLowerCase()
    );

    const thumbOnError =
      "this.onerror=null;this.replaceWith(Object.assign(document.createElement('span'),{className:'item-thumb item-thumb--empty',innerHTML:'<i data-lucide=\\'package\\'></i>'}));window.lucide&&window.lucide.createIcons&&window.lucide.createIcons();";
    const thumb = imageUrl
      ? `<img class="item-thumb" src="${escapeHtml(imageUrl)}" alt="" width="32" height="32" onerror="${thumbOnError}">`
      : `<span class="item-thumb item-thumb--empty" aria-hidden="true"><i data-lucide="package"></i></span>`;

    const categoryCell = includeCategory
      ? `<td class="item-cell--category" data-label="Category">${escapeHtml(
          category || "Uncategorised"
        )}</td>`
      : "";

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
      ${categoryCell}
      <td class="item-cell--range" data-label="Min price">
        <span class="item-price-range">
          <span class="item-price-range-bound item-price-range-bound--min">
            <span class="item-price-range-label">Min</span>
            <span class="item-price-range-amount">KSh ${escapeHtml(
              item.minimum_selling_price || "0.00"
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
            data-shop-price="${escapeHtml(item.shop_price || "")}"
            data-pricing-mode="${escapeHtml(item.pricing_mode || "single")}"
            data-shop-prices="${escapeHtml(shopPrices)}"
            data-sold-shops="${escapeHtml(JSON.stringify(item.sold_shops || {}))}"
            data-track-serial-number="${item.track_serial ? "1" : "0"}"
            data-image-url="${escapeHtml(imageUrl)}"
          >
            <i data-lucide="pencil" aria-hidden="true"></i>
          </button>`
              : ""
          }
          ${
            canToggleSuspend
              ? item.is_suspended
                ? `<button
            class="item-action-icon"
            type="button"
            disabled
            title="Suspended — unsuspend from the Suspended items page"
            aria-label="${escapeHtml(name)} is suspended"
          >
            <i data-lucide="pause" aria-hidden="true"></i>
          </button>`
                : `<form class="item-inline-form" method="post">
            <input type="hidden" name="csrfmiddlewaretoken" value="${escapeHtml(csrf)}">
            <input type="hidden" name="action" value="toggle_suspend">
            <input type="hidden" name="item_id" value="${item.id}">
            <button
              class="item-action-icon"
              type="submit"
              title="Suspend item"
              aria-label="Suspend ${escapeHtml(name)}"
            >
              <i data-lucide="pause" aria-hidden="true"></i>
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
      flatTbody = null;
      root.innerHTML = "";
    }
    const byName = activeSort === "name";
    items.forEach((item) => {
      const tbody = byName ? ensureFlatTable() : ensureGroup(item.category);
      tbody.appendChild(buildRow(item, { includeCategory: byName }));
    });
    if (!byName) refreshGroupCounts();
  };

  const cacheKeyFor = (page, q, sort) =>
    `item-catalog:p${page}:s${pageSize}:q${String(q || "").toLowerCase()}:sort${
      sort || "category"
    }`;

  const applyCatalogData = (data, { page, q, sort, append }) => {
    lastFetchedPage = append
      ? Math.max(lastFetchedPage, Number(page) || 0)
      : Number(page) || 1;
    totalCount = Number(data.total || 0);
    panel.dataset.itemTotal = String(totalCount);
    hasMore = Boolean(data.has_more);
    nextPage = data.next_page || null;
    activeQuery = String(data.q || q || "");
    activeSort = String(data.sort || sort || "category");
    syncSortUi();
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
    saveScrollState();
  };

  const fetchPage = async ({ page, q, sort, append }) => {
    const seq = ++inFlight;
    if (!append) {
      root.innerHTML =
        '<div class="dashboard-placeholder" data-item-catalog-loading><i data-lucide="loader-circle" aria-hidden="true"></i><p>Loading items…</p></div>';
    }
    if (moreBtn) moreBtn.disabled = true;

    const sortKey = sort || activeSort || "category";
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
      sort: sortKey,
    });
    if (q) params.set("q", q);
    const cacheKey = cacheKeyFor(page, q, sortKey);

    try {
      let data = null;
      let fromCache = false;
      const { isAppOnline } = await import("./offline/net.js");
      const online = await isAppOnline();

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
          const store = await import("./offline/store.js");
          data = await store.cacheGet(cacheKey);
          fromCache = Boolean(data?.ok);
          if (!fromCache) throw _networkErr;
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
      applyCatalogData(data, { page, q, sort: sortKey, append });
      if (fromCache) {
        panel.setAttribute("data-catalog-from-cache", "1");
      } else {
        panel.removeAttribute("data-catalog-from-cache");
      }

      if (online && !append && !fromCache && hasMore && nextPage) {
        const warmPage = nextPage;
        const warmQ = activeQuery;
        const warmSort = activeSort;
        const warmKey = cacheKeyFor(warmPage, warmQ, warmSort);
        const warmParams = new URLSearchParams({
          page: String(warmPage),
          page_size: String(pageSize),
          sort: warmSort,
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
              await store.cacheSet(warmKey, warmData, catalogCacheTtl);
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
          applyCatalogData(cached, { page, q, sort: sortKey, append });
          panel.setAttribute("data-catalog-from-cache", "1");
          return;
        }
      } catch (_cacheErr) {
        /* fall through */
      }
      if (!append) {
        const { isAppOnline } = await import("./offline/net.js");
        const offline = !(await isAppOnline());
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

  const reload = (q = activeQuery, sort = activeSort) => {
    groupEls.clear();
    flatTbody = null;
    nextPage = 1;
    hasMore = false;
    activeSort = sort;
    syncSortUi();
    fetchPage({ page: 1, q, sort, append: false });
  };

  moreBtn?.addEventListener("click", () => {
    if (!hasMore || !nextPage) return;
    fetchPage({ page: nextPage, q: activeQuery, sort: activeSort, append: true });
  });

  searchInput?.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      reload(String(searchInput.value || "").trim(), activeSort);
    }, 220);
  });
  searchInput?.addEventListener("search", () => {
    window.clearTimeout(searchTimer);
    reload(String(searchInput.value || "").trim(), activeSort);
  });

  sortButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const sort = btn.getAttribute("data-item-sort") || "category";
      if (sort === activeSort) return;
      reload(activeQuery, sort);
    });
  });

  panel.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-toggle-shop-cols]");
    if (!toggle || !panel.contains(toggle)) return;
    event.preventDefault();
    shopColumnsOpen = !shopColumnsOpen;
    syncShopColumnsUi();
  });

  syncSortUi();
  syncShopColumnsUi();

  let scrollSaveTimer = 0;
  window.addEventListener(
    "scroll",
    () => {
      if (scrollSaveTimer) return;
      scrollSaveTimer = window.setTimeout(() => {
        scrollSaveTimer = 0;
        saveScrollState();
      }, 200);
    },
    { passive: true }
  );
  window.addEventListener("pagehide", saveScrollState);
  window.addEventListener("beforeunload", saveScrollState);

  // A saved state for this URL means the user was just here (refresh,
  // back/forward, or the POST -> redirect -> GET round-trip after a form
  // action) — pick up where they left off.
  const initialState = readScrollState();
  if (initialState && "scrollRestoration" in history) {
    history.scrollRestoration = "manual";
  }
  if (!initialState) {
    try {
      window.sessionStorage.removeItem(scrollStateKey);
    } catch (_e) {
      /* storage optional */
    }
  }

  const boot = async () => {
    if (!initialState) {
      reload("", "category");
      return;
    }
    const q = String(initialState.q || "");
    const sort = initialState.sort === "name" ? "name" : "category";
    const y = Math.max(0, Number(initialState.y) || 0);
    const targetPages = Math.max(
      1,
      Math.min(Number(initialState.pages) || 1, 40)
    );
    if (searchInput) searchInput.value = q;
    groupEls.clear();
    flatTbody = null;
    nextPage = 1;
    hasMore = false;
    activeSort = sort;
    syncSortUi();
    restoringScroll = true;
    try {
      for (let p = 1; p <= targetPages; p += 1) {
        await fetchPage({ page: p, q, sort, append: p > 1 });
        if (lastFetchedPage !== p || !hasMore) break;
      }
    } catch (_e) {
      /* restore best-effort */
    }
    // Don't fight a server-rendered open modal (scroll is locked anyway).
    if (y > 0 && !document.body.classList.contains("workspace-modal-open")) {
      requestAnimationFrame(() => applyScrollRestore(y));
    } else {
      restoringScroll = false;
      saveScrollState();
    }
  };

  import("./offline/catalog-cache.js")
    .then((mod) => {
      mod.onCatalogInvalidate?.((detail) => {
        const scopes = detail?.scopes || ["all"];
        if (!scopes.includes("all") && !scopes.includes("item")) return;
        if (detail?.softReload === false) return;
        import("./offline/net.js")
          .then(({ isAppOnline }) => isAppOnline())
          .then((online) => {
            if (online) reload(activeQuery, activeSort);
          })
          .catch(() => {});
      });
    })
    .catch(() => {});

  // Mirrors items.services.item_text_search_q for client-side row filtering.
  const itemMatchesQuery = (item, query) => {
    const phrase = String(query || "").trim().toLowerCase();
    if (!phrase) return true;
    const name = String(item.name || "").toLowerCase();
    const category = String(item.category || "").toLowerCase();
    const description = String(item.description || "").toLowerCase();
    const tokens = phrase.split(/\s+/).filter(Boolean);
    if (tokens.length === 1) {
      const token = tokens[0];
      return (
        name.includes(token) ||
        category.includes(token) ||
        description.includes(token)
      );
    }
    if (name.includes(phrase)) return true;
    return tokens.every(
      (token) => name.includes(token) || category.includes(token)
    );
  };

  const insertRowSorted = (tbody, tr, name) => {
    const next = [...tbody.querySelectorAll("[data-item-row]")].find((row) => {
      const rowName =
        row.querySelector(".item-person-copy strong")?.textContent || "";
      return (
        rowName.localeCompare(String(name || ""), undefined, {
          sensitivity: "base",
        }) > 0
      );
    });
    if (next) tbody.insertBefore(tr, next);
    else tbody.appendChild(tr);
  };

  const removeRowById = (id) => {
    const trigger = [...root.querySelectorAll("[data-edit-item]")].find(
      (el) => el.dataset.itemId === String(id)
    );
    trigger?.closest("[data-item-row]")?.remove();
  };

  const refreshAfterRowChange = () => {
    if (activeSort !== "name") refreshGroupCounts();
    const visible = root.querySelectorAll("[data-item-row]").length;
    if (noResults) {
      noResults.hidden = visible > 0 || (!activeQuery && totalCount === 0);
    }
    root.hidden = visible === 0 && Boolean(activeQuery);
    updateCountLabel(visible || totalCount, Boolean(activeQuery));
    if (window.lucide?.createIcons) window.lucide.createIcons();
    saveScrollState();
  };

  // In-place row refresh after an edit that saved without a page reload.
  document.addEventListener("item-catalog:item-updated", (event) => {
    const item = event.detail?.item;
    if (!item || item.id == null) return;
    const byName = activeSort === "name";
    removeRowById(item.id);
    if (itemMatchesQuery(item, activeQuery)) {
      const tbody = byName ? ensureFlatTable() : ensureGroup(item.category);
      insertRowSorted(tbody, buildRow(item, { includeCategory: byName }), item.name);
    }
    refreshAfterRowChange();
  });

  // Drop a row whose item no longer exists server-side (stale cache/row).
  document.addEventListener("item-catalog:item-removed", (event) => {
    const id = event.detail?.id;
    if (id == null || id === "") return;
    removeRowById(id);
    refreshAfterRowChange();
  });

  boot();
})();
