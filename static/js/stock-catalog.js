(() => {
  const panel = document.querySelector("[data-stock-mode][data-stock-catalog-api]");
  if (!panel) return;

  const apiUrl = panel.getAttribute("data-stock-catalog-api") || "";
  const listRoot = panel.querySelector("[data-stock-catalog-root]");
  const parked = panel.querySelector("[data-stock-parked]");
  const moreWrap = panel.querySelector("[data-stock-catalog-more-wrap]");
  const moreBtn = panel.querySelector("[data-stock-catalog-more]");
  const searchInput = panel.querySelector("[data-item-search]");
  const browseBtn = panel.querySelector("[data-stock-catalog-browse]");
  const noResults = panel.querySelector("[data-item-no-results]");
  const visibleCountEl = panel.querySelector("[data-item-visible-count]");
  const addAnotherBtn = panel.querySelector("[data-stock-add-another]");
  const pickerTools = () => panel.querySelectorAll("[data-stock-picker-tools]");
  if (!apiUrl || !listRoot) return;

  const mode = panel.dataset.stockMode || "in";
  const shopId = panel.dataset.stockCatalogShop || "";
  const fromShopId = panel.dataset.stockCatalogFromShop || "";
  const shopName = panel.dataset.stockCatalogShopName || "Shop";
  const fromShopName = panel.dataset.stockCatalogFromShopName || "From shop";
  let catalogShopIds = [];
  try {
    const parsedIds = JSON.parse(panel.dataset.stockCatalogShopIds || "[]");
    if (Array.isArray(parsedIds)) {
      catalogShopIds = parsedIds
        .map((id) => String(id || "").trim())
        .filter(Boolean);
    }
  } catch (_err) {
    catalogShopIds = [];
  }
  if (!catalogShopIds.length && shopId) catalogShopIds = [String(shopId)];
  let viewShops = [];
  try {
    viewShops = JSON.parse(panel.dataset.stockCatalogShops || "[]");
    if (!Array.isArray(viewShops)) viewShops = [];
  } catch (_err) {
    viewShops = [];
  }
  const editableMatrix =
    panel.hasAttribute("data-stock-editable-matrix") &&
    (mode === "in" || mode === "out" || mode === "request") &&
    viewShops.length > 0;
  // View (and legacy browse) use a read-only multi-shop matrix.
  const browseAllShops =
    !editableMatrix &&
    (mode === "view" ||
      ((mode === "in" || mode === "out" || mode === "request") && !shopId)) &&
    viewShops.length > 0;
  const showAllShops =
    (editableMatrix || browseAllShops) && viewShops.length > 1;
  const readOnlyMatrix = browseAllShops;
  const multiShopMatrix = editableMatrix || readOnlyMatrix;
  const pageSize = Math.min(
    Math.max(Number(panel.dataset.stockCatalogPageSize || 48) || 48, 12),
    96
  );

  let totalCount = Number(panel.dataset.itemTotal || 0) || 0;
  let nextPage = 1;
  let hasMore = false;
  let activeQuery = "";
  let inFlight = 0;
  let searchTimer = 0;
  let abortController = null;
  const groupEls = new Map();

  const setBusy = (busy) => {
    if (busy) panel.setAttribute("data-stock-catalog-busy", "1");
    else panel.removeAttribute("data-stock-catalog-busy");
    document.dispatchEvent(
      new CustomEvent("stock-catalog:busy", { detail: { busy: Boolean(busy) } })
    );
  };

  const escapeHtml = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const compactShopName = (full, maxLen = 10) => {
    const name = String(full || "Shop").trim() || "Shop";
    if (name.length <= maxLen) return name;
    const parts = name.split(/\s+/).filter(Boolean);
    if (parts.length >= 2 && parts[0].length <= maxLen) return parts[0];
    if (parts.length >= 2) {
      return parts
        .slice(0, 4)
        .map((part) => part[0] || "")
        .join("")
        .toUpperCase();
    }
    return `${name.slice(0, Math.max(maxLen - 1, 1)).trim()}…`;
  };

  const money = (value) => {
    if (value == null || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? String(Math.round(n)) : null;
  };

  const simpleMode = panel.hasAttribute("data-stock-catalog-simple");
  const searchFirst = panel.hasAttribute("data-stock-catalog-search-first");
  // Buy-stock popup: preload once, then filter in memory (no DB per keystroke).
  const localFilter =
    simpleMode &&
    searchFirst &&
    panel.hasAttribute("data-stock-catalog-local-filter");
  const parkedWrap = panel.querySelector(".buy-stock-simple-parked");
  let browseOpen = false;
  let pickerCollapsed = false;
  const itemCache = new Map();
  const memoryCache = new Map();
  let offlineStorePromise = null;
  let localCatalog = null;
  let localFiltered = null;
  let localVisibleLimit = pageSize;
  let localCatalogPromise = null;
  let localRevalidating = false;
  let lastRenderKey = "";
  let localRenderFrame = 0;
  const PRELOAD_PAGE_SIZE = 500;

  const getOfflineStore = () => {
    if (!offlineStorePromise) {
      offlineStorePromise = import("./offline/store.js").catch(() => null);
    }
    return offlineStorePromise;
  };

  const cacheKeyFor = (page, q) =>
    `stock-catalog:${catalogShopIds.join("-") || shopId || "all"}:${fromShopId || "0"}:${mode}:p${page}:s${pageSize}:q${String(
      q || ""
    ).toLowerCase()}`;

  // Search keys are computed once per item so keystrokes only do substring tests.
  const indexItem = (item) => {
    if (!item || item.__searchName != null) return item;
    item.__searchName = String(item.name || "").toLowerCase();
    item.__searchCategory = String(item.category || "").toLowerCase();
    item.__searchDescription = String(item.description || "").toLowerCase();
    return item;
  };

  const filterLocalItems = (source, q) => {
    const phrase = String(q || "")
      .trim()
      .toLowerCase();
    if (!phrase) return source.slice();
    const tokens = phrase.split(/\s+/).filter(Boolean);
    if (tokens.length === 1) {
      const token = tokens[0];
      return source.filter(
        (item) =>
          item.__searchName.includes(token) ||
          item.__searchCategory.includes(token) ||
          item.__searchDescription.includes(token)
      );
    }
    return source.filter((item) => {
      if (item.__searchName.includes(phrase)) return true;
      return tokens.every(
        (token) =>
          item.__searchName.includes(token) ||
          item.__searchCategory.includes(token)
      );
    });
  };

  const rememberItems = (items) => {
    (items || []).forEach((item) => {
      if (item?.id != null) itemCache.set(String(item.id), indexItem(item));
    });
  };

  const applyLocalSlice = ({ append = false, force = false } = {}) => {
    const source = Array.isArray(localFiltered) ? localFiltered : [];
    const slice = source.slice(0, localVisibleLimit);
    // Typing often lands on the same result set (narrowing a unique match,
    // backspacing, etc.) — skip the whole DOM pass when nothing would change.
    // The DOM counts are part of the key so parking/removing an item still
    // forces a reconcile even when the query is unchanged.
    const parkedKey =
      parked?.querySelectorAll("[data-item-row][data-item-id]").length ?? 0;
    const domKey =
      groupEls
        .get("__simple__")
        ?.querySelector("[data-stock-catalog-tbody]")?.childElementCount ?? -1;
    const renderKey = `${source.length}:${parkedKey}:${domKey}:${slice
      .map((item) => item.id)
      .join(",")}`;
    if (!force && !append && renderKey === lastRenderKey) return;
    lastRenderKey = renderKey;
    hasMore = source.length > localVisibleLimit;
    nextPage = hasMore ? Math.floor(localVisibleLimit / pageSize) + 1 : null;
    activeQuery = String(activeQuery || "");
    if (Array.isArray(localCatalog)) {
      totalCount = localCatalog.length;
      panel.dataset.itemTotal = String(totalCount);
    }
    appendItems(slice, { replace: !append });
    const parkedCount = parked?.querySelectorAll("[data-item-row]").length || 0;
    const shown = Math.min(source.length, localVisibleLimit);
    if (noResults) {
      const idle = searchFirst && !activeQuery && !browseOpen;
      noResults.hidden =
        idle || shown + parkedCount > 0 || (!activeQuery && totalCount === 0);
    }
    if (moreWrap) moreWrap.hidden = !hasMore;
    if (activeQuery) {
      const savedTotal = totalCount;
      totalCount = source.length;
      updateCount(shown + parkedCount || source.length, true);
      totalCount = savedTotal;
    } else {
      updateCount(shown + parkedCount || totalCount, false);
    }
    notify();
  };

  const preloadKeyFor = (page) =>
    `stock-catalog-preload:${catalogShopIds.join("-") || shopId || "all"}:${mode}:p${page}:s${PRELOAD_PAGE_SIZE}`;

  const absorbPreloadPage = (data, into) => {
    const items = Array.isArray(data?.items) ? data.items : [];
    into.push(...items);
    rememberItems(items);
    if (Array.isArray(data?.shops) && data.shops.length && multiShopMatrix) {
      viewShops = data.shops;
    }
  };

  const fetchPreloadPage = async (page) => {
    const params = buildFetchParams(page, "");
    params.set("page_size", String(PRELOAD_PAGE_SIZE));
    const response = await fetch(`${apiUrl}?${params.toString()}`, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data?.ok) throw new Error(data?.error || "preload_failed");
    memoryCache.set(preloadKeyFor(page), data);
    getOfflineStore().then((store) => {
      if (store?.cacheSet) store.cacheSet(preloadKeyFor(page), data, 60 * 60 * 12);
    });
    return data;
  };

  const readPreloadFromCache = async () => {
    const all = [];
    let page = 1;
    let guard = 0;
    while (guard++ < 40) {
      const key = preloadKeyFor(page);
      let data = memoryCache.get(key);
      if (!data?.ok) {
        const store = await getOfflineStore();
        data = store?.cacheGet ? await store.cacheGet(key) : null;
        if (data?.ok) memoryCache.set(key, data);
      }
      if (!data?.ok) return null;
      absorbPreloadPage(data, all);
      if (!data.has_more) break;
      page = Number(data.next_page) || page + 1;
    }
    return all.length ? all : null;
  };

  const fetchPreloadFromNetwork = async () => {
    const all = [];
    let page = 1;
    let guard = 0;
    while (guard++ < 40) {
      const data = await fetchPreloadPage(page);
      absorbPreloadPage(data, all);
      if (!data.has_more) break;
      page = Number(data.next_page) || page + 1;
    }
    return all;
  };

  const catalogSignature = (items) =>
    Array.isArray(items)
      ? `${items.length}:${items
          .map((item) => `${item.id}|${item.shop_qty}|${item.last_buying_price || ""}`)
          .join(",")}`
      : "";

  const setLocalCatalog = (items) => {
    items.forEach(indexItem);
    localCatalog = items;
    totalCount = items.length;
    panel.dataset.itemTotal = String(totalCount);
  };

  const revalidateLocalCatalog = () => {
    if (localRevalidating) return;
    const online = typeof navigator === "undefined" || navigator.onLine;
    if (!online) return;
    localRevalidating = true;
    const before = catalogSignature(localCatalog);
    fetchPreloadFromNetwork()
      .then((items) => {
        if (!items.length) return;
        const changed = catalogSignature(items) !== before;
        setLocalCatalog(items);
        // Only re-render when the data actually moved, so a background refresh
        // never yanks the list out from under someone mid-edit.
        if (changed && (activeQuery || browseOpen)) {
          localFiltered = filterLocalItems(items, activeQuery);
          applyLocalSlice({ append: false, force: true });
        }
      })
      .catch(() => {})
      .finally(() => {
        localRevalidating = false;
      });
  };

  // Cache-first: serve the stored catalog instantly, refresh in the background.
  const ensureLocalCatalog = () => {
    if (!localFilter) return Promise.resolve(null);
    if (Array.isArray(localCatalog)) return Promise.resolve(localCatalog);
    if (localCatalogPromise) return localCatalogPromise;

    localCatalogPromise = (async () => {
      const cached = await readPreloadFromCache().catch(() => null);
      if (cached) {
        setLocalCatalog(cached);
        revalidateLocalCatalog();
        return localCatalog;
      }
      setLocalCatalog(await fetchPreloadFromNetwork());
      return localCatalog;
    })().catch((err) => {
      localCatalogPromise = null;
      throw err;
    });

    return localCatalogPromise;
  };

  const reloadFromLocal = async (q = "", { forceBrowse = false } = {}) => {
    const query = String(q || "").trim();
    if (pickerCollapsed && !forceBrowse && !query) return;
    if (query || forceBrowse) setPickerCollapsed(false);
    if (searchFirst && !query && !forceBrowse && !browseOpen) {
      abortController?.abort();
      parkFilled();
      showIdleHint();
      setBusy(false);
      return;
    }
    if (!query && (forceBrowse || browseOpen)) setBrowseOpen(true);

    const warm = Array.isArray(localCatalog);
    if (!warm) {
      if (simpleMode) showCatalogLoading();
      setBusy(true);
    }
    try {
      const all = await ensureLocalCatalog();
      localFiltered = filterLocalItems(all, query);
      localVisibleLimit = pageSize;
      activeQuery = query;
      applyLocalSlice({ append: false });
    } catch (_err) {
      // Fall back to server search if preload fails.
      await fetchPage({ page: 1, q: query, append: false });
    } finally {
      if (!warm) setBusy(false);
    }
  };

  // Coalesce keystrokes into at most one render per animation frame.
  const scheduleLocalRender = (query) => {
    if (localRenderFrame) window.cancelAnimationFrame(localRenderFrame);
    localRenderFrame = window.requestAnimationFrame(() => {
      localRenderFrame = 0;
      reloadFromLocal(query);
    });
  };

  // Result rows are rebuilt on every keystroke, so ship the SVG inline instead
  // of asking lucide to convert dozens of <i data-lucide> placeholders per frame.
  const svgIcon = (name, body) =>
    `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-${name}" aria-hidden="true">${body}</svg>`;
  const ICON_X = svgIcon("x", '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>');
  const ICON_PLUS = svgIcon("plus", '<path d="M5 12h14"/><path d="M12 5v14"/>');

  const refreshIcons = (root = panel) => {
    if (!window.lucide?.createIcons) return;
    if (root && root !== panel && root instanceof Element) {
      // Reused rows keep their rendered SVGs — only pay for unconverted icons.
      if (!root.querySelector("[data-lucide]")) return;
      window.lucide.createIcons({ root });
      return;
    }
    window.lucide.createIcons();
  };

  const showCatalogLoading = () => {
    if (pickerCollapsed || !listRoot) return;
    lastRenderKey = "";
    listRoot.innerHTML = `
      <div class="buy-stock-catalog-loading" data-stock-catalog-loading aria-live="polite">
        <span class="buy-stock-catalog-loading-bar" aria-hidden="true"></span>
        <p>Loading items…</p>
      </div>`;
  };

  const buildSimplePickInputsHtml = (item) => {
    const fields = mode === "in" ? buildInFields(item) : buildOutFields(item);
    return `
      <div class="buy-stock-pick-inputs" data-stock-item-inputs hidden>
        <div class="stock-item-inputs stock-item-inputs--matrix">
          <input type="hidden" name="item_id" value="${item.id}" disabled data-stock-field>
          ${fields}
        </div>
      </div>`;
  };

  const ensurePickInputs = (row) => {
    if (!row || !simpleMode) return Boolean(getInputsRowFromRow(row));
    if (row.querySelector(":scope > [data-stock-item-inputs]")) return true;
    const item = itemCache.get(String(row.getAttribute("data-item-id") || ""));
    if (!item) return false;
    row.insertAdjacentHTML("beforeend", buildSimplePickInputsHtml(item));
    refreshIcons(row);
    return true;
  };

  const getInputsRowFromRow = (row) => {
    if (!row) return null;
    const nested = row.querySelector(":scope > [data-stock-item-inputs]");
    if (nested) return nested;
    const next = row.nextElementSibling;
    return next?.matches?.("[data-stock-item-inputs]") ? next : null;
  };

  panel.ensurePickInputs = ensurePickInputs;

  const syncAddAnother = () => {
    if (!addAnotherBtn) return;
    const hasSelected = Boolean(parked?.querySelector("[data-item-row]"));
    addAnotherBtn.hidden = !hasSelected || !pickerCollapsed;
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const setPickerCollapsed = (collapsed) => {
    if (!simpleMode) return;
    pickerCollapsed = Boolean(collapsed);
    panel.classList.toggle("is-picker-collapsed", pickerCollapsed);
    pickerTools().forEach((el) => {
      el.hidden = pickerCollapsed;
    });
    if (pickerCollapsed) {
      setBrowseOpen(false);
      if (moreWrap) moreWrap.hidden = true;
      if (noResults) noResults.hidden = true;
      if (visibleCountEl) visibleCountEl.hidden = true;
    }
    syncAddAnother();
  };

  const setBrowseOpen = (open) => {
    browseOpen = Boolean(open);
    if (!browseBtn) return;
    browseBtn.classList.toggle("is-open", browseOpen);
    browseBtn.setAttribute("aria-expanded", String(browseOpen));
    browseBtn.setAttribute(
      "aria-label",
      browseOpen ? "Hide item list" : "Browse items"
    );
    browseBtn.title = browseOpen ? "Hide item list" : "Browse all items";
  };

  const updateCount = (visible, query) => {
    if (!visibleCountEl) return;
    if (query) {
      visibleCountEl.textContent = `${visible} of ${totalCount} item${
        totalCount === 1 ? "" : "s"
      }`;
      visibleCountEl.hidden = false;
    } else if (simpleMode) {
      visibleCountEl.textContent = "";
      visibleCountEl.hidden = true;
    } else {
      visibleCountEl.textContent = `${totalCount} item${totalCount === 1 ? "" : "s"}`;
      visibleCountEl.hidden = false;
    }
  };

  const isFilledPair = (headerRow) => {
    if (!headerRow) return false;
    // Keep the row the user just moved into when search clears after a promote.
    if (
      document.activeElement instanceof Element &&
      headerRow.contains(document.activeElement)
    ) {
      return true;
    }
    if (editableMatrix) {
      return [...headerRow.querySelectorAll("[data-stock-shop-cell]")].some(
        (cell) => Number(cell.querySelector("[data-stock-qty]")?.value || 0) > 0
      );
    }
    if (
      headerRow.classList.contains("is-open") ||
      headerRow.classList.contains("is-selected")
    ) {
      return true;
    }
    const inputs =
      headerRow.querySelector(":scope > [data-stock-item-inputs]") ||
      headerRow.nextElementSibling;
    if (!inputs?.matches?.("[data-stock-item-inputs]")) return false;
    const qty = Number(inputs.querySelector("[data-stock-qty]")?.value || 0);
    return qty > 0;
  };

  // Simple picker parks rows explicitly on Add, so only rows carrying state can
  // ever need moving — scanning every result row on each keystroke is wasted.
  const PARK_CANDIDATE_SELECTOR = simpleMode
    ? "[data-item-row][data-item-id].is-filled, [data-item-row][data-item-id].is-selected, [data-item-row][data-item-id].is-open"
    : "[data-item-row][data-item-id]";

  const parkFilled = () => {
    if (!parked) return;
    listRoot.querySelectorAll(PARK_CANDIDATE_SELECTOR).forEach((row) => {
      if (!isFilledPair(row)) return;
      if (parked.contains(row)) return;
      if (editableMatrix) {
        parked.appendChild(row);
        return;
      }
      const nestedInputs = row.querySelector(":scope > [data-stock-item-inputs]");
      if (nestedInputs) {
        parked.appendChild(row);
      } else {
        const inputs = row.nextElementSibling;
        parked.appendChild(row);
        if (inputs?.matches?.("[data-stock-item-inputs]")) parked.appendChild(inputs);
      }
    });
    if (parkedWrap) {
      parkedWrap.hidden = !parked.querySelector("[data-item-row]");
    }
    syncAddAnother();
  };

  const ensureSelectedGroup = () => {
    if (!editableMatrix || !multiShopMatrix) return null;
    const key = "__selected__";
    let section = groupEls.get(key);
    if (section) {
      if (listRoot.firstElementChild !== section) {
        listRoot.insertBefore(section, listRoot.firstElementChild);
      }
      section.hidden = false;
      return section;
    }
    section = ensureGroup("__selected__");
    const title = section.querySelector(".stock-category-title");
    if (title) title.textContent = "Items with quantity";
    section.setAttribute("data-stock-filled-group", "");
    if (listRoot.firstElementChild !== section) {
      listRoot.insertBefore(section, listRoot.firstElementChild);
    }
    return section;
  };

  const restoreParkedToTop = () => {
    if (!parked) return;
    const parkedRows = [...parked.querySelectorAll("[data-item-row][data-item-id]")];
    if (!parkedRows.length) {
      if (parkedWrap) parkedWrap.hidden = true;
      const parkedTable = parked.closest("table");
      if (parkedTable) parkedTable.hidden = true;
      return;
    }

    if (editableMatrix && multiShopMatrix) {
      const section = ensureSelectedGroup();
      const tbody = section?.querySelector("[data-stock-catalog-tbody]");
      if (tbody) {
        parkedRows.forEach((row) => {
          row.classList.add("is-filled");
          tbody.appendChild(row);
        });
      }
      const parkedTable = parked.closest("table");
      if (parkedTable) parkedTable.hidden = true;
      return;
    }

    if (parkedWrap) {
      parkedWrap.hidden = false;
      const list = parkedWrap.parentElement;
      if (list && listRoot && parkedWrap.nextElementSibling !== listRoot) {
        list.insertBefore(parkedWrap, listRoot);
      }
    }
  };

  const sortFilledRowsInPlace = () => {
    listRoot.querySelectorAll("[data-stock-catalog-tbody]").forEach((tbody) => {
      const rows = [...tbody.querySelectorAll(":scope > [data-item-row]")];
      if (rows.length < 2) return;
      const filled = rows.filter((row) => isFilledPair(row) || row.classList.contains("is-filled"));
      const blank = rows.filter((row) => !filled.includes(row));
      [...filled, ...blank].forEach((row) => tbody.appendChild(row));
    });
  };

  const ensureGroup = (category) => {
    if (simpleMode) {
      let section = groupEls.get("__simple__");
      if (section) return section;
      section = document.createElement("div");
      section.className = "buy-stock-pick-group";
      section.setAttribute("data-item-category-group", "");
      section.innerHTML = `<div data-stock-catalog-tbody class="buy-stock-pick-stack"></div>`;
      listRoot.appendChild(section);
      groupEls.set("__simple__", section);
      return section;
    }
    const key = category || "Uncategorised";
    let section = groupEls.get(key);
    if (section) return section;
    section = document.createElement("section");
    section.className = readOnlyMatrix
      ? "stock-category stock-category--view"
      : "stock-category";
    section.setAttribute("data-item-category-group", "");

    if (multiShopMatrix) {
      const shopHeaders = editableMatrix
        ? viewShops
            .map((shop) => {
              const name = escapeHtml(shop.name || "Shop");
              const shopIdAttr = escapeHtml(String(shop.id || ""));
              if (mode === "in") {
                return `<th scope="col" class="stock-matrix-shop-col stock-th--pair" title="${name}">
                  <span class="stock-th-pair">
                    <span class="stock-th-pair-name">${name}</span>
                    <span class="stock-th-pair-cols" aria-hidden="true"><span>Stock</span><span>Qty</span><span>Buy</span></span>
                  </span>
                </th>`;
              }
              if (mode === "request") {
                const pairLocked = panel.hasAttribute("data-stock-request-pair");
                const requestingId = String(
                  panel.dataset.stockCatalogShop || ""
                ).trim();
                const isRequesting =
                  requestingId && String(shop.id) === requestingId;
                const roleLabel = isRequesting ? "Requesting" : "From";
                if (pairLocked) {
                  return `<th
                  scope="col"
                  class="stock-matrix-shop-col stock-th--pair stock-th--request${
                    isRequesting ? " is-requesting" : ""
                  }"
                  title="${name} · ${roleLabel}"
                  data-stock-request-shop-header
                  data-shop-id="${shopIdAttr}"
                  data-shop-name="${name}"
                >
                  <span class="stock-th-pair">
                    <span class="stock-th-pair-name">${name}</span>
                    <span class="stock-th-pair-role" data-stock-request-role>${roleLabel}</span>
                    <span class="stock-th-pair-cols" aria-hidden="true"><span>Stock</span><span>Qty</span></span>
                  </span>
                </th>`;
                }
                return `<th
                  scope="col"
                  class="stock-matrix-shop-col stock-th--pair stock-th--request"
                  title="Click to set ${name} as requesting shop"
                  data-stock-request-shop-header
                  data-shop-id="${shopIdAttr}"
                  data-shop-name="${name}"
                  tabindex="0"
                  role="button"
                >
                  <span class="stock-th-pair">
                    <span class="stock-th-pair-name">${name}</span>
                    <span class="stock-th-pair-role" data-stock-request-role>From</span>
                    <span class="stock-th-pair-cols" aria-hidden="true"><span>Stock</span><span>Qty</span></span>
                  </span>
                </th>`;
              }
              return `<th scope="col" class="stock-matrix-shop-col stock-th--pair" title="${name}">
                <span class="stock-th-pair">
                  <span class="stock-th-pair-name">${name}</span>
                  <span class="stock-th-pair-cols stock-th-pair-cols--out" aria-hidden="true"><span>Stock</span><span>Qty</span><span>Balance</span></span>
                </span>
              </th>`;
            })
            .join("")
        : viewShops
            .map((shop, index) => {
              const full = escapeHtml(shop.name || "Shop");
              const label = escapeHtml(compactShopName(shop.name));
              const bandStart = index === 0 ? " stock-th--band-start" : "";
              return `<th scope="col" class="stock-matrix-shop-col stock-th--pair stock-th--band-flow${bandStart}" title="${full}">
                <span class="stock-th-pair">
                  <span class="stock-th-pair-name">${label}</span>
                  <span class="stock-th-pair-cols" aria-hidden="true"><span>Qty</span></span>
                </span>
              </th>`;
            })
            .join("");
      const totalHeader = editableMatrix
        ? ""
        : showAllShops
          ? `<th scope="col" class="stock-matrix-total-col stock-th--pair stock-th--band-total stock-th--band-start">
              <span class="stock-th-pair">
                <span class="stock-th-pair-name">Total</span>
                <span class="stock-th-pair-cols" aria-hidden="true"><span>Qty</span></span>
              </span>
            </th>`
          : "";
      const matrixClass = [
        "stock-matrix",
        showAllShops || editableMatrix ? "stock-matrix--all" : "stock-matrix--single",
        editableMatrix ? "stock-matrix--editable stock-matrix--list" : "",
        readOnlyMatrix ? "stock-matrix--view stock-matrix--list" : "",
      ]
        .filter(Boolean)
        .join(" ");
      section.innerHTML = `
      <header class="stock-category-head">
        <div class="stock-category-title-wrap">
          <span class="stock-category-mark" aria-hidden="true"></span>
          <h3 class="stock-category-title"></h3>
        </div>
        <span class="stock-category-count" data-category-count>0</span>
      </header>
      <div class="stock-matrix-scroll${
        editableMatrix || readOnlyMatrix ? " stock-matrix-scroll--list" : ""
      }">
        <table class="${matrixClass}">
          <thead>
            <tr>
              <th scope="col" class="stock-matrix-item-col">Item</th>
              ${shopHeaders}
              ${totalHeader}
            </tr>
          </thead>
          <tbody data-stock-catalog-tbody></tbody>
          ${
            readOnlyMatrix
              ? `<tfoot data-category-subtotals hidden>
            <tr class="stock-matrix-row stock-matrix-row--subtotal">
              <th scope="row" class="stock-matrix-item-col">Category total</th>
            </tr>
          </tfoot>`
              : ""
          }
        </table>
      </div>`;
      section.querySelector(".stock-category-title").textContent = key;
      section.dataset.colCount = String(
        1 + viewShops.length + (editableMatrix || !showAllShops ? 0 : 1)
      );
      listRoot.appendChild(section);
      groupEls.set(key, section);
      return section;
    }

    const colCount = mode === "request" ? 4 : mode === "out" ? 4 : 3;
    section.innerHTML = `
      <header class="stock-category-head">
        <div class="stock-category-title-wrap">
          <span class="stock-category-mark" aria-hidden="true"></span>
          <h3 class="stock-category-title"></h3>
        </div>
        <span class="stock-category-count" data-category-count>0</span>
      </header>
      <div class="stock-matrix-scroll">
        <table class="stock-matrix stock-matrix--single stock-matrix--action${
          mode === "request" ? " stock-matrix--request" : ""
        }">
          <thead>
            <tr>
              <th scope="col" class="stock-matrix-item-col">Item</th>
              <th scope="col" class="stock-matrix-shop-col">${escapeHtml(shopName)}</th>
              ${
                mode === "request"
                  ? `<th scope="col" class="stock-matrix-shop-col">${escapeHtml(
                      fromShopName
                    )}</th>`
                  : ""
              }
              ${
                mode === "out"
                  ? `<th scope="col" class="stock-matrix-shop-col stock-matrix-balance-col">Balance</th>`
                  : ""
              }
              <th scope="col" class="stock-matrix-action-col"><span class="visually-hidden">Action</span></th>
            </tr>
          </thead>
          <tbody data-stock-catalog-tbody></tbody>
        </table>
      </div>
    `;
    section.querySelector(".stock-category-title").textContent = key;
    section.dataset.colCount = String(colCount);
    listRoot.appendChild(section);
    groupEls.set(key, section);
    return section;
  };

  const buildSerialBlock = (item) => {
    if (!item.track_serial || mode === "request") {
      return `<input type="hidden" name="serial_numbers" value="" data-stock-field disabled>`;
    }
    const entryWrap =
      mode === "out"
        ? `<div class="stock-serial-entry-wrap" data-serial-search-root data-serial-scan-continuous>
            <div class="stock-serial-row">
              <div class="stock-serial-input-wrap">
                <input
                  type="text"
                  placeholder="Search serial to stock out"
                  autocomplete="off"
                  spellcheck="false"
                  data-stock-serial-entry
                  data-stock-serial-input
                  data-serial-search
                  data-stock-field
                  disabled
                >
              </div>
            </div>
            <div class="stock-supplier-suggest" data-serial-suggest hidden></div>
          </div>`
        : `<div class="stock-serial-entry-wrap" data-serial-scan-continuous>
            <div class="stock-serial-row has-serial-scan">
              <div class="stock-serial-input-wrap">
                <input
                  type="text"
                  placeholder="Scan or type serial number"
                  autocomplete="off"
                  spellcheck="false"
                  data-stock-serial-entry
                  data-stock-serial-input
                  data-stock-field
                  disabled
                >
              </div>
            </div>
          </div>`;
    return `
      <div class="stock-inline-field stock-inline-field--serial">
        <div class="stock-serial-head">
          <span>${simpleMode ? "Serial" : "Serial number"}</span>
          <span class="visually-hidden">Remove</span>
        </div>
        ${entryWrap}
        <ul class="stock-serial-scanned" data-stock-serial-scanned aria-live="polite" hidden></ul>
        <small class="stock-serial-hint">${
          mode === "out"
            ? "Scan or search serials — press Enter after each."
            : "Scan continuously — press Enter after each serial."
        }</small>
        <input type="hidden" name="serial_numbers" value="" data-stock-serials data-stock-field disabled>
      </div>`;
  };

  const buildInFields = (item) => {
    const prev = money(item.last_buying_price);
    const qtyLabel = mode === "in" ? "Qty bought" : "Qty to stock in";
    const qtyBlock = item.track_serial
      ? `<div class="stock-inline-field">
          <span>${qtyLabel}</span>
          <strong class="stock-serial-qty-live stock-serial-qty-live--field">
            <span data-stock-serial-count>0</span>
          </strong>
          <input type="hidden" name="quantity" value="" data-stock-qty data-stock-field disabled>
        </div>`
      : `<label class="stock-inline-field">
          <span>${qtyLabel}</span>
          <input type="number" name="quantity" min="1" step="1" placeholder="0" inputmode="numeric" data-stock-qty data-stock-field disabled>
        </label>`;
    const priceBlock = `<label class="stock-inline-field">
          <span class="stock-inline-label-row">
            <span>Unit buying price</span>
            ${
              prev
                ? `<em class="stock-prev-price">Prev ${prev}</em>`
                : `<em class="stock-prev-price is-empty">No previous</em>`
            }
          </span>
          <input
            type="number"
            name="buying_price"
            min="0"
            step="1"
            placeholder="${prev || "0"}"
            inputmode="numeric"
            data-stock-buying-price
            data-stock-field
            ${prev ? `data-stock-prev-buying="${prev}"` : ""}
            disabled
          >
        </label>`;
    const supplierHidden = `
      <input type="hidden" name="supplier_id" value="" data-stock-supplier-id data-stock-field disabled>
      <input type="hidden" name="supplier_phone_country_code" value="+254" data-stock-supplier-dial data-stock-field disabled>
      <input type="hidden" value="KE" data-stock-supplier-iso disabled>
      <input type="hidden" name="supplier_phone_number" value="" data-stock-supplier-phone data-stock-field disabled>
      <input type="hidden" name="supplier_name" value="" data-stock-supplier-name data-stock-field disabled>
      <input type="hidden" name="payment_status" value="" data-stock-payment data-stock-field disabled>
    `;
    if (simpleMode) {
      const qtyBlockSimple = item.track_serial
        ? `<div class="stock-inline-field">
          <span>Qty</span>
          <strong class="stock-serial-qty-live stock-serial-qty-live--field">
            <span data-stock-serial-count>0</span>
          </strong>
          <input type="hidden" name="quantity" value="" data-stock-qty data-stock-field disabled>
        </div>`
        : `<label class="stock-inline-field">
          <span>Qty</span>
          <input type="number" name="quantity" min="1" step="1" placeholder="0" inputmode="numeric" data-stock-qty data-stock-field disabled>
        </label>`;
      const priceBlockSimple = `<label class="stock-inline-field stock-inline-field--buy">
          <span>Unit buy</span>
          <span class="stock-buy-price-wrap${prev ? " has-prev" : ""}">
            <input
              type="number"
              name="buying_price"
              min="0"
              step="1"
              placeholder="${prev || " "}"
              inputmode="numeric"
              data-stock-buying-price
              data-stock-field
              ${prev ? `data-stock-prev-buying="${prev}"` : ""}
              disabled
            >
            ${
              prev
                ? `<span class="stock-buy-price-ghost" aria-hidden="true"><span class="stock-buy-price-ghost-label">Prev</span><span class="stock-buy-price-ghost-value">${prev}</span></span>`
                : ""
            }
          </span>
        </label>`;
      return `
      <div class="stock-in-field-row buy-stock-pick-fields">
        ${qtyBlockSimple}
        ${priceBlockSimple}
      </div>
      ${supplierHidden}
      ${buildSerialBlock(item)}`;
    }
    return `
      <div class="stock-in-field-row">
        ${qtyBlock}
        ${priceBlock}
      </div>
      <div class="stock-in-field-row stock-in-field-row--supplier">
        <input type="hidden" name="supplier_id" value="" data-stock-supplier-id data-stock-field disabled>
        <label class="stock-inline-field stock-inline-field--phone">
          <span>Supplier phone</span>
          <div class="stock-phone-field" data-stock-phone-field data-supplier-search-root>
            <input type="hidden" name="supplier_phone_country_code" value="+254" data-stock-supplier-dial data-stock-field disabled>
            <input type="hidden" value="KE" data-stock-supplier-iso disabled>
            <button type="button" class="stock-country-trigger" data-stock-country-trigger aria-label="Select country" aria-haspopup="listbox" aria-expanded="false">
              <img class="flag-img" src="https://flagcdn.com/w40/ke.png" width="20" height="15" alt="" data-stock-flag-img>
            </button>
            <span class="stock-phone-dial" data-stock-dial-display>+254</span>
            <input type="tel" name="supplier_phone_number" placeholder="7XXXXXXXX" inputmode="numeric" maxlength="9" autocomplete="tel-national" data-stock-supplier-phone data-supplier-search="phone" data-stock-field disabled>
            <div class="stock-supplier-suggest" data-supplier-suggest hidden></div>
          </div>
        </label>
        <label class="stock-inline-field">
          <span>Supplier name</span>
          <div class="stock-supplier-name-wrap" data-supplier-search-root>
            <input type="text" name="supplier_name" placeholder="Type name to search…" autocomplete="organization" data-uppercase data-stock-supplier-name data-supplier-search="name" data-stock-field disabled>
            <div class="stock-supplier-suggest" data-supplier-suggest hidden></div>
          </div>
        </label>
        <label class="stock-inline-field">
          <span>Payment status</span>
          <select name="payment_status" data-stock-payment data-stock-field disabled>
            <option value="">Select</option>
            <option value="unpaid">Unpaid</option>
            <option value="paid">Paid</option>
            <option value="partial">Partial</option>
          </select>
        </label>
      </div>
      ${buildSerialBlock(item)}`;
  };

  const buildOutFields = (item) => {
    const qtyBlock = item.track_serial
      ? `<div class="stock-inline-field">
          <span>Qty to stock out</span>
          <strong class="stock-serial-qty-live stock-serial-qty-live--field"><span data-stock-serial-count>0</span></strong>
          <input type="hidden" name="quantity" value="" data-stock-qty data-stock-field disabled>
        </div>`
      : `<label class="stock-inline-field">
          <span>Qty to stock out</span>
          <input type="number" name="quantity" min="1" step="1" placeholder="0" inputmode="numeric" data-stock-qty data-stock-field disabled>
        </label>`;
    return `
      <div class="stock-in-field-row">
        ${qtyBlock}
      </div>
      <div class="stock-in-field-row stock-in-field-row--out-meta">
        <label class="stock-inline-field">
          <span>Reason</span>
          <select name="reason" data-stock-reason data-stock-field disabled>
            <option value="">Select</option>
            <option value="waste">Waste</option>
            <option value="transfer">Transfer</option>
            <option value="display">Display</option>
            <option value="return">Supplier return</option>
          </select>
        </label>
        <label class="stock-inline-field">
          <span>Refund</span>
          <select name="refund" data-stock-refund data-stock-field disabled>
            <option value="">Select</option>
            <option value="no">No</option>
            <option value="yes">Yes</option>
          </select>
        </label>
        <label class="stock-inline-field" data-stock-refund-amount-wrap hidden>
          <span>Refund amount</span>
          <input
            type="number"
            name="refund_amount"
            min="0"
            step="1"
            placeholder="0"
            inputmode="numeric"
            data-stock-refund-amount
            data-stock-field
            disabled
          >
        </label>
      </div>
      ${buildSerialBlock(item)}`;
  };

  const buildRequestFields = () => `
    <label class="stock-inline-field">
      <span>Qty to request</span>
      <input type="number" name="quantity" min="1" step="1" placeholder="0" inputmode="numeric" data-stock-qty data-stock-field disabled>
    </label>
    <input type="hidden" name="serial_numbers" value="" data-stock-field disabled>`;

  const sellingPriceFor = (item, index) => {
    if (Array.isArray(item.selling_prices) && item.selling_prices[index] != null) {
      return String(item.selling_prices[index] || "");
    }
    return String(item.selling_price || "");
  };

  const buildShopCell = (item, shop, stockQty, sellingPrice = "") => {
    const prev = money(item.last_buying_price);
    const track = item.track_serial && mode !== "request" ? "1" : "0";
    const shopLabel = escapeHtml(shop.name || "Shop");
    const qtyControl =
      mode === "request" || !item.track_serial
        ? `<input
          type="number"
          class="stock-list-input"
          name="quantity"
          min="1"
          step="1"
          placeholder="0"
          inputmode="numeric"
          aria-label="Quantity at ${shopLabel}"
          data-stock-qty
        >`
        : `<input
          type="text"
          class="stock-list-input stock-list-input--serial"
          name="quantity"
          value=""
          placeholder="0"
          inputmode="numeric"
          readonly
          data-stock-qty
          data-stock-serial-open
          data-stock-serial-count
          aria-label="Enter serials for quantity at ${shopLabel}"
          title="Click to enter serial numbers"
        >`;
    const priceBlock =
      mode === "in"
        ? `<input
            type="number"
            class="stock-list-input stock-list-input--price"
            name="buying_price"
            min="0"
            step="1"
            placeholder="${prev || "0"}"
            inputmode="numeric"
            aria-label="Unit buying price at ${shopLabel}"
            title="${prev ? `Previous unit ${prev}` : "Unit buying price"}"
            data-stock-buying-price
            ${prev ? `data-stock-prev-buying="${prev}"` : ""}
          >`
        : mode === "out"
          ? `<input type="hidden" name="reason" value="" data-stock-reason data-stock-field disabled>
           <input type="hidden" name="refund" value="" data-stock-refund data-stock-field disabled>
           <input type="hidden" name="refund_amount" value="" data-stock-refund-amount data-stock-field disabled>`
          : "";
    const supplierHidden =
      mode === "in"
        ? `<input type="hidden" name="supplier_id" value="" data-stock-supplier-id data-stock-field disabled>
           <input type="hidden" name="supplier_phone_country_code" value="+254" data-stock-supplier-dial data-stock-field disabled>
           <input type="hidden" value="KE" data-stock-supplier-iso disabled>
           <input type="hidden" name="supplier_phone_number" value="" data-stock-supplier-phone data-stock-field disabled>
           <input type="hidden" name="supplier_name" value="" data-stock-supplier-name data-stock-field disabled>
           <input type="hidden" name="payment_status" value="" data-stock-payment data-stock-field disabled>`
        : "";
    const pairClass =
      mode === "in"
        ? "stock-list-pair stock-list-pair--in"
        : "stock-list-pair stock-list-pair--out";
    const balanceBlock =
      mode === "out"
        ? `<span class="stock-list-balance" data-stock-display-balance hidden aria-hidden="true">—</span>`
        : "";
    return `<td class="stock-matrix-shop-col stock-matrix-shop-col--edit">
      <div
        class="stock-shop-cell ${pairClass}"
        data-stock-shop-cell
        data-item-id="${item.id}"
        data-item-name="${escapeHtml(item.name || "")}"
        data-selling-price="${escapeHtml(sellingPrice || item.selling_price || "")}"
        data-shop-id="${shop.id}"
        data-shop-name="${shopLabel}"
        data-item-stock="${stockQty}"
        data-track-serial="${track}"
      >
        <span class="stock-list-stock${stockQty === 0 ? " is-empty" : ""}" data-stock-display-qty>${stockQty}</span>
        <input type="hidden" name="item_id" value="${item.id}" disabled data-stock-field>
        <input type="hidden" name="line_shop_id" value="${shop.id}" disabled data-stock-field>
        ${qtyControl}
        ${balanceBlock}
        ${priceBlock}
        <input type="hidden" name="serial_numbers" value="" data-stock-serials data-stock-field disabled>
        ${supplierHidden}
      </div>
    </td>`;
  };

  const buildPair = (item) => {
    const stock = Math.max(0, Math.floor(Number(item.shop_qty) || 0));
    const fromQty = Math.max(0, Math.floor(Number(item.requested_from_qty) || 0));
    const track =
      item.track_serial && mode !== "request" ? "1" : "0";
    const name = String(item.name || "");
    const category = String(item.category || "");
    const desc = String(item.description || "");
    const colCount = mode === "request" ? 4 : mode === "out" ? 4 : 3;

    if (editableMatrix) {
      const quantities = Array.isArray(item.shop_quantities)
        ? item.shop_quantities.map((q) => Math.max(0, Math.floor(Number(q) || 0)))
        : viewShops.map(() => stock);
      const cells = viewShops
        .map((shop, index) =>
          buildShopCell(item, shop, quantities[index] || 0, sellingPriceFor(item, index))
        )
        .join("");
      const header = document.createElement("tr");
      header.className = `stock-matrix-row stock-matrix-row--editable${
        item.is_suspended ? " is-suspended" : ""
      }`;
      header.setAttribute("data-item-row", "");
      header.setAttribute("data-item-id", String(item.id));
      header.setAttribute("data-item-name", name);
      header.setAttribute("data-selling-price", String(item.selling_price || ""));
      header.setAttribute("data-track-serial", track);
      header.setAttribute(
        "data-search-text",
        `${name} ${category} ${desc}`.toLowerCase()
      );
      header.innerHTML = `
        <th scope="row" class="stock-matrix-item-col">
          <div class="stock-matrix-item">
            <strong>${escapeHtml(name)}</strong>
            ${item.is_suspended ? '<span class="stock-item-badge">Suspended</span>' : ""}
            ${
              item.track_serial
                ? '<span class="stock-item-badge stock-item-badge--serial">Serial</span>'
                : ""
            }
          </div>
        </th>
        ${cells}`;
      return [header];
    }

    if (readOnlyMatrix) {
      const quantities = Array.isArray(item.shop_quantities)
        ? item.shop_quantities.map((q) => Math.max(0, Math.floor(Number(q) || 0)))
        : [stock];
      const rowTotal = Number.isFinite(Number(item.row_total))
        ? Math.max(0, Math.floor(Number(item.row_total) || 0))
        : quantities.reduce((sum, q) => sum + q, 0);
      const qtyCells = showAllShops
        ? quantities
            .map(
              (qty) =>
                `<td class="stock-matrix-shop-col"><span class="stock-matrix-qty${
                  qty === 0 ? " is-empty" : ""
                }">${qty === 0 ? "—" : qty}</span></td>`
            )
            .join("") +
          `<td class="stock-matrix-total-col"><span class="stock-matrix-qty stock-matrix-qty--total${
            rowTotal === 0 ? " is-empty" : ""
          }">${rowTotal === 0 ? "—" : rowTotal}</span></td>`
        : `<td class="stock-matrix-shop-col"><span class="stock-matrix-qty${
            (quantities[0] || 0) === 0 ? " is-empty" : ""
          }">${(quantities[0] || 0) === 0 ? "—" : quantities[0] || 0}</span></td>`;
      const header = document.createElement("tr");
      header.className = `stock-matrix-row${
        item.is_suspended ? " is-suspended" : ""
      }`;
      header.setAttribute("data-item-row", "");
      header.setAttribute(
        "data-search-text",
        `${name} ${category} ${desc}`.toLowerCase()
      );
      header.innerHTML = `
        <th scope="row" class="stock-matrix-item-col">
          <div class="stock-matrix-item">
            <strong>${escapeHtml(name)}</strong>
            ${
              item.track_serial
                ? '<span class="stock-item-badge stock-item-badge--serial">Serial</span>'
                : ""
            }
          </div>
        </th>
        ${qtyCells}`;
      return [header];
    }

    if (simpleMode) {
      const header = document.createElement("article");
      header.className = `buy-stock-pick${
        item.is_suspended ? " is-suspended" : ""
      }${stock === 0 ? " is-empty-stock" : ""}`;
      header.setAttribute("data-item-row", "");
      header.setAttribute("data-item-id", String(item.id));
      header.setAttribute("data-item-name", name);
      header.setAttribute("data-item-stock", String(stock));
      header.setAttribute("data-selling-price", String(item.selling_price || ""));
      header.setAttribute("data-track-serial", track);
      header.setAttribute(
        "data-search-text",
        `${name} ${category} ${desc}`.toLowerCase()
      );
      header.innerHTML = `
        <div class="buy-stock-pick-head">
          <div class="buy-stock-pick-copy">
            <div class="buy-stock-pick-title">
              <button
                type="button"
                class="buy-stock-pick-name"
                data-stock-item-toggle
                aria-label="Add ${escapeHtml(name)}"
              >
                <strong>${escapeHtml(name)}</strong>
              </button>
              <button
                type="button"
                class="buy-stock-pick-remove"
                data-stock-item-remove
                aria-label="Remove ${escapeHtml(name)}"
                title="Remove item"
                hidden
              >
                ${ICON_X}
              </button>
            </div>
            <p class="buy-stock-pick-meta">
              ${escapeHtml(category || "Item")}
              · in shop ${stock}
              ${item.track_serial ? " · Serial" : ""}
              ${item.is_suspended ? " · Suspended" : ""}
            </p>
          </div>
          <button
            type="button"
            class="buy-stock-pick-select"
            data-stock-item-toggle
            aria-label="Add ${escapeHtml(name)}"
          >
            <span>Add</span>
            ${ICON_PLUS}
          </button>
        </div>`;
      return [header];
    }

    const header = document.createElement("tr");
    header.className = `stock-matrix-row stock-matrix-row--action${
      item.is_suspended ? " is-suspended" : ""
    }${stock === 0 ? " is-empty-stock" : ""}`;
    header.setAttribute("data-item-row", "");
    header.setAttribute("data-item-id", String(item.id));
    header.setAttribute("data-item-name", name);
    header.setAttribute("data-item-stock", String(stock));
    header.setAttribute("data-selling-price", String(item.selling_price || ""));
    header.setAttribute("data-track-serial", track);
    header.setAttribute(
      "data-search-text",
      `${name} ${category} ${desc}`.toLowerCase()
    );
    header.innerHTML = `
      <th scope="row" class="stock-matrix-item-col">
        <button type="button" class="stock-matrix-toggle" data-stock-item-toggle>
          <div class="stock-matrix-item">
            <strong>${escapeHtml(name)}</strong>
            ${item.is_suspended ? '<span class="stock-item-badge">Suspended</span>' : ""}
            ${
              item.track_serial
                ? '<span class="stock-item-badge stock-item-badge--serial">Serial</span>'
                : ""
            }
          </div>
        </button>
      </th>
      <td class="stock-matrix-shop-col">
        <span class="stock-matrix-qty${stock === 0 ? " is-empty" : ""}" data-stock-display-qty>${stock}</span>
      </td>
      ${
        mode === "request"
          ? `<td class="stock-matrix-shop-col"><span class="stock-matrix-qty${
              fromQty === 0 ? " is-empty" : ""
            }">${fromQty}</span></td>`
          : ""
      }
      ${
        mode === "out"
          ? `<td class="stock-matrix-shop-col stock-matrix-balance-col"><span class="stock-matrix-qty stock-matrix-balance" data-stock-display-balance hidden aria-hidden="true">—</span></td>`
          : ""
      }
      <td class="stock-matrix-action-col">
        <button type="button" class="stock-matrix-chevron" data-stock-item-toggle aria-label="Open ${escapeHtml(
          name
        )}">
          <i data-lucide="chevron-down" aria-hidden="true"></i>
        </button>
      </td>`;

    const formRow = document.createElement("tr");
    formRow.className = "stock-matrix-form-row";
    formRow.setAttribute("data-stock-item-inputs", "");
    formRow.hidden = true;
    let fields = "";
    if (mode === "in") fields = buildInFields(item);
    else if (mode === "out") fields = buildOutFields(item);
    else fields = buildRequestFields();
    formRow.innerHTML = `
      <td colspan="${colCount}">
        <div class="stock-item-inputs stock-item-inputs--matrix">
          <input type="hidden" name="item_id" value="${item.id}" disabled data-stock-field>
          ${fields}
        </div>
      </td>`;
    return [header, formRow];
  };

  const refreshGroupCounts = () => {
    groupEls.forEach((section) => {
      const count = section.querySelectorAll("[data-item-row]").length;
      const el = section.querySelector("[data-category-count]");
      if (el) el.textContent = String(count);
      section.hidden = count === 0;
    });
    refreshCategorySubtotals();
  };

  const parseQtyCell = (cell) => {
    const text = cell?.querySelector(".stock-matrix-qty")?.textContent?.trim() || "";
    if (!text || text === "—") return 0;
    const value = Number(String(text).replace(/,/g, ""));
    return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
  };

  const formatQty = (value) => (value === 0 ? "—" : value.toLocaleString());

  const refreshCategorySubtotals = () => {
    if (!readOnlyMatrix) return;
    const shopCount = showAllShops ? viewShops.length : 1;
    groupEls.forEach((section) => {
      const tfoot = section.querySelector("[data-category-subtotals]");
      const row = tfoot?.querySelector("tr");
      if (!tfoot || !row) return;

      const rows = [...section.querySelectorAll("tbody [data-item-row]")].filter(
        (itemRow) => !itemRow.hidden
      );
      if (!rows.length) {
        tfoot.hidden = true;
        return;
      }

      const sums = Array.from({ length: shopCount }, () => 0);
      let grandTotal = 0;

      rows.forEach((itemRow) => {
        const shopCells = itemRow.querySelectorAll(".stock-matrix-shop-col");
        shopCells.forEach((cell, index) => {
          sums[index] = (sums[index] || 0) + parseQtyCell(cell);
        });
        const totalCell = itemRow.querySelector(".stock-matrix-total-col");
        if (totalCell) {
          grandTotal += parseQtyCell(totalCell);
        } else if (shopCells.length === 1) {
          grandTotal += parseQtyCell(shopCells[0]);
        }
      });

      let cellsHtml = `<th scope="row" class="stock-matrix-item-col">Category total</th>`;
      sums.forEach((sum) => {
        cellsHtml += `<td class="stock-matrix-shop-col"><span class="stock-matrix-qty stock-matrix-qty--subtotal${
          sum === 0 ? " is-empty" : ""
        }">${formatQty(sum)}</span></td>`;
      });
      if (showAllShops) {
        cellsHtml += `<td class="stock-matrix-total-col"><span class="stock-matrix-qty stock-matrix-qty--subtotal stock-matrix-qty--total${
          grandTotal === 0 ? " is-empty" : ""
        }">${formatQty(grandTotal)}</span></td>`;
      }

      row.innerHTML = cellsHtml;
      tfoot.hidden = false;
    });
  };

  // Simple picker: reconcile the existing rows against the new result set so
  // live search reuses untouched rows instead of re-parsing the whole list.
  const syncSimpleRows = (items) => {
    parkFilled();
    // The idle hint / loading placeholder replaces listRoot's contents, so make
    // sure we are reconciling against a group that is actually mounted.
    let section = groupEls.get("__simple__");
    if (!section || section.parentElement !== listRoot) {
      groupEls.delete("__simple__");
      listRoot.innerHTML = "";
      section = ensureGroup("");
    }
    [...listRoot.children].forEach((child) => {
      if (child !== section) child.remove();
    });
    const tbody = section.querySelector("[data-stock-catalog-tbody]");
    if (!tbody) return;
    const parkedIds = new Set(
      [...(parked?.querySelectorAll("[data-item-row][data-item-id]") || [])].map(
        (row) => row.getAttribute("data-item-id")
      )
    );
    const desired = items.filter((item) => !parkedIds.has(String(item.id)));
    const desiredIds = new Set(desired.map((item) => String(item.id)));

    const existing = new Map();
    [...tbody.children].forEach((node) => {
      const id = node.getAttribute?.("data-item-id");
      if (!id || !desiredIds.has(id)) {
        node.remove();
        return;
      }
      existing.set(id, node);
    });

    desired.forEach((item, index) => {
      itemCache.set(String(item.id), indexItem(item));
      const node =
        existing.get(String(item.id)) || buildPair(item).filter(Boolean)[0];
      if (!node) return;
      const current = tbody.children[index];
      if (current !== node) tbody.insertBefore(node, current || null);
    });

    restoreParkedToTop();
    // No sortFilledRowsInPlace here: filled rows live in the parked stack, so
    // reordering would re-append every result row on every keystroke.
    refreshGroupCounts();
  };

  const appendItems = (items, { replace }) => {
    if (simpleMode && replace) {
      syncSimpleRows(items);
      return;
    }
    if (replace) {
      parkFilled();
      groupEls.clear();
      listRoot.innerHTML = "";
    }
    const parkedIds = new Set(
      [...(parked?.querySelectorAll("[data-item-row][data-item-id]") || [])].map(
        (row) => row.getAttribute("data-item-id")
      )
    );
    // Also skip ids already restored into the selected group from a prior pass.
    listRoot.querySelectorAll("[data-stock-filled-group] [data-item-row][data-item-id]").forEach((row) => {
      parkedIds.add(row.getAttribute("data-item-id"));
    });
    if (replace) restoreParkedToTop();
    items.forEach((item) => {
      itemCache.set(String(item.id), item);
      if (parkedIds.has(String(item.id))) return;
      const section = ensureGroup(item.category);
      const tbody = section.querySelector("[data-stock-catalog-tbody]");
      const nodes = buildPair(item).filter(Boolean);
      tbody.append(...nodes);
    });
    sortFilledRowsInPlace();
    refreshGroupCounts();
  };

  const notify = () => {
    document.dispatchEvent(new CustomEvent("stock-catalog:rendered"));
    refreshIcons(listRoot);
  };

  const showLoadError = ({ append, q }) => {
    if (append) return;
    lastRenderKey = "";
    groupEls.clear();
    listRoot.innerHTML = `
      <div class="dashboard-placeholder" data-stock-catalog-error>
        <i data-lucide="wifi-off" aria-hidden="true"></i>
        <p>Could not load items.</p>
        <button type="button" class="btn btn--ghost" data-stock-catalog-retry>Try again</button>
      </div>`;
    if (window.lucide?.createIcons) window.lucide.createIcons();
    listRoot
      .querySelector("[data-stock-catalog-retry]")
      ?.addEventListener("click", () => reload(q || activeQuery));
  };

  const buildFetchParams = (page, q) => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
      mode,
    });
    if (catalogShopIds.length) {
      catalogShopIds.forEach((id) => params.append("shop_id", id));
    } else if (shopId) {
      params.set("shop_id", shopId);
    }
    if (mode === "request" && fromShopId) {
      params.set("requested_from_shop_id", fromShopId);
    }
    if (q) params.set("q", q);
    return params;
  };

  const warmCatalogPage = async ({ page = 1, q = "" } = {}) => {
    const cacheKey = cacheKeyFor(page, q);
    if (memoryCache.has(cacheKey)) return;
    try {
      const response = await fetch(`${apiUrl}?${buildFetchParams(page, q).toString()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data?.ok) return;
      memoryCache.set(cacheKey, data);
      getOfflineStore().then((store) => {
        if (store?.cacheSet) store.cacheSet(cacheKey, data, 60 * 60 * 12);
      });
    } catch (_err) {
      /* optional warm-up */
    }
  };

  const applyCatalogData = (data, { q, append }) => {
    totalCount = Number(data.total || 0);
    panel.dataset.itemTotal = String(totalCount);
    hasMore = Boolean(data.has_more);
    nextPage = data.next_page || null;
    activeQuery = String(data.q || q || "");
    if (Array.isArray(data.shops) && data.shops.length && multiShopMatrix) {
      viewShops = data.shops;
    }
    appendItems(Array.isArray(data.items) ? data.items : [], { replace: !append });

    const visible = listRoot.querySelectorAll("[data-item-row]").length;
    const parkedCount = parked?.querySelectorAll("[data-item-row]").length || 0;
    if (noResults) {
      const idle = searchFirst && !activeQuery && !browseOpen;
      noResults.hidden =
        idle || visible + parkedCount > 0 || (!activeQuery && totalCount === 0);
    }
    if (moreWrap) moreWrap.hidden = !hasMore;
    updateCount(visible + parkedCount || totalCount, Boolean(activeQuery));
    notify();
  };

  const fetchPage = async ({ page, q, append }) => {
    const seq = ++inFlight;
    if (!append) {
      abortController?.abort();
      abortController = new AbortController();
    }
    const signal = append ? undefined : abortController?.signal;
    if (moreBtn) moreBtn.disabled = true;
    setBusy(true);

    const cacheKey = cacheKeyFor(page, q);
    const online = typeof navigator === "undefined" || navigator.onLine;
    const memHit = memoryCache.get(cacheKey);

    if (memHit?.ok) {
      if (seq !== inFlight) return;
      applyCatalogData(memHit, { q, append });
      panel.removeAttribute("data-catalog-from-cache");
      setBusy(false);
      if (moreBtn) moreBtn.disabled = false;
      if (online && !append && memHit.has_more && memHit.next_page) {
        warmCatalogPage({ page: memHit.next_page, q: memHit.q || q || "" });
      }
      return;
    }

    try {
      let data = null;
      let fromCache = false;

      if (online) {
        const response = await fetch(`${apiUrl}?${buildFetchParams(page, q).toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
          signal,
        });
        data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) throw new Error(data.error || "failed");
        memoryCache.set(cacheKey, data);
        getOfflineStore().then((store) => {
          if (store?.cacheSet) store.cacheSet(cacheKey, data, 60 * 60 * 12);
        });
      } else {
        const store = await getOfflineStore();
        data = store ? await store.cacheGet(cacheKey) : null;
        fromCache = Boolean(data?.ok);
        if (!fromCache) throw new Error("offline_catalog_miss");
      }

      if (seq !== inFlight) return;
      applyCatalogData(data, { q, append });
      if (fromCache) panel.setAttribute("data-catalog-from-cache", "1");
      else panel.removeAttribute("data-catalog-from-cache");

      if (online && !append && !fromCache && hasMore && nextPage) {
        warmCatalogPage({ page: nextPage, q: activeQuery });
      }
    } catch (err) {
      if (err?.name === "AbortError") return;
      if (seq !== inFlight) return;
      const cached = memoryCache.get(cacheKey);
      if (cached?.ok) {
        applyCatalogData(cached, { q, append });
        panel.setAttribute("data-catalog-from-cache", "1");
        return;
      }
      try {
        const store = await getOfflineStore();
        const idbCached = store ? await store.cacheGet(cacheKey) : null;
        if (seq === inFlight && idbCached?.ok) {
          memoryCache.set(cacheKey, idbCached);
          applyCatalogData(idbCached, { q, append });
          panel.setAttribute("data-catalog-from-cache", "1");
          return;
        }
      } catch (_cacheErr) {
        /* fall through */
      }
      showLoadError({ append, q });
      if (moreWrap) moreWrap.hidden = true;
    } finally {
      if (seq === inFlight) {
        setBusy(false);
        if (moreBtn) moreBtn.disabled = false;
      }
    }
  };

  const showIdleHint = () => {
    if (!simpleMode || !searchFirst) return;
    setBrowseOpen(false);
    lastRenderKey = "";
    groupEls.clear();
    const hasParked = Boolean(parked?.querySelector("[data-item-row]"));
    listRoot.innerHTML = hasParked
      ? `
      <div class="buy-stock-simple-empty buy-stock-simple-empty--compact" data-stock-catalog-idle>
        <p>Search to add another item</p>
      </div>`
      : `
      <div class="buy-stock-simple-empty buy-stock-simple-empty-card" data-stock-catalog-idle>
        <span class="buy-stock-simple-empty-icon" aria-hidden="true">
          <i data-lucide="package-search"></i>
        </span>
        <p>Type a name to add items</p>
      </div>`;
    if (moreWrap) moreWrap.hidden = true;
    if (noResults) noResults.hidden = true;
    updateCount(0, "");
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const resetLiveSearch = ({ focusSearch = false } = {}) => {
    window.clearTimeout(searchTimer);
    abortController?.abort();
    if (searchInput) searchInput.value = "";
    parkFilled();
    showIdleHint();
    setBusy(false);
    setPickerCollapsed(false);
    if (focusSearch) searchInput?.focus();
  };

  const openPicker = ({ browse = false } = {}) => {
    setPickerCollapsed(false);
    if (browse) {
      if (searchInput) searchInput.value = "";
      setBrowseOpen(true);
      showCatalogLoading();
      reload("", { forceBrowse: true }).then(() => {
        searchInput?.focus();
        if (window.lucide?.createIcons) window.lucide.createIcons();
      });
      return;
    }
    showIdleHint();
    searchInput?.focus();
  };

  const reload = (q = "", { forceBrowse = false } = {}) => {
    if (localFilter) {
      return reloadFromLocal(q, { forceBrowse });
    }
    const query = String(q || "").trim();
    if (pickerCollapsed && !forceBrowse && !query) {
      return Promise.resolve();
    }
    if (query || forceBrowse) setPickerCollapsed(false);
    if (searchFirst && !query && !forceBrowse && !browseOpen) {
      abortController?.abort();
      parkFilled();
      showIdleHint();
      setBusy(false);
      return Promise.resolve();
    }
    if (!query && (forceBrowse || browseOpen)) setBrowseOpen(true);
    if (simpleMode) showCatalogLoading();
    return fetchPage({ page: 1, q: query, append: false });
  };

  moreBtn?.addEventListener("click", () => {
    if (!hasMore || panel.hasAttribute("data-stock-catalog-busy")) return;
    if (localFilter && Array.isArray(localFiltered)) {
      localVisibleLimit += pageSize;
      applyLocalSlice({ append: false });
      return;
    }
    if (!nextPage) return;
    fetchPage({ page: nextPage, q: activeQuery, append: true });
  });

  browseBtn?.addEventListener("click", () => {
    setPickerCollapsed(false);
    const query = String(searchInput?.value || "").trim();
    if (browseOpen && !query) {
      abortController?.abort();
      parkFilled();
      showIdleHint();
      setBusy(false);
      return;
    }
    if (searchInput && query) searchInput.value = "";
    setBrowseOpen(true);
    showCatalogLoading();
    reload("", { forceBrowse: true }).then(() => {
      listRoot.scrollIntoView({ behavior: "smooth", block: "nearest" });
      if (window.lucide?.createIcons) window.lucide.createIcons();
    });
  });

  addAnotherBtn?.addEventListener("click", () => {
    openPicker({ browse: false });
  });

  searchInput?.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    const query = String(searchInput.value || "").trim();
    const warmLocal = localFilter && Array.isArray(localCatalog);
    if (query) {
      setPickerCollapsed(false);
      setBrowseOpen(false);
      // Local filter is instant — skip the loading flash once catalog is warm.
      if (!warmLocal) showCatalogLoading();
    }
    if (warmLocal) {
      // No debounce needed: filtering is in memory and render is frame-batched.
      scheduleLocalRender(query);
      return;
    }
    searchTimer = window.setTimeout(() => reload(query), 150);
  });
  searchInput?.addEventListener("search", () => {
    window.clearTimeout(searchTimer);
    const query = String(searchInput.value || "").trim();
    if (query) setPickerCollapsed(false);
    if (!query && browseOpen) {
      reload("", { forceBrowse: true });
      return;
    }
    if (!query) setBrowseOpen(false);
    reload(query);
  });

  panel.addEventListener("stock-catalog:reset-search", (event) => {
    if (!simpleMode) return;
    resetLiveSearch({
      focusSearch: Boolean(event?.detail?.focusSearch),
    });
  });

  panel.addEventListener("stock-catalog:collapse-picker", () => {
    if (!simpleMode) return;
    resetLiveSearch();
    setPickerCollapsed(true);
  });

  panel.addEventListener("stock-catalog:expand-picker", () => {
    openPicker({ browse: false });
  });

  document.addEventListener("stock-catalog:rendered", () => {
    if (pickerCollapsed) setPickerCollapsed(true);
    else syncAddAnother();
  });

  const startCatalog = () => {
    if (panel.dataset.stockCatalogStarted === "1") return;
    panel.dataset.stockCatalogStarted = "1";
    if (searchFirst) {
      showIdleHint();
      if (localFilter) ensureLocalCatalog().catch(() => {});
      else warmCatalogPage({ page: 1, q: "" });
      return;
    }
    reload("");
  };

  if (panel.hasAttribute("data-stock-catalog-defer")) {
    panel.addEventListener("stock-catalog:load", startCatalog, { once: true });
    document.addEventListener(
      "buy-stock-modal:open",
      () => {
        if (localFilter) ensureLocalCatalog().catch(() => {});
        else warmCatalogPage({ page: 1, q: "" });
        panel.dispatchEvent(new CustomEvent("stock-catalog:load"));
      },
      { once: true }
    );
  } else {
    startCatalog();
  }
})();
