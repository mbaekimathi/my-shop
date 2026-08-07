(() => {
  const floor = document.querySelector(".shop-floor[data-catalog-api]");
  if (!floor) return;

  const apiUrl = floor.getAttribute("data-catalog-api") || "";
  const root = floor.querySelector("[data-catalog-root]");
  const moreWrap = floor.querySelector("[data-catalog-more-wrap]");
  const moreBtn = floor.querySelector("[data-catalog-more]");
  const searchInput = floor.querySelector("[data-item-search]");
  const noResults = floor.querySelector("[data-item-no-results]");
  const visibleCountEl = floor.querySelector("[data-item-visible-count]");
  const checkoutEnabled = floor.dataset.posCheckout === "1";

  if (!apiUrl || !root) return;

  let pageSize = Number(floor.dataset.catalogPageSize || 48) || 48;
  pageSize = Math.min(Math.max(pageSize, 12), 96);

  let totalCount = Number(floor.dataset.itemTotal || 0) || 0;
  let currentPage = 0;
  let nextPage = 1;
  let hasMore = false;
  let activeQuery = "";
  let inFlight = 0;
  let searchTimer = 0;
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
        <span data-category-count>0</span>
      </header>
      <div class="shop-floor-grid"></div>
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

  const appendItems = (items, { replace }) => {
    if (replace) {
      groupEls.clear();
      root.innerHTML = "";
    }
    items.forEach((item) => {
      const section = ensureGroup(item.category);
      const grid = section.querySelector(".shop-floor-grid");
      grid.appendChild(buildCard(item));
    });
    refreshGroupCounts();
  };

  const fetchPage = async ({ page, q, append }) => {
    const seq = ++inFlight;
    setLoading(!append);
    if (moreBtn) moreBtn.disabled = true;

    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (q) params.set("q", q);

    try {
      const response = await fetch(`${apiUrl}?${params.toString()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (seq !== inFlight) return;
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Catalog load failed");
      }

      totalCount = Number(data.total || 0);
      floor.dataset.itemTotal = String(totalCount);
      currentPage = Number(data.page || page);
      hasMore = Boolean(data.has_more);
      nextPage = data.next_page || null;
      activeQuery = String(data.q || q || "");

      appendItems(Array.isArray(data.items) ? data.items : [], { replace: !append });

      const visible = root.querySelectorAll("[data-item-row]").length;
      if (noResults) noResults.hidden = visible > 0 || (!activeQuery && totalCount === 0);
      if (root) root.hidden = visible === 0 && Boolean(activeQuery);
      if (moreWrap) moreWrap.hidden = !hasMore;
      updateCountLabel(visible || totalCount, Boolean(activeQuery));
      notifyRendered();
    } catch (_error) {
      if (seq !== inFlight) return;
      if (!append) {
        root.innerHTML =
          '<div class="dashboard-placeholder"><p>Could not load catalog. Try again.</p></div>';
      }
      if (moreWrap) moreWrap.hidden = true;
    } finally {
      if (seq === inFlight) {
        setLoading(false);
        if (moreBtn) moreBtn.disabled = false;
      }
    }
  };

  const reload = (q = "") => {
    groupEls.clear();
    currentPage = 0;
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

  reload("");
})();
