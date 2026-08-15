(() => {
  const forms = document.querySelectorAll("[data-register-form], [data-edit-employee-form]");
  if (!forms.length) return;

  forms.forEach((form) => {
  const refreshIcons = () => {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const submitBtn = form.querySelector('button[type="submit"]');

  // Profile photo preview
  const input = form.querySelector("[data-profile-input]");
  const image = form.querySelector("[data-profile-image]");
  const placeholder = form.querySelector("[data-profile-placeholder]");

  input?.addEventListener("change", () => {
    const file = input.files?.[0];
    if (!file || !image) return;
    const url = URL.createObjectURL(file);
    image.src = url;
    image.hidden = false;
    image.alt = "Selected profile photo";
    if (placeholder) placeholder.hidden = true;
  });

  // Employee ID availability
  const employeeId = form.querySelector("[data-employee-id]");
  const idStatus = form.querySelector("[data-employee-id-status]");
  let idAvailable = null;
  let checkTimer = null;

  const setIdStatus = (state, message) => {
    if (!idStatus) return;
    idStatus.textContent = message || "";
    idStatus.className = "field-hint";
    if (state === "ok") idStatus.classList.add("field-hint--ok");
    if (state === "error") idStatus.classList.add("field-hint--error");
    if (state === "loading") idStatus.classList.add("field-hint--muted");
  };

  const syncSubmit = () => {
    if (!submitBtn) return;
    const blocked = idAvailable === false;
    submitBtn.disabled = blocked;
    submitBtn.classList.toggle("is-disabled", blocked);
  };

  const checkEmployeeId = async (code) => {
    const url = employeeId?.dataset.checkUrl;
    if (!url || !idStatus) return;

    if (!code) {
      idAvailable = null;
      setIdStatus("", "");
      syncSubmit();
      return;
    }

    if (code.length < 6) {
      idAvailable = false;
      setIdStatus("error", "Employee code must be exactly 6 digits.");
      syncSubmit();
      return;
    }

    setIdStatus("loading", "Checking code availability…");
    try {
      const { isOnline } = await import("./offline/connectivity.js");
      if (!isOnline()) {
        const cached = await import("./offline/store.js").then((m) =>
          m.getCachedEmployeeIdCheck(code)
        );
        if (cached) {
          idAvailable = Boolean(cached.available);
          setIdStatus(idAvailable ? "ok" : "error", cached.message || "");
        } else {
          idAvailable = null;
          setIdStatus(
            "error",
            "Offline — code will be verified when you reconnect."
          );
        }
        syncSubmit();
        return;
      }

      const response = await fetch(
        `${url}?code=${encodeURIComponent(code)}${
          employeeId?.dataset.checkExclude
            ? `&exclude=${encodeURIComponent(employeeId.dataset.checkExclude)}`
            : ""
        }`,
        {
        headers: { Accept: "application/json" },
      }
      );
      const data = await response.json();
      idAvailable = Boolean(data.available);
      setIdStatus(idAvailable ? "ok" : "error", data.message || "");
      try {
        const store = await import("./offline/store.js");
        await store.cacheEmployeeIdCheck(code, data);
      } catch (_e) {
        /* ignore cache errors */
      }
    } catch (_err) {
      idAvailable = null;
      setIdStatus("error", "Could not verify employee code. Try again.");
    }
    syncSubmit();
  };

  employeeId?.addEventListener("input", () => {
    employeeId.value = employeeId.value.replace(/\D/g, "").slice(0, 6);
    clearTimeout(checkTimer);
    checkTimer = setTimeout(() => checkEmployeeId(employeeId.value), 350);
  });

  if (employeeId?.value) {
    checkEmployeeId(employeeId.value);
  }

  // Password match — letters, numbers, and symbols allowed
  const password = form.querySelector("[data-password]");
  const passwordConfirm = form.querySelector("[data-password-confirm]");
  const matchStatus = form.querySelector("[data-password-match]");

  const setMatchStatus = (state, message) => {
    if (!matchStatus) return;
    matchStatus.textContent = message || "";
    matchStatus.className = "field-hint";
    if (state === "ok") matchStatus.classList.add("field-hint--ok");
    if (state === "error") matchStatus.classList.add("field-hint--error");
  };

  const checkPasswordMatch = () => {
    const a = password?.value || "";
    const b = passwordConfirm?.value || "";

    if (!a && !b) {
      setMatchStatus("", "");
      return true;
    }

    if (a && a.length < 6) {
      setMatchStatus("error", "Password must be at least 6 characters. Codes & symbols are allowed.");
      return false;
    }

    if (b && a !== b) {
      setMatchStatus("error", "Password and confirm password do not match.");
      passwordConfirm?.classList.add("is-invalid");
      password?.classList.add("is-invalid");
      return false;
    }

    if (a && b && a === b) {
      setMatchStatus("ok", "Passwords match.");
      passwordConfirm?.classList.remove("is-invalid");
      password?.classList.remove("is-invalid");
      return true;
    }

    setMatchStatus("", "");
    passwordConfirm?.classList.remove("is-invalid");
    password?.classList.remove("is-invalid");
    return true;
  };

  password?.addEventListener("input", checkPasswordMatch);
  passwordConfirm?.addEventListener("input", checkPasswordMatch);

  form.addEventListener("submit", async (event) => {
    if (form.matches("[data-edit-employee-form]")) {
      const code = employeeId?.value || "";
      if (code.length === 6 && idAvailable === false) {
        event.preventDefault();
        setIdStatus("error", `Employee code ${code} is not available. Choose another.`);
        employeeId?.focus();
        return;
      }
      if (!checkPasswordMatch()) {
        event.preventDefault();
        passwordConfirm?.focus();
      }
      return;
    }

    const { isOnline } = await import("./offline/connectivity.js");
    if (!isOnline()) {
      event.preventDefault();
      const payload = {
        employee_id: form.querySelector("[data-employee-id]")?.value?.trim(),
        first_name: form.querySelector("[name=first_name]")?.value?.trim(),
        last_name: form.querySelector("[name=last_name]")?.value?.trim(),
        email: form.querySelector("[name=email]")?.value?.trim(),
        phone_country_code: form.querySelector("[data-dial-input]")?.value?.trim(),
        phone_number: form.querySelector("[name=phone_number]")?.value?.trim(),
        password: form.querySelector("[data-password]")?.value,
      };
      try {
        const { queueOperation } = await import("./offline/sync.js");
        await queueOperation("register_employee", payload);
        alert(
          "You are offline. Registration queued — it will submit when you reconnect."
        );
      } catch (_e) {
        alert("Offline registration could not be queued. Try again when online.");
      }
      return;
    }

    const code = employeeId?.value || "";
    if (code.length === 6 && idAvailable === false) {
      event.preventDefault();
      setIdStatus("error", `Employee code ${code} is not available. Choose another.`);
      employeeId?.focus();
      return;
    }

    if (!checkPasswordMatch() || (password?.value && passwordConfirm?.value && password.value !== passwordConfirm.value)) {
      event.preventDefault();
      setMatchStatus("error", "Password and confirm password do not match.");
      passwordConfirm?.focus();
    }
  });

  // Country code picker with flags
  const phoneField = form.querySelector("[data-phone-field]");
  if (phoneField) {
    const trigger = phoneField.querySelector("[data-country-trigger]");
    const menu = phoneField.querySelector("[data-country-menu]");
    const search = phoneField.querySelector("[data-country-search]");
    const dialInput = phoneField.querySelector("[data-dial-input]");
    const isoInput = phoneField.querySelector("[data-iso-input]");
    const dialLabel = phoneField.querySelector("[data-dial-label]");
    const flagImg = phoneField.querySelector("[data-flag-img]");
    const options = [...phoneField.querySelectorAll(".country-option")];

    const setOpen = (open) => {
      if (!menu || !trigger) return;
      menu.hidden = !open;
      trigger.setAttribute("aria-expanded", String(open));
      if (open) {
        search?.focus();
        refreshIcons();
      }
    };

    trigger?.addEventListener("click", () => {
      setOpen(Boolean(menu?.hidden));
    });

    document.addEventListener("click", (event) => {
      if (!phoneField.contains(event.target)) setOpen(false);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setOpen(false);
    });

    search?.addEventListener("input", () => {
      const q = search.value.trim().toLowerCase();
      options.forEach((option) => {
        const hay = `${option.dataset.name} ${option.dataset.dial} ${option.dataset.iso}`.toLowerCase();
        option.parentElement.hidden = Boolean(q) && !hay.includes(q);
      });
    });

    options.forEach((option) => {
      option.addEventListener("click", () => {
        const { iso, dial } = option.dataset;
        if (!iso || !dial) return;

        if (dialInput) dialInput.value = dial;
        if (isoInput) isoInput.value = iso;
        if (dialLabel) dialLabel.textContent = dial;
        if (flagImg) {
          flagImg.src = `https://flagcdn.com/w40/${iso.toLowerCase()}.png`;
        }

        options.forEach((o) => o.classList.remove("is-selected"));
        option.classList.add("is-selected");
        setOpen(false);
      });
    });
  }

  refreshIcons();
  });
})();
