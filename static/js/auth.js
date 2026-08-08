(() => {
  const refreshIcons = () => {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    const wrap = button.closest(".password-field");
    const input = wrap?.querySelector("input");
    const openIcon = button.querySelector("[data-eye-open]");
    const closedIcon = button.querySelector("[data-eye-closed]");
    if (!input) return;

    button.addEventListener("click", () => {
      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      button.setAttribute("aria-label", showing ? "Show password" : "Hide password");
      if (openIcon) openIcon.hidden = !showing;
      if (closedIcon) closedIcon.hidden = showing;
      refreshIcons();
    });
  });

  const portal = document.querySelector("[data-portal-login]");
  if (portal) {
    const shell = document.querySelector(".auth-shell--portal");
    const tabs = Array.from(portal.querySelectorAll("[data-auth-tab]"));
    const panels = Array.from(portal.querySelectorAll("[data-auth-panel]"));
    const visualKicker = document.querySelector("[data-auth-visual-kicker]");
    const visualLead = document.querySelector("[data-auth-visual-lead]");
    const copy = {
      employee: {
        kicker: "Staff portal",
        lead: "Access your workspace with your employee ID and password.",
      },
      shop: {
        kicker: "Branch portal",
        lead: "Open your shop floor with the branch code and password.",
      },
    };

    const setMode = (mode, { focus = true } = {}) => {
      portal.dataset.loginMode = mode;
      if (shell) shell.dataset.visualMode = mode;
      if (visualKicker) visualKicker.textContent = copy[mode]?.kicker || "";
      if (visualLead) visualLead.textContent = copy[mode]?.lead || "";

      tabs.forEach((tab) => {
        const active = tab.dataset.authTab === mode;
        tab.classList.toggle("is-active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
        tab.tabIndex = active ? 0 : -1;
      });
      panels.forEach((panel) => {
        const active = panel.dataset.authPanel === mode;
        panel.classList.toggle("is-active", active);
        panel.hidden = !active;
        if (active) {
          panel.style.animation = "none";
          // Force reflow so the entrance animation can replay.
          void panel.offsetWidth;
          panel.style.animation = "";
        }
      });
      if (focus) {
        const activePanel = portal.querySelector(`[data-auth-panel="${mode}"]`);
        const codeInput = activePanel?.querySelector("[data-code-input]");
        codeInput?.focus();
        codeInput?.select?.();
      }
      refreshIcons();
    };

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => setMode(tab.dataset.authTab));
      tab.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const index = tabs.indexOf(tab);
        const next =
          event.key === "ArrowRight"
            ? tabs[(index + 1) % tabs.length]
            : tabs[(index - 1 + tabs.length) % tabs.length];
        setMode(next.dataset.authTab);
        next.focus();
      });
    });

    portal.querySelectorAll("[data-code-input]").forEach((input) => {
      const form = input.closest("form");
      const password = form?.querySelector("[data-password-input]");

      const normalize = () => {
        const digits = String(input.value || "").replace(/\D+/g, "").slice(0, 6);
        if (input.value !== digits) input.value = digits;
        return digits;
      };

      input.addEventListener("input", () => {
        const digits = normalize();
        if (digits.length === 6 && password) {
          password.focus();
        }
      });

      input.addEventListener("paste", (event) => {
        event.preventDefault();
        const text = event.clipboardData?.getData("text") || "";
        input.value = text.replace(/\D+/g, "").slice(0, 6);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });

    portal.querySelectorAll("[data-auth-form]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        const code = form.querySelector("[data-code-input]");
        const password = form.querySelector("[data-password-input]");
        const submit = form.querySelector("[data-auth-submit]");
        const label = form.querySelector("[data-auth-submit-label]");
        const digits = String(code?.value || "").replace(/\D+/g, "").slice(0, 6);

        if (code) code.value = digits;
        if (digits.length !== 6) {
          event.preventDefault();
          code?.focus();
          return;
        }
        if (!String(password?.value || "")) {
          event.preventDefault();
          password?.focus();
          return;
        }
        if (submit?.dataset.submitting === "1") {
          event.preventDefault();
          return;
        }
        if (submit) {
          submit.dataset.submitting = "1";
          submit.disabled = true;
          submit.setAttribute("aria-busy", "true");
        }
        if (label) {
          label.textContent =
            form.dataset.authForm === "shop" ? "Opening shop…" : "Signing in…";
        }
      });
    });

    const initial = portal.dataset.loginMode || "employee";
    setMode(initial, { focus: !portal.querySelector(".form-alert") });
  }

  refreshIcons();
})();
