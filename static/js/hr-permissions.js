(() => {
  const matrix = document.querySelector("[data-permission-matrix]");
  if (!matrix) return;

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

  function setStateLabel(input, allowed) {
    const label = input.closest(".perm-switch")?.querySelector(".perm-switch-state");
    if (label) {
      label.textContent = allowed ? "Allowed" : "Denied";
    }
    input.closest(".perm-switch")?.classList.toggle("is-denied", !allowed);
  }

  matrix.querySelectorAll("[data-permission-toggle]").forEach((input) => {
    setStateLabel(input, input.checked);

    input.addEventListener("change", async () => {
      const allowed = input.checked;
      const previous = !allowed;
      setStateLabel(input, allowed);
      input.disabled = true;
      input.closest(".perm-switch")?.classList.add("is-saving");

      const body = new URLSearchParams({
        action: "toggle_permission",
        employee_id: input.dataset.employeeId || "",
        module_slug: input.dataset.moduleSlug || "",
        submodule_slug: input.dataset.submoduleSlug || "",
        allowed: allowed ? "1" : "0",
      });

      try {
        const response = await fetch(window.location.pathname, {
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
          throw new Error(data.error || "Could not save permission.");
        }
        setStateLabel(input, Boolean(data.allowed));
        input.checked = Boolean(data.allowed);
      } catch (error) {
        input.checked = previous;
        setStateLabel(input, previous);
        window.alert(error.message || "Could not save permission.");
      } finally {
        input.disabled = false;
        input.closest(".perm-switch")?.classList.remove("is-saving");
      }
    });
  });
})();
