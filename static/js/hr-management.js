(() => {

  const body = document.body;



  const setModalOpen = (modal, open) => {

    if (!modal) return;

    modal.hidden = !open;

    modal.setAttribute("aria-hidden", open ? "false" : "true");

    const anyOpen = document.querySelector('.workspace-modal:not([hidden])');

    body.classList.toggle("workspace-modal-open", Boolean(anyOpen));

    if (open) {

      const firstField = modal.querySelector("input:not([type=hidden]), textarea, select");

      firstField?.focus();

      if (window.lucide?.createIcons) window.lucide.createIcons();

      if (window.initUppercaseInputs) window.initUppercaseInputs(modal);

    }

  };



  document.querySelectorAll(".workspace-modal").forEach((modal) => {

    const name = modal.dataset.modal;

    if (!name) return;



    document.querySelectorAll(`[data-modal-open="${name}"]`).forEach((trigger) => {

      trigger.addEventListener("click", () => setModalOpen(modal, true));

    });



    modal.querySelectorAll("[data-modal-close]").forEach((el) => {

      el.addEventListener("click", () => setModalOpen(modal, false));

    });



    if (!modal.hidden) body.classList.add("workspace-modal-open");

  });



  window.addEventListener("keydown", (event) => {

    if (event.key !== "Escape") return;

    document.querySelectorAll(".workspace-modal:not([hidden])").forEach((modal) => {

      setModalOpen(modal, false);

    });

  });



  const editModal = document.querySelector('[data-modal="edit-employee"]');

  const editForm = editModal?.querySelector("[data-edit-employee-form]");

  const originalEmployeeIdInput = editForm?.querySelector("[data-original-employee-id]");

  const imageWrap = editForm?.querySelector("[data-current-image-wrap]");

  const currentImage = editForm?.querySelector("[data-current-image]");

  const removeImage = editForm?.querySelector("[data-remove-image]");



  const setField = (name, value) => {

    const field = editForm?.querySelector(`[name="${name}"]`);

    if (field) field.value = value ?? "";

  };



  const setCountryFields = (iso, dial) => {

    const dialInput = editForm?.querySelector("[data-dial-input]");

    const isoInput = editForm?.querySelector("[data-iso-input]");

    const dialLabel = editForm?.querySelector("[data-dial-label]");

    const flagImg = editForm?.querySelector("[data-flag-img]");

    if (dialInput) dialInput.value = dial || "";

    if (isoInput) isoInput.value = iso || "";

    if (dialLabel) dialLabel.textContent = dial || "";

    if (flagImg && iso) {

      flagImg.src = `/static/img/flags/${iso.toLowerCase()}.png`;

    }

    editForm?.querySelectorAll(".country-option").forEach((option) => {

      option.classList.toggle(

        "is-selected",

        option.dataset.iso === iso && option.dataset.dial === dial

      );

    });

  };



  const bindEditPhoneField = () => {

    const phoneField = editForm?.querySelector("[data-phone-field]");

    if (!phoneField || phoneField.dataset.phoneBound === "true") return;

    phoneField.dataset.phoneBound = "true";



    const trigger = phoneField.querySelector("[data-country-trigger]");

    const menu = phoneField.querySelector("[data-country-menu]");

    const search = phoneField.querySelector("[data-country-search]");

    const options = [...phoneField.querySelectorAll(".country-option")];



    const setOpen = (open) => {

      if (!menu || !trigger) return;

      menu.hidden = !open;

      trigger.setAttribute("aria-expanded", String(open));

      if (open) search?.focus();

    };



    trigger?.addEventListener("click", () => setOpen(Boolean(menu?.hidden)));



    document.addEventListener("click", (event) => {

      if (!phoneField.contains(event.target)) setOpen(false);

    });



    search?.addEventListener("input", () => {

      const query = search.value.trim().toLowerCase();

      options.forEach((option) => {

        const haystack = `${option.dataset.name} ${option.dataset.dial} ${option.dataset.iso}`.toLowerCase();

        option.parentElement.hidden = Boolean(query) && !haystack.includes(query);

      });

    });



    options.forEach((option) => {

      option.addEventListener("click", () => {

        const { iso, dial } = option.dataset;

        if (!iso || !dial) return;

        setCountryFields(iso, dial);

        setOpen(false);

      });

    });

  };



  const bindEditProfileUpload = () => {

    const input = editForm?.querySelector("[data-profile-input]");

    const image = editForm?.querySelector("[data-profile-image]");

    const placeholder = editForm?.querySelector("[data-profile-placeholder]");

    if (!input || input.dataset.uploadBound === "true") return;

    input.dataset.uploadBound = "true";



    input.addEventListener("change", () => {

      const file = input.files?.[0];

      if (!file || !image) return;

      image.src = URL.createObjectURL(file);

      image.hidden = false;

      image.alt = "Selected profile photo";

      if (placeholder) placeholder.hidden = true;

      if (removeImage) removeImage.checked = false;

    });

  };



  document.querySelectorAll("[data-edit-employee]").forEach((button) => {

    button.addEventListener("click", () => {

      if (!editForm) return;



      const dataset = button.dataset;

      if (originalEmployeeIdInput) {

        originalEmployeeIdInput.value = dataset.originalEmployeeId || "";

      }

      setField("first_name", dataset.firstName);

      setField("last_name", dataset.lastName);

      setField("email", (dataset.email || "").toLowerCase());

      setField("phone_number", dataset.phoneNumber);

      setField("employee_id", dataset.employeeId);

      setField("role", dataset.role);

      setField("password", "");

      setField("password_confirm", "");

      setCountryFields(dataset.phoneCountryIso, dataset.phoneCountryCode);



      const employeeIdInput = editForm.querySelector("[data-employee-id]");

      if (employeeIdInput) {

        employeeIdInput.dataset.checkExclude = dataset.originalEmployeeId || "";

      }



      const fileInput = editForm.querySelector('input[type="file"][name="profile_photo"]');

      if (fileInput) fileInput.value = "";

      if (removeImage) removeImage.checked = false;



      const previewImage = editForm.querySelector("[data-profile-image]");

      const previewPlaceholder = editForm.querySelector("[data-profile-placeholder]");

      if (previewImage) {

        previewImage.hidden = true;

        previewImage.removeAttribute("src");

      }

      if (previewPlaceholder) previewPlaceholder.hidden = false;



      if (dataset.profilePhotoUrl && imageWrap && currentImage) {

        currentImage.src = dataset.profilePhotoUrl;

        imageWrap.hidden = false;

      } else if (imageWrap) {

        imageWrap.hidden = true;

        if (currentImage) currentImage.removeAttribute("src");

      }



      bindEditPhoneField();

      bindEditProfileUpload();

      if (window.initUppercaseInputs) window.initUppercaseInputs(editForm);

      setModalOpen(editModal, true);

    });

  });



  if (editModal && !editModal.hidden) {

    bindEditPhoneField();

    bindEditProfileUpload();

    if (window.initUppercaseInputs) window.initUppercaseInputs(editForm);

  }



  document.querySelectorAll("[data-confirm-delete]").forEach((form) => {

    form.addEventListener("submit", (event) => {

      const name = form.dataset.employeeName || form.dataset.itemName || "this employee";

      if (!window.confirm(`Delete “${name}”? This cannot be undone.`)) {

        event.preventDefault();

      }

    });

  });



  const workspaceRoot = document.querySelector("[data-workspace]");

  const approvalsStatusUrl = workspaceRoot?.dataset?.hrApprovalsStatusUrl || "";

  if (approvalsStatusUrl) {

    const POLL_MS = 12000;

    let lastCount = null;

    let pollTimer = null;



    const pushApprovalsToast = (text) => {

      let host = document.querySelector("[data-workspace-toast]");

      if (!host) {

        host = document.createElement("div");

        host.className = "workspace-toast";

        host.setAttribute("role", "status");

        host.setAttribute("aria-live", "polite");

        host.setAttribute("data-workspace-toast", "");

        document.querySelector(".workspace-frame")?.appendChild(host);

      }

      let list = host.querySelector(".message-list");

      if (!list) {

        list = document.createElement("ul");

        list.className = "message-list workspace-messages";

        host.appendChild(list);

      }

      const item = document.createElement("li");

      item.className = "workspace-toast__item workspace-toast__item--warning";

      item.dataset.toastDuration = "4500";

      item.innerHTML = `<span class="workspace-toast__text"></span>`;

      item.querySelector(".workspace-toast__text").textContent = text;

      list.appendChild(item);

      window.setTimeout(() => item.remove(), 4500);

    };



    const updateApprovalsBadges = (count, { notify = false } = {}) => {

      const n = Math.max(0, Number(count) || 0);

      const label =

        n === 1 ? "1 pending approval" : `${n} pending approvals`;



      document.querySelectorAll("[data-hr-approvals-nav-badge]").forEach((el) => {

        el.textContent = String(n);

        if (n < 1) el.setAttribute("hidden", "");

        else el.removeAttribute("hidden");

        el.setAttribute("aria-label", label);

      });



      const nav = document.querySelector("[data-hr-approvals-nav]");

      if (nav) {

        nav.classList.toggle("has-live-badge", n > 0);

      }



      document.querySelectorAll("[data-hr-approvals-count]").forEach((el) => {

        el.textContent = String(n);

      });



      document.querySelectorAll("[data-hr-approvals-stat]").forEach((el) => {

        if (n < 1) el.setAttribute("hidden", "");

        else el.removeAttribute("hidden");

      });



      if (notify && n > 0) {

        const message =

          n === 1

            ? "1 employee is waiting for approval."

            : `${n} employees are waiting for approval.`;

        pushApprovalsToast(message);

        if ("Notification" in window && Notification.permission === "granted") {

          try {

            new Notification("Employee approvals", {

              body: message,

              tag: "hr-approvals",

            });

          } catch (_err) {

            /* ignore */

          }

        } else if (

          "Notification" in window &&

          Notification.permission === "default"

        ) {

          Notification.requestPermission().catch(() => {});

        }

      }

    };



    const pollApprovals = async () => {

      if (document.hidden) return;

      try {

        const response = await fetch(approvalsStatusUrl, {

          headers: {

            Accept: "application/json",

            "X-Requested-With": "XMLHttpRequest",

          },

          credentials: "same-origin",

        });

        if (response.status === 401 || response.status === 403) {

          if (pollTimer) {

            window.clearInterval(pollTimer);

            pollTimer = null;

          }

          return;

        }

        if (!response.ok) return;

        const data = await response.json();

        if (!data?.ok) return;

        const nextCount =

          typeof data.pending_count === "number" ? data.pending_count : 0;

        const increased =

          lastCount !== null && nextCount > lastCount;

        updateApprovalsBadges(nextCount, { notify: increased });

        if (

          increased &&

          document.querySelector(".item-panel--approvals")

        ) {

          window.setTimeout(() => window.location.reload(), 800);

        }

        lastCount = nextCount;

      } catch (_err) {

        /* ignore transient poll errors */

      }

    };



    const seedBadge = document.querySelector("[data-hr-approvals-nav-badge]");

    if (seedBadge && !seedBadge.hasAttribute("hidden")) {

      lastCount = Number(seedBadge.textContent) || 0;

    } else {

      const seedCount = document.querySelector("[data-hr-approvals-count]");

      lastCount = seedCount ? Number(seedCount.textContent) || 0 : 0;

    }



    pollApprovals();

    pollTimer = window.setInterval(pollApprovals, POLL_MS);

    document.addEventListener("visibilitychange", () => {

      if (!document.hidden) pollApprovals();

    });

  }

})();


