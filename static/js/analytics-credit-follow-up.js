(() => {
  const modal = document.querySelector("[data-ax-follow-up-modal]");
  if (!modal) return;

  const openBtn = document.querySelector("[data-ax-follow-up-open]");
  const form = modal.querySelector("[data-ax-follow-up-form]");
  const dateInput = modal.querySelector("[data-ax-follow-up-date]");
  const statusEl = modal.querySelector("[data-ax-follow-up-status]");
  const saveBtn = modal.querySelector("[data-ax-follow-up-save]");
  const closeEls = modal.querySelectorAll("[data-ax-follow-up-close]");
  const url = modal.getAttribute("data-follow-up-url") || "";
  const leadEl = document.querySelector(".ax--account .ax-lead");

  const readCookie = (name) => {
    const parts = `; ${document.cookie}`.split(`; ${name}=`);
    if (parts.length === 2) {
      return decodeURIComponent(parts.pop().split(";").shift() || "");
    }
    return "";
  };

  const csrf =
    form?.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    readCookie("csrftoken") ||
    "";

  const setStatus = (message, { error = false } = {}) => {
    if (!statusEl) return;
    statusEl.hidden = !message;
    statusEl.textContent = message || "";
    statusEl.className = error ? "ax-pay-status is-error" : "ax-pay-status is-ok";
  };

  const syncBody = (open) => {
    document.body.classList.toggle("ax-follow-up-open", open);
  };

  const close = () => {
    modal.hidden = true;
    syncBody(false);
    setStatus("");
  };

  const open = () => {
    modal.hidden = false;
    syncBody(true);
    window.lucide?.createIcons?.();
    dateInput?.focus();
  };

  const updateLead = (data) => {
    if (!leadEl || !data.follow_up) return;
    const overdueClass = data.follow_up_overdue ? ' class="is-overdue"' : "";
    leadEl.innerHTML = leadEl.innerHTML.replace(
      /Account follow-up[\s\S]*?(?= ·)/,
      `Account follow-up is <strong${overdueClass}>${data.follow_up}</strong>`
    );
    if (openBtn) {
      const label = openBtn.querySelector("span");
      if (label) label.textContent = "Change follow-up";
    }
  };

  openBtn?.addEventListener("click", open);
  closeEls.forEach((el) => el.addEventListener("click", close));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) close();
  });

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!url) return;
    const value = (dateInput?.value || "").trim();
    if (!value) {
      setStatus("Enter a follow-up date.", { error: true });
      return;
    }
    if (saveBtn) saveBtn.disabled = true;
    setStatus("Saving…");
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "X-CSRFToken": csrf,
          Accept: "application/json",
        },
        body: new URLSearchParams({ credit_follow_up_date: value }),
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not save follow-up date.");
      }
      if (dateInput && data.follow_up_raw) dateInput.value = data.follow_up_raw;
      updateLead(data);
      const okMessage = data.message || "Follow-up date saved.";
      const notifyFailed = data.notified === false && Boolean(data.notify_reason);
      setStatus(okMessage, { error: notifyFailed });
    } catch (err) {
      setStatus(err.message || "Could not save follow-up date.", { error: true });
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  });
})();
