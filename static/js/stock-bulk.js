(() => {
  const form = document.querySelector("[data-stock-bulk-form]");
  const panel = document.querySelector("[data-stock-mode]");
  if (!form || !panel) return;

  const mode = panel.dataset.stockMode || "view";

  const navigateWithShops = () => {
    const params = new URLSearchParams();
    params.set("mode", mode);
    const shopSelect = form.querySelector("[data-stock-shop-nav]");
    const fromSelect = form.querySelector("[data-stock-from-nav]");
    if (shopSelect?.value) params.set("shop_id", shopSelect.value);
    if (mode === "request" && fromSelect?.value) {
      // Keep from shop only when it differs from requesting.
      if (!shopSelect?.value || fromSelect.value !== shopSelect.value) {
        params.set("requested_from_shop_id", fromSelect.value);
      }
    }
    window.location.assign(`${window.location.pathname}?${params.toString()}`);
  };

  form.querySelectorAll("[data-stock-shop-nav], [data-stock-from-nav]").forEach((select) => {
    select.addEventListener("change", navigateWithShops);
  });

  const filterRoot = form.querySelector("[data-stock-shop-filter]");
  if (filterRoot && (mode === "in" || mode === "out")) {
    const allBox = filterRoot.querySelector("[data-stock-shop-filter-all]");
    const applyBtn = filterRoot.querySelector("[data-stock-shop-filter-apply]");
    const hidden = filterRoot.querySelector("[data-stock-filter-shop-ids]");
    const boxes = () => [
      ...filterRoot.querySelectorAll("[data-stock-shop-filter-id]"),
    ];

    const syncHidden = () => {
      const selected = boxes()
        .filter((box) => box.checked)
        .map((box) => box.value);
      const allSelected =
        selected.length > 0 && selected.length === boxes().length;
      if (hidden) {
        hidden.value = allSelected || !selected.length ? "" : selected.join(",");
      }
      if (allBox) allBox.checked = allSelected || selected.length === 0;
    };

    allBox?.addEventListener("change", () => {
      if (allBox.checked) {
        boxes().forEach((box) => {
          box.checked = true;
        });
      }
      syncHidden();
    });

    boxes().forEach((box) => {
      box.addEventListener("change", () => {
        const selected = boxes().filter((item) => item.checked);
        if (!selected.length) {
          box.checked = true;
        }
        if (allBox) {
          allBox.checked = boxes().every((item) => item.checked);
        }
        syncHidden();
      });
    });

    applyBtn?.addEventListener("click", () => {
      syncHidden();
      const params = new URLSearchParams();
      params.set("mode", mode);
      const ids = String(hidden?.value || "")
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean);
      ids.forEach((id) => params.append("shop_id", id));
      window.location.assign(`${window.location.pathname}?${params.toString()}`);
    });

    syncHidden();
  }

  if (mode === "view") return;

  const serialCheckUrl =
    form.getAttribute("data-serial-check-url") ||
    form.dataset.serialCheckUrl ||
    "";
  const SERIAL_CHECK_MIN_LEN = 3;
  const SERIAL_CHECK_DEBOUNCE_MS = 400;
  const SERIAL_CHECK_CACHE_TTL_MS = 5 * 60 * 1000;
  const SERIAL_CHECK_CACHE_MAX = 200;
  const serialCheckCache = new Map();
  const serialCheckTimers = new WeakMap();
  let serialCheckAbort = null;
  let serialCheckSeq = 0;

  const serialCheckCacheKey = (itemId, serial) => `${itemId}|${serial}`;

  const readSerialCheckCache = (itemId, serial) => {
    const key = serialCheckCacheKey(itemId, serial);
    const hit = serialCheckCache.get(key);
    if (!hit) return null;
    if (Date.now() - hit.ts > SERIAL_CHECK_CACHE_TTL_MS) {
      serialCheckCache.delete(key);
      return null;
    }
    // Refresh LRU order.
    serialCheckCache.delete(key);
    serialCheckCache.set(key, hit);
    return hit;
  };

  const writeSerialCheckCache = (itemId, serial, payload) => {
    const key = serialCheckCacheKey(itemId, serial);
    if (serialCheckCache.has(key)) serialCheckCache.delete(key);
    serialCheckCache.set(key, { ...payload, ts: Date.now() });
    while (serialCheckCache.size > SERIAL_CHECK_CACHE_MAX) {
      const oldest = serialCheckCache.keys().next().value;
      serialCheckCache.delete(oldest);
    }
  };

  const ensureSerialRowMessage = (serialRow) => {
    let msg = serialRow.querySelector("[data-serial-in-stock-msg]");
    if (!msg) {
      msg = document.createElement("p");
      msg.className = "stock-serial-in-stock-msg";
      msg.setAttribute("data-serial-in-stock-msg", "");
      msg.hidden = true;
      serialRow.appendChild(msg);
    }
    return msg;
  };

  const clearSerialInStockState = (serialRow) => {
    if (!serialRow) return;
    serialRow.classList.remove("is-already-in-stock", "is-serial-duplicate");
    delete serialRow.dataset.serialBlocked;
    delete serialRow.dataset.serverInStock;
    const input = serialRow.querySelector("[data-stock-serial-input]");
    if (input) delete input.dataset.serialBlocked;
    const msg = serialRow.querySelector("[data-serial-in-stock-msg]");
    if (msg) {
      msg.hidden = true;
      msg.textContent = "";
    }
  };

  const setSerialInStockState = (serialRow, { blocked = false, message = "" } = {}) => {
    if (!serialRow) return;
    const input = serialRow.querySelector("[data-stock-serial-input]");
    if (!blocked) {
      clearSerialInStockState(serialRow);
      return;
    }
    serialRow.classList.add("is-already-in-stock");
    serialRow.dataset.serialBlocked = "1";
    if (input) input.dataset.serialBlocked = "1";
    const msg = ensureSerialRowMessage(serialRow);
    msg.hidden = false;
    msg.textContent = message || "Already in stock — remove";
  };

  const applySerialCheckResult = (serialRow, serial, hit) => {
    if (hit?.in_stock) {
      serialRow.dataset.serverInStock = "1";
      const shopBit = hit.shop_name ? ` at ${hit.shop_name}` : "";
      setSerialInStockState(serialRow, {
        blocked: true,
        message: `Already in stock${shopBit} — remove`,
      });
      return;
    }
    clearSerialInStockState(serialRow);
  };

  const runSerialInStockCheck = async (input, { itemId, container } = {}) => {
    if (mode !== "in" || !(input instanceof HTMLInputElement)) return;
    const serialRow = input.closest(".stock-serial-row");
    if (!serialRow) return;
    const serial = String(input.value || "").trim().toUpperCase();

    if (!serial) {
      clearSerialInStockState(serialRow);
      return;
    }

    const peers = [
      ...(container?.querySelectorAll("[data-stock-serial-input]") || []),
    ];
    const isDuplicate = peers.some(
      (peer) =>
        peer !== input &&
        String(peer.value || "").trim().toUpperCase() === serial
    );
    if (isDuplicate) {
      serialRow.dataset.serverInStock = "0";
      setSerialInStockState(serialRow, {
        blocked: true,
        message: "Duplicate serial — remove",
      });
      serialRow.classList.add("is-serial-duplicate");
      return;
    }

    // Wait for a meaningful value before hitting the network.
    if (serial.length < SERIAL_CHECK_MIN_LEN) {
      clearSerialInStockState(serialRow);
      return;
    }

    if (!serialCheckUrl || !itemId) {
      clearSerialInStockState(serialRow);
      return;
    }

    const cached = readSerialCheckCache(itemId, serial);
    if (cached) {
      applySerialCheckResult(serialRow, serial, cached);
      return;
    }

    if (serialCheckAbort) {
      try {
        serialCheckAbort.abort();
      } catch (_err) {
        /* ignore */
      }
    }
    const controller = new AbortController();
    serialCheckAbort = controller;
    const seq = ++serialCheckSeq;

    try {
      const params = new URLSearchParams({
        item_id: String(itemId),
        serial,
      });
      const response = await fetch(`${serialCheckUrl}?${params.toString()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        signal: controller.signal,
        cache: "no-store",
      });
      if (!response.ok) return;
      const data = await response.json().catch(() => ({}));
      if (seq !== serialCheckSeq) return;
      if (String(input.value || "").trim().toUpperCase() !== serial) return;
      const hit = (Array.isArray(data.results) ? data.results : []).find(
        (row) => row.serial === serial
      ) || { serial, in_stock: false, shop_name: "" };
      writeSerialCheckCache(itemId, serial, {
        in_stock: Boolean(hit.in_stock),
        shop_name: hit.shop_name || "",
      });
      applySerialCheckResult(serialRow, serial, hit);
    } catch (err) {
      if (err?.name === "AbortError") return;
      /* ignore network errors while typing */
    } finally {
      if (serialCheckAbort === controller) serialCheckAbort = null;
    }
  };

  const queueSerialInStockCheck = (input, opts = {}) => {
    if (!(input instanceof HTMLInputElement)) return;
    const prev = serialCheckTimers.get(input);
    if (prev) window.clearTimeout(prev);
    const delay = opts.immediate ? 0 : SERIAL_CHECK_DEBOUNCE_MS;
    const timer = window.setTimeout(() => {
      serialCheckTimers.delete(input);
      runSerialInStockCheck(input, opts);
    }, delay);
    serialCheckTimers.set(input, timer);
  };

  const readStockRequirements = () => {
    const defaults = {
      in: { buying_price: true, supplier: true, payment_status: true },
      out: { reason: true, refund: true },
      request: { note: false },
    };
    try {
      const raw = form.getAttribute("data-stock-requirements") || "";
      if (!raw) return defaults;
      const parsed = JSON.parse(raw);
      return {
        in: { ...defaults.in, ...(parsed.in || {}) },
        out: { ...defaults.out, ...(parsed.out || {}) },
        request: { ...defaults.request, ...(parsed.request || {}) },
      };
    } catch (_err) {
      return defaults;
    }
  };
  const stockReq = readStockRequirements();

  const markOptionalLabel = (el, required) => {
    if (!el) return;
    const label = el.closest("label")?.querySelector(":scope > span");
    if (!label) return;
    const base = (label.dataset.baseLabel || label.textContent || "").trim();
    if (!label.dataset.baseLabel) label.dataset.baseLabel = base.replace(/\s*\(optional\)\s*$/i, "");
    const text = label.dataset.baseLabel;
    label.textContent = required ? text : `${text} (optional)`;
    el.closest("label")?.classList.toggle("is-optional-rule", !required);
  };

  /* ── Multi-shop matrix (stock in/out/request management) ─────────────── */
  if (form.hasAttribute("data-stock-multi-shop") && (mode === "in" || mode === "out" || mode === "request")) {
    const floatRoot =
      form.querySelector("[data-stock-float]") ||
      document.querySelector("[data-stock-float]");
    const emptyEl = floatRoot?.querySelector("[data-stock-float-empty]");
    const heldEl = floatRoot?.querySelector("[data-stock-float-held]");
    const heldCountEl = floatRoot?.querySelector("[data-stock-float-held-count]");
    const linesEl = floatRoot?.querySelector("[data-stock-float-lines]");
    const clearBtn = floatRoot?.querySelector("[data-stock-float-clear]");
    const submitBtn = floatRoot?.querySelector("[data-stock-float-submit]");
    const shopLabelEl = floatRoot?.querySelector("[data-stock-float-shop-label]");
    const requestingLabelEl = floatRoot?.querySelector(
      "[data-stock-float-requesting-label]"
    );
    const requestingShopInput = form.querySelector("[data-stock-requesting-shop]");
    const applyStatus = floatRoot?.querySelector("[data-stock-float-apply-status]");
    const applyBtn = floatRoot?.querySelector("[data-stock-float-apply-btn]");
    const floatSupplierName = floatRoot?.querySelector("[data-stock-float-supplier-name]");
    const floatSupplierDial = floatRoot?.querySelector("[data-stock-float-supplier-dial]");
    const floatSupplierPhone = floatRoot?.querySelector("[data-stock-float-supplier-phone]");
    const floatSupplierId = floatRoot?.querySelector("[data-stock-float-supplier-id]");
    const floatPayment = floatRoot?.querySelector("[data-stock-float-payment]");
    const floatReason = floatRoot?.querySelector("[data-stock-float-reason]");
    const floatRefund = floatRoot?.querySelector("[data-stock-float-refund]");
    const floatRefundAmount = floatRoot?.querySelector("[data-stock-float-refund-amount]");
    const floatRefundAmountWrap = floatRoot?.querySelector(
      "[data-stock-float-refund-amount-wrap]"
    );
    markOptionalLabel(floatSupplierPhone, stockReq.in.supplier);
    markOptionalLabel(floatSupplierName, stockReq.in.supplier);
    markOptionalLabel(floatPayment, stockReq.in.payment_status);
    markOptionalLabel(floatReason, stockReq.out.reason);
    markOptionalLabel(floatRefund, stockReq.out.refund);
    const serialModal = document.querySelector("[data-stock-serial-modal]");
    const serialModalList = serialModal?.querySelector("[data-stock-serial-modal-list]");
    const serialModalCount = serialModal?.querySelector("[data-stock-serial-modal-count]");
    const serialModalTitle = serialModal?.querySelector("[data-stock-serial-modal-title]");
    const serialModalShop = serialModal?.querySelector("[data-stock-serial-modal-shop]");
    const serialSearchUrl = form.getAttribute("data-serial-search-url") || "";
    const usesCatalogApi = panel.hasAttribute("data-stock-catalog-api");
    let catalogBusy = usesCatalogApi && panel.hasAttribute("data-stock-catalog-busy");
    let activeSerialCell = null;
    let serialSearchTimer = 0;
    let serialSearchSeq = 0;
    let requestingShopId = (requestingShopInput?.value || "").trim();
    let requestingShopName = panel.dataset.stockCatalogShopName || "";
    const requestPairLocked = panel.hasAttribute("data-stock-request-pair");
    const fromShopFixedName = panel.dataset.stockCatalogFromShopName || "";
    if (mode === "request" && !requestingShopId) {
      requestingShopId =
        new URLSearchParams(window.location.search).get("shop_id") ||
        panel.dataset.stockCatalogShop ||
        "";
    }
    if (mode === "request" && requestingShopId && !requestingShopName) {
      requestingShopName = panel.dataset.stockCatalogShopName || "";
    }

    const cells = () => [
      ...panel.querySelectorAll("[data-stock-shop-cell]"),
      ...(document
        .querySelector("[data-stock-parked]")
        ?.querySelectorAll("[data-stock-shop-cell]") || []),
    ];

    const normalizePhone = (value) => String(value || "").replace(/\D/g, "").slice(0, 9);
    const setApplyStatus = (message, isError = false) => {
      if (!applyStatus) return;
      if (!message) {
        applyStatus.hidden = true;
        applyStatus.textContent = "";
        applyStatus.classList.remove("is-error");
        return;
      }
      applyStatus.hidden = false;
      applyStatus.textContent = message;
      applyStatus.classList.toggle("is-error", isError);
    };

    const cellQty = (cell) => {
      const raw = cell.querySelector("[data-stock-qty]")?.value || "";
      const n = Number(raw);
      return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
    };

    const cellHasPrice = (cell) => {
      if (mode !== "in") return true;
      if (!stockReq.in.buying_price) return true;
      const raw = cell.querySelector("[data-stock-buying-price]")?.value;
      if (raw == null || String(raw).trim() === "") return false;
      const n = Number(raw);
      return Number.isFinite(n) && n >= 0 && Number.isInteger(n);
    };

    const tracksSerial = (cell) => cell.getAttribute("data-track-serial") === "1";

    const updateFilledGroupMeta = (section) => {
      if (!section) return;
      const count = section.querySelectorAll("[data-item-row]").length;
      const countEl = section.querySelector("[data-category-count]");
      if (countEl) countEl.textContent = String(count);
      section.hidden = count === 0;
    };

    const ensureLiveFilledTbody = () => {
      const catalogRoot = panel.querySelector("[data-stock-catalog-root]");
      if (!catalogRoot) return null;
      let section = catalogRoot.querySelector("[data-stock-filled-group]");
      if (section) {
        if (catalogRoot.firstElementChild !== section) {
          catalogRoot.insertBefore(section, catalogRoot.firstElementChild);
        }
        return section.querySelector("[data-stock-catalog-tbody]");
      }
      const sample = catalogRoot.querySelector(
        ".stock-category:not([data-stock-filled-group])"
      );
      if (!sample) return null;
      section = sample.cloneNode(true);
      section.setAttribute("data-stock-filled-group", "");
      const tbody = section.querySelector("[data-stock-catalog-tbody]");
      if (tbody) tbody.innerHTML = "";
      const title = section.querySelector(".stock-category-title");
      if (title) title.textContent = "Items with quantity";
      const countEl = section.querySelector("[data-category-count]");
      if (countEl) countEl.textContent = "0";
      catalogRoot.insertBefore(section, catalogRoot.firstElementChild);
      return tbody;
    };

    const markFilled = (cell) => {
      const filled = cellQty(cell) > 0;
      cell.classList.toggle("is-filled", filled);
      const row = cell.closest("[data-item-row]");
      if (!row) return;
      const rowFilled = [
        ...row.querySelectorAll("[data-stock-shop-cell]"),
      ].some((c) => cellQty(c) > 0);
      row.classList.toggle("is-filled", rowFilled);
    };

    const clearLiveSearchIfUsed = () => {
      const searchInput = panel.querySelector("[data-item-search]");
      if (!searchInput) return;
      if (!String(searchInput.value || "").trim()) return;
      searchInput.value = "";
      searchInput.dispatchEvent(new Event("search", { bubbles: true }));
    };

    const promoteRowAfterLeave = (row) => {
      if (!row) return;
      // Refresh filled state from current inputs, then move only after the user left.
      row.querySelectorAll("[data-stock-shop-cell]").forEach((cell) => {
        cell.classList.toggle("is-filled", cellQty(cell) > 0);
      });
      const rowFilled = [
        ...row.querySelectorAll("[data-stock-shop-cell]"),
      ].some((c) => cellQty(c) > 0);
      row.classList.toggle("is-filled", rowFilled);
      reorderFilledRow(row);
      if (rowFilled) {
        // Defer so focus can land on the next field before catalog reload parks rows.
        window.setTimeout(() => clearLiveSearchIfUsed(), 0);
      }
    };

    const reorderFilledRow = (row) => {
      if (!row) return;
      const fromSection = row.closest(".stock-category");
      if (row.classList.contains("is-filled")) {
        const tbody = ensureLiveFilledTbody();
        if (tbody) {
          if (tbody.firstElementChild !== row) {
            tbody.insertBefore(row, tbody.firstElementChild);
          }
          updateFilledGroupMeta(tbody.closest(".stock-category"));
          if (fromSection && fromSection !== tbody.closest(".stock-category")) {
            updateFilledGroupMeta(fromSection);
          }
          return;
        }
        const fallback = row.closest("tbody");
        if (fallback && fallback.firstElementChild !== row) {
          fallback.insertBefore(row, fallback.firstElementChild);
        }
        return;
      }

      // Cleared: leave the selected group and sink under remaining filled rows.
      const selected = row.closest("[data-stock-filled-group]");
      const catalogRoot = panel.querySelector("[data-stock-catalog-root]");
      const home =
        catalogRoot?.querySelector(
          ".stock-category:not([data-stock-filled-group]) [data-stock-catalog-tbody]"
        ) || row.closest("tbody");
      if (home && row.parentElement !== home) {
        home.appendChild(row);
      } else if (home) {
        home.appendChild(row);
      }
      if (selected) updateFilledGroupMeta(selected);
      updateFilledGroupMeta(home?.closest?.(".stock-category"));
    };

    const collectReady = () =>
      cells()
        .map((cell) => {
          const qty = cellQty(cell);
          if (!qty) return null;
          if (mode === "request" && requestingShopId && cell.dataset.shopId === requestingShopId) {
            return null;
          }
          return {
            cell,
            id: cell.dataset.itemId,
            name: cell.dataset.itemName || "Item",
            shopId: cell.dataset.shopId,
            shopName: cell.dataset.shopName || "Shop",
            quantity: qty,
          };
        })
        .filter(Boolean);

    const floatSupplierReady = () => {
      if (mode !== "in") return false;
      const name = (floatSupplierName?.value || "").trim();
      const dial = (floatSupplierDial?.value || "").trim();
      const phone = normalizePhone(floatSupplierPhone?.value);
      const payment = (floatPayment?.value || "").trim();
      if (stockReq.in.supplier && !(name && dial && phone.length === 9)) return false;
      if (stockReq.in.payment_status && !payment) return false;
      return true;
    };

    const supplierCoreReady = () => {
      if (mode !== "in") return false;
      if (!stockReq.in.supplier) return true;
      const name = (floatSupplierName?.value || "").trim();
      const dial = (floatSupplierDial?.value || "").trim();
      const phone = normalizePhone(floatSupplierPhone?.value);
      return Boolean(name && dial && phone.length === 9);
    };

    const floatOutReady = () => {
      if (mode !== "out") return false;
      const reason = (floatReason?.value || "").trim();
      const refund = (floatRefund?.value || "").trim();
      if (stockReq.out.reason && !reason) return false;
      if (stockReq.out.refund) {
        if (!refund) return false;
        if (refund === "yes") {
          const amount = Number(floatRefundAmount?.value);
          return Number.isFinite(amount) && amount > 0 && Number.isInteger(amount);
        }
      }
      return true;
    };

    const cellHasSupplier = (cell) => {
      if (!stockReq.in.supplier && !stockReq.in.payment_status) return true;
      const name = (cell.querySelector("[data-stock-supplier-name]")?.value || "").trim();
      const phone = normalizePhone(
        cell.querySelector("[data-stock-supplier-phone]")?.value
      );
      const payment = (cell.querySelector("[data-stock-payment]")?.value || "").trim();
      const supplierOk =
        !stockReq.in.supplier || Boolean(name && phone.length === 9);
      const paymentOk = !stockReq.in.payment_status || Boolean(payment);
      return supplierOk && paymentOk;
    };

    const cellHasOutDetails = (cell) => {
      if (!stockReq.out.reason && !stockReq.out.refund) return true;
      const reason = (cell.querySelector("[data-stock-reason]")?.value || "").trim();
      const refund = (cell.querySelector("[data-stock-refund]")?.value || "").trim();
      if (stockReq.out.reason && !reason) return false;
      if (stockReq.out.refund) {
        if (!refund) return false;
        if (refund === "yes") {
          const amount = Number(
            cell.querySelector("[data-stock-refund-amount]")?.value
          );
          return Number.isFinite(amount) && amount > 0 && Number.isInteger(amount);
        }
      }
      return true;
    };

    const syncRequestingColumn = () => {
      if (mode !== "request") return;
      if (requestingShopInput) requestingShopInput.value = requestingShopId || "";
      document.querySelectorAll("[data-stock-request-shop-header]").forEach((header) => {
        const isRequesting = requestingShopId && header.dataset.shopId === requestingShopId;
        header.classList.toggle("is-requesting", Boolean(isRequesting));
        const roleEl = header.querySelector("[data-stock-request-role]");
        if (roleEl) roleEl.textContent = isRequesting ? "Requesting" : "From";
        if (isRequesting) requestingShopName = header.dataset.shopName || requestingShopName;
      });
      if (requestingLabelEl) {
        requestingLabelEl.textContent =
          requestingShopName || "Choose requesting shop";
      }
      cells().forEach((cell) => {
        const isRequesting =
          requestingShopId && cell.dataset.shopId === requestingShopId;
        cell.classList.toggle("is-requesting-shop", Boolean(isRequesting));
        const qty = cell.querySelector("[data-stock-qty]");
        if (!qty) return;
        if (isRequesting) {
          qty.value = "";
          qty.disabled = true;
          qty.setAttribute("title", "This is the requesting shop");
        } else {
          qty.disabled = false;
          qty.removeAttribute("title");
        }
        markFilled(cell);
      });
    };

    const setRequestingShop = (shopId, shopName) => {
      if (mode !== "request") return;
      if (requestPairLocked) return;
      const nextId = String(shopId || "").trim();
      if (requestingShopId === nextId) {
        requestingShopId = "";
        requestingShopName = "";
      } else {
        requestingShopId = nextId;
        requestingShopName = shopName || "Shop";
      }
      syncRequestingColumn();
      renderSummary();
    };

    const canSubmit = (ready) => {
      if (!ready.length) return false;
      if (mode === "in") {
        if (!floatSupplierReady()) return false;
        if (!ready.every((item) => cellHasPrice(item.cell))) return false;
        return ready.every((item) => cellHasSupplier(item.cell));
      }
      if (mode === "request") {
        return Boolean(requestingShopId);
      }
      if (!floatOutReady()) return false;
      return ready.every((item) => cellHasOutDetails(item.cell));
    };

    const stampMeta = (cell) => {
      if (mode === "request") return;
      if (mode === "in") {
        const payment = cell.querySelector("[data-stock-payment]");
        const name = cell.querySelector("[data-stock-supplier-name]");
        const phone = cell.querySelector("[data-stock-supplier-phone]");
        const dial = cell.querySelector("[data-stock-supplier-dial]");
        const supplierId = cell.querySelector("[data-stock-supplier-id]");
        if (payment) payment.value = floatPayment?.value || "";
        if (name) name.value = (floatSupplierName?.value || "").trim();
        if (dial) dial.value = floatSupplierDial?.value || "+254";
        if (phone) phone.value = normalizePhone(floatSupplierPhone?.value);
        if (supplierId) supplierId.value = floatSupplierId?.value || "";
      } else {
        const reason = cell.querySelector("[data-stock-reason]");
        const refund = cell.querySelector("[data-stock-refund]");
        const amount = cell.querySelector("[data-stock-refund-amount]");
        if (reason) reason.value = floatReason?.value || "";
        if (refund) refund.value = floatRefund?.value || "";
        if (amount) {
          amount.value =
            floatRefund?.value === "yes" ? floatRefundAmount?.value || "" : "";
        }
      }
    };

    const autoApplyDetailsToReady = ({ silent = true } = {}) => {
      if (mode === "request") return 0;
      const ready = collectReady();
      if (!ready.length) return 0;
      if (mode === "in") {
        if (stockReq.in.supplier && !supplierCoreReady()) return 0;
        ready.forEach((item) => stampMeta(item.cell));
        if (!silent) {
          if (floatSupplierReady()) {
            setApplyStatus(
              `Supplier details applied to ${ready.length} item(s).`
            );
          } else if (stockReq.in.payment_status && !(floatPayment?.value || "").trim()) {
            setApplyStatus(
              "Details applied. Select payment status to submit.",
              true
            );
          } else {
            setApplyStatus(
              `Details applied to ${ready.length} item(s).`
            );
          }
        }
        return ready.length;
      }
      if (mode === "out") {
        if (
          (stockReq.out.reason || stockReq.out.refund) &&
          !floatOutReady()
        ) {
          return 0;
        }
        ready.forEach((item) => stampMeta(item.cell));
        if (!silent) {
          setApplyStatus(
            `Stock-out details applied to ${ready.length} item(s).`
          );
        }
        return ready.length;
      }
      return 0;
    };

    const revealAndFocus = (el) => {
      if (!el) return;
      if (floatRoot) {
        floatRoot.classList.remove("is-collapsed");
        floatRoot
          .querySelector("[data-stock-float-toggle]")
          ?.setAttribute("aria-expanded", "true");
      }
      try {
        el.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
      } catch (_err) {
        /* ignore */
      }
      window.setTimeout(() => {
        try {
          el.focus({ preventScroll: true });
        } catch (_err) {
          el.focus?.();
        }
      }, 180);
    };

    const focusFirstIncomplete = (ready) => {
      if (catalogBusy || panel.hasAttribute("data-stock-catalog-busy")) {
        setApplyStatus("Still loading items — wait a moment, then submit.", true);
        revealAndFocus(submitBtn || floatRoot);
        return true;
      }
      if (!ready.length) {
        setApplyStatus("Enter quantity on at least one shop cell.", true);
        const firstQty =
          panel.querySelector("[data-stock-shop-cell] [data-stock-qty]:not([disabled])") ||
          panel.querySelector("[data-item-search]");
        revealAndFocus(firstQty || panel);
        return true;
      }
      if (mode === "request" && !requestingShopId) {
        setApplyStatus("Click a shop column header to set the requesting shop.", true);
        revealAndFocus(
          document.querySelector("[data-stock-request-shop-header]") || requestingLabelEl
        );
        return true;
      }
      if (mode === "in") {
        autoApplyDetailsToReady({ silent: true });
        if (stockReq.in.supplier) {
          const phone = normalizePhone(floatSupplierPhone?.value);
          const name = (floatSupplierName?.value || "").trim();
          if (!phone || phone.length !== 9) {
            setApplyStatus("Enter a valid supplier phone.", true);
            revealAndFocus(floatSupplierPhone);
            return true;
          }
          if (!name) {
            setApplyStatus("Enter or select supplier name.", true);
            revealAndFocus(floatSupplierName);
            return true;
          }
        }
        if (stockReq.in.payment_status && !(floatPayment?.value || "").trim()) {
          setApplyStatus("Select payment status before submitting.", true);
          revealAndFocus(floatPayment);
          return true;
        }
        const missingPrice = ready.find((item) => !cellHasPrice(item.cell));
        if (missingPrice) {
          setApplyStatus(
            `Enter buying price for ${missingPrice.name} · ${missingPrice.shopName}.`,
            true
          );
          revealAndFocus(
            missingPrice.cell.querySelector("[data-stock-buying-price]") ||
              missingPrice.cell
          );
          return true;
        }
        const missingSupplier = ready.find((item) => !cellHasSupplier(item.cell));
        if (missingSupplier) {
          setApplyStatus(
            "Supplier details are incomplete — check the submit panel.",
            true
          );
          revealAndFocus(floatPayment || floatSupplierPhone);
          return true;
        }
      }
      if (mode === "out") {
        autoApplyDetailsToReady({ silent: true });
        const reason = (floatReason?.value || "").trim();
        const refund = (floatRefund?.value || "").trim();
        if (stockReq.out.reason && !reason) {
          setApplyStatus("Choose a stock-out reason.", true);
          revealAndFocus(floatReason);
          return true;
        }
        if (stockReq.out.refund) {
          if (refund !== "yes" && refund !== "no") {
            setApplyStatus("Choose whether a refund applies.", true);
            revealAndFocus(floatRefund);
            return true;
          }
          if (refund === "yes") {
            const amount = Number(floatRefundAmount?.value);
            if (!Number.isFinite(amount) || amount <= 0 || !Number.isInteger(amount)) {
              setApplyStatus("Enter a whole-number refund amount greater than zero.", true);
              revealAndFocus(floatRefundAmount);
              return true;
            }
          }
        }
        const missingOut = ready.find((item) => !cellHasOutDetails(item.cell));
        if (missingOut) {
          setApplyStatus(
            "Stock-out details are incomplete — check the submit panel.",
            true
          );
          revealAndFocus(floatReason);
          return true;
        }
      }
      return false;
    };

    const enableReadyFields = (ready) => {
      // Disable all matrix fields first so empty cells are not posted.
      cells().forEach((cell) => {
        cell.querySelectorAll("[data-stock-field], [data-stock-qty], [data-stock-buying-price]").forEach(
          (field) => {
            field.disabled = true;
          }
        );
      });
      ready.forEach((item) => {
        stampMeta(item.cell);
        item.cell
          .querySelectorAll("[data-stock-field], [data-stock-qty], [data-stock-buying-price]")
          .forEach((field) => {
            field.disabled = false;
          });
      });
    };

    const renderSummary = () => {
      autoApplyDetailsToReady({ silent: true });
      const ready = collectReady();
      const units = ready.reduce((sum, item) => sum + item.quantity, 0);
      const shopIds = new Set(ready.map((item) => item.shopId).filter(Boolean));
      if (shopLabelEl) {
        if (mode === "request") {
          shopLabelEl.textContent =
            fromShopFixedName ||
            (shopIds.size === 0
              ? "Choose from shop"
              : shopIds.size === 1
                ? ready.find((item) => item.shopId)?.shopName || "1 shop"
                : `${shopIds.size} shops`);
        } else {
          const singleShopName = panel.dataset.stockCatalogShopName || "";
          shopLabelEl.textContent =
            shopIds.size === 0
              ? singleShopName || "All shops"
              : shopIds.size === 1
                ? ready.find((item) => item.shopId)?.shopName || "1 shop"
                : `${shopIds.size} shops`;
        }
      }
      if (emptyEl) emptyEl.hidden = ready.length > 0;
      if (linesEl) {
        linesEl.hidden = ready.length === 0;
        linesEl.innerHTML = ready
          .map(
            (item) =>
              `<li>
                <span title="${item.name} · ${item.shopName}">${item.name} · ${
                mode === "request" ? `from ${item.shopName}` : item.shopName
              }</span>
                <strong>×${item.quantity}</strong>
                <button type="button" data-stock-float-remove data-item-id="${item.id}" data-shop-id="${item.shopId}" aria-label="Clear ${item.name} at ${item.shopName}">×</button>
              </li>`
          )
          .join("");
      }
      const parked = document.querySelector("[data-stock-parked]");
      const held = parked
        ? [...parked.querySelectorAll("[data-stock-shop-cell]")].filter(
            (c) => cellQty(c) > 0
          ).length
        : 0;
      if (heldEl) heldEl.hidden = held === 0;
      if (heldCountEl) heldCountEl.textContent = String(held);
      if (clearBtn) clearBtn.hidden = ready.length === 0;
      if (submitBtn) {
        // Keep submit clickable whenever there are ready items so validation can
        // jump the user to the first incomplete field.
        submitBtn.disabled = catalogBusy || ready.length === 0;
      }
    };

    const clearCell = (cell) => {
      const qty = cell.querySelector("[data-stock-qty]");
      const price = cell.querySelector("[data-stock-buying-price]");
      const serials = cell.querySelector("[data-stock-serials]");
      if (qty) qty.value = "";
      if (price) price.value = "";
      if (serials) serials.value = "";
      markFilled(cell);
    };

    const clearAll = () => {
      cells().forEach(clearCell);
      setApplyStatus("");
      renderSummary();
    };

    const refreshIcons = () => {
      if (window.lucide?.createIcons) window.lucide.createIcons();
    };

    const modalSerials = () =>
      [...(serialModalList?.querySelectorAll("[data-stock-serial-input]") || [])]
        .filter(
          (input) =>
            input.closest(".stock-serial-row")?.dataset.serialBlocked !== "1"
        )
        .map((input) => String(input.value || "").trim().toUpperCase())
        .filter(Boolean);

    const syncModalCount = () => {
      const serials = modalSerials();
      if (serialModalCount) serialModalCount.textContent = String(serials.length);
      serialModalList
        ?.querySelectorAll("[data-stock-serial-remove]")
        .forEach((btn) => {
          btn.hidden = (serialModalList?.querySelectorAll(".stock-serial-row").length || 0) <= 1;
        });
    };

    const createModalSerialRow = (serial = "") => {
      if (!serialModalList) return null;
      const row = document.createElement("div");
      row.className = "stock-serial-row";
      const wrap = document.createElement("div");
      wrap.className = "stock-serial-input-wrap";
      if (mode === "out") wrap.setAttribute("data-serial-search-root", "");
      row.appendChild(wrap);
      const input = document.createElement("input");
      input.type = "text";
      input.placeholder =
        mode === "out" ? "Search serial to stock out" : "Enter serial number";
      input.autocomplete = "off";
      input.spellcheck = false;
      input.setAttribute("data-stock-serial-input", "");
      if (mode === "out") input.setAttribute("data-serial-search", "");
      input.value = serial;
      wrap.appendChild(input);
      if (mode === "out") {
        const suggest = document.createElement("div");
        suggest.className = "stock-supplier-suggest";
        suggest.setAttribute("data-serial-suggest", "");
        suggest.hidden = true;
        wrap.appendChild(suggest);
      }
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "stock-serial-remove";
      remove.setAttribute("data-stock-serial-remove", "");
      remove.setAttribute("aria-label", "Remove serial");
      remove.innerHTML = '<i data-lucide="x" aria-hidden="true"></i>';
      row.appendChild(remove);
      serialModalList.appendChild(row);
      refreshIcons();
      syncModalCount();
      if (mode === "in" && serial) {
        queueSerialInStockCheck(input, {
          itemId: activeSerialCell?.dataset.itemId || "",
          container: serialModalList,
          immediate: true,
        });
      }
      return input;
    };

    const openSerialModal = (cell) => {
      activeSerialCell = cell;
      const itemName = cell.dataset.itemName || "Item";
      const shopName = cell.dataset.shopName || "Shop";
      if (serialModalTitle) serialModalTitle.textContent = itemName;
      if (serialModalShop) serialModalShop.textContent = shopName;
      if (serialModalList) serialModalList.innerHTML = "";
      const existing = String(cell.querySelector("[data-stock-serials]")?.value || "")
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (existing.length) existing.forEach((s) => createModalSerialRow(s));
      else createModalSerialRow("");
      syncModalCount();
      serialModal.hidden = false;
      serialModal.setAttribute("aria-hidden", "false");
      document.body.classList.add("workspace-modal-open");
      serialModalList?.querySelector("[data-stock-serial-input]")?.focus();
    };

    const closeSerialModal = ({ save = false } = {}) => {
      const cell = activeSerialCell;
      if (save && cell) {
        const blocked = serialModalList?.querySelector(
          ".stock-serial-row.is-already-in-stock"
        );
        if (blocked) {
          blocked.querySelector("[data-stock-serial-input]")?.focus();
          return;
        }
        const serials = modalSerials();
        const unique = [...new Set(serials)];
        const serialHidden = cell.querySelector("[data-stock-serials]");
        const qtyInput = cell.querySelector("[data-stock-qty]");
        if (serialHidden) serialHidden.value = unique.join("\n");
        if (qtyInput) qtyInput.value = unique.length ? String(unique.length) : "";
        markFilled(cell);
        renderSummary();
      }
      activeSerialCell = null;
      if (serialModal) {
        serialModal.hidden = true;
        serialModal.setAttribute("aria-hidden", "true");
      }
      document.body.classList.remove("workspace-modal-open");
      // Keep row in place until the user moves on; only refresh filled styling.
      if (cell) markFilled(cell);
    };

    const runSerialSearch = async (input) => {
      if (mode !== "out" || !serialSearchUrl || !activeSerialCell) return;
      const itemId = activeSerialCell.dataset.itemId || "";
      const shopId = activeSerialCell.dataset.shopId || "";
      if (!itemId || !shopId) return;
      const root = input.closest("[data-serial-search-root]");
      const suggest = root?.querySelector("[data-serial-suggest]");
      if (!suggest) return;
      const query = String(input.value || "").trim().toUpperCase();
      const seq = ++serialSearchSeq;
      const params = new URLSearchParams({
        item_id: itemId,
        shop_id: shopId,
        q: query,
      });
      modalSerials().forEach((serial) => {
        if (serial !== query) params.append("exclude", serial);
      });
      try {
        const response = await fetch(`${serialSearchUrl}?${params.toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        if (!response.ok) return;
        const data = await response.json();
        if (seq !== serialSearchSeq) return;
        const results = Array.isArray(data.results) ? data.results : [];
        suggest.innerHTML = "";
        if (!results.length) {
          suggest.hidden = true;
          return;
        }
        results.slice(0, 8).forEach((serial) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.textContent = serial;
          btn.addEventListener("mousedown", (event) => {
            event.preventDefault();
            input.value = serial;
            suggest.hidden = true;
            syncModalCount();
            createModalSerialRow("")?.focus();
          });
          suggest.appendChild(btn);
        });
        suggest.hidden = false;
      } catch (_err) {
        /* ignore */
      }
    };

    // Float collapse (mobile)
    const floatToggle = floatRoot?.querySelector("[data-stock-float-toggle]");
    const floatCollapseMq = window.matchMedia("(max-width: 1199px)");
    const floatCollapseKey = `stock-float-collapsed:${mode}`;
    const setFloatCollapsed = (collapsed, { persist = true } = {}) => {
      if (!floatRoot || !floatToggle) return;
      const next = Boolean(collapsed) && floatCollapseMq.matches;
      floatRoot.classList.toggle("is-collapsed", next);
      floatToggle.setAttribute("aria-expanded", next ? "false" : "true");
      if (persist) {
        try {
          sessionStorage.setItem(floatCollapseKey, next ? "1" : "0");
        } catch (_err) {
          /* ignore */
        }
      }
    };
    floatToggle?.addEventListener("click", () => {
      setFloatCollapsed(!floatRoot.classList.contains("is-collapsed"));
    });
    try {
      setFloatCollapsed(sessionStorage.getItem(floatCollapseKey) === "1", {
        persist: false,
      });
    } catch (_err) {
      setFloatCollapsed(true, { persist: false });
    }

    if (floatRefund) {
      floatRefund.addEventListener("change", () => {
        const show = floatRefund.value === "yes";
        if (floatRefundAmountWrap) floatRefundAmountWrap.hidden = !show;
        if (!show && floatRefundAmount) floatRefundAmount.value = "";
        renderSummary();
      });
    }

    panel.addEventListener("input", (event) => {
      const cell = event.target.closest?.("[data-stock-shop-cell]");
      if (!cell) return;
      if (
        event.target.matches("[data-stock-qty], [data-stock-buying-price]")
      ) {
        markFilled(cell);
        renderSummary();
      }
    });

    // Promote/sink only after the user leaves the item row (moves to another item).
    panel.addEventListener("focusout", (event) => {
      const fromRow = event.target.closest?.("[data-item-row]");
      if (!fromRow) return;
      const toEl = event.relatedTarget;
      const toRow =
        toEl instanceof Element ? toEl.closest("[data-item-row]") : null;
      if (toRow === fromRow) return;
      // Ignore blur into the serial modal — user is still editing this cell.
      if (
        toEl instanceof Element &&
        serialModal &&
        !serialModal.hidden &&
        serialModal.contains(toEl)
      ) {
        return;
      }
      promoteRowAfterLeave(fromRow);
    });

    const openSerialFromEvent = (event) => {
      const openEl = event.target.closest?.("[data-stock-serial-open]");
      if (!openEl) return false;
      event.preventDefault();
      const cell = openEl.closest("[data-stock-shop-cell]");
      if (cell) openSerialModal(cell);
      return true;
    };

    panel.addEventListener("click", (event) => {
      openSerialFromEvent(event);
    });
    panel.addEventListener("focusin", (event) => {
      openSerialFromEvent(event);
    });
    panel.addEventListener("keydown", (event) => {
      if (!event.target.matches?.("[data-stock-serial-open]")) return;
      if (event.key === "Enter" || event.key === " ") {
        openSerialFromEvent(event);
      } else if (event.key.length === 1 || event.key === "Backspace") {
        // Quantity is derived from serials — block direct edits.
        event.preventDefault();
        openSerialFromEvent(event);
      }
    });

    document
      .querySelector("[data-stock-parked]")
      ?.addEventListener("click", (event) => {
        openSerialFromEvent(event);
      });
    document
      .querySelector("[data-stock-parked]")
      ?.addEventListener("focusin", (event) => {
        openSerialFromEvent(event);
      });

    serialModal?.addEventListener("click", (event) => {
      if (event.target.closest("[data-stock-serial-modal-close]")) {
        event.preventDefault();
        closeSerialModal({ save: false });
        return;
      }
      if (event.target.closest("[data-stock-serial-modal-done]")) {
        event.preventDefault();
        closeSerialModal({ save: true });
        return;
      }
      if (event.target.closest("[data-stock-serial-modal-add]")) {
        event.preventDefault();
        createModalSerialRow("")?.focus();
        return;
      }
      const remove = event.target.closest("[data-stock-serial-remove]");
      if (remove) {
        event.preventDefault();
        remove.closest(".stock-serial-row")?.remove();
        if (!serialModalList?.querySelector(".stock-serial-row")) {
          createModalSerialRow("");
        }
        syncModalCount();
      }
    });

    serialModal?.addEventListener("input", (event) => {
      if (!event.target.matches("[data-stock-serial-input]")) return;
      event.target.value = String(event.target.value || "").toUpperCase();
      syncModalCount();
      if (mode === "out" && event.target.matches("[data-serial-search]")) {
        window.clearTimeout(serialSearchTimer);
        serialSearchTimer = window.setTimeout(
          () => runSerialSearch(event.target),
          220
        );
      }
      if (mode === "in") {
        queueSerialInStockCheck(event.target, {
          itemId: activeSerialCell?.dataset.itemId || "",
          container: serialModalList,
        });
      }
    });

    serialModal?.addEventListener("myshop:serial-applied", (event) => {
      if (mode !== "in") return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.matches("[data-stock-serial-input]")) return;
      syncModalCount();
      queueSerialInStockCheck(target, {
        itemId: activeSerialCell?.dataset.itemId || "",
        container: serialModalList,
        immediate: true,
      });
    });

    serialModal?.addEventListener("focusout", (event) => {
      if (mode !== "in") return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.matches("[data-stock-serial-input]")) return;
      queueSerialInStockCheck(target, {
        itemId: activeSerialCell?.dataset.itemId || "",
        container: serialModalList,
        immediate: true,
      });
    });

    serialModal?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeSerialModal({ save: false });
      }
    });

    floatRoot?.addEventListener("click", (event) => {
      const removeBtn = event.target.closest("[data-stock-float-remove]");
      if (!removeBtn) return;
      event.preventDefault();
      const itemId = removeBtn.getAttribute("data-item-id");
      const shopId = removeBtn.getAttribute("data-shop-id");
      const cell = cells().find(
        (c) => c.dataset.itemId === itemId && c.dataset.shopId === shopId
      );
      if (cell) clearCell(cell);
      renderSummary();
    });

    clearBtn?.addEventListener("click", clearAll);

    applyBtn?.addEventListener("click", () => {
      if (mode === "request") return;
      const ready = collectReady();
      if (!ready.length) {
        setApplyStatus("Add quantity on at least one shop cell first.", true);
        return;
      }
      if (mode === "in" && stockReq.in.supplier && !supplierCoreReady()) {
        setApplyStatus("Enter supplier name and phone first.", true);
        return;
      }
      if (mode === "out" && !floatOutReady()) {
        setApplyStatus("Choose reason and refund details.", true);
        return;
      }
      autoApplyDetailsToReady({ silent: false });
      renderSummary();
    });

    /* ── Supplier live search (submit panel + any in-form fields) ───────── */
    const supplierSearchUrl =
      form.dataset.supplierSearchUrl ||
      form.getAttribute("data-supplier-search-url") ||
      "";
    const floatPhoneWrap = floatRoot?.querySelector("[data-stock-float-phone-wrap]");
    const floatSupplierIso = floatRoot?.querySelector("[data-stock-float-supplier-iso]");
    let supplierSearchTimer = 0;
    let supplierSearchSeq = 0;
    let fillingSupplier = false;

    const setFloatCountry = (dial, iso) => {
      if (floatSupplierDial && dial) floatSupplierDial.value = dial;
      if (floatSupplierIso && iso) floatSupplierIso.value = iso;
      const dialDisplay = floatPhoneWrap?.querySelector("[data-stock-dial-display]");
      const flagImg = floatPhoneWrap?.querySelector("[data-stock-flag-img]");
      if (dialDisplay && dial) dialDisplay.textContent = dial;
      if (flagImg && iso) {
        flagImg.src = `https://flagcdn.com/w40/${String(iso).toLowerCase()}.png`;
      }
      if (floatSupplierPhone?.value) {
        floatSupplierPhone.value = normalizePhone(floatSupplierPhone.value);
      }
    };

    const hideSupplierSuggest = (root) => {
      const nodes = root
        ? root.querySelectorAll("[data-supplier-suggest]")
        : form.querySelectorAll("[data-supplier-suggest]");
      nodes?.forEach((el) => {
        el.hidden = true;
        el.innerHTML = "";
      });
    };

    const resolveSupplierFields = (fromInput) => {
      const floatScope = fromInput?.closest?.("[data-stock-float-apply]");
      if (floatScope || fromInput?.matches?.("[data-stock-float-supplier-name], [data-stock-float-supplier-phone]")) {
        return {
          nameInput: floatSupplierName,
          phoneInput: floatSupplierPhone,
          dialRoot: floatPhoneWrap || floatRoot,
          idInput: floatSupplierId,
          scope: floatRoot || form,
        };
      }
      const cell = fromInput?.closest?.("[data-stock-shop-cell], [data-stock-item-inputs], .buy-stock-pick-inputs");
      if (cell) {
        return {
          nameInput: cell.querySelector("[data-stock-supplier-name], [data-stock-float-supplier-name]"),
          phoneInput: cell.querySelector("[data-stock-supplier-phone], [data-stock-float-supplier-phone]"),
          dialRoot: cell.querySelector("[data-stock-phone-field]") || cell,
          idInput: cell.querySelector("[data-stock-supplier-id], [data-stock-float-supplier-id]"),
          scope: cell,
        };
      }
      return {
        nameInput: floatSupplierName,
        phoneInput: floatSupplierPhone,
        dialRoot: floatPhoneWrap || floatRoot,
        idInput: floatSupplierId,
        scope: floatRoot || form,
      };
    };

    const applySupplierResult = (fromInput, supplier) => {
      if (!supplier) return;
      fillingSupplier = true;
      const targets = resolveSupplierFields(fromInput);
      if (targets.nameInput) {
        targets.nameInput.value = String(supplier.name || "").toUpperCase();
      }
      setFloatCountry(supplier.dial || "+254", supplier.iso || "KE");
      if (targets.dialRoot && targets.dialRoot !== floatPhoneWrap) {
        const dialInput =
          targets.dialRoot.querySelector?.(
            "[data-stock-supplier-dial], [data-stock-float-supplier-dial]"
          ) || null;
        const isoInput =
          targets.dialRoot.querySelector?.(
            "[data-stock-supplier-iso], [data-stock-float-supplier-iso]"
          ) || null;
        const dialDisplay = targets.dialRoot.querySelector?.("[data-stock-dial-display]");
        const flagImg = targets.dialRoot.querySelector?.("[data-stock-flag-img]");
        if (dialInput) dialInput.value = supplier.dial || "+254";
        if (isoInput) isoInput.value = supplier.iso || "KE";
        if (dialDisplay) dialDisplay.textContent = supplier.dial || "+254";
        if (flagImg && supplier.iso) {
          flagImg.src = `https://flagcdn.com/w40/${String(supplier.iso).toLowerCase()}.png`;
        }
      }
      if (targets.phoneInput) {
        targets.phoneInput.value = normalizePhone(supplier.phone || "");
        targets.phoneInput.dataset.supplierResolved = "1";
      }
      // Keep float fields in sync when picking from a row/cell field.
      if (targets.nameInput !== floatSupplierName && floatSupplierName) {
        floatSupplierName.value = String(supplier.name || "").toUpperCase();
      }
      if (targets.phoneInput !== floatSupplierPhone && floatSupplierPhone) {
        floatSupplierPhone.value = normalizePhone(supplier.phone || "");
        floatSupplierPhone.dataset.supplierResolved = "1";
      }
      if (targets.idInput) {
        targets.idInput.value = supplier.id != null ? String(supplier.id) : "";
      }
      if (floatSupplierId && targets.idInput !== floatSupplierId) {
        floatSupplierId.value = supplier.id != null ? String(supplier.id) : "";
      }
      fillingSupplier = false;
      hideSupplierSuggest(targets.scope);
      hideSupplierSuggest(floatRoot);
      autoApplyDetailsToReady({ silent: false });
      renderSummary();
    };

    const renderSupplierSuggest = (input, results) => {
      const root = input.closest("[data-supplier-search-root]");
      const suggest = root?.querySelector("[data-supplier-suggest]");
      if (!suggest) return;
      suggest.innerHTML = "";
      if (!results.length) {
        suggest.hidden = true;
        return;
      }
      const by = input.getAttribute("data-supplier-search") || "name";
      results.forEach((supplier) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "stock-supplier-suggest-option";
        btn.innerHTML = "<strong></strong><small></small>";
        const phoneLabel = `${supplier.dial || ""}${supplier.phone || ""}`;
        if (by === "name") {
          btn.querySelector("strong").textContent = supplier.name || "";
          btn.querySelector("small").textContent = phoneLabel;
        } else {
          btn.querySelector("strong").textContent = phoneLabel;
          btn.querySelector("small").textContent = supplier.name || "";
        }
        btn.addEventListener("mousedown", (event) => {
          event.preventDefault();
          applySupplierResult(input, supplier);
        });
        suggest.appendChild(btn);
      });
      suggest.hidden = false;
    };

    const runSupplierSearch = async (input) => {
      if (!supplierSearchUrl || fillingSupplier || mode !== "in") return;
      const by = input.getAttribute("data-supplier-search");
      if (!by) return;
      const query = (input.value || "").trim();
      const root = input.closest("[data-supplier-search-root]");
      const minLen = by === "phone" ? 2 : 2;
      if (query.length < minLen) {
        hideSupplierSuggest(root);
        return;
      }

      const dial =
        (floatSupplierDial?.value || "").trim() ||
        (
          input
            .closest("[data-stock-phone-field]")
            ?.querySelector(
              "[data-stock-float-supplier-dial], [data-stock-supplier-dial]"
            )?.value || ""
        ).trim();
      const seq = ++supplierSearchSeq;
      const params = new URLSearchParams({ q: query, by });
      if (by === "phone" && dial) params.set("dial", dial);

      try {
        const response = await fetch(`${supplierSearchUrl}?${params.toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        if (!response.ok) return;
        const data = await response.json();
        if (seq !== supplierSearchSeq) return;
        const results = Array.isArray(data.results) ? data.results : [];
        renderSupplierSuggest(input, results);

        // Phone → autofill name. Name → suggest only; user must pick a row.
        if (by !== "phone") return;
        const digits = (value) => String(value || "").replace(/\D+/g, "");
        const qDigits = digits(query);
        if (qDigits.length < 7 || !results.length) return;
        const match =
          results.find((row) => digits(row.phone) === qDigits) ||
          (results.length === 1 &&
          digits(results[0].phone).includes(qDigits)
            ? results[0]
            : null);
        if (match) applySupplierResult(input, match);
      } catch (_error) {
        /* ignore network errors while typing */
      }
    };

    const queueSupplierSearch = (input) => {
      window.clearTimeout(supplierSearchTimer);
      supplierSearchTimer = window.setTimeout(() => runSupplierSearch(input), 200);
    };

    const onSupplierFieldInput = (target) => {
      if (!(target instanceof Element)) return;
      if (!target.matches("[data-supplier-search]")) return;
      if (target.matches("[data-stock-float-supplier-name], [data-stock-supplier-name]")) {
        const start = target.selectionStart;
        const end = target.selectionEnd;
        target.value = String(target.value || "").toUpperCase();
        if (typeof start === "number" && typeof end === "number") {
          target.setSelectionRange(start, end);
        }
      } else if (
        target.matches("[data-stock-float-supplier-phone], [data-stock-supplier-phone]")
      ) {
        delete target.dataset.supplierResolved;
        target.value = normalizePhone(target.value);
      }
      if (floatSupplierId) floatSupplierId.value = "";
      const idInScope = resolveSupplierFields(target).idInput;
      if (idInScope) idInScope.value = "";
      queueSupplierSearch(target);
      renderSummary();
    };

    form.addEventListener("input", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.matches("[data-supplier-search]")) {
        onSupplierFieldInput(target);
        return;
      }
      if (
        target.matches(
          "[data-stock-float-payment], [data-stock-float-reason], [data-stock-float-refund], [data-stock-float-refund-amount]"
        )
      ) {
        if (target.matches("[data-stock-float-refund]")) {
          const show = target.value === "yes";
          if (floatRefundAmountWrap) floatRefundAmountWrap.hidden = !show;
          if (!show && floatRefundAmount) floatRefundAmount.value = "";
        }
        autoApplyDetailsToReady({ silent: true });
        renderSummary();
      }
    });
    form.addEventListener("change", (event) => {
      const target = event.target;
      if (
        target instanceof Element &&
        target.matches(
          "[data-stock-float-payment], [data-stock-float-reason], [data-stock-float-refund], [data-stock-float-refund-amount]"
        )
      ) {
        autoApplyDetailsToReady({ silent: false });
      }
      renderSummary();
    });
    form.addEventListener("focusin", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.matches("[data-supplier-search]")) return;
      if ((target.value || "").trim().length >= 2) queueSupplierSearch(target);
    });
    form.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.matches("[data-supplier-search]")) return;
      const root = target.closest("[data-supplier-search-root]");
      const first = root?.querySelector(
        ".stock-supplier-suggest-option:not([disabled])"
      );
      if (!first) return;
      event.preventDefault();
      first.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });

    document.addEventListener("mousedown", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.closest("[data-supplier-search-root]")) return;
      hideSupplierSuggest(form);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") hideSupplierSuggest(form);
    });

    const countryMenu = document.querySelector("[data-stock-country-menu]");
    const countrySearch = countryMenu?.querySelector("[data-stock-country-search]");
    const countryOptions = [
      ...(countryMenu?.querySelectorAll(".stock-country-option") || []),
    ];
    let activePhoneField = null;

    const closeCountryMenu = () => {
      if (!countryMenu) return;
      countryMenu.hidden = true;
      floatRoot
        ?.querySelectorAll("[data-stock-country-trigger][aria-expanded='true']")
        .forEach((btn) => btn.setAttribute("aria-expanded", "false"));
      activePhoneField = null;
      if (countrySearch) countrySearch.value = "";
      countryOptions.forEach((option) => {
        if (option.parentElement) option.parentElement.hidden = false;
      });
    };

    const openCountryMenu = (trigger) => {
      if (!countryMenu || !trigger || trigger.disabled) return;
      const phoneField = trigger.closest("[data-stock-phone-field]");
      if (!phoneField) return;
      activePhoneField = phoneField;
      const rect = trigger.getBoundingClientRect();
      const menuWidth = Math.min(264, window.innerWidth - 24);
      let left = rect.left;
      if (left + menuWidth > window.innerWidth - 12) {
        left = Math.max(12, window.innerWidth - menuWidth - 12);
      }
      let top = rect.bottom + 6;
      countryMenu.hidden = false;
      const menuHeight = countryMenu.offsetHeight || 240;
      if (top + menuHeight > window.innerHeight - 12) {
        top = Math.max(12, rect.top - menuHeight - 6);
      }
      countryMenu.style.left = `${left}px`;
      countryMenu.style.top = `${top}px`;
      trigger.setAttribute("aria-expanded", "true");
      const currentDial = floatSupplierDial?.value || "";
      countryOptions.forEach((option) => {
        option.classList.toggle("is-selected", option.dataset.dial === currentDial);
      });
      countrySearch?.focus();
    };

    floatRoot?.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-stock-country-trigger]");
      if (trigger && floatRoot.contains(trigger)) {
        event.preventDefault();
        if (trigger.getAttribute("aria-expanded") === "true") closeCountryMenu();
        else openCountryMenu(trigger);
      }
    });

    document.addEventListener("click", (event) => {
      const option = event.target.closest?.(".stock-country-option");
      if (option && countryMenu && !countryMenu.hidden && countryMenu.contains(option)) {
        event.preventDefault();
        const { dial, iso } = option.dataset;
        if (dial && iso) {
          setFloatCountry(dial, iso);
          autoApplyDetailsToReady({ silent: true });
        }
        closeCountryMenu();
        renderSummary();
        return;
      }
      if (
        countryMenu &&
        !countryMenu.hidden &&
        !countryMenu.contains(event.target) &&
        !event.target.closest?.("[data-stock-country-trigger]")
      ) {
        closeCountryMenu();
      }
    });

    countrySearch?.addEventListener("input", () => {
      const q = countrySearch.value.trim().toLowerCase();
      countryOptions.forEach((option) => {
        const hay = `${option.dataset.name} ${option.dataset.dial} ${option.dataset.iso}`.toLowerCase();
        if (option.parentElement) {
          option.parentElement.hidden = Boolean(q) && !hay.includes(q);
        }
      });
    });

    panel.addEventListener("click", (event) => {
      if (mode !== "request") return;
      const header = event.target.closest("[data-stock-request-shop-header]");
      if (!header || !panel.contains(header)) return;
      event.preventDefault();
      setRequestingShop(header.dataset.shopId, header.dataset.shopName);
    });

    panel.addEventListener("keydown", (event) => {
      if (mode !== "request") return;
      if (event.key !== "Enter" && event.key !== " ") return;
      const header = event.target.closest("[data-stock-request-shop-header]");
      if (!header || !panel.contains(header)) return;
      event.preventDefault();
      setRequestingShop(header.dataset.shopId, header.dataset.shopName);
    });

    const restoreMatrixFields = () => {
      cells().forEach((cell) => {
        const isRequesting =
          mode === "request" &&
          requestingShopId &&
          cell.dataset.shopId === requestingShopId;
        cell
          .querySelectorAll("[data-stock-qty], [data-stock-buying-price]")
          .forEach((field) => {
            field.disabled = Boolean(isRequesting);
          });
        // Keep hidden line meta enabled only when the cell still has qty.
        const filled = cellQty(cell) > 0 && !isRequesting;
        cell.querySelectorAll("[data-stock-field]").forEach((field) => {
          field.disabled = !filled;
        });
      });
    };

    let submitInFlight = false;

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submitInFlight) return;
      const ready = collectReady();
      if (focusFirstIncomplete(ready)) return;
      if (mode === "request" && requestingShopInput) {
        requestingShopInput.value = requestingShopId;
      }

      submitInFlight = true;
      if (submitBtn) submitBtn.disabled = true;
      const pendingLabel =
        mode === "in"
          ? "Submitting stock in…"
          : mode === "out"
            ? "Submitting stock out…"
            : "Submitting request…";
      setApplyStatus(pendingLabel);
      enableReadyFields(ready);

      try {
        const body = new FormData(form);
        body.set("ajax", "1");
        const response = await fetch(
          form.getAttribute("action") || window.location.href,
          {
            method: "POST",
            headers: {
              Accept: "application/json",
              "X-Requested-With": "XMLHttpRequest",
            },
            credentials: "same-origin",
            body,
          }
        );
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          const errors = Array.isArray(data.errors) ? data.errors.filter(Boolean) : [];
          const error =
            data.error ||
            errors[0] ||
            "Could not submit. Your entries were kept — fix the issue and try again.";
          setApplyStatus(error, true);
          restoreMatrixFields();
          submitInFlight = false;
          renderSummary();
          return;
        }
        setApplyStatus(data.message || "Submitted successfully.");
        window.location.assign(data.next || window.location.href);
      } catch (_error) {
        setApplyStatus(
          "Network error. Your entries were kept — try again.",
          true
        );
        restoreMatrixFields();
        submitInFlight = false;
        renderSummary();
      }
    });

    document.addEventListener("stock-catalog:rendered", () => {
      syncRequestingColumn();
      cells().forEach(markFilled);
      renderSummary();
    });
    document.addEventListener("stock-catalog:busy", (event) => {
      catalogBusy = Boolean(event.detail?.busy);
      renderSummary();
    });

    syncRequestingColumn();
    renderSummary();
    return;
  }
  /* ── End multi-shop matrix ───────────────────────────────────────────── */

  const floatRoot =
    form.querySelector("[data-stock-float]") ||
    document.querySelector("[data-stock-float]");
  const emptyEl = floatRoot?.querySelector("[data-stock-float-empty]");
  const heldEl = floatRoot?.querySelector("[data-stock-float-held]");
  const heldCountEl = floatRoot?.querySelector("[data-stock-float-held-count]");
  const summaryEl = floatRoot?.querySelector("[data-stock-float-summary]");
  const linesEl = floatRoot?.querySelector("[data-stock-float-lines]");
  const clearBtn = floatRoot?.querySelector("[data-stock-float-clear]");
  const submitBtn = floatRoot?.querySelector("[data-stock-float-submit]");
  const readyCountEl = floatRoot?.querySelector("[data-stock-ready-count]");
  const readyUnitsEl = floatRoot?.querySelector("[data-stock-ready-units]");
  const usesCatalogApi = panel.hasAttribute("data-stock-catalog-api");
  let catalogBusy = usesCatalogApi && panel.hasAttribute("data-stock-catalog-busy");
  const simpleCatalog = panel.hasAttribute("data-stock-catalog-simple");
  const applyPanel = floatRoot?.querySelector("[data-stock-float-apply]");
  const applyBtn = floatRoot?.querySelector("[data-stock-float-apply-btn]");
  const applyStatus = floatRoot?.querySelector("[data-stock-float-apply-status]");
  const floatSupplierName = floatRoot?.querySelector("[data-stock-float-supplier-name]");
  const floatSupplierDial = floatRoot?.querySelector("[data-stock-float-supplier-dial]");
  const floatSupplierPhone = floatRoot?.querySelector("[data-stock-float-supplier-phone]");
  const floatSupplierId = floatRoot?.querySelector("[data-stock-float-supplier-id]");
  const floatPayment = floatRoot?.querySelector("[data-stock-float-payment]");
  const floatReason = floatRoot?.querySelector("[data-stock-float-reason]");
  const floatRefund = floatRoot?.querySelector("[data-stock-float-refund]");
  const floatRefundAmount = floatRoot?.querySelector("[data-stock-float-refund-amount]");
  const floatRefundAmountWrap = floatRoot?.querySelector("[data-stock-float-refund-amount-wrap]");
  markOptionalLabel(floatSupplierPhone, stockReq.in.supplier);
  markOptionalLabel(floatSupplierName, stockReq.in.supplier);
  markOptionalLabel(floatPayment, stockReq.in.payment_status);
  markOptionalLabel(floatReason, stockReq.out.reason);
  markOptionalLabel(floatRefund, stockReq.out.refund);
  const loginCodeInput = floatRoot?.querySelector("[data-stock-float-login-code]");
  const loginStatusEl = floatRoot?.querySelector("[data-stock-float-login-status]");
  const verifyLoginUrl = form.getAttribute("data-verify-login-url") || "";
  const requiresLoginCode = Boolean(loginCodeInput);

  const getRows = () => [
    ...panel.querySelectorAll("[data-item-row][data-item-id]"),
    ...(document.querySelector("[data-stock-parked]")?.querySelectorAll("[data-item-row][data-item-id]") || []),
  ];
  // Deduplicate (parked rows are also under panel if nested).
  const rows = () => {
    const seen = new Set();
    return getRows().filter((row) => {
      const id = row.getAttribute("data-item-id");
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  };
  let appliedDetails = null;
  let loginVerified = false;
  let loginVerifyTimer = null;
  let loginVerifySeq = 0;
  let autoStockInFlight = false;
  let autoStockInTimer = null;

  const getInputsRow = (row) => {
    if (!row) return null;
    if (typeof panel.ensurePickInputs === "function") {
      panel.ensurePickInputs(row);
    }
    const nested = row.querySelector(":scope > [data-stock-item-inputs]");
    if (nested) return nested;
    const next = row.nextElementSibling;
    return next?.matches?.("[data-stock-item-inputs]") ? next : null;
  };

  const findItemRowFromNode = (node) => {
    const inputsRow = node?.closest?.("[data-stock-item-inputs]");
    if (!inputsRow) return null;
    const parentRow = inputsRow.closest("[data-item-row][data-item-id]");
    if (parentRow) return parentRow;
    const prev = inputsRow.previousElementSibling;
    return prev?.matches?.("[data-item-row][data-item-id]") ? prev : null;
  };

  const refreshIcons = () => {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const floatToggle = floatRoot?.querySelector("[data-stock-float-toggle]");
  const floatCollapseMq = window.matchMedia("(max-width: 1199px)");
  const floatCollapseKey = `stock-float-collapsed:${mode}`;

  const setFloatCollapsed = (collapsed, { persist = true } = {}) => {
    if (!floatRoot || !floatToggle) return;
    const next = Boolean(collapsed) && floatCollapseMq.matches;
    floatRoot.classList.toggle("is-collapsed", next);
    floatToggle.setAttribute("aria-expanded", next ? "false" : "true");
    floatToggle.setAttribute(
      "aria-label",
      next ? "Open submit section" : "Close submit section"
    );
    if (persist && floatCollapseMq.matches) {
      try {
        sessionStorage.setItem(floatCollapseKey, next ? "1" : "0");
      } catch (_) {
        /* ignore */
      }
    }
  };

  const readStoredFloatCollapsed = () => {
    try {
      const stored = sessionStorage.getItem(floatCollapseKey);
      if (stored === "0") return false;
      if (stored === "1") return true;
    } catch (_) {
      /* ignore */
    }
    return true;
  };

  if (floatRoot && floatToggle) {
    setFloatCollapsed(readStoredFloatCollapsed(), { persist: false });
    floatToggle.addEventListener("click", () => {
      if (!floatCollapseMq.matches) return;
      setFloatCollapsed(!floatRoot.classList.contains("is-collapsed"));
    });
    const syncFloatCollapseForViewport = () => {
      if (!floatCollapseMq.matches) {
        floatRoot.classList.remove("is-collapsed");
        floatToggle.setAttribute("aria-expanded", "true");
        floatToggle.removeAttribute("aria-label");
        return;
      }
      setFloatCollapsed(readStoredFloatCollapsed(), { persist: false });
    };
    if (typeof floatCollapseMq.addEventListener === "function") {
      floatCollapseMq.addEventListener("change", syncFloatCollapseForViewport);
    } else if (typeof floatCollapseMq.addListener === "function") {
      floatCollapseMq.addListener(syncFloatCollapseForViewport);
    }
  }

  const tracksSerial = (row) =>
    mode !== "request" && row.dataset.trackSerial === "1";

  const getSerialRows = (row) =>
    [...(getInputsRow(row)?.querySelectorAll(".stock-serial-row") || [])];

  const normalizeSerial = (value) => String(value || "").trim().toUpperCase();

  const collectSerials = (row) => {
    const seen = new Set();
    const serials = [];
    getSerialRows(row).forEach((serialRow) => {
      if (serialRow.dataset.serialBlocked === "1") return;
      const serial = normalizeSerial(
        serialRow.querySelector("[data-stock-serial-input]")?.value
      );
      if (!serial || seen.has(serial)) return;
      seen.add(serial);
      serials.push(serial);
    });
    return serials;
  };

  const updateSerialRemoveButtons = (row) => {
    const serialRows = getSerialRows(row);
    serialRows.forEach((serialRow) => {
      const removeBtn = serialRow.querySelector("[data-stock-serial-remove]");
      if (!removeBtn) return;
      removeBtn.hidden = serialRows.length <= 1;
    });
  };

  const phoneDigits = (value) => String(value || "").replace(/\D+/g, "");

  const normalizeNationalPhone = (raw, dial = "+254") => {
    let digits = phoneDigits(raw);
    const dialDigitsOnly = phoneDigits(dial);
    if (dialDigitsOnly && digits.startsWith(dialDigitsOnly) && digits.length > dialDigitsOnly.length) {
      digits = digits.slice(dialDigitsOnly.length);
    }
    while (digits.startsWith("0")) digits = digits.slice(1);
    return digits.slice(0, 9);
  };

  const setCountryOnField = (root, dial, iso) => {
    if (!root) return;
    const dialInput =
      root.querySelector("[data-stock-supplier-dial]") ||
      root.querySelector("[data-stock-float-supplier-dial]");
    const isoInput =
      root.querySelector("[data-stock-supplier-iso]") ||
      root.querySelector("[data-stock-float-supplier-iso]");
    const flagImg = root.querySelector("[data-stock-flag-img]");
    const dialDisplay = root.querySelector("[data-stock-dial-display]");
    const phoneInput =
      root.querySelector("[data-stock-supplier-phone]") ||
      root.querySelector("[data-stock-float-supplier-phone]");
    if (dialInput && dial) dialInput.value = dial;
    if (isoInput && iso) isoInput.value = iso;
    if (dialDisplay && dial) dialDisplay.textContent = dial;
    if (flagImg && iso) {
      flagImg.src = `https://flagcdn.com/w40/${String(iso).toLowerCase()}.png`;
    }
    if (phoneInput && phoneInput.value) {
      phoneInput.value = normalizeNationalPhone(phoneInput.value, dial || dialInput?.value || "+254");
    }
  };

  const normalizePhoneInput = (input) => {
    if (!(input instanceof HTMLInputElement)) return "";
    if (
      !input.matches("[data-stock-supplier-phone], [data-stock-float-supplier-phone]")
    ) {
      return input.value;
    }
    const root = input.closest("[data-stock-phone-field]") || input.parentElement;
    const dial =
      root?.querySelector("[data-stock-supplier-dial], [data-stock-float-supplier-dial]")
        ?.value || "+254";
    const start = input.selectionStart;
    const before = input.value;
    const normalized = normalizeNationalPhone(before, dial);
    if (normalized !== before) {
      input.value = normalized;
      if (typeof start === "number") {
        const next = Math.min(normalized.length, start);
        input.setSelectionRange(next, next);
      }
    }
    return normalized;
  };

  const syncRefundAmountVisibility = (root, refundValue) => {
    if (!root) return;
    const show = String(refundValue || "").toLowerCase() === "yes";
    root
      .querySelectorAll("[data-stock-refund-amount-wrap], [data-stock-float-refund-amount-wrap]")
      .forEach((wrap) => {
        wrap.hidden = !show;
        wrap.classList.toggle("is-refund-amount-visible", show);
        if (!show) {
          const amountInput = wrap.querySelector(
            "[data-stock-refund-amount], [data-stock-float-refund-amount]"
          );
          if (amountInput) amountInput.value = "";
        }
      });
  };

  const syncRefundFromSelect = (select) => {
    if (!(select instanceof HTMLSelectElement)) return;
    if (select.matches("[data-stock-float-refund]")) {
      const scope =
        select.closest("[data-stock-float-apply]") ||
        floatRoot ||
        select.parentElement;
      syncRefundAmountVisibility(scope, select.value);
      return;
    }
    if (select.matches("[data-stock-refund]")) {
      const scope =
        select.closest("[data-stock-item-inputs]") ||
        select.closest(".stock-item-inputs") ||
        select.parentElement;
      syncRefundAmountVisibility(scope, select.value);
    }
  };

  const writeSupplierMeta = (row, details) => {
    if (!details) return;
    const inputs = getInputsRow(row);
    if (!inputs) return;
    const payment = inputs.querySelector("[data-stock-payment]");
    const name = inputs.querySelector("[data-stock-supplier-name]");
    const phone = inputs.querySelector("[data-stock-supplier-phone]");
    const supplierId = inputs.querySelector("[data-stock-supplier-id]");
    if (payment) payment.value = details.payment;
    if (name) name.value = details.name;
    setCountryOnField(inputs, details.dial, details.iso || "KE");
    if (phone) phone.value = normalizeNationalPhone(details.phone, details.dial || "+254");
    if (supplierId) supplierId.value = details.supplierId ? String(details.supplierId) : "";
  };

  const writeOutMeta = (row, details) => {
    if (!details) return;
    const inputs = getInputsRow(row);
    if (!inputs) return;
    const reason = inputs.querySelector("[data-stock-reason]");
    const refund = inputs.querySelector("[data-stock-refund]");
    const amount = inputs.querySelector("[data-stock-refund-amount]");
    if (reason) reason.value = details.reason || "";
    if (refund) {
      refund.value = details.refund || "";
      syncRefundFromSelect(refund);
    }
    if (amount && details.refund === "yes") {
      amount.value = details.refundAmount || "";
    }
  };

  const syncSerialQuantity = (row) => {
    if (!tracksSerial(row)) return 0;
    const inputs = getInputsRow(row);
    const serialHidden = inputs?.querySelector("[data-stock-serials]");
    const qtyInput = inputs?.querySelector("[data-stock-qty]");
    const countEl = inputs?.querySelector("[data-stock-serial-count]");
    const serials = collectSerials(row);
    if (serialHidden) serialHidden.value = serials.join("\n");
    if (qtyInput) qtyInput.value = serials.length ? String(serials.length) : "";
    if (countEl) countEl.textContent = String(serials.length);
    updateSerialRemoveButtons(row);
    return serials.length;
  };

  const createSerialRow = (row, { serial = "", enabled = true } = {}) => {
    const list = getInputsRow(row)?.querySelector("[data-stock-serial-list]");
    if (!list) return null;

    const serialRow = document.createElement("div");
    serialRow.className = "stock-serial-row";

    const wrap = document.createElement("div");
    wrap.className = "stock-serial-input-wrap";
    if (mode === "out") wrap.setAttribute("data-serial-search-root", "");
    serialRow.appendChild(wrap);

    const input = document.createElement("input");
    input.type = "text";
    input.placeholder =
      mode === "out" ? "Search serial to stock out" : "Enter serial number";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.setAttribute("data-stock-serial-input", "");
    input.setAttribute("data-stock-field", "");
    if (mode === "out") input.setAttribute("data-serial-search", "");
    input.value = serial;
    input.disabled = !enabled;
    wrap.appendChild(input);

    if (mode === "out") {
      const suggest = document.createElement("div");
      suggest.className = "stock-supplier-suggest";
      suggest.setAttribute("data-serial-suggest", "");
      suggest.hidden = true;
      wrap.appendChild(suggest);
    }

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "stock-serial-remove";
    removeBtn.setAttribute("data-stock-serial-remove", "");
    removeBtn.setAttribute("aria-label", "Remove serial");
    removeBtn.hidden = true;
    removeBtn.innerHTML = '<i data-lucide="x" aria-hidden="true"></i>';
    serialRow.appendChild(removeBtn);

    list.appendChild(serialRow);
    window.MyShopSerialScan?.enhance?.(serialRow);
    refreshIcons();
    updateSerialRemoveButtons(row);
    return input;
  };

  const resetSerialList = (row) => {
    const inputs = getInputsRow(row);
    const list = inputs?.querySelector("[data-stock-serial-list]");
    if (!list) return;
    list.innerHTML = "";
    createSerialRow(row, { enabled: row.classList.contains("is-open") });
    const countEl = inputs?.querySelector("[data-stock-serial-count]");
    if (countEl) countEl.textContent = "0";
    const serialHidden = inputs?.querySelector("[data-stock-serials]");
    if (serialHidden) serialHidden.value = "";
    const qtyInput = inputs?.querySelector("[data-stock-qty]");
    if (qtyInput) qtyInput.value = "";
  };

  const refreshRowState = (row) => {
    syncSerialQuantity(row);
    syncFilled(row);
    renderSummary();
  };

  const addSerialRow = (row) => {
    const input = createSerialRow(row, { enabled: row.classList.contains("is-open") });
    refreshRowState(row);
    input?.focus();
    return input;
  };

  const getQty = (row) => {
    if (tracksSerial(row)) return syncSerialQuantity(row);
    const input = getInputsRow(row)?.querySelector("[data-stock-qty]");
    const value = Number(input?.value || 0);
    return Number.isFinite(value) && value > 0 ? value : 0;
  };

  const rowHasBuyingPrice = (row) => {
    if (!stockReq.in.buying_price) return true;
    const raw = (getInputsRow(row)?.querySelector("[data-stock-buying-price]")?.value || "").trim();
    if (!raw) return false;
    const n = Number(raw);
    return Number.isFinite(n) && n >= 0 && Number.isInteger(n);
  };

  const rowHasSupplierDetails = (row) => {
    if (!stockReq.in.supplier && !stockReq.in.payment_status) return true;
    const inputs = getInputsRow(row);
    if (!inputs) return false;
    const payment = (inputs.querySelector("[data-stock-payment]")?.value || "").trim();
    const name = (inputs.querySelector("[data-stock-supplier-name]")?.value || "").trim();
    const phone = (inputs.querySelector("[data-stock-supplier-phone]")?.value || "").trim();
    const dial = (inputs.querySelector("[data-stock-supplier-dial]")?.value || "").trim();
    const supplierOk =
      !stockReq.in.supplier || Boolean(name && phone && dial);
    const paymentOk = !stockReq.in.payment_status || Boolean(payment);
    return supplierOk && paymentOk;
  };

  const rowHasOutDetails = (row) => {
    if (!stockReq.out.reason && !stockReq.out.refund) return true;
    const inputs = getInputsRow(row);
    if (!inputs) return false;
    const reason = (inputs.querySelector("[data-stock-reason]")?.value || "").trim();
    const refund = (inputs.querySelector("[data-stock-refund]")?.value || "").trim().toLowerCase();
    if (stockReq.out.reason && !reason) return false;
    if (stockReq.out.refund) {
      if (refund !== "yes" && refund !== "no") return false;
      if (refund === "yes") {
        const amount = Number(inputs.querySelector("[data-stock-refund-amount]")?.value || 0);
        return Number.isFinite(amount) && amount > 0 && Number.isInteger(amount);
      }
    }
    return true;
  };

  const rowReadyToPark = (row) => {
    const qty = getQty(row);
    if (mode === "in") {
      if (!qty) return false;
      if (stockReq.in.buying_price && !rowHasBuyingPrice(row)) return false;
      return true;
    }
    return qty > 0;
  };

  const rowIncompleteReason = (row) => {
    if (mode !== "in") return null;
    const qty = getQty(row);
    const hasPrice = rowHasBuyingPrice(row);
    if (qty > 0 && !hasPrice) return "price";
    if (hasPrice && qty === 0) return "qty";
    return null;
  };

  const focusIncompleteItem = (row, reason) => {
    setRowOpen(row, true);
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    const inputs = getInputsRow(row);
    if (reason === "price") {
      inputs?.querySelector("[data-stock-buying-price]")?.focus();
      setApplyStatus("Enter buying price before moving to another item.", true);
      return;
    }
    const qtyFocus =
      inputs?.querySelector("[data-stock-serial-input]") ||
      inputs?.querySelector("[data-stock-qty]");
    qtyFocus?.focus();
    setApplyStatus("Enter quantity or serial numbers before moving to another item.", true);
  };

  const parkSelectedRow = (row) => {
    const inputs = getInputsRow(row);
    const parked = parkedRoot();
    const wrap = parked?.closest?.(".buy-stock-simple-parked");
    if (!row || !inputs || !parked) return;
    if (wrap) wrap.hidden = false;
    const nested = inputs.parentElement === row;
    if (parked.contains(row)) {
      if (!nested && row.nextElementSibling !== inputs) {
        parked.insertBefore(inputs, row.nextSibling);
      }
    } else if (nested) {
      parked.insertBefore(row, parked.firstElementChild);
    } else {
      parked.insertBefore(row, parked.firstElementChild);
      parked.insertBefore(inputs, row.nextSibling);
    }
    row.classList.add("is-selected");
    syncParkedVisibility();
    syncItemRemoveControls(row);
  };

  const moveItemPairToTop = (row) => {
    const inputs = getInputsRow(row);
    if (!row || !inputs) return;
    const parked = parkedRoot();
    const simpleParked = parked?.closest?.(".buy-stock-simple-parked");
    // Simple buy-stock: selected items live in the stack above search results.
    if (simpleParked && (simpleCatalog || getQty(row) > 0)) {
      parkSelectedRow(row);
      return;
    }
    const nested = inputs.parentElement === row;
    const parent = row.parentElement;
    if (!parent) return;
    const anchor = parent.firstElementChild;
    if (!anchor || anchor === row) {
      if (!nested && row.nextElementSibling !== inputs) {
        parent.insertBefore(inputs, row.nextSibling);
      }
      return;
    }
    parent.insertBefore(row, anchor);
    if (!nested) parent.insertBefore(inputs, row.nextSibling);
  };

  const moveItemPairToBottom = (row) => {
    const inputs = getInputsRow(row);
    const parent = row?.parentElement;
    if (!parent || !inputs) return;
    const nested = inputs.parentElement === row;
    parent.appendChild(row);
    if (!nested) parent.appendChild(inputs);
  };

  const clearEmptyOpenRow = (row) => {
    if (tracksSerial(row)) resetSerialList(row);
    clearItemMeta(row);
    getInputsRow(row)
      ?.querySelectorAll("[data-stock-field]")
      .forEach((field) => {
        if (
          field.matches(
            "[data-stock-serial-input], [data-stock-serials], [data-stock-buying-price], [data-stock-payment], [data-stock-supplier-name], [data-stock-supplier-dial], [data-stock-supplier-phone]"
          )
        ) {
          return;
        }
        if (field.type === "hidden" && !field.matches("[data-stock-qty]")) return;
        if (field.tagName === "SELECT") field.value = "";
        else field.value = "";
      });
    const countEl = getInputsRow(row)?.querySelector("[data-stock-serial-count]");
    if (countEl) countEl.textContent = "0";
  };

  const syncParkedVisibility = () => {
    const root = parkedRoot();
    const wrap = root?.closest(".buy-stock-simple-parked");
    if (wrap) wrap.hidden = !root?.querySelector("[data-item-row]");
    const addAnother = panel.querySelector("[data-stock-add-another]");
    if (addAnother) {
      const hasSelected = Boolean(root?.querySelector("[data-item-row]"));
      const collapsed = panel.classList.contains("is-picker-collapsed");
      addAnother.hidden = !hasSelected || !collapsed;
    }
    if (
      simpleCatalog &&
      panel.classList.contains("is-picker-collapsed") &&
      !root?.querySelector("[data-item-row]")
    ) {
      panel.dispatchEvent(new CustomEvent("stock-catalog:expand-picker"));
    }
  };

  const syncItemRemoveControls = (row) => {
    if (!row) return;
    const show =
      row.classList.contains("is-open") ||
      row.classList.contains("is-filled") ||
      row.classList.contains("is-selected") ||
      isParkedRow(row);
    row.querySelectorAll("[data-stock-item-remove]").forEach((btn) => {
      btn.hidden = !show;
    });
    row.querySelectorAll(".buy-stock-pick-select").forEach((btn) => {
      btn.hidden = show;
    });
  };

  const removeItemRow = (row, { silent = false } = {}) => {
    if (!row) return;
    const inputs = getInputsRow(row);
    if (tracksSerial(row)) resetSerialList(row);
    clearItemMeta(row);
    inputs?.querySelectorAll("[data-stock-field]").forEach((field) => {
      if (field.tagName === "SELECT") field.value = "";
      else if (field.type !== "hidden" || field.matches("[data-stock-qty]")) {
        field.value = "";
      }
    });
    const countEl = inputs?.querySelector("[data-stock-serial-count]");
    if (countEl) countEl.textContent = "0";
    row.classList.remove("is-selected");
    const wasParked = isParkedRow(row);
    setRowOpen(row, false);
    syncFilled(row);

    if (wasParked) {
      row.remove();
      if (inputs && inputs.parentElement !== null && !row.contains(inputs)) {
        inputs.remove();
      }
      syncParkedVisibility();
    } else {
      syncItemRemoveControls(row);
    }
    if (!silent) setApplyStatus("Item removed.");
  };

  const closeAndParkRow = (row) => {
    const incomplete = rowIncompleteReason(row);
    if (incomplete) {
      // Allow dismissing a partial selection instead of trapping the user.
      // Simple buy-stock keeps partial lines visible so the user can finish them.
      if (simpleCatalog) {
        parkSelectedRow(row);
        setRowOpen(row, true);
        return true;
      }
      removeItemRow(row);
      return true;
    }
    if (rowReadyToPark(row)) {
      // Fill meta from float only when this item still needs details.
      if (mode === "in" && !rowHasSupplierDetails(row)) {
        const details = readFloatDetails();
        const supplierOk =
          !stockReq.in.supplier ||
          Boolean(details.name && details.dial && details.phone);
        const paymentOk = !stockReq.in.payment_status || Boolean(details.payment);
        if (supplierOk && paymentOk) {
          appliedDetails = details;
          writeSupplierMeta(row, details);
        } else if (appliedDetails) {
          writeSupplierMeta(row, appliedDetails);
        }
      }
      if (mode === "out" && !rowHasOutDetails(row)) {
        const details = readFloatDetails();
        const reasonOk = !stockReq.out.reason || Boolean(details.reason);
        const refundOk =
          !stockReq.out.refund ||
          ((details.refund === "yes" || details.refund === "no") &&
            (details.refund !== "yes" ||
              (Number.isInteger(Number(details.refundAmount)) &&
                Number(details.refundAmount) > 0 &&
                Number.isFinite(Number(details.refundAmount)))));
        if (reasonOk && refundOk) {
          appliedDetails = details;
          writeOutMeta(row, details);
        } else if (appliedDetails) {
          writeOutMeta(row, appliedDetails);
        }
      }
      if (simpleCatalog) {
        parkSelectedRow(row);
        setRowOpen(row, true);
      } else {
        setRowOpen(row, false);
        moveItemPairToTop(row);
      }
      syncFilled(row);
      window.setTimeout(() => {
        const searchInput = panel.querySelector("[data-item-search]");
        if (searchInput && String(searchInput.value || "").trim()) {
          searchInput.value = "";
          searchInput.dispatchEvent(new Event("search", { bubbles: true }));
        }
      }, 0);
      return true;
    }
    if (simpleCatalog) {
      parkSelectedRow(row);
      setRowOpen(row, true);
      syncFilled(row);
      return true;
    }
    setRowOpen(row, false);
    if (getQty(row) === 0 && !(mode === "in" && rowHasBuyingPrice(row))) {
      clearEmptyOpenRow(row);
    }
    syncFilled(row);
    return true;
  };

  const setFieldsEnabled = (row, enabled) => {
    const inputs = getInputsRow(row);
    inputs?.querySelectorAll("[data-stock-field]").forEach((field) => {
      field.disabled = !enabled;
    });
    inputs?.querySelectorAll("[data-stock-country-trigger]").forEach((btn) => {
      btn.disabled = !enabled;
    });
  };

  const setRowOpen = (row, open) => {
    const inputs = getInputsRow(row);
    const keepVisible = simpleCatalog && (open || isParkedRow(row) || row.classList.contains("is-selected"));
    const show = Boolean(open || keepVisible);
    row.classList.toggle("is-open", show);
    row.setAttribute("aria-expanded", String(show));
    if (inputs) inputs.hidden = !show;
    setFieldsEnabled(row, show);
    if (show) {
      const refundSelect = inputs?.querySelector("[data-stock-refund]");
      if (refundSelect) syncRefundFromSelect(refundSelect);
      if (open) {
        const focusTarget =
          inputs?.querySelector("[data-stock-qty]:not([type='hidden'])") ||
          inputs?.querySelector("[data-stock-buying-price]") ||
          inputs?.querySelector("[data-stock-serial-input]");
        focusTarget?.focus();
      }
      refreshRowState(row);
    }
    syncItemRemoveControls(row);
  };

  const syncFilled = (row) => {
    const qty = getQty(row);
    const filled = qty > 0;
    row.classList.toggle("is-filled", filled);
    syncItemRemoveControls(row);

    const current = Number(row.dataset.itemStock || 0);
    const qtyEl = row.querySelector("[data-stock-display-qty]");
    if (!qtyEl) return;

    qtyEl.classList.remove("is-projected", "is-projected-out", "is-empty");

    if (filled && mode === "in") {
      const total = current + qty;
      qtyEl.textContent = `${current} + ${qty} = ${total}`;
      qtyEl.title = `Was ${current}, adding ${qty}`;
      qtyEl.classList.add("is-projected");
      return;
    }

    if (filled && mode === "out") {
      const total = Math.max(0, current - qty);
      qtyEl.textContent = `${current} − ${qty} = ${total}`;
      qtyEl.title = `Was ${current}, removing ${qty}`;
      qtyEl.classList.add("is-projected", "is-projected-out");
      return;
    }

    qtyEl.textContent = String(current);
    qtyEl.removeAttribute("title");
    if (current === 0) qtyEl.classList.add("is-empty");
  };

  const parkedRoot = () => document.querySelector("[data-stock-parked]");

  const isParkedRow = (row) => {
    const root = parkedRoot();
    return Boolean(root && row && root.contains(row));
  };

  const collectReady = () =>
    rows()
      .map((row) => {
        const qty = getQty(row);
        if (!qty) return null;
        return {
          row,
          id: row.dataset.itemId,
          name: row.dataset.itemName || "Item",
          quantity: qty,
          held: isParkedRow(row),
        };
      })
      .filter(Boolean);

  const isCatalogBusy = () =>
    catalogBusy || panel.getAttribute("data-stock-catalog-busy") === "1";

  const setApplyStatus = (message, isError = false) => {
    if (!applyStatus) return;
    if (!message) {
      applyStatus.hidden = true;
      applyStatus.textContent = "";
      applyStatus.classList.remove("is-error");
      return;
    }
    applyStatus.hidden = false;
    applyStatus.textContent = message;
    applyStatus.classList.toggle("is-error", isError);
  };

  const floatSupplierReady = () => {
    if (mode !== "in") return false;
    const details = readFloatDetails();
    if (
      stockReq.in.supplier &&
      !(
        details.name &&
        details.dial &&
        details.phone &&
        details.phone.length === 9
      )
    ) {
      return false;
    }
    if (stockReq.in.payment_status && !details.payment) return false;
    return true;
  };

  const supplierCoreReady = () => {
    if (mode !== "in") return false;
    if (!stockReq.in.supplier) return true;
    const details = readFloatDetails();
    return Boolean(
      details.name &&
        details.dial &&
        details.phone &&
        details.phone.length === 9
    );
  };

  const floatOutReady = () => {
    if (mode !== "out") return false;
    const details = readFloatDetails();
    if (stockReq.out.reason && !details.reason) return false;
    if (stockReq.out.refund) {
      if (details.refund !== "yes" && details.refund !== "no") return false;
      if (details.refund === "yes") {
        const amount = Number(details.refundAmount);
        return Number.isFinite(amount) && amount > 0 && Number.isInteger(amount);
      }
    }
    return true;
  };

  const canSubmitStockIn = (ready) => {
    if (!ready.length) return false;
    if (!floatSupplierReady()) return false;
    if (!ready.every((item) => rowHasBuyingPrice(item.row))) return false;
    return ready.every((item) => rowHasSupplierDetails(item.row));
  };

  const canSubmitStockOut = (ready) => {
    if (!ready.length) return false;
    if (!floatOutReady()) return false;
    return ready.every((item) => rowHasOutDetails(item.row));
  };

  const autoApplyDetailsToReady = ({ silent = true } = {}) => {
    const ready = collectReady();
    if (!ready.length) return 0;
    if (mode === "in") {
      if (stockReq.in.supplier && !supplierCoreReady()) return 0;
      const details = readFloatDetails();
      appliedDetails = details.payment ? details : appliedDetails;
      ready.forEach((item) => writeSupplierMeta(item.row, details));
      if (!silent) {
        if (floatSupplierReady()) {
          setApplyStatus(
            `Supplier details applied to ${ready.length} item(s).`
          );
        } else if (stockReq.in.payment_status && !details.payment) {
          setApplyStatus(
            "Details applied. Select payment status to submit.",
            true
          );
        } else {
          setApplyStatus(`Details applied to ${ready.length} item(s).`);
        }
      }
      return ready.length;
    }
    if (mode === "out") {
      if ((stockReq.out.reason || stockReq.out.refund) && !floatOutReady()) {
        return 0;
      }
      const details = readFloatDetails();
      appliedDetails = details;
      ready.forEach((item) => writeOutMeta(item.row, details));
      if (!silent) {
        setApplyStatus(
          `Stock-out details applied to ${ready.length} item(s).`
        );
      }
      return ready.length;
    }
    return 0;
  };

  const revealAndFocus = (el) => {
    if (!el) return;
    if (floatRoot) {
      floatRoot.classList.remove("is-collapsed");
      floatRoot
        .querySelector("[data-stock-float-toggle]")
        ?.setAttribute("aria-expanded", "true");
    }
    if (applyPanel) applyPanel.hidden = false;
    try {
      el.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    } catch (_err) {
      /* ignore */
    }
    window.setTimeout(() => {
      try {
        el.focus({ preventScroll: true });
      } catch (_err) {
        el.focus?.();
      }
    }, 180);
  };

  const focusFirstIncomplete = (ready) => {
    if (isCatalogBusy()) {
      setApplyStatus("Still loading items — wait a moment, then submit.", true);
      revealAndFocus(submitBtn || floatRoot);
      return true;
    }
    if (!ready.length) {
      setApplyStatus("Add at least one item with quantity before submitting.", true);
      revealAndFocus(
        panel.querySelector("[data-item-search]") ||
          panel.querySelector("[data-stock-qty]") ||
          panel
      );
      return true;
    }
    if (mode === "in") {
      autoApplyDetailsToReady({ silent: true });
      const details = readFloatDetails();
      if (stockReq.in.supplier) {
        if (!details.phone || details.phone.length !== 9) {
          setApplyStatus("Enter a valid supplier phone.", true);
          revealAndFocus(floatSupplierPhone);
          return true;
        }
        if (!details.name) {
          setApplyStatus("Enter or select supplier name.", true);
          revealAndFocus(floatSupplierName);
          return true;
        }
      }
      if (stockReq.in.payment_status && !details.payment) {
        setApplyStatus("Select payment status before submitting.", true);
        revealAndFocus(floatPayment);
        return true;
      }
      const missingPrice = ready.find((item) => !rowHasBuyingPrice(item.row));
      if (missingPrice) {
        setRowOpen(missingPrice.row, true);
        setApplyStatus(`Enter buying price for ${missingPrice.name}.`, true);
        revealAndFocus(
          getInputsRow(missingPrice.row)?.querySelector("[data-stock-buying-price]") ||
            missingPrice.row
        );
        return true;
      }
      if (!ready.every((item) => rowHasSupplierDetails(item.row))) {
        setApplyStatus(
          "Supplier details are incomplete — check the submit panel.",
          true
        );
        revealAndFocus(floatPayment || floatSupplierPhone);
        return true;
      }
    }
    if (mode === "out") {
      autoApplyDetailsToReady({ silent: true });
      const details = readFloatDetails();
      if (stockReq.out.reason && !details.reason) {
        setApplyStatus("Choose a stock-out reason.", true);
        revealAndFocus(floatReason);
        return true;
      }
      if (stockReq.out.refund) {
        if (details.refund !== "yes" && details.refund !== "no") {
          setApplyStatus("Choose whether a refund applies.", true);
          revealAndFocus(floatRefund);
          return true;
        }
        if (details.refund === "yes") {
          const amount = Number(details.refundAmount);
          if (!Number.isFinite(amount) || amount <= 0 || !Number.isInteger(amount)) {
            setApplyStatus(
              "Enter a whole-number refund amount greater than zero.",
              true
            );
            revealAndFocus(floatRefundAmount);
            return true;
          }
        }
      }
      const missingOut = ready.find((item) => !rowHasOutDetails(item.row));
      if (missingOut) {
        setRowOpen(missingOut.row, true);
        setApplyStatus(
          "Stock-out details are incomplete — check the submit panel.",
          true
        );
        revealAndFocus(floatReason);
        return true;
      }
    }
    return false;
  };

  const getCsrf = () =>
    form.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const setLoginStatus = (message, { ok = false, error = false } = {}) => {
    if (!loginStatusEl) return;
    loginStatusEl.textContent =
      message || "Enter an active staff member’s 6-digit ID to stock in.";
    loginStatusEl.classList.toggle("is-ok", ok);
    loginStatusEl.classList.toggle("is-error", error);
  };

  const verifyLoginCode = async () => {
    if (!requiresLoginCode) return true;
    const code = (loginCodeInput?.value || "").trim();
    const current = ++loginVerifySeq;
    if (code.length < 6) {
      loginVerified = false;
      setLoginStatus(
        code.length
          ? `Enter ${6 - code.length} more digit${6 - code.length === 1 ? "" : "s"}.`
          : ""
      );
      if (submitBtn) {
        const ready = collectReady();
        submitBtn.disabled = ready.length === 0 || isCatalogBusy();
      }
      return false;
    }
    if (!/^\d{6}$/.test(code)) {
      loginVerified = false;
      setLoginStatus("Staff ID must be exactly 6 digits.", { error: true });
      if (submitBtn) {
        const ready = collectReady();
        submitBtn.disabled = ready.length === 0 || isCatalogBusy();
      }
      return false;
    }
    if (!verifyLoginUrl) {
      loginVerified = false;
      setLoginStatus("Verification is unavailable. Refresh and try again.", {
        error: true,
      });
      if (submitBtn) {
        const ready = collectReady();
        submitBtn.disabled = ready.length === 0 || isCatalogBusy();
      }
      return false;
    }

    try {
      const body = new URLSearchParams({ login_code: code });
      const response = await fetch(verifyLoginUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": getCsrf(),
        },
        credentials: "same-origin",
        body,
      });
      const data = await response.json().catch(() => ({}));
      if (current !== loginVerifySeq) return false;
      if (!response.ok || !data.ok) {
        loginVerified = false;
        setLoginStatus(data.error || "Not a valid active staff ID.", {
          error: true,
        });
        if (submitBtn) {
          const ready = collectReady();
          submitBtn.disabled = ready.length === 0 || isCatalogBusy();
        }
        return false;
      }
      loginVerified = true;
      setLoginStatus(
        `Verified: ${data.name || "staff"} (${data.employee_id || code}).`,
        { ok: true }
      );
      renderSummary();
      return true;
    } catch (_error) {
      if (current !== loginVerifySeq) return false;
      loginVerified = false;
      setLoginStatus("Could not verify staff ID. Try again.", { error: true });
      if (submitBtn) {
        const ready = collectReady();
        submitBtn.disabled = ready.length === 0 || isCatalogBusy();
      }
      return false;
    }
  };

  const renderSummary = () => {
    autoApplyDetailsToReady({ silent: true });
    const ready = collectReady();
    const units = ready.reduce((sum, item) => sum + item.quantity, 0);
    const heldCount = ready.filter((item) => item.held).length;
    const hasReady = ready.length > 0;
    const busy = isCatalogBusy();

    if (emptyEl) emptyEl.hidden = hasReady;
    if (heldEl) {
      heldEl.hidden = heldCount === 0;
      if (heldCountEl) heldCountEl.textContent = String(heldCount);
    }
    if (summaryEl) summaryEl.hidden = true;
    if (linesEl) linesEl.hidden = !hasReady;
    if (clearBtn) clearBtn.hidden = !hasReady;
    if (applyPanel) applyPanel.hidden = !(mode === "in" || mode === "out");
    if (submitBtn) {
      // Keep clickable so incomplete fields can be focused on submit.
      submitBtn.disabled = !hasReady || busy;
      submitBtn.classList.toggle("is-catalog-busy", busy);
    }
    if (readyCountEl) readyCountEl.textContent = String(ready.length);
    if (readyUnitsEl) readyUnitsEl.textContent = String(units);

    if (mode === "in") {
      if (!hasReady) {
        const needs =
          [
            stockReq.in.supplier ? "supplier" : "",
            stockReq.in.payment_status ? "payment" : "",
            stockReq.in.buying_price ? "buying price" : "",
          ].filter(Boolean);
        setApplyStatus(
          needs.length
            ? `Add item quantities, then enter ${needs.join(", ")}.`
            : "Add item quantities to stock in."
        );
      } else if (stockReq.in.supplier && !supplierCoreReady()) {
        setApplyStatus(
          "Enter supplier phone and name — details apply to all ready items.",
          true
        );
      } else if (!floatSupplierReady()) {
        setApplyStatus(
          stockReq.in.payment_status
            ? "Select payment status before submitting."
            : "Complete supplier details before submitting.",
          true
        );
      } else if (!ready.every((item) => rowHasBuyingPrice(item.row))) {
        setApplyStatus("Enter buying price on every stocked item.", true);
      } else if (requiresLoginCode && !loginVerified) {
        setApplyStatus(
          "Item details complete. Enter a valid staff ID to stock in."
        );
      } else {
        setApplyStatus(`Ready to stock in ${ready.length} item(s).`);
      }
    } else if (mode === "out") {
      if (!hasReady) {
        const needs = [
          stockReq.out.reason ? "reason" : "",
          stockReq.out.refund ? "refund" : "",
        ].filter(Boolean);
        setApplyStatus(
          needs.length
            ? `Add item quantities, then choose ${needs.join(" and ")}.`
            : "Add item quantities to stock out."
        );
      } else if (!floatOutReady()) {
        setApplyStatus(
          "Choose reason and refund — details apply to all ready items.",
          true
        );
      } else if (!ready.every((item) => rowHasOutDetails(item.row))) {
        setApplyStatus(
          "Stock-out details are incomplete on one or more items.",
          true
        );
      } else {
        setApplyStatus(`Ready to stock out ${ready.length} item(s).`);
      }
    } else if (!hasReady) {
      setApplyStatus("");
    }

    if (!linesEl) return;
    linesEl.innerHTML = "";
    ready.forEach((item) => {
      const complete =
        mode === "in"
          ? rowHasBuyingPrice(item.row) && rowHasSupplierDetails(item.row)
          : mode === "out"
            ? rowHasOutDetails(item.row)
            : true;
      const li = document.createElement("li");
      li.className = `stock-float-line stock-float-line--summary${
        complete ? " is-complete" : " is-incomplete"
      }${item.held ? " is-held" : ""}`;
      li.dataset.itemId = item.id;
      li.innerHTML = `
        <span class="stock-float-line-name"></span>
        ${item.held ? '<span class="stock-float-line-held">Held</span>' : ""}
        <span class="stock-float-line-qty"></span>
        <span class="stock-float-line-status" aria-label="${complete ? "Details complete" : "Details incomplete"}">
          <i data-lucide="${complete ? "check" : "x"}" aria-hidden="true"></i>
        </span>
        <button
          type="button"
          class="stock-float-line-remove"
          data-stock-float-remove
          data-item-id="${item.id}"
          aria-label="Remove ${String(item.name).replace(/"/g, "&quot;")}"
          title="Remove item"
        >
          <i data-lucide="x" aria-hidden="true"></i>
        </button>
      `;
      li.querySelector(".stock-float-line-name").textContent = item.name;
      li.querySelector(".stock-float-line-qty").textContent = String(item.quantity);
      linesEl.appendChild(li);
    });
    refreshIcons();
  };

  const readFloatDetails = () => {
    if (mode === "out") {
      const reason = (floatReason?.value || "").trim().toLowerCase();
      const refund = (floatRefund?.value || "").trim().toLowerCase();
      const refundAmount = (floatRefundAmount?.value || "").trim();
      if (floatRefund) syncRefundFromSelect(floatRefund);
      return { reason, refund, refundAmount };
    }
    const name = (floatSupplierName?.value || "").trim().toUpperCase();
    const dial = (floatSupplierDial?.value || "").trim();
    const iso = (floatRoot?.querySelector("[data-stock-float-supplier-iso]")?.value || "KE").trim();
    const phone = normalizeNationalPhone(floatSupplierPhone?.value || "", dial || "+254");
    if (floatSupplierPhone) floatSupplierPhone.value = phone;
    const payment = (floatPayment?.value || "").trim().toLowerCase();
    const supplierId = (floatSupplierId?.value || "").trim();
    return { name, dial, iso, phone, payment, supplierId };
  };

  const prepareStockInRows = () => {
    // Disable everything first so empty/closed items are not posted.
    rows().forEach((row) => setFieldsEnabled(row, false));
    rows().forEach((row) => {
      if (tracksSerial(row)) syncSerialQuantity(row);
    });
    const ready = collectReady();
    if (mode === "in") {
      const details = readFloatDetails();
      const supplierOk =
        !stockReq.in.supplier ||
        Boolean(details.name && details.dial && details.phone);
      const paymentOk = !stockReq.in.payment_status || Boolean(details.payment);
      if (supplierOk && paymentOk) {
        appliedDetails = details;
        ready.forEach((item) => writeSupplierMeta(item.row, details));
      } else if (
        details.name ||
        details.phone ||
        details.payment ||
        appliedDetails
      ) {
        const payload = paymentOk && supplierOk ? details : appliedDetails;
        if (payload) {
          ready.forEach((item) => writeSupplierMeta(item.row, payload));
        }
      }
    }
    ready.forEach((item) => {
      setRowOpen(item.row, true);
      if (tracksSerial(item.row)) syncSerialQuantity(item.row);
      setFieldsEnabled(item.row, true);
    });
    return ready;
  };

  const queueAutoStockInAndPrint = () => {
    if (!form.hasAttribute("data-supplier-print") || mode !== "in") return;
    window.clearTimeout(autoStockInTimer);
    autoStockInTimer = window.setTimeout(() => {
      submitStockInWithPrint();
    }, 280);
  };

  const submitStockInWithPrint = async () => {
    if (!form.hasAttribute("data-supplier-print") || mode !== "in") return false;
    if (autoStockInFlight || isCatalogBusy()) return false;
    if (requiresLoginCode && !loginVerified) return false;

    const ready = prepareStockInRows();
    if (!ready.length || !canSubmitStockIn(ready)) return false;

    autoStockInFlight = true;
    if (submitBtn) submitBtn.disabled = true;
    setApplyStatus("Recording stock and printing supplier receipt…");
    try {
      const body = new FormData(form);
      body.set("ajax", "1");
      const response = await fetch(
        form.getAttribute("action") || window.location.href,
        {
          method: "POST",
          headers: { Accept: "application/json" },
          credentials: "same-origin",
          body,
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        setApplyStatus(data.error || "Could not stock in items.", true);
        autoStockInFlight = false;
        if (submitBtn) submitBtn.disabled = false;
        renderSummary();
        return false;
      }
      setApplyStatus(data.message || "Stocked in. Printing supplier receipt…");
      if (data.receipt_text && window.RichcomPrinter?.printReceipt) {
        await window.RichcomPrinter.printReceipt({
          text: data.receipt_text,
          channel: data.print_via || "",
          qr: data.receipt_qr || null,
          fontStyle: data.receipt_font || null,
          ticket: data.receipt_ticket || null,
          paperWidth: data.receipt_paper_width || "",
        });
      }
      window.location.assign(data.next || window.location.href);
      return true;
    } catch (_) {
      setApplyStatus("Network error while stocking in.", true);
      autoStockInFlight = false;
      if (submitBtn) submitBtn.disabled = false;
      renderSummary();
      return false;
    }
  };

  const applyDetailsToReady = () => {
    const ready = collectReady();
    if (!ready.length) {
      setApplyStatus("Add item quantity/serials first.", true);
      return false;
    }
    if (mode === "out") {
      if (!floatOutReady()) {
        const details = readFloatDetails();
        if (stockReq.out.reason && !details.reason) {
          setApplyStatus("Choose a stock-out reason.", true);
          floatReason?.focus();
        } else if (
          stockReq.out.refund &&
          details.refund !== "yes" &&
          details.refund !== "no"
        ) {
          setApplyStatus("Choose whether a refund applies.", true);
          floatRefund?.focus();
        } else if (stockReq.out.refund && details.refund === "yes") {
          setApplyStatus(
            "Enter a whole-number refund amount greater than zero.",
            true
          );
          floatRefundAmount?.focus();
        } else {
          setApplyStatus("Complete stock-out details first.", true);
        }
        return false;
      }
      autoApplyDetailsToReady({ silent: false });
      renderSummary();
      return true;
    }

    if (stockReq.in.supplier && !supplierCoreReady()) {
      const details = readFloatDetails();
      if (!details.phone) {
        setApplyStatus("Enter supplier phone.", true);
        floatSupplierPhone?.focus();
      } else if (!details.name) {
        setApplyStatus("Enter supplier name.", true);
        floatSupplierName?.focus();
      } else {
        setApplyStatus("Select a country.", true);
        floatRoot
          ?.querySelector(
            "[data-stock-float-phone-wrap] [data-stock-country-trigger]"
          )
          ?.focus();
      }
      return false;
    }
    if (stockReq.in.payment_status && !floatSupplierReady()) {
      setApplyStatus("Select payment status before applying.", true);
      floatPayment?.focus();
      return false;
    }
    autoApplyDetailsToReady({ silent: false });
    renderSummary();
    return true;
  };

  const clearItemMeta = (row) => {
    const inputs = getInputsRow(row);
    if (!inputs) return;
    const buying = inputs.querySelector("[data-stock-buying-price]");
    const payment = inputs.querySelector("[data-stock-payment]");
    const name = inputs.querySelector("[data-stock-supplier-name]");
    const phone = inputs.querySelector("[data-stock-supplier-phone]");
    const supplierId = inputs.querySelector("[data-stock-supplier-id]");
    const reason = inputs.querySelector("[data-stock-reason]");
    const refund = inputs.querySelector("[data-stock-refund]");
    const refundAmount = inputs.querySelector("[data-stock-refund-amount]");
    if (buying) buying.value = "";
    if (payment) payment.value = "";
    if (name) name.value = "";
    if (phone) phone.value = "";
    if (supplierId) supplierId.value = "";
    if (reason) reason.value = "";
    if (refund) refund.value = "";
    if (refundAmount) refundAmount.value = "";
    syncRefundAmountVisibility(inputs, "");
    setCountryOnField(inputs, "+254", "KE");
  };

  const clearAll = () => {
    appliedDetails = null;
    if (floatSupplierName) floatSupplierName.value = "";
    if (floatSupplierPhone) floatSupplierPhone.value = "";
    if (floatSupplierId) floatSupplierId.value = "";
    if (floatPayment) floatPayment.value = "";
    if (floatReason) floatReason.value = "";
    if (floatRefund) floatRefund.value = "";
    if (floatRefundAmount) floatRefundAmount.value = "";
    syncRefundAmountVisibility(floatRoot, "");
    setCountryOnField(floatRoot?.querySelector("[data-stock-float-phone-wrap]"), "+254", "KE");
    setApplyStatus("");

    [...rows()].forEach((row) => {
      removeItemRow(row, { silent: true });
    });
    renderSummary();
  };

  panel.addEventListener("click", (event) => {
    const itemRemoveBtn = event.target.closest("[data-stock-item-remove]");
    if (itemRemoveBtn) {
      event.preventDefault();
      event.stopPropagation();
      const row =
        itemRemoveBtn.closest("[data-item-row][data-item-id]") ||
        findItemRowFromNode(itemRemoveBtn);
      if (row) {
        removeItemRow(row);
        renderSummary();
      }
      return;
    }

    const addBtn = event.target.closest("[data-stock-serial-add]");
    if (addBtn) {
      event.preventDefault();
      const row = findItemRowFromNode(addBtn);
      if (row) addSerialRow(row);
      return;
    }

    const removeBtn = event.target.closest("[data-stock-serial-remove]");
    if (removeBtn) {
      event.preventDefault();
      const row = findItemRowFromNode(removeBtn);
      const serialRow = removeBtn.closest(".stock-serial-row");
      if (!row || !serialRow) return;
      const serialRows = getSerialRows(row);
      if (serialRows.length <= 1) {
        const input = serialRow.querySelector("[data-stock-serial-input]");
        if (input) input.value = "";
      } else {
        serialRow.remove();
      }
      refreshRowState(row);
      getSerialRows(row)
        .at(-1)
        ?.querySelector("[data-stock-serial-input]")
        ?.focus();
      return;
    }

    const toggle = event.target.closest("[data-stock-item-toggle]");
    if (!toggle) return;
    const row = toggle.closest("[data-item-row][data-item-id]");
    const allRows = rows();
    if (!row || !allRows.includes(row)) return;

    if (simpleCatalog) {
      if (isParkedRow(row) || row.classList.contains("is-selected")) {
        setRowOpen(row, true);
        row.scrollIntoView({ behavior: "smooth", block: "nearest" });
        renderSummary();
        return;
      }
      const openOthers = allRows.filter(
        (other) =>
          other !== row &&
          (other.classList.contains("is-open") || other.classList.contains("is-selected")) &&
          !isParkedRow(other)
      );
      for (const other of openOthers) {
        closeAndParkRow(other);
      }
      setRowOpen(row, true);
      parkSelectedRow(row);
      if (panel.hasAttribute("data-stock-catalog-search-first")) {
        const searchInput = panel.querySelector("[data-item-search]");
        if (searchInput && searchInput.value) {
          searchInput.value = "";
        }
      }
      panel.dispatchEvent(new CustomEvent("stock-catalog:collapse-picker"));
      row.scrollIntoView({ behavior: "smooth", block: "nearest" });
      renderSummary();
      return;
    }

    const willOpen = !row.classList.contains("is-open");

    if (willOpen) {
      const openOthers = allRows.filter(
        (other) => other !== row && other.classList.contains("is-open")
      );
      for (const other of openOthers) {
        if (!closeAndParkRow(other)) return;
      }
      setRowOpen(row, true);
      // Popup search-first: clear live search so the selected item parks cleanly.
      if (panel.hasAttribute("data-stock-catalog-search-first")) {
        const searchInput = panel.querySelector("[data-item-search]");
        if (searchInput && searchInput.value) {
          searchInput.value = "";
          searchInput.dispatchEvent(new Event("search", { bubbles: true }));
        }
      }
      row.scrollIntoView({ behavior: "smooth", block: "nearest" });
      renderSummary();
      return;
    }

    if (!closeAndParkRow(row)) return;
    renderSummary();
  });

  panel.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!target.matches("[data-stock-serial-input]")) return;
    event.preventDefault();
    event.stopPropagation();
    const row = findItemRowFromNode(target);
    if (!row) return;

    if (mode === "out") {
      const root = target.closest("[data-serial-search-root]");
      const firstOption = root?.querySelector(
        ".stock-supplier-suggest-option:not([disabled])"
      );
      if (firstOption) {
        firstOption.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        return;
      }
    }

    refreshRowState(row);
    const currentValue = normalizeSerial(target.value);
    if (!currentValue) return;
    const hasEmpty = getSerialRows(row).some((serialRow) => {
      const input = serialRow.querySelector("[data-stock-serial-input]");
      if (input === target) return false;
      return !normalizeSerial(input?.value);
    });
    if (!hasEmpty) addSerialRow(row);
  });

  panel.addEventListener("myshop:serial-applied", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!target.matches("[data-stock-serial-input]")) return;
    if (mode !== "in") return;
    const row = findItemRowFromNode(target);
    if (!row || !tracksSerial(row)) return;
    const list =
      getInputsBag(row)?.querySelector("[data-stock-serial-list]") ||
      row.querySelector("[data-stock-serial-list]");
    queueSerialInStockCheck(target, {
      itemId: row.dataset.itemId || "",
      container: list,
      immediate: true,
    });
    refreshRowState(row);
    const hasEmpty = getSerialRows(row).some((serialRow) => {
      const value = normalizeSerial(
        serialRow.querySelector("[data-stock-serial-input]")?.value
      );
      return !value;
    });
    if (!hasEmpty) {
      window.setTimeout(() => addSerialRow(row), 40);
    }
  });

  panel.addEventListener("focusin", (event) => {
    if (mode !== "out") return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!target.matches("[data-serial-search]")) return;
    queueSerialSearch(target);
  });

  const onFieldChange = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (
      target.matches(
        "[data-stock-float-supplier-name], [data-stock-float-supplier-phone], [data-stock-float-supplier-dial], [data-stock-float-payment], [data-stock-float-reason], [data-stock-float-refund], [data-stock-float-refund-amount]"
      )
    ) {
      if (target.matches("[data-stock-float-refund]")) {
        syncRefundFromSelect(target);
      }
      if (target.matches("[data-stock-float-supplier-name], [data-stock-float-supplier-phone]")) {
        if (target.matches("[data-stock-float-supplier-phone]")) {
          delete target.dataset.supplierResolved;
          normalizePhoneInput(target);
        } else {
          const start = target.selectionStart;
          const end = target.selectionEnd;
          target.value = target.value.toUpperCase();
          if (typeof start === "number" && typeof end === "number") {
            target.setSelectionRange(start, end);
          }
        }
        if (target.matches("[data-supplier-search]")) queueSupplierSearch(target);
      }
      renderSummary();
      if (loginVerified) queueAutoStockInAndPrint();
      return;
    }

    const itemRow = findItemRowFromNode(target);
    if (!itemRow) return;
    if (target.matches(
      "[data-stock-serial-input], [data-stock-qty], [data-stock-buying-price], [data-stock-supplier-name], [data-stock-supplier-phone], [data-stock-supplier-dial], [data-stock-payment], [data-stock-reason], [data-stock-refund], [data-stock-refund-amount]"
    )) {
      if (target.matches("[data-stock-refund]")) {
        syncRefundFromSelect(target);
      }
      if (target.matches("[data-stock-supplier-phone]")) {
        delete target.dataset.supplierResolved;
        normalizePhoneInput(target);
      } else if (target.matches("[data-stock-supplier-name]")) {
        const start = target.selectionStart;
        const end = target.selectionEnd;
        target.value = target.value.toUpperCase();
        if (typeof start === "number" && typeof end === "number") {
          target.setSelectionRange(start, end);
        }
      }
      if (target.matches("[data-supplier-search]")) queueSupplierSearch(target);
      if (mode === "out" && target.matches("[data-serial-search]")) {
        const start = target.selectionStart;
        const end = target.selectionEnd;
        target.value = target.value.toUpperCase();
        if (typeof start === "number" && typeof end === "number") {
          target.setSelectionRange(start, end);
        }
        queueSerialSearch(target);
      }
      if (mode === "in" && target.matches("[data-stock-serial-input]")) {
        const start = target.selectionStart;
        const end = target.selectionEnd;
        target.value = target.value.toUpperCase();
        if (typeof start === "number" && typeof end === "number") {
          target.setSelectionRange(start, end);
        }
        const list =
          getInputsBag(itemRow)?.querySelector("[data-stock-serial-list]") ||
          itemRow.querySelector("[data-stock-serial-list]");
        queueSerialInStockCheck(target, {
          itemId: itemRow.dataset.itemId || "",
          container: list,
        });
      }
      refreshRowState(itemRow);
      if (loginVerified) queueAutoStockInAndPrint();
      return;
    }
    syncFilled(itemRow);
    renderSummary();
  };

  panel.addEventListener("input", onFieldChange);
  panel.addEventListener("change", onFieldChange);
  floatRoot?.addEventListener("input", onFieldChange);
  floatRoot?.addEventListener("change", onFieldChange);

  // Dedicated refund toggle so amount field always appears for Yes.
  const onRefundChange = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!target.matches("[data-stock-refund], [data-stock-float-refund]")) return;
    syncRefundFromSelect(target);
    renderSummary();
  };
  document.addEventListener("change", onRefundChange);
  document.addEventListener("input", onRefundChange);

  if (floatRefund) syncRefundFromSelect(floatRefund);
  document.querySelectorAll("[data-stock-refund]").forEach((select) => {
    syncRefundFromSelect(select);
  });

  applyBtn?.addEventListener("click", (event) => {
    event.preventDefault();
    applyDetailsToReady();
  });

  const supplierSearchUrl =
    form.dataset.supplierSearchUrl ||
    form.getAttribute("data-supplier-search-url") ||
    "";
  let supplierSearchTimer = null;
  let supplierSearchSeq = 0;
  let fillingSupplier = false;

  const hideSupplierSuggest = (root) => {
    const nodes = root
      ? root.querySelectorAll("[data-supplier-suggest]")
      : document.querySelectorAll("[data-supplier-suggest]");
    nodes.forEach((el) => {
      el.hidden = true;
      el.classList.remove("is-open-up");
      el.innerHTML = "";
    });
  };

  const positionSupplierSuggest = (input, suggest) => {
    if (!input || !suggest) return;
    const root = input.closest("[data-supplier-search-root]");
    const host =
      root?.closest(".buy-stock-simple-confirm-card") ||
      root?.closest("[data-stock-float]") ||
      null;
    suggest.classList.remove("is-open-up");
    if (!host) return;
    const inputRect = input.getBoundingClientRect();
    const hostRect = host.getBoundingClientRect();
    const spaceBelow = hostRect.bottom - inputRect.bottom;
    if (spaceBelow < 160) suggest.classList.add("is-open-up");
  };

  const resolveSupplierTargets = (fromInput) => {
    const floatField = fromInput.closest("[data-stock-float-apply]");
    if (floatField) {
      return {
        scope: floatRoot,
        nameInput: floatSupplierName,
        phoneInput: floatSupplierPhone,
        dialRoot: floatRoot?.querySelector("[data-stock-float-phone-wrap]"),
        dialInput: floatSupplierDial,
      };
    }
    const inputs = getInputsRow(findItemRowFromNode(fromInput));
    if (!inputs) return null;
    return {
      scope: inputs,
      nameInput: inputs.querySelector("[data-stock-supplier-name]"),
      phoneInput: inputs.querySelector("[data-stock-supplier-phone]"),
      dialRoot: inputs,
      dialInput: inputs.querySelector("[data-stock-supplier-dial]"),
    };
  };

  const applySupplierResult = (fromInput, supplier, { fillAll = false, sourceBy = "" } = {}) => {
    const targets = resolveSupplierTargets(fromInput);
    if (!targets || !supplier) return;
    fillingSupplier = true;
    const supplierId = supplier.id != null ? String(supplier.id) : "";

    // Always fill both name and phone (and dial) so the other input is autofilled.
    if (targets.nameInput) {
      targets.nameInput.value = String(supplier.name || "").toUpperCase();
    }
    setCountryOnField(targets.dialRoot, supplier.dial || "+254", supplier.iso || "KE");
    if (targets.phoneInput) {
      targets.phoneInput.value = normalizeNationalPhone(
        supplier.phone || "",
        supplier.dial || "+254"
      );
      targets.phoneInput.dataset.supplierResolved = "1";
    }

    if (targets.scope === floatRoot || fromInput.closest("[data-stock-float-apply]")) {
      if (floatSupplierId) floatSupplierId.value = supplierId;
      if (floatSupplierName && targets.nameInput !== floatSupplierName) {
        floatSupplierName.value = String(supplier.name || "").toUpperCase();
      }
      if (floatSupplierPhone && targets.phoneInput !== floatSupplierPhone) {
        floatSupplierPhone.value = normalizeNationalPhone(
          supplier.phone || "",
          supplier.dial || "+254"
        );
        floatSupplierPhone.dataset.supplierResolved = "1";
      }
    } else {
      const idInput = targets.scope?.querySelector("[data-stock-supplier-id]");
      if (idInput) idInput.value = supplierId;
      if (floatSupplierId) floatSupplierId.value = supplierId;
      if (floatSupplierName) {
        floatSupplierName.value = String(supplier.name || "").toUpperCase();
      }
      if (floatSupplierPhone) {
        floatSupplierPhone.value = normalizeNationalPhone(
          supplier.phone || "",
          supplier.dial || "+254"
        );
        floatSupplierPhone.dataset.supplierResolved = "1";
      }
      if (floatRoot) {
        setCountryOnField(
          floatRoot.querySelector("[data-stock-float-phone-wrap]") || floatRoot,
          supplier.dial || "+254",
          supplier.iso || "KE"
        );
      }
    }

    fillingSupplier = false;
    hideSupplierSuggest(targets.scope);
    hideSupplierSuggest(floatRoot);
    const itemRow = findItemRowFromNode(fromInput);
    if (itemRow) refreshRowState(itemRow);
    autoApplyDetailsToReady({ silent: false });
    renderSummary();
  };

  const renderSupplierSuggest = (input, results) => {
    const root = input.closest("[data-supplier-search-root]");
    const suggest = root?.querySelector("[data-supplier-suggest]");
    if (!suggest) return;
    suggest.innerHTML = "";
    if (!results.length) {
      const empty = document.createElement("button");
      empty.type = "button";
      empty.className = "stock-supplier-suggest-option is-empty";
      empty.disabled = true;
      empty.innerHTML =
        `<strong>No registered supplier</strong><small>Keep typing or enter a new supplier</small>`;
      suggest.appendChild(empty);
      positionSupplierSuggest(input, suggest);
      suggest.hidden = false;
      return;
    }
    const by = input.getAttribute("data-supplier-search") || "name";
    results.forEach((supplier) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "stock-supplier-suggest-option";
      btn.innerHTML = `<strong></strong><small></small>`;
      const phoneLabel = `${supplier.dial || ""}${supplier.phone || ""}`;
      if (by === "name") {
        btn.querySelector("strong").textContent = supplier.name || "";
        btn.querySelector("small").textContent = phoneLabel;
      } else {
        btn.querySelector("strong").textContent = phoneLabel;
        btn.querySelector("small").textContent = supplier.name || "";
      }
      btn.addEventListener("mousedown", (event) => {
        event.preventDefault();
        applySupplierResult(input, supplier, { fillAll: true });
      });
      suggest.appendChild(btn);
    });
    positionSupplierSuggest(input, suggest);
    suggest.hidden = false;
  };

  const runSupplierSearch = async (input) => {
    if (!supplierSearchUrl || fillingSupplier || mode !== "in") return;
    const by = input.getAttribute("data-supplier-search");
    if (!by) return;
    const query = (input.value || "").trim();
    const root = input.closest("[data-supplier-search-root]");
    const minLen = by === "phone" ? 3 : 2;
    if (query.length < minLen) {
      hideSupplierSuggest(root);
      return;
    }

    const dial =
      root?.querySelector("[data-stock-float-supplier-dial], [data-stock-supplier-dial]")?.value ||
      floatSupplierDial?.value ||
      "";
    const seq = ++supplierSearchSeq;
    const params = new URLSearchParams({ q: query, by });
    if (by === "phone" && dial) params.set("dial", dial);

    try {
      const response = await fetch(`${supplierSearchUrl}?${params.toString()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const data = await response.json();
      if (seq !== supplierSearchSeq) return;
      if (!data || data.ok === false) return;
      const results = Array.isArray(data.results) ? data.results : [];
      renderSupplierSuggest(input, results);

      // Phone → autofill name. Name → suggest only; user must pick a row.
      if (by !== "phone" || !results.length) return;
      const digits = (value) => String(value || "").replace(/\D+/g, "");
      const qDigits = digits(query);
      if (qDigits.length < 7) return;
      const match =
        results.find((row) => digits(row.phone) === qDigits) ||
        (results.length === 1 && digits(results[0].phone).includes(qDigits)
          ? results[0]
          : null);
      if (match) applySupplierResult(input, match, { fillAll: true });
    } catch (_error) {
      /* ignore network errors during typing */
    }
  };

  const queueSupplierSearch = (input) => {
    if (!(input instanceof Element)) return;
    window.clearTimeout(supplierSearchTimer);
    supplierSearchTimer = window.setTimeout(() => runSupplierSearch(input), 180);
  };

  floatRoot?.addEventListener("focusin", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!target.matches("[data-supplier-search]")) return;
    if ((target.value || "").trim()) queueSupplierSearch(target);
  });

  // Form-level backup so float fields always search even if a parent listener misses.
  form.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!target.matches("[data-supplier-search]")) return;
    if (target.matches("[data-stock-float-supplier-phone]")) {
      delete target.dataset.supplierResolved;
      normalizePhoneInput(target);
    } else if (target.matches("[data-stock-float-supplier-name], [data-stock-supplier-name]")) {
      const start = target.selectionStart;
      const end = target.selectionEnd;
      target.value = String(target.value || "").toUpperCase();
      if (typeof start === "number" && typeof end === "number") {
        target.setSelectionRange(start, end);
      }
    }
    queueSupplierSearch(target);
  });

  const serialSearchUrl = form.dataset.serialSearchUrl || "";
  let serialSearchTimer = null;
  let serialSearchSeq = 0;

  const hideSerialSuggest = (root) => {
    const nodes = root
      ? root.querySelectorAll("[data-serial-suggest]")
      : document.querySelectorAll("[data-serial-suggest]");
    nodes.forEach((el) => {
      el.hidden = true;
      el.innerHTML = "";
    });
  };

  const otherSelectedSerials = (row, exceptInput) => {
    const selected = [];
    getSerialRows(row).forEach((serialRow) => {
      const input = serialRow.querySelector("[data-stock-serial-input]");
      if (!input || input === exceptInput) return;
      const value = normalizeSerial(input.value);
      if (value) selected.push(value);
    });
    return selected;
  };

  const renderSerialSuggest = (input, results) => {
    const root = input.closest("[data-serial-search-root]");
    const suggest = root?.querySelector("[data-serial-suggest]");
    if (!suggest) return;
    suggest.innerHTML = "";
    if (!results.length) {
      const empty = document.createElement("button");
      empty.type = "button";
      empty.className = "stock-supplier-suggest-option";
      empty.disabled = true;
      empty.innerHTML = `<strong>No matching serials</strong><small>Available at this shop only</small>`;
      suggest.appendChild(empty);
      suggest.hidden = false;
      return;
    }
    results.forEach((serial) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "stock-supplier-suggest-option";
      btn.innerHTML = `<strong></strong>`;
      btn.querySelector("strong").textContent = serial;
      btn.addEventListener("mousedown", (event) => {
        event.preventDefault();
        input.value = serial;
        hideSerialSuggest(root);
        const row = findItemRowFromNode(input);
        if (!row) return;
        refreshRowState(row);
        const next = addSerialRow(row);
        next?.focus();
      });
      suggest.appendChild(btn);
    });
    suggest.hidden = false;
  };

  const runSerialSearch = async (input) => {
    if (mode !== "out" || !serialSearchUrl) return;
    const row = findItemRowFromNode(input);
    if (!row || !tracksSerial(row)) return;
    const itemId = row.dataset.itemId || "";
    const shopId = form.querySelector("[data-stock-shop]")?.value || "";
    if (!itemId || !shopId) return;

    const root = input.closest("[data-serial-search-root]");
    const query = normalizeSerial(input.value);
    const seq = ++serialSearchSeq;
    const params = new URLSearchParams({
      item_id: itemId,
      shop_id: shopId,
      q: query,
    });
    otherSelectedSerials(row, input).forEach((serial) => params.append("exclude", serial));

    try {
      const response = await fetch(`${serialSearchUrl}?${params.toString()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const data = await response.json();
      if (seq !== serialSearchSeq) return;
      const results = Array.isArray(data.results) ? data.results : [];
      renderSerialSuggest(input, results);
      if (results.length === 1 && query && results[0] === query) {
        hideSerialSuggest(root);
      }
    } catch (_error) {
      /* ignore network errors during typing */
    }
  };

  const queueSerialSearch = (input) => {
    window.clearTimeout(serialSearchTimer);
    serialSearchTimer = window.setTimeout(() => runSerialSearch(input), 220);
  };

  const syncAllRows = () => {
    rows().forEach((row) => {
      if (simpleCatalog && (isParkedRow(row) || row.classList.contains("is-selected"))) {
        setRowOpen(row, true);
        syncFilled(row);
        return;
      }
      setRowOpen(row, false);
      syncFilled(row);
    });
  };
  syncAllRows();
  document.addEventListener("stock-catalog:rendered", () => {
    // Keep open/filled state for parked rows; only close brand-new unloaded rows.
    rows().forEach((row) => {
      if (
        row.classList.contains("is-open") ||
        row.classList.contains("is-filled") ||
        row.classList.contains("is-selected") ||
        (simpleCatalog && isParkedRow(row))
      ) {
        if (simpleCatalog && (isParkedRow(row) || row.classList.contains("is-selected"))) {
          setRowOpen(row, true);
        }
        return;
      }
      setRowOpen(row, false);
      syncFilled(row);
    });
    renderSummary();
  });

  document.addEventListener("stock-catalog:busy", (event) => {
    catalogBusy = Boolean(event.detail?.busy);
    renderSummary();
  });

  clearBtn?.addEventListener("click", clearAll);

  floatRoot?.addEventListener("click", (event) => {
    const removeBtn = event.target.closest("[data-stock-float-remove]");
    if (!removeBtn) return;
    event.preventDefault();
    const itemId = removeBtn.getAttribute("data-item-id");
    if (!itemId) return;
    const row = rows().find((r) => r.getAttribute("data-item-id") === itemId);
    if (!row) return;
    removeItemRow(row);
    renderSummary();
  });

  loginCodeInput?.addEventListener("input", () => {
    loginVerified = false;
    if (submitBtn) submitBtn.disabled = true;
    window.clearTimeout(loginVerifyTimer);
    loginVerifyTimer = window.setTimeout(async () => {
      const ok = await verifyLoginCode();
      if (ok) queueAutoStockInAndPrint();
    }, 220);
  });
  loginCodeInput?.addEventListener("blur", async () => {
    const ok = await verifyLoginCode();
    if (ok) queueAutoStockInAndPrint();
  });

  form.addEventListener("submit", async (event) => {
    if (isCatalogBusy()) {
      event.preventDefault();
      setApplyStatus("Still loading items — wait a moment, then submit.", true);
      return;
    }

    const shopSelect = form.querySelector("[data-stock-shop]");
    const fromSelect = form.querySelector("[data-stock-from-shop]");
    const printSupplier = form.hasAttribute("data-supplier-print") && mode === "in";

    if (shopSelect && !shopSelect.value) {
      event.preventDefault();
      shopSelect.focus();
      return;
    }

    if (mode === "request") {
      if (fromSelect && !fromSelect.value) {
        event.preventDefault();
        fromSelect.focus();
        return;
      }
      if (shopSelect && fromSelect && shopSelect.value === fromSelect.value) {
        event.preventDefault();
        fromSelect.focus();
        return;
      }
    }

    rows().forEach((row) => {
      if (tracksSerial(row)) syncSerialQuantity(row);
    });

    const ready = collectReady();
    if (focusFirstIncomplete(ready)) {
      event.preventDefault();
      return;
    }
    if (requiresLoginCode && !loginVerified) {
      event.preventDefault();
      const ok = await verifyLoginCode();
      if (!ok) {
        setApplyStatus("Enter a valid staff ID to stock in.", true);
        revealAndFocus(loginCodeInput);
        return;
      }
    }

    if (printSupplier) {
      event.preventDefault();
      await submitStockInWithPrint();
      return;
    }

    event.preventDefault();
    rows().forEach((row) => {
      const active = getQty(row) > 0;
      if (active) {
        setRowOpen(row, true);
        syncSerialQuantity(row);
      }
      setFieldsEnabled(row, active);
    });

    // Mirror print path: only post ready lines, with serials synced.
    rows().forEach((row) => setFieldsEnabled(row, false));
    const readyActive = collectReady();
    readyActive.forEach((item) => {
      setRowOpen(item.row, true);
      if (tracksSerial(item.row)) syncSerialQuantity(item.row);
      setFieldsEnabled(item.row, true);
    });

    if (autoStockInFlight) return;
    autoStockInFlight = true;
    if (submitBtn) submitBtn.disabled = true;
    setApplyStatus(
      mode === "out" ? "Submitting stock out…" : "Submitting stock in…"
    );
    try {
      const body = new FormData(form);
      body.set("ajax", "1");
      const response = await fetch(
        form.getAttribute("action") || window.location.href,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          credentials: "same-origin",
          body,
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        const errors = Array.isArray(data.errors) ? data.errors.filter(Boolean) : [];
        setApplyStatus(
          data.error ||
            errors[0] ||
            "Could not submit. Your entries were kept — try again.",
          true
        );
        autoStockInFlight = false;
        renderSummary();
        return;
      }
      setApplyStatus(data.message || "Submitted successfully.");
      window.location.assign(data.next || window.location.href);
    } catch (_error) {
      setApplyStatus("Network error. Your entries were kept — try again.", true);
      autoStockInFlight = false;
      renderSummary();
    }
  });

  if (window.initUppercaseInputs) window.initUppercaseInputs(floatRoot || document);

  // Shared flag country picker (compact, one menu for all rows).
  const countryMenu =
    form.querySelector("[data-stock-country-menu]") ||
    form.parentElement?.querySelector?.("[data-stock-country-menu]") ||
    document.querySelector("[data-stock-country-menu]");
  const countrySearch = countryMenu?.querySelector("[data-stock-country-search]");
  const countryOptions = [...(countryMenu?.querySelectorAll(".stock-country-option") || [])];
  let activePhoneField = null;

  const closeCountryMenu = () => {
    if (!countryMenu) return;
    countryMenu.hidden = true;
    document
      .querySelectorAll("[data-stock-country-trigger][aria-expanded='true']")
      .forEach((btn) => btn.setAttribute("aria-expanded", "false"));
    activePhoneField = null;
    if (countrySearch) countrySearch.value = "";
    countryOptions.forEach((option) => {
      if (option.parentElement) option.parentElement.hidden = false;
    });
  };

  const openCountryMenu = (trigger) => {
    if (!countryMenu || !trigger || trigger.disabled) return;
    const phoneField = trigger.closest("[data-stock-phone-field]");
    if (!phoneField) return;

    activePhoneField = phoneField;
    const rect = trigger.getBoundingClientRect();
    const menuWidth = Math.min(264, window.innerWidth - 24);
    let left = rect.left;
    if (left + menuWidth > window.innerWidth - 12) {
      left = Math.max(12, window.innerWidth - menuWidth - 12);
    }
    let top = rect.bottom + 6;
    countryMenu.hidden = false;
    const menuHeight = countryMenu.offsetHeight || 240;
    if (top + menuHeight > window.innerHeight - 12) {
      top = Math.max(12, rect.top - menuHeight - 6);
    }
    countryMenu.style.left = `${left}px`;
    countryMenu.style.top = `${top}px`;

    document
      .querySelectorAll("[data-stock-country-trigger]")
      .forEach((btn) => btn.setAttribute("aria-expanded", "false"));
    trigger.setAttribute("aria-expanded", "true");

    const currentDial =
      phoneField.querySelector("[data-stock-supplier-dial], [data-stock-float-supplier-dial]")
        ?.value || "";
    countryOptions.forEach((option) => {
      option.classList.toggle("is-selected", option.dataset.dial === currentDial);
    });
    refreshIcons();
    countrySearch?.focus();
  };

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-stock-country-trigger]");
    if (trigger) {
      // Only handle triggers that belong to this stock form / float.
      if (!form.contains(trigger) && !(floatRoot && floatRoot.contains(trigger))) {
        return;
      }
      event.preventDefault();
      if (trigger.getAttribute("aria-expanded") === "true") {
        closeCountryMenu();
      } else {
        openCountryMenu(trigger);
      }
      return;
    }

    const option = event.target.closest(".stock-country-option");
    if (option && countryMenu && !countryMenu.hidden && countryMenu.contains(option)) {
      event.preventDefault();
      const { dial, iso } = option.dataset;
      if (activePhoneField && dial && iso) {
        setCountryOnField(activePhoneField, dial, iso);
        const itemRow = findItemRowFromNode(activePhoneField);
        if (itemRow) refreshRowState(itemRow);
      }
      closeCountryMenu();
      return;
    }

    if (
      countryMenu &&
      !countryMenu.hidden &&
      !countryMenu.contains(event.target)
    ) {
      closeCountryMenu();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeCountryMenu();
      hideSupplierSuggest();
      hideSerialSuggest();
    }
  });

  document.addEventListener("mousedown", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest("[data-supplier-search-root]")) return;
    if (target.closest("[data-serial-search-root]")) return;
    hideSupplierSuggest();
    hideSerialSuggest();
  });

  countrySearch?.addEventListener("input", () => {
    const q = countrySearch.value.trim().toLowerCase();
    countryOptions.forEach((option) => {
      const hay = `${option.dataset.name} ${option.dataset.dial} ${option.dataset.iso}`.toLowerCase();
      if (option.parentElement) {
        option.parentElement.hidden = Boolean(q) && !hay.includes(q);
      }
    });
  });

  window.addEventListener("scroll", () => {
    if (countryMenu && !countryMenu.hidden) closeCountryMenu();
  }, true);

  renderSummary();
})();
