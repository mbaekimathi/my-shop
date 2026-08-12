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

  const ackSupplierSeen = () => {
    const ackUrl =
      requestModal?.getAttribute("data-stock-request-supplier-ack-url") ||
      document
        .querySelector("[data-stock-request-supplier-ack-url]")
        ?.getAttribute("data-stock-request-supplier-ack-url") ||
      "";
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

  const requestControls = bindModal({
    modal: requestModal,
    openSelectors: "[data-stock-request-open]",
    closeSelectors: "[data-stock-request-close]",
    autoOpen: requestModal?.getAttribute("data-auto-open") === "1",
    onClose: () => {
      ackSupplierSeen();
      window.clearTimeout(requestSnoozeTimer);
      requestSnoozeTimer = window.setTimeout(() => {
        if (requestModal && document.querySelector("[data-stock-request-open]")) {
          requestControls?.open();
        }
      }, SNOOZE_MS);
    },
  });

  // If the incoming modal auto-opened, mark supplier alerts as seen.
  if (requestModal && requestModal.hidden === false) {
    ackSupplierSeen();
  }

  const decisionModal = document.querySelector("[data-stock-decision-modal]");
  const decisionControls = bindModal({
    modal: decisionModal,
    openSelectors: "[data-stock-decision-open]",
    closeSelectors: "[data-stock-decision-close]",
    autoOpen:
      decisionModal?.getAttribute("data-auto-open") === "1" && !requestModal,
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
    const rows = [...createModal.querySelectorAll("[data-stock-create-row]")];
    const form = createModal.querySelector("[data-stock-create-form]");
    const fromSelect = createModal.querySelector("[data-stock-create-from]");
    const itemsHint = createModal.querySelector("[data-stock-create-items-hint]");
    const showEmptyToggle = createModal.querySelector("[data-stock-create-show-empty]");
    const filterWrap = createModal.querySelector("[data-stock-create-filter-wrap]");
    const noStockMsg = createModal.querySelector("[data-stock-create-no-stock]");
    const fromStockUrl =
      createModal.getAttribute("data-stock-create-from-stock-url") || "";
    let fromStockSeq = 0;
    let stockLoaded = false;

    document.querySelectorAll('[data-modal-open="request-stock"]').forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        createControls?.open();
        window.setTimeout(() => {
          createModal.querySelector("[data-stock-create-from]")?.focus();
        }, 40);
      });
    });

    const params = new URLSearchParams(window.location.search);
    if ((params.get("modal") || "").trim() === "request-stock") {
      createControls?.open();
      params.delete("modal");
      const query = params.toString();
      const next = `${window.location.pathname}${query ? `?${query}` : ""}${
        window.location.hash || ""
      }`;
      window.history.replaceState({}, "", next);
    }

    const syncRowEnabled = (row) => {
      const qty = row.querySelector("[data-stock-create-qty]");
      const idInput = row.querySelector("[data-stock-create-id]");
      const hasQty = Number(qty?.value || 0) > 0;
      if (idInput) idInput.disabled = !hasQty;
      if (qty) qty.disabled = false;
      row.classList.toggle("is-active", hasQty);
    };

    const selectedBadge = createModal.querySelector("[data-stock-create-selected]");
    const selectedCount = createModal.querySelector("[data-stock-create-selected-count]");
    const noResults = createModal.querySelector("[data-stock-create-no-results]");

    const syncSelectedCount = () => {
      const count = rows.filter((row) => {
        const qty = row.querySelector("[data-stock-create-qty]");
        return Number(qty?.value || 0) > 0;
      }).length;
      if (selectedCount) selectedCount.textContent = String(count);
      if (selectedBadge) selectedBadge.hidden = count < 1;
    };

    const showEmptyStock = () => Boolean(showEmptyToggle?.checked);

    const syncRowVisibility = () => {
      const q = String(searchInput?.value || "").trim().toLowerCase();
      const allowEmpty = showEmptyStock();
      let matchedSearch = 0;
      let visible = 0;
      let inStockCount = 0;

      rows.forEach((row) => {
        const name = row.getAttribute("data-item-name") || "";
        const nameMatch = !q || name.includes(q);
        const availRaw = row.getAttribute("data-avail");
        const hasAvail = availRaw !== "" && availRaw != null;
        const avail = hasAvail ? Number(availRaw) || 0 : null;
        if (avail != null && avail > 0) inStockCount += 1;
        const stockOk = !stockLoaded || allowEmpty || (avail != null && avail > 0);
        const show = nameMatch && stockOk;
        row.hidden = !show;
        if (nameMatch) matchedSearch += 1;
        if (show) visible += 1;
      });

      if (noResults) noResults.hidden = !q || matchedSearch > 0;
      if (noStockMsg) {
        const searchingMiss = Boolean(q) && matchedSearch < 1;
        noStockMsg.hidden =
          !stockLoaded || allowEmpty || searchingMiss || inStockCount > 0 || visible > 0;
        if (stockLoaded && !allowEmpty && !searchingMiss && inStockCount < 1) {
          noStockMsg.hidden = false;
        }
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
      if (value == null) {
        row.setAttribute("data-avail", "");
        el.textContent = "—";
        el.classList.add("is-empty");
        if (text) text.textContent = "Pick a shop to check stock";
        return;
      }
      const qty = Number(value) || 0;
      row.setAttribute("data-avail", String(qty));
      el.textContent = String(qty);
      if (qty <= 0) {
        el.classList.add("is-empty");
        if (text) text.textContent = "Out of stock at this shop";
      } else if (qty <= 3) {
        el.classList.add("is-low");
        if (text) text.textContent = `Only ${qty} left at this shop`;
      } else {
        el.classList.add("is-ok");
        if (text) text.textContent = `${qty} available at this shop`;
      }
    };

    const clearFromStock = () => {
      stockLoaded = false;
      if (filterWrap) filterWrap.hidden = true;
      if (showEmptyToggle) showEmptyToggle.checked = false;
      rows.forEach((row) => {
        setAvailDisplay(row, null);
        row.classList.remove("is-out");
        const qty = row.querySelector("[data-stock-create-qty]");
        if (qty) qty.removeAttribute("max");
      });
      if (itemsHint) {
        itemsHint.textContent =
          "Choose a shop first to see what they have in stock.";
      }
      syncRowVisibility();
    };

    const applyFromStock = (stocks, shopName) => {
      const map = stocks && typeof stocks === "object" ? stocks : {};
      stockLoaded = true;
      if (filterWrap) filterWrap.hidden = false;
      let withStock = 0;
      rows.forEach((row) => {
        const itemId = row.getAttribute("data-item-id") || "";
        const avail = Number(map[itemId] ?? map[String(itemId)] ?? 0) || 0;
        if (avail > 0) withStock += 1;
        setAvailDisplay(row, avail);
        row.classList.toggle("is-out", avail <= 0);
        const qty = row.querySelector("[data-stock-create-qty]");
        if (qty) {
          if (avail > 0) qty.setAttribute("max", String(avail));
          else qty.removeAttribute("max");
        }
      });
      if (itemsHint) {
        itemsHint.textContent = shopName
          ? withStock
            ? `Showing items in stock at ${shopName}. Enter how many you need.`
            : `${shopName} has no stock on these items right now.`
          : "Enter how many you need for each item.";
      }
      syncRowVisibility();
    };

    const loadFromStock = async () => {
      const fromId = String(fromSelect?.value || "").trim();
      if (!fromId || !fromStockUrl) {
        clearFromStock();
        return;
      }
      const seq = ++fromStockSeq;
      stockLoaded = false;
      rows.forEach((row) => {
        setAvailDisplay(row, null, { loading: true });
      });
      if (itemsHint) itemsHint.textContent = "Loading stock levels…";
      try {
        const url = `${fromStockUrl}?from_shop_id=${encodeURIComponent(fromId)}`;
        const response = await fetch(url, {
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          credentials: "same-origin",
        });
        if (seq !== fromStockSeq) return;
        if (!response.ok) {
          clearFromStock();
          return;
        }
        const data = await response.json();
        if (seq !== fromStockSeq) return;
        if (!data?.ok) {
          clearFromStock();
          return;
        }
        applyFromStock(data.stocks || {}, data.from_shop_name || "");
      } catch {
        if (seq !== fromStockSeq) return;
        clearFromStock();
      }
    };

    fromSelect?.addEventListener("change", () => {
      loadFromStock();
    });

    showEmptyToggle?.addEventListener("change", () => {
      syncRowVisibility();
    });

    rows.forEach((row) => {
      const qty = row.querySelector("[data-stock-create-qty]");
      if (!qty) return;
      qty.disabled = false;
      qty.addEventListener("input", () => {
        syncRowEnabled(row);
        syncSelectedCount();
      });
      qty.addEventListener("change", () => {
        syncRowEnabled(row);
        syncSelectedCount();
      });
      syncRowEnabled(row);
    });
    syncSelectedCount();
    clearFromStock();

    searchInput?.addEventListener("input", syncRowVisibility);

    form?.addEventListener("submit", (event) => {
      const fromShop = form.querySelector("[data-stock-create-from]");
      const code = form.querySelector("[data-stock-create-code]");
      rows.forEach((row) => {
        const qty = row.querySelector("[data-stock-create-qty]");
        const idInput = row.querySelector("[data-stock-create-id]");
        const hasQty = Number(qty?.value || 0) > 0;
        if (qty) qty.disabled = !hasQty;
        if (idInput) idInput.disabled = !hasQty;
      });
      const activeRows = rows.filter((row) => {
        const qty = row.querySelector("[data-stock-create-qty]");
        return Number(qty?.value || 0) > 0 && !qty.disabled;
      });
      if (!fromShop?.value) {
        event.preventDefault();
        rows.forEach((row) => syncRowEnabled(row));
        window.alert("Choose which shop the stock should come from.");
        fromShop?.focus();
        return;
      }
      if (!activeRows.length) {
        event.preventDefault();
        rows.forEach((row) => syncRowEnabled(row));
        window.alert("Enter how many you need for at least one item.");
        return;
      }
      if (!String(code?.value || "").trim()) {
        event.preventDefault();
        rows.forEach((row) => syncRowEnabled(row));
        window.alert("Enter your 6-digit staff ID to send the request.");
        code?.focus();
      }
    });

    if (fromSelect?.value) loadFromStock();
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

  const pushStockRequestToast = (text, tone = "warning") => {
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
    const list = host.querySelector(".workspace-messages") || host;
    const item = document.createElement("li");
    const safeTone = tone === "error" ? "error" : tone === "success" ? "success" : "warning";
    item.className = `workspace-toast__item workspace-toast__item--${safeTone}`;
    item.innerHTML = `<span class="workspace-toast__text"></span>`;
    item.querySelector(".workspace-toast__text").textContent = text;
    list.appendChild(item);
    window.setTimeout(() => {
      host.classList.add("is-hiding");
      window.setTimeout(() => host.remove(), 220);
    }, 4200);
  };

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
        if (!response.ok) return;
        const data = await response.json();
        if (!data?.ok) return;
        const pending = Array.isArray(data.pending) ? data.pending : [];
        const decisions = Array.isArray(data.decisions) ? data.decisions : [];
        const newRows = pending.filter((row) => !knownIds.includes(String(row.id)));
        const newDecisions = decisions.filter(
          (row) => !knownDecisionIds.includes(String(row.id))
        );

        if (!seeded) {
          seedKnown(pending);
          seedKnownDecisions(decisions);
          markSeeded();
          return;
        }

        if (newRows.length) {
          const sample = newRows[0];
          const fromName = sample?.from_shop || "another shop";
          const count = newRows.length;
          const message =
            count === 1
              ? `New stock request from ${fromName}.`
              : `${count} new stock requests, including from ${fromName}.`;
          pushStockRequestToast(message);
          notifyBrowser("Stock request", message);
          seedKnown(pending);
          seedKnownDecisions(decisions);
          // Reload so incoming modal + badge HTML stay in sync.
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
            message = `${fromName} declined your stock request.`;
          } else if (newDecisions.length === 1 && allAccepted) {
            message = `${fromName} accepted your stock request.`;
          } else if (allDeclined) {
            message = `${newDecisions.length} stock requests were declined.`;
          } else if (allAccepted) {
            message = `${newDecisions.length} stock requests were accepted.`;
          } else {
            message = `Updates on your stock requests, including from ${fromName}.`;
          }
          pushStockRequestToast(message, allDeclined ? "error" : "success");
          notifyBrowser("Stock request update", message);
          seedKnown(pending);
          seedKnownDecisions(decisions);
          window.setTimeout(() => {
            window.location.reload();
          }, 900);
          return;
        }

        seedKnown(pending);
        seedKnownDecisions(decisions);

        const badge = document.querySelector(".workspace-nav-badge");
        if (badge && typeof data.pending_count === "number") {
          if (data.pending_count > 0) {
            badge.hidden = false;
            badge.textContent = String(data.pending_count);
          } else {
            badge.hidden = true;
          }
        }
      } catch {
        /* ignore transient poll errors */
      }
    };

    window.setInterval(pollStockRequests, 12000);
    window.setTimeout(pollStockRequests, 2500);
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
      if (picked) serial = picked;
    }

    entry.value = "";
    hideSerialSuggest(root);

    state.busy = true;
    transferCommitState.set(block, state);
    let ok = false;
    try {
      if (!serialSearchUrl) {
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
    window.MyShopSerialScan?.enhance?.(panel);
    refreshIcons();
  };

  const clampTransferQty = (input) => {
    if (input.dataset.qtyFromSerial === "1") {
      return Number(input.value || 0);
    }
    const row = input.closest("[data-request-line]");
    const max = Number(input.max || row?.dataset.available || 0);
    let value = Number(input.value || 0);
    if (!Number.isFinite(value) || value < 0) value = 0;
    value = Math.floor(value);
    if (value > max) value = max;
    input.value = String(value);
    return value;
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
    if (!serialSearchUrl || !supplyShopId) return;
    const block = input.closest("[data-serial-block]");
    if (!block || block.hidden) return;
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

  const setVerified = (form, verified, message = "") => {
    form.dataset.codeVerified = verified ? "1" : "0";
    const actions = form.querySelector("[data-decision-actions]");
    const status = form.querySelector("[data-verify-status]");
    const buttons = form.querySelectorAll("[data-decision-submit]");
    if (actions) actions.removeAttribute("hidden");
    buttons.forEach((btn) => {
      btn.disabled = !verified;
    });
    if (status) {
      status.textContent =
        message ||
        (verified
          ? "Staff verified. You can Accept or Decline."
          : "Enter any active staff member’s 6-digit ID (any role) to unlock Accept and Decline.");
      status.classList.toggle("is-ok", verified);
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
      const body = new URLSearchParams({ login_code: code });
      const response = await fetch(verifyUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": getCsrfToken(form),
        },
        credentials: "same-origin",
        body,
      });
      const data = await response.json().catch(() => ({}));
      if (seq !== employeeVerifySeq) return false;
      if (!response.ok || !data.ok) {
        setVerified(form, false, data.error || "Not a valid active staff ID.");
        return false;
      }
      const label = data.name
        ? `Verified: ${data.name} (${data.employee_id}).`
        : `Verified staff ${data.employee_id}.`;
      setVerified(form, true, `${label} You can Accept or Decline.`);
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
      const shopName =
        form.getAttribute("data-requesting-shop") || "the requesting shop";
      if (
        !window.confirm(
          `Decline this stock request from ${shopName}? They will be notified that it was declined.`
        )
      ) {
        return false;
      }
      return true;
    }

    if (decision === "accept") {
      form.querySelectorAll("[data-serial-block]").forEach((block) => {
        syncSerialBlockQty(form, block);
      });

      let total = 0;
      const qtyInputs = [...form.querySelectorAll("[data-transfer-qty]")];
      for (const input of qtyInputs) {
        const qty = clampTransferQty(input);
        const max = Number(input.max || 0);
        if (qty > max) {
          window.alert("Transfer quantity cannot exceed available stock.");
          input.focus();
          return false;
        }
        total += qty;
      }
      if (total <= 0) {
        window.alert(
          form.querySelector("[data-serial-block]")
            ? "Select at least one serial number to transfer."
            : "Enter at least one quantity to transfer."
        );
        form.querySelector("[data-serial-entry]")?.focus();
        return false;
      }

      for (const block of form.querySelectorAll("[data-serial-block]")) {
        const max = Number(block.dataset.maxQty || 0);
        if (max <= 0) continue;
        const filled = filledSerialsInBlock(block);
        const qtyInput = getQtyInput(form, block.dataset.lineId);
        const qty = Number(qtyInput?.value || 0);
        if (qty > 0 && filled.length !== qty) {
          window.alert(
            `Select ${qty} serial number${qty === 1 ? "" : "s"} for this item before accepting.`
          );
          block.querySelector("[data-serial-entry]")?.focus();
          return false;
        }
        if (qty > 0 && filled.length === 0) {
          window.alert("Serial-tracked items require serial numbers to transfer.");
          block.querySelector("[data-serial-entry]")?.focus();
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
      input.addEventListener("change", () => clampTransferQty(input));
    });

    form.querySelector("[data-login-code]")?.addEventListener("input", (event) => {
      const input = event.target;
      input.value = String(input.value || "").replace(/\D/g, "").slice(0, 6);
      setVerified(form, false);
      queueEmployeeVerify(form);
    });

    form.addEventListener("submit", async (event) => {
      const submitter = event.submitter;
      const decision = (
        submitter?.getAttribute?.("data-decision-submit") ||
        submitter?.value ||
        ""
      ).trim();
      if (!validateForm(form, decision)) {
        event.preventDefault();
        return;
      }

      // Accept: transfer stock then print a From → To delivery note.
      event.preventDefault();
      const buttons = form.querySelectorAll("[data-decision-submit]");
      buttons.forEach((btn) => {
        btn.disabled = true;
      });
      const status = form.querySelector("[data-verify-status]");
      if (status) {
        status.textContent =
          decision === "accept"
            ? "Accepting request and printing delivery note…"
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
          if (status) {
            status.textContent = data.error || "Could not respond to this request.";
            status.classList.add("is-error");
            status.classList.remove("is-ok");
          } else {
            window.alert(data.error || "Could not respond to this request.");
          }
          buttons.forEach((btn) => {
            btn.disabled = form.dataset.codeVerified !== "1";
          });
          return;
        }

        if (
          decision === "accept" &&
          data.receipt_text &&
          window.RichcomPrinter?.printReceipt
        ) {
          if (status) {
            status.textContent = "Printing delivery note…";
          }
          try {
            await window.RichcomPrinter.printReceipt({
              text: data.receipt_text,
              channel: data.print_via || "",
              qr: data.receipt_qr || null,
              fontStyle: data.receipt_font || null,
              ticket: data.receipt_ticket || null,
              paperWidth: data.receipt_paper_width || "",
            });
          } catch (_printError) {
            /* Transfer already succeeded; continue to workspace. */
          }
        }

        window.location.assign(data.next || window.location.href);
      } catch (_error) {
        if (status) {
          status.textContent = "Network error while responding. Try again.";
          status.classList.add("is-error");
          status.classList.remove("is-ok");
        } else {
          window.alert("Network error while responding. Try again.");
        }
        buttons.forEach((btn) => {
          btn.disabled = form.dataset.codeVerified !== "1";
        });
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
      if (block && form) syncSerialBlockQty(form, block);
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
    const checkoutForm = drawer?.querySelector("[data-cart-checkout]");
    const whatsappWrap = checkoutForm?.querySelector("[data-cart-whatsapp-wrap]");
    const paymentWrap = checkoutForm?.querySelector("[data-cart-payment-wrap]");
    const splitWrap = checkoutForm?.querySelector("[data-cart-split]");
    const cashInput = checkoutForm?.querySelector("[data-cart-cash]");
    const mpesaInput = checkoutForm?.querySelector("[data-cart-mpesa]");
    const cartStatus = checkoutForm?.querySelector("[data-cart-status]");
    const cartSubmit = checkoutForm?.querySelector("[data-cart-submit]");
    const cartLoginCode = checkoutForm?.querySelector("[data-cart-login-code]");
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
    const stkReady = cartRoot.getAttribute("data-stk-ready") === "1";
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
    const defaultStatusMessage = (() => {
      if (!checkoutEnabled) {
        return "Checkout is disabled in POS settings.";
      }
      if (enabledPrintChannels.length && cartRoot.dataset.posCompulsoryPrint === "1") {
        return "Enter an active staff member’s 6-digit ID to complete and print.";
      }
      if (enabledPrintChannels.length) {
        return "Enter an active staff member’s 6-digit ID to complete. Connect a printer to print.";
      }
      return "Enter an active staff member’s 6-digit ID to complete the receipt.";
    })();
    let cartCodeVerified = false;
    let cartVerifyTimer = null;
    let cartVerifySeq = 0;
    let checkoutInFlight = false;
    let clientLookupTimer = null;
    let clientLookupSeq = 0;
    let clientNameSearchTimer = null;
    let clientNameSearchSeq = 0;
    let clientNameAutofilled = false;
    let clientPhoneAutofilled = false;
    let clientSuggestIndex = -1;
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

    [fab, overlay, drawer, productModal, serialSaleModal].forEach((el) => {
      if (el && el.parentElement !== document.body) {
        document.body.appendChild(el);
      }
    });

    /** @type {Map<string, {id:string, name:string, category:string, description:string, price:number, listPrice:number, minPrice:number, maxPrice:number, stock:number, image:string, qty:number, trackSerial?:boolean, serials?:string[]}>} */
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

    const setCartStatus = (message, { ok = false, error = false } = {}) => {
      if (!cartStatus) return;
      cartStatus.textContent = message || defaultStatusMessage;
      cartStatus.classList.toggle("is-ok", ok);
      cartStatus.classList.toggle("is-error", error);
    };

    const setCartVerified = (verified, message = "") => {
      cartCodeVerified = Boolean(verified);
      syncCheckoutMode();
      setCartStatus(
        message ||
          (verified
            ? "Staff verified — printing…"
            : ""),
        { ok: verified, error: Boolean(message) && !verified }
      );
    };

    const applyStockUpdates = (updates) => {
      if (!Array.isArray(updates) || !updates.length) return;
      updates.forEach((row) => {
        const id = String(row?.id || "");
        if (!id) return;
        const qty = Math.max(0, Math.floor(Number(row.quantity) || 0));
        cartRoot
          .querySelectorAll(
            `[data-cart-item][data-item-id="${CSS.escape(id)}"]`
          )
          .forEach((card) => {
            card.setAttribute("data-item-stock", String(qty));
            const valueEl = card.querySelector(".shop-floor-stock-value");
            if (valueEl) valueEl.textContent = String(qty);
            const stockWrap = card.querySelector(".shop-floor-stock");
            if (stockWrap) stockWrap.classList.toggle("is-empty", qty <= 0);
          });
        if (productModal?.dataset.itemId === id) {
          productModal.dataset.itemStock = String(qty);
          if (productStock) {
            productStock.textContent =
              qty > 0 ? `${qty} in stock` : "Out of stock";
            productStock.classList.toggle("is-empty", qty <= 0);
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

    const saleNeedsStk = () =>
      Boolean(
        stkReady &&
          selectedKind() === "sale" &&
          paymentsEnabled &&
          (selectedPayment() === "mpesa" || selectedPayment() === "both")
      );

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
      const needs = saleNeedsStk();
      if (stkPanel) stkPanel.hidden = !needs;

      const amount = mpesaPromptAmount();
      setStaffCodeLocked(false);

      if (!needs) {
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
        setStkButtonLabel("Waiting…", { disabled: true });
        setStkWaiting(true, "Waiting for customer to confirm on their phone…");
        return;
      }

      setStkWaiting(false);
      if (stkFailed) {
        setStkButtonLabel("Try again", {
          retry: true,
          disabled: !stkReady || amount < 1,
        });
      } else {
        setStkButtonLabel("Send STK prompt (optional)", {
          disabled: !stkReady || amount < 1,
        });
      }
      setCartStatus(
        "Optional: send STK prompt, or enter staff ID to finish without it."
      );
    };

    const sendStkPrompt = async () => {
      if (!saleNeedsStk() || stkSending || stkConfirmed) return;
      if (!stkInitiateUrl || !stkStatusTemplate) {
        stkFailed = true;
        setStkStatus("STK Push is unavailable. Refresh and try again.", {
          error: true,
        });
        syncStkPanel();
        return;
      }
      const phone = normalizeClientPhoneField({ force: true });
      if (!phone) {
        setStkStatus("Enter the client phone number for M-Pesa STK Push.", {
          error: true,
        });
        focusCartClientFields();
        return;
      }
      if (selectedPayment() === "both") {
        syncSplitAmounts(splitLastEdited);
      }
      const amount = mpesaPromptAmount();
      if (amount < 1) {
        setStkStatus("M-Pesa amount must be at least KSh 1.", { error: true });
        return;
      }

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
            phone,
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
                "Customer did not complete M-Pesa payment."
            );
          }
          const liveMsg =
            statusData.result_desc ||
            "Waiting for customer to confirm on their phone…";
          setStkWaiting(true, liveMsg);
          setCartStatus(liveMsg);
        }
        if (pollToken !== stkPollToken) return;
        if (!confirmed) {
          throw new Error("Timed out waiting for M-Pesa confirmation.");
        }

        stkConfirmed = {
          id: confirmed.id,
          mpesa_receipt_number: confirmed.mpesa_receipt_number || "",
          amount,
          phone,
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
        setStkButtonLabel("Try again", { retry: true, disabled: false });
        setCartStatus(err?.message || "STK Push failed.", { error: true });
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

    const setClientHint = (message = "", { ok = false } = {}) => {
      if (!clientHint) return;
      clientHint.textContent = message;
      clientHint.classList.toggle("is-ok", ok && Boolean(message));
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

    const normalizeClientName = () => {
      if (!clientNameInput) return;
      const start = clientNameInput.selectionStart;
      const end = clientNameInput.selectionEnd;
      const next = (clientNameInput.value || "").toUpperCase();
      if (clientNameInput.value !== next) {
        clientNameInput.value = next;
        if (typeof start === "number" && typeof end === "number") {
          clientNameInput.setSelectionRange(start, end);
        }
      }
    };

    const normalizeClientPhoneField = ({ force = false } = {}) => {
      if (!clientPhoneInput) return "";
      const raw = (clientPhoneInput.value || "").trim();
      if (!raw) return "";
      const formatted = toKenyaPhone(raw);
      if (formatted) {
        if (clientPhoneInput.value !== formatted) {
          clientPhoneInput.value = formatted;
        }
        return formatted;
      }
      if (force && raw) {
        setClientHint("Use a Kenyan number (07… or +2547…).");
      }
      return raw;
    };

    const cartHasSerialTracked = () =>
      [...cart.values()].some(
        (line) => line.trackSerial || (line.serials && line.serials.length)
      );

    const syncClientRequirements = () => {
      const hasSerials = cartHasSerialTracked();
      const required = selectedKind() !== "sale" || hasSerials;
      if (clientPhoneInput) clientPhoneInput.required = required;
      if (clientNameInput) clientNameInput.required = required;
      if (clientBlock) clientBlock.classList.toggle("is-required", required);
      if (clientHeading) {
        clientHeading.innerHTML = required
          ? 'Link client <span class="shop-serial-required" aria-hidden="true">*</span>'
          : "Link client";
      }
      if (clientNote) {
        if (hasSerials) {
          clientNote.hidden = false;
          clientNote.textContent =
            "Required — serial numbers in this cart are linked to these client details.";
        } else if (required) {
          clientNote.hidden = false;
          clientNote.textContent = "Required for credit and quotation.";
        } else {
          clientNote.hidden = false;
          clientNote.textContent = "Optional for cash sales without serials";
        }
      }
      if (clientPhoneLabel) {
        clientPhoneLabel.innerHTML = required
          ? 'Client phone <span class="shop-serial-required">*</span>'
          : 'Client phone <em>(optional)</em>';
      }
      if (clientNameLabel) {
        clientNameLabel.innerHTML = required
          ? 'Client full name <span class="shop-serial-required">*</span>'
          : 'Client full name <em>(optional)</em>';
      }
    };

    const focusCartClientFields = () => {
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

    const hideClientSuggest = () => {
      if (!clientSuggest) return;
      clientSuggest.hidden = true;
      clientSuggest.innerHTML = "";
      clientSuggestIndex = -1;
    };

    const applyClientSelection = (client) => {
      if (!client) return;
      if (clientNameInput) {
        clientNameInput.value = String(client.full_name || "").toUpperCase();
        clientNameAutofilled = true;
      }
      if (clientPhoneInput && client.phone_number) {
        clientPhoneInput.value = client.phone_number;
        clientPhoneAutofilled = true;
      }
      hideClientSuggest();
      setClientHint("Registered client selected.", { ok: true });
    };

    const renderClientSuggest = (results) => {
      if (!clientSuggest) return;
      clientSuggest.innerHTML = "";
      clientSuggestIndex = -1;
      if (!results.length) {
        const empty = document.createElement("div");
        empty.className = "shop-cart-client-suggest-empty";
        empty.textContent = "No matching clients";
        clientSuggest.appendChild(empty);
        clientSuggest.hidden = false;
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
          applyClientSelection(client);
        });
        clientSuggest.appendChild(btn);
      });
      clientSuggest.hidden = false;
    };

    const moveClientSuggest = (delta) => {
      if (!clientSuggest || clientSuggest.hidden) return;
      const options = [
        ...clientSuggest.querySelectorAll(".shop-cart-client-suggest-option"),
      ];
      if (!options.length) return;
      clientSuggestIndex =
        (clientSuggestIndex + delta + options.length) % options.length;
      options.forEach((el, idx) => {
        el.classList.toggle("is-active", idx === clientSuggestIndex);
      });
      options[clientSuggestIndex]?.scrollIntoView({ block: "nearest" });
    };

    const selectActiveClientSuggest = () => {
      if (!clientSuggest || clientSuggest.hidden) return false;
      const options = [
        ...clientSuggest.querySelectorAll(".shop-cart-client-suggest-option"),
      ];
      if (!options.length) return false;
      const active =
        options[clientSuggestIndex] ||
        options.find((el) => el.classList.contains("is-active")) ||
        options[0];
      active?.dispatchEvent(new Event("mousedown", { bubbles: true }));
      return true;
    };

    const searchClientsByName = async () => {
      normalizeClientName();
      const query = (clientNameInput?.value || "").trim();
      const seq = ++clientNameSearchSeq;
      if (query.length < 2) {
        hideClientSuggest();
        if (clientPhoneAutofilled && clientPhoneInput) {
          clientPhoneInput.value = "";
          clientPhoneAutofilled = false;
          setClientHint("");
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
        if (seq !== clientNameSearchSeq) return;
        if (!response.ok || !data.ok) {
          hideClientSuggest();
          return;
        }
        const results = Array.isArray(data.results) ? data.results : [];
        renderClientSuggest(results);
        if (!results.length) {
          setClientHint("No registered client with that name.");
        } else {
          setClientHint("Select a client to fill the phone number.");
        }
      } catch (_) {
        if (seq !== clientNameSearchSeq) return;
        hideClientSuggest();
      }
    };

    const queueClientNameSearch = () => {
      window.clearTimeout(clientNameSearchTimer);
      clientNameSearchTimer = window.setTimeout(() => {
        searchClientsByName();
      }, 220);
    };

    const lookupClientByPhone = async () => {
      normalizeClientPhoneField();
      const phone = (clientPhoneInput?.value || "").trim();
      const digits = phone.replace(/\D+/g, "");
      const seq = ++clientLookupSeq;

      if (digits.length < 12) {
        if (clientNameAutofilled && clientNameInput) {
          clientNameInput.value = "";
          clientNameAutofilled = false;
        }
        setClientHint("");
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
        if (seq !== clientLookupSeq) return;
        if (!response.ok || !data.ok) {
          setClientHint("");
          return;
        }
        if (data.found && data.full_name) {
          if (clientNameInput) {
            clientNameInput.value = String(data.full_name || "").toUpperCase();
            clientNameAutofilled = true;
          }
          if (data.phone_number && clientPhoneInput) {
            clientPhoneInput.value = data.phone_number;
            clientPhoneAutofilled = true;
          }
          hideClientSuggest();
          setClientHint("Registered client found.", { ok: true });
        } else {
          if (clientNameAutofilled && clientNameInput) {
            clientNameInput.value = "";
            clientNameAutofilled = false;
          }
          setClientHint("New client — will be saved on complete.");
        }
      } catch (_) {
        if (seq !== clientLookupSeq) return;
        setClientHint("");
      }
    };

    const queueClientLookup = () => {
      window.clearTimeout(clientLookupTimer);
      clientLookupTimer = window.setTimeout(() => {
        lookupClientByPhone();
      }, 280);
    };

    const syncCheckoutMode = () => {
      const kind = selectedKind();
      const isSale = kind === "sale";
      const isQuote = kind === "quotation";
      const isCredit = kind === "credit";
      if (creditDueWrap) creditDueWrap.hidden = !isCredit;
      if (creditDueInput) creditDueInput.required = isCredit;
      if (whatsappWrap) whatsappWrap.hidden = !isQuote;
      if (!isQuote && checkoutForm) {
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
        const body = new URLSearchParams({ login_code: code });
        const response = await fetch(cartVerifyUrl, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-CSRFToken": getCsrfToken(checkoutForm),
          },
          credentials: "same-origin",
          body,
        });
        const data = await response.json().catch(() => ({}));
        if (seq !== cartVerifySeq) return false;
        if (!response.ok || !data.ok) {
          setCartVerified(false, data.error || "Not a valid active staff ID.");
          return false;
        }
        setCartVerified(
          true,
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
      clientNameAutofilled = false;
      clientPhoneAutofilled = false;
      setClientHint("");
      hideClientSuggest();
      clearStkConfirmation();
      setStaffCodeLocked(false);
      setCartVerified(false);
      syncCheckoutMode();
    };

    const submitCheckout = async () => {
      if (checkoutInFlight) return;
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
      if (cartHasSerialTracked()) {
        const phone = normalizeClientPhoneField({ force: true });
        const name = (clientNameInput?.value || "").trim();
        if (!phone || !name) {
          setCartStatus(
            "Link a client (name and phone) for serial-tracked sales.",
            { error: true }
          );
          focusCartClientFields();
          return;
        }
      }
      const payload = {
        kind,
        client_name: (
          checkoutForm.querySelector("[data-cart-client-name]")?.value || ""
        ).trim().toUpperCase(),
        client_phone: normalizeClientPhoneField({ force: true }),
        login_code: (cartLoginCode?.value || "").trim(),
        share_whatsapp: Boolean(
          checkoutForm.querySelector("[data-cart-whatsapp]")?.checked
        ),
        lines: [...cart.values()].map((line) => ({
          id: line.id,
          qty: line.qty,
          price: discountEnabled ? line.price : line.listPrice || line.price,
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
        if (!dueDate) {
          setCartStatus("Enter the payment due date for this credit.", {
            error: true,
          });
          creditDueInput?.focus();
          return;
        }
        payload.credit_due_date = dueDate;
      }

      const needsStk =
        kind === "sale" &&
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
          setCartStatus(
            "Compulsory printing is on, but no print channels are enabled in settings.",
            { error: true }
          );
          return;
        }
        if (!printVia) {
          setCartStatus(
            "Connect an enabled printer from the sidebar before completing the sale.",
            { error: true }
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
          setCartStatus(
            "Connect a printer from the sidebar (Connect to printer) before completing the sale.",
            { error: true }
          );
          return;
        }
      }
      if (printVia && hasPrintChannels) payload.print_via = printVia;

      checkoutInFlight = true;
      if (cartSubmit) cartSubmit.disabled = true;
      setCartStatus(
        needsStk && stkConfirmed?.id
          ? "Completing M-Pesa sale…"
          : "Printing receipt…"
      );

      const finishSuccessfulCheckout = async (data, { queued = false } = {}) => {
        const soldLines = [...cart.values()].map((line) => ({
          id: line.id,
          qty: line.qty,
        }));
        cart.clear();
        renderCart();
        resetCheckoutForm();
        if (Array.isArray(data.stock_updates) && data.stock_updates.length) {
          applyStockUpdates(data.stock_updates);
        } else if (kind !== "quotation") {
          applyStockUpdates(
            soldLines.map((line) => {
              const card = cartRoot.querySelector(
                `[data-cart-item][data-item-id="${CSS.escape(String(line.id))}"]`
              );
              const current = Math.max(
                0,
                Math.floor(Number(card?.getAttribute("data-item-stock")) || 0)
              );
              return {
                id: line.id,
                quantity: Math.max(0, current - Math.max(0, line.qty || 0)),
              };
            })
          );
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
            kind === "quotation");
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
        const clientId =
          (crypto?.randomUUID && crypto.randomUUID()) ||
          `checkout-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const shopId = Number(cartRoot?.dataset?.shopId || 0) || 0;
        const { queueOperation } = await import("./offline/sync.js");
        await queueOperation("complete_shop_checkout", {
          client_id: clientId,
          shop_id: shopId,
          checkout: payload,
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
        const online = typeof navigator === "undefined" || navigator.onLine;
        if (!online) {
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
        if (!response.ok || !data.ok) {
          setCartStatus(data.error || "Could not complete the receipt.", {
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
      doc.open();
      doc.write(`<!doctype html><html><head><title>Receipt (${label})</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap">
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
</style></head><body><pre>${esc(text)}</pre>
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
      const maxPrice = roundMoney(
        el?.getAttribute?.("data-item-max-price") ||
          el?.dataset?.itemMaxPrice ||
          listPrice ||
          minPrice
      );
      const price = roundMoney(
        el?.getAttribute?.("data-item-price") || el?.dataset?.itemPrice || listPrice
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
        maxPrice,
        stock: Math.max(
          0,
          Math.floor(
            Number(
              el?.getAttribute?.("data-item-stock") || el?.dataset?.itemStock || 0
            )
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

    const clampPrice = (price, minPrice, maxPrice, listPrice = maxPrice) => {
      let next = roundMoney(price);
      if (!Number.isFinite(next)) next = listPrice > 0 ? listPrice : maxPrice;
      if (!discountEnabled) {
        return listPrice > 0 ? listPrice : Math.max(0, next);
      }
      const min = roundMoney(minPrice);
      const max = roundMoney(maxPrice);
      if (min > 0 && next < min) next = min;
      if (max > 0 && next > max) next = max;
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
          const qty = Math.max(1, Math.floor(Number(line.qty) || 1));
          const listPrice = Number(line.listPrice || line.price) || 0;
          const minPrice = Number(line.minPrice) || 0;
          const maxPrice = Number(line.maxPrice || line.listPrice || line.minPrice) || 0;
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
              ? clampPrice(storedPrice, minPrice, maxPrice, listPrice)
              : listPrice,
            listPrice,
            minPrice,
            maxPrice,
            stock: Math.max(0, Math.floor(Number(line.stock) || 0)),
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
      let next = Math.floor(Number(qty));
      if (!Number.isFinite(next) || next < 1) next = 0;
      if (stock > 0 && next > stock) next = stock;
      return next;
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
          maxPrice: 0,
          image: "",
        };
      const listPrice = roundMoney(base.listPrice ?? base.price ?? 0);
      const minPrice = roundMoney(base.minPrice ?? 0);
      const maxPrice = roundMoney(base.maxPrice ?? listPrice ?? minPrice);
      const price = clampPrice(base.price ?? listPrice, minPrice, maxPrice, listPrice);
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
        maxPrice,
        stock,
        image: base.image || "",
        qty: trackSerial && serials.length ? serials.length : nextQty,
        trackSerial,
        serials,
      });
    };

    const setLinePrice = (id, price, meta = null) => {
      if (!id) return null;
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
      const maxPrice = roundMoney(
        base.maxPrice ?? meta?.maxPrice ?? listPrice ?? minPrice
      );
      const nextPrice = clampPrice(price, minPrice, maxPrice, listPrice);
      if (existing) {
        existing.price = nextPrice;
        existing.listPrice = listPrice;
        existing.minPrice = minPrice;
        existing.maxPrice = maxPrice;
        cart.set(id, existing);
      }
      return nextPrice;
    };

    const syncPriceHint = (minPrice, maxPrice, salePrice, listPrice = maxPrice) => {
      if (!productPriceHint) return;
      if (!discountEnabled) {
        productPriceHint.textContent =
          listPrice > 0 ? `List ${money(listPrice)}` : "";
        productPriceHint.classList.remove("is-discount");
        return;
      }
      const parts = [];
      if (minPrice > 0) parts.push(`Min ${money(minPrice)}`);
      if (maxPrice > 0) parts.push(`Max ${money(maxPrice)}`);
      if (listPrice > 0) parts.push(`List ${money(listPrice)}`);
      if (salePrice + 0.0001 < listPrice) parts.push("Discount applied");
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
        const stock = Math.max(0, Math.floor(Number(card.getAttribute("data-item-stock")) || 0));

        card.classList.toggle("is-in-cart", inCart);
        if (addBtn) {
          if (stock <= 0) {
            addBtn.hidden = false;
            addBtn.disabled = true;
          } else {
            addBtn.hidden = inCart;
            addBtn.disabled = false;
          }
        }
        if (qtyWrap) qtyWrap.hidden = !inCart;
        if (qtyInput && line) {
          qtyInput.max = String(stock || "");
          qtyInput.value = String(line.qty);
        }
        const inc = card.querySelector('[data-cart-qty="inc"]');
        if (inc && line) inc.disabled = stock > 0 && line.qty >= stock;
      });
    };

    const syncProductControls = () => {
      if (!productModal || !activeProductId) return;
      const line = cart.get(activeProductId);
      const stock = Math.max(
        0,
        Math.floor(Number(productModal.dataset.itemStock || line?.stock || 0))
      );
      const inCart = Boolean(line);
      if (productAdd) {
        productAdd.hidden = inCart || stock <= 0;
        productAdd.disabled = stock <= 0;
        const label = productAdd.querySelector("span");
        if (label) label.textContent = stock <= 0 ? "Out of stock" : "Add to cart";
      }
      if (productQtyWrap) productQtyWrap.hidden = !inCart;
      if (productQtyInput && line) {
        productQtyInput.max = String(stock || "");
        productQtyInput.value = String(line.qty);
      }
      const inc = productModal.querySelector('[data-product-qty="inc"]');
      if (inc && line) inc.disabled = stock > 0 && line.qty >= stock;

      const listPrice = roundMoney(
        productModal.dataset.itemListPrice || line?.listPrice || 0
      );
      const minPrice = roundMoney(
        productModal.dataset.itemMinPrice || line?.minPrice || 0
      );
      const maxPrice = roundMoney(
        productModal.dataset.itemMaxPrice ||
          line?.maxPrice ||
          listPrice ||
          minPrice
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
        productPriceInput.max = String(
          discountEnabled ? maxPrice || 0 : listPrice || 0
        );
        productPriceInput.value = salePrice.toFixed(2);
        productPriceInput.readOnly = !discountEnabled;
        productPriceInput.tabIndex = discountEnabled ? 0 : -1;
      }
      productPriceInput
        ?.closest(".shop-product-price-field")
        ?.classList.toggle("is-locked", !discountEnabled);
      syncPriceHint(minPrice, maxPrice, salePrice, listPrice);
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
              clampPrice(item.listPrice, item.minPrice, item.maxPrice, item.listPrice)
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
      productModal.dataset.itemMaxPrice = String(item.maxPrice);
      productModal.dataset.itemImage = item.image;
      productModal.dataset.itemTrackSerial = item.trackSerial ? "1" : "0";

      if (productCategory) productCategory.textContent = item.category || "";
      if (productName) productName.textContent = item.name;
      if (productDescription) productDescription.textContent = item.description || "";
      if (productPriceInput) {
        productPriceInput.min = String(
          discountEnabled ? item.minPrice || 0 : item.listPrice || 0
        );
        productPriceInput.max = String(
          discountEnabled ? item.maxPrice || 0 : item.listPrice || 0
        );
        productPriceInput.value = salePrice.toFixed(2);
        productPriceInput.readOnly = !discountEnabled;
        productPriceInput.tabIndex = discountEnabled ? 0 : -1;
      }
      productPriceInput
        ?.closest(".shop-product-price-field")
        ?.classList.toggle("is-locked", !discountEnabled);
      syncPriceHint(item.minPrice, item.maxPrice, salePrice, item.listPrice);
      if (productStock) {
        productStock.textContent =
          item.stock > 0 ? `${item.stock} in stock` : "Out of stock";
        productStock.classList.toggle("is-empty", item.stock <= 0);
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
      proxy.setAttribute("data-item-max-price", String(line.maxPrice || line.listPrice || 0));
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

        const priceField = document.createElement("label");
        priceField.className = "shop-cart-price-field";
        if (!discountEnabled) priceField.classList.add("is-locked");
        const pricePrefix = document.createElement("span");
        pricePrefix.textContent = "KSh";
        const priceInput = document.createElement("input");
        priceInput.type = "number";
        priceInput.className = "shop-cart-price-input";
        priceInput.min = String(discountEnabled ? line.minPrice || 0 : line.listPrice || 0);
        priceInput.max = String(
          discountEnabled ? line.maxPrice || line.listPrice || 0 : line.listPrice || 0
        );
        priceInput.step = "0.01";
        priceInput.inputMode = "decimal";
        priceInput.value = roundMoney(
          discountEnabled ? line.price : line.listPrice || line.price
        ).toFixed(2);
        priceInput.readOnly = !discountEnabled;
        priceInput.tabIndex = discountEnabled ? 0 : -1;
        priceInput.setAttribute("aria-label", `Sale price for ${line.name}`);
        if (!discountEnabled) {
          priceInput.setAttribute("aria-readonly", "true");
        }
        priceInput.dataset.cartPriceInput = "";
        priceField.append(pricePrefix, priceInput);

        const priceHint = document.createElement("span");
        priceHint.className = "shop-cart-price-hint";
        if (discountEnabled && line.minPrice > 0 && line.maxPrice > 0) {
          priceHint.textContent = `Min ${money(line.minPrice)} · Max ${money(line.maxPrice)}`;
          if (
            line.listPrice > 0 &&
            line.price + 0.0001 < line.listPrice
          ) {
            priceHint.classList.add("is-discount");
          }
        } else if (discountEnabled && line.minPrice > 0) {
          priceHint.textContent = `Min ${money(line.minPrice)}`;
        } else if (discountEnabled && line.maxPrice > 0) {
          priceHint.textContent = `Max ${money(line.maxPrice)}`;
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
        qtyInput.type = "number";
        qtyInput.className = "shop-cart-qty-input";
        qtyInput.min = "1";
        if (line.stock > 0) qtyInput.max = String(line.stock);
        qtyInput.value = String(line.qty);
        qtyInput.inputMode = "numeric";
        qtyInput.setAttribute("aria-label", "Quantity");
        qtyInput.dataset.cartQtyInput = "";

        const inc = document.createElement("button");
        inc.type = "button";
        inc.setAttribute("aria-label", "Increase quantity");
        inc.dataset.cartQty = "inc";
        inc.innerHTML = '<i data-lucide="plus" aria-hidden="true"></i>';
        if (line.stock > 0 && line.qty >= line.stock) inc.disabled = true;

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
        serialSaleItem?.stock > 0 &&
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
        if (isSerialSaleLast4Mode() && !resolved && serial.length <= 4) {
          if (!serialSearchUrl) {
            setSerialSaleStatus("Select a full serial from the suggestions.", {
              error: true,
            });
            return false;
          }
          const params = new URLSearchParams({
            item_id: String(serialSaleItem.id),
            shop_id: String(shopId),
            q: serial.slice(-4),
            match: "last4",
          });
          collectSerialSaleValues().forEach((s) => params.append("exclude", s));
          const response = await fetch(`${serialSearchUrl}?${params.toString()}`, {
            headers: { Accept: "application/json" },
            credentials: "same-origin",
          });
          const data = await response.json().catch(() => ({}));
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
            "No in-stock serial ends with those digits. Check and try again.",
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

        const params = new URLSearchParams({
          item_id: String(serialSaleItem.id),
          shop_id: String(shopId),
          q: serial,
          match: isSerialSaleLast4Mode() ? "last4" : "contains",
        });
        collectSerialSaleValues().forEach((s) => params.append("exclude", s));
        const response = await fetch(`${serialSearchUrl}?${params.toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
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
        setSerialSaleStatus("Serial not available at this shop.", { error: true });
        return false;
      } catch (_err) {
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
      const params = new URLSearchParams({
        item_id: String(serialSaleItem.id),
        shop_id: String(shopId),
        q,
        match: isSerialSaleLast4Mode() ? "last4" : "contains",
      });
      others.forEach((serial) => params.append("exclude", serial));
      try {
        const response = await fetch(`${serialSearchUrl}?${params.toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const data = await response.json().catch(() => ({}));
        if (seq !== serialSaleSearchSeq) return;
        if (!response.ok) {
          hideSerialSaleSuggest(serialSaleEntryWrap);
          setSerialSaleStatus(data.error || "Could not search serials.", {
            error: true,
          });
          return;
        }
        const results = Array.isArray(data.results) ? data.results : [];
        renderSerialSaleSuggest(input, results);
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
        item.maxPrice ?? existing?.maxPrice ?? item.listPrice ?? 0,
        item.listPrice ?? existing?.listPrice ?? 0
      );
      serialSaleItem = {
        ...item,
        id: String(item.id),
        price: salePrice,
        listPrice: roundMoney(item.listPrice ?? existing?.listPrice ?? salePrice),
        minPrice: roundMoney(item.minPrice ?? existing?.minPrice ?? 0),
        maxPrice: roundMoney(
          item.maxPrice ?? existing?.maxPrice ?? item.listPrice ?? salePrice
        ),
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
        const stock = Math.max(0, Math.floor(Number(serialSaleItem.stock) || 0));
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
      if (serialSaleItem.stock > 0 && serials.length > serialSaleItem.stock) {
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

      const phone = (clientPhoneInput?.value || "").trim();
      const name = (clientNameInput?.value || "").trim();
      if (!phone || !name) {
        setCartStatus(
          "Link a client in the cart — required for serial-tracked sales.",
          { error: true }
        );
        focusCartClientFields();
      } else {
        setCartOpen(true);
      }
      return true;
    };

    const addItem = (sourceEl) => {
      if (!checkoutEnabled) return;
      const item = readItemFromEl(sourceEl);
      if (!item.id || item.stock <= 0) return;
      if (item.trackSerial || cart.get(item.id)?.trackSerial) {
        const existing = cart.get(item.id);
        const salePrice = clampPrice(
          sourceEl === productModal
            ? productPriceInput?.value ?? item.price
            : existing?.price ?? item.listPrice,
          item.minPrice,
          item.maxPrice,
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
        item.maxPrice,
        item.listPrice
      );
      if (productModal && sourceEl === productModal) {
        productModal.dataset.itemPrice = String(salePrice);
        if (productPriceInput) productPriceInput.value = salePrice.toFixed(2);
      }
      setQty(item.id, (existing?.qty || 0) + 1, {
        ...item,
        price: existing ? existing.price : salePrice,
        listPrice: item.listPrice,
        minPrice: item.minPrice,
        maxPrice: item.maxPrice,
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
        creditDueInput.min = new Date().toISOString().slice(0, 10);
      }
      renderCart();
      syncCheckoutMode();
    }

    document.addEventListener("shop-catalog:rendered", () => {
      syncCardControls();
      if (window.lucide?.createIcons) window.lucide.createIcons();
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
      verifyCartLoginCode({ autoCheckout: true });
    });

    clientPhoneInput?.addEventListener("input", () => {
      clientPhoneAutofilled = false;
      hideClientSuggest();
      queueClientLookup();
    });
    clientPhoneInput?.addEventListener("blur", () => {
      normalizeClientPhoneField({ force: true });
      lookupClientByPhone();
    });
    clientNameInput?.addEventListener("input", () => {
      clientNameAutofilled = false;
      normalizeClientName();
      queueClientNameSearch();
    });
    clientNameInput?.addEventListener("keydown", (event) => {
      if (!clientSuggest || clientSuggest.hidden) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        moveClientSuggest(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        moveClientSuggest(-1);
      } else if (event.key === "Enter") {
        if (selectActiveClientSuggest()) event.preventDefault();
      } else if (event.key === "Escape") {
        hideClientSuggest();
      }
    });
    clientNameInput?.addEventListener("blur", () => {
      normalizeClientName();
      window.setTimeout(() => hideClientSuggest(), 120);
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
      const maxPrice = roundMoney(
        productModal.dataset.itemMaxPrice || productModal.dataset.itemListPrice || 0
      );
      const listPrice = roundMoney(productModal.dataset.itemListPrice || 0);
      if (!discountEnabled) {
        productPriceInput.value = listPrice.toFixed(2);
        productModal.dataset.itemPrice = String(listPrice);
        syncPriceHint(minPrice, maxPrice, listPrice, listPrice);
        if (cart.has(activeProductId)) {
          setLinePrice(activeProductId, listPrice, {
            listPrice,
            minPrice,
            maxPrice,
          });
          renderCart();
        }
        return;
      }
      const nextPrice = clampPrice(
        productPriceInput.value,
        minPrice,
        maxPrice,
        listPrice
      );
      productPriceInput.value = nextPrice.toFixed(2);
      productModal.dataset.itemPrice = String(nextPrice);
      syncPriceHint(minPrice, maxPrice, nextPrice, listPrice);

      if (cart.has(activeProductId)) {
        setLinePrice(activeProductId, nextPrice, {
          listPrice,
          minPrice,
          maxPrice,
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
      if (!line) return;
      const action = qtyBtn.getAttribute("data-product-qty");
      setQty(activeProductId, action === "inc" ? line.qty + 1 : line.qty - 1, line);
      renderCart();
    });

    productQtyInput?.addEventListener("change", () => {
      if (!activeProductId) return;
      const line = cart.get(activeProductId);
      if (!line) return;
      if (line.trackSerial) {
        openSerialSaleModal(line);
        renderCart();
        return;
      }
      setQty(activeProductId, productQtyInput.value, line);
      renderCart();
    });

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
