(() => {
  const body = document.body;
  const selectModal = document.querySelector(".shop-select-modal");
  const SNOOZE_MS = 5 * 60 * 1000;

  if (selectModal) {
    body.classList.add("workspace-modal-open");

    const cancel = selectModal.querySelector("[data-shop-select-cancel]");
    cancel?.addEventListener("click", () => {
      const href = cancel.getAttribute("data-href");
      if (href) window.location.href = href;
    });

    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      const href = cancel?.getAttribute("data-href");
      if (href) window.location.href = href;
    });

    const picker = selectModal.querySelector("[data-shop-picker]");
    if (picker) {
      const syncSelected = () => {
        picker.querySelectorAll(".shop-select-card").forEach((card) => {
          const input = card.querySelector("[data-shop-option]");
          card.classList.toggle("is-selected", Boolean(input?.checked));
        });
      };
      picker.addEventListener("change", syncSelected);
      syncSelected();
    }

    selectModal.querySelectorAll("[data-password-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const field = btn.closest(".password-field");
        const input = field?.querySelector("input");
        if (!input) return;
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        const openIcon = btn.querySelector("[data-eye-open]");
        const closedIcon = btn.querySelector("[data-eye-closed]");
        if (openIcon) openIcon.hidden = show;
        if (closedIcon) closedIcon.hidden = !show;
        btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      });
    });
  }

  document.querySelectorAll(".shop-floor-category").forEach((section, index) => {
    section.style.setProperty("--stagger", String(index));
  });

  const shopFloor = document.querySelector(".shop-floor[data-shop-view]");
  if (shopFloor) {
    const VIEW_KEY = "richcom.myShop.catalogView";
    const VIEW_MODES = new Set(["cards", "list", "scroll"]);
    const viewButtons = shopFloor.querySelectorAll("[data-shop-view-set]");

    const syncCategoryScrollControls = (section) => {
      const track = section.querySelector("[data-category-scroll-track]");
      const buttons = section.querySelectorAll("[data-category-scroll]");
      if (!track || !buttons.length) return;

      const isScrollView = shopFloor.dataset.shopView === "scroll";
      if (!isScrollView) {
        buttons.forEach((btn) => {
          btn.disabled = true;
        });
        return;
      }

      const maxScroll = Math.max(0, track.scrollWidth - track.clientWidth);
      const canScroll = maxScroll > 4;
      const atStart = track.scrollLeft <= 4;
      const atEnd = track.scrollLeft >= maxScroll - 4;

      buttons.forEach((btn) => {
        const isPrev = btn.dataset.categoryScroll === "prev";
        btn.disabled = !canScroll || (isPrev ? atStart : atEnd);
      });
    };

    const syncAllCategoryScrollControls = () => {
      shopFloor
        .querySelectorAll("[data-item-category-group]")
        .forEach((section) => syncCategoryScrollControls(section));
    };

    const applyView = (view) => {
      const next = VIEW_MODES.has(view) ? view : "cards";
      shopFloor.dataset.shopView = next;
      viewButtons.forEach((btn) => {
        const active = btn.dataset.shopViewSet === next;
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      });
      try {
        localStorage.setItem(VIEW_KEY, next);
      } catch (_) {
        /* ignore quota / private mode */
      }
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          syncAllCategoryScrollControls();
          if (window.lucide?.createIcons) window.lucide.createIcons();
        });
      });
    };

    let saved = "";
    try {
      saved = localStorage.getItem(VIEW_KEY) || "";
    } catch (_) {
      saved = "";
    }
    applyView(VIEW_MODES.has(saved) ? saved : shopFloor.dataset.shopView);

    shopFloor.addEventListener("click", (event) => {
      const viewBtn = event.target.closest?.("[data-shop-view-set]");
      if (viewBtn && shopFloor.contains(viewBtn)) {
        applyView(viewBtn.dataset.shopViewSet);
        return;
      }

      const scrollBtn = event.target.closest?.("[data-category-scroll]");
      if (!scrollBtn || !shopFloor.contains(scrollBtn)) return;
      if (shopFloor.dataset.shopView !== "scroll") return;
      if (scrollBtn.disabled) return;

      const section = scrollBtn.closest("[data-item-category-group]");
      const track = section?.querySelector("[data-category-scroll-track]");
      if (!track) return;

      const dir = scrollBtn.dataset.categoryScroll === "prev" ? -1 : 1;
      const step = Math.max(track.clientWidth * 0.78, 220);
      track.scrollBy({ left: dir * step, behavior: "smooth" });
    });

    shopFloor.addEventListener(
      "scroll",
      (event) => {
        const track = event.target;
        if (!(track instanceof HTMLElement)) return;
        if (!track.hasAttribute("data-category-scroll-track")) return;
        const section = track.closest("[data-item-category-group]");
        if (section) syncCategoryScrollControls(section);
      },
      true
    );

    window.addEventListener("resize", () => {
      syncAllCategoryScrollControls();
    });

    document.addEventListener("shop-catalog:rendered", () => {
      requestAnimationFrame(syncAllCategoryScrollControls);
    });

    const catalogRoot = shopFloor.querySelector("[data-catalog-root]");
    if (catalogRoot && typeof MutationObserver === "function") {
      let syncTimer = 0;
      const observer = new MutationObserver(() => {
        window.clearTimeout(syncTimer);
        syncTimer = window.setTimeout(syncAllCategoryScrollControls, 60);
      });
      observer.observe(catalogRoot, { childList: true, subtree: true });
    }
  }

  const syncModalOpen = () => {
    const anyOpen = Boolean(
      document.querySelector(".workspace-modal:not([hidden])")
    );
    body.classList.toggle("workspace-modal-open", Boolean(anyOpen || selectModal));
  };

  const bindModal = ({
    modal,
    openSelectors,
    closeSelectors,
    autoOpen = false,
    onClose = null,
  }) => {
    if (!modal || selectModal) return null;

    const open = () => {
      modal.hidden = false;
      syncModalOpen();
      if (window.lucide?.createIcons) window.lucide.createIcons();
    };

    const close = () => {
      modal.hidden = true;
      syncModalOpen();
      if (typeof onClose === "function") onClose();
    };

    if (autoOpen) open();
    else modal.hidden = true;

    document.querySelectorAll(openSelectors).forEach((btn) => {
      btn.addEventListener("click", open);
    });
    modal.querySelectorAll(closeSelectors).forEach((el) => {
      el.addEventListener("click", close);
    });

    return { open, close, modal };
  };

  const requestModal = document.querySelector("[data-stock-request-modal]");
  let requestSnoozeTimer = null;

  const csrfToken = () => {
    const input = document.querySelector("[name=csrfmiddlewaretoken]");
    if (input?.value) return input.value;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  };

  const postAck = (ackUrl) => {
    if (!ackUrl) return;
    const token = csrfToken();
    const body = new URLSearchParams();
    if (token) body.set("csrfmiddlewaretoken", token);
    fetch(ackUrl, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
        ...(token ? { "X-CSRFToken": token } : {}),
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: body.toString(),
      credentials: "same-origin",
    }).catch(() => {});
  };

  const ackSupplierSeen = () => {
    const ackUrl =
      requestModal?.getAttribute("data-stock-request-supplier-ack-url") ||
      document
        .querySelector("[data-stock-request-supplier-ack-url]")
        ?.getAttribute("data-stock-request-supplier-ack-url") ||
      "";
    postAck(ackUrl);
  };

  const ackDecisionSeen = () => {
    const ackUrl =
      document
        .querySelector("[data-stock-decision-modal]")
        ?.getAttribute("data-stock-decision-ack-url") ||
      document
        .querySelector("[data-stock-decision-ack-url]")
        ?.getAttribute("data-stock-decision-ack-url") ||
      "";
    postAck(ackUrl);
  };

  const focusIncomingRequest = (requestId) => {
    if (!requestModal) return;
    const targetId = String(requestId || "").trim();
    requestModal.querySelectorAll("[data-request-card]").forEach((card) => {
      const match =
        Boolean(targetId) &&
        String(card.getAttribute("data-request-id") || "") === targetId;
      card.classList.toggle("is-focused", match);
      if (!match) return;
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
      const qty = card.querySelector("[data-transfer-qty]");
      if (qty && !qty.disabled) {
        window.setTimeout(() => qty.focus(), 40);
      }
    });
  };

  const requestControls = bindModal({
    modal: requestModal,
    openSelectors: "[data-stock-request-open]",
    closeSelectors: "[data-stock-request-close]",
    autoOpen: requestModal?.getAttribute("data-auto-open") === "1",
    onClose: () => {
      ackSupplierSeen();
      requestModal
        ?.querySelectorAll("[data-request-card].is-focused")
        .forEach((card) => card.classList.remove("is-focused"));
      window.clearTimeout(requestSnoozeTimer);
      requestSnoozeTimer = window.setTimeout(() => {
        if (requestModal && document.querySelector("[data-stock-request-open]")) {
          requestControls?.open();
        }
      }, SNOOZE_MS);
    },
  });

  document.querySelectorAll("[data-stock-request-open]").forEach((btn) => {
    btn.addEventListener("click", () => {
      window.requestAnimationFrame(() => {
        focusIncomingRequest(btn.getAttribute("data-stock-request-focus") || "");
      });
    });
  });

  // If the incoming modal auto-opened, mark supplier alerts as seen.
  if (requestModal && requestModal.hidden === false) {
    ackSupplierSeen();
  }

  const decisionModal = document.querySelector("[data-stock-decision-modal]");
  const decisionShouldAutoOpen =
    decisionModal?.getAttribute("data-auto-open") === "1" && !requestModal;
  const decisionControls = bindModal({
    modal: decisionModal,
    openSelectors: "[data-stock-decision-open]",
    closeSelectors: "[data-stock-decision-close]",
    autoOpen: decisionShouldAutoOpen,
    onClose: () => {
      // Closing marks updates as seen so the modal only appears once.
      ackDecisionSeen();
    },
  });

  // Seeing the updates (auto-open or manual open) counts as acknowledged.
  if (decisionModal && decisionShouldAutoOpen && decisionModal.hidden === false) {
    ackDecisionSeen();
  }
  document.querySelectorAll("[data-stock-decision-open]").forEach((btn) => {
    btn.addEventListener("click", () => {
      ackDecisionSeen();
    });
  });

  if (requestModal && decisionModal && requestModal.hidden === false) {
    decisionModal.hidden = true;
    syncModalOpen();
  }

  const createModal = document.querySelector("[data-stock-create-modal]");
  const createControls = bindModal({
    modal: createModal,
    openSelectors: "[data-stock-create-open]",
    closeSelectors: "[data-stock-create-close]",
    autoOpen: false,
  });

  if (createModal) {
    const searchInput = createModal.querySelector("[data-stock-create-search]");
    const itemsRoot = createModal.querySelector("[data-stock-create-items]");
    const itemsHead = createModal.querySelector("[data-stock-create-items-head]");
    const form = createModal.querySelector("[data-stock-create-form]");
    const fromSelect = createModal.querySelector("[data-stock-create-from]");
    const itemsHint = createModal.querySelector("[data-stock-create-items-hint]");
    const showEmptyToggle = createModal.querySelector("[data-stock-create-show-empty]");
    const noStockMsg = createModal.querySelector("[data-stock-create-no-stock]");
    const selectedBadge = createModal.querySelector("[data-stock-create-selected]");
    const selectedCount = createModal.querySelector("[data-stock-create-selected-count]");
    const noResults = createModal.querySelector("[data-stock-create-no-results]");
    const fromStockUrl =
      createModal.getAttribute("data-stock-create-from-stock-url") || "";
    const createSerialSearchUrl =
      createModal.getAttribute("data-serial-search-url") || "";
    const createSupplyShopId =
      createModal.getAttribute("data-supply-shop-id") || "";
    const createSerialModal = document.querySelector(
      "[data-stock-create-serial-modal]"
    );
    const createSerialEntry = createSerialModal?.querySelector(
      "[data-stock-create-serial-entry]"
    );
    const createSerialScanned = createSerialModal?.querySelector(
      "[data-stock-create-serial-scanned]"
    );
    const createSerialCount = createSerialModal?.querySelector(
      "[data-stock-create-serial-count]"
    );
    const createSerialTitle = createSerialModal?.querySelector(
      "[data-stock-create-serial-title]"
    );
    const createSerialShop = createSerialModal?.querySelector(
      "[data-stock-create-serial-shop]"
    );
    let fromStockSeq = 0;
    let searchTimer = null;
    let rows = [];
    const selectedItems = new Map();
    let activeCreateSerialRow = null;
    let createSerialSearchTimer = 0;
    let createSerialSearchSeq = 0;
    let createSerialCommitBusy = false;
    let lastCreateSerialCommit = "";
    let lastCreateSerialCommitAt = 0;

    const escapeHtml = (value) =>
      String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");

    const normalizeCreateSerial = (value) =>
      String(value || "").trim().toUpperCase();

    const rowTracksSerial = (row) =>
      row?.getAttribute("data-track-serial") === "1";

    const rowSerials = (row) => {
      const raw = row?.querySelector("[data-stock-create-serials]")?.value || "";
      return raw
        .split(/[\n,]+/)
        .map((s) => normalizeCreateSerial(s))
        .filter(Boolean);
    };

    const applyRowSerials = (row, serials) => {
      const unique = [...new Set(serials.map(normalizeCreateSerial).filter(Boolean))];
      const serialHidden = row.querySelector("[data-stock-create-serials]");
      const qty = row.querySelector("[data-stock-create-qty]");
      if (serialHidden) serialHidden.value = unique.join("\n");
      if (qty) qty.value = unique.length ? String(unique.length) : "";
      return unique;
    };

    const captureSelectedQtys = () => {
      rows.forEach((row) => {
        const id = row.getAttribute("data-item-id") || "";
        const name =
          row.querySelector(".stock-create-item-name")?.textContent?.trim() || "";
        const qty = row.querySelector("[data-stock-create-qty]");
        const avail = Number(row.getAttribute("data-avail") || 0) || 0;
        const trackSerial = rowTracksSerial(row);
        if (!id || !qty) return;
        const value = String(qty.value || "").trim();
        const serials = trackSerial ? rowSerials(row) : [];
        if (Number(value) > 0) {
          selectedItems.set(id, {
            name,
            avail,
            qty: value,
            trackSerial,
            serials,
          });
        } else {
          selectedItems.delete(id);
        }
      });
    };

    const syncRowEnabled = (row) => {
      const qty = row.querySelector("[data-stock-create-qty]");
      const idInput = row.querySelector("[data-stock-create-id]");
      const serialHidden = row.querySelector("[data-stock-create-serials]");
      const avail = Number(row.getAttribute("data-avail") || 0) || 0;
      const hasQty = Number(qty?.value || 0) > 0;
      const canSend = avail > 0;
      if (qty) {
        qty.disabled = !canSend;
        if (!canSend) {
          qty.value = "";
          if (serialHidden) serialHidden.value = "";
        }
      }
      if (idInput) idInput.disabled = !(canSend && hasQty);
      if (serialHidden) serialHidden.disabled = !(canSend && hasQty);
      row.classList.toggle("is-active", canSend && hasQty);
      row.classList.toggle("is-out", !canSend);
    };

    const refreshCreateIcons = () => {
      if (window.lucide?.createIcons) window.lucide.createIcons();
    };

    const modalCreateSerials = () =>
      [
        ...(createSerialScanned?.querySelectorAll(
          "[data-stock-create-serial-scanned-value]"
        ) || []),
      ]
        .map((el) => normalizeCreateSerial(el.textContent))
        .filter(Boolean);

    const syncCreateSerialModalCount = () => {
      const serials = modalCreateSerials();
      if (createSerialCount) createSerialCount.textContent = String(serials.length);
      if (createSerialScanned) createSerialScanned.hidden = serials.length === 0;
    };

    const hideCreateSerialSuggest = () => {
      const suggest = createSerialModal?.querySelector("[data-serial-suggest]");
      if (!suggest) return;
      suggest.hidden = true;
      suggest.innerHTML = "";
    };

    const createModalScannedItem = (serial) => {
      if (!createSerialScanned || !serial) return null;
      const li = document.createElement("li");
      li.className = "stock-serial-modal-scanned-item";
      li.dataset.serialValue = serial;
      const value = document.createElement("span");
      value.className = "stock-serial-modal-scanned-value";
      value.setAttribute("data-stock-create-serial-scanned-value", "");
      value.textContent = serial;
      li.appendChild(value);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "stock-serial-modal-scanned-remove";
      remove.setAttribute("data-stock-create-serial-remove", "");
      remove.setAttribute("aria-label", `Remove ${serial}`);
      remove.innerHTML = '<i data-lucide="x" aria-hidden="true"></i>';
      li.appendChild(remove);
      createSerialScanned.prepend(li);
      refreshCreateIcons();
      syncCreateSerialModalCount();
      return li;
    };

    const renderCreateSerialSuggest = (results) => {
      const suggest = createSerialModal?.querySelector("[data-serial-suggest]");
      if (!suggest) return;
      suggest.innerHTML = "";
      if (!results.length) {
        const empty = document.createElement("button");
        empty.type = "button";
        empty.className = "stock-supplier-suggest-option";
        empty.disabled = true;
        empty.innerHTML =
          "<strong>No matching serials</strong><small>Available at this shop only</small>";
        suggest.appendChild(empty);
        suggest.hidden = false;
        return;
      }
      results.forEach((serial) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "stock-supplier-suggest-option";
        btn.innerHTML = "<strong></strong>";
        btn.querySelector("strong").textContent = serial;
        btn.addEventListener("mousedown", (event) => {
          event.preventDefault();
          commitCreateSerialEntry({ serial });
        });
        suggest.appendChild(btn);
      });
      suggest.hidden = false;
    };

    const runCreateSerialSearch = async () => {
      if (!createSerialSearchUrl || !createSupplyShopId || !activeCreateSerialRow) {
        return;
      }
      const itemId = activeCreateSerialRow.getAttribute("data-item-id") || "";
      if (!itemId || !createSerialEntry) return;
      const query = normalizeCreateSerial(createSerialEntry.value);
      const seq = ++createSerialSearchSeq;
      const params = new URLSearchParams({
        item_id: itemId,
        shop_id: createSupplyShopId,
        q: query,
        limit: 60,
      });
      modalCreateSerials().forEach((serial) => params.append("exclude", serial));
      try {
        const response = await fetch(`${createSerialSearchUrl}?${params.toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        if (!response.ok) return;
        const data = await response.json();
        if (seq !== createSerialSearchSeq) return;
        renderCreateSerialSuggest(data.results || []);
      } catch (_error) {
        /* ignore */
      }
    };

    const dispatchCreateSerialSettled = (detail = {}) => {
      createSerialEntry?.dispatchEvent(
        new CustomEvent("myshop:serial-commit-settled", {
          bubbles: true,
          detail,
        })
      );
    };

    const commitCreateSerialEntry = async ({ serial: override } = {}) => {
      if (createSerialCommitBusy || !createSerialEntry || !activeCreateSerialRow) {
        dispatchCreateSerialSettled({ ok: false, reason: "busy" });
        return false;
      }
      let serial = normalizeCreateSerial(override || createSerialEntry.value);
      if (!serial) {
        dispatchCreateSerialSettled({ ok: false, reason: "empty" });
        return false;
      }
      const now = Date.now();
      if (
        serial === lastCreateSerialCommit &&
        now - lastCreateSerialCommitAt < 1000
      ) {
        createSerialEntry.value = "";
        dispatchCreateSerialSettled({ ok: false, reason: "dedupe", serial });
        return false;
      }
      if (modalCreateSerials().includes(serial)) {
        createSerialEntry.value = "";
        createSerialEntry.classList.add("is-duplicate");
        window.setTimeout(
          () => createSerialEntry.classList.remove("is-duplicate"),
          700
        );
        createSerialEntry.focus?.();
        dispatchCreateSerialSettled({ ok: false, reason: "duplicate", serial });
        return false;
      }
      const avail = Number(activeCreateSerialRow.getAttribute("data-avail") || 0) || 0;
      if (avail > 0 && modalCreateSerials().length >= avail) {
        createSerialEntry.value = "";
        createSerialEntry.focus?.();
        dispatchCreateSerialSettled({ ok: false, reason: "stock", serial });
        return false;
      }
      const root = createSerialEntry.closest("[data-serial-search-root]");
      const options = [
        ...(root?.querySelectorAll(
          ".stock-supplier-suggest-option:not([disabled])"
        ) || []),
      ];
      // Prefer an exact match on the typed serial; only fall back to the top
      // suggestion when it is the sole option, so a stale list can't hijack
      // what the user actually scanned.
      const exactOption = options.find(
        (opt) => normalizeCreateSerial(opt.textContent) === serial
      );
      if (exactOption) {
        serial = normalizeCreateSerial(exactOption.textContent);
      } else if (options.length === 1) {
        const picked = normalizeCreateSerial(options[0].textContent);
        if (picked) serial = picked;
      }
      createSerialEntry.value = "";
      hideCreateSerialSuggest();
      createSerialCommitBusy = true;
      let ok = false;
      try {
        if (!createSerialSearchUrl) {
          createModalScannedItem(serial);
          ok = true;
        } else {
          const params = new URLSearchParams({
            item_id: activeCreateSerialRow.getAttribute("data-item-id") || "",
            shop_id: createSupplyShopId,
            q: serial,
          });
          modalCreateSerials().forEach((s) => params.append("exclude", s));
          const response = await fetch(
            `${createSerialSearchUrl}?${params.toString()}`,
            {
              headers: { Accept: "application/json" },
              credentials: "same-origin",
            }
          );
          const data = await response.json().catch(() => ({}));
          const results = Array.isArray(data.results) ? data.results : [];
          const exact = results.find(
            (row) => normalizeCreateSerial(row) === serial
          );
          const picked = exact || (results.length === 1 ? results[0] : "");
          if (!picked) {
            if (results.length > 1) {
              createSerialEntry.value = serial;
              renderCreateSerialSuggest(results);
            }
            return false;
          }
          createModalScannedItem(normalizeCreateSerial(picked));
          ok = true;
        }
        if (ok) {
          lastCreateSerialCommit = serial;
          lastCreateSerialCommitAt = Date.now();
          // Refresh the pick list so remaining serials stay one tap away.
          runCreateSerialSearch();
        }
        return ok;
      } finally {
        createSerialCommitBusy = false;
        createSerialEntry.value = "";
        createSerialEntry.focus?.();
        dispatchCreateSerialSettled({ ok, serial });
      }
    };

    const openCreateSerialModal = (row) => {
      if (!createSerialModal || !row || !rowTracksSerial(row)) return;
      const avail = Number(row.getAttribute("data-avail") || 0) || 0;
      if (avail <= 0) return;
      activeCreateSerialRow = row;
      const itemName =
        row.querySelector(".stock-create-item-name")?.textContent?.trim() ||
        "Item";
      if (createSerialTitle) createSerialTitle.textContent = itemName;
      if (createSerialShop) {
        createSerialShop.textContent = "Select serials to transfer";
      }
      if (createSerialScanned) createSerialScanned.innerHTML = "";
      if (createSerialEntry) createSerialEntry.value = "";
      hideCreateSerialSuggest();
      lastCreateSerialCommit = "";
      lastCreateSerialCommitAt = 0;
      rowSerials(row).forEach((serial) => createModalScannedItem(serial));
      syncCreateSerialModalCount();
      window.MyShopSerialScan?.enhance?.(createSerialModal);
      createSerialModal.hidden = false;
      createSerialModal.setAttribute("aria-hidden", "false");
      document.body.classList.add("workspace-modal-open");
      createSerialEntry?.focus({ preventScroll: true });
      // Pre-load available serials so the user can tap them without typing.
      runCreateSerialSearch();
    };

    const closeCreateSerialModal = ({ save = false } = {}) => {
      const row = activeCreateSerialRow;
      if (save && row) {
        const pending = String(createSerialEntry?.value || "").trim();
        if (pending) {
          commitCreateSerialEntry().then((ok) => {
            if (ok) closeCreateSerialModal({ save: true });
          });
          return;
        }
        applyRowSerials(row, modalCreateSerials());
        syncRowEnabled(row);
        syncSelectedCount();
        captureSelectedQtys();
      }
      activeCreateSerialRow = null;
      if (createSerialModal) {
        createSerialModal.hidden = true;
        createSerialModal.setAttribute("aria-hidden", "true");
      }
      // Keep body lock while the transfer create modal is still open.
      if (createModal?.hidden !== false) {
        document.body.classList.remove("workspace-modal-open");
      } else {
        document.body.classList.add("workspace-modal-open");
      }
      if (row) {
        row.querySelector("[data-stock-create-qty]")?.focus?.();
      }
    };

    const bindRowQty = (row) => {
      const qty = row.querySelector("[data-stock-create-qty]");
      if (!qty) return;
      const tracksSerial = rowTracksSerial(row);
      const focusQty = () => {
        if (qty.disabled) return;
        if (tracksSerial) openCreateSerialModal(row);
        else qty.focus();
      };
      row.addEventListener("click", (event) => {
        if (event.target === qty) return;
        if (event.target.closest?.("[data-stock-create-serial-open]")) return;
        focusQty();
      });
      if (tracksSerial) {
        // Serial-tracked items go through the serial picker — the quantity
        // input is readonly and just reflects how many serials are picked.
        // Clicking it (or pressing Enter/Space) opens the picker.
        qty.addEventListener("click", (event) => {
          event.preventDefault();
          if (!qty.disabled) openCreateSerialModal(row);
        });
        qty.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (!qty.disabled) openCreateSerialModal(row);
          }
        });
      }
      const onQtyChanged = () => {
        syncRowEnabled(row);
        syncSelectedCount();
        captureSelectedQtys();
      };
      qty.addEventListener("input", onQtyChanged);
      qty.addEventListener("change", onQtyChanged);
      syncRowEnabled(row);
    };

    const syncSelectedCount = () => {
      const count = [...selectedItems.values()].filter(
        (item) => Number(item.qty) > 0
      ).length;
      if (selectedCount) selectedCount.textContent = String(count);
      if (selectedBadge) selectedBadge.hidden = count < 1;
    };

    const setEmptyStates = ({ loading = false, query = "", itemCount = 0, inStockCount = 0 } = {}) => {
      if (itemsHead) itemsHead.hidden = itemCount < 1;
      if (noResults) {
        noResults.hidden = loading || !query || itemCount > 0;
        noResults.textContent = query
          ? `No items match “${query}”.`
          : "No items match your search.";
      }
      if (noStockMsg) {
        noStockMsg.hidden =
          loading ||
          Boolean(query) ||
          itemCount > 0 ||
          showEmptyToggle?.checked ||
          inStockCount > 0;
      }
    };

    const setAvailDisplay = (row, value, { loading = false } = {}) => {
      const el = row.querySelector("[data-stock-create-avail]");
      const text = row.querySelector("[data-stock-create-avail-text]");
      if (!el) return;
      el.classList.remove("is-empty", "is-low", "is-ok", "is-loading");
      if (loading) {
        row.setAttribute("data-avail", "");
        el.textContent = "…";
        el.classList.add("is-loading");
        if (text) text.textContent = "Checking stock…";
        return;
      }
      const qty = Number(value) || 0;
      const tracksSerial = rowTracksSerial(row);
      row.setAttribute("data-avail", String(qty));
      el.textContent = String(qty);
      if (qty <= 0) {
        el.classList.add("is-empty");
        if (text) text.textContent = "Out of stock here — cannot send";
      } else if (tracksSerial) {
        el.classList.add(qty <= 3 ? "is-low" : "is-ok");
        if (text) {
          text.textContent =
            qty <= 3
              ? `Only ${qty} left — pick serials to send`
              : `${qty} available — pick serials to send`;
        }
      } else if (qty <= 3) {
        el.classList.add("is-low");
        if (text) text.textContent = `Only ${qty} left — enter amount to send`;
      } else {
        el.classList.add("is-ok");
        if (text) text.textContent = `${qty} available — enter amount to send`;
      }
    };

    const buildItemRows = (items, stocks) => {
      if (!itemsRoot) return;
      captureSelectedQtys();
      const list = Array.isArray(items) ? items : [];
      const map = stocks && typeof stocks === "object" ? stocks : {};
      const seen = new Set();
      const merged = [];

      selectedItems.forEach((saved, id) => {
        if (Number(saved.qty) > 0) {
          merged.push({
            id,
            name: saved.name,
            avail: Number(map[id] ?? saved.avail ?? 0) || 0,
            qty: saved.qty,
            trackSerial: Boolean(saved.trackSerial),
            serials: Array.isArray(saved.serials) ? saved.serials : [],
            pinned: true,
          });
          seen.add(String(id));
        }
      });

      list.forEach((item) => {
        const id = String(item?.id ?? "").trim();
        const name = String(item?.name || "").trim();
        if (!id || !name || seen.has(id)) return;
        seen.add(id);
        const avail = Number(map[id] ?? map[String(id)] ?? 0) || 0;
        const saved = selectedItems.get(id);
        const trackSerial = Boolean(
          saved?.trackSerial ?? item?.track_serial ?? item?.track_serial_number
        );
        merged.push({
          id,
          name,
          avail,
          qty: saved?.qty || "",
          trackSerial,
          serials: Array.isArray(saved?.serials) ? saved.serials : [],
          pinned: false,
        });
      });

      const frag = document.createDocumentFragment();
      merged.forEach((item) => {
        const row = document.createElement("label");
        row.className = "stock-create-item-row";
        if (item.pinned) row.classList.add("is-pinned");
        if (item.trackSerial) row.classList.add("is-serial");
        row.setAttribute("data-stock-create-row", "");
        row.setAttribute("data-item-id", item.id);
        row.setAttribute("data-item-name", item.name.toLowerCase());
        row.setAttribute("data-avail", String(item.avail));
        row.setAttribute("data-track-serial", item.trackSerial ? "1" : "0");
        const serialValue = (item.serials || []).join("\n");
        const qtyValue = item.trackSerial
          ? item.serials?.length
            ? String(item.serials.length)
            : item.qty || ""
          : item.qty || "";
        const qtyControl = item.trackSerial
          ? `<input
            type="text"
            class="stock-create-qty stock-create-qty--serial"
            name="quantity"
            value="${escapeHtml(qtyValue)}"
            placeholder="Pick"
            readonly
            data-stock-create-qty
            data-stock-create-serial-open
            aria-label="Serial numbers to send for ${escapeHtml(item.name)}"
            title="Click to pick the serial numbers to send"
          >`
          : `<input
            type="text"
            class="stock-create-qty"
            name="quantity"
            min="0.001"
            step="any"
            inputmode="decimal"
            placeholder="0"
            value="${escapeHtml(qtyValue)}"
            data-stock-create-qty
            aria-label="Quantity to send for ${escapeHtml(item.name)}"
          >`;
        row.innerHTML = `
          <span class="stock-create-item-main">
            <span class="stock-create-item-name">${escapeHtml(item.name)}</span>
            ${
              item.trackSerial
                ? '<span class="stock-create-serial-tag">Serial</span>'
                : ""
            }
            <span class="stock-create-item-avail-text" data-stock-create-avail-text></span>
            <input type="hidden" name="item_id" value="${escapeHtml(item.id)}" disabled data-stock-create-id>
            <input type="hidden" name="serial_numbers" value="${escapeHtml(serialValue)}" disabled data-stock-create-serials>
          </span>
          <span
            class="stock-create-item-stock"
            data-stock-create-avail
            title="On-hand at this shop"
          >—</span>
          ${qtyControl}
        `;
        frag.appendChild(row);
        setAvailDisplay(row, item.avail);
        const qty = row.querySelector("[data-stock-create-qty]");
        if (qty && !item.trackSerial) {
          if (item.avail > 0) qty.setAttribute("max", String(item.avail));
          else qty.removeAttribute("max");
        }
      });
      itemsRoot.innerHTML = "";
      if (!frag.childNodes.length) {
        itemsRoot.innerHTML =
          '<p class="stock-create-empty">No items to show.</p>';
        rows = [];
        syncSelectedCount();
        return;
      }
      itemsRoot.appendChild(frag);
      rows = [...itemsRoot.querySelectorAll("[data-stock-create-row]")];
      rows.forEach((row) => {
        bindRowQty(row);
        syncRowEnabled(row);
      });
      syncSelectedCount();
    };

    createSerialModal?.addEventListener("click", (event) => {
      if (event.target.closest("[data-stock-create-serial-close]")) {
        event.preventDefault();
        closeCreateSerialModal({ save: false });
        return;
      }
      if (event.target.closest("[data-stock-create-serial-done]")) {
        event.preventDefault();
        closeCreateSerialModal({ save: true });
        return;
      }
      const remove = event.target.closest("[data-stock-create-serial-remove]");
      if (remove) {
        event.preventDefault();
        remove.closest("li")?.remove();
        syncCreateSerialModalCount();
      }
    });

    createSerialEntry?.addEventListener("input", (event) => {
      const raw = String(event.target.value || "");
      if (/[\r\n]/.test(raw)) {
        event.target.value = raw.replace(/[\r\n]+/g, "").trim().toUpperCase();
        commitCreateSerialEntry();
        return;
      }
      event.target.value = raw.toUpperCase();
      event.target.classList.remove("is-duplicate");
      window.clearTimeout(createSerialSearchTimer);
      createSerialSearchTimer = window.setTimeout(runCreateSerialSearch, 220);
    });

    createSerialEntry?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        commitCreateSerialEntry();
      } else if (event.key === "Escape") {
        event.preventDefault();
        closeCreateSerialModal({ save: false });
      }
    });

    createSerialModal?.addEventListener("myshop:serial-applied", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.matches("[data-stock-create-serial-entry]")) return;
      const serial = normalizeCreateSerial(
        event.detail?.serial || target.value || ""
      );
      commitCreateSerialEntry(serial ? { serial } : {});
    });

    const loadItems = async ({ focusSearch = false } = {}) => {
      if (!fromStockUrl) {
        if (itemsRoot) {
          itemsRoot.innerHTML =
            '<p class="stock-create-empty">Item search is unavailable right now.</p>';
        }
        setEmptyStates({ loading: false, itemCount: 0 });
        return;
      }
      const toId = String(fromSelect?.value || "").trim();
      const query = String(searchInput?.value || "").trim();
      const includeEmpty = Boolean(showEmptyToggle?.checked);
      const seq = ++fromStockSeq;
      if (itemsHint) {
        itemsHint.textContent = query
          ? `Searching for “${query}”…`
          : "Loading items you can send…";
      }
      if (itemsRoot) {
        itemsRoot.innerHTML =
          '<p class="stock-create-empty" data-stock-create-loading>Loading items…</p>';
      }
      setEmptyStates({ loading: true, query });
      try {
        const params = new URLSearchParams();
        if (toId) params.set("from_shop_id", toId);
        if (query) params.set("q", query);
        if (includeEmpty) params.set("include_empty", "1");
        const url = `${fromStockUrl}${params.toString() ? `?${params}` : ""}`;
        const response = await fetch(url, {
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          credentials: "same-origin",
        });
        if (seq !== fromStockSeq) return;
        if (!response.ok) {
          if (itemsRoot) {
            itemsRoot.innerHTML =
              '<p class="stock-create-empty">Could not load items. Try again.</p>';
          }
          setEmptyStates({ loading: false, query, itemCount: 0 });
          return;
        }
        const data = await response.json();
        if (seq !== fromStockSeq) return;
        if (!data?.ok) {
          if (itemsRoot) {
            itemsRoot.innerHTML = `<p class="stock-create-empty">${escapeHtml(
              data.error || "Could not load items."
            )}</p>`;
          }
          setEmptyStates({ loading: false, query, itemCount: 0 });
          return;
        }
        const items = Array.isArray(data.items) ? data.items : [];
        const inStockCount = Number(data.in_stock_count || 0) || 0;
        // Keep the out-of-stock toggle in sync when the API auto-includes empties.
        if (showEmptyToggle && data.include_empty && !showEmptyToggle.checked) {
          showEmptyToggle.checked = true;
        }
        buildItemRows(items, data.stocks || {});
        const shopName = data.from_shop_name || "";
        const visibleCount = rows.length;
        if (itemsHint) {
          if (query) {
            itemsHint.textContent = items.length
              ? `Matches for “${query}”. Enter qty to send — click serial items to pick serials.`
              : selectedItems.size
                ? `No new matches for “${query}”. Your selected items stay listed above.`
                : `No matches for “${query}”.`;
          } else if (visibleCount) {
            itemsHint.textContent = includeEmpty
              ? `Showing all items at ${shopName || "this shop"} (including out of stock). Serial-tracked items: click to pick the serial numbers to send.`
              : `In-stock items at ${shopName || "this shop"}. Serial-tracked items: click to pick the serial numbers to send.`;
          } else {
            itemsHint.textContent =
              "Search an item name to load matches, then enter qty or select serials to send.";
          }
        }
        setEmptyStates({
          loading: false,
          query,
          itemCount: visibleCount,
          inStockCount,
        });
        if (noResults) {
          noResults.hidden = !query || items.length > 0 || selectedItems.size > 0;
        }
        if (focusSearch) {
          window.setTimeout(() => searchInput?.focus(), 40);
        }
      } catch {
        if (seq !== fromStockSeq) return;
        if (itemsRoot) {
          itemsRoot.innerHTML =
            '<p class="stock-create-empty">Network error while loading items.</p>';
        }
        setEmptyStates({ loading: false, query, itemCount: 0 });
      }
    };

    const scheduleSearch = () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        loadItems();
      }, 220);
    };

    const openCreateAndLoadStock = () => {
      createControls?.open();
      loadItems({ focusSearch: true });
    };

    document.querySelectorAll('[data-modal-open="request-stock"]').forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        openCreateAndLoadStock();
      });
    });

    const params = new URLSearchParams(window.location.search);
    if ((params.get("modal") || "").trim() === "request-stock") {
      openCreateAndLoadStock();
      params.delete("modal");
      const query = params.toString();
      const next = `${window.location.pathname}${query ? `?${query}` : ""}${
        window.location.hash || ""
      }`;
      window.history.replaceState({}, "", next);
    }

    fromSelect?.addEventListener("change", () => {
      // Destination only; stock is always from the current shop.
    });

    showEmptyToggle?.addEventListener("change", () => {
      loadItems();
    });

    syncSelectedCount();
    searchInput?.addEventListener("input", scheduleSearch);
    searchInput?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        window.clearTimeout(searchTimer);
        loadItems();
        const firstQty = itemsRoot?.querySelector(
          "[data-stock-create-qty]:not(:disabled)"
        );
        firstQty?.focus();
      }
    });

    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const fromShop = form.querySelector("[data-stock-create-from]");
      const code = form.querySelector("[data-stock-create-code]");
      const submitBtn = form.querySelector("[data-stock-create-submit]");
      captureSelectedQtys();
      rows.forEach((row) => {
        const qty = row.querySelector("[data-stock-create-qty]");
        const idInput = row.querySelector("[data-stock-create-id]");
        const serialHidden = row.querySelector("[data-stock-create-serials]");
        const hasQty = Number(qty?.value || 0) > 0;
        if (qty) qty.disabled = !hasQty;
        if (idInput) idInput.disabled = !hasQty;
        if (serialHidden) serialHidden.disabled = !hasQty;
      });
      const activeRows = rows.filter((row) => {
        const qty = row.querySelector("[data-stock-create-qty]");
        return Number(qty?.value || 0) > 0 && !qty.disabled;
      });
      for (const row of activeRows) {
        if (!rowTracksSerial(row)) continue;
        const serials = rowSerials(row);
        const qty = Number(row.querySelector("[data-stock-create-qty]")?.value || 0);
        if (serials.length !== qty) {
          rows.forEach((r) => syncRowEnabled(r));
          window.alert(
            "Pick the serial numbers to send — serial-tracked items need serials, not just a quantity."
          );
          openCreateSerialModal(row);
          return;
        }
      }
      if (!fromShop?.value) {
        rows.forEach((row) => syncRowEnabled(row));
        window.alert("Choose which shop should receive the stock.");
        fromShop?.focus();
        return;
      }
      if (!activeRows.length) {
        rows.forEach((row) => syncRowEnabled(row));
        window.alert(
          "Search for an item and enter qty to send (or select serials for serial items)."
        );
        searchInput?.focus();
        return;
      }
      if (!String(code?.value || "").trim()) {
        rows.forEach((row) => syncRowEnabled(row));
        window.alert("Enter your 6-digit staff ID to send the transfer.");
        code?.focus();
        return;
      }

      if (submitBtn) submitBtn.disabled = true;
      try {
        const body = new FormData(form);
        body.set("ajax", "1");
        const response = await fetch(form.getAttribute("action") || window.location.href, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          credentials: "same-origin",
          body,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          const errorText = data.error || "Could not send this transfer.";
          if (
            data.code === "unauthorized" ||
            window.MyshopUnauthorized?.isUnauthorizedText?.(errorText)
          ) {
            window.MyshopUnauthorized?.show(errorText);
          } else {
            window.alert(errorText);
          }
          rows.forEach((row) => syncRowEnabled(row));
          if (submitBtn) submitBtn.disabled = false;
          return;
        }

        const goNext = () => {
          const next =
            data.next ||
            window.location.pathname ||
            window.location.href;
          try {
            const url = new URL(next, window.location.origin);
            url.searchParams.set("_r", String(Date.now()));
            window.location.replace(url.pathname + url.search + url.hash);
          } catch {
            window.location.href = next;
          }
        };

        if (data.receipt_text) {
          if (submitBtn) {
            const label = submitBtn.querySelector("span");
            if (label) label.textContent = "Printing delivery note…";
          }
          try {
            if (window.RichcomPrinter?.printReceipt) {
              await Promise.race([
                window.RichcomPrinter.printReceipt({
                  text: data.receipt_text,
                  channel: data.print_via || "",
                  qr: data.receipt_qr || null,
                  fontStyle: data.receipt_font || null,
                  ticket: data.receipt_ticket || null,
                  paperWidth: data.receipt_paper_width || "",
                }),
                new Promise((resolve) => window.setTimeout(resolve, 2500)),
              ]);
            } else {
              // Last-resort browser print when shop printer JS is unavailable.
              const win = window.open("", "_blank", "noopener,noreferrer,width=480,height=720");
              if (win) {
                const prepared = window.RichcomPrinter?.maskReceiptForPrint
                  ? window.RichcomPrinter.maskReceiptForPrint(
                      data.receipt_text,
                      data.receipt_ticket || null
                    )
                  : { text: data.receipt_text };
                const safe = String(prepared.text || "")
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
                win.document.write(
                  `<pre style="font:12px/1.35 monospace;white-space:pre-wrap;padding:12px">${safe}</pre>`
                );
                win.document.close();
                win.focus();
                win.print();
              }
            }
          } catch (_printError) {
            /* Transfer already succeeded; continue. */
          }
        }

        goNext();
      } catch (_error) {
        window.alert("Network error while sending transfer. Try again.");
        rows.forEach((row) => syncRowEnabled(row));
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }

  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || selectModal) return;
    if (createModal && !createModal.hidden) {
      createControls?.close();
      return;
    }
    if (requestModal && !requestModal.hidden) {
      requestControls?.close();
      return;
    }
    if (decisionModal && !decisionModal.hidden) {
      decisionControls?.close();
    }
  });

  const statusRoot =
    document.querySelector("[data-stock-request-status-url]") ||
    document.querySelector("[data-shop-id]");
  const statusUrl =
    statusRoot?.getAttribute("data-stock-request-status-url") || "";
  const shopIdForAlerts = statusRoot?.getAttribute("data-shop-id") || "";

  const TRANSFER_FLASH_KEY = "myshop:transfer-flash";

  const pushStockRequestToast = (text, tone = "warning", { duration, silent = false } = {}) => {
    if (!text) return;
    if (!silent) {
      window.MyShopNotifySound?.play?.(
        tone === "error" ? "error" : tone === "success" ? "success" : "warning"
      );
    }
    let host = document.querySelector("[data-workspace-toast]");
    if (!host) {
      host = document.createElement("div");
      host.className = "workspace-toast";
      host.setAttribute("role", "status");
      host.setAttribute("aria-live", "polite");
      host.setAttribute("data-workspace-toast", "");
      host.innerHTML = '<ul class="message-list workspace-messages"></ul>';
      document.querySelector(".workspace-frame")?.appendChild(host) ||
        document.body.appendChild(host);
    }
    host.classList.remove("is-hiding");
    const list = host.querySelector(".workspace-messages") || host;
    const item = document.createElement("li");
    const safeTone = tone === "error" ? "error" : tone === "success" ? "success" : "warning";
    item.className = `workspace-toast__item workspace-toast__item--${safeTone}`;
    if (safeTone === "success") {
      item.innerHTML = `<span class="workspace-toast__icon" aria-hidden="true">
          <svg class="workspace-toast__glyph" viewBox="0 0 52 52" focusable="false">
            <circle class="workspace-toast__ring" cx="26" cy="26" r="24" fill="none" />
            <path class="workspace-toast__mark workspace-toast__mark--check" fill="none" d="M14.5 27.2 l7.1 7.2 16.4-16.8" />
          </svg>
        </span>
        <span class="workspace-toast__text"></span>`;
    } else {
      item.innerHTML = `<span class="workspace-toast__text"></span>`;
    }
    item.querySelector(".workspace-toast__text").textContent = text;
    list.appendChild(item);
    const hideAfter = Number(duration) > 0 ? Number(duration) : safeTone === "success" ? 4500 : 4200;
    window.setTimeout(() => {
      host.classList.add("is-hiding");
      window.setTimeout(() => host.remove(), 220);
    }, hideAfter);
  };

  const stashTransferFlash = (text, tone = "success") => {
    if (!text) return;
    try {
      sessionStorage.setItem(
        TRANSFER_FLASH_KEY,
        JSON.stringify({ text, tone, at: Date.now() })
      );
    } catch (_error) {
      /* ignore quota / private mode */
    }
  };

  const consumeTransferFlash = () => {
    try {
      const raw = sessionStorage.getItem(TRANSFER_FLASH_KEY);
      if (!raw) return;
      sessionStorage.removeItem(TRANSFER_FLASH_KEY);
      const parsed = JSON.parse(raw);
      if (!parsed?.text) return;
      // Ignore stale flashes older than 2 minutes.
      if (parsed.at && Date.now() - Number(parsed.at) > 120000) return;
      pushStockRequestToast(parsed.text, parsed.tone || "success");
    } catch (_error) {
      try {
        sessionStorage.removeItem(TRANSFER_FLASH_KEY);
      } catch (_ignore) {
        /* ignore */
      }
    }
  };

  consumeTransferFlash();

  if (statusUrl && shopIdForAlerts) {
    const storageKey = `myshop:stock-req:${shopIdForAlerts}`;
    const decisionKey = `myshop:stock-req-dec:${shopIdForAlerts}`;
    const seededKey = `myshop:stock-req-seeded:${shopIdForAlerts}`;

    const readIdList = (key) => {
      try {
        const parsed = JSON.parse(sessionStorage.getItem(key) || "[]");
        return Array.isArray(parsed) ? parsed.map(String) : [];
      } catch {
        return [];
      }
    };

    const writeIdList = (key, ids) => {
      try {
        sessionStorage.setItem(key, JSON.stringify(ids));
      } catch {
        /* ignore quota */
      }
    };

    let knownIds = readIdList(storageKey);
    let knownDecisionIds = readIdList(decisionKey);
    let seeded = false;
    try {
      seeded = sessionStorage.getItem(seededKey) === "1";
    } catch {
      seeded = false;
    }

    const seedKnown = (pending) => {
      knownIds = pending.map((row) => String(row.id));
      writeIdList(storageKey, knownIds);
    };

    const seedKnownDecisions = (decisions) => {
      knownDecisionIds = decisions.map((row) => String(row.id));
      writeIdList(decisionKey, knownDecisionIds);
    };

    const markSeeded = () => {
      seeded = true;
      try {
        sessionStorage.setItem(seededKey, "1");
      } catch {
        /* ignore quota */
      }
    };

    const notifyBrowser = (title, body) => {
      if (!("Notification" in window)) return;
      if (Notification.permission === "granted") {
        try {
          new Notification(title, { body, tag: `stock-req-${shopIdForAlerts}` });
        } catch {
          /* ignore */
        }
      } else if (Notification.permission === "default") {
        Notification.requestPermission().catch(() => {});
      }
    };

    const updateTransferBadges = (count, { pulse = false } = {}) => {
      const n = Math.max(0, Number(count) || 0);
      const label =
        n === 1 ? "1 incoming stock transfer" : `${n} incoming stock transfers`;
      document
        .querySelectorAll(
          "[data-stock-transfer-nav-badge], [data-stock-transfer-bell-badge]"
        )
        .forEach((el) => {
          el.textContent = String(n);
          if (n < 1) el.setAttribute("hidden", "");
          else el.removeAttribute("hidden");
          el.setAttribute("aria-label", label);
        });
      const nav = document.querySelector("[data-stock-transfer-nav]");
      if (nav) {
        nav.classList.toggle("has-live-badge", n > 0);
      }
      const bell = document.querySelector("[data-stock-transfer-bell]");
      if (bell) {
        bell.classList.toggle("has-pending", n > 0);
        bell.setAttribute("aria-label", n > 0 ? label : "Stock transfers");
        if (pulse && n > 0) {
          bell.classList.remove("is-ringing");
          // Force reflow so the ring animation can replay.
          void bell.offsetWidth;
          bell.classList.add("is-ringing");
          window.setTimeout(() => bell.classList.remove("is-ringing"), 1200);
        }
      }
    };

    let pollTimer = null;
    const stopPolling = () => {
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const pollStockRequests = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(statusUrl, {
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          credentials: "same-origin",
        });
        if (response.status === 401 || response.status === 403) {
          stopPolling();
          try {
            const data = await response.clone().json();
            if (
              data?.login_url &&
              (data.error === "session_expired" ||
                data.error === "auth_required" ||
                String(data.error || "")
                  .toLowerCase()
                  .includes("session"))
            ) {
              window.location.replace(data.login_url);
            }
          } catch (_err) {
            /* ignore non-json */
          }
          return;
        }
        if (!response.ok) return;
        const data = await response.json();
        if (!data?.ok) return;
        const pending = Array.isArray(data.pending) ? data.pending : [];
        const decisions = Array.isArray(data.decisions) ? data.decisions : [];
        const pendingCount =
          typeof data.pending_count === "number"
            ? data.pending_count
            : pending.length;
        // Badge = incoming only (transfers this shop must confirm).
        const badgeCount =
          typeof data.badge_count === "number" ? data.badge_count : pendingCount;
        const newRows = pending.filter((row) => !knownIds.includes(String(row.id)));
        const newDecisions = decisions.filter(
          (row) => !knownDecisionIds.includes(String(row.id))
        );

        if (!seeded) {
          seedKnown(pending);
          seedKnownDecisions(decisions);
          markSeeded();
          updateTransferBadges(badgeCount);
          return;
        }

        updateTransferBadges(badgeCount, { pulse: newRows.length > 0 });

        if (newRows.length) {
          const sample = newRows[0];
          const fromName = sample?.from_shop || "another shop";
          const count = newRows.length;
          const message =
            count === 1
              ? `New stock transfer from ${fromName}.`
              : `${count} new stock transfers, including from ${fromName}.`;
          pushStockRequestToast(message);
          notifyBrowser("Stock request", message);
          seedKnown(pending);
          seedKnownDecisions(decisions);
          // Reload so incoming confirm modal HTML stays in sync.
          window.setTimeout(() => {
            window.location.reload();
          }, 900);
          return;
        }

        if (newDecisions.length) {
          const sample = newDecisions[0];
          const fromName = sample?.from_shop || "another shop";
          const allDeclined = newDecisions.every((row) => row.status === "declined");
          const allAccepted = newDecisions.every((row) => row.status === "fulfilled");
          let message;
          if (newDecisions.length === 1 && allDeclined) {
            message = `${fromName} declined your stock transfer.`;
          } else if (newDecisions.length === 1 && allAccepted) {
            message = `${fromName} confirmed your stock transfer.`;
          } else if (allDeclined) {
            message = `${newDecisions.length} stock transfers were declined.`;
          } else if (allAccepted) {
            message = `${newDecisions.length} stock transfers were confirmed.`;
          } else {
            message = `Updates on your stock transfers, including from ${fromName}.`;
          }
          pushStockRequestToast(message, allDeclined ? "error" : "success");
          notifyBrowser("Stock transfer update", message);
          seedKnown(pending);
          seedKnownDecisions(decisions);
          window.setTimeout(() => {
            window.location.reload();
          }, 900);
          return;
        }

        seedKnown(pending);
        seedKnownDecisions(decisions);
      } catch {
        /* ignore transient poll errors */
      }
    };

    pollTimer = window.setInterval(pollStockRequests, 12000);
    window.setTimeout(pollStockRequests, 1200);
  }

  const normalizeSerial = (value) => String(value || "").trim().toUpperCase();
  const serialSearchUrl = requestModal?.dataset.serialSearchUrl || "";
  const supplyShopId = requestModal?.dataset.supplyShopId || "";
  let serialSearchTimer = null;
  let serialSearchSeq = 0;

  const refreshIcons = () => {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const hideSerialSuggest = (root) => {
    const suggest = root?.querySelector("[data-serial-suggest]");
    if (!suggest) return;
    suggest.hidden = true;
    suggest.innerHTML = "";
  };

  const filledSerialsInBlock = (block) => {
    const values = [];
    block?.querySelectorAll("[data-serial-scanned-value]").forEach((el) => {
      const value = normalizeSerial(el.textContent);
      if (value) values.push(value);
    });
    return values;
  };

  const getQtyInput = (form, lineId) =>
    form.querySelector(`[data-transfer-qty][data-line-id="${lineId}"]`);

  const getTransferSerialEntry = (block) =>
    block?.querySelector("[data-serial-entry]");

  const getTransferSerialScanned = (block) =>
    block?.querySelector("[data-serial-scanned]");

  const syncSerialBlockQty = (form, block) => {
    const lineId = block.dataset.lineId;
    const max = Number(block.dataset.maxQty || block.dataset.qty || 0);
    const filled = filledSerialsInBlock(block);
    const qtyInput = getQtyInput(form, lineId);
    if (qtyInput?.dataset.qtyFromSerial === "1") {
      qtyInput.value = String(filled.length);
    }
    block.dataset.qty = String(filled.length);
    const need = block.querySelector("[data-serial-need]");
    if (need) need.textContent = `${filled.length} / ${max} selected`;
    const scanned = getTransferSerialScanned(block);
    if (scanned) scanned.hidden = filled.length === 0;
    return filled.length;
  };

  const createTransferScannedItem = (block, serial) => {
    const scanned = getTransferSerialScanned(block);
    if (!scanned || !serial) return null;
    const li = document.createElement("li");
    li.className = "stock-request-serial-scanned-item";
    li.dataset.serialValue = serial;

    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = `serials_${block.dataset.lineId}`;
    hidden.value = serial;
    li.appendChild(hidden);

    const value = document.createElement("span");
    value.className = "stock-serial-scanned-value";
    value.setAttribute("data-serial-scanned-value", "");
    value.textContent = serial;
    li.appendChild(value);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "stock-serial-scanned-remove";
    remove.setAttribute("data-serial-scanned-remove", "");
    remove.setAttribute("aria-label", `Remove ${serial}`);
    remove.innerHTML = '<i data-lucide="x" aria-hidden="true"></i>';
    li.appendChild(remove);

    scanned.prepend(li);
    refreshIcons();
    return li;
  };

  const transferCommitState = new WeakMap();

  const transferAllowedSerials = (block) =>
    [...(block?.querySelectorAll("[data-serial-allowed]") || [])]
      .map((el) => normalizeSerial(el.textContent || ""))
      .filter(Boolean);

  const commitTransferSerialEntry = async (block, form, { serial: override } = {}) => {
    const entry = getTransferSerialEntry(block);
    if (!entry || !form) return false;

    const state = transferCommitState.get(block) || {
      busy: false,
      lastSerial: "",
      lastAt: 0,
    };
    if (state.busy) return false;

    let serial = normalizeSerial(override || entry.value);
    if (!serial) return false;

    const max = Number(block.dataset.maxQty || 0);
    const filled = filledSerialsInBlock(block);
    const now = Date.now();
    if (serial === state.lastSerial && now - state.lastAt < 1000) {
      entry.value = "";
      entry.focus?.();
      return false;
    }
    if (filled.includes(serial)) {
      entry.value = "";
      entry.classList.add("is-duplicate");
      window.setTimeout(() => entry.classList.remove("is-duplicate"), 700);
      entry.focus?.();
      return false;
    }
    if (max > 0 && filled.length >= max) {
      entry.value = "";
      entry.focus?.();
      return false;
    }

    const root = entry.closest("[data-serial-search-root]");
    const firstOption = root?.querySelector(
      ".stock-supplier-suggest-option:not([disabled])"
    );
    if (firstOption) {
      const picked = normalizeSerial(firstOption.textContent);
      // In transfer-check mode an exact typed match wins over the
      // first suggestion — a partial scan must not jump to another serial.
      const exactAllowed =
        block.dataset.serialMode === "transfer" &&
        transferAllowedSerials(block).includes(serial);
      if (picked && !exactAllowed) serial = picked;
    }

    entry.value = "";
    hideSerialSuggest(root);

    state.busy = true;
    transferCommitState.set(block, state);
    let ok = false;
    try {
      if (block.dataset.serialMode === "transfer") {
        // Receiving a transfer: serial must be one of the serials recorded
        // on the transfer itself — do not search shop stock.
        const match = transferAllowedSerials(block).find((value) => value === serial);
        if (!match) {
          entry.classList.add("is-duplicate");
          window.setTimeout(() => entry.classList.remove("is-duplicate"), 700);
          pushStockRequestToast(
            `Serial ${serial} is not on this transfer.`,
            "error",
            { silent: true }
          );
          return false;
        }
        createTransferScannedItem(block, match);
        ok = true;
      } else if (!serialSearchUrl) {
        createTransferScannedItem(block, serial);
        ok = true;
      } else {
        const params = new URLSearchParams({
          item_id: block.dataset.itemId || "",
          shop_id: supplyShopId,
          q: serial,
        });
        filled.forEach((s) => params.append("exclude", s));
        const response = await fetch(`${serialSearchUrl}?${params.toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const data = await response.json().catch(() => ({}));
        const results = Array.isArray(data.results) ? data.results : [];
        const exact = results.find((row) => normalizeSerial(row) === serial);
        const picked = exact || (results.length === 1 ? results[0] : "");
        if (!picked) {
          if (results.length > 1) {
            entry.value = serial;
            renderSerialSuggest(entry, results);
          }
          return false;
        }
        createTransferScannedItem(block, normalizeSerial(picked));
        ok = true;
      }
      if (ok) {
        state.lastSerial = serial;
        state.lastAt = Date.now();
        syncSerialBlockQty(form, block);
        syncDecisionButtons(form);
      }
      return ok;
    } finally {
      state.busy = false;
      transferCommitState.set(block, state);
      entry.value = "";
      entry.focus?.();
      entry.dispatchEvent(
        new CustomEvent("myshop:serial-commit-settled", {
          bubbles: true,
          detail: { ok, serial },
        })
      );
    }
  };

  const initSerialPanels = (form) => {
    const panel = form.querySelector("[data-serial-panel]");
    const blocks = [...form.querySelectorAll("[data-serial-block]")];
    if (!panel) return;
    if (!blocks.length) {
      panel.setAttribute("hidden", "");
      return;
    }
    panel.removeAttribute("hidden");
    blocks.forEach((block) => {
      const scanned = getTransferSerialScanned(block);
      if (scanned) scanned.innerHTML = "";
      const entry = getTransferSerialEntry(block);
      if (entry) entry.value = "";
      transferCommitState.set(block, { busy: false, lastSerial: "", lastAt: 0 });
      syncSerialBlockQty(form, block);
    });
    syncDecisionButtons(form);
    window.MyShopSerialScan?.enhance?.(panel);
    refreshIcons();
  };

  const applySerialChoice = (input, serial) => {
    const block = input?.closest?.("[data-serial-block]");
    const form = block?.closest?.("[data-request-form]");
    if (!block || !form) return;
    commitTransferSerialEntry(block, form, { serial });
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
      empty.innerHTML =
        "<strong>No matching serials</strong><small>Available at this shop only</small>";
      suggest.appendChild(empty);
      suggest.hidden = false;
      return;
    }
    results.forEach((serial) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "stock-supplier-suggest-option";
      btn.innerHTML = "<strong></strong>";
      btn.querySelector("strong").textContent = serial;
      btn.addEventListener("mousedown", (event) => {
        event.preventDefault();
        applySerialChoice(input, serial);
      });
      suggest.appendChild(btn);
    });
    suggest.hidden = false;
  };

  const runSerialSearch = async (input) => {
    const block = input.closest("[data-serial-block]");
    if (!block || block.hidden) return;
    if (block.dataset.serialMode === "transfer") {
      const filled = new Set(filledSerialsInBlock(block));
      const query = normalizeSerial(input.value);
      const results = transferAllowedSerials(block)
        .filter((serial) => !filled.has(serial))
        .filter((serial) => !query || serial.includes(query))
        .slice(0, 10);
      renderSerialSuggest(input, results);
      return;
    }
    if (!serialSearchUrl || !supplyShopId) return;
    const itemId = block.dataset.itemId || "";
    if (!itemId) return;

    const query = normalizeSerial(input.value);
    const seq = ++serialSearchSeq;
    const params = new URLSearchParams({
      item_id: itemId,
      shop_id: supplyShopId,
      q: query,
    });
    filledSerialsInBlock(block).forEach((serial) =>
      params.append("exclude", serial)
    );

    try {
      const response = await fetch(`${serialSearchUrl}?${params.toString()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const data = await response.json();
      if (seq !== serialSearchSeq) return;
      renderSerialSuggest(input, data.results || []);
    } catch (_error) {
      /* ignore */
    }
  };

  const queueSerialSearch = (input) => {
    window.clearTimeout(serialSearchTimer);
    serialSearchTimer = window.setTimeout(() => runSerialSearch(input), 220);
  };

  const getCsrfToken = (form) =>
    form?.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const clampTransferQty = (input) => {
    if (input.dataset.qtyFromSerial === "1") {
      return Number(input.value || 0);
    }
    const row = input.closest("[data-request-line]");
    const max = Number(
      input.getAttribute("data-must-match") ||
        input.max ||
        row?.dataset.available ||
        row?.dataset.requested ||
        0
    );
    const raw = String(input.value || "").trim();
    if (raw === "") {
      input.value = "";
      return NaN;
    }
    let value = Number(raw);
    if (!Number.isFinite(value) || value < 0) value = 0;
    value = Math.round(value * 1000) / 1000;
    if (max > 0 && value > max) value = max;
    input.value = String(value);
    return value;
  };

  const quantitiesMatch = (form) => {
    const qtyInputs = [...form.querySelectorAll("[data-transfer-qty]")];
    if (!qtyInputs.length) return false;
    return qtyInputs.every((input) => {
      const received = Number(input.value);
      const mustMatch = Number(
        input.getAttribute("data-must-match") ||
          input.getAttribute("data-requested") ||
          0
      );
      return (
        String(input.value).trim() !== "" &&
        Number.isFinite(received) &&
        Number.isFinite(mustMatch) &&
        received === mustMatch
      );
    });
  };

  const syncDecisionButtons = (form) => {
    const verified = form.dataset.codeVerified === "1";
    const matched = quantitiesMatch(form);
    const declineBtn = form.querySelector('[data-decision-submit="decline"]');
    const acceptBtn = form.querySelector('[data-decision-submit="accept"]');
    if (declineBtn) declineBtn.disabled = !verified;
    if (acceptBtn) acceptBtn.disabled = !(verified && matched);
    form.dataset.qtyMatched = matched ? "1" : "0";
  };

  const setVerified = (form, verified, message = "") => {
    form.dataset.codeVerified = verified ? "1" : "0";
    const actions = form.querySelector("[data-decision-actions]");
    const status = form.querySelector("[data-verify-status]");
    if (actions) actions.removeAttribute("hidden");
    syncDecisionButtons(form);
    if (status) {
      const matched = quantitiesMatch(form);
      let text = message;
      if (!text) {
        text = verified
          ? matched
            ? "Staff verified. Quantities match — you can Confirm or Decline."
            : "Staff verified. Enter received quantities that match sent to Confirm, or Decline."
          : "Enter quantities received, then your staff ID. Confirm only when received matches sent.";
      } else if (verified) {
        text = matched
          ? `${message} Quantities match — you can Confirm or Decline.`
          : `${message} Enter received quantities that match sent to Confirm, or Decline.`;
      }
      status.textContent = text;
      status.classList.toggle("is-ok", verified && matched);
      status.classList.toggle("is-error", Boolean(message) && !verified);
    }
  };

  let employeeVerifyTimer = null;
  let employeeVerifySeq = 0;

  const verifyLoginCode = async (form, { focusOnShort = false } = {}) => {
    const codeInput = form.querySelector("[data-login-code]");
    const code = (codeInput?.value || "").trim();
    const verifyUrl =
      requestModal?.dataset.verifyLoginUrl ||
      form.dataset.verifyLoginUrl ||
      "";
    const seq = ++employeeVerifySeq;

    if (code.length < 6) {
      setVerified(
        form,
        false,
        code.length
          ? `Enter ${6 - code.length} more digit${6 - code.length === 1 ? "" : "s"}.`
          : ""
      );
      if (focusOnShort) codeInput?.focus();
      return false;
    }

    if (!/^\d{6}$/.test(code)) {
      setVerified(form, false, "Employee ID must be exactly 6 digits.");
      return false;
    }
    if (!verifyUrl) {
      setVerified(form, false, "Verification is unavailable. Refresh and try again.");
      return false;
    }

    try {
      const { verifyStaffLoginCode } = await import("./offline/staff.js");
      const data = await verifyStaffLoginCode({
        url: verifyUrl,
        code,
        csrfToken: getCsrfToken(form),
      });
      if (seq !== employeeVerifySeq) return false;
      if (!data.ok) {
        setVerified(form, false, data.error || "Not a valid active staff ID.");
        return false;
      }
      const label = data.message
        ? data.message
        : data.name
          ? `Verified: ${data.name} (${data.employee_id}).`
          : `Verified staff ${data.employee_id}.`;
      setVerified(form, true, label);
      return true;
    } catch (_error) {
      if (seq !== employeeVerifySeq) return false;
      setVerified(form, false, "Could not verify staff ID. Check your connection.");
      return false;
    }
  };

  const queueEmployeeVerify = (form) => {
    window.clearTimeout(employeeVerifyTimer);
    employeeVerifyTimer = window.setTimeout(() => {
      verifyLoginCode(form);
    }, 180);
  };

  const validateForm = (form, decision) => {
    const code = (form.querySelector("[data-login-code]")?.value || "").trim();
    if (form.dataset.codeVerified !== "1") {
      window.alert("Enter a valid active staff 6-digit ID first.");
      form.querySelector("[data-login-code]")?.focus();
      return false;
    }
    if (!decision || !["accept", "decline"].includes(decision)) {
      window.alert("Choose Accept or Decline.");
      return false;
    }
    if (!/^\d{6}$/.test(code)) {
      window.alert("Enter a valid active staff 6-digit ID.");
      form.querySelector("[data-login-code]")?.focus();
      return false;
    }

    if (decision === "decline") {
      const reasonPanel = form.querySelector("[data-decline-reason-panel]");
      const reasonInput = form.querySelector("[data-decline-reason]");
      if (reasonPanel) reasonPanel.hidden = false;
      const reason = (reasonInput?.value || "").trim();
      if (reason.length < 3) {
        window.alert(
          "Enter a reason for declining (at least 3 characters), e.g. Wrong quantity."
        );
        reasonInput?.focus();
        return false;
      }
      const shopName =
        form.getAttribute("data-requesting-shop") || "the sending shop";
      if (
        !window.confirm(
          `Decline this stock transfer from ${shopName}?\nReason: ${reason}\n\nThey will be notified and no stock will move.`
        )
      ) {
        return false;
      }
      return true;
    }

    if (decision === "accept") {
      const qtyInputs = [...form.querySelectorAll("[data-transfer-qty]")];
      if (!qtyInputs.length) {
        window.alert("This transfer has no items.");
        return false;
      }
      for (const input of qtyInputs) {
        const qty = Number(input.value);
        const mustMatch = Number(
          input.getAttribute("data-must-match") ||
            input.getAttribute("data-requested") ||
            0
        );
        if (input.value === "" || !Number.isFinite(qty) || qty !== mustMatch) {
          const row = input.closest("[data-request-line]");
          const itemName = (
            row?.querySelector(".stock-transfer-item span")?.textContent || ""
          ).trim();
          window.alert(
            `${itemName ? `“${itemName}”: ` : ""}received quantity does not match this transfer. Decline with a reason if the count is wrong.`
          );
          input.focus();
          return false;
        }
      }
    }
    return true;
  };

  document.querySelectorAll("[data-request-form]").forEach((form) => {
    initSerialPanels(form);
    setVerified(form, false);

    form.querySelectorAll("[data-transfer-qty]").forEach((input) => {
      if (input.dataset.qtyFromSerial === "1") return;
      const onQtyChange = () => {
        if (input.value !== "") clampTransferQty(input);
        syncDecisionButtons(form);
        if (form.dataset.codeVerified === "1") {
          setVerified(form, true);
        }
      };
      input.addEventListener("input", onQtyChange);
      input.addEventListener("change", onQtyChange);
    });

    const loginCodeInput = form.querySelector("[data-login-code]");
    loginCodeInput?.addEventListener("input", (event) => {
      const input = event.target;
      input.value = String(input.value || "").replace(/\D/g, "").slice(0, 6);
      setVerified(form, false);
      queueEmployeeVerify(form);
    });
    loginCodeInput?.addEventListener("blur", () => {
      if ((loginCodeInput.value || "").trim().length === 6) {
        verifyLoginCode(form);
      }
    });

    form.querySelectorAll("[data-decline-preset]").forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        const reasonInput = form.querySelector("[data-decline-reason]");
        const reasonPanel = form.querySelector("[data-decline-reason-panel]");
        if (reasonPanel) reasonPanel.hidden = false;
        if (reasonInput) {
          reasonInput.value = btn.getAttribute("data-decline-preset") || "";
          reasonInput.focus();
        }
      });
    });

    form
      .querySelector('[data-decision-submit="decline"]')
      ?.addEventListener("click", (event) => {
        event.preventDefault();
        if (form.dataset.codeVerified !== "1") {
          window.alert("Enter a valid active staff 6-digit ID first.");
          form.querySelector("[data-login-code]")?.focus();
          return;
        }
        const reasonPanel = form.querySelector("[data-decline-reason-panel]");
        const reasonInput = form.querySelector("[data-decline-reason]");
        if (reasonPanel?.hidden) {
          reasonPanel.hidden = false;
          if (window.lucide?.createIcons) window.lucide.createIcons();
          reasonInput?.focus();
          const status = form.querySelector("[data-verify-status]");
          if (status) {
            status.textContent =
              "Enter a decline reason (e.g. Wrong quantity), then press Decline again.";
            status.classList.remove("is-ok");
          }
          return;
        }
        form.dataset.pendingDecision = "decline";
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit();
        } else {
          form.dispatchEvent(
            new Event("submit", { cancelable: true, bubbles: true })
          );
        }
      });

    form.addEventListener("submit", async (event) => {
      const submitter = event.submitter;
      let decision = (
        submitter?.getAttribute?.("data-decision-submit") ||
        submitter?.value ||
        ""
      ).trim();
      // Decline uses a type=button, then submits with pendingDecision.
      if (!decision && form.dataset.pendingDecision) {
        decision = form.dataset.pendingDecision;
      }
      if (form.dataset.pendingDecision) {
        delete form.dataset.pendingDecision;
      }
      if (!validateForm(form, decision)) {
        event.preventDefault();
        return;
      }

      // Accept: move stock then print goods-received receipt.
      event.preventDefault();
      const buttons = form.querySelectorAll("[data-decision-submit]");
      buttons.forEach((btn) => {
        btn.disabled = true;
      });
      const status = form.querySelector("[data-verify-status]");
      if (status) {
        status.textContent =
          decision === "accept"
            ? "Confirming receipt and printing goods received slip…"
            : "Declining request…";
        status.classList.add("is-ok");
        status.classList.remove("is-error");
      }

      try {
        const body = new FormData(form);
        body.set("decision", decision);
        body.set("ajax", "1");
        const response = await fetch(form.getAttribute("action") || window.location.href, {
          method: "POST",
          headers: { Accept: "application/json" },
          credentials: "same-origin",
          body,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          const errorText = data.error || "Could not respond to this request.";
          if (
            data.code === "unauthorized" ||
            window.MyshopUnauthorized?.isUnauthorizedText?.(errorText)
          ) {
            window.MyshopUnauthorized?.show(errorText);
          }
          if (status) {
            status.textContent = errorText;
            status.classList.add("is-error");
            status.classList.remove("is-ok");
          } else {
            window.alert(errorText);
          }
          buttons.forEach((btn) => {
            btn.disabled = true;
          });
          syncDecisionButtons(form);
          return;
        }

        const flashText =
          data.message ||
          (decision === "accept"
            ? "Stock updated. Transfer confirmed."
            : "Transfer declined. No stock was moved.");
        const flashTone = decision === "accept" ? "success" : "warning";
        if (status) {
          status.textContent = `${flashText} Refreshing…`;
          status.classList.add("is-ok");
          status.classList.remove("is-error");
        }
        pushStockRequestToast(flashText, flashTone);
        stashTransferFlash(flashText, flashTone);

        const goNext = () => {
          const next = data.next || window.location.href;
          try {
            const url = new URL(next, window.location.origin);
            // Bust cache so pending transfer UI and stock counts refresh.
            url.searchParams.set("_r", String(Date.now()));
            window.location.replace(url.pathname + url.search + url.hash);
          } catch {
            window.location.href = next;
          }
        };

        const printThenRefresh = async () => {
          if (
            decision === "accept" &&
            data.receipt_text &&
            window.RichcomPrinter?.printReceipt
          ) {
            if (status) {
              status.textContent = "Printing goods received receipt…";
            }
            try {
              await Promise.race([
                window.RichcomPrinter.printReceipt({
                  text: data.receipt_text,
                  channel: data.print_via || "",
                  qr: data.receipt_qr || null,
                  fontStyle: data.receipt_font || null,
                  ticket: data.receipt_ticket || null,
                  paperWidth: data.receipt_paper_width || "",
                }),
                new Promise((resolve) => window.setTimeout(resolve, 1800)),
              ]);
            } catch (_printError) {
              /* Transfer already succeeded; continue to refresh. */
            }
          }
          // Brief pause so the stock-updated popup is visible, then refresh.
          await new Promise((resolve) => window.setTimeout(resolve, 450));
          goNext();
        };

        await printThenRefresh();
      } catch (_error) {
        if (status) {
          status.textContent = "Network error while responding. Try again.";
          status.classList.add("is-error");
          status.classList.remove("is-ok");
        } else {
          window.alert("Network error while responding. Try again.");
        }
        syncDecisionButtons(form);
      }
    });
  });

  if (requestModal) {
    requestModal.addEventListener("click", (event) => {
      const remove = event.target.closest?.("[data-serial-scanned-remove]");
      if (!remove) return;
      const block = remove.closest("[data-serial-block]");
      const form = block?.closest("[data-request-form]");
      remove.closest("li")?.remove();
      if (block && form) {
        syncSerialBlockQty(form, block);
        syncDecisionButtons(form);
      }
      getTransferSerialEntry(block)?.focus?.();
    });
    requestModal.addEventListener("input", (event) => {
      const input = event.target;
      if (!input.matches?.("[data-serial-entry], [data-serial-input]")) return;
      const block = input.closest("[data-serial-block]");
      const form = input.closest("[data-request-form]");
      if (!block || !form) return;
      const raw = String(input.value || "");
      if (/[\r\n]/.test(raw)) {
        const serial = normalizeSerial(raw.replace(/[\r\n]+/g, ""));
        input.value = serial;
        commitTransferSerialEntry(block, form, serial ? { serial } : {});
        return;
      }
      input.value = raw.toUpperCase();
      input.classList.remove("is-duplicate");
      queueSerialSearch(input);
    });
    requestModal.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      const input = event.target;
      if (!input.matches?.("[data-serial-entry], [data-serial-input]")) return;
      event.preventDefault();
      const block = input.closest("[data-serial-block]");
      const form = input.closest("[data-request-form]");
      if (block && form) commitTransferSerialEntry(block, form);
    });
    requestModal.addEventListener("myshop:serial-applied", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.matches("[data-serial-entry], [data-serial-input]")) return;
      const block = target.closest("[data-serial-block]");
      const form = block?.closest("[data-request-form]");
      if (!block || !form) return;
      const serial = normalizeSerial(event.detail?.serial || target.value);
      commitTransferSerialEntry(block, form, serial ? { serial } : {});
    });
    requestModal.addEventListener("focusin", (event) => {
      const input = event.target;
      if (!input.matches?.("[data-serial-entry], [data-serial-input]")) return;
      queueSerialSearch(input);
    });
    requestModal.addEventListener("focusout", (event) => {
      const root = event.target.closest?.("[data-serial-search-root]");
      if (!root) return;
      window.setTimeout(() => hideSerialSuggest(root), 180);
    });
  }


  /* —— Shop floor cart —— */
  const cartRoot = document.querySelector("[data-shop-cart]");
  if (cartRoot) {
    const shopId = cartRoot.getAttribute("data-shop-id") || "0";
    const storageKey = `myshop-cart:${shopId}`;
    const fab = document.querySelector("[data-cart-open]");
    const countEl = document.querySelector("[data-cart-count]");
    const overlay = document.querySelector("[data-cart-overlay]");
    const drawer = document.querySelector("[data-cart-drawer]");
    const linesEl = document.querySelector("[data-cart-lines]");
    const footEl = document.querySelector("[data-cart-foot]");
    const itemLabelEl = document.querySelector("[data-cart-item-label]");
    const cartNotice = drawer?.querySelector("[data-cart-notice]");
    const cartNoticeText = drawer?.querySelector("[data-cart-notice-text]");
    const totalEl = document.querySelector("[data-cart-total]");
    const subtotalEl = document.querySelector("[data-cart-subtotal]");
    const taxEl = document.querySelector("[data-cart-tax]");
    const taxLabelEl = document.querySelector("[data-cart-tax-label]");
    const subtotalRow = document.querySelector("[data-cart-subtotal-row]");
    const taxRow = document.querySelector("[data-cart-tax-row]");
    const productModal = document.querySelector("[data-product-modal]");
    const productMedia = productModal?.querySelector("[data-product-media]");
    const productCategory = productModal?.querySelector("[data-product-category]");
    const productName = productModal?.querySelector("[data-product-name]");
    const productDescription = productModal?.querySelector("[data-product-description]");
    const productPriceInput = productModal?.querySelector("[data-product-price-input]");
    const productPriceHint = productModal?.querySelector("[data-product-price-hint]");
    const productStock = productModal?.querySelector("[data-product-stock]");
    const productAdd = productModal?.querySelector("[data-product-add]");
    const productQtyWrap = productModal?.querySelector("[data-product-qty-wrap]");
    const productQtyInput = productModal?.querySelector("[data-product-qty-input]");
    const serialSaleModal = document.querySelector("[data-serial-sale-modal]");
    const serialSaleMedia = serialSaleModal?.querySelector("[data-serial-sale-media]");
    const serialSaleCategory = serialSaleModal?.querySelector("[data-serial-sale-category]");
    const serialSaleName = serialSaleModal?.querySelector("[data-serial-sale-name]");
    const serialSaleDescription = serialSaleModal?.querySelector(
      "[data-serial-sale-description]"
    );
    const serialSaleStock = serialSaleModal?.querySelector("[data-serial-sale-stock]");
    const serialSaleForm = serialSaleModal?.querySelector("[data-serial-sale-form]");
    const serialSaleEntry = serialSaleModal?.querySelector("[data-serial-sale-entry]");
    const serialSaleEntryWrap = serialSaleModal?.querySelector("[data-serial-sale-search-root]");
    const serialSaleScanned = serialSaleModal?.querySelector("[data-serial-sale-scanned]");
    const serialSaleCount = serialSaleModal?.querySelector("[data-serial-sale-count]");
    const serialSaleStatus = serialSaleModal?.querySelector("[data-serial-sale-status]");
    const serialSaleConfirm = serialSaleModal?.querySelector("[data-serial-sale-confirm]");
    const tradeModal = document.querySelector('[data-modal="trade"]');
    const tradeStatusEl = tradeModal?.querySelector("[data-trade-status]");
    const tradeClientNameEl = tradeModal?.querySelector("[data-trade-client-name]");
    const tradeClientPhoneEl = tradeModal?.querySelector("[data-trade-client-phone]");
    const checkoutForm = drawer?.querySelector("[data-cart-checkout]");
    const whatsappWrap = checkoutForm?.querySelector("[data-cart-whatsapp-wrap]");
    const paymentWrap = checkoutForm?.querySelector("[data-cart-payment-wrap]");
    const splitWrap = checkoutForm?.querySelector("[data-cart-split]");
    const cashInput = checkoutForm?.querySelector("[data-cart-cash]");
    const mpesaInput = checkoutForm?.querySelector("[data-cart-mpesa]");
    const cartStatus = checkoutForm?.querySelector("[data-cart-status]");
    const cartSubmit = checkoutForm?.querySelector("[data-cart-submit]");
    const cartLoginCode = checkoutForm?.querySelector("[data-cart-login-code]");
    const wrongShopModal = document.querySelector("[data-wrong-shop-modal]");
    const wrongShopMessage = wrongShopModal?.querySelector("[data-wrong-shop-message]");
    const wrongShopDetail = wrongShopModal?.querySelector("[data-wrong-shop-detail]");
    const shopName = cartRoot.getAttribute("data-shop-name") || "this shop";
    const staffWrap = checkoutForm?.querySelector("[data-cart-staff-wrap]");
    const staffLockHint = checkoutForm?.querySelector("[data-cart-staff-lock]");
    const stkPanel = checkoutForm?.querySelector("[data-cart-stk-panel]");
    const stkSendBtn = checkoutForm?.querySelector("[data-cart-stk-send]");
    const stkWaitEl = checkoutForm?.querySelector("[data-cart-stk-wait]");
    const stkWaitTextEl = checkoutForm?.querySelector("[data-cart-stk-wait-text]");
    const stkReceiptEl = checkoutForm?.querySelector("[data-cart-stk-receipt]");
    const stkReceiptCodeEl = checkoutForm?.querySelector(
      "[data-cart-stk-receipt-code]"
    );
    const clientBlock = checkoutForm?.querySelector("[data-cart-client]");
    const clientHeading = checkoutForm?.querySelector("[data-cart-client-heading]");
    const clientNote = checkoutForm?.querySelector("[data-cart-client-note]");
    const clientPhoneInput = checkoutForm?.querySelector("[data-cart-client-phone]");
    const clientNameInput = checkoutForm?.querySelector("[data-cart-client-name]");
    const clientPhoneLabel = checkoutForm?.querySelector("[data-cart-client-phone-label]");
    const clientNameLabel = checkoutForm?.querySelector("[data-cart-client-name-label]");
    const clientHint = checkoutForm?.querySelector("[data-cart-client-hint]");
    const clientSuggest = checkoutForm?.querySelector("[data-cart-client-suggest]");
    const creditDueWrap = checkoutForm?.querySelector("[data-cart-credit-due-wrap]");
    const creditDueInput = checkoutForm?.querySelector("[data-cart-credit-due]");
    const checkoutUrl = cartRoot.getAttribute("data-checkout-url") || "";
    const stkInitiateUrl = cartRoot.getAttribute("data-stk-initiate-url") || "";
    const stkStatusTemplate =
      cartRoot.getAttribute("data-stk-status-url-template") || "";
    const stkEnabled = cartRoot.getAttribute("data-stk-enabled") === "1";
    const stkReady = cartRoot.getAttribute("data-stk-ready") === "1";
    const stkOffLabel = cartRoot.getAttribute("data-stk-off-label") || "STK unavailable";
    const stkBlockerMessage =
      cartRoot.getAttribute("data-stk-blocker-message") || stkOffLabel;
    const stkHintEl = checkoutForm?.querySelector("[data-cart-stk-hint]");
    const stkErrorEl = checkoutForm?.querySelector("[data-cart-stk-error]");
    const stkErrorTextEl = checkoutForm?.querySelector("[data-cart-stk-error-text]");
    const cartVerifyUrl = cartRoot.getAttribute("data-verify-login-url") || "";
    const clientLookupUrl = cartRoot.getAttribute("data-client-lookup-url") || "";
    const serialSearchUrl =
      cartRoot.getAttribute("data-serial-search-url") || "";
    const enabledPrintChannels = String(cartRoot.dataset.printChannels || "")
      .split(",")
      .map((c) => c.trim())
      .filter(Boolean);
    const enabledKinds = String(cartRoot.dataset.posKinds || "")
      .split(",")
      .map((c) => c.trim())
      .filter(Boolean);
    const checkoutEnabled = cartRoot.dataset.posCheckout === "1";
    const creditWhatsapp = cartRoot.getAttribute("data-credit-whatsapp") === "1";
    const defaultStatusMessage = (() => {
      if (!checkoutEnabled) {
        return "Checkout is disabled in POS settings.";
      }
      if (enabledPrintChannels.length && cartRoot.dataset.posCompulsoryPrint === "1") {
        return "Enter the seller’s personal 6-digit employee ID (not the shop code) to complete and print.";
      }
      if (enabledPrintChannels.length) {
        return "Enter the seller’s personal 6-digit employee ID (not the shop code) to complete. Connect a printer to print.";
      }
      return "Enter the seller’s personal 6-digit employee ID (not the shop code) to complete the receipt.";
    })();
    let cartCodeVerified = false;
    let cartStaffAllocatedToShop = null;
    let cartStaffName = "";
    let cartStaffAllocatedShopNames = [];
    let cartVerifyTimer = null;
    let cartVerifySeq = 0;
    let checkoutInFlight = false;
    let splitLastEdited = "cash";
    let splitSyncing = false;
    let serialSaleItem = null;
    let serialSaleSearchTimer = null;
    let serialSaleSearchSeq = 0;
    const serialSaleMatchModeRoot = serialSaleModal?.querySelector(
      "[data-serial-sale-match-mode]"
    );
    const serialSaleHint = serialSaleModal?.querySelector("[data-serial-sale-hint]");
    const SERIAL_SALE_MATCH_KEY = "myShopSerialSaleMatchMode";
    let serialSaleMatchMode = "full";
    try {
      const saved = String(
        window.sessionStorage?.getItem(SERIAL_SALE_MATCH_KEY) || ""
      ).toLowerCase();
      if (saved === "last4" || saved === "full") serialSaleMatchMode = saved;
    } catch (_err) {
      /* ignore */
    }
    let stkConfirmed = null;
    let stkPollToken = 0;
    let stkSending = false;
    let stkFailed = false;

    [fab, overlay, drawer, productModal, serialSaleModal, wrongShopModal].forEach((el) => {
      if (el && el.parentElement !== document.body) {
        document.body.appendChild(el);
      }
    });

    /** @type {Map<string, {id:string, name:string, category:string, description:string, price:number, listPrice:number, minPrice:number, stock:number, image:string, qty:number, trackSerial?:boolean, serials?:string[]}>} */
    const cart = new Map();
    let activeProductId = "";

    const cartSubtotal = () =>
      roundMoney(
        [...cart.values()].reduce((sum, line) => sum + line.price * line.qty, 0)
      );

    const taxEnabled = cartRoot.dataset.posTax === "1";
    const taxPercent = Math.max(
      0,
      Math.min(100, Number(cartRoot.dataset.posTaxPercent || 0) || 0)
    );

    const cartTaxAmount = (subtotal = cartSubtotal()) => {
      if (!taxEnabled || taxPercent <= 0) return 0;
      return roundMoney((subtotal * taxPercent) / 100);
    };

    const cartTotal = () => {
      const subtotal = cartSubtotal();
      return roundMoney(subtotal + cartTaxAmount(subtotal));
    };

    const pagePrinterNotice = cartRoot.querySelector("[data-printer-required-notice]");
    const pagePrinterNoticeText = cartRoot.querySelector(
      "[data-printer-required-notice-text]"
    );

    const setNoticeEl = (root, textEl, message, { showConnect = false } = {}) => {
      if (!root || !textEl) return;
      textEl.textContent = message || "";
      root.hidden = !message;
      const connectBtn = root.querySelector("[data-printer-required-connect]");
      if (connectBtn) connectBtn.hidden = !(message && showConnect);
      if (message && window.lucide?.createIcons) {
        window.lucide.createIcons({ nodes: [root] });
      }
    };

    const setCartNotice = (message, options = {}) => {
      setNoticeEl(cartNotice, cartNoticeText, message, options);
    };

    const setPagePrinterNotice = (message, options = {}) => {
      setNoticeEl(pagePrinterNotice, pagePrinterNoticeText, message, options);
    };

    const setCartStatus = (message, { ok = false, error = false } = {}) => {
      const text = message || defaultStatusMessage;
      if (cartStatus) {
        cartStatus.textContent = text;
        cartStatus.classList.toggle("is-ok", ok);
        cartStatus.classList.toggle("is-error", error);
      }
      if (tradeStatusEl && tradeModal && !tradeModal.hidden) {
        tradeStatusEl.hidden = false;
        tradeStatusEl.textContent = text;
        tradeStatusEl.classList.toggle("is-ok", ok);
        tradeStatusEl.classList.toggle("is-error", error);
      }
    };

    const PRINTER_CONNECT_MSG =
      "Connect a printer before completing the sale.";
    const PRINTER_CHANNELS_MSG =
      "Compulsory printing is on, but no print channels are enabled in settings.";

    const printerRequiredNotice = () => {
      if (!compulsoryPrintOnSale || !checkoutEnabled) {
        return { message: "", showConnect: false };
      }
      if (!hasEnabledPrintChannels()) {
        return { message: PRINTER_CHANNELS_MSG, showConnect: false };
      }
      const status = window.RichcomPrinter?.getStatus?.();
      if (!status?.connected) {
        return { message: PRINTER_CONNECT_MSG, showConnect: true };
      }
      const channel = resolvePrintChannel(status.channel);
      if (
        !channel ||
        (window.RichcomPrinter?.canAutoPrint &&
          !window.RichcomPrinter.canAutoPrint(channel))
      ) {
        return { message: PRINTER_CONNECT_MSG, showConnect: true };
      }
      return { message: "", showConnect: false };
    };

    const syncPrinterRequiredNotice = () => {
      const { message, showConnect } = printerRequiredNotice();
      setCartNotice(message, { showConnect });
      setPagePrinterNotice(message, { showConnect });
      return message;
    };

    const notifyPrinterRequired = (message) => {
      const notice = printerRequiredNotice();
      const text = message || notice.message;
      if (!text) return;
      const showConnect =
        text === PRINTER_CONNECT_MSG || notice.showConnect;
      setCartStatus(text, { error: true });
      setCartNotice(text, { showConnect });
      setPagePrinterNotice(text, { showConnect });
      pushStockRequestToast(text, "error", { silent: true });
    };

    const setCartVerified = (verified, message = "") => {
      cartCodeVerified = Boolean(verified);
      if (!verified) {
        cartStaffAllocatedToShop = null;
        cartStaffName = "";
        cartStaffAllocatedShopNames = [];
      }
      syncCheckoutMode();
      setCartStatus(
        message ||
          (verified
            ? "Staff verified — printing…"
            : ""),
        { ok: verified, error: Boolean(message) && !verified }
      );
    };

    const applyPendingStockToCatalog = async () => {
      const shopId = Number(cartRoot?.dataset?.shopId || 0);
      if (!shopId) return;
      let pending = {};
      try {
        const store = await import("./offline/store.js");
        pending = await store.pendingQuantitiesForShop(shopId);
      } catch (_err) {
        return;
      }
      cartRoot.querySelectorAll("[data-cart-item]").forEach((card) => {
        const id = card.getAttribute("data-item-id");
        let base = card.getAttribute("data-item-stock-base");
        if (base == null) {
          base = card.getAttribute("data-item-stock") || "0";
          card.setAttribute("data-item-stock-base", base);
        }
        const held = Math.max(0, roundQty(pending[id]));
        const qty = Math.max(0, roundQty(roundQty(base) - held));
        card.setAttribute("data-item-stock", String(qty));
        const valueEl = card.querySelector(".shop-floor-stock-value");
        if (valueEl) valueEl.textContent = String(qty);
        const stockWrap = card.querySelector(".shop-floor-stock");
        if (stockWrap) stockWrap.classList.toggle("is-empty", qty <= 0);
        card.classList.toggle("is-out", stockTrackingEnabled && qty <= 0);
      });
      syncCardControls();
      syncProductControls();
    };

    const applyStockUpdates = (updates) => {
      if (!Array.isArray(updates) || !updates.length) return;
      updates.forEach((row) => {
        const id = String(row?.id || "");
        if (!id) return;
        const qty = Math.max(0, roundQty(row.quantity));
        cartRoot
          .querySelectorAll(
            `[data-cart-item][data-item-id="${CSS.escape(id)}"]`
          )
          .forEach((card) => {
            card.setAttribute("data-item-stock-base", String(qty));
            card.setAttribute("data-item-stock", String(qty));
            const valueEl = card.querySelector(".shop-floor-stock-value");
            if (valueEl) valueEl.textContent = String(qty);
            const stockWrap = card.querySelector(".shop-floor-stock");
            if (stockWrap) stockWrap.classList.toggle("is-empty", qty <= 0);
          });
        if (productModal?.dataset.itemId === id) {
          productModal.dataset.itemStock = String(qty);
          if (productStock) {
            if (stockTrackingEnabled) {
              productStock.hidden = false;
              productStock.textContent =
                qty > 0 ? `${qty} in stock` : "Out of stock";
              productStock.classList.toggle("is-empty", qty <= 0);
            } else {
              productStock.hidden = true;
            }
          }
        }
      });
      syncCardControls();
      syncProductControls();
    };

    const defaultKind = cartRoot.dataset.defaultKind || "";
    const defaultPayment = cartRoot.dataset.defaultPayment || "";
    const defaultPrintVia = cartRoot.dataset.defaultPrintVia || "";
    const paymentsEnabled = cartRoot.dataset.posCashSale === "1";
    const discountEnabled = cartRoot.dataset.posDiscount === "1";
    const stockTrackingEnabled = cartRoot.dataset.posStockTracking !== "0";
    const clientDataEnabled = cartRoot.dataset.posClientData === "1";
    const requireClientPhone = cartRoot.dataset.posRequireClientPhone === "1";
    const requireClientName = cartRoot.dataset.posRequireClientName === "1";
    const compulsoryPrintOnSale = cartRoot.dataset.posCompulsoryPrint === "1";

    const selectedKind = () => {
      const checked = checkoutForm?.querySelector("[data-cart-kind]:checked");
      if (checked?.value) return checked.value;
      const any = checkoutForm?.querySelector("[data-cart-kind]");
      if (any?.value) return any.value;
      return defaultKind;
    };

    const selectedPayment = () =>
      checkoutForm?.querySelector("[data-cart-pay]:checked")?.value ||
      checkoutForm?.querySelector("[data-cart-pay]")?.value ||
      defaultPayment;

    const selectedPrintVia = () => {
      const status = window.RichcomPrinter?.getStatus?.();
      const connected = status?.connected && status.channel;
      if (connected && enabledPrintChannels.includes(status.channel)) {
        return status.channel;
      }
      if (
        status?.wantConnected &&
        status.preferredChannel &&
        enabledPrintChannels.includes(status.preferredChannel)
      ) {
        return status.preferredChannel;
      }
      if (defaultPrintVia && enabledPrintChannels.includes(defaultPrintVia)) {
        return defaultPrintVia;
      }
      return enabledPrintChannels[0] || "";
    };

    const hasEnabledKinds = () =>
      enabledKinds.length > 0 ||
      Boolean(checkoutForm?.querySelector("[data-cart-kind]"));

    const hasEnabledPayments = () =>
      Boolean(checkoutForm?.querySelector("[data-cart-pay]"));

    const hasEnabledPrintChannels = () => enabledPrintChannels.length > 0;

    const resolvePrintChannel = (preferred = "") => {
      const candidates = [
        preferred,
        window.RichcomPrinter?.getStatus?.()?.channel,
        window.RichcomPrinter?.getStatus?.()?.preferredChannel,
        selectedPrintVia(),
      ]
        .map((c) => String(c || "").trim().toLowerCase())
        .filter(Boolean);
      return candidates.find((c) => enabledPrintChannels.includes(c)) || "";
    };

    syncPrinterRequiredNotice();
    if (window.RichcomPrinter?.onChange) {
      window.RichcomPrinter.onChange(() => {
        syncPrinterRequiredNotice();
      });
    } else {
      // Printer module may still be booting — keep checking briefly.
      let tries = 0;
      const waitForPrinter = window.setInterval(() => {
        tries += 1;
        if (window.RichcomPrinter?.onChange) {
          window.clearInterval(waitForPrinter);
          window.RichcomPrinter.onChange(() => {
            syncPrinterRequiredNotice();
          });
          syncPrinterRequiredNotice();
          return;
        }
        syncPrinterRequiredNotice();
        if (tries >= 40) window.clearInterval(waitForPrinter);
      }, 250);
    }

    const syncSplitAmounts = (source = splitLastEdited) => {
      if (!cashInput || !mpesaInput || selectedPayment() !== "both") return;
      const total = cartTotal();
      splitSyncing = true;
      if (source === "cash") {
        const cash = Math.max(0, roundMoney(cashInput.value || 0));
        mpesaInput.value = Math.max(0, roundMoney(total - cash)).toFixed(2);
      } else {
        const mpesa = Math.max(0, roundMoney(mpesaInput.value || 0));
        cashInput.value = Math.max(0, roundMoney(total - mpesa)).toFixed(2);
      }
      splitSyncing = false;
    };

    const saleUsesMpesa = () =>
      Boolean(
        selectedKind() === "sale" &&
          paymentsEnabled &&
          (selectedPayment() === "mpesa" || selectedPayment() === "both")
      );

    const saleNeedsStk = () => Boolean(saleUsesMpesa() && stkEnabled && stkReady);

    const mpesaPromptAmount = () => {
      if (selectedPayment() === "both") {
        syncSplitAmounts(splitLastEdited);
        return Math.max(0, Math.round(Number(mpesaInput?.value || 0) || 0));
      }
      return Math.max(0, Math.round(cartTotal()));
    };

    const setStkStatus = (message = "", { error = false, ok = false } = {}) => {
      if (!message) return;
      setCartStatus(message, { error, ok });
    };

    const setStkError = (message = "", { show = true } = {}) => {
      const text = String(message || "").trim();
      if (!stkErrorEl) return;
      if (!show || !text) {
        stkErrorEl.hidden = true;
        return;
      }
      if (stkErrorTextEl) stkErrorTextEl.textContent = text;
      stkErrorEl.hidden = false;
      refreshIcons();
    };

    const setStkWaiting = (waiting, message = "") => {
      if (stkPanel) stkPanel.classList.toggle("is-waiting", Boolean(waiting));
      if (stkWaitEl) {
        stkWaitEl.hidden = !waiting;
        if (waiting && stkWaitTextEl) {
          stkWaitTextEl.textContent =
            message || "Waiting for customer to confirm on their phone…";
        }
      }
      if (waiting) refreshIcons();
    };

    const setStkReceiptVisible = (code = "") => {
      const value = String(code || "").trim();
      if (stkReceiptEl) stkReceiptEl.hidden = !value;
      if (stkReceiptCodeEl) stkReceiptCodeEl.textContent = value;
    };

    const setStkButtonLabel = (label, { retry = false, disabled = false } = {}) => {
      if (!stkSendBtn) return;
      stkSendBtn.hidden = false;
      stkSendBtn.textContent = label;
      stkSendBtn.disabled = Boolean(disabled);
      stkSendBtn.classList.toggle("is-retry", Boolean(retry));
      stkSendBtn.classList.toggle("is-blocked", !stkReady && !retry);
    };

    const clearStkConfirmation = ({ keepStatus = false, keepFailed = false } = {}) => {
      stkConfirmed = null;
      stkPollToken += 1;
      stkSending = false;
      if (!keepFailed) stkFailed = false;
      setStkWaiting(false);
      setStkReceiptVisible("");
      if (!stkFailed) {
        setStkButtonLabel("Send STK prompt (optional)", {
          disabled: !stkReady,
        });
      }
      if (!keepStatus) {
        /* status restored by syncStkPanel / default cart copy */
      }
    };

    const setStaffCodeLocked = (locked) => {
      if (staffWrap) staffWrap.classList.toggle("is-locked", Boolean(locked));
      if (staffLockHint) staffLockHint.hidden = !locked;
      if (cartLoginCode) {
        cartLoginCode.disabled = Boolean(locked);
        cartLoginCode.readOnly = Boolean(locked);
        if (locked) {
          cartLoginCode.value = "";
          cartCodeVerified = false;
          if (cartSubmit) cartSubmit.disabled = true;
        }
      }
    };

    const syncStkPanel = () => {
      const usesMpesa = saleUsesMpesa();
      const showStk = Boolean(usesMpesa && stkEnabled);
      if (stkPanel) stkPanel.hidden = !showStk;

      const amount = mpesaPromptAmount();
      setStaffCodeLocked(false);

      if (!showStk) {
        setStkError("", { show: false });
        clearStkConfirmation();
        return;
      }

      if (
        stkConfirmed &&
        Number(stkConfirmed.amount || 0) !== Number(amount || 0)
      ) {
        clearStkConfirmation({ keepStatus: true });
        setCartStatus("Cart amount changed — send a new STK prompt if needed.", {
          error: true,
        });
      }

      if (stkConfirmed) {
        setStkError("", { show: false });
        setStkWaiting(false);
        setStkReceiptVisible(
          stkConfirmed.mpesa_receipt_number || "Payment confirmed"
        );
        if (stkSendBtn) stkSendBtn.hidden = true;
        setCartStatus(
          stkConfirmed.mpesa_receipt_number
            ? `M-Pesa ${stkConfirmed.mpesa_receipt_number}. Enter staff ID to finish.`
            : "M-Pesa confirmed. Enter the 6-digit staff ID to finish the sale.",
          { ok: true }
        );
        return;
      }

      setStkReceiptVisible("");
      if (stkSending) {
        setStkError("", { show: false });
        setStkButtonLabel("Waiting…", { disabled: true });
        setStkWaiting(true, "Waiting for customer to confirm on their phone…");
        return;
      }

      setStkWaiting(false);
      if (!stkReady) {
        const blocker =
          stkBlockerMessage ||
          stkOffLabel ||
          "STK Push is not ready. Finish with staff ID or fix Daraja settings.";
        setStkError(blocker);
        if (stkHintEl) {
          stkHintEl.hidden = false;
          stkHintEl.textContent = blocker;
        }
        setStkButtonLabel("Send STK prompt (optional)", {
          disabled: false,
        });
        setCartStatus(blocker, { error: true });
        return;
      }

      if (!stkFailed) {
        setStkError("", { show: false });
        if (stkHintEl) stkHintEl.hidden = true;
      }
      if (stkFailed) {
        setStkButtonLabel("Try again", {
          retry: true,
          disabled: amount < 1,
        });
        return;
      }
      setStkButtonLabel("Send STK prompt (optional)", {
        disabled: amount < 1,
      });
      setCartStatus(
        "Optional: send STK prompt, or enter staff ID to finish without it."
      );
    };

    const sendStkPrompt = async () => {
      if (!saleUsesMpesa() || !stkEnabled || stkSending || stkConfirmed) return;
      if (!stkReady) {
        const blocker = stkBlockerMessage || stkOffLabel || "STK Push is not ready.";
        setStkError(blocker);
        setStkStatus(blocker, { error: true });
        syncStkPanel();
        return;
      }
      if (!stkInitiateUrl || !stkStatusTemplate) {
        const unavailableMsg = "STK Push is unavailable. Refresh and try again.";
        stkFailed = true;
        setStkError(unavailableMsg);
        setStkStatus(unavailableMsg, { error: true });
        syncStkPanel();
        return;
      }
      const phoneRaw = normalizeClientPhoneField({ force: true });
      const phoneDigits = String(phoneRaw || "").replace(/\D/g, "");
      if (!phoneDigits.startsWith("254") || phoneDigits.length !== 12) {
        const phoneMsg = "Enter a valid Kenyan phone number for M-Pesa STK Push.";
        setStkError(phoneMsg);
        setStkStatus(phoneMsg, { error: true });
        focusCartClientFields();
        return;
      }
      if (selectedPayment() === "both") {
        syncSplitAmounts(splitLastEdited);
      }
      const amount = mpesaPromptAmount();
      if (amount < 1) {
        const amountMsg = "M-Pesa amount must be at least KSh 1.";
        setStkError(amountMsg);
        setStkStatus(amountMsg, { error: true });
        return;
      }

      setStkError("", { show: false });

      stkFailed = false;
      stkSending = true;
      const pollToken = ++stkPollToken;
      setStkButtonLabel("Sending…", { disabled: true });
      setStkWaiting(true, "Sending M-Pesa STK Push…");
      setStkReceiptVisible("");
      setCartStatus("Sending M-Pesa STK Push…");
      refreshIcons();

      try {
        const stkStart = await fetch(stkInitiateUrl, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken(checkoutForm),
          },
          credentials: "same-origin",
          body: JSON.stringify({
            amount: String(amount),
            phone: phoneDigits,
            description: "Sale payment",
          }),
        });
        const stkData = await stkStart.json().catch(() => ({}));
        if (!stkStart.ok || !stkData.ok || !stkData.id) {
          throw new Error(stkData.error || "Could not start M-Pesa STK Push.");
        }

        const waitMsg = "Waiting for customer to confirm on their phone…";
        setStkButtonLabel("Waiting…", { disabled: true });
        setStkWaiting(true, waitMsg);
        setCartStatus(waitMsg);

        const statusUrl = stkStatusTemplate.replace("__ID__", stkData.id);
        let confirmed = null;
        const deadline = Date.now() + 120000;
        while (Date.now() < deadline) {
          if (pollToken !== stkPollToken) return;
          await new Promise((resolve) => window.setTimeout(resolve, 2500));
          if (pollToken !== stkPollToken) return;
          const statusRes = await fetch(statusUrl, {
            headers: { Accept: "application/json" },
            credentials: "same-origin",
            signal: AbortSignal.timeout(20000),
          });
          const statusData = await statusRes.json().catch(() => ({}));
          if (!statusRes.ok || !statusData.ok) {
            throw new Error(statusData.error || "Could not check M-Pesa status.");
          }
          if (statusData.success) {
            confirmed = statusData;
            break;
          }
          if (statusData.failed) {
            throw new Error(
              statusData.result_desc ||
                statusData.status_label ||
                "Customer did not complete M-Pesa payment."
            );
          }
          const liveMsg =
            statusData.result_desc ||
            "Waiting for customer to confirm on their phone…";
          setStkWaiting(true, liveMsg);
          setCartStatus(liveMsg);
          if (statusData.mpesa_receipt_number) {
            setStkReceiptVisible(statusData.mpesa_receipt_number);
          }
        }
        if (pollToken !== stkPollToken) return;
        if (!confirmed) {
          throw new Error("Timed out waiting for M-Pesa confirmation.");
        }

        stkConfirmed = {
          id: confirmed.id,
          mpesa_receipt_number: confirmed.mpesa_receipt_number || "",
          amount,
          phone: phoneDigits,
        };
        stkFailed = false;
        stkSending = false;
        setStkWaiting(false);
        setStkReceiptVisible(
          stkConfirmed.mpesa_receipt_number || "Payment confirmed"
        );
        setCartStatus(
          stkConfirmed.mpesa_receipt_number
            ? `M-Pesa reference ${stkConfirmed.mpesa_receipt_number}`
            : "M-Pesa payment confirmed.",
          { ok: true }
        );
        syncStkPanel();
        cartLoginCode?.focus();
      } catch (err) {
        if (pollToken !== stkPollToken) return;
        stkFailed = true;
        stkConfirmed = null;
        stkSending = false;
        setStkWaiting(false);
        setStkReceiptVisible("");
        const failMsg = err?.message || "STK Push failed.";
        setStkButtonLabel("Try again", { retry: true, disabled: false });
        setStkError(failMsg);
        setStkStatus(failMsg, { error: true });
        if (stkHintEl) {
          stkHintEl.hidden = false;
          stkHintEl.textContent = failMsg;
        }
        syncStkPanel();
      } finally {
        if (pollToken === stkPollToken) {
          stkSending = false;
          if (!stkConfirmed) {
            setStkWaiting(false);
          }
          syncStkPanel();
        }
      }
    };

    const toKenyaPhone = (value) => {
      let digits = String(value || "").replace(/\D+/g, "");
      if (!digits) return "";
      if (digits.startsWith("00")) digits = digits.slice(2);
      if (digits.startsWith("0") && digits.length === 10) {
        digits = `254${digits.slice(1)}`;
      } else if ((digits.startsWith("7") || digits.startsWith("1")) && digits.length === 9) {
        digits = `254${digits}`;
      }
      if (digits.startsWith("254") && digits.length === 12) {
        return `+${digits}`;
      }
      return "";
    };

    // Client autofill: typing a full phone looks up the name, typing a name
    // shows matching clients and fills the phone on selection.
    const createClientAutofill = (
      { nameInput, phoneInput, suggestEl, onHint } = {}
    ) => {
      let lookupTimer = null;
      let lookupSeq = 0;
      let nameSearchTimer = null;
      let nameSearchSeq = 0;
      let nameAutofilled = false;
      let phoneAutofilled = false;
      let suggestIndex = -1;

      const setHint = (message = "", { ok = false } = {}) => {
        onHint?.(message, { ok });
      };

      const normalizeName = () => {
        if (!nameInput) return;
        const start = nameInput.selectionStart;
        const end = nameInput.selectionEnd;
        const next = (nameInput.value || "").toUpperCase();
        if (nameInput.value !== next) {
          nameInput.value = next;
          if (typeof start === "number" && typeof end === "number") {
            nameInput.setSelectionRange(start, end);
          }
        }
      };

      const normalizePhoneField = ({ force = false } = {}) => {
        if (!phoneInput) return "";
        const raw = (phoneInput.value || "").trim();
        if (!raw) return "";
        const formatted = toKenyaPhone(raw);
        if (formatted) {
          if (phoneInput.value !== formatted) {
            phoneInput.value = formatted;
          }
          return formatted;
        }
        if (force && raw) {
          setHint("Use a Kenyan number (07… or +2547…).");
        }
        return raw;
      };

      const hideSuggest = () => {
        if (!suggestEl) return;
        suggestEl.hidden = true;
        suggestEl.innerHTML = "";
        suggestIndex = -1;
      };

      const applySelection = (client) => {
        if (!client) return;
        if (nameInput) {
          nameInput.value = String(client.full_name || "").toUpperCase();
          nameAutofilled = true;
        }
        if (phoneInput && client.phone_number) {
          phoneInput.value = client.phone_number;
          phoneAutofilled = true;
        }
        hideSuggest();
        setHint("Registered client selected.", { ok: true });
      };

      const renderSuggest = (results) => {
        if (!suggestEl) return;
        suggestEl.innerHTML = "";
        suggestIndex = -1;
        if (!results.length) {
          const empty = document.createElement("div");
          empty.className = "shop-cart-client-suggest-empty";
          empty.textContent = "No matching clients";
          suggestEl.appendChild(empty);
          suggestEl.hidden = false;
          return;
        }

        results.forEach((client, index) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "shop-cart-client-suggest-option";
          btn.setAttribute("role", "option");
          btn.dataset.index = String(index);
          btn.innerHTML = `<strong></strong><small></small>`;
          btn.querySelector("strong").textContent = client.full_name || "";
          btn.querySelector("small").textContent = client.phone_number || "";
          btn.addEventListener("mousedown", (event) => {
            event.preventDefault();
            applySelection(client);
          });
          suggestEl.appendChild(btn);
        });
        suggestEl.hidden = false;
      };

      const moveSuggest = (delta) => {
        if (!suggestEl || suggestEl.hidden) return;
        const options = [
          ...suggestEl.querySelectorAll(".shop-cart-client-suggest-option"),
        ];
        if (!options.length) return;
        suggestIndex =
          (suggestIndex + delta + options.length) % options.length;
        options.forEach((el, idx) => {
          el.classList.toggle("is-active", idx === suggestIndex);
        });
        options[suggestIndex]?.scrollIntoView({ block: "nearest" });
      };

      const selectActiveSuggest = () => {
        if (!suggestEl || suggestEl.hidden) return false;
        const options = [
          ...suggestEl.querySelectorAll(".shop-cart-client-suggest-option"),
        ];
        if (!options.length) return false;
        const active =
          options[suggestIndex] ||
          options.find((el) => el.classList.contains("is-active")) ||
          options[0];
        active?.dispatchEvent(new Event("mousedown", { bubbles: true }));
        return true;
      };

      const searchByName = async () => {
        normalizeName();
        const query = (nameInput?.value || "").trim();
        const seq = ++nameSearchSeq;
        if (query.length < 2) {
          hideSuggest();
          if (phoneAutofilled && phoneInput) {
            phoneInput.value = "";
            phoneAutofilled = false;
            setHint("");
          }
          return;
        }
        if (!clientLookupUrl) return;

        try {
          const url = `${clientLookupUrl}?name=${encodeURIComponent(query)}`;
          const response = await fetch(url, {
            method: "GET",
            headers: { Accept: "application/json" },
            credentials: "same-origin",
          });
          const data = await response.json().catch(() => ({}));
          if (seq !== nameSearchSeq) return;
          if (!response.ok || !data.ok) {
            hideSuggest();
            return;
          }
          const results = Array.isArray(data.results) ? data.results : [];
          renderSuggest(results);
          if (!results.length) {
            setHint("No registered client with that name.");
          } else {
            setHint("Select a client to fill the phone number.");
          }
        } catch (_) {
          if (seq !== nameSearchSeq) return;
          hideSuggest();
        }
      };

      const queueNameSearch = () => {
        window.clearTimeout(nameSearchTimer);
        nameSearchTimer = window.setTimeout(() => {
          searchByName();
        }, 220);
      };

      const lookupByPhone = async () => {
        normalizePhoneField();
        const phone = (phoneInput?.value || "").trim();
        const digits = phone.replace(/\D+/g, "");
        const seq = ++lookupSeq;

        if (digits.length < 12) {
          if (nameAutofilled && nameInput) {
            nameInput.value = "";
            nameAutofilled = false;
          }
          setHint("");
          return;
        }
        if (!clientLookupUrl) return;

        try {
          const url = `${clientLookupUrl}?phone=${encodeURIComponent(phone)}`;
          const response = await fetch(url, {
            method: "GET",
            headers: { Accept: "application/json" },
            credentials: "same-origin",
          });
          const data = await response.json().catch(() => ({}));
          if (seq !== lookupSeq) return;
          if (!response.ok || !data.ok) {
            setHint("");
            return;
          }
          if (data.found && data.full_name) {
            if (nameInput) {
              nameInput.value = String(data.full_name || "").toUpperCase();
              nameAutofilled = true;
            }
            if (data.phone_number && phoneInput) {
              phoneInput.value = data.phone_number;
              phoneAutofilled = true;
            }
            hideSuggest();
            setHint("Registered client found.", { ok: true });
          } else {
            if (nameAutofilled && nameInput) {
              nameInput.value = "";
              nameAutofilled = false;
            }
            setHint("New client — will be saved on complete.");
          }
        } catch (_) {
          if (seq !== lookupSeq) return;
          setHint("");
        }
      };

      const queueLookup = () => {
        window.clearTimeout(lookupTimer);
        lookupTimer = window.setTimeout(() => {
          lookupByPhone();
        }, 280);
      };

      const reset = () => {
        nameAutofilled = false;
        phoneAutofilled = false;
        window.clearTimeout(lookupTimer);
        window.clearTimeout(nameSearchTimer);
        lookupSeq += 1;
        nameSearchSeq += 1;
        setHint("");
        hideSuggest();
      };

      phoneInput?.addEventListener("input", () => {
        phoneAutofilled = false;
        hideSuggest();
        queueLookup();
      });
      phoneInput?.addEventListener("blur", () => {
        normalizePhoneField({ force: true });
        lookupByPhone();
      });
      nameInput?.addEventListener("input", () => {
        nameAutofilled = false;
        normalizeName();
        queueNameSearch();
      });
      nameInput?.addEventListener("keydown", (event) => {
        if (!suggestEl || suggestEl.hidden) return;
        if (event.key === "ArrowDown") {
          event.preventDefault();
          moveSuggest(1);
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          moveSuggest(-1);
        } else if (event.key === "Enter") {
          if (selectActiveSuggest()) event.preventDefault();
        } else if (event.key === "Escape") {
          hideSuggest();
        }
      });
      nameInput?.addEventListener("blur", () => {
        normalizeName();
        window.setTimeout(() => hideSuggest(), 120);
      });

      return {
        setHint,
        normalizeName,
        normalizePhoneField,
        hideSuggest,
        lookupByPhone,
        reset,
      };
    };

    const cartClient = createClientAutofill({
      nameInput: clientNameInput,
      phoneInput: clientPhoneInput,
      suggestEl: clientSuggest,
      onHint: (message = "", { ok = false } = {}) => {
        if (!clientHint) return;
        clientHint.textContent = message;
        clientHint.classList.toggle("is-ok", ok && Boolean(message));
      },
    });
    const normalizeClientPhoneField = cartClient.normalizePhoneField;

    const cartHasSerialTracked = () =>
      [...cart.values()].some(
        (line) => line.trackSerial || (line.serials && line.serials.length)
      );

    const localTodayIso = () => {
      const now = new Date();
      const year = now.getFullYear();
      const month = String(now.getMonth() + 1).padStart(2, "0");
      const day = String(now.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    };

    const ensureCreditDueDefault = () => {
      if (!creditDueInput) return;
      creditDueInput.min = localTodayIso();
      if (!creditDueInput.value) creditDueInput.value = localTodayIso();
    };

    const syncClientRequirements = () => {
      const kind = selectedKind();
      const isTrade = kind === "trade_out" || kind === "trade_in";
      if (!clientDataEnabled && !isTrade) {
        if (clientBlock) clientBlock.hidden = true;
        if (clientHint) {
          clientHint.hidden = true;
          clientHint.textContent = "";
        }
        if (clientSuggest) {
          clientSuggest.hidden = true;
          clientSuggest.innerHTML = "";
        }
        return;
      }
      if (clientBlock) clientBlock.hidden = false;
      const hasSerials = cartHasSerialTracked();
      const eitherRequired = kind === "credit" || kind === "quotation";
      const phoneRequired =
        requireClientPhone || (kind === "credit" && creditWhatsapp) || isTrade;
      const nameRequired = requireClientName || isTrade;
      const blockRequired = phoneRequired || nameRequired || eitherRequired;
      if (clientPhoneInput) clientPhoneInput.required = phoneRequired;
      if (clientNameInput) clientNameInput.required = nameRequired;
      if (clientBlock) clientBlock.classList.toggle("is-required", blockRequired);
      if (clientHeading) {
        clientHeading.innerHTML = blockRequired
          ? 'Link client <span class="shop-serial-required" aria-hidden="true">*</span>'
          : "Link client";
      }
      if (clientNote) {
        clientNote.hidden = false;
        if (kind === "credit" && creditWhatsapp) {
          clientNote.textContent =
            "Enter the client phone number. A credit sale notice is sent on WhatsApp.";
        } else if (kind === "trade_out") {
          clientNote.textContent =
            "Client who is taking the goods. Name and phone are required.";
        } else if (kind === "trade_in") {
          clientNote.textContent =
            "Client who brought the goods. Name and phone are required. Settle later.";
        } else if (phoneRequired && nameRequired) {
          clientNote.textContent = "Client phone and full name are required.";
        } else if (phoneRequired) {
          clientNote.textContent = "Client phone is required.";
        } else if (nameRequired) {
          clientNote.textContent = "Client full name is required.";
        } else if (eitherRequired) {
          clientNote.textContent = "Enter a name, a phone number, or both.";
        } else if (hasSerials) {
          clientNote.textContent =
            "Optional. Enter a name, a phone number, or both so serials can be linked.";
        } else {
          clientNote.textContent =
            "Optional. Enter a name, a phone number, or both.";
        }
      }
      if (clientPhoneLabel) {
        clientPhoneLabel.innerHTML = phoneRequired
          ? 'Client phone <span class="shop-serial-required" aria-hidden="true">*</span>'
          : "Client phone <em>(optional)</em>";
      }
      if (clientNameLabel) {
        clientNameLabel.innerHTML = nameRequired
          ? 'Client full name <span class="shop-serial-required" aria-hidden="true">*</span>'
          : "Client full name <em>(optional)</em>";
      }
    };

    const focusCartClientFields = () => {
      if (tradeModal && !tradeModal.hidden) {
        window.setTimeout(() => {
          const phone = (tradeClientPhoneEl?.value || "").trim();
          if (!phone) {
            tradeClientPhoneEl?.focus();
            return;
          }
          tradeClientNameEl?.focus();
        }, 80);
        return;
      }
      setCartOpen(true);
      window.setTimeout(() => {
        const phone = (clientPhoneInput?.value || "").trim();
        if (!phone) {
          clientPhoneInput?.focus();
          return;
        }
        clientNameInput?.focus();
      }, 80);
    };

    const syncCheckoutMode = () => {
      const kind = selectedKind();
      const isSale = kind === "sale";
      const isQuote = kind === "quotation";
      const isCredit = kind === "credit";
      if (creditDueWrap) creditDueWrap.hidden = !isCredit;
      if (creditDueInput) {
        creditDueInput.required = false;
        if (isCredit) ensureCreditDueDefault();
      }
      if (whatsappWrap) whatsappWrap.hidden = !(isQuote && clientDataEnabled);
      if ((!isQuote || !clientDataEnabled) && checkoutForm) {
        const wa = checkoutForm.querySelector("[data-cart-whatsapp]");
        if (wa) wa.checked = false;
      }
      const canTakePayment = isSale && paymentsEnabled && hasEnabledPayments();
      if (paymentWrap) {
        paymentWrap.hidden = !canTakePayment;
      }
      const showSplit =
        canTakePayment && selectedPayment() === "both";
      if (splitWrap) splitWrap.hidden = !showSplit;
      if (showSplit) {
        if (!cashInput?.value && !mpesaInput?.value) {
          const half = roundMoney(cartTotal() / 2);
          if (cashInput) cashInput.value = half.toFixed(2);
          splitLastEdited = "cash";
        }
        syncSplitAmounts(splitLastEdited);
      }
      syncClientRequirements();
      syncStkPanel();
    };

    const verifyCartLoginCode = async ({ autoCheckout = true } = {}) => {
      const code = (cartLoginCode?.value || "").trim();
      const seq = ++cartVerifySeq;
      if (code.length < 6) {
        setCartVerified(
          false,
          code.length
            ? `Enter ${6 - code.length} more digit${6 - code.length === 1 ? "" : "s"}.`
            : ""
        );
        return false;
      }
      if (!/^\d{6}$/.test(code)) {
        setCartVerified(false, "Staff ID must be exactly 6 digits.");
        return false;
      }
      if (!cartVerifyUrl) {
        setCartVerified(false, "Verification is unavailable. Refresh and try again.");
        return false;
      }
      try {
        const { verifyStaffLoginCode } = await import("./offline/staff.js");
        const data = await verifyStaffLoginCode({
          url: cartVerifyUrl,
          code,
          csrfToken: getCsrfToken(checkoutForm),
        });
        if (seq !== cartVerifySeq) return false;
        if (!data.ok) {
          setCartVerified(false, data.error || "Not a valid active staff ID.");
          return false;
        }
        cartStaffAllocatedToShop = data.allocated_to_shop !== false;
        cartStaffName = data.name || "";
        cartStaffAllocatedShopNames = Array.isArray(data.allocated_shop_names)
          ? data.allocated_shop_names.filter(Boolean)
          : [];
        setCartVerified(
          true,
          data.message ||
            `Verified: ${data.name || "staff"} (${data.employee_id || code}).`
        );
        if (autoCheckout && checkoutEnabled && cart.size > 0 && !checkoutInFlight) {
          await submitCheckout();
        }
        return true;
      } catch (_) {
        if (seq !== cartVerifySeq) return false;
        setCartVerified(false, "Could not verify staff ID. Try again.");
        return false;
      }
    };

    const newCheckoutClientId = () =>
      (crypto?.randomUUID && crypto.randomUUID()) ||
      `checkout-${Date.now()}-${Math.random().toString(16).slice(2)}`;

    const confirmWrongShopSale = () =>
      new Promise((resolve) => {
        if (!wrongShopModal) {
          resolve(true);
          return;
        }

        const staffLabel = cartStaffName || "This staff member";
        if (wrongShopMessage) {
          wrongShopMessage.innerHTML = `${staffLabel} is not allocated to <strong>${shopName}</strong>.`;
        }
        if (wrongShopDetail) {
          if (cartStaffAllocatedShopNames.length) {
            wrongShopDetail.hidden = false;
            wrongShopDetail.textContent = `Allocated shop${
              cartStaffAllocatedShopNames.length === 1 ? "" : "s"
            }: ${cartStaffAllocatedShopNames.join(", ")}.`;
          } else {
            wrongShopDetail.hidden = true;
            wrongShopDetail.textContent = "";
          }
        }

        const finish = (confirmed) => {
          wrongShopModal.hidden = true;
          syncModalOpen();
          wrongShopModal.removeEventListener("click", onClick);
          window.removeEventListener("keydown", onKeydown);
          resolve(confirmed);
        };

        const onClick = (event) => {
          if (event.target.closest("[data-wrong-shop-confirm]")) {
            event.preventDefault();
            finish(true);
            return;
          }
          if (event.target.closest("[data-wrong-shop-cancel]")) {
            event.preventDefault();
            finish(false);
          }
        };

        const onKeydown = (event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            finish(false);
          }
        };

        wrongShopModal.hidden = false;
        syncModalOpen();
        if (window.lucide?.createIcons) window.lucide.createIcons();
        wrongShopModal.querySelector("[data-wrong-shop-confirm]")?.focus();
        wrongShopModal.addEventListener("click", onClick);
        window.addEventListener("keydown", onKeydown);
      });

    const queueCartVerify = () => {
      window.clearTimeout(cartVerifyTimer);
      cartVerifyTimer = window.setTimeout(() => {
        verifyCartLoginCode();
      }, 220);
    };

    const resetCheckoutForm = () => {
      if (!checkoutForm) return;
      checkoutForm.reset();
      const kindInput =
        checkoutForm.querySelector(`[data-cart-kind][value="${defaultKind}"]`) ||
        checkoutForm.querySelector("[data-cart-kind]");
      const payInput =
        checkoutForm.querySelector(`[data-cart-pay][value="${defaultPayment}"]`) ||
        checkoutForm.querySelector("[data-cart-pay]");
      if (kindInput) kindInput.checked = true;
      if (payInput) payInput.checked = true;
      if (cashInput) cashInput.value = "";
      if (mpesaInput) mpesaInput.value = "";
      if (creditDueInput) creditDueInput.value = "";
      if (selectedKind() === "credit" || defaultKind === "credit") {
        ensureCreditDueDefault();
      }
      cartClient.reset();
      clearStkConfirmation();
      setStaffCodeLocked(false);
      setCartVerified(false);
      syncCheckoutMode();
    };

    const submitCheckout = async () => {
      // Latch immediately so blur/input/Enter races cannot start a second sale.
      if (checkoutInFlight) return;
      checkoutInFlight = true;
      if (cartSubmit) cartSubmit.disabled = true;
      window.clearTimeout(cartVerifyTimer);
      cartVerifySeq += 1;

      try {
        if (!checkoutEnabled) {
          setCartStatus("Checkout is disabled in POS settings.", { error: true });
          return;
        }
        if (!checkoutForm || !checkoutUrl) {
          setCartStatus("Checkout is unavailable. Refresh and try again.", {
            error: true,
          });
          return;
        }
        if (cart.size === 0) {
          setCartStatus("Add items before completing the receipt.", { error: true });
          return;
        }
        if (!hasEnabledKinds()) {
          setCartStatus("No document types are enabled in POS settings.", {
            error: true,
          });
          return;
        }
        const verified =
          cartCodeVerified || (await verifyCartLoginCode({ autoCheckout: false }));
        if (!verified) return;

        const kind = selectedKind();
        if (!kind || !checkoutForm.querySelector(`[data-cart-kind][value="${kind}"]`)) {
          setCartStatus("Choose an enabled document type.", { error: true });
          return;
        }
        for (const line of cart.values()) {
          if (!line.trackSerial) continue;
          const serials = Array.isArray(line.serials) ? line.serials : [];
          if (!serials.length || serials.length !== line.qty) {
            setCartStatus(
              `Add serial numbers for “${line.name}” before checkout.`,
              { error: true }
            );
            openSerialSaleModal(line);
            return;
          }
        }
        if (clientDataEnabled || kind === "trade_out" || kind === "trade_in") {
          const phone = normalizeClientPhoneField({ force: true });
          const name = (clientNameInput?.value || "").trim();
          if (requireClientPhone && !phone) {
            setCartStatus(
              "Enter the client phone number before completing.",
              { error: true }
            );
            focusCartClientFields();
            return;
          }
          if (requireClientName && !name) {
            setCartStatus(
              "Enter the client full name before completing.",
              { error: true }
            );
            focusCartClientFields();
            return;
          }
          if (kind === "trade_out" || kind === "trade_in") {
            if (!name) {
              setCartStatus("Enter the client full name.", { error: true });
              focusCartClientFields();
              return;
            }
            if (!phone) {
              setCartStatus("Enter the client phone number.", { error: true });
              focusCartClientFields();
              return;
            }
          }
          if (kind === "credit" || kind === "quotation") {
            if (kind === "credit" && creditWhatsapp && !phone) {
              setCartStatus(
                "Enter the client phone number so the credit sale can be sent on WhatsApp.",
                { error: true }
              );
              focusCartClientFields();
              return;
            }
            if (!phone && !name) {
              setCartStatus(
                "Enter a client name, a phone number, or both for credit and quotation.",
                { error: true }
              );
              focusCartClientFields();
              return;
            }
          }
        }
        const checkoutClientId = newCheckoutClientId();
        const payload = {
          kind,
          client_id: checkoutClientId,
          client_name:
            clientDataEnabled || kind === "trade_out" || kind === "trade_in"
              ? (
                  checkoutForm.querySelector("[data-cart-client-name]")?.value || ""
                )
                  .trim()
                  .toUpperCase()
              : "",
          client_phone:
            clientDataEnabled || kind === "trade_out" || kind === "trade_in"
              ? normalizeClientPhoneField({ force: true })
              : "",
          login_code: (cartLoginCode?.value || "").trim(),
          share_whatsapp: Boolean(
            clientDataEnabled &&
              checkoutForm.querySelector("[data-cart-whatsapp]")?.checked
          ),
          lines: [...cart.values()].map((line) => ({
            id: line.id,
            qty: line.qty,
            price:
              kind === "trade_in" || discountEnabled
                ? line.price
                : line.listPrice || line.price,
            serials: Array.isArray(line.serials) ? line.serials : [],
          })),
        };

        if (kind === "sale") {
          if (!paymentsEnabled || !hasEnabledPayments()) {
            setCartStatus("No payment methods are enabled in POS settings.", {
              error: true,
            });
            return;
          }
          payload.payment_method = selectedPayment();
          if (
            !payload.payment_method ||
            !checkoutForm.querySelector(
              `[data-cart-pay][value="${payload.payment_method}"]`
            )
          ) {
            setCartStatus("Choose an enabled payment method.", { error: true });
            return;
          }
          if (payload.payment_method === "both") {
            syncSplitAmounts(splitLastEdited);
            payload.cash_amount = cashInput?.value || "0";
            payload.mpesa_amount = mpesaInput?.value || "0";
          }
        }

        if (kind === "credit") {
          const dueDate = (creditDueInput?.value || "").trim();
          if (dueDate) payload.credit_due_date = dueDate;
        }

        const needsStk =
          kind === "sale" &&
          stkEnabled &&
          stkReady &&
          (payload.payment_method === "mpesa" ||
            payload.payment_method === "both");
        if (needsStk && stkConfirmed?.id) {
          const phone = normalizeClientPhoneField({ force: true });
          if (!phone) {
            setCartStatus(
              "Enter the client phone number used for the STK Push.",
              { error: true }
            );
            focusCartClientFields();
            return;
          }
          payload.client_phone = phone;
          const expectedAmount = mpesaPromptAmount();
          if (Number(stkConfirmed.amount || 0) !== Number(expectedAmount || 0)) {
            clearStkConfirmation({ keepStatus: true });
            setStkStatus("Cart amount changed — send a new STK prompt if needed.", {
              error: true,
            });
            setCartStatus("Cart amount changed — send a new STK prompt if needed.", {
              error: true,
            });
            syncStkPanel();
            return;
          }
          payload.stk_payment_id = stkConfirmed.id;
          payload.mpesa_receipt_number = stkConfirmed.mpesa_receipt_number || "";
        }

        const printerStatus = window.RichcomPrinter?.getStatus?.();
        let printVia = resolvePrintChannel(
          (printerStatus?.connected && printerStatus.channel) ||
            (printerStatus?.wantConnected && printerStatus.preferredChannel) ||
            ""
        );
        const hasPrintChannels = hasEnabledPrintChannels();
        if (kind === "sale" && compulsoryPrintOnSale) {
          if (!hasPrintChannels) {
            notifyPrinterRequired(
              "Compulsory printing is on, but no print channels are enabled in settings."
            );
            return;
          }
          if (!printVia) {
            notifyPrinterRequired(
              "Connect a printer before completing the sale."
            );
            return;
          }
          if (window.RichcomPrinter) {
            try {
              await window.RichcomPrinter.ensureConnected(printVia);
            } catch (_) {
              /* restore best-effort */
            }
            const live = window.RichcomPrinter.getStatus();
            if (live.connected) {
              printVia = resolvePrintChannel(live.channel || printVia);
            }
          }
          if (
            window.RichcomPrinter &&
            !window.RichcomPrinter.canAutoPrint(printVia)
          ) {
            notifyPrinterRequired(
              "Connect a printer before completing the sale."
            );
            return;
          }
        }
        if (printVia && hasPrintChannels) payload.print_via = printVia;

        if (cartStaffAllocatedToShop === false) {
          const confirmed = await confirmWrongShopSale();
          if (!confirmed) {
            setCartStatus("Sale cancelled.", { ok: false, error: false });
            return;
          }
        }

        setCartStatus(
          needsStk && stkConfirmed?.id
            ? "Completing M-Pesa sale…"
            : "Printing receipt…"
        );

        const finishSuccessfulCheckout = async (data, { queued = false } = {}) => {
          const soldLines = [...cart.values()].map((line) => ({
            id: line.id,
            qty: line.qty,
            serials: Array.isArray(line.serials) ? line.serials.slice() : [],
          }));
          cart.clear();
          renderCart();
          resetCheckoutForm();
          let stockUpdates = [];
          if (Array.isArray(data.stock_updates) && data.stock_updates.length) {
            stockUpdates = data.stock_updates;
            applyStockUpdates(data.stock_updates);
            applyPendingStockToCatalog();
          } else if (queued && kind !== "quotation" && kind !== "trade_in") {
            await applyPendingStockToCatalog();
          } else if (kind !== "quotation") {
            stockUpdates = soldLines.map((line) => {
              const card = cartRoot.querySelector(
                `[data-cart-item][data-item-id="${CSS.escape(String(line.id))}"]`
              );
              const base = Math.max(
                0,
                roundQty(
                  card?.getAttribute("data-item-stock-base") ??
                    card?.getAttribute("data-item-stock")
                )
              );
              return {
                id: line.id,
                quantity: Math.max(0, roundQty(base - Math.max(0, roundQty(line.qty)))),
              };
            });
            applyStockUpdates(stockUpdates);
            applyPendingStockToCatalog();
          }
          if (kind !== "quotation") {
            const shopId = cartRoot?.dataset?.shopId || "";
            import("./offline/catalog-cache.js")
              .then((mod) =>
                mod.notifyCatalogChanged?.({
                  mode: "sale",
                  scopes: ["stock", "item"],
                  shopId,
                  stockUpdates,
                  soldSerials: soldLines
                    .filter((line) => line.serials?.length)
                    .map((line) => ({ itemId: line.id, serials: line.serials })),
                  reason: queued ? "offline_sale" : "sale",
                  softReload: true,
                })
              )
              .catch(() => {});
          }
          setCartOpen(false);
          setCartStatus(
            queued
              ? data.message || "Sale queued — it will sync when you reconnect."
              : data.message || "Receipt completed.",
            { ok: true }
          );
          if (data.whatsapp_url) {
            window.open(data.whatsapp_url, "_blank", "noopener");
          }
          const channel = resolvePrintChannel(data.print_via || printVia || "");
          const shouldPrint =
            !queued &&
            hasEnabledPrintChannels() &&
            Boolean(channel) &&
            Boolean(data.receipt_text) &&
            (Boolean(data.print_required) ||
              kind === "sale" ||
              kind === "credit" ||
              kind === "quotation" ||
              kind === "trade_out" ||
              kind === "trade_in");
          if (shouldPrint) {
            await printReceiptText(
              data.receipt_text,
              channel,
              data.receipt_qr,
              {
                ...(data.receipt_font || {}),
                paper_width: data.receipt_paper_width || "",
              },
              data.receipt_ticket || null
            );
          }
        };

        const queueOfflineCheckout = async () => {
          if (needsStk && !stkConfirmed?.id) {
            setCartStatus(
              "M-Pesa STK needs a network connection. Switch to cash or reconnect.",
              { error: true }
            );
            return false;
          }
          if (
            kind === "sale" &&
            (payload.payment_method === "mpesa" || payload.payment_method === "both") &&
            !payload.stk_payment_id
          ) {
            setCartStatus(
              "M-Pesa sales without a completed STK prompt need a network connection.",
              { error: true }
            );
            return false;
          }
          const shopId = Number(cartRoot?.dataset?.shopId || 0) || 0;
          const { queueOperation } = await import("./offline/sync.js");
          // Reuse the same client_id as the online attempt so a lost response
          // cannot create a second receipt when the queue syncs later.
          const offlineCheckout = { ...payload };
          delete offlineCheckout.client_id;
          await queueOperation("complete_shop_checkout", {
            client_id: checkoutClientId,
            shop_id: shopId,
            checkout: offlineCheckout,
          });
          await finishSuccessfulCheckout(
            {
              ok: true,
              message: "Sale queued offline — it will sync when you reconnect.",
              stock_updates: [],
            },
            { queued: true }
          );
          return true;
        };

        try {
          const { isOnline } = await import("./offline/connectivity.js");
          if (!isOnline()) {
            await queueOfflineCheckout();
            return;
          }

          const response = await fetch(checkoutUrl, {
            method: "POST",
            headers: {
              Accept: "application/json",
              "Content-Type": "application/json",
              "X-CSRFToken": getCsrfToken(checkoutForm),
            },
            credentials: "same-origin",
            body: JSON.stringify(payload),
          });
          const data = await response.json().catch(() => ({}));
          if (response.status === 503 || data.offline) {
            await queueOfflineCheckout();
            return;
          }
          if (!response.ok || !data.ok) {
            const errorText = data.error || "Could not complete the receipt.";
            if (
              data.code === "unauthorized" ||
              window.MyshopUnauthorized?.isUnauthorizedText?.(errorText)
            ) {
              window.MyshopUnauthorized?.show(errorText);
            }
            setCartStatus(errorText, {
              error: true,
            });
            return;
          }
          await finishSuccessfulCheckout(data);
        } catch (err) {
          // Network failure while supposedly online — queue for sync.
          try {
            const queued = await queueOfflineCheckout();
            if (queued) return;
          } catch (_queueErr) {
            /* fall through */
          }
          setCartStatus(
            err?.message || "Network error while completing the receipt.",
            { error: true }
          );
        }
      } finally {
        checkoutInFlight = false;
        if (cartSubmit) cartSubmit.disabled = !cartCodeVerified;
      }
    };

    const printReceiptText = async (
      text,
      channel,
      qr = null,
      fontStyle = null,
      ticket = null
    ) => {
      const printer = window.RichcomPrinter;
      const status = printer?.getStatus?.();
      const targetChannel =
        (status?.connected && status.channel) || channel || "";
      const styleOverride =
        fontStyle && typeof fontStyle === "object" ? fontStyle : null;
      const qrPayload = {
        payload: qr?.payload || qr?.url || "",
        label: qr?.label || "",
        ready: Boolean(qr?.ready),
        image_data_url: qr?.image_data_url || "",
      };

      // Keep floor dataset in sync with latest receipt font settings.
      if (styleOverride) {
        if (styleOverride.size) {
          cartRoot.dataset.posReceiptFontSize = styleOverride.size;
        }
        if (styleOverride.weight) {
          cartRoot.dataset.posReceiptFontWeight = styleOverride.weight;
        }
        if (styleOverride.weight_css) {
          cartRoot.dataset.posReceiptFontWeightCss = styleOverride.weight_css;
        }
        if (styleOverride.size_px_80) {
          cartRoot.dataset.posReceiptFontPx80 = styleOverride.size_px_80;
        }
        if (styleOverride.size_px_58) {
          cartRoot.dataset.posReceiptFontPx58 = styleOverride.size_px_58;
        }
        if (styleOverride.paper_width === "58" || styleOverride.paper_width === "80") {
          cartRoot.dataset.posReceiptWidth = styleOverride.paper_width;
        }
      }

      if (printer?.canAutoPrint?.(targetChannel)) {
        try {
          await printer.print(
            targetChannel,
            text,
            qrPayload,
            styleOverride,
            ticket
          );
          return true;
        } catch (err) {
          setCartStatus(
            err?.message ||
              "Printer connected but print failed — opening browser print.",
            { error: true }
          );
        }
      }

      const label =
        targetChannel === "bluetooth"
          ? "Bluetooth"
          : targetChannel === "usb"
            ? "USB"
            : targetChannel === "wifi"
              ? "Wi‑Fi"
              : "Printer";

      if (typeof printer?.browserPrint === "function") {
        try {
          await printer.browserPrint(text, qrPayload, label, styleOverride, ticket);
          return true;
        } catch (_) {
          /* fall through to minimal fallback */
        }
      }

      // Minimal fallback if shop-printer.js is unavailable.
      const paperMm =
        styleOverride?.paper_width === "58" ||
        styleOverride?.paper_width === "80"
          ? styleOverride.paper_width
          : cartRoot.dataset.posReceiptWidth === "58"
            ? "58"
            : "80";
      const pageWidth = `${paperMm}mm`;
      const fontSize =
        styleOverride?.[`size_px_${paperMm}`] ||
        (paperMm === "58"
          ? cartRoot.dataset.posReceiptFontPx58
          : cartRoot.dataset.posReceiptFontPx80) ||
        "11.5px";
      const fontWeight =
        styleOverride?.weight_css ||
        cartRoot.dataset.posReceiptFontWeightCss ||
        "400";
      const frame = document.createElement("iframe");
      frame.setAttribute("aria-hidden", "true");
      frame.style.cssText =
        "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
      document.body.appendChild(frame);
      const doc = frame.contentDocument || frame.contentWindow?.document;
      if (!doc) {
        frame.remove();
        return false;
      }
      const esc = (v) =>
        String(v ?? "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
      const prepared = printer?.maskReceiptForPrint
        ? printer.maskReceiptForPrint(text, ticket)
        : { text };
      doc.open();
      doc.write(`<!doctype html><html><head><title>Receipt (${label})</title>
<link rel="stylesheet" href="/static/css/fonts.css?v=20261019-assets">
<style>
  @page { size: ${pageWidth} auto; margin: 2mm; }
  body {
    width: ${pageWidth}; margin: 0; padding: 2mm;
    font-family: "Manrope", system-ui, sans-serif;
    font-size: ${fontSize}; font-weight: 900;
    color: #000 !important;
    background: #fff;
    white-space: pre-wrap;
    -webkit-font-smoothing: none;
    text-shadow: 0.35px 0 0 #000, -0.35px 0 0 #000;
  }
</style></head><body><pre>${esc(prepared.text)}</pre>
<script>window.onload = () => { window.focus(); window.print(); };<\/script>
</body></html>`);
      doc.close();
      window.setTimeout(() => frame.remove(), 4000);
      return true;
    };

    const money = (value) =>
      `KSh ${Number(value || 0).toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`;

    const roundMoney = (value) => Math.round(Number(value || 0) * 100) / 100;

    const roundQty = (value) => {
      const num = Number(value);
      if (!Number.isFinite(num)) return 0;
      return Math.round(num * 1000) / 1000;
    };

    const readItemFromEl = (el) => {
      const id = el?.getAttribute?.("data-item-id") || el?.dataset?.itemId || "";
      const listPrice = roundMoney(
        el?.getAttribute?.("data-item-list-price") ||
          el?.dataset?.itemListPrice ||
          el?.getAttribute?.("data-item-price") ||
          el?.dataset?.itemPrice ||
          0
      );
      const minPrice = roundMoney(
        el?.getAttribute?.("data-item-min-price") || el?.dataset?.itemMinPrice || 0
      );
      const price = roundMoney(
        el?.getAttribute?.("data-item-price") || el?.dataset?.itemPrice || listPrice
      );
      const discountMinQty = Math.max(
        0,
        Math.floor(
          Number(
            el?.getAttribute?.("data-item-discount-min-qty") ||
              el?.dataset?.itemDiscountMinQty ||
              0
          )
        )
      );
      const discountAmount = roundMoney(
        el?.getAttribute?.("data-item-discount-amount") ||
          el?.dataset?.itemDiscountAmount ||
          0
      );
      return {
        id,
        name:
          el?.getAttribute?.("data-item-name") ||
          el?.dataset?.itemName ||
          "Item",
        category:
          el?.getAttribute?.("data-item-category") ||
          el?.dataset?.itemCategory ||
          "",
        description:
          el?.getAttribute?.("data-item-description") ||
          el?.dataset?.itemDescription ||
          "",
        price,
        listPrice,
        minPrice,
        discountMinQty,
        discountAmount,
        stock: Math.max(
          0,
          roundQty(
            el?.getAttribute?.("data-item-stock") || el?.dataset?.itemStock || 0
          )
        ),
        image:
          el?.getAttribute?.("data-item-image") || el?.dataset?.itemImage || "",
        trackSerial:
          (el?.getAttribute?.("data-item-track-serial") ||
            el?.dataset?.itemTrackSerial ||
            "") === "1",
      };
    };

    const clampPrice = (price, minPrice, listPrice) => {
      let next = roundMoney(price);
      if (!Number.isFinite(next)) next = listPrice > 0 ? listPrice : 0;
      if (selectedKind() === "trade_in") {
        return Math.max(0, next);
      }
      if (!discountEnabled) {
        return listPrice > 0 ? listPrice : Math.max(0, next);
      }
      const min = roundMoney(minPrice);
      if (min > 0 && next < min) next = min;
      if (next < 0) next = 0;
      return next;
    };

    const loadCart = () => {
      try {
        const raw = sessionStorage.getItem(storageKey);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return;
        parsed.forEach((line) => {
          if (!line?.id) return;
          const qty = Math.max(0.001, roundQty(Number(line.qty) || 1));
          const listPrice = Number(line.listPrice || line.price) || 0;
          const minPrice = Number(line.minPrice) || 0;
          const storedPrice = Number(line.price) || 0;
          const serials = Array.isArray(line.serials)
            ? line.serials
                .map((s) => String(s || "").trim().toUpperCase())
                .filter(Boolean)
            : [];
          const trackSerial = Boolean(line.trackSerial) || serials.length > 0;
          cart.set(String(line.id), {
            id: String(line.id),
            name: String(line.name || "Item"),
            category: String(line.category || ""),
            description: String(line.description || ""),
            price: discountEnabled
              ? clampPrice(storedPrice, minPrice, listPrice)
              : listPrice,
            listPrice,
            minPrice,
            discountMinQty: Math.max(
              0,
              Math.floor(Number(line.discountMinQty) || 0)
            ),
            discountAmount: roundMoney(line.discountAmount || 0),
            stock: Math.max(0, roundQty(line.stock)),
            image: String(line.image || ""),
            qty: trackSerial && serials.length ? serials.length : qty,
            trackSerial,
            serials,
          });
        });
      } catch (_error) {
        /* ignore bad storage */
      }
    };

    const saveCart = () => {
      try {
        sessionStorage.setItem(
          storageKey,
          JSON.stringify([...cart.values()].map((line) => ({ ...line })))
        );
      } catch (_error) {
        /* ignore quota */
      }
    };

    const clampQty = (qty, stock) => {
      let next = roundQty(qty);
      if (!Number.isFinite(next) || next <= 0) next = 0;
      if (
        stockTrackingEnabled &&
        selectedKind() !== "trade_in" &&
        stock > 0 &&
        next > stock
      ) {
        next = stock;
      }
      return next;
    };

    // Quantity typed into a stepper before the item is in the cart yet —
    // decimals allowed, kept positive, and capped at tracked stock.
    const pendingQty = (input, stock) => {
      const qty = clampQty(input?.value, stock);
      return qty > 0 ? qty : 1;
    };

    const volumeUnitPrice = (listPrice, minPrice, qty, discountMinQty, discountAmount) => {
      const list = roundMoney(listPrice);
      const min = roundMoney(minPrice);
      const threshold = Math.max(0, Math.floor(Number(discountMinQty) || 0));
      const amount = roundMoney(discountAmount);
      if (!discountEnabled || threshold <= 0 || amount <= 0) return list;
      if (roundQty(qty) < threshold) return list;
      return clampPrice(list - amount, min, list);
    };

    const setQty = (id, qty, meta = null) => {
      if (!id) return;
      const existing = cart.get(id);
      const stock = meta?.stock ?? existing?.stock ?? 0;
      const nextQty = clampQty(qty, stock);
      if (nextQty <= 0) {
        cart.delete(id);
        return;
      }
      const base =
        existing ||
        meta || {
          id,
          name: "Item",
          category: "",
          description: "",
          price: 0,
          listPrice: 0,
          minPrice: 0,
          discountMinQty: 0,
          discountAmount: 0,
          image: "",
        };
      const listPrice = roundMoney(base.listPrice ?? base.price ?? 0);
      const minPrice = roundMoney(base.minPrice ?? 0);
      const discountMinQty = Math.max(
        0,
        Math.floor(
          Number(meta?.discountMinQty ?? base.discountMinQty ?? 0)
        )
      );
      const discountAmount = roundMoney(
        meta?.discountAmount ?? base.discountAmount ?? 0
      );
      const volumePrice = volumeUnitPrice(
        listPrice,
        minPrice,
        nextQty,
        discountMinQty,
        discountAmount
      );
      // Prefer volume pricing when configured; otherwise keep the current sale price.
      const preferred =
        discountMinQty > 0 && discountAmount > 0
          ? volumePrice
          : base.price ?? listPrice;
      const price = clampPrice(preferred, minPrice, listPrice);
      const trackSerial = Boolean(base.trackSerial || meta?.trackSerial);
      let serials = Array.isArray(meta?.serials)
        ? meta.serials
        : Array.isArray(base.serials)
          ? base.serials
          : [];
      serials = serials
        .map((s) => String(s || "").trim().toUpperCase())
        .filter(Boolean);
      if (trackSerial) {
        if (meta?.serials) {
          serials = meta.serials
            .map((s) => String(s || "").trim().toUpperCase())
            .filter(Boolean);
        } else if (serials.length > nextQty) {
          serials = serials.slice(0, nextQty);
        }
      } else {
        serials = [];
      }
      cart.set(id, {
        id,
        name: base.name || "Item",
        category: base.category || "",
        description: base.description || "",
        price,
        listPrice,
        minPrice,
        discountMinQty,
        discountAmount,
        stock,
        image: base.image || "",
        qty: trackSerial && serials.length ? serials.length : nextQty,
        trackSerial,
        serials,
      });
    };

    const setLinePrice = (id, price, meta = null) => {
      if (!id) return null;
      if (selectedKind() === "trade_in") {
        const existing = cart.get(id);
        const nextPrice = Math.max(0, roundMoney(price));
        if (existing) {
          existing.price = nextPrice;
          cart.set(id, existing);
        }
        return nextPrice;
      }
      if (!discountEnabled) {
        const existing = cart.get(id);
        const listPrice = roundMoney(
          existing?.listPrice ?? meta?.listPrice ?? existing?.price ?? 0
        );
        if (existing) {
          existing.price = listPrice;
          cart.set(id, existing);
        }
        return listPrice;
      }
      const existing = cart.get(id);
      const base = existing || meta;
      if (!base) return null;
      const listPrice = roundMoney(base.listPrice ?? meta?.listPrice ?? base.price ?? 0);
      const minPrice = roundMoney(base.minPrice ?? meta?.minPrice ?? 0);
      const nextPrice = clampPrice(price, minPrice, listPrice);
      if (existing) {
        existing.price = nextPrice;
        existing.listPrice = listPrice;
        existing.minPrice = minPrice;
        cart.set(id, existing);
      }
      return nextPrice;
    };

    const syncPriceHint = (minPrice, salePrice, listPrice) => {
      if (!productPriceHint) return;
      if (!discountEnabled) {
        productPriceHint.textContent =
          listPrice > 0 ? `List ${money(listPrice)}` : "";
        productPriceHint.classList.remove("is-discount");
        return;
      }
      const parts = [];
      if (minPrice > 0) parts.push(`Min ${money(minPrice)}`);
      if (listPrice > 0) parts.push(`List ${money(listPrice)}`);
      if (salePrice + 0.0001 < listPrice) parts.push("Discount applied");
      else if (listPrice > 0 && salePrice > listPrice + 0.0001) {
        parts.push("Price increased");
      }
      productPriceHint.textContent = parts.join(" · ");
      productPriceHint.classList.toggle(
        "is-discount",
        salePrice + 0.0001 < listPrice
      );
    };

    const syncCardControls = () => {
      cartRoot.querySelectorAll("[data-cart-item]").forEach((card) => {
        const id = card.getAttribute("data-item-id");
        const line = id ? cart.get(id) : null;
        const inCart = Boolean(line);
        const addBtn = card.querySelector("[data-cart-add]");
        const qtyWrap = card.querySelector("[data-cart-qty-wrap]");
        const qtyInput = card.querySelector("[data-cart-qty-input]");
        const stock = Math.max(0, roundQty(card.getAttribute("data-item-stock")));

        card.classList.toggle("is-in-cart", inCart);
        if (addBtn) {
          const label = addBtn.querySelector(".shop-floor-add-label");
          const short = addBtn.querySelector(".shop-floor-add-short");
          if (stockTrackingEnabled && selectedKind() !== "trade_in" && stock <= 0) {
            addBtn.hidden = false;
            addBtn.disabled = true;
            if (label) label.textContent = "Out of stock";
            if (short) short.textContent = "Out";
          } else {
            addBtn.hidden = inCart;
            addBtn.disabled = false;
            if (label) label.textContent = "Add to cart";
            if (short) short.textContent = "Add";
          }
        }
        if (qtyWrap) qtyWrap.hidden = !inCart;
        if (qtyInput && line) {
          if (stockTrackingEnabled && stock > 0) qtyInput.max = String(stock);
          else qtyInput.removeAttribute("max");
          qtyInput.value = String(line.qty);
        }
        const inc = card.querySelector('[data-cart-qty="inc"]');
        if (inc && line) {
          inc.disabled = stockTrackingEnabled && stock > 0 && line.qty >= stock;
        }
      });
    };

    const syncProductControls = () => {
      if (!productModal || !activeProductId) return;
      const line = cart.get(activeProductId);
      const stock = Math.max(
        0,
        roundQty(productModal.dataset.itemStock || line?.stock || 0)
      );
      const inCart = Boolean(line);
      const outOfStock =
        stockTrackingEnabled && selectedKind() !== "trade_in" && stock <= 0;
      const trackSerial =
        productModal.dataset.itemTrackSerial === "1" || Boolean(line?.trackSerial);
      if (productAdd) {
        productAdd.hidden = inCart || outOfStock;
        productAdd.disabled = outOfStock;
        const label = productAdd.querySelector("span");
        if (label) label.textContent = outOfStock ? "Out of stock" : "Add to cart";
      }
      if (productQtyWrap) {
        productQtyWrap.hidden = outOfStock || (trackSerial && !inCart);
      }
      if (productQtyInput) {
        if (stockTrackingEnabled && stock > 0) productQtyInput.max = String(stock);
        else productQtyInput.removeAttribute("max");
        if (line) {
          productQtyInput.value = String(line.qty);
        } else if (document.activeElement !== productQtyInput) {
          productQtyInput.value = String(pendingQty(productQtyInput, stock));
        }
      }
      const inc = productModal.querySelector('[data-product-qty="inc"]');
      if (inc) {
        const current = line ? line.qty : roundQty(productQtyInput?.value);
        inc.disabled = stockTrackingEnabled && stock > 0 && current >= stock;
      }

      const listPrice = roundMoney(
        productModal.dataset.itemListPrice || line?.listPrice || 0
      );
      const minPrice = roundMoney(
        productModal.dataset.itemMinPrice || line?.minPrice || 0
      );
      const salePrice = roundMoney(
        discountEnabled
          ? line?.price ?? productModal.dataset.itemPrice ?? listPrice
          : listPrice
      );
      if (productPriceInput && document.activeElement !== productPriceInput) {
        productPriceInput.min = String(
          discountEnabled ? minPrice || 0 : listPrice || 0
        );
        if (discountEnabled) productPriceInput.removeAttribute("max");
        else productPriceInput.max = String(listPrice || 0);
        productPriceInput.value = salePrice.toFixed(2);
        productPriceInput.readOnly = !discountEnabled;
        productPriceInput.tabIndex = discountEnabled ? 0 : -1;
      }
      productPriceInput
        ?.closest(".shop-product-price-field")
        ?.classList.toggle("is-locked", !discountEnabled);
      syncPriceHint(minPrice, salePrice, listPrice);
    };

    const syncFab = () => {
      const count = cart.size;
      if (countEl) {
        countEl.textContent = String(count);
        countEl.hidden = count <= 0;
      }
      if (fab) {
        fab.setAttribute(
          "aria-label",
          count ? `Open cart, ${count} item${count === 1 ? "" : "s"}` : "Open cart"
        );
      }
    };

    const setCartOpen = (open) => {
      if (!drawer || !overlay) return;
      overlay.classList.toggle("is-open", open);
      drawer.classList.toggle("is-open", open);
      fab?.classList.toggle("is-open", open);
      overlay.setAttribute("aria-hidden", String(!open));
      drawer.setAttribute("aria-hidden", String(!open));
      fab?.setAttribute("aria-expanded", String(open));
      body.classList.toggle("shop-cart-open", open);
      if (open) {
        syncPrinterRequiredNotice();
        refreshIcons();
        drawer.querySelector("[data-cart-close]")?.focus();
      }
    };

    const setProductOpen = (open) => {
      if (!productModal) return;
      productModal.classList.toggle("is-open", open);
      productModal.setAttribute("aria-hidden", String(!open));
      body.classList.toggle("shop-product-open", open);
      if (!open) activeProductId = "";
      if (open) {
        refreshIcons();
        productModal.querySelector("[data-product-close]")?.focus();
      }
    };

    const openProduct = (card) => {
      if (!productModal || !card) return;
      const item = readItemFromEl(card);
      if (!item.id) return;
      const existing = cart.get(item.id);
      const salePrice = roundMoney(
        discountEnabled
          ? existing?.price ??
              clampPrice(item.listPrice, item.minPrice, item.listPrice)
          : item.listPrice
      );
      activeProductId = item.id;
      productModal.dataset.itemId = item.id;
      productModal.dataset.itemStock = String(item.stock);
      productModal.dataset.itemName = item.name;
      productModal.dataset.itemCategory = item.category;
      productModal.dataset.itemDescription = item.description;
      productModal.dataset.itemPrice = String(salePrice);
      productModal.dataset.itemListPrice = String(item.listPrice);
      productModal.dataset.itemMinPrice = String(item.minPrice);
      productModal.dataset.itemImage = item.image;
      productModal.dataset.itemTrackSerial = item.trackSerial ? "1" : "0";

      if (productCategory) productCategory.textContent = item.category || "";
      if (productName) productName.textContent = item.name;
      if (productDescription) productDescription.textContent = item.description || "";
      if (productPriceInput) {
        productPriceInput.min = String(
          discountEnabled ? item.minPrice || 0 : item.listPrice || 0
        );
        if (discountEnabled) productPriceInput.removeAttribute("max");
        else productPriceInput.max = String(item.listPrice || 0);
        productPriceInput.value = salePrice.toFixed(2);
        productPriceInput.readOnly = !discountEnabled;
        productPriceInput.tabIndex = discountEnabled ? 0 : -1;
      }
      productPriceInput
        ?.closest(".shop-product-price-field")
        ?.classList.toggle("is-locked", !discountEnabled);
      if (productQtyInput) {
        productQtyInput.value = String(existing?.qty || 1);
      }
      syncPriceHint(item.minPrice, salePrice, item.listPrice);
      if (productStock) {
        if (stockTrackingEnabled) {
          productStock.hidden = false;
          productStock.textContent =
            item.stock > 0 ? `${item.stock} in stock` : "Out of stock";
          productStock.classList.toggle("is-empty", item.stock <= 0);
        } else {
          productStock.hidden = true;
        }
      }
      if (productMedia) {
        if (item.image) {
          productMedia.innerHTML = `<img src="${item.image}" alt="">`;
        } else {
          productMedia.innerHTML =
            '<span class="shop-product-modal-media-fallback"><i data-lucide="package" aria-hidden="true"></i></span>';
        }
      }
      syncProductControls();
      setProductOpen(true);
    };

    const openProductFromLine = (line) => {
      if (!productModal || !line?.id) return;
      const proxy = document.createElement("div");
      proxy.setAttribute("data-item-id", line.id);
      proxy.setAttribute("data-item-name", line.name || "Item");
      proxy.setAttribute("data-item-category", line.category || "");
      proxy.setAttribute("data-item-description", line.description || "");
      proxy.setAttribute("data-item-price", String(line.listPrice || line.price || 0));
      proxy.setAttribute("data-item-list-price", String(line.listPrice || line.price || 0));
      proxy.setAttribute("data-item-min-price", String(line.minPrice || 0));
      proxy.setAttribute("data-item-stock", String(line.stock || 0));
      proxy.setAttribute(
        "data-item-track-serial",
        line.trackSerial || (line.serials && line.serials.length) ? "1" : "0"
      );
      if (line.image) proxy.setAttribute("data-item-image", line.image);
      openProduct(proxy);
    };

    const renderCart = () => {
      if (!linesEl || !footEl) return;
      const lines = [...cart.values()];
      const count = lines.length;
      const hasItems = count > 0;

      linesEl.hidden = !hasItems;
      footEl.hidden = !hasItems;

      if (itemLabelEl) {
        itemLabelEl.textContent = hasItems
          ? `${count} item${count === 1 ? "" : "s"}`
          : "No items yet";
      }

      const subtotal = lines.reduce((sum, line) => sum + line.price * line.qty, 0);
      const taxAmount = cartTaxAmount(subtotal);
      const total = roundMoney(subtotal + taxAmount);
      if (subtotalEl) subtotalEl.textContent = money(subtotal);
      if (taxEl) taxEl.textContent = money(taxAmount);
      if (taxLabelEl) {
        taxLabelEl.textContent =
          taxEnabled && taxPercent > 0
            ? `Tax (${taxPercent.toFixed(2).replace(/\.?0+$/, "")}%)`
            : "Tax";
      }
      if (subtotalRow) subtotalRow.hidden = !taxEnabled;
      if (taxRow) taxRow.hidden = !taxEnabled;
      if (totalEl) totalEl.textContent = money(total);

      linesEl.innerHTML = "";
      lines.forEach((line) => {
        const li = document.createElement("li");
        li.className = "shop-cart-line";
        li.dataset.lineId = line.id;

        const media = document.createElement("button");
        media.type = "button";
        media.className = "shop-cart-line-media";
        media.dataset.cartLinePreview = "";
        media.setAttribute("aria-label", `View ${line.name}`);
        if (line.image) {
          const img = document.createElement("img");
          img.src = line.image;
          img.alt = "";
          media.appendChild(img);
        } else {
          media.innerHTML = '<i data-lucide="package" aria-hidden="true"></i>';
        }

        const copy = document.createElement("div");
        copy.className = "shop-cart-line-copy";

        const name = document.createElement("strong");
        name.textContent = line.name;

        const canEditPrice = discountEnabled || selectedKind() === "trade_in";
        const priceField = document.createElement("label");
        priceField.className = "shop-cart-price-field";
        if (!canEditPrice) priceField.classList.add("is-locked");
        const pricePrefix = document.createElement("span");
        pricePrefix.textContent = "KSh";
        const priceInput = document.createElement("input");
        priceInput.type = "text";
        priceInput.className = "shop-cart-price-input";
        priceInput.min = String(
          selectedKind() === "trade_in"
            ? 0.01
            : canEditPrice
              ? line.minPrice || 0
              : line.listPrice || 0
        );
        if (!canEditPrice) priceInput.max = String(line.listPrice || 0);
        else priceInput.removeAttribute("max");
        priceInput.step = "0.01";
        priceInput.inputMode = "decimal";
        priceInput.value = roundMoney(
          canEditPrice ? line.price : line.listPrice || line.price
        ).toFixed(2);
        priceInput.readOnly = !canEditPrice;
        priceInput.tabIndex = canEditPrice ? 0 : -1;
        priceInput.setAttribute(
          "aria-label",
          selectedKind() === "trade_in"
            ? `Amount owed for ${line.name}`
            : `Sale price for ${line.name}`
        );
        if (!canEditPrice) {
          priceInput.setAttribute("aria-readonly", "true");
        }
        priceInput.dataset.cartPriceInput = "";
        priceField.append(pricePrefix, priceInput);

        const priceHint = document.createElement("span");
        priceHint.className = "shop-cart-price-hint";
        if (selectedKind() === "trade_in") {
          priceHint.textContent = "Amount owed to the sending shop";
        } else if (discountEnabled && line.minPrice > 0 && line.listPrice > 0) {
          let hint = `Min ${money(line.minPrice)} · List ${money(line.listPrice)}`;
          if (line.discountMinQty > 0 && line.discountAmount > 0) {
            hint += ` · From ${line.discountMinQty}+: −${money(line.discountAmount)}`;
          }
          if (line.price + 0.0001 < line.listPrice) {
            hint += " · Discount applied";
            priceHint.classList.add("is-discount");
          } else if (line.price > line.listPrice + 0.0001) {
            hint += " · Price increased";
          }
          priceHint.textContent = hint;
        } else if (discountEnabled && line.minPrice > 0) {
          priceHint.textContent = `Min ${money(line.minPrice)}`;
        } else if (discountEnabled && line.listPrice > 0) {
          priceHint.textContent = `List ${money(line.listPrice)}`;
        }

        copy.append(name, priceField);
        if (priceHint.textContent) copy.append(priceHint);
        if (line.trackSerial || (line.serials && line.serials.length)) {
          const editSerials = document.createElement("button");
          editSerials.type = "button";
          editSerials.className = "shop-cart-line-serial-edit";
          editSerials.dataset.cartEditSerials = "";
          const serialCount = (line.serials || []).length;
          editSerials.textContent =
            serialCount > 0
              ? `Edit serials (${serialCount})`
              : "Add serials";
          copy.appendChild(editSerials);
        }

        const controls = document.createElement("div");
        controls.className = "shop-cart-line-controls";

        const qtyWrap = document.createElement("div");
        qtyWrap.className = "shop-cart-qty";

        const dec = document.createElement("button");
        dec.type = "button";
        dec.setAttribute("aria-label", "Decrease quantity");
        dec.dataset.cartQty = "dec";
        dec.innerHTML = '<i data-lucide="minus" aria-hidden="true"></i>';

        const qtyInput = document.createElement("input");
        qtyInput.type = "text";
        qtyInput.className = "shop-cart-qty-input";
        qtyInput.min = "0.001";
        qtyInput.step = "0.001";
        if (stockTrackingEnabled && selectedKind() !== "trade_in" && line.stock > 0) {
          qtyInput.max = String(line.stock);
        }
        qtyInput.value = String(line.qty);
        qtyInput.inputMode = "decimal";
        qtyInput.setAttribute("aria-label", "Quantity");
        qtyInput.dataset.cartQtyInput = "";

        const inc = document.createElement("button");
        inc.type = "button";
        inc.setAttribute("aria-label", "Increase quantity");
        inc.dataset.cartQty = "inc";
        inc.innerHTML = '<i data-lucide="plus" aria-hidden="true"></i>';
        if (
          stockTrackingEnabled &&
          selectedKind() !== "trade_in" &&
          line.stock > 0 &&
          line.qty >= line.stock
        ) {
          inc.disabled = true;
        }

        qtyWrap.append(dec, qtyInput, inc);

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "shop-cart-line-remove";
        remove.dataset.cartRemove = "";
        remove.textContent = "Remove";

        controls.append(qtyWrap, remove);
        li.append(media, copy, controls);
        linesEl.appendChild(li);
      });

      syncCardControls();
      syncProductControls();
      syncFab();
      saveCart();
      refreshIcons();
      syncCheckoutMode();
    };


    const setSerialSaleStatus = (message = "", { error = false } = {}) => {
      if (!serialSaleStatus) return;
      if (!message) {
        serialSaleStatus.hidden = true;
        serialSaleStatus.textContent = "";
        return;
      }
      serialSaleStatus.hidden = false;
      serialSaleStatus.textContent = message;
      serialSaleStatus.classList.toggle("is-error", error);
    };

    const isSerialSaleLast4Mode = () => serialSaleMatchMode === "last4";

    const serialSalePlaceholder = () =>
      isSerialSaleLast4Mode()
        ? "Type last 4 digits…"
        : "Search serial number";

    const syncSerialSaleMatchModeUi = () => {
      serialSaleMatchModeRoot
        ?.querySelectorAll("[data-serial-sale-match]")
        .forEach((btn) => {
          const mode = btn.getAttribute("data-serial-sale-match");
          const active = mode === serialSaleMatchMode;
          btn.classList.toggle("is-active", active);
          btn.setAttribute("aria-pressed", active ? "true" : "false");
        });
      if (serialSaleHint) {
        serialSaleHint.textContent = isSerialSaleLast4Mode()
          ? "Type last 4 digits or scan full serial — press Enter after each. Add to cart when finished."
          : "Scan continuously — press Enter after each serial. Add to cart when finished.";
      }
      syncSerialSaleInputMode(serialSaleEntry);
    };

    const syncSerialSaleInputMode = (input) => {
      if (!input) return;
      const resolved = input.dataset.serialResolved === "1";
      if (isSerialSaleLast4Mode() && !resolved) {
        input.maxLength = 4;
        input.placeholder = serialSalePlaceholder();
        input.setAttribute("aria-label", "Last 4 digits of serial number");
      } else {
        input.removeAttribute("maxLength");
        input.placeholder = resolved && isSerialSaleLast4Mode()
          ? "Full serial selected"
          : serialSalePlaceholder();
        input.setAttribute("aria-label", "Serial number");
      }
    };

    const setSerialSaleMatchMode = (mode, { refocus = true } = {}) => {
      const next = mode === "last4" ? "last4" : "full";
      if (next === serialSaleMatchMode) {
        syncSerialSaleMatchModeUi();
        return;
      }
      serialSaleMatchMode = next;
      try {
        window.sessionStorage?.setItem(SERIAL_SALE_MATCH_KEY, serialSaleMatchMode);
      } catch (_err) {
        /* ignore */
      }
      if (serialSaleEntry) {
        hideSerialSaleSuggest(serialSaleEntryWrap);
        if (isSerialSaleLast4Mode()) {
          if (serialSaleEntry.dataset.serialResolved !== "1") {
            const raw = String(serialSaleEntry.value || "")
              .trim()
              .toUpperCase()
              .replace(/[^A-Z0-9]/g, "");
            serialSaleEntry.value = raw.slice(-4);
            delete serialSaleEntry.dataset.serialResolved;
          }
        } else {
          delete serialSaleEntry.dataset.serialResolved;
        }
        syncSerialSaleInputMode(serialSaleEntry);
      }
      syncSerialSaleMatchModeUi();
      syncSerialSaleCount();
      setSerialSaleStatus(
        isSerialSaleLast4Mode()
          ? "Last 4 mode — select a suggestion to fill the full serial."
          : "Full serial search mode."
      );
      if (refocus) {
        focusSerialSaleEntry();
      }
    };

    const hideSerialSaleSuggest = (root) => {
      const suggest = root?.querySelector?.("[data-serial-sale-suggest]");
      if (!suggest) return;
      suggest.hidden = true;
      suggest.innerHTML = "";
    };

    const collectSerialSaleValues = () => {
      if (!serialSaleScanned) return [];
      return [...serialSaleScanned.querySelectorAll("[data-serial-sale-scanned-value]")]
        .map((el) => String(el.textContent || "").trim().toUpperCase())
        .filter(Boolean);
    };

    const serialSaleScannedEmpty = serialSaleModal?.querySelector(
      "[data-serial-sale-scanned-empty]"
    );

    const syncSerialSaleCount = () => {
      const count = collectSerialSaleValues().length;
      if (serialSaleCount) {
        serialSaleCount.textContent =
          count === 1 ? "1 selected" : `${count} selected`;
        serialSaleCount.classList.toggle("is-active", count > 0);
      }
      if (serialSaleScanned) {
        serialSaleScanned.hidden = count === 0;
      }
      if (serialSaleScannedEmpty) {
        serialSaleScannedEmpty.hidden = count > 0;
      }
      return count;
    };

    const clearSerialSaleEntry = () => {
      if (!serialSaleEntry) return;
      serialSaleEntry.value = "";
      serialSaleEntry.classList.remove("is-duplicate");
      delete serialSaleEntry.dataset.serialResolved;
      syncSerialSaleInputMode(serialSaleEntry);
      hideSerialSaleSuggest(serialSaleEntryWrap);
    };

    const focusSerialSaleEntry = () => {
      serialSaleEntry?.focus({ preventScroll: true });
    };

    const createSerialSaleScannedItem = (serial) => {
      if (!serialSaleScanned || !serial) return null;
      const li = document.createElement("li");
      li.className = "shop-serial-scanned-item";
      li.dataset.serialValue = serial;

      const mark = document.createElement("span");
      mark.className = "shop-serial-scanned-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.innerHTML = '<i data-lucide="check"></i>';
      li.appendChild(mark);

      const value = document.createElement("span");
      value.className = "shop-serial-scanned-value";
      value.setAttribute("data-serial-sale-scanned-value", "");
      value.textContent = serial;
      li.appendChild(value);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "shop-serial-scanned-remove";
      remove.setAttribute("data-serial-sale-scanned-remove", "");
      remove.setAttribute("aria-label", `Remove ${serial}`);
      remove.innerHTML = '<i data-lucide="trash-2" aria-hidden="true"></i>';
      li.appendChild(remove);

      serialSaleScanned.prepend(li);
      syncSerialSaleCount();
      refreshIcons();
      return li;
    };

    const applySerialSaleChoice = (serial, { fromSuggest = false } = {}) => {
      const value = String(serial || "").trim().toUpperCase();
      if (!value) return false;
      if (collectSerialSaleValues().includes(value)) {
        setSerialSaleStatus("That serial is already selected.", { error: true });
        return false;
      }
      if (
        stockTrackingEnabled &&
        serialSaleItem.stock > 0 &&
        collectSerialSaleValues().length >= serialSaleItem.stock
      ) {
        setSerialSaleStatus("No more stock available for another serial.", {
          error: true,
        });
        return false;
      }
      createSerialSaleScannedItem(value);
      clearSerialSaleEntry();
      setSerialSaleStatus(
        fromSuggest && isSerialSaleLast4Mode() ? `Added ${value}.` : ""
      );
      focusSerialSaleEntry();
      return true;
    };

    let serialSaleCommitBusy = false;
    let lastSerialSaleCommitSerial = "";
    let lastSerialSaleCommitAt = 0;
    const SERIAL_SALE_COMMIT_DEDUPE_MS = 1000;

    const dispatchSerialSaleCommitSettled = (detail = {}) => {
      serialSaleEntry?.dispatchEvent(
        new CustomEvent("myshop:serial-commit-settled", { bubbles: true, detail })
      );
    };

    const commitSerialSaleEntry = async ({ serial: serialOverride } = {}) => {
      if (serialSaleCommitBusy || !serialSaleEntry || !serialSaleItem?.id) {
        dispatchSerialSaleCommitSettled({ ok: false, reason: "busy" });
        return false;
      }

      let serial = String(serialOverride || serialSaleEntry.value || "")
        .trim()
        .toUpperCase();
      if (!serial) {
        dispatchSerialSaleCommitSettled({ ok: false, reason: "empty" });
        return false;
      }

      const now = Date.now();
      if (
        serial === lastSerialSaleCommitSerial &&
        now - lastSerialSaleCommitAt < SERIAL_SALE_COMMIT_DEDUPE_MS
      ) {
        clearSerialSaleEntry();
        focusSerialSaleEntry();
        dispatchSerialSaleCommitSettled({ ok: false, reason: "dedupe", serial });
        return false;
      }

      if (collectSerialSaleValues().includes(serial)) {
        clearSerialSaleEntry();
        serialSaleEntry.classList.add("is-duplicate");
        window.setTimeout(() => serialSaleEntry.classList.remove("is-duplicate"), 700);
        setSerialSaleStatus("That serial is already selected.", { error: true });
        focusSerialSaleEntry();
        dispatchSerialSaleCommitSettled({ ok: false, reason: "duplicate", serial });
        return false;
      }

      if (
        stockTrackingEnabled &&
        serialSaleItem.stock > 0 &&
        collectSerialSaleValues().length >= serialSaleItem.stock
      ) {
        setSerialSaleStatus("No more stock available for another serial.", {
          error: true,
        });
        dispatchSerialSaleCommitSettled({ ok: false, reason: "stock", serial });
        return false;
      }

      const resolved = serialSaleEntry.dataset.serialResolved === "1";
      // Clear immediately so the next scan never appends to the same field.
      clearSerialSaleEntry();

      serialSaleCommitBusy = true;
      let ok = false;
      try {
        const searchSaleSerials = async ({ query, match, exclude }) => {
          if (!serialSearchUrl || !serialSaleItem?.id || !shopId) {
            return { ok: false, results: [], error: "unavailable" };
          }
          const { searchSerialsOnlineOrCache } = await import("./offline/serials.js");
          return searchSerialsOnlineOrCache({
            url: serialSearchUrl,
            itemId: serialSaleItem.id,
            shopId,
            query,
            match,
            exclude,
            limit: 12,
          });
        };

        if (isSerialSaleLast4Mode() && !resolved && serial.length <= 4) {
          if (!serialSearchUrl) {
            setSerialSaleStatus("Select a full serial from the suggestions.", {
              error: true,
            });
            return false;
          }
          const data = await searchSaleSerials({
            query: serial.slice(-4),
            match: "last4",
            exclude: collectSerialSaleValues(),
          });
          const results = Array.isArray(data.results) ? data.results : [];
          if (results.length === 1) {
            ok = applySerialSaleChoice(results[0], { fromSuggest: true });
            if (ok) {
              lastSerialSaleCommitSerial = String(results[0]).trim().toUpperCase();
              lastSerialSaleCommitAt = Date.now();
            }
            return ok;
          }
          if (results.length > 1) {
            serialSaleEntry.value = serial;
            syncSerialSaleInputMode(serialSaleEntry);
            renderSerialSaleSuggest(serialSaleEntry, results);
            setSerialSaleStatus(
              "Several serials end with those digits — select one.",
              { error: true }
            );
            return false;
          }
          setSerialSaleStatus(
            data.offline
              ? "Offline — type or scan the full serial."
              : "No in-stock serial ends with those digits. Check and try again.",
            { error: true }
          );
          return false;
        }

        if (!serialSearchUrl) {
          ok = applySerialSaleChoice(serial);
          if (ok) {
            lastSerialSaleCommitSerial = serial;
            lastSerialSaleCommitAt = Date.now();
          }
          return ok;
        }

        const data = await searchSaleSerials({
          query: serial,
          match: isSerialSaleLast4Mode() ? "last4" : "contains",
          exclude: collectSerialSaleValues(),
        });
        if (!data.ok && !data.offline && !(data.results || []).length) {
          setSerialSaleStatus(data.error || "Could not search serials.", {
            error: true,
          });
          return false;
        }
        const results = Array.isArray(data.results) ? data.results : [];
        const exact = results.find((row) => String(row || "").toUpperCase() === serial);
        const picked = exact || (results.length === 1 ? results[0] : "");
        if (picked) {
          ok = applySerialSaleChoice(picked);
          if (ok) {
            lastSerialSaleCommitSerial = String(picked).trim().toUpperCase();
            lastSerialSaleCommitAt = Date.now();
          }
          return ok;
        }
        if (results.length > 1) {
          serialSaleEntry.value = serial;
          if (resolved) serialSaleEntry.dataset.serialResolved = "1";
          syncSerialSaleInputMode(serialSaleEntry);
          renderSerialSaleSuggest(serialSaleEntry, results);
          setSerialSaleStatus("Select a serial from the list.", { error: true });
          return false;
        }
        const { isOnline } = await import("./offline/connectivity.js");
        const offlineMiss = Boolean(data.offline || !isOnline());
        if (offlineMiss && serial.length > 4) {
          ok = applySerialSaleChoice(serial);
          if (ok) {
            lastSerialSaleCommitSerial = serial;
            lastSerialSaleCommitAt = Date.now();
            setSerialSaleStatus(
              "Offline — serial will be confirmed when you reconnect."
            );
          }
          return ok;
        }
        setSerialSaleStatus(
          offlineMiss
            ? "Offline — type or scan the full serial."
            : "Serial not available at this shop.",
          { error: true }
        );
        return false;
      } catch (_err) {
        const { isOnline } = await import("./offline/connectivity.js");
        if (!isOnline() && serial.length > 4) {
          ok = applySerialSaleChoice(serial);
          if (ok) {
            lastSerialSaleCommitSerial = serial;
            lastSerialSaleCommitAt = Date.now();
            setSerialSaleStatus(
              "Offline — serial will be confirmed when you reconnect."
            );
            return ok;
          }
        }
        setSerialSaleStatus("Could not verify serial. Try again.", { error: true });
        return false;
      } finally {
        serialSaleCommitBusy = false;
        focusSerialSaleEntry();
        dispatchSerialSaleCommitSettled({ ok, serial });
      }
    };

    const formatSerialSaleSuggestLabel = (serial) => {
      const value = String(serial || "");
      if (!isSerialSaleLast4Mode() || value.length <= 4) {
        return { main: value, meta: "In stock" };
      }
      const head = value.slice(0, -4);
      const tail = value.slice(-4);
      return {
        mainHtml: true,
        head,
        tail,
        meta: `Ends with ${tail} · In stock`,
      };
    };

    const renderSerialSaleSuggest = (input, results) => {
      const root = input?.closest?.("[data-serial-sale-search-root]");
      const suggest = root?.querySelector?.("[data-serial-sale-suggest]");
      if (!suggest) return;
      suggest.innerHTML = "";
      if (!results.length) {
        const empty = document.createElement("div");
        empty.className = "shop-serial-suggest-empty";
        empty.innerHTML = isSerialSaleLast4Mode()
          ? "<strong>No matching serials</strong><small>Type more of the last 4 digits from an in-stock unit</small>"
          : "<strong>No matching serials</strong><small>Available at this shop only</small>";
        suggest.appendChild(empty);
        suggest.hidden = false;
        return;
      }
      results.forEach((serial) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "shop-serial-suggest-option";
        const label = formatSerialSaleSuggestLabel(serial);
        if (label.mainHtml) {
          btn.innerHTML =
            '<strong><span class="shop-serial-suggest-head"></span><span class="shop-serial-suggest-tail"></span></strong><small></small>';
          btn.querySelector(".shop-serial-suggest-head").textContent = label.head;
          btn.querySelector(".shop-serial-suggest-tail").textContent = label.tail;
          btn.querySelector("small").textContent = label.meta;
        } else {
          btn.innerHTML = "<strong></strong><small></small>";
          btn.querySelector("strong").textContent = label.main;
          btn.querySelector("small").textContent = label.meta;
        }
        btn.addEventListener("mousedown", (event) => {
          event.preventDefault();
          applySerialSaleChoice(serial, { fromSuggest: true });
        });
        suggest.appendChild(btn);
      });
      suggest.hidden = false;
    };

    const runSerialSaleSearch = async (input) => {
      if (!serialSearchUrl || !serialSaleItem?.id || !shopId) return;
      if (isSerialSaleLast4Mode() && input.dataset.serialResolved === "1") {
        hideSerialSaleSuggest(serialSaleEntryWrap);
        return;
      }
      const q = String(input.value || "").trim().toUpperCase();
      if (!q) {
        hideSerialSaleSuggest(serialSaleEntryWrap);
        return;
      }
      const others = new Set(collectSerialSaleValues());
      if (q && others.has(q)) {
        const suggest = serialSaleEntryWrap?.querySelector?.("[data-serial-sale-suggest]");
        if (suggest) {
          suggest.innerHTML =
            "<div class=\"shop-serial-suggest-empty\"><strong>Already selected</strong><small>That serial is already in the list</small></div>";
          suggest.hidden = false;
        }
        return;
      }
      const seq = ++serialSaleSearchSeq;
      try {
        const { searchSerialsOnlineOrCache } = await import("./offline/serials.js");
        const data = await searchSerialsOnlineOrCache({
          url: serialSearchUrl,
          itemId: serialSaleItem.id,
          shopId,
          query: q,
          match: isSerialSaleLast4Mode() ? "last4" : "contains",
          exclude: [...others],
          limit: 12,
        });
        if (seq !== serialSaleSearchSeq) return;
        const results = Array.isArray(data.results) ? data.results : [];
        if (!data.ok && !results.length) {
          hideSerialSaleSuggest(serialSaleEntryWrap);
          if (data.offline) {
            setSerialSaleStatus(
              "Offline — type or scan a full serial if search has no saved list.",
              { error: true }
            );
          } else if (data.error) {
            setSerialSaleStatus(data.error, { error: true });
          }
          return;
        }
        renderSerialSaleSuggest(input, results);
        if (data.fromCache) {
          setSerialSaleStatus("Offline — showing saved serials for this item.");
        }
      } catch (_error) {
        if (seq !== serialSaleSearchSeq) return;
        hideSerialSaleSuggest(serialSaleEntryWrap);
      }
    };

    const queueSerialSaleSearch = (input) => {
      window.clearTimeout(serialSaleSearchTimer);
      serialSaleSearchTimer = window.setTimeout(() => runSerialSaleSearch(input), 220);
    };

    const resetSerialSaleModal = (serials = []) => {
      if (serialSaleScanned) serialSaleScanned.innerHTML = "";
      clearSerialSaleEntry();
      serials
        .map((serial) => String(serial || "").trim().toUpperCase())
        .filter(Boolean)
        .forEach((serial) => createSerialSaleScannedItem(serial));
      syncSerialSaleMatchModeUi();
      syncSerialSaleCount();
    };

    const setSerialSaleOpen = (open) => {
      if (!serialSaleModal) return;
      serialSaleModal.classList.toggle("is-open", open);
      serialSaleModal.setAttribute("aria-hidden", String(!open));
      body.classList.toggle("shop-serial-sale-open", open);
      if (!open) {
        serialSaleItem = null;
        setSerialSaleStatus("");
      } else {
        refreshIcons();
        serialSaleModal.querySelector("[data-serial-sale-close]")?.focus();
      }
    };

    const openSerialSaleModal = (item, { append = false } = {}) => {
      if (!serialSaleModal || !item?.id || !checkoutEnabled) return;
      const existing = cart.get(item.id);
      const salePrice = clampPrice(
        item.price ?? existing?.price ?? item.listPrice,
        item.minPrice ?? existing?.minPrice ?? 0,
        item.listPrice ?? existing?.listPrice ?? 0
      );
      serialSaleItem = {
        ...item,
        id: String(item.id),
        price: salePrice,
        listPrice: roundMoney(item.listPrice ?? existing?.listPrice ?? salePrice),
        minPrice: roundMoney(item.minPrice ?? existing?.minPrice ?? 0),
        trackSerial: true,
        serials: Array.isArray(item.serials)
          ? item.serials
          : Array.isArray(existing?.serials)
            ? existing.serials
            : [],
      };

      if (serialSaleCategory) {
        serialSaleCategory.textContent = serialSaleItem.category || "Serial tracked";
      }
      if (serialSaleName) serialSaleName.textContent = serialSaleItem.name || "Item";
      if (serialSaleDescription) {
        serialSaleDescription.textContent = serialSaleItem.description || "";
      }
      if (serialSaleStock) {
        const stock = Math.max(0, roundQty(serialSaleItem.stock));
        serialSaleStock.textContent =
          stock > 0 ? `${stock} in stock` : "Out of stock";
        serialSaleStock.classList.toggle("is-empty", stock <= 0);
      }
      if (serialSaleMedia) {
        if (serialSaleItem.image) {
          serialSaleMedia.innerHTML = `<img src="${serialSaleItem.image}" alt="">`;
        } else {
          serialSaleMedia.innerHTML =
            '<span class="shop-serial-modal-media-fallback"><i data-lucide="package" aria-hidden="true"></i></span>';
        }
      }

      const seed = append
        ? [...(serialSaleItem.serials || [])]
        : serialSaleItem.serials?.length
          ? [...serialSaleItem.serials]
          : [];
      resetSerialSaleModal(seed);
      lastSerialSaleCommitSerial = "";
      lastSerialSaleCommitAt = 0;
      setSerialSaleStatus("");
      if (serialSaleConfirm) {
        const label = serialSaleConfirm.querySelector("span");
        if (label) {
          label.textContent = existing ? "Update cart" : "Add to cart";
        }
      }
      setProductOpen(false);
      setSerialSaleOpen(true);
      window.MyShopSerialScan?.enhance?.(serialSaleModal);
      window.setTimeout(() => focusSerialSaleEntry(), 40);
      if (serialSearchUrl && shopId && serialSaleItem.id) {
        import("./offline/serials.js")
          .then(({ searchSerialsOnlineOrCache }) =>
            searchSerialsOnlineOrCache({
              url: serialSearchUrl,
              itemId: serialSaleItem.id,
              shopId,
              query: "",
              match: "contains",
              exclude: [],
              limit: 500,
            })
          )
          .catch(() => {});
      }
    };

    const confirmSerialSale = async () => {
      if (!serialSaleItem?.id) return false;

      const pending = String(serialSaleEntry?.value || "").trim();
      if (pending) {
        const ok = await commitSerialSaleEntry();
        if (!ok) return false;
      }

      const serials = collectSerialSaleValues();
      if (!serials.length) {
        setSerialSaleStatus("Enter at least one serial number.", { error: true });
        focusSerialSaleEntry();
        return false;
      }
      if (
        stockTrackingEnabled &&
        serialSaleItem.stock > 0 &&
        serials.length > serialSaleItem.stock
      ) {
        setSerialSaleStatus(
          `Only ${serialSaleItem.stock} unit${serialSaleItem.stock === 1 ? "" : "s"} in stock.`,
          { error: true }
        );
        return false;
      }

      setQty(serialSaleItem.id, serials.length, {
        ...serialSaleItem,
        trackSerial: true,
        serials,
        price: serialSaleItem.price,
      });
      setLinePrice(serialSaleItem.id, serialSaleItem.price, serialSaleItem);
      setSerialSaleOpen(false);
      renderCart();

      if (tradeModal && !tradeModal.hidden) {
        // Serials were picked inside the trade popup — stay there.
        renderTradeLines();
        return true;
      }

      const phone = (clientPhoneInput?.value || "").trim();
      const name = (clientNameInput?.value || "").trim();
      if (!phone && !name) {
        setCartStatus(
          "Client details are optional. Add a name, a phone number, or both if you want this serial linked.",
          { ok: true }
        );
      }
      setCartOpen(true);
      return true;
    };

    const addItem = (sourceEl) => {
      if (!checkoutEnabled) return;
      const item = readItemFromEl(sourceEl);
      if (!item.id) return;
      if (stockTrackingEnabled && selectedKind() !== "trade_in" && item.stock <= 0) {
        return;
      }
      if (item.trackSerial || cart.get(item.id)?.trackSerial) {
        const existing = cart.get(item.id);
        const salePrice = clampPrice(
          sourceEl === productModal
            ? productPriceInput?.value ?? item.price
            : existing?.price ?? item.listPrice,
          item.minPrice,
          item.listPrice
        );
        if (productModal && sourceEl === productModal) {
          productModal.dataset.itemPrice = String(salePrice);
          if (productPriceInput) productPriceInput.value = salePrice.toFixed(2);
        }
        openSerialSaleModal({
          ...item,
          price: salePrice,
          serials: existing?.serials || [],
        }, { append: Boolean(existing) });
        return;
      }
      const existing = cart.get(item.id);
      const salePrice = clampPrice(
        sourceEl === productModal
          ? productPriceInput?.value ?? item.price
          : existing?.price ?? item.listPrice,
        item.minPrice,
        item.listPrice
      );
      if (productModal && sourceEl === productModal) {
        productModal.dataset.itemPrice = String(salePrice);
        if (productPriceInput) productPriceInput.value = salePrice.toFixed(2);
      }
      const qtyToAdd =
        sourceEl === productModal ? pendingQty(productQtyInput, item.stock) : 1;
      setQty(item.id, (existing?.qty || 0) + qtyToAdd, {
        ...item,
        price: existing ? existing.price : salePrice,
        listPrice: item.listPrice,
        minPrice: item.minPrice,
        discountMinQty: item.discountMinQty,
        discountAmount: item.discountAmount,
      });
      // If newly added from popup with discount, ensure price sticks.
      if (!existing) {
        setLinePrice(item.id, salePrice, item);
      }
      renderCart();
    };

    loadCart();
    if (checkoutEnabled) {
      if (creditDueInput) {
        creditDueInput.min = localTodayIso();
        if (selectedKind() === "credit") ensureCreditDueDefault();
      }
      renderCart();
      syncCheckoutMode();
    }

    document.addEventListener("shop-catalog:rendered", () => {
      applyPendingStockToCatalog();
      syncCardControls();
      if (window.lucide?.createIcons) window.lucide.createIcons();
    });
    document.addEventListener("myshop-offline-queue-changed", () => {
      applyPendingStockToCatalog();
    });

    cartRoot.addEventListener("click", (event) => {
      const previewBtn = event.target.closest?.("[data-cart-preview]");
      if (previewBtn) {
        event.preventDefault();
        openProduct(previewBtn.closest("[data-cart-item]"));
        return;
      }

      const addBtn = event.target.closest?.("[data-cart-add]");
      if (addBtn) {
        event.preventDefault();
        event.stopPropagation();
        addItem(addBtn.closest("[data-cart-item]"));
        return;
      }

      const qtyBtn = event.target.closest?.("[data-cart-qty]");
      if (qtyBtn) {
        event.preventDefault();
        event.stopPropagation();
        const card = qtyBtn.closest("[data-cart-item]");
        const id = card?.getAttribute("data-item-id");
        const line = id ? cart.get(id) : null;
        if (!line) return;
        const action = qtyBtn.getAttribute("data-cart-qty");
        if (line.trackSerial) {
          if (action === "inc") {
            openSerialSaleModal({ ...line, ...readItemFromEl(card) }, { append: true });
          } else {
            const nextSerials = (line.serials || []).slice(0, Math.max(0, line.qty - 1));
            if (!nextSerials.length) {
              cart.delete(id);
            } else {
              setQty(id, nextSerials.length, { ...line, serials: nextSerials });
            }
            renderCart();
          }
          return;
        }
        setQty(id, action === "inc" ? line.qty + 1 : line.qty - 1, readItemFromEl(card));
        renderCart();
      }
    });

    cartRoot.addEventListener("change", (event) => {
      const input = event.target.closest?.("[data-cart-qty-input]");
      if (!input) return;
      const card = input.closest("[data-cart-item]");
      const id = card?.getAttribute("data-item-id");
      if (!id) return;
      const line = cart.get(id);
      if (line?.trackSerial) {
        openSerialSaleModal({ ...line, ...readItemFromEl(card) });
        renderCart();
        return;
      }
      setQty(id, input.value, readItemFromEl(card));
      renderCart();
    });

    cartRoot.addEventListener("keydown", (event) => {
      const input = event.target.closest?.("[data-cart-qty-input]");
      if (!input || event.key !== "Enter") return;
      event.preventDefault();
      input.blur();
    });

    fab?.addEventListener("click", () => setCartOpen(true));
    overlay?.addEventListener("click", () => setCartOpen(false));
    drawer?.querySelector("[data-cart-close]")?.addEventListener("click", () => setCartOpen(false));
    drawer?.querySelector("[data-cart-clear]")?.addEventListener("click", () => {
      cart.clear();
      resetCheckoutForm();
      renderCart();
    });

    checkoutForm?.addEventListener("change", (event) => {
      if (
        event.target.matches?.("[data-cart-kind], [data-cart-pay], [data-cart-whatsapp]")
      ) {
        if (event.target.matches?.("[data-cart-pay], [data-cart-kind]")) {
          clearStkConfirmation();
        }
        syncCheckoutMode();
        if (event.target.matches?.("[data-cart-kind]")) renderCart();
      }
    });

    cashInput?.addEventListener("input", () => {
      if (splitSyncing) return;
      splitLastEdited = "cash";
      syncSplitAmounts("cash");
      syncStkPanel();
    });
    mpesaInput?.addEventListener("input", () => {
      if (splitSyncing) return;
      splitLastEdited = "mpesa";
      syncSplitAmounts("mpesa");
      syncStkPanel();
    });

    stkSendBtn?.addEventListener("click", () => {
      sendStkPrompt();
    });

    cartLoginCode?.addEventListener("input", () => {
      if (cartLoginCode.disabled) return;
      cartCodeVerified = false;
      if (cartSubmit) cartSubmit.disabled = true;
      queueCartVerify();
    });
    cartLoginCode?.addEventListener("blur", () => {
      if (cartLoginCode.disabled) return;
      // Verify only — auto-checkout is owned by the debounced input path
      // so blur + verify cannot start a second sale.
      if (checkoutInFlight || cartCodeVerified) return;
      verifyCartLoginCode({ autoCheckout: false });
    });

    checkoutForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      submitCheckout();
    });

    drawer?.addEventListener("click", (event) => {
      const lineEl = event.target.closest?.(".shop-cart-line");
      if (!lineEl) return;
      const lineId = lineEl.dataset.lineId;
      const line = lineId ? cart.get(lineId) : null;
      if (!line) return;

      if (event.target.closest?.("[data-cart-line-preview]")) {
        event.preventDefault();
        const card = cartRoot.querySelector(
          `[data-cart-item][data-item-id="${CSS.escape(lineId)}"]`
        );
        if (card) {
          openProduct(card);
        } else {
          openProductFromLine(line);
        }
        return;
      }

      if (event.target.closest?.("[data-cart-edit-serials]")) {
        event.preventDefault();
        openSerialSaleModal(line);
        return;
      }

      if (event.target.closest?.("[data-cart-remove]")) {
        cart.delete(lineId);
        renderCart();
        return;
      }

      const qtyBtn = event.target.closest?.("[data-cart-qty]");
      if (!qtyBtn) return;
      const action = qtyBtn.getAttribute("data-cart-qty");
      if (line.trackSerial) {
        if (action === "inc") {
          openSerialSaleModal(line, { append: true });
        } else {
          const nextSerials = (line.serials || []).slice(0, Math.max(0, line.qty - 1));
          if (!nextSerials.length) cart.delete(lineId);
          else setQty(lineId, nextSerials.length, { ...line, serials: nextSerials });
          renderCart();
        }
        return;
      }
      setQty(lineId, action === "inc" ? line.qty + 1 : line.qty - 1, line);
      renderCart();
    });

    drawer?.addEventListener("change", (event) => {
      const qtyInput = event.target.closest?.("[data-cart-qty-input]");
      if (qtyInput) {
        const lineEl = qtyInput.closest(".shop-cart-line");
        const lineId = lineEl?.dataset.lineId;
        const line = lineId ? cart.get(lineId) : null;
        if (!line) return;
        if (line.trackSerial) {
          openSerialSaleModal(line);
          renderCart();
          return;
        }
        setQty(lineId, qtyInput.value, line);
        renderCart();
        return;
      }

      const priceInput = event.target.closest?.("[data-cart-price-input]");
      if (!priceInput) return;
      if (!discountEnabled) {
        const lineEl = priceInput.closest(".shop-cart-line");
        const lineId = lineEl?.dataset.lineId;
        const line = lineId ? cart.get(lineId) : null;
        if (line) {
          priceInput.value = roundMoney(line.listPrice || line.price).toFixed(2);
        }
        return;
      }
      const lineEl = priceInput.closest(".shop-cart-line");
      const lineId = lineEl?.dataset.lineId;
      const line = lineId ? cart.get(lineId) : null;
      if (!line) return;
      const nextPrice = setLinePrice(lineId, priceInput.value, line);
      if (nextPrice != null) priceInput.value = roundMoney(nextPrice).toFixed(2);
      renderCart();
    });

    drawer?.addEventListener("keydown", (event) => {
      const priceInput = event.target.closest?.("[data-cart-price-input]");
      if (!priceInput || event.key !== "Enter") return;
      if (!discountEnabled) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      priceInput.blur();
    });


    serialSaleModal?.querySelectorAll("[data-serial-sale-close]").forEach((el) => {
      el.addEventListener("click", () => setSerialSaleOpen(false));
    });
    serialSaleMatchModeRoot?.addEventListener("click", (event) => {
      const btn = event.target.closest?.("[data-serial-sale-match]");
      if (!btn) return;
      setSerialSaleMatchMode(btn.getAttribute("data-serial-sale-match") || "full");
    });
    syncSerialSaleMatchModeUi();
    serialSaleForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      confirmSerialSale();
    });
    serialSaleScanned?.addEventListener("click", (event) => {
      const remove = event.target.closest?.("[data-serial-sale-scanned-remove]");
      if (!remove) return;
      remove.closest("li")?.remove();
      syncSerialSaleCount();
      focusSerialSaleEntry();
    });
    serialSaleEntry?.addEventListener("input", (event) => {
      const input = event.target;
      if (!(input instanceof HTMLInputElement)) return;
      if (input.dataset.serialScanApply === "1") {
        delete input.dataset.serialScanApply;
        return;
      }
      const raw = String(input.value || "");
      if (/[\r\n]/.test(raw)) {
        const serial = raw.replace(/[\r\n]+/g, "").trim().toUpperCase();
        input.value = serial;
        commitSerialSaleEntry(serial ? { serial } : {});
        return;
      }
      if (isSerialSaleLast4Mode()) {
        const cleaned = raw
          .toUpperCase()
          .replace(/[^A-Z0-9]/g, "");
        if (cleaned.length > 4) {
          input.value = cleaned;
          input.dataset.serialResolved = "1";
          syncSerialSaleInputMode(input);
          hideSerialSaleSuggest(serialSaleEntryWrap);
          return;
        }
        delete input.dataset.serialResolved;
        input.value = cleaned.slice(0, 4);
        syncSerialSaleInputMode(input);
      } else {
        input.value = raw.toUpperCase();
        delete input.dataset.serialResolved;
      }
      input.classList.remove("is-duplicate");
      queueSerialSaleSearch(input);
    });
    serialSaleEntry?.addEventListener("focusout", () => {
      window.setTimeout(() => hideSerialSaleSuggest(serialSaleEntryWrap), 160);
    });
    serialSaleEntry?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      commitSerialSaleEntry();
    });
    serialSaleModal?.addEventListener("myshop:serial-applied", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.matches("[data-serial-sale-entry]")) return;
      window.clearTimeout(serialSaleSearchTimer);
      serialSaleSearchSeq += 1;
      hideSerialSaleSuggest(serialSaleEntryWrap);
      const serial = String(event.detail?.serial || target.value || "")
        .trim()
        .toUpperCase();
      commitSerialSaleEntry(serial ? { serial } : {});
    });

    productModal?.querySelectorAll("[data-product-close]").forEach((el) => {
      el.addEventListener("click", () => setProductOpen(false));
    });

    productAdd?.addEventListener("click", () => {
      if (!activeProductId || !productModal) return;
      addItem(productModal);
    });

    const applySalePriceFromInput = () => {
      if (!activeProductId || !productModal || !productPriceInput) return;
      const minPrice = roundMoney(productModal.dataset.itemMinPrice || 0);
      const listPrice = roundMoney(productModal.dataset.itemListPrice || 0);
      if (!discountEnabled) {
        productPriceInput.value = listPrice.toFixed(2);
        productModal.dataset.itemPrice = String(listPrice);
        syncPriceHint(minPrice, listPrice, listPrice);
        if (cart.has(activeProductId)) {
          setLinePrice(activeProductId, listPrice, {
            listPrice,
            minPrice,
          });
          renderCart();
        }
        return;
      }
      const nextPrice = clampPrice(
        productPriceInput.value,
        minPrice,
        listPrice
      );
      productPriceInput.value = nextPrice.toFixed(2);
      productModal.dataset.itemPrice = String(nextPrice);
      syncPriceHint(minPrice, nextPrice, listPrice);

      if (cart.has(activeProductId)) {
        setLinePrice(activeProductId, nextPrice, {
          listPrice,
          minPrice,
        });
        renderCart();
      }
    };

    productPriceInput?.addEventListener("change", applySalePriceFromInput);
    productPriceInput?.addEventListener("blur", applySalePriceFromInput);
    productPriceInput?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      if (!discountEnabled) return;
      applySalePriceFromInput();
      productPriceInput.blur();
    });

    productModal?.addEventListener("click", (event) => {
      const qtyBtn = event.target.closest?.("[data-product-qty]");
      if (!qtyBtn || !activeProductId) return;
      const line = cart.get(activeProductId);
      if (line?.trackSerial) {
        event.preventDefault();
        const action = qtyBtn.getAttribute("data-product-qty");
        if (action === "inc") openSerialSaleModal(line, { append: true });
        else {
          const nextSerials = (line.serials || []).slice(0, Math.max(0, line.qty - 1));
          if (!nextSerials.length) cart.delete(activeProductId);
          else setQty(activeProductId, nextSerials.length, { ...line, serials: nextSerials });
          renderCart();
        }
        return;
      }
      const action = qtyBtn.getAttribute("data-product-qty");
      if (!line) {
        if (!productQtyInput) return;
        const stock = Math.max(
          0,
          roundQty(productModal.dataset.itemStock || 0)
        );
        const current = pendingQty(productQtyInput, stock);
        let next =
          action === "inc" ? current + 1 : Math.max(0.001, current - 1);
        next = clampQty(next, stock);
        if (!(next > 0)) next = 0.001;
        productQtyInput.value = String(next);
        syncProductControls();
        return;
      }
      setQty(activeProductId, action === "inc" ? line.qty + 1 : line.qty - 1, line);
      renderCart();
    });

    productQtyInput?.addEventListener("change", () => {
      if (!activeProductId) return;
      const line = cart.get(activeProductId);
      if (!line) {
        const stock = Math.max(
          0,
          roundQty(productModal?.dataset.itemStock || 0)
        );
        productQtyInput.value = String(pendingQty(productQtyInput, stock));
        syncProductControls();
        return;
      }
      if (line.trackSerial) {
        openSerialSaleModal(line);
        renderCart();
        return;
      }
      setQty(activeProductId, productQtyInput.value, line);
      renderCart();
    });

    /* —— Trade popup (sidebar → modal) —— */
    const tradeForm = tradeModal?.querySelector("[data-trade-form]");
    const tradeKindInputs = tradeModal
      ? [...tradeModal.querySelectorAll("[data-trade-kind]")]
      : [];
    const tradeSearch = tradeModal?.querySelector("[data-trade-item-search]");
    const tradeSuggest = tradeModal?.querySelector("[data-trade-suggest]");
    const tradeLinesEl = tradeModal?.querySelector("[data-trade-lines]");
    const tradeEmptyEl = tradeModal?.querySelector("[data-trade-empty]");
    const tradeTotalEl = tradeModal?.querySelector("[data-trade-total]");
    const tradeLeadEl = tradeModal?.querySelector("[data-trade-lead]");
    const tradeItemsLabelEl = tradeModal?.querySelector("[data-trade-items-label]");
    const tradeSubmitBtn = tradeModal?.querySelector("[data-trade-submit]");
    const tradeSubmitLabel = tradeModal?.querySelector("[data-trade-submit-label]");
    const tradeLoginCode = tradeModal?.querySelector("[data-trade-login-code]");
    const tradeCatalogApi = cartRoot.getAttribute("data-catalog-api") || "";
    const TRADE_KINDS = new Set(["trade_out", "trade_in"]);
    let tradePrevKind = "";
    let tradeSearchTimer = 0;
    let tradeSearchSeq = 0;
    let tradeInFlight = false;

    const tradeKind = () =>
      tradeModal?.querySelector("[data-trade-kind]:checked")?.value ||
      tradeKindInputs[0]?.value ||
      "";

    const setTradeStatus = (message = "", { error = false, ok = false } = {}) => {
      if (!tradeStatusEl) return;
      tradeStatusEl.hidden = !message;
      tradeStatusEl.textContent = message || "";
      tradeStatusEl.classList.toggle("is-error", Boolean(error));
      tradeStatusEl.classList.toggle("is-ok", Boolean(ok));
    };

    const tradeClientSuggest = tradeModal?.querySelector(
      "[data-trade-client-suggest]"
    );
    const tradeClient = createClientAutofill({
      nameInput: tradeClientNameEl,
      phoneInput: tradeClientPhoneEl,
      suggestEl: tradeClientSuggest,
      onHint: (message = "", { ok = false } = {}) =>
        setTradeStatus(message, { ok }),
    });

    const setCartKind = (kind) => {
      if (!checkoutForm || !kind) return;
      checkoutForm.querySelectorAll("[data-cart-kind]").forEach((input) => {
        input.checked = input.value === kind;
      });
      syncCheckoutMode();
    };

    const syncTradeCopy = () => {
      const isIn = tradeKind() === "trade_in";
      if (tradeLeadEl) {
        tradeLeadEl.textContent = isIn
          ? "The client brings the items — record what you owe them."
          : "The client takes the items from this shop.";
      }
      if (tradeItemsLabelEl) {
        tradeItemsLabelEl.textContent = isIn ? "Items received" : "Items taken";
      }
      if (tradeSubmitLabel) {
        tradeSubmitLabel.textContent = isIn
          ? "Complete trade in"
          : "Complete trade out";
      }
    };

    const hideTradeSuggest = () => {
      if (!tradeSuggest) return;
      tradeSuggest.hidden = true;
      tradeSuggest.innerHTML = "";
    };

    const tradeItemFromJson = (item) => ({
      id: String(item?.id ?? ""),
      name: String(item?.name || "Item"),
      category: String(item?.category || ""),
      description: String(item?.description || ""),
      price: roundMoney(item?.price),
      listPrice: roundMoney(item?.price),
      minPrice: roundMoney(item?.min_price),
      discountMinQty: Math.max(
        0,
        Math.floor(Number(item?.discount_min_qty) || 0)
      ),
      discountAmount: roundMoney(item?.discount_amount),
      stock: Math.max(0, roundQty(item?.stock)),
      image: String(item?.image_url || ""),
      trackSerial: Boolean(item?.track_serial),
    });

    const renderTradeSuggest = (items, query) => {
      if (!tradeSuggest) return;
      tradeSuggest.innerHTML = "";
      const kind = tradeKind();
      items.slice(0, 8).forEach((item) => {
        const option = document.createElement("button");
        option.type = "button";
        option.className = "shop-cart-client-suggest-option";
        const out =
          stockTrackingEnabled && kind !== "trade_in" && item.stock <= 0;
        option.disabled = out;
        const name = document.createElement("strong");
        name.textContent = item.name;
        const meta = document.createElement("small");
        meta.textContent = [
          item.category,
          item.trackSerial ? "Serial tracked" : "",
          stockTrackingEnabled
            ? out
              ? "Out of stock"
              : `${item.stock} in stock`
            : "",
          money(item.price),
        ]
          .filter(Boolean)
          .join(" · ");
        option.append(name, meta);
        option.addEventListener("mousedown", (event) => event.preventDefault());
        option.addEventListener("click", () => addTradeItem(item));
        tradeSuggest.appendChild(option);
      });
      if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "shop-cart-client-suggest-empty";
        empty.textContent = `No items match “${query}”.`;
        tradeSuggest.appendChild(empty);
      }
      tradeSuggest.hidden = false;
    };

    const searchTradeItems = async (query) => {
      const seq = ++tradeSearchSeq;
      const q = String(query || "").trim();
      if (!q) {
        hideTradeSuggest();
        return;
      }
      if (tradeCatalogApi) {
        try {
          const params = new URLSearchParams({
            page: "1",
            page_size: "8",
            q,
          });
          const response = await fetch(`${tradeCatalogApi}?${params}`, {
            headers: { Accept: "application/json" },
            credentials: "same-origin",
          });
          const data = await response.json().catch(() => ({}));
          if (seq !== tradeSearchSeq) return;
          if (response.ok && data.ok) {
            renderTradeSuggest((data.items || []).map(tradeItemFromJson), q);
            return;
          }
        } catch (_err) {
          /* fall back to the rendered catalog */
        }
      }
      if (seq !== tradeSearchSeq) return;
      const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
      const items = [];
      cartRoot.querySelectorAll("[data-cart-item]").forEach((card) => {
        if (items.length >= 8) return;
        const haystack = String(
          card.getAttribute("data-search-text") || ""
        ).toLowerCase();
        if (tokens.every((token) => haystack.includes(token))) {
          items.push(readItemFromEl(card));
        }
      });
      renderTradeSuggest(items, q);
    };

    const renderTradeLines = () => {
      if (!tradeLinesEl || !tradeModal) return;
      const isIn = tradeKind() === "trade_in";
      const editable = isIn || discountEnabled;
      const lines = [...cart.values()];
      tradeLinesEl.innerHTML = "";
      if (tradeEmptyEl) tradeEmptyEl.hidden = lines.length > 0;
      lines.forEach((line) => {
        const li = document.createElement("li");
        li.className = "shop-trade-line";
        li.dataset.lineId = line.id;

        const copy = document.createElement("div");
        copy.className = "shop-trade-line-copy";
        const name = document.createElement("strong");
        name.textContent = line.name;
        copy.appendChild(name);
        const meta = document.createElement("small");
        meta.className = "shop-trade-line-meta";
        const serialCount = (line.serials || []).length;
        meta.textContent = line.trackSerial
          ? `${serialCount} serial${serialCount === 1 ? "" : "s"}`
          : `${line.qty} × ${money(line.price)}`;
        copy.appendChild(meta);
        li.appendChild(copy);

        if (line.trackSerial) {
          const serialsBtn = document.createElement("button");
          serialsBtn.type = "button";
          serialsBtn.className = "shop-cart-line-serial-edit";
          serialsBtn.dataset.tradeEditSerials = "";
          serialsBtn.textContent = serialCount
            ? `Serials (${serialCount})`
            : "Add serials";
          li.appendChild(serialsBtn);
        } else {
          const qtyWrap = document.createElement("div");
          qtyWrap.className = "shop-cart-qty shop-trade-qty";
          const dec = document.createElement("button");
          dec.type = "button";
          dec.className = "shop-floor-qty-btn";
          dec.dataset.tradeQty = "dec";
          dec.setAttribute("aria-label", "Decrease quantity");
          dec.innerHTML = '<i data-lucide="minus" aria-hidden="true"></i>';
          const qtyInput = document.createElement("input");
          qtyInput.type = "text";
          qtyInput.className = "shop-cart-qty-input";
          qtyInput.min = "0.001";
          qtyInput.step = "0.001";
          if (!isIn && stockTrackingEnabled && line.stock > 0) {
            qtyInput.max = String(line.stock);
          }
          qtyInput.value = String(line.qty);
          qtyInput.inputMode = "decimal";
          qtyInput.setAttribute("aria-label", "Quantity");
          qtyInput.dataset.tradeQtyInput = "";
          const inc = document.createElement("button");
          inc.type = "button";
          inc.className = "shop-floor-qty-btn";
          inc.dataset.tradeQty = "inc";
          inc.setAttribute("aria-label", "Increase quantity");
          inc.innerHTML = '<i data-lucide="plus" aria-hidden="true"></i>';
          if (
            !isIn &&
            stockTrackingEnabled &&
            line.stock > 0 &&
            line.qty >= line.stock
          ) {
            inc.disabled = true;
          }
          qtyWrap.append(dec, qtyInput, inc);
          li.appendChild(qtyWrap);
        }

        const priceField = document.createElement("label");
        priceField.className = "shop-cart-price-field";
        if (!editable) priceField.classList.add("is-locked");
        const prefix = document.createElement("span");
        prefix.textContent = "KSh";
        const priceInput = document.createElement("input");
        priceInput.type = "text";
        priceInput.className = "shop-cart-price-input";
        priceInput.min = isIn
          ? "0.01"
          : String(editable ? line.minPrice || 0 : line.listPrice || 0);
        if (!editable) priceInput.max = String(line.listPrice || 0);
        priceInput.step = "0.01";
        priceInput.inputMode = "decimal";
        priceInput.value = roundMoney(line.price).toFixed(2);
        priceInput.readOnly = !editable;
        priceInput.tabIndex = editable ? 0 : -1;
        priceInput.dataset.tradePriceInput = "";
        priceInput.setAttribute(
          "aria-label",
          isIn ? `Amount owed for ${line.name}` : `Price for ${line.name}`
        );
        priceField.append(prefix, priceInput);
        li.appendChild(priceField);

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "shop-cart-line-remove";
        remove.dataset.tradeRemove = "";
        remove.textContent = "Remove";
        li.appendChild(remove);

        tradeLinesEl.appendChild(li);
      });
      if (tradeTotalEl) tradeTotalEl.textContent = money(cartTotal());
      refreshIcons();
    };

    const setTradeOpen = (open) => {
      if (!tradeModal) return;
      tradeModal.hidden = !open;
      tradeModal.setAttribute("aria-hidden", String(!open));
      syncModalOpen();
      if (open) {
        refreshIcons();
        window.setTimeout(() => tradeSearch?.focus(), 60);
      } else {
        hideTradeSuggest();
        tradeClient.hideSuggest();
      }
    };

    const openTrade = () => {
      if (!tradeModal || !tradeKind()) return;
      const current = selectedKind();
      tradePrevKind = TRADE_KINDS.has(current) ? "" : current;
      setCartKind(tradeKind());
      setCartOpen(false);
      setTradeStatus(
        checkoutEnabled
          ? ""
          : cartRoot.dataset.shopDayCanTrade === "0"
            ? "Open the shop before trading."
            : "Checkout is disabled in POS settings.",
        { error: !checkoutEnabled }
      );
      syncTradeCopy();
      setTradeOpen(true);
      renderCart();
      renderTradeLines();
    };

    const closeTrade = ({ restoreKind = true } = {}) => {
      if (!tradeModal || tradeModal.hidden) return;
      if (restoreKind) {
        setCartKind(tradePrevKind || defaultKind);
        renderCart();
      }
      setTradeOpen(false);
    };

    const addTradeItem = (item) => {
      if (!item?.id || !tradeModal) return;
      hideTradeSuggest();
      if (tradeSearch) tradeSearch.value = "";
      const kind = tradeKind();
      if (stockTrackingEnabled && kind !== "trade_in" && item.stock <= 0) {
        setTradeStatus(`“${item.name}” is out of stock.`, { error: true });
        return;
      }
      const existing = cart.get(item.id);
      if (item.trackSerial || existing?.trackSerial) {
        openSerialSaleModal(
          {
            ...item,
            price: existing?.price ?? item.price,
            serials: existing?.serials || [],
          },
          { append: Boolean(existing) }
        );
        return;
      }
      const price = clampPrice(
        existing?.price ?? item.price,
        item.minPrice,
        item.listPrice
      );
      setQty(item.id, (existing?.qty || 0) + 1, {
        ...item,
        price: existing ? existing.price : price,
      });
      if (!existing) setLinePrice(item.id, price, item);
      renderCart();
      renderTradeLines();
      setTradeStatus("");
    };

    const submitTrade = async () => {
      if (tradeInFlight || checkoutInFlight) return;
      const kind = tradeKind();
      if (!TRADE_KINDS.has(kind)) {
        setTradeStatus("Choose trade out or trade in.", { error: true });
        return;
      }
      if (!checkoutEnabled) {
        setTradeStatus(
          cartRoot.dataset.shopDayCanTrade === "0"
            ? "Open the shop before trading."
            : "Checkout is disabled in POS settings.",
          { error: true }
        );
        return;
      }
      if (!cart.size) {
        setTradeStatus("Search and add at least one item.", { error: true });
        tradeSearch?.focus();
        return;
      }
      const name = (tradeClientNameEl?.value || "").trim();
      tradeClient.normalizePhoneField({ force: true });
      const phone = (tradeClientPhoneEl?.value || "").trim();
      tradeClient.hideSuggest();
      if (!name) {
        setTradeStatus("Enter the client full name.", { error: true });
        tradeClientNameEl?.focus();
        return;
      }
      if (!phone) {
        setTradeStatus("Enter the client phone number.", { error: true });
        tradeClientPhoneEl?.focus();
        return;
      }
      const code = (tradeLoginCode?.value || "").trim();
      if (!/^\d{6}$/.test(code)) {
        setTradeStatus("Enter your 6-digit employee ID.", { error: true });
        tradeLoginCode?.focus();
        return;
      }

      tradeInFlight = true;
      if (tradeSubmitBtn) tradeSubmitBtn.disabled = true;
      try {
        setCartKind(kind);
        if (clientNameInput) clientNameInput.value = name.toUpperCase();
        if (clientPhoneInput) clientPhoneInput.value = phone;
        if (cartLoginCode) cartLoginCode.value = code;
        cartCodeVerified = false;
        cartStaffAllocatedToShop = null;
        renderCart();
        setTradeStatus("Verifying employee ID…");
        await submitCheckout();
        if (!cart.size) {
          // Checkout finished and cleared the cart.
          tradeForm?.reset();
          tradeClient.reset();
          if (tradeSearch) tradeSearch.value = "";
          hideTradeSuggest();
          renderTradeLines();
          closeTrade({ restoreKind: false });
          pushStockRequestToast(
            kind === "trade_in" ? "Trade in recorded." : "Trade out recorded.",
            "success"
          );
        }
      } finally {
        tradeInFlight = false;
        if (tradeSubmitBtn) tradeSubmitBtn.disabled = false;
      }
    };

    document.querySelectorAll('[data-modal-open="trade"]').forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        openTrade();
      });
    });

    if (tradeModal && checkoutForm) {
      tradeModal.addEventListener("click", (event) => {
        if (event.target.closest?.("[data-trade-close]")) {
          event.preventDefault();
          closeTrade();
          return;
        }
        const lineEl = event.target.closest?.(".shop-trade-line");
        const line = lineEl?.dataset.lineId
          ? cart.get(lineEl.dataset.lineId)
          : null;

        const qtyBtn = event.target.closest?.("[data-trade-qty]");
        if (qtyBtn && line) {
          event.preventDefault();
          setQty(
            line.id,
            qtyBtn.dataset.tradeQty === "inc" ? line.qty + 1 : line.qty - 1,
            line
          );
          renderCart();
          renderTradeLines();
          return;
        }
        const serialsBtn = event.target.closest?.("[data-trade-edit-serials]");
        if (serialsBtn && line) {
          event.preventDefault();
          openSerialSaleModal(line);
          return;
        }
        const removeBtn = event.target.closest?.("[data-trade-remove]");
        if (removeBtn && line) {
          event.preventDefault();
          cart.delete(line.id);
          renderCart();
          renderTradeLines();
          return;
        }
        if (
          !event.target.closest?.("[data-trade-suggest]") &&
          !event.target.closest?.("[data-trade-item-search]")
        ) {
          hideTradeSuggest();
        }
        if (
          !event.target.closest?.("[data-trade-client-suggest]") &&
          !event.target.closest?.("[data-trade-client-name]")
        ) {
          tradeClient.hideSuggest();
        }
      });

      tradeModal.addEventListener("change", (event) => {
        if (event.target.matches?.("[data-trade-kind]")) {
          setCartKind(tradeKind());
          syncTradeCopy();
          renderCart();
          renderTradeLines();
          return;
        }
        const qtyInput = event.target.closest?.("[data-trade-qty-input]");
        if (qtyInput) {
          const lineEl = qtyInput.closest(".shop-trade-line");
          const line = lineEl ? cart.get(lineEl.dataset.lineId) : null;
          if (!line) return;
          if (line.trackSerial) {
            openSerialSaleModal(line);
            renderTradeLines();
            return;
          }
          setQty(line.id, qtyInput.value, line);
          renderCart();
          renderTradeLines();
          return;
        }
        const priceInput = event.target.closest?.("[data-trade-price-input]");
        if (priceInput) {
          const lineEl = priceInput.closest(".shop-trade-line");
          const line = lineEl ? cart.get(lineEl.dataset.lineId) : null;
          if (!line) return;
          const next = setLinePrice(line.id, priceInput.value, line);
          if (next != null) priceInput.value = roundMoney(next).toFixed(2);
          renderCart();
          renderTradeLines();
        }
      });

      tradeForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        submitTrade();
      });

      tradeSearch?.addEventListener("input", () => {
        window.clearTimeout(tradeSearchTimer);
        tradeSearchTimer = window.setTimeout(() => {
          searchTradeItems(tradeSearch.value);
        }, 220);
      });
      tradeSearch?.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          hideTradeSuggest();
          return;
        }
        if (event.key !== "Enter") return;
        event.preventDefault();
        window.clearTimeout(tradeSearchTimer);
        searchTradeItems(tradeSearch.value);
      });
      tradeSearch?.addEventListener("focusout", () => {
        window.setTimeout(hideTradeSuggest, 160);
      });
    }

    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || selectModal) return;
      if (serialSaleModal?.classList.contains("is-open")) {
        event.stopPropagation();
        setSerialSaleOpen(false);
        return;
      }
      if (productModal?.classList.contains("is-open")) {
        event.stopPropagation();
        setProductOpen(false);
        return;
      }
      if (tradeModal && !tradeModal.hidden) {
        event.stopPropagation();
        closeTrade();
        return;
      }
      if (drawer?.classList.contains("is-open")) {
        event.stopPropagation();
        setCartOpen(false);
      }
    });
  }


  if (window.lucide?.createIcons) {
    window.lucide.createIcons();
  }
})();
