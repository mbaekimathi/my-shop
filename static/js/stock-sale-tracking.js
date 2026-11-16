(() => {
  const root = document.querySelector("[data-sale-tracking]");
  if (!root) return;

  const canEdit = root.getAttribute("data-can-edit") === "1";
  const list = root.querySelector("[data-sale-tracking-list]");
  const searchInput = root.querySelector("[data-sale-tracking-search]");
  const noResults = root.querySelector("[data-sale-tracking-no-results]");
  const visibleCountEl = root.querySelector("[data-sale-tracking-visible-count]");
  const trackedCountEl = root.querySelector("[data-sale-tracking-count]");
  const trackAllInput = root.querySelector("[data-sale-tracking-all]");

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

  const setStateLabel = (input, enabled, { allItems } = {}) => {
    const wrap = input.closest(".perm-switch");
    const label = wrap?.querySelector(".perm-switch-state");
    if (label) {
      if (allItems) label.textContent = enabled ? "All on" : "Track all";
      else label.textContent = enabled ? "On" : "Off";
    }
    wrap?.classList.toggle("is-denied", !enabled);
  };

  const setBadge = (row, enabled) => {
    const badge = row.querySelector("[data-sale-tracking-badge]");
    if (!badge) return;
    badge.classList.remove("is-ok", "is-off");
    badge.classList.add(enabled ? "is-ok" : "is-off");
    badge.textContent = enabled ? "Auto-deduct" : "Manual";
    row.dataset.tracked = enabled ? "1" : "0";
  };

  const syncCounts = () => {
    const boxes = list
      ? list.querySelectorAll("[data-sale-tracking-toggle]")
      : [];
    const onCount = list
      ? list.querySelectorAll("[data-sale-tracking-toggle]:checked").length
      : 0;
    if (trackedCountEl) trackedCountEl.textContent = String(onCount);
    if (trackAllInput) {
      const allOn = boxes.length > 0 && onCount === boxes.length;
      trackAllInput.checked = allOn;
      setStateLabel(trackAllInput, allOn, { allItems: true });
    }
  };

  const filterRows = () => {
    if (!list) return;
    const q = (searchInput?.value || "").trim().toLowerCase();
    let visible = 0;
    list.querySelectorAll("[data-sale-tracking-row]").forEach((row) => {
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

  const postForm = async (fields) => {
    const body = new URLSearchParams({
      mode: "sale-tracking",
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
      throw new Error(data.error || "Could not save stock tracking.");
    }
    return data;
  };

  const applyEverywhere = (enabled) => {
    if (!list) return;
    list.querySelectorAll("[data-sale-tracking-row]").forEach((row) => {
      const input = row.querySelector("[data-sale-tracking-toggle]");
      if (input) {
        input.checked = enabled;
        setStateLabel(input, enabled);
      }
      setBadge(row, enabled);
    });
    syncCounts();
  };

  if (searchInput) {
    searchInput.addEventListener("input", filterRows);
  }

  if (canEdit && list) {
    list.querySelectorAll("[data-sale-tracking-row]").forEach((row) => {
      const input = row.querySelector("[data-sale-tracking-toggle]");
      if (!input) return;
      setStateLabel(input, input.checked);
      input.addEventListener("change", async () => {
        const enabled = input.checked;
        const previous = !enabled;
        input.disabled = true;
        row.classList.add("is-saving");
        try {
          const data = await postForm({
            action: "toggle_sale_tracking",
            item_id: row.dataset.itemId || "",
            enabled: enabled ? "1" : "0",
          });
          const next = Boolean(data.deduct_stock_on_sale);
          input.checked = next;
          setStateLabel(input, next);
          setBadge(row, next);
          syncCounts();
        } catch (error) {
          input.checked = previous;
          setStateLabel(input, previous);
          setBadge(row, previous);
          syncCounts();
          window.alert(error.message || "Could not save stock tracking.");
        } finally {
          input.disabled = false;
          row.classList.remove("is-saving");
        }
      });
    });
  }

  if (canEdit && trackAllInput) {
    setStateLabel(trackAllInput, trackAllInput.checked, { allItems: true });
    trackAllInput.addEventListener("change", async () => {
      const enabled = trackAllInput.checked;
      const previous = !enabled;
      const rows = Array.from(
        list?.querySelectorAll("[data-sale-tracking-row]") || []
      );
      rows.forEach((row) => row.classList.add("is-saving"));
      trackAllInput.disabled = true;
      try {
        const data = await postForm({
          action: "track_all_sale_stock",
          enabled: enabled ? "1" : "0",
        });
        applyEverywhere(Boolean(data.enabled));
      } catch (error) {
        trackAllInput.checked = previous;
        setStateLabel(trackAllInput, previous, { allItems: true });
        window.alert(error.message || "Could not update all items.");
      } finally {
        rows.forEach((row) => row.classList.remove("is-saving"));
        trackAllInput.disabled = false;
      }
    });
  }

  syncCounts();
  filterRows();
})();
