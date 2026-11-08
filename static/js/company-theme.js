(() => {
  const root = document.querySelector("[data-company-theme-settings]");
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

  const options = Array.from(root.querySelectorAll("[data-company-theme-option]"));
  const activeLabel = root.querySelector("[data-company-theme-active-label]");
  const activeName = root.querySelector("[data-company-theme-active-name]");
  const hint = root.querySelector("[data-company-theme-hint]");
  let selectedSlug =
    options.find((btn) => btn.classList.contains("is-selected"))?.dataset.themeSlug ||
    "";
  let saveSeq = 0;

  function applyDocumentTheme(slug) {
    if (!slug) return;
    document.documentElement.setAttribute("data-company-theme", slug);
  }

  function markSelected(slug) {
    options.forEach((btn) => {
      const on = btn.dataset.themeSlug === slug;
      btn.classList.toggle("is-selected", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function syncCopy(btn) {
    const label = btn?.dataset.themeLabel || "";
    const summary = btn?.dataset.themeSummary || "";
    if (activeLabel) activeLabel.textContent = label;
    if (activeName) activeName.textContent = label;
    if (hint && label) {
      hint.innerHTML = `Using <strong data-company-theme-active-name>${label}</strong>${
        summary ? ` — ${summary}` : ""
      }`;
    }
  }

  async function saveTheme(btn) {
    const slug = btn.dataset.themeSlug || "";
    if (!slug || slug === selectedSlug) {
      applyDocumentTheme(slug || selectedSlug);
      markSelected(slug || selectedSlug);
      return;
    }

    const previous = selectedSlug;
    const seq = ++saveSeq;
    selectedSlug = slug;
    markSelected(slug);
    applyDocumentTheme(slug);
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
          action: "set_company_theme",
          theme: slug,
        }),
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not save company theme.");
      }
      if (seq !== saveSeq) return;
      selectedSlug = data.theme || slug;
      const savedBtn =
        options.find((el) => el.dataset.themeSlug === selectedSlug) || btn;
      markSelected(selectedSlug);
      applyDocumentTheme(selectedSlug);
      syncCopy(savedBtn);
    } catch (error) {
      if (seq !== saveSeq) return;
      selectedSlug = previous;
      const prevBtn = options.find((el) => el.dataset.themeSlug === previous);
      markSelected(previous);
      applyDocumentTheme(previous);
      if (prevBtn) syncCopy(prevBtn);
      window.alert(error.message || "Could not save company theme.");
    } finally {
      if (seq === saveSeq) {
        btn.classList.remove("is-saving");
      }
    }
  }

  options.forEach((btn) => {
    btn.addEventListener("click", () => {
      saveTheme(btn);
    });
  });
})();
