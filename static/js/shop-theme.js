(() => {
  const root = document.querySelector("[data-shop-theme-settings]");
  if (!root) return;

  function readCookie(name) {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) {
      return decodeURIComponent(parts.pop().split(";").shift() || "");
    }
    return "";
  }

  const csrfToken =
    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    readCookie("csrftoken") ||
    "";

  const options = Array.from(root.querySelectorAll("[data-shop-theme-option]"));
  const activeLabel = root.querySelector("[data-shop-theme-active-label]");
  const modeEl = root.querySelector("[data-shop-theme-mode]");
  const hint = root.querySelector("[data-shop-theme-hint]");
  let selectedValue =
    options.find((btn) => btn.classList.contains("is-selected"))?.dataset.themeValue ||
    "";
  let saveSeq = 0;

  function applyDocumentTheme(slug) {
    if (!slug) return;
    document.documentElement.setAttribute("data-company-theme", slug);
  }

  function markSelected(value) {
    options.forEach((btn) => {
      const on = (btn.dataset.themeValue || "") === value;
      btn.classList.toggle("is-selected", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function syncCopy(btn) {
    const label = btn?.dataset.themeLabel || "";
    const summary = btn?.dataset.themeSummary || "";
    const isCompany = btn?.dataset.themeCompany === "1";
    if (activeLabel) activeLabel.textContent = label;
    if (modeEl) modeEl.textContent = isCompany ? "Company" : "Custom";
    if (hint && label) {
      const prefix = isCompany ? "Using company default" : "Using shop theme";
      hint.innerHTML = `${prefix} — <strong data-shop-theme-active-name>${label}</strong>${
        summary ? ` · ${summary}` : ""
      }`;
    }
  }

  async function saveTheme(btn) {
    const value = btn.dataset.themeValue || "";
    const effective = btn.dataset.themeEffective || value;
    if (value === selectedValue) {
      applyDocumentTheme(effective);
      markSelected(value);
      return;
    }

    const previous = selectedValue;
    const prevBtn = options.find((el) => (el.dataset.themeValue || "") === previous);
    const seq = ++saveSeq;
    selectedValue = value;
    markSelected(value);
    applyDocumentTheme(effective);
    syncCopy(btn);
    btn.classList.add("is-saving");

    try {
      const response = await fetch(window.location.pathname, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfToken,
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({
          action: "set_shop_theme",
          theme: value || "company",
        }),
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not save shop theme.");
      }
      if (seq !== saveSeq) return;
      selectedValue = data.using_company_theme ? "" : data.shop_theme || value;
      const savedBtn =
        options.find((el) => (el.dataset.themeValue || "") === selectedValue) || btn;
      markSelected(selectedValue);
      applyDocumentTheme(data.theme || effective);
      syncCopy(savedBtn);
    } catch (error) {
      if (seq !== saveSeq) return;
      selectedValue = previous;
      markSelected(previous);
      if (prevBtn) {
        applyDocumentTheme(prevBtn.dataset.themeEffective || previous);
        syncCopy(prevBtn);
      }
      window.alert(error.message || "Could not save shop theme.");
    } finally {
      if (seq === saveSeq) {
        btn.classList.remove("is-saving");
      }
    }
  }

  options.forEach((btn) => {
    btn.addEventListener("click", () => saveTheme(btn));
  });
})();
