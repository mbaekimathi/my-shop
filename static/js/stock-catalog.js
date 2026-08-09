(() => {
  const panel = document.querySelector("[data-stock-mode][data-stock-catalog-api]");
  if (!panel) return;

  const apiUrl = panel.getAttribute("data-stock-catalog-api") || "";
  const listRoot = panel.querySelector("[data-stock-catalog-root]");
  const parked = panel.querySelector("[data-stock-parked]");
  const moreWrap = panel.querySelector("[data-stock-catalog-more-wrap]");
  const moreBtn = panel.querySelector("[data-stock-catalog-more]");
  const searchInput = panel.querySelector("[data-item-search]");
  const noResults = panel.querySelector("[data-item-no-results]");
  const visibleCountEl = panel.querySelector("[data-item-visible-count]");
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

  const money = (value) => {
    if (value == null || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? String(Math.round(n)) : null;
  };

  const simpleMode = panel.hasAttribute("data-stock-catalog-simple");
  const searchFirst = panel.hasAttribute("data-stock-catalog-search-first");
  const parkedWrap = panel.querySelector(".buy-stock-simple-parked");

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
    if (editableMatrix) {
      return [...headerRow.querySelectorAll("[data-stock-shop-cell]")].some(
        (cell) => Number(cell.querySelector("[data-stock-qty]")?.value || 0) > 0
      );
    }
    if (headerRow.classList.contains("is-open")) return true;
    const inputs = headerRow.nextElementSibling;
    if (!inputs?.matches?.("[data-stock-item-inputs]")) return false;
    const qty = Number(inputs.querySelector("[data-stock-qty]")?.value || 0);
    return qty > 0;
  };

  const parkFilled = () => {
    if (!parked) return;
    listRoot.querySelectorAll("[data-item-row][data-item-id]").forEach((row) => {
      if (!isFilledPair(row)) return;
      if (parked.contains(row)) return;
      if (editableMatrix) {
        parked.appendChild(row);
        return;
      }
      const inputs = row.nextElementSibling;
      parked.appendChild(row);
      if (inputs?.matches?.("[data-stock-item-inputs]")) parked.appendChild(inputs);
    });
    if (parkedWrap) {
      parkedWrap.hidden = !parked.querySelector("[data-item-row]");
    }
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
    section.className = "stock-category";
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
                  <span class="stock-th-pair-cols" aria-hidden="true"><span>Stock</span><span>Qty</span></span>
                </span>
              </th>`;
            })
            .join("")
        : viewShops
            .map(
              (shop) =>
                `<th scope="col" class="stock-matrix-shop-col">${escapeHtml(
                  shop.name || "Shop"
                )}</th>`
            )
            .join("");
      const totalHeader = editableMatrix
        ? ""
        : showAllShops
          ? `<th scope="col" class="stock-matrix-total-col">Total</th>`
          : "";
      const matrixClass = [
        "stock-matrix",
        showAllShops || editableMatrix ? "stock-matrix--all" : "stock-matrix--single",
        editableMatrix ? "stock-matrix--editable stock-matrix--list" : "",
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
      <div class="stock-matrix-scroll${editableMatrix ? " stock-matrix-scroll--list" : ""}">
        <table class="${matrixClass}">
          <thead>
            <tr>
              <th scope="col" class="stock-matrix-item-col">Item</th>
              ${shopHeaders}
              ${totalHeader}
            </tr>
          </thead>
          <tbody data-stock-catalog-tbody></tbody>
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

    const colCount = mode === "request" ? 4 : 3;
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
    const serialInput =
      mode === "out"
        ? `<div class="stock-serial-row">
            <div class="stock-serial-input-wrap" data-serial-search-root>
              <input
                type="text"
                placeholder="Search serial to stock out"
                autocomplete="off"
                spellcheck="false"
                data-stock-serial-input
                data-serial-search
                data-stock-field
                disabled
              >
              <div class="stock-supplier-suggest" data-serial-suggest hidden></div>
            </div>
            <button type="button" class="stock-serial-remove" data-stock-serial-remove aria-label="Remove serial" hidden>
              <i data-lucide="x" aria-hidden="true"></i>
            </button>
          </div>`
        : `<div class="stock-serial-row">
            <input
              type="text"
              placeholder="Enter serial number"
              autocomplete="off"
              spellcheck="false"
              data-stock-serial-input
              data-stock-field
              disabled
            >
            <button type="button" class="stock-serial-remove" data-stock-serial-remove aria-label="Remove serial" hidden>
              <i data-lucide="x" aria-hidden="true"></i>
            </button>
          </div>`;
    return `
      <div class="stock-inline-field stock-inline-field--serial">
        <div class="stock-serial-head">
          <span>Serial number</span>
          <span class="visually-hidden">Remove</span>
        </div>
        <div class="stock-serial-list" data-stock-serial-list>
          ${serialInput}
        </div>
        <div class="stock-serial-actions">
          <button type="button" class="stock-serial-add" data-stock-serial-add>
            <i data-lucide="plus" aria-hidden="true"></i>
            Add serial
          </button>
          <small class="stock-serial-hint">${
            mode === "out"
              ? "Type to search available serials at this shop"
              : "Quantity updates as you add serials"
          }</small>
        </div>
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
            <span>Buying price</span>
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
      return `
      <div class="stock-in-field-row buy-stock-pick-fields">
        ${qtyBlock}
        ${priceBlock}
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
            <option value="return">Return</option>
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

  const buildShopCell = (item, shop, stockQty) => {
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
            aria-label="Buying price at ${shopLabel}"
            title="${prev ? `Previous ${prev}` : "Buying price"}"
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
    return `<td class="stock-matrix-shop-col stock-matrix-shop-col--edit">
      <div
        class="stock-shop-cell ${pairClass}"
        data-stock-shop-cell
        data-item-id="${item.id}"
        data-item-name="${escapeHtml(item.name || "")}"
        data-shop-id="${shop.id}"
        data-shop-name="${shopLabel}"
        data-item-stock="${stockQty}"
        data-track-serial="${track}"
      >
        <span class="stock-list-stock${stockQty === 0 ? " is-empty" : ""}" data-stock-display-qty>${stockQty}</span>
        <input type="hidden" name="item_id" value="${item.id}" disabled data-stock-field>
        <input type="hidden" name="line_shop_id" value="${shop.id}" disabled data-stock-field>
        ${qtyControl}
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
    const colCount = mode === "request" ? 4 : 3;

    if (editableMatrix) {
      const quantities = Array.isArray(item.shop_quantities)
        ? item.shop_quantities.map((q) => Math.max(0, Math.floor(Number(q) || 0)))
        : viewShops.map(() => stock);
      const cells = viewShops
        .map((shop, index) =>
          buildShopCell(item, shop, quantities[index] || 0)
        )
        .join("");
      const header = document.createElement("tr");
      header.className = `stock-matrix-row stock-matrix-row--editable${
        item.is_suspended ? " is-suspended" : ""
      }`;
      header.setAttribute("data-item-row", "");
      header.setAttribute("data-item-id", String(item.id));
      header.setAttribute("data-item-name", name);
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
                }">${qty}</span></td>`
            )
            .join("") +
          `<td class="stock-matrix-total-col"><span class="stock-matrix-qty stock-matrix-qty--total${
            rowTotal === 0 ? " is-empty" : ""
          }">${rowTotal}</span></td>`
        : `<td class="stock-matrix-shop-col"><span class="stock-matrix-qty${
            (quantities[0] || 0) === 0 ? " is-empty" : ""
          }">${quantities[0] || 0}</span></td>`;
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
      header.setAttribute("data-track-serial", track);
      header.setAttribute(
        "data-search-text",
        `${name} ${category} ${desc}`.toLowerCase()
      );
      header.innerHTML = `
        <button type="button" class="buy-stock-pick-toggle" data-stock-item-toggle>
          <div class="buy-stock-pick-copy">
            <strong>${escapeHtml(name)}</strong>
            <span class="buy-stock-pick-meta">
              ${escapeHtml(category || "Item")}
              · in shop ${stock}
              ${item.track_serial ? " · Serial" : ""}
              ${item.is_suspended ? " · Suspended" : ""}
            </span>
          </div>
          <i data-lucide="chevron-down" aria-hidden="true"></i>
        </button>
        <button
          type="button"
          class="buy-stock-pick-remove"
          data-stock-item-remove
          aria-label="Remove ${escapeHtml(name)}"
          title="Remove item"
          hidden
        >
          <i data-lucide="x" aria-hidden="true"></i>
        </button>`;

      const formRow = document.createElement("div");
      formRow.className = "buy-stock-pick-inputs";
      formRow.setAttribute("data-stock-item-inputs", "");
      formRow.hidden = true;
      formRow.innerHTML = `
        <div class="stock-item-inputs stock-item-inputs--matrix">
          <input type="hidden" name="item_id" value="${item.id}" disabled data-stock-field>
          ${mode === "in" ? buildInFields(item) : buildOutFields(item)}
          <button type="button" class="buy-stock-pick-remove-btn" data-stock-item-remove>
            <i data-lucide="trash-2" aria-hidden="true"></i>
            Remove item
          </button>
        </div>`;
      return [header, formRow];
    }

    const header = document.createElement("tr");
    header.className = `stock-matrix-row stock-matrix-row--action${
      item.is_suspended ? " is-suspended" : ""
    }${stock === 0 ? " is-empty-stock" : ""}`;
    header.setAttribute("data-item-row", "");
    header.setAttribute("data-item-id", String(item.id));
    header.setAttribute("data-item-name", name);
    header.setAttribute("data-item-stock", String(stock));
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
  };

  const appendItems = (items, { replace }) => {
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
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const showLoadError = ({ append, q }) => {
    if (append) return;
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

  const cacheKeyFor = (page, q) =>
    `stock-catalog:${catalogShopIds.join("-") || shopId || "all"}:${fromShopId || "0"}:${mode}:p${page}:s${pageSize}:q${String(
      q || ""
    ).toLowerCase()}`;

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
      const idle = searchFirst && !activeQuery;
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
    const cacheKey = cacheKeyFor(page, q);
    const online = typeof navigator === "undefined" || navigator.onLine;

    try {
      let data = null;
      let fromCache = false;

      if (online) {
        const response = await fetch(`${apiUrl}?${params.toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
          signal,
        });
        data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) throw new Error(data.error || "failed");
        try {
          const store = await import("./offline/store.js");
          await store.cacheSet(cacheKey, data, 60 * 60 * 12);
        } catch (_cacheErr) {
          /* optional */
        }
      } else {
        const store = await import("./offline/store.js");
        data = await store.cacheGet(cacheKey);
        fromCache = Boolean(data?.ok);
        if (!fromCache) throw new Error("offline_catalog_miss");
      }

      if (seq !== inFlight) return;
      applyCatalogData(data, { q, append });
      if (fromCache) panel.setAttribute("data-catalog-from-cache", "1");
      else panel.removeAttribute("data-catalog-from-cache");

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
        const warmParams = new URLSearchParams(params);
        warmParams.set("page", String(warmPage));
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
    } catch (err) {
      if (err?.name === "AbortError") return;
      if (seq !== inFlight) return;
      try {
        const store = await import("./offline/store.js");
        const cached = await store.cacheGet(cacheKey);
        if (seq === inFlight && cached?.ok) {
          applyCatalogData(cached, { q, append });
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
    groupEls.clear();
    listRoot.innerHTML = `
      <div class="buy-stock-simple-empty" data-stock-catalog-idle>
        <i data-lucide="package-search" aria-hidden="true"></i>
        <p>Search for an item to begin</p>
      </div>`;
    if (moreWrap) moreWrap.hidden = true;
    if (noResults) noResults.hidden = true;
    updateCount(0, "");
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const reload = (q = "") => {
    const query = String(q || "").trim();
    if (searchFirst && !query) {
      abortController?.abort();
      parkFilled();
      showIdleHint();
      setBusy(false);
      return Promise.resolve();
    }
    return fetchPage({ page: 1, q: query, append: false });
  };

  moreBtn?.addEventListener("click", () => {
    if (!hasMore || !nextPage || panel.hasAttribute("data-stock-catalog-busy")) return;
    fetchPage({ page: nextPage, q: activeQuery, append: true });
  });

  searchInput?.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(
      () => reload(String(searchInput.value || "").trim()),
      220
    );
  });
  searchInput?.addEventListener("search", () => {
    window.clearTimeout(searchTimer);
    reload(String(searchInput.value || "").trim());
  });

  const startCatalog = () => {
    if (panel.dataset.stockCatalogStarted === "1") return;
    panel.dataset.stockCatalogStarted = "1";
    if (searchFirst) {
      showIdleHint();
      return;
    }
    reload("");
  };

  if (panel.hasAttribute("data-stock-catalog-defer")) {
    panel.addEventListener("stock-catalog:load", startCatalog, { once: true });
    document.addEventListener(
      "buy-stock-modal:open",
      () => {
        panel.dispatchEvent(new CustomEvent("stock-catalog:load"));
      },
      { once: true }
    );
  } else {
    startCatalog();
  }
})();
