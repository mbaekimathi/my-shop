(() => {
  const root = document.querySelector("[data-item-discounts]");
  if (!root) return;

  const canEdit = root.dataset.canEdit === "1";
  const search = root.querySelector("[data-item-discount-search]");
  const rows = Array.from(root.querySelectorAll("[data-item-discount-row]"));
  const empty = root.querySelector("[data-item-discount-empty]");
  const countEl = root.querySelector("[data-item-visible-count]");
  const total = rows.length;
  const csrf =
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.querySelector("[data-item-csrf]")?.value ||
    "";

  const syncSearch = () => {
    const q = (search?.value || "").trim().toLowerCase();
    let visible = 0;
    rows.forEach((row) => {
      const hay = row.getAttribute("data-search-text") || "";
      const show = !q || hay.includes(q);
      row.hidden = !show;
      if (show) visible += 1;
    });
    if (empty) empty.hidden = visible !== 0;
    if (countEl) {
      countEl.textContent = q
        ? `${visible} of ${total} item${total === 1 ? "" : "s"}`
        : `${total} item${total === 1 ? "" : "s"}`;
    }
  };

  search?.addEventListener("input", syncSearch);

  if (!canEdit) return;

  const readValues = (row) => ({
    wholesale_from_qty: row.querySelector("[data-wholesale-from-qty]")?.value ?? "0",
    wholesale_price: row.querySelector("[data-wholesale-price]")?.value ?? "",
  });

  const markDirty = (row) => {
    const saveBtn = row.querySelector("[data-discount-save]");
    const status = row.querySelector("[data-discount-status]");
    if (saveBtn) saveBtn.disabled = false;
    if (status) {
      status.hidden = true;
      status.textContent = "";
      status.classList.remove("is-error", "is-ok");
    }
  };

  const setStatus = (row, message, { ok = true } = {}) => {
    const status = row.querySelector("[data-discount-status]");
    if (!status) return;
    status.hidden = !message;
    status.textContent = message || "";
    status.classList.toggle("is-error", !ok);
    status.classList.toggle("is-ok", Boolean(ok && message));
  };

  const saveRow = async (row) => {
    const saveBtn = row.querySelector("[data-discount-save]");
    const itemId = row.getAttribute("data-item-id");
    if (!itemId || !saveBtn || saveBtn.disabled) return;

    const values = readValues(row);
    const body = new URLSearchParams({
      action: "save_discount",
      item_id: itemId,
      ajax: "1",
      ...values,
    });

    saveBtn.disabled = true;
    saveBtn.classList.add("is-busy");
    setStatus(row, "Saving…");

    try {
      const response = await fetch(window.location.href, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrf,
        },
        body,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not save discount.");
      }
      const fromInput = row.querySelector("[data-wholesale-from-qty]");
      const priceInput = row.querySelector("[data-wholesale-price]");
      if (fromInput) fromInput.value = String(data.wholesale_from_qty ?? data.discount_min_qty ?? 0);
      if (priceInput) {
        const hasDiscount =
          Number(data.wholesale_from_qty ?? data.discount_min_qty) > 0 &&
          Number(data.discount_amount) > 0;
        priceInput.value = hasDiscount ? String(data.wholesale_price || "") : "";
      }
      row.classList.toggle(
        "is-volume-discount",
        Number(data.wholesale_from_qty ?? data.discount_min_qty) > 0 &&
          Number(data.discount_amount) > 0
      );
      setStatus(row, "Saved", { ok: true });
      saveBtn.disabled = true;
      if (window.lucide?.createIcons) window.lucide.createIcons();
    } catch (error) {
      setStatus(row, error.message || "Save failed", { ok: false });
      saveBtn.disabled = false;
    } finally {
      saveBtn.classList.remove("is-busy");
    }
  };

  rows.forEach((row) => {
    row
      .querySelectorAll("[data-wholesale-from-qty], [data-wholesale-price]")
      .forEach((input) => {
        input.addEventListener("input", () => markDirty(row));
        input.addEventListener("change", () => markDirty(row));
      });
    row.querySelector("[data-discount-save]")?.addEventListener("click", () => {
      saveRow(row);
    });
  });
})();
