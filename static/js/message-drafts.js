(() => {
  const root = document.querySelector("[data-msg-compose]");
  if (!root) return;

  const form = root.querySelector("[data-draft-form]");
  if (!form) return;

  const body = form.querySelector("[data-msg-body]");
  const link = form.querySelector("[data-msg-link]");
  const triggerSel = form.querySelector('select[name="trigger"]');
  const statusSel = form.querySelector('select[name="status"]');
  const amountInput = form.querySelector('input[name="delay_amount"]');
  const unitSel = form.querySelector('select[name="delay_unit"]');

  const preview = root.querySelector("[data-msg-preview]");
  const pvBody = preview?.querySelector("[data-pv-body]");
  const pvLink = preview?.querySelector("[data-pv-link]");
  const pvMeta = preview?.querySelector("[data-pv-meta]");

  const sample = {
    first_name: "Kim",
    full_name: "Kim Otieno",
    shop_name: preview?.dataset.sampleShop || "MY-SHOP",
    total: "KSh 400.00",
    receipt_number: "SL-0001",
    due_date: "in 30 days",
    account_link: "https://myshop.example/credit-note/AbC123",
    trade_link: "https://myshop.example/trades/TdE901",
    receipt_link: "https://myshop.example/receipt/XyZ456",
    quotation_link: "https://myshop.example/quotation/QrT789",
  };

  function timingValue() {
    return (
      form.querySelector('input[name="send_timing"]:checked')?.value || "now"
    );
  }

  function syncTiming() {
    const later = timingValue() === "later";
    root
      .querySelectorAll("[data-delay-fields] input, [data-delay-fields] select")
      .forEach((el) => {
        el.disabled = !later;
      });
    root.querySelectorAll(".msg-pill").forEach((pill) => {
      const input = pill.querySelector('input[name="send_timing"]');
      pill.classList.toggle("is-active", !!(input && input.checked));
    });
  }

  function renderPreview() {
    if (!pvBody) return;
    let text = (body.value || "").trim();
    if (!text) text = "Write a message to see a preview…";
    Object.entries(sample).forEach(([key, value]) => {
      text = text.split(`{${key}}`).join(value);
    });
    pvBody.textContent = text;

    let url = (link?.value || "").trim();
    Object.entries(sample).forEach(([key, value]) => {
      url = url.split(`{${key}}`).join(value);
    });
    if (pvLink) {
      if (url) {
        pvLink.textContent = url;
        pvLink.href = /^https?:\/\//i.test(url) ? url : `https://${url}`;
        pvLink.hidden = false;
      } else {
        pvLink.hidden = true;
      }
    }

    if (pvMeta) {
      const triggerLabel =
        triggerSel?.selectedOptions[0]?.textContent?.trim().toLowerCase() ||
        "event";
      let when;
      if (timingValue() === "later" && amountInput?.value) {
        const unit =
          unitSel?.selectedOptions[0]?.textContent?.trim().toLowerCase() ||
          "minutes";
        when = `sends ${amountInput.value} ${unit} after a ${triggerLabel}`;
      } else {
        when = `sends right after a ${triggerLabel}`;
      }
      const status =
        statusSel?.value === "active" ? "Active" : "Draft — won’t send";
      pvMeta.textContent = `${status} · ${when}`;
    }
  }

  root.querySelectorAll("[data-insert]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const token = chip.dataset.insert;
      const start = body.selectionStart ?? body.value.length;
      const end = body.selectionEnd ?? start;
      body.value = body.value.slice(0, start) + token + body.value.slice(end);
      body.focus();
      body.selectionStart = body.selectionEnd = start + token.length;
      renderPreview();
    });
  });

  root.querySelectorAll("[data-link-insert]").forEach((chip) => {
    chip.addEventListener("click", () => {
      if (!link) return;
      link.value = chip.dataset.linkInsert;
      link.focus();
      renderPreview();
    });
  });

  form.addEventListener("input", renderPreview);
  form.addEventListener("change", () => {
    syncTiming();
    renderPreview();
  });
  syncTiming();
  renderPreview();
})();
