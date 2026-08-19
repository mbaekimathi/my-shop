(() => {
  const root = document.querySelector("[data-low-stock-settings]");
  if (!root) return;

  const canEdit = root.getAttribute("data-can-edit") === "1";
  const list = root.querySelector("[data-low-stock-list]");
  const searchInput = root.querySelector("[data-low-stock-search]");
  const noResults = root.querySelector("[data-low-stock-no-results]");
  const visibleCountEl = root.querySelector("[data-low-stock-visible-count]");
  const notifyCountEl = root.querySelector("[data-low-stock-notify-count]");
  const syncAllBtn = root.querySelector("[data-low-stock-sync-all]");
  const notifyAllInput = root.querySelector("[data-low-stock-notify-all]");

  const readCookie = (name) => {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) {
      return decodeURIComponent(parts.pop().split(";").shift() || "");
    }
    return "";
  };

  const csrfToken =
    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    readCookie("csrftoken") ||
    "";

  const itemRows = (itemId) => {
    if (!list || !itemId) return [];
    return Array.from(
      list.querySelectorAll(`[data-low-stock-row][data-item-id="${itemId}"]`)
    );
  };

  const setBusy = (rows, busy) => {
    rows.forEach((row) => row.classList.toggle("is-saving", busy));
  };

  const setStateLabel = (input, enabled, { allItems } = {}) => {
    const wrap = input.closest(".perm-switch");
    const label = wrap?.querySelector(".perm-switch-state");
    if (label) {
      if (allItems) label.textContent = enabled ? "All on" : "Notify all";
      else label.textContent = enabled ? "On" : "Off";
    }
    wrap?.classList.toggle("is-denied", !enabled);
  };

  const setBadge = (row, { notify, isLow }) => {
    const badge = row.querySelector("[data-low-stock-badge]");
    if (!badge) return;
    badge.classList.remove("is-low", "is-ok", "is-off");
    if (isLow) {
      badge.classList.add("is-low");
      badge.textContent = "Low now";
    } else if (notify) {
      badge.classList.add("is-ok");
      badge.textContent = "Watching";
    } else {
      badge.classList.add("is-off");
      badge.textContent = "Off";
    }
    row.classList.toggle("is-low", Boolean(isLow));
    row.dataset.notify = notify ? "1" : "0";
  };

  const applyThresholdInput = (input, shop, row) => {
    if (!input) return;
    const manual = Boolean(shop.manual);
    const effective = shop.threshold;
    const autoValue = shop.auto_threshold ?? shop.threshold ?? 0;
    const field = input.closest(".low-stock-threshold-field");
    const hint = field?.querySelector("[data-low-stock-threshold-hint]");
    const shown = effective != null ? effective : autoValue;
    input.value = String(shown ?? 0);
    input.placeholder = String(autoValue);
    input.defaultValue = input.value;
    field?.classList.toggle("is-manual", manual);
    field?.classList.toggle("is-auto", !manual);
    if (hint) hint.textContent = manual ? "set" : "avg";
    if (row) {
      row.dataset.manual = manual ? "1" : "0";
      row.dataset.effectiveThreshold = String(effective ?? autoValue ?? 0);
    }
  };

  const applyItemSnapshot = (data) => {
    if (!data || !data.item_id) return;
    const shops = Array.isArray(data.shops) ? data.shops : [];
    const shopsById = new Map(
      shops.map((shop) => [String(shop.shop_id || ""), shop])
    );
    const notify = Boolean(data.notify);
    itemRows(String(data.item_id)).forEach((row) => {
      const unitsEl = row.querySelector("[data-low-stock-units]");
      const thresholdInput = row.querySelector("[data-low-stock-threshold]");
      const notifyInput = row.querySelector("[data-low-stock-notify]");
      if (notifyInput) {
        notifyInput.checked = notify;
        setStateLabel(notifyInput, notify);
      }
      if (row.classList.contains("is-item-total")) {
        if (unitsEl && data.total_units != null) {
          unitsEl.textContent = String(data.total_units);
        }
        setBadge(row, { notify, isLow: data.is_low });
        return;
      }
      const shop = shopsById.get(row.dataset.shopId || "");
      if (shop) {
        if (unitsEl) unitsEl.textContent = String(shop.units ?? 0);
        applyThresholdInput(thresholdInput, shop, row);
        setBadge(row, { notify, isLow: shop.is_low });
        return;
      }
      setBadge(row, { notify, isLow: data.is_low });
    });
  };

  const syncNotifyCount = () => {
    if (!notifyCountEl || !list) return;
    const boxes = list.querySelectorAll("[data-low-stock-notify]");
    const onCount = list.querySelectorAll("[data-low-stock-notify]:checked").length;
    notifyCountEl.textContent = String(onCount);
    if (notifyAllInput) {
      const allOn = boxes.length > 0 && onCount === boxes.length;
      notifyAllInput.checked = allOn;
      setStateLabel(notifyAllInput, allOn, { allItems: true });
    }
  };

  const applyNotifyEverywhere = (notify) => {
    if (!list) return;
    const groups = new Map();
    list.querySelectorAll("[data-low-stock-row]").forEach((row) => {
      const id = row.dataset.itemId || "";
      if (!groups.has(id)) groups.set(id, []);
      groups.get(id).push(row);
    });
    groups.forEach((rows) => {
      let anyLow = false;
      rows.forEach((row) => {
        if (row.classList.contains("is-item-total")) return;
        const units = Number(
          row.querySelector("[data-low-stock-units]")?.textContent || 0
        );
        const threshold = Number(
          row.dataset.effectiveThreshold ||
            row.querySelector("[data-low-stock-threshold]")?.placeholder ||
            row.querySelector("[data-low-stock-threshold]")?.value ||
            0
        );
        const isLow = Boolean(notify && units <= threshold);
        anyLow = anyLow || isLow;
        setBadge(row, { notify, isLow });
        const notifyInput = row.querySelector("[data-low-stock-notify]");
        if (notifyInput) {
          notifyInput.checked = notify;
          setStateLabel(notifyInput, notify);
        }
      });
      rows.forEach((row) => {
        if (!row.classList.contains("is-item-total")) return;
        setBadge(row, { notify, isLow: anyLow });
      });
    });
    syncNotifyCount();
  };

  const filterRows = () => {
    if (!list) return;
    const q = (searchInput?.value || "").trim().toLowerCase();
    const groups = new Map();
    list.querySelectorAll("[data-low-stock-row]").forEach((row) => {
      const id = row.dataset.itemId || "";
      if (!groups.has(id)) groups.set(id, []);
      groups.get(id).push(row);
    });
    let visibleItems = 0;
    groups.forEach((rows) => {
      const show =
        !q ||
        rows.some((row) => (row.getAttribute("data-search-text") || "").includes(q));
      rows.forEach((row) => {
        row.hidden = !show;
      });
      if (show) visibleItems += 1;
    });
    if (noResults) noResults.hidden = visibleItems !== 0;
    if (visibleCountEl) {
      if (q) {
        visibleCountEl.hidden = false;
        visibleCountEl.textContent = `${visibleItems} match${visibleItems === 1 ? "" : "es"}`;
      } else {
        visibleCountEl.hidden = true;
      }
    }
  };

  const postForm = async (fields) => {
    const body = new URLSearchParams({
      mode: "low-stock",
      ajax: "1",
      ...fields,
    });
    const response = await fetch(window.location.href, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrfToken,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body,
      credentials: "same-origin",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Could not save low stock setting.");
    }
    return data;
  };

  const saveNotify = async (row, { revert } = {}) => {
    const notifyInput = row.querySelector("[data-low-stock-notify]");
    if (!notifyInput) return;
    const previous = revert || { notify: row.dataset.notify === "1" };
    const group = itemRows(row.dataset.itemId);
    setBusy(group, true);
    notifyInput.disabled = true;
    try {
      const data = await postForm({
        action: "save_low_stock",
        item_id: row.dataset.itemId || "",
        notify: notifyInput.checked ? "1" : "0",
      });
      applyItemSnapshot(data);
      syncNotifyCount();
    } catch (error) {
      notifyInput.checked = Boolean(previous.notify);
      setStateLabel(notifyInput, notifyInput.checked);
      syncNotifyCount();
      window.alert(error.message || "Could not save low stock setting.");
    } finally {
      setBusy(group, false);
      if (canEdit) notifyInput.disabled = false;
    }
  };

  const saveThreshold = async (row, { revert } = {}) => {
    const thresholdInput = row.querySelector("[data-low-stock-threshold]");
    if (!thresholdInput || !row.dataset.shopId) return;
    const previous = revert || {
      threshold: thresholdInput.defaultValue || thresholdInput.value,
    };
    const group = itemRows(row.dataset.itemId);
    setBusy(group, true);
    thresholdInput.disabled = true;
    try {
      const data = await postForm({
        action: "save_low_stock",
        item_id: row.dataset.itemId || "",
        shop_id: row.dataset.shopId || "",
        threshold: thresholdInput.value,
      });
      applyItemSnapshot(data);
    } catch (error) {
      thresholdInput.value = previous.threshold ?? "";
      window.alert(error.message || "Could not save low stock setting.");
    } finally {
      setBusy(group, false);
      if (canEdit) thresholdInput.disabled = false;
    }
  };

  const syncItems = async ({ itemId, button } = {}) => {
    const rows = itemId
      ? itemRows(itemId)
      : Array.from(list?.querySelectorAll("[data-low-stock-row]") || []);
    setBusy(rows, true);
    if (button) button.disabled = true;
    try {
      const fields = { action: "sync_low_stock" };
      if (itemId) fields.item_id = itemId;
      const data = await postForm(fields);
      (data.items || []).forEach(applyItemSnapshot);
      syncNotifyCount();
    } catch (error) {
      window.alert(error.message || "Could not sync averages.");
    } finally {
      setBusy(rows, false);
      if (button) button.disabled = false;
    }
  };

  if (searchInput) {
    searchInput.addEventListener("input", filterRows);
  }

  if (canEdit && list) {
    list.querySelectorAll("[data-low-stock-row]").forEach((row) => {
      const notifyInput = row.querySelector("[data-low-stock-notify]");
      const thresholdInput = row.querySelector("[data-low-stock-threshold]");
      const syncBtn = row.querySelector("[data-low-stock-sync]");
      if (notifyInput) {
        setStateLabel(notifyInput, notifyInput.checked);
        notifyInput.addEventListener("change", () => {
          saveNotify(row, { revert: { notify: !notifyInput.checked } });
        });
      }
      if (thresholdInput) {
        thresholdInput.defaultValue = thresholdInput.value;
        let timer = null;
        const queueSave = () => {
          const previous = {
            threshold: thresholdInput.defaultValue || "",
          };
          window.clearTimeout(timer);
          timer = window.setTimeout(
            () => saveThreshold(row, { revert: previous }),
            450
          );
        };
        thresholdInput.addEventListener("change", queueSave);
        thresholdInput.addEventListener("blur", () => {
          if (thresholdInput.value === thresholdInput.defaultValue) return;
          queueSave();
        });
      }
      if (syncBtn) {
        syncBtn.addEventListener("click", () => {
          syncItems({ itemId: row.dataset.itemId, button: syncBtn });
        });
      }
    });
  }

  if (canEdit && syncAllBtn) {
    syncAllBtn.addEventListener("click", () => {
      syncItems({ button: syncAllBtn });
    });
  }

  if (canEdit && notifyAllInput) {
    setStateLabel(notifyAllInput, notifyAllInput.checked, { allItems: true });
    notifyAllInput.addEventListener("change", async () => {
      const notify = notifyAllInput.checked;
      const previous = !notify;
      const rows = Array.from(list?.querySelectorAll("[data-low-stock-row]") || []);
      setBusy(rows, true);
      notifyAllInput.disabled = true;
      try {
        const data = await postForm({
          action: "notify_all_low_stock",
          notify: notify ? "1" : "0",
        });
        applyNotifyEverywhere(Boolean(data.notify));
      } catch (error) {
        notifyAllInput.checked = previous;
        setStateLabel(notifyAllInput, previous, { allItems: true });
        window.alert(error.message || "Could not update notify for all items.");
      } finally {
        setBusy(rows, false);
        notifyAllInput.disabled = false;
      }
    });
  }

  syncNotifyCount();
  filterRows();
})();
