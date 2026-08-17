(() => {
  const root = document.querySelector("[data-marketing-links]");
  if (!root) return;

  const search = root.querySelector("[data-marketing-search]");
  const cards = [...root.querySelectorAll("[data-marketing-card]")];
  const empty = root.querySelector("[data-marketing-empty]");
  const count = root.querySelector("[data-marketing-count]");

  const visibleCards = () => cards.filter((card) => !card.hidden);

  const setCopied = (btn) => {
    const label = btn.querySelector("[data-copy-label]");
    if (!label) return;
    if (!btn.dataset.labelText) btn.dataset.labelText = label.textContent.trim();
    btn.classList.add("is-copied");
    label.textContent = "Copied";
    window.setTimeout(() => {
      btn.classList.remove("is-copied");
      label.textContent = btn.dataset.labelText;
    }, 1400);
  };

  const copyText = async (text, btn) => {
    const value = String(text || "").trim();
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const field = document.createElement("textarea");
      field.value = value;
      field.setAttribute("readonly", "");
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.append(field);
      field.select();
      document.execCommand("copy");
      field.remove();
    }
    if (btn) setCopied(btn);
  };

  root.addEventListener("click", (event) => {
    const allBtn = event.target.closest("[data-copy-all]");
    if (allBtn && root.contains(allBtn)) {
      event.preventDefault();
      const urls = visibleCards()
        .map((card) => card.dataset.url || "")
        .filter(Boolean);
      copyText(urls.join("\n"), allBtn);
      return;
    }
    const copyBtn = event.target.closest("[data-copy-url]");
    if (copyBtn && root.contains(copyBtn)) {
      event.preventDefault();
      copyText(copyBtn.dataset.copyUrl || "", copyBtn);
    }
  });

  root.addEventListener("focusin", (event) => {
    const input = event.target.closest("[data-marketing-url]");
    if (input) input.select();
  });

  const filter = () => {
    const query = (search?.value || "").trim().toLowerCase();
    let visible = 0;
    cards.forEach((card) => {
      const hay = card.dataset.search || "";
      const show = !query || hay.includes(query);
      card.hidden = !show;
      if (show) visible += 1;
    });
    if (count) {
      count.textContent = `${visible} link${visible === 1 ? "" : "s"}`;
    }
    if (empty) empty.hidden = visible > 0;
  };

  search?.addEventListener("input", filter);
})();
