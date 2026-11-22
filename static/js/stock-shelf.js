/**
 * Shelf stock popup — register a shelf item from the stock-management sidebar.
 * Opens the [data-modal="shelf-stock"] dialog; when the modal is absent (other
 * pages) the sidebar button navigates to the view page with ?shelf=1.
 */
(function () {
  "use strict";

  const modal = document.querySelector("[data-modal='shelf-stock']");

  // Hang the fixed overlay off <body>: stock pages wrap content in
  // data-animate/transformed sections, which trap position:fixed and let
  // the popup scroll with the page.
  if (modal) document.body.appendChild(modal);

  // No modal on this page: sidebar action carries a data-href to navigate.
  if (!modal) {
    document.querySelectorAll('[data-modal-open="shelf-stock"]').forEach((trigger) => {
      trigger.addEventListener("click", (event) => {
        const href = trigger.getAttribute("data-href");
        if (!href) return;
        event.preventDefault();
        window.location.assign(href);
      });
    });
    return;
  }

  const form = modal.querySelector("[data-shelf-register-form]");
  const shopBoxes = Array.from(
    modal.querySelectorAll('input[name="shelf_shop_id"]')
  );
  const pageShopSelect = document.querySelector("[data-stock-shop-nav]");

  /* --- Category: live suggestions from registered shelf categories ------- */
  const catInput = modal.querySelector("[data-shelf-category-input]");
  const catList = modal.querySelector("[data-shelf-category-list]");
  const catCategories = Array.from(
    modal.querySelectorAll("[data-shelf-category-source] option")
  ).map((opt) => opt.value);
  let catActive = -1;

  const catHide = () => {
    if (!catList) return;
    catList.hidden = true;
    catActive = -1;
    catInput?.setAttribute("aria-expanded", "false");
  };

  const catShow = () => {
    if (!catInput || !catList) return;
    const needle = catInput.value.trim().toLowerCase();
    const matches = catCategories
      .filter((cat) => !needle || cat.toLowerCase().includes(needle))
      .slice(0, 8);
    catList.replaceChildren();
    if (!matches.length) {
      catHide();
      return;
    }
    matches.forEach((cat) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "shelf-cat-option";
      btn.setAttribute("role", "option");
      btn.textContent = cat;
      btn.addEventListener("mousedown", (event) => {
        event.preventDefault();
        catInput.value = cat;
        catHide();
        catInput.focus();
      });
      li.appendChild(btn);
      catList.appendChild(li);
    });
    catList.hidden = false;
    catActive = -1;
    catInput.setAttribute("aria-expanded", "true");
  };

  const catMove = (step) => {
    if (!catList || catList.hidden) return;
    const options = Array.from(catList.querySelectorAll(".shelf-cat-option"));
    if (!options.length) return;
    catActive = (catActive + step + options.length) % options.length;
    options.forEach((btn, index) => {
      btn.classList.toggle("is-active", index === catActive);
      if (index === catActive) btn.scrollIntoView({ block: "nearest" });
    });
  };

  if (catInput && catList) {
    catInput.addEventListener("focus", catShow);
    catInput.addEventListener("input", catShow);
    catInput.addEventListener("blur", () => window.setTimeout(catHide, 120));
    catInput.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (catList.hidden) catShow();
        catMove(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        catMove(-1);
      } else if (event.key === "Enter" && !catList.hidden && catActive >= 0) {
        event.preventDefault();
        const active = catList.querySelectorAll(".shelf-cat-option")[catActive];
        if (active) {
          catInput.value = active.textContent;
          catHide();
        }
      } else if (event.key === "Escape" && !catList.hidden) {
        event.stopPropagation();
        catHide();
      }
    });
    modal.querySelectorAll("[data-shelf-category-chip]").forEach((chip) => {
      chip.addEventListener("click", () => {
        catInput.value = chip.getAttribute("data-shelf-category-chip") || "";
        catHide();
        catInput.focus();
      });
    });
  }

  /* --- Image: drop zone with preview ------------------------------------- */
  const fileDrop = modal.querySelector("[data-shelf-file-drop]");
  const fileInput = modal.querySelector("[data-shelf-file-input]");
  const fileEmpty = modal.querySelector("[data-shelf-file-empty]");
  const filePicked = modal.querySelector("[data-shelf-file-picked]");
  const fileThumb = modal.querySelector("[data-shelf-file-thumb]");
  const fileName = modal.querySelector("[data-shelf-file-name]");
  const fileClear = modal.querySelector("[data-shelf-file-clear]");

  const fileRender = () => {
    if (!fileInput || !fileEmpty || !filePicked) return;
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      fileEmpty.hidden = false;
      filePicked.hidden = true;
      return;
    }
    if (fileName) fileName.textContent = file.name;
    if (fileThumb) {
      if (file.type.startsWith("image/")) {
        fileThumb.src = URL.createObjectURL(file);
      } else {
        fileThumb.removeAttribute("src");
      }
    }
    fileEmpty.hidden = true;
    filePicked.hidden = false;
  };

  if (fileDrop && fileInput) {
    fileDrop.addEventListener("click", (event) => {
      if (event.target.closest("[data-shelf-file-clear]")) return;
      fileInput.click();
    });
    fileInput.addEventListener("change", fileRender);
    ["dragenter", "dragover"].forEach((type) => {
      fileDrop.addEventListener(type, (event) => {
        event.preventDefault();
        fileDrop.classList.add("is-drag");
      });
    });
    ["dragleave", "drop"].forEach((type) => {
      fileDrop.addEventListener(type, (event) => {
        event.preventDefault();
        fileDrop.classList.remove("is-drag");
      });
    });
    fileDrop.addEventListener("drop", (event) => {
      const files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length) {
        fileInput.files = files;
        fileRender();
      }
    });
    fileClear?.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      fileInput.value = "";
      fileRender();
      fileInput.focus();
    });
  }

  const openModal = () => {
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("workspace-modal-open");
    const pageShop = pageShopSelect && pageShopSelect.value;
    if (pageShop && shopBoxes.some((box) => box.value === pageShop)) {
      shopBoxes.forEach((box) => {
        box.checked = box.value === pageShop;
      });
    } else if (!shopBoxes.some((box) => box.checked)) {
      shopBoxes.forEach((box) => {
        box.checked = true;
      });
    }
    try {
      window.lucide?.createIcons?.();
    } catch (_) {
      /* ignore */
    }
    window.setTimeout(() => {
      (catInput || modal.querySelector('input[name="name"]'))?.focus();
    }, 40);
  };

  const closeModal = () => {
    catHide();
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
  };

  document.querySelectorAll('[data-modal-open="shelf-stock"]').forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      openModal();
    });
  });

  modal.querySelectorAll("[data-modal-close]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.preventDefault();
      closeModal();
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });

  // Opened via the sidebar action: land on the view page with ?shelf=1.
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.get("shelf") === "1") {
      openModal();
      params.delete("shelf");
      const query = params.toString();
      const next = `${window.location.pathname}${query ? `?${query}` : ""}${
        window.location.hash || ""
      }`;
      window.history.replaceState({}, "", next);
    }
  } catch (_err) {
    /* ignore */
  }

  // Shelf rows live inside the page's outer stock form (nested forms are not
  // allowed), so shelf_action buttons submit that form directly.
  const layout = document.querySelector("[data-stock-layout]");
  if (layout) {
    layout.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) return;
      const shelfPanel = target.closest("[data-shelf-stock]");
      if (!shelfPanel) {
        // Enter in search/filters must not submit a shelf row.
        event.preventDefault();
        return;
      }
      event.preventDefault();
      const cell = target.closest("[data-shelf-cell]");
      if (!cell) return;
      (
        document.querySelector("[data-shelf-float-submit]") ||
        document.querySelector("[data-shelf-bulk-submit]")
      )?.click();
    });

    layout.addEventListener("submit", (event) => {
      const submitter = event.submitter;
      if (!submitter || submitter.name !== "shelf_action") return;
      const question = submitter.getAttribute("data-shelf-confirm");
      if (question && !window.confirm(question)) {
        event.preventDefault();
      }
    });
  }

  const shelfPanel = document.querySelector("[data-shelf-stock]");

  // Shelf float panel (mirrors the item stock-in/out submit card): lists the
  // filled cells and submits them all via in_bulk/out_bulk.
  const floatRoot = document.querySelector("[data-stock-float]");
  const floatLines = floatRoot?.querySelector("[data-shelf-float-lines]");
  const floatEmpty = floatRoot?.querySelector("[data-shelf-float-empty]");
  const floatClear = floatRoot?.querySelector("[data-shelf-float-clear]");
  const floatToggle = floatRoot?.querySelector("[data-shelf-float-toggle]");
  const floatSubmit = floatRoot?.querySelector("[data-shelf-float-submit]");
  const floatMode = floatRoot?.dataset.stockFloatMode || "";
  const floatTotal = floatRoot?.querySelector("[data-shelf-float-total]");
  const floatTotalCaption = floatRoot?.querySelector(
    "[data-shelf-float-total-caption]"
  );
  const floatTotalValue = floatRoot?.querySelector(
    "[data-shelf-float-total-value]"
  );

  // Shelf variant owns its collapse toggle (the item flow binds the shared
  // data-stock-float-toggle itself when that variant is rendered).
  if (floatRoot && floatToggle) {
    const collapseMq = window.matchMedia("(max-width: 1199px)");
    const collapseKey = `stock-float-collapsed:${
      floatRoot.dataset.stockFloatMode || "in"
    }:shelf`;
    const setCollapsed = (collapsed, persist = true) => {
      const next = Boolean(collapsed) && collapseMq.matches;
      floatRoot.classList.toggle("is-collapsed", next);
      floatToggle.setAttribute("aria-expanded", next ? "false" : "true");
      if (persist) {
        try {
          sessionStorage.setItem(collapseKey, next ? "1" : "0");
        } catch (_err) {
          /* ignore */
        }
      }
    };
    floatToggle.addEventListener("click", () => {
      setCollapsed(!floatRoot.classList.contains("is-collapsed"));
    });
    try {
      setCollapsed(sessionStorage.getItem(collapseKey) === "1", false);
    } catch (_err) {
      /* ignore */
    }
  }
  const esc = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const cellQty = (cell) => {
    const raw = (
      cell.querySelector('[name^="shelf_qty_"]')?.value || ""
    ).trim();
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : 0;
  };

  const updateCellState = (cell) => {
    const qty = cellQty(cell);
    cell.classList.toggle("is-filled", qty > 0);
    cell.closest("tr")?.classList.toggle(
      "is-filled",
      qty > 0 ||
        Boolean(
          cell
            .closest("tr")
            ?.querySelector(".stock-list-pair.is-filled")
        )
    );
    // Stock-out cells mirror the item matrix balance readout.
    const balance = cell.querySelector("[data-shelf-balance]");
    if (balance) {
      const stock = Number(cell.getAttribute("data-shelf-stock")) || 0;
      const left = stock - qty;
      balance.hidden = qty <= 0;
      balance.textContent = qty <= 0 ? "—" : String(left);
      balance.classList.toggle("is-empty", qty <= 0 || left === 0);
      balance.classList.toggle("is-projected-out", qty > 0 && left < 0);
    }
  };

  const collectShelfLines = () => {
    const lines = [];
    document.querySelectorAll("[data-shelf-cell]").forEach((cell) => {
      const qty = cellQty(cell);
      if (qty <= 0) return;
      const id = cell.getAttribute("data-shelf-id") || "";
      const name =
        cell
          .closest("tr")
          ?.querySelector(".stock-matrix-item strong")
          ?.textContent.trim() || `Item ${id}`;
      const shopName = cell.getAttribute("data-shop-name") || "";
      const priceField = cell.querySelector('[name^="shelf_price_"]');
      const price =
        priceField && !priceField.disabled
          ? priceField.value.trim()
          : "";
      const cost = cell.getAttribute("data-shelf-cost") || "";
      lines.push({ id, name, shopName, qty, price, cost });
    });
    return lines;
  };

  const numLabel = (value) =>
    Number(value || 0).toLocaleString(undefined, {
      maximumFractionDigits: 2,
    });

  const unitsLabel = (units) =>
    `${numLabel(units)} unit${units === 1 ? "" : "s"}`;

  const renderShelfFloat = () => {
    if (!floatLines || !floatEmpty) return;
    const lines = collectShelfLines();
    floatEmpty.hidden = lines.length > 0;
    floatLines.hidden = lines.length === 0;

    // One card per item name — its per-shop cells nest underneath.
    const groups = [];
    lines.forEach((line) => {
      let group = groups.find((g) => g.name === line.name);
      if (!group) {
        group = { name: line.name, lines: [] };
        groups.push(group);
      }
      group.lines.push(line);
    });

    let grandUnits = 0;
    let grandAmount = 0;
    let anyPrice = false;
    let anyEstimated = false;

    floatLines.innerHTML = groups
      .map((group) => {
        const units = group.lines.reduce((sum, l) => sum + l.qty, 0);
        let amount = 0;
        let estimated = false;
        group.lines.forEach((l) => {
          const typed = Number(l.price);
          const cost = Number(l.cost);
          if (Number.isFinite(typed) && typed > 0) {
            anyPrice = true;
            amount += typed * l.qty;
          } else if (Number.isFinite(cost) && cost > 0) {
            estimated = true;
            anyEstimated = true;
            amount += cost * l.qty;
          }
        });
        grandUnits += units;
        grandAmount += amount;
        const rows = group.lines
          .map((line) => {
            const typed = Number(line.price);
            const cost = Number(line.cost);
            const hasTyped = Number.isFinite(typed) && typed > 0;
            const hasEst =
              !hasTyped && Number.isFinite(cost) && cost > 0;
            const lineTotal = hasTyped
              ? typed * line.qty
              : hasEst
                ? cost * line.qty
                : 0;
            const unitPrice = line.price || line.cost;
            return `<li>
                <span class="stock-float-line-meta">${esc(line.shopName)}${
                  unitPrice ? ` · @ ${esc(unitPrice)}` : ""
                }</span>
                <span class="stock-float-line-amount"><strong>×${esc(
                  line.qty
                )}</strong>${
                  lineTotal > 0
                    ? ` ${hasEst ? "≈" : "="} KSh ${numLabel(lineTotal)}`
                    : ""
                }</span>
                <button type="button" data-shelf-float-remove data-shelf-id="${esc(
                  line.id
                )}" aria-label="Clear ${esc(line.name)} at ${esc(
                  line.shopName
                )}">×</button>
              </li>`;
          })
          .join("");
        return `<li class="stock-float-group">
            <div class="stock-float-group-head">
              <span class="stock-float-line-label">
                <span class="stock-float-line-title">${esc(group.name)}</span>
                <span class="stock-float-line-meta">${
                  group.lines.length
                } shop${group.lines.length === 1 ? "" : "s"} · ${unitsLabel(
                  units
                )}</span>
              </span>
              ${
                amount > 0
                  ? `<strong class="stock-float-group-total">${
                      estimated ? "≈ " : ""
                    }KSh ${numLabel(amount)}</strong>`
                  : ""
              }
              <button
                type="button"
                data-shelf-float-remove-item
                data-shelf-ids="${group.lines
                  .map((l) => esc(l.id))
                  .join(",")}"
                aria-label="Clear ${esc(group.name)} from all shops"
              >×</button>
            </div>
            <ul class="stock-float-group-lines">${rows}</ul>
          </li>`;
      })
      .join("");

    if (floatTotal && floatTotalCaption && floatTotalValue) {
      floatTotal.hidden = lines.length === 0;
      if (anyEstimated && grandAmount > 0) {
        floatTotalCaption.textContent = `${
          floatMode === "out" ? "Est. loss" : "Est. total"
        } · ${unitsLabel(grandUnits)}`;
        floatTotalValue.textContent = `≈ KSh ${numLabel(grandAmount)}`;
      } else if (anyPrice && grandAmount > 0) {
        floatTotalCaption.textContent = `Total · ${unitsLabel(grandUnits)}`;
        floatTotalValue.textContent = `KSh ${numLabel(grandAmount)}`;
      } else {
        floatTotalCaption.textContent = "Total";
        floatTotalValue.textContent = unitsLabel(grandUnits);
      }
    }
    if (floatClear) floatClear.hidden = lines.length === 0;
    // Disabled only while JS is running — a no-JS post still reaches the
    // server, which validates and reports an empty submit.
    if (floatSubmit) floatSubmit.disabled = lines.length === 0;
    document
      .querySelectorAll("[data-shelf-cell]")
      .forEach(updateCellState);
  };

  const clearLine = (id) => {
    document
      .querySelector(`[data-shelf-cell][data-shelf-id="${id}"]`)
      ?.querySelectorAll("input")
      .forEach((field) => {
        field.value = "";
      });
  };

  if (floatLines) {
    (layout || shelfPanel)?.addEventListener("input", renderShelfFloat);
    (layout || shelfPanel)?.addEventListener("change", renderShelfFloat);
    floatLines.addEventListener("click", (event) => {
      const groupBtn = event.target.closest("[data-shelf-float-remove-item]");
      if (groupBtn) {
        (groupBtn.getAttribute("data-shelf-ids") || "")
          .split(",")
          .filter(Boolean)
          .forEach(clearLine);
        renderShelfFloat();
        return;
      }
      const btn = event.target.closest("[data-shelf-float-remove]");
      if (!btn) return;
      clearLine(btn.getAttribute("data-shelf-id"));
      renderShelfFloat();
    });
    floatClear?.addEventListener("click", () => {
      document
        .querySelectorAll(
          '[data-shelf-cell] input[name^="shelf_"]'
        )
        .forEach((field) => {
          field.value = "";
        });
      renderShelfFloat();
    });
    renderShelfFloat();
  }

  /* --- Shelf request page: the flow picker switches the submit between a
   * transfer to the To shop and a one-way trade with a person. Trade picks
   * show the person fields and free the To-shop select; trade in enables
   * the per-cell unit value input. */
  const flowInputs = Array.from(
    document.querySelectorAll("[data-shelf-flow]")
  );
  if (flowInputs.length) {
    const tradeFields = document.querySelector(
      "[data-shelf-trade-fields]"
    );
    const submitLabel = document.querySelector(
      "[data-shelf-submit-label]"
    );
    const flowHint = document.querySelector("[data-shelf-flow-hint]");
    const fromNav = document.querySelector("[data-stock-from-nav]");
    const toLabel = document.querySelector("[data-shelf-to-label]");
    const personInput = document.querySelector(
      '[data-shelf-trade-fields] [name="person_name"]'
    );
    const transferTo = toLabel ? toLabel.textContent : "";
    const priceFields = () =>
      Array.from(document.querySelectorAll("[data-shelf-trade-price]"));
    const flowMeta = {
      transfer: {
        label: "Send transfer",
        hint: "Transfer sends the entered qty to the To shop.",
      },
      trade_out: {
        label: "Record trade out",
        hint: "Trade out — the person takes items off the shelf.",
      },
      trade_in: {
        label: "Record trade in",
        hint: "Trade in — the person brings items; the Value column is their unit value.",
      },
    };
    const syncFlow = () => {
      const flow =
        (flowInputs.find((input) => input.checked) || {}).value ||
        "transfer";
      const isTrade = flow !== "transfer";
      const meta = flowMeta[flow] || flowMeta.transfer;
      if (tradeFields) tradeFields.hidden = !isTrade;
      if (submitLabel) submitLabel.textContent = meta.label;
      if (flowHint) flowHint.textContent = meta.hint;
      if (fromNav) fromNav.disabled = isTrade;
      if (toLabel) {
        toLabel.textContent = isTrade
          ? personInput?.value.trim() || "Person"
          : transferTo;
      }
      priceFields().forEach((input) => {
        const off = flow !== "trade_in";
        input.hidden = off;
        input.disabled = off;
      });
      renderShelfFloat();
    };
    /* Pre-pick the flow from ?flow= so sidebar links can land on a trade. */
    const flowParam = new URLSearchParams(window.location.search).get(
      "flow"
    );
    if (flowParam) {
      const match = flowInputs.find((input) => input.value === flowParam);
      if (match) match.checked = true;
    }
    flowInputs.forEach((input) =>
      input.addEventListener("change", syncFlow)
    );
    personInput?.addEventListener("input", syncFlow);
    syncFlow();
  }

  /* --- Trade-out portion targets: search/browse picker -----------------
   * Each [data-portion-picker] lists the registered items from a hidden
   * datalist. Typing filters the suggestions; focusing the empty input
   * opens the whole list. Picked items become rows with a portions-per-
   * unit input — each portion is valued at the item's list price. */
  const portionEscape = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const portionRead = (input) => {
    const num = Number.parseFloat(input.value);
    return Number.isFinite(num) && num > 0 ? num : 0;
  };

  const bindPortionPicker = (root) => {
    if (!root || root.dataset.portionBound === "1") return;
    root.dataset.portionBound = "1";
    const search = root.querySelector("[data-portion-search]");
    const results = root.querySelector("[data-portion-results]");
    const rows = root.querySelector("[data-portion-rows]");
    const totalEl = root.querySelector("[data-portion-total]");
    const emptyEl = root.querySelector("[data-portion-empty]");
    const source = Array.from(
      root.querySelectorAll("[data-portion-source] option")
    ).map((opt) => ({
      id: opt.getAttribute("data-id") || "",
      name: opt.value || "",
      category: opt.getAttribute("data-category") || "",
    }));
    if (!rows) return;

    const qtyInputs = () =>
      Array.from(rows.querySelectorAll("[data-portion-qty]"));

    const pickedIds = () =>
      new Set(
        Array.from(rows.querySelectorAll('[name="portion_item_id"]')).map(
          (input) => String(input.value || "")
        )
      );

    const syncTotal = () => {
      const inputs = qtyInputs();
      const sum = inputs.reduce((acc, input) => acc + portionRead(input), 0);
      if (totalEl) {
        totalEl.textContent = String(
          Math.round(sum * 1000) / 1000
        ).replace(/\.?0+$/, "");
      }
      const complete =
        inputs.length > 0 && inputs.every((input) => portionRead(input) > 0);
      root.classList.toggle("is-complete", complete);
      root.classList.toggle("has-rows", inputs.length > 0);
      if (emptyEl) emptyEl.hidden = inputs.length > 0;
    };

    const addRow = ({ id, name, category, portions }) => {
      if (!id || pickedIds().has(String(id))) return;
      const row = document.createElement("div");
      row.className = "item-combo-row";
      row.setAttribute("data-portion-row", "");
      row.innerHTML = `
        <input type="hidden" name="portion_item_id" value="${portionEscape(id)}">
        <div class="item-combo-row-main">
          <span class="item-combo-row-name">${portionEscape(name)}</span>
          <span class="item-combo-row-price">${portionEscape(category)}</span>
        </div>
        <label class="item-combo-row-qty">
          <input
            type="number"
            name="portion_qty"
            min="0.001"
            step="any"
            inputmode="decimal"
            placeholder="0"
            data-portion-qty
            required
            aria-label="Portions per unit of ${portionEscape(name)}"
          >
          <span aria-hidden="true">×</span>
        </label>
        <button
          type="button"
          class="item-combo-row-remove"
          data-portion-remove
          aria-label="Remove ${portionEscape(name)}"
        >
          <i data-lucide="x" aria-hidden="true"></i>
        </button>`;
      rows.appendChild(row);
      if (window.lucide?.createIcons) window.lucide.createIcons();
      const input = row.querySelector("[data-portion-qty]");
      if (portions !== undefined && portions !== null && portions !== "") {
        input.value = portions;
      }
      syncTotal();
    };

    const renderResults = () => {
      if (!results || !search) return;
      const needle = search.value.trim().toLowerCase();
      const picked = pickedIds();
      const matches = source
        .filter(
          (entry) =>
            !needle ||
            entry.name.toLowerCase().includes(needle) ||
            entry.category.toLowerCase().includes(needle)
        )
        .slice(0, 8);
      results.replaceChildren();
      if (!matches.length) {
        const empty = document.createElement("p");
        empty.className = "item-combo-result-empty";
        empty.textContent = "No matching items.";
        results.appendChild(empty);
      } else {
        matches.forEach((entry) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "item-combo-result";
          const taken = picked.has(String(entry.id));
          btn.disabled = taken;
          btn.innerHTML = `
            <span class="item-combo-result-name">${portionEscape(
              entry.name
            )}</span>
            <span class="item-combo-result-meta">
              ${entry.category ? `<small>${portionEscape(entry.category)}</small>` : ""}
              <small>${taken ? "Added" : "Pick for portions"}</small>
            </span>`;
          btn.addEventListener("click", () => {
            addRow({ id: entry.id, name: entry.name, category: entry.category });
            search.value = "";
            renderResults();
            search.focus();
          });
          results.appendChild(btn);
        });
      }
      results.hidden = false;
      search.setAttribute("aria-expanded", "true");
    };

    const hideResults = () => {
      if (!results) return;
      results.hidden = true;
      search?.setAttribute("aria-expanded", "false");
    };

    if (search && results) {
      search.addEventListener("focus", renderResults);
      search.addEventListener("input", renderResults);
      search.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          hideResults();
        } else if (event.key === "ArrowDown" && !results.hidden) {
          event.preventDefault();
          results.querySelector("button:not(:disabled)")?.focus();
        } else if (event.key === "Enter") {
          event.preventDefault();
          results
            .querySelector("button:not(:disabled)")
            ?.dispatchEvent(new Event("click", { bubbles: false }));
        }
      });
      results.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          hideResults();
          search.focus();
        }
      });
      document.addEventListener("click", (event) => {
        if (!root.contains(event.target)) hideResults();
      });
    }

    root.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-portion-remove]");
      if (!btn) return;
      btn.closest("[data-portion-row]")?.remove();
      syncTotal();
    });
    root.addEventListener("input", (event) => {
      if (event.target.matches("[data-portion-qty]")) syncTotal();
    });

    root.__portionFill = (targets) => {
      rows.replaceChildren();
      (targets || []).forEach((target) =>
        addRow({
          id: target.id,
          name: target.name,
          category: target.suspended
            ? "Suspended"
            : target.category || "",
          portions: target.portions,
        })
      );
      syncTotal();
    };
    root.__portionReset = () => {
      root.__portionFill([]);
      if (search) search.value = "";
      hideResults();
    };
    syncTotal();
  };

  document
    .querySelectorAll("[data-portion-picker]")
    .forEach(bindPortionPicker);

  // Edit-modal prefill + register reset hook for the inline scripts.
  window.shelfPortionPicker = {
    fill(scope, targets) {
      const root =
        scope?.querySelector?.("[data-portion-picker]") ||
        (scope?.matches?.("[data-portion-picker]") ? scope : null);
      if (!root) return;
      bindPortionPicker(root);
      root.__portionFill?.(targets);
    },
    reset(scope) {
      const root =
        scope?.querySelector?.("[data-portion-picker]") ||
        (scope?.matches?.("[data-portion-picker]") ? scope : null);
      if (!root) return;
      bindPortionPicker(root);
      root.__portionReset?.();
    },
  };

  // A fresh register intent clears previously picked portion items.
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest?.(
      '[data-modal-open="shelf-stock"]'
    );
    if (!trigger) return;
    window.shelfPortionPicker.reset(
      document.querySelector(
        '[data-modal="shelf-stock"] [data-shelf-register-form]'
      )
    );
  });
})();
