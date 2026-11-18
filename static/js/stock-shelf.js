/**
 * Shelf stock popup — register a shelf item from the stock-management sidebar.
 * Opens the [data-modal="shelf-stock"] dialog; when the modal is absent (other
 * pages) the sidebar button navigates to the view page with ?shelf=1.
 */
(function () {
  "use strict";

  const modal = document.querySelector("[data-modal='shelf-stock']");

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
      const inputsRow = target.closest("tr[data-shelf-row-inputs]");
      if (!inputsRow) return;
      (
        document.querySelector("[data-shelf-float-submit]") ||
        document.querySelector("[data-shelf-bulk-submit]") ||
        inputsRow.querySelector('button[name="shelf_action"][value^="transfer:"]')
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

  // Expandable shelf rows (items-page pattern): clicking a row's head opens
  // the inputs row beneath it; typed values persist while collapsed.
  const shelfPanel = document.querySelector("[data-shelf-stock]");
  shelfPanel?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-shelf-row-toggle]");
    if (!btn) return;
    const headRow = btn.closest("tr");
    const inputsRow = headRow && headRow.nextElementSibling;
    if (!inputsRow || !inputsRow.hasAttribute("data-shelf-row-inputs")) return;
    const opening = inputsRow.hidden;
    inputsRow.hidden = !opening;
    headRow.classList.toggle("is-open", opening);
    btn.setAttribute("aria-expanded", opening ? "true" : "false");
    if (opening) {
      inputsRow.querySelector("input, select")?.focus({ preventScroll: true });
    }
  });

  // Shelf float panel (mirrors the item stock-in/out submit card): lists the
  // filled lines — expanded rows and the quick-add strip — and submits them
  // all via in_bulk/out_bulk.
  const floatRoot = document.querySelector("[data-stock-float]");
  const floatLines = floatRoot?.querySelector("[data-shelf-float-lines]");
  const floatEmpty = floatRoot?.querySelector("[data-shelf-float-empty]");
  const floatClear = floatRoot?.querySelector("[data-shelf-float-clear]");
  const floatToggle = floatRoot?.querySelector("[data-shelf-float-toggle]");
  const floatSubmit = floatRoot?.querySelector("[data-shelf-float-submit]");

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

  const collectShelfLines = () => {
    const lines = [];
    document
      .querySelectorAll('[data-shelf-row-inputs] input[name^="shelf_qty_"]')
      .forEach((qtyInput) => {
        const qty = qtyInput.value.trim();
        if (!qty || !(Number(qty) > 0)) return;
        const inputsRow = qtyInput.closest("tr");
        const headRow = inputsRow && inputsRow.previousElementSibling;
        const id = qtyInput.name.slice("shelf_qty_".length);
        const name =
          headRow
            ?.querySelector(".stock-matrix-item strong")
            ?.textContent.trim() || `Item ${id}`;
        const shopName = headRow?.getAttribute("data-shop-name") || "";
        const price =
          inputsRow
            .querySelector(`input[name="shelf_price_${id}"]`)
            ?.value.trim() || "";
        const note =
          inputsRow
            .querySelector(`input[name="shelf_note_${id}"]`)
            ?.value.trim() || "";
        lines.push({ id, name, shopName, qty, price, note });
      });
    return lines;
  };

  const renderShelfFloat = () => {
    if (!floatLines || !floatEmpty) return;
    const lines = collectShelfLines();
    floatEmpty.hidden = lines.length > 0;
    floatLines.hidden = lines.length === 0;
    floatLines.innerHTML = lines
      .map(
        (line) =>
          `<li>
            <span title="${esc(line.name)}${
              line.shopName ? ` · ${esc(line.shopName)}` : ""
            }">${esc(line.name)}${
              line.shopName ? ` · ${esc(line.shopName)}` : ""
            }${line.price ? ` @ ${esc(line.price)}` : ""
            }${line.note ? ` · ${esc(line.note)}` : ""}</span>
            <strong>×${esc(line.qty)}</strong>
            <button type="button" data-shelf-float-remove data-shelf-id="${esc(
              line.id
            )}" aria-label="Clear ${esc(line.name)}">×</button>
          </li>`
      )
      .join("");
    if (floatClear) floatClear.hidden = lines.length === 0;
    // Disabled only while JS is running — a no-JS post still reaches the
    // server, which validates and reports an empty submit.
    if (floatSubmit) floatSubmit.disabled = lines.length === 0;
    // Highlight filled head rows like the item stock matrix.
    document
      .querySelectorAll('[data-shelf-row-inputs] input[name^="shelf_qty_"]')
      .forEach((qtyInput) => {
        const headRow = qtyInput.closest("tr")?.previousElementSibling;
        headRow?.classList.toggle(
          "is-filled",
          Number(qtyInput.value.trim()) > 0
        );
      });
  };

  const clearLine = (id) => {
    const inputsRow = document
      .querySelector(
        `[data-shelf-row-inputs] input[name="shelf_qty_${id}"]`
      )
      ?.closest("tr");
    inputsRow
      ?.querySelectorAll("input")
      .forEach((field) => {
        field.value = "";
      });
  };

  if (floatLines) {
    (layout || shelfPanel)?.addEventListener("input", renderShelfFloat);
    (layout || shelfPanel)?.addEventListener("change", renderShelfFloat);
    floatLines.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-shelf-float-remove]");
      if (!btn) return;
      clearLine(btn.getAttribute("data-shelf-id"));
      renderShelfFloat();
    });
    floatClear?.addEventListener("click", () => {
      document
        .querySelectorAll(
          '[data-shelf-row-inputs] input[name^="shelf_"]'
        )
        .forEach((field) => {
          field.value = "";
        });
      renderShelfFloat();
    });
    renderShelfFloat();
  }
})();
