(() => {
  const root = document.querySelector("[data-low-stock-settings]");
  if (!root) return;

  const canEdit = root.getAttribute("data-can-edit") === "1";
  const list = root.querySelector("[data-low-stock-list]");
  const searchInput = root.querySelector("[data-low-stock-search]");
  const noResults = root.querySelector("[data-low-stock-no-results]");
  const visibleCountEl = root.querySelector("[data-low-stock-visible-count]");
  const notifyCountEl = root.querySelector("[data-low-stock-notify-count]");

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

  const setStateLabel = (input, enabled) => {
    const wrap = input.closest(".perm-switch");
    const label = wrap?.querySelector(".perm-switch-state");
    if (label) label.textContent = enabled ? "On" : "Off";
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

  const syncNotifyCount = () => {
    if (!notifyCountEl || !list) return;
    notifyCountEl.textContent = String(
      list.querySelectorAll("[data-low-stock-notify]:checked").length
    );
  };

  const filterRows = () => {
    if (!list) return;
    const q = (searchInput?.value || "").trim().toLowerCase();
    let visible = 0;
    list.querySelectorAll("[data-low-stock-row]").forEach((row) => {
      const hay = row.getAttribute("data-search-text") || "";
      const show = !q || hay.includes(q);
      row.hidden = !show;
      if (show) visible += 1;
    });
    if (noResults) noResults.hidden = visible !== 0;
    if (visibleCountEl) {
      if (q) {
        visibleCountEl.hidden = false;
        visibleCountEl.textContent = `${visible} match${visible === 1 ? "" : "es"}`;
      } else {
        visibleCountEl.hidden = true;
      }
    }
  };

  const postRow = async (row) => {
    const notifyInput = row.querySelector("[data-low-stock-notify]");
    const thresholdInput = row.querySelector("[data-low-stock-threshold]");
    const body = new URLSearchParams({
      mode: "low-stock",
      action: "save_low_stock",
      item_id: row.dataset.itemId || "",
      notify: notifyInput?.checked ? "1" : "0",
      threshold: thresholdInput?.value || "0",
      ajax: "1",
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

  const saveRow = async (row, { revert } = {}) => {
    const notifyInput = row.querySelector("[data-low-stock-notify]");
    const thresholdInput = row.querySelector("[data-low-stock-threshold]");
    if (!notifyInput || !thresholdInput) return;

    const previous = revert || {
      notify: row.dataset.notify === "1",
      threshold: thresholdInput.defaultValue || thresholdInput.value,
    };

    row.classList.add("is-saving");
    notifyInput.disabled = true;
    thresholdInput.disabled = true;

    try {
      const data = await postRow(row);
      notifyInput.checked = Boolean(data.notify);
      thresholdInput.value = String(data.threshold ?? 0);
      thresholdInput.defaultValue = thresholdInput.value;
      setStateLabel(notifyInput, notifyInput.checked);
      setBadge(row, { notify: data.notify, isLow: data.is_low });
      const unitsEl = row.querySelector("[data-low-stock-units]");
      if (unitsEl && data.total_units != null) {
        unitsEl.textContent = String(data.total_units);
      }
      syncNotifyCount();
    } catch (error) {
      notifyInput.checked = Boolean(previous.notify);
      thresholdInput.value = String(previous.threshold ?? 0);
      setStateLabel(notifyInput, notifyInput.checked);
      setBadge(row, {
        notify: previous.notify,
        isLow:
          previous.notify &&
          Number(row.querySelector("[data-low-stock-units]")?.textContent || 0) <=
            Number(previous.threshold || 0),
      });
      syncNotifyCount();
      window.alert(error.message || "Could not save low stock setting.");
    } finally {
      row.classList.remove("is-saving");
      if (canEdit) {
        notifyInput.disabled = false;
        thresholdInput.disabled = false;
      }
    }
  };

  if (searchInput) {
    searchInput.addEventListener("input", filterRows);
  }

  if (canEdit && list) {
    list.querySelectorAll("[data-low-stock-row]").forEach((row) => {
      const notifyInput = row.querySelector("[data-low-stock-notify]");
      const thresholdInput = row.querySelector("[data-low-stock-threshold]");
      if (notifyInput) {
        setStateLabel(notifyInput, notifyInput.checked);
        notifyInput.addEventListener("change", () => {
          const previous = {
            notify: !notifyInput.checked,
            threshold: thresholdInput?.defaultValue || thresholdInput?.value || "0",
          };
          if (notifyInput.checked) {
            const current = Number(thresholdInput?.value || 0);
            if (current < 1 && thresholdInput) {
              thresholdInput.value = "5";
            }
          }
          saveRow(row, { revert: previous });
        });
      }
      if (thresholdInput) {
        thresholdInput.defaultValue = thresholdInput.value;
        let timer = null;
        const queueSave = () => {
          const previous = {
            notify: notifyInput?.checked || false,
            threshold: thresholdInput.defaultValue || "0",
          };
          window.clearTimeout(timer);
          timer = window.setTimeout(() => saveRow(row, { revert: previous }), 450);
        };
        thresholdInput.addEventListener("change", queueSave);
        thresholdInput.addEventListener("blur", () => {
          if (thresholdInput.value === thresholdInput.defaultValue) return;
          queueSave();
        });
      }
    });
  }

  syncNotifyCount();
  filterRows();
})();
