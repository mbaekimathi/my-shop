(() => {
  const root = document.querySelector("[data-receipt-settings]");
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

  const activeLabel = root.querySelector("[data-receipt-active-label]");
  const paperStat = root.querySelector("[data-receipt-paper-stat]");
  const copyCountEl = root.querySelector("[data-receipt-copy-count]");
  const copyEmptyEl = root.querySelector("[data-receipt-copy-empty]");
  const COPY_FIELDS = [
    "print_customer_receipt",
    "print_staff_receipt",
    "print_cashier_receipt",
  ];
  const DEFAULT_RETURN_NOTICE = "Goods once bought cannot be returned";
  const PREVIEW_TOGGLE_FIELDS = new Set([
    "show_client_phone_on_receipt",
    "show_return_notice_on_receipt",
    "show_maintainer_on_receipt",
    "enable_receipt_payment_methods",
    "print_customer_receipt",
    "print_staff_receipt",
    "print_cashier_receipt",
  ]);

  function readReturnNoticeText() {
    const input = root.querySelector("[data-return-notice-text]");
    const value = String(input?.value || "").trim();
    return value || DEFAULT_RETURN_NOTICE;
  }

  // Live QR payload for the preview — kept in sync when QR settings save,
  // because re-rendering tickets replaces the DOM nodes renderQrPreview used to target.
  let liveQrState = (() => {
    const sample = (() => {
      try {
        const el = document.getElementById("receipt-sample-data");
        return el ? JSON.parse(el.textContent || "{}") : null;
      } catch (_) {
        return null;
      }
    })();
    return sample?.qr && typeof sample.qr === "object" ? { ...sample.qr } : {};
  })();

  async function postSettings(body) {
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
      throw new Error(data.error || "Could not save setting.");
    }
    return data;
  }

  function maskClientPhoneDigits(phone, visibleDigits = 7) {
    const chars = [...String(phone || "")];
    let digits = 0;
    for (let i = 0; i < chars.length; i += 1) {
      if (/\d/.test(chars[i])) {
        digits += 1;
        if (digits > visibleDigits) chars[i] = "*";
      }
    }
    return chars.join("");
  }

  function readReceiptSample() {
    const sampleEl = document.getElementById("receipt-sample-data");
    try {
      return sampleEl ? JSON.parse(sampleEl.textContent || "{}") : null;
    } catch (_) {
      return null;
    }
  }

  function syncCopyPreviews() {
    let visible = 0;
    root.querySelectorAll("[data-receipt-copy-card]").forEach((card) => {
      const field = card.getAttribute("data-receipt-copy-field");
      const input = field
        ? root.querySelector(`[data-pos-toggle][data-field="${field}"]`)
        : null;
      const on = input ? input.checked : !card.hidden;
      card.hidden = !on;
      if (on) visible += 1;
    });
    if (copyEmptyEl) copyEmptyEl.hidden = visible > 0;
    if (copyCountEl) copyCountEl.textContent = String(visible);
    const width =
      root.querySelector("[data-receipt-paper]:checked")?.value || "80";
    if (activeLabel) {
      const noun = visible === 1 ? "copy" : "copies";
      activeLabel.innerHTML = `Active: ${width} mm · <span data-receipt-copy-count>${visible}</span> ${noun}`;
    }
  }

  function paintLiveCopyPreviews() {
    const render = window.RichcomPrinter?.renderTicketHtml;
    const sample = readReceiptSample();
    if (typeof render !== "function" || !sample?.ticket) return false;

    const width =
      root.querySelector("[data-receipt-paper]:checked")?.value ||
      sample.paper_width ||
      "80";
    const showClientPhone =
      root.querySelector(
        '[data-pos-toggle][data-field="show_client_phone_on_receipt"]'
      )?.checked !== false;
    const returnNoticeOn = Boolean(
      root.querySelector(
        '[data-pos-toggle][data-field="show_return_notice_on_receipt"]'
      )?.checked
    );
    const maintainerOn = Boolean(
      root.querySelector(
        '[data-pos-toggle][data-field="show_maintainer_on_receipt"]'
      )?.checked
    );
    const maintainerPhone = String(
      root.querySelector("[data-maintainer-phone]")?.value || ""
    ).trim();
    const maintainer =
      maintainerOn && maintainerPhone
        ? `System maintained by ${maintainerPhone}`
        : "";
    const paymentOn = Boolean(
      root.querySelector(
        '[data-pos-toggle][data-field="enable_receipt_payment_methods"]'
      )?.checked ?? true
    );
    const qrToggle = root.querySelector("[data-receipt-qr-enabled]");
    const qrEnabled = qrToggle ? Boolean(qrToggle.checked) : true;
    const activeQr =
      liveQrState && typeof liveQrState === "object" && liveQrState.ready
        ? liveQrState
        : sample.qr && sample.qr.ready
          ? sample.qr
          : liveQrState || sample.qr || {};
    const livePayment = paymentOn
      ? typeof buildPaymentPreview === "function"
        ? buildPaymentPreview()
        : sample.ticket.payment_details || { label: "", lines: [] }
      : { label: "", lines: [] };

    (sample.copies || []).forEach((copy) => {
      const node = root.querySelector(
        `[data-receipt-copy-ticket="${copy.role}"]`
      );
      if (!node) return;
      const ticket = {
        ...(copy.ticket || sample.ticket),
      };
      const fullPhone = String(
        ticket.party_phone_full || ticket.party_phone || ""
      );
      ticket.party_phone_full = fullPhone;
      ticket.party_phone = showClientPhone
        ? fullPhone
        : maskClientPhoneDigits(fullPhone);
      ticket.return_notice = returnNoticeOn ? readReturnNoticeText() : "";
      ticket.maintainer = maintainer;
      if (!paymentOn) {
        ticket.payment = "";
        ticket.payment_details = { label: "", lines: [] };
      } else {
        ticket.payment =
          ticket.payment ||
          sample.ticket?.payment ||
          activatedPaymentMethodsLabel();
        ticket.payment_details =
          copy.role === "staff"
            ? { label: "", lines: [] }
            : livePayment?.lines?.length
              ? livePayment
              : ticket.payment_details || { label: "", lines: [] };
      }
      const allowQr = ticket.include_qr !== false && qrEnabled;
      const qr = allowQr
        ? {
            ready: Boolean(activeQr?.ready && activeQr?.image_data_url),
            image_data_url: activeQr?.image_data_url || "",
            label: activeQr?.label || "",
            payload: activeQr?.payload || activeQr?.url || "",
          }
        : { ready: false, image_data_url: "", label: "", payload: "" };
      const html = render(ticket, qr, width);
      if (!html) return;
      node.innerHTML = `${html}<p class="receipt-ticket-width-tag" aria-hidden="true">${width} mm thermal</p>`;
      // Preserve selected paper + font CSS vars on the ticket shell.
      node.classList.toggle("receipt-ticket--80", width === "80");
      node.classList.toggle("receipt-ticket--58", width === "58");
      const sizePx =
        width === "58"
          ? root.dataset.receiptFontPx58 || ""
          : root.dataset.receiptFontPx80 || "";
      if (sizePx) node.style.setProperty("--receipt-font-size", sizePx);
      const weightCss = root.dataset.receiptFontWeight || "";
      if (weightCss) node.style.setProperty("--receipt-font-weight", weightCss);
    });
    return true;
  }

  function syncSelection(width) {
    root.querySelectorAll(".receipt-paper-option").forEach((option) => {
      const input = option.querySelector("[data-receipt-paper]");
      option.classList.toggle("is-active", Boolean(input?.checked));
    });
    const sizePx =
      width === "58"
        ? root.dataset.receiptFontPx58 || ""
        : root.dataset.receiptFontPx80 || "";
    root.querySelectorAll("[data-receipt-ticket]").forEach((ticket) => {
      ticket.classList.toggle("receipt-ticket--80", width === "80");
      ticket.classList.toggle("receipt-ticket--58", width === "58");
      if (sizePx) ticket.style.setProperty("--receipt-font-size", sizePx);
    });
    if (paperStat) {
      paperStat.textContent = `${width} mm`;
    }
    paintLiveCopyPreviews();
    syncCopyPreviews();
  }

  function localPreview(prefix) {
    const clean = String(prefix || "R")
      .trim()
      .toUpperCase()
      .replace(/[^A-Z0-9]/g, "")
      .slice(0, 8);
    const stamp = "2608070915";
    return `${clean || "R"}12-${stamp}-A3F1`;
  }

  function updateFormatPreviews(map) {
    ["sale", "credit", "quotation", "trade_out", "trade_in"].forEach((kind) => {
      const el = root.querySelector(`[data-receipt-format-preview="${kind}"]`);
      if (!el) return;
      const value = map?.[kind] || localPreview(
        root.querySelector(`[data-receipt-format="${kind}"]`)?.value
      );
      el.textContent = `Example: ${value}`;
    });
    const salePreview =
      map?.sale ||
      localPreview(root.querySelector('[data-receipt-format="sale"]')?.value);
    root.querySelectorAll("[data-receipt-ticket-number]").forEach((el) => {
      el.textContent = salePreview;
    });
  }

  root.querySelectorAll("[data-receipt-paper]").forEach((input) => {
    input.addEventListener("change", async () => {
      if (!input.checked) return;
      const width = input.value;
      const previous = [...root.querySelectorAll("[data-receipt-paper]")].find(
        (el) => el !== input && el.dataset.previous === "1"
      );
      root.querySelectorAll("[data-receipt-paper]").forEach((el) => {
        el.dataset.previous = el.checked && el !== input ? "1" : "0";
      });
      syncSelection(width);
      input.disabled = true;

      try {
        const data = await postSettings(
          new URLSearchParams({
            action: "set_receipt_paper_width",
            receipt_paper_width: width,
          })
        );
        syncSelection(String(data.receipt_paper_width || width));
      } catch (error) {
        const fallback = previous?.value || (width === "80" ? "58" : "80");
        const fallbackInput = root.querySelector(
          `[data-receipt-paper][value="${fallback}"]`
        );
        if (fallbackInput) {
          fallbackInput.checked = true;
          syncSelection(fallback);
        }
        window.alert(error.message || "Could not save paper size.");
      } finally {
        input.disabled = false;
      }
    });
  });

  const formatInputs = [...root.querySelectorAll("[data-receipt-format]")];
  if (formatInputs.length) {
    let formatTimer = 0;
    const saved = Object.fromEntries(
      formatInputs.map((input) => [input.dataset.receiptFormat, input.value])
    );

    const saveFormats = async () => {
      const payload = {
        action: "set_receipt_number_formats",
        receipt_format_sale:
          root.querySelector('[data-receipt-format="sale"]')?.value || "",
        receipt_format_credit:
          root.querySelector('[data-receipt-format="credit"]')?.value || "",
        receipt_format_quotation:
          root.querySelector('[data-receipt-format="quotation"]')?.value || "",
        receipt_format_trade_out:
          root.querySelector('[data-receipt-format="trade_out"]')?.value || "",
        receipt_format_trade_in:
          root.querySelector('[data-receipt-format="trade_in"]')?.value || "",
      };
      const unchanged =
        payload.receipt_format_sale === saved.sale &&
        payload.receipt_format_credit === saved.credit &&
        payload.receipt_format_quotation === saved.quotation &&
        payload.receipt_format_trade_out === saved.trade_out &&
        payload.receipt_format_trade_in === saved.trade_in;
      if (unchanged) return;

      formatInputs.forEach((input) => input.classList.add("is-saving"));
      try {
        const data = await postSettings(new URLSearchParams(payload));
        saved.sale = data.receipt_format_sale;
        saved.credit = data.receipt_format_credit;
        saved.quotation = data.receipt_format_quotation;
        saved.trade_out = data.receipt_format_trade_out;
        saved.trade_in = data.receipt_format_trade_in;
        const saleInput = root.querySelector('[data-receipt-format="sale"]');
        const creditInput = root.querySelector('[data-receipt-format="credit"]');
        const quotationInput = root.querySelector(
          '[data-receipt-format="quotation"]'
        );
        const tradeOutInput = root.querySelector(
          '[data-receipt-format="trade_out"]'
        );
        if (saleInput) saleInput.value = saved.sale;
        if (creditInput) creditInput.value = saved.credit;
        if (quotationInput) quotationInput.value = saved.quotation;
        if (tradeOutInput) tradeOutInput.value = saved.trade_out;
        const tradeInInput = root.querySelector(
          '[data-receipt-format="trade_in"]'
        );
        if (tradeInInput) tradeInInput.value = saved.trade_in;
        formatInputs.forEach((input) => input.classList.remove("is-error"));
        updateFormatPreviews({
          sale: data.preview_sale,
          credit: data.preview_credit,
          quotation: data.preview_quotation,
          trade_out: data.preview_trade_out,
          trade_in: data.preview_trade_in,
        });
      } catch (error) {
        formatInputs.forEach((input) => {
          input.classList.add("is-error");
          input.value = saved[input.dataset.receiptFormat] || input.value;
        });
        updateFormatPreviews({
          sale: localPreview(saved.sale),
          credit: localPreview(saved.credit),
          quotation: localPreview(saved.quotation),
          trade_out: localPreview(saved.trade_out),
          trade_in: localPreview(saved.trade_in),
        });
        window.alert(error.message || "Could not save receipt formats.");
      } finally {
        formatInputs.forEach((input) => input.classList.remove("is-saving"));
      }
    };

    formatInputs.forEach((input) => {
      input.addEventListener("input", () => {
        const next = input.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8);
        if (input.value !== next) input.value = next;
        updateFormatPreviews();
        window.clearTimeout(formatTimer);
        formatTimer = window.setTimeout(saveFormats, 700);
      });
      input.addEventListener("change", saveFormats);
      input.addEventListener("blur", saveFormats);
    });
  }

  const typeInputs = [...root.querySelectorAll("[data-mpesa-type]")];
  const fieldBusiness = root.querySelector('[data-mpesa-field="business_number"]');
  const fieldAccount = root.querySelector('[data-mpesa-field="account_number"]');
  const fieldTill = root.querySelector('[data-mpesa-field="till_number"]');
  const fieldPhones = [...root.querySelectorAll('[data-mpesa-field="phone_number"]')];
  function paymentPreviewNodes() {
    return {
      blocks: root.querySelectorAll("[data-receipt-payment-block]"),
      titles: root.querySelectorAll("[data-receipt-payment-title]"),
      lines: root.querySelectorAll("[data-receipt-payment-lines]"),
    };
  }

  function selectedMpesaType() {
    return root.querySelector("[data-mpesa-type]:checked")?.value || "";
  }

  function activePhoneField() {
    const type = selectedMpesaType();
    return (
      root.querySelector(
        `[data-mpesa-field="phone_number"][data-mpesa-phone-for="${type}"]`
      ) || fieldPhones[0] || null
    );
  }

  function syncMpesaFields(type) {
    root.querySelectorAll("[data-mpesa-fields]").forEach((row) => {
      row.hidden = row.getAttribute("data-mpesa-fields") !== type;
    });
    root.querySelectorAll(".receipt-payment-type .receipt-paper-option").forEach((option) => {
      const input = option.querySelector("[data-mpesa-type]");
      option.classList.toggle("is-active", Boolean(input?.checked));
    });
  }

  function buildPaymentPreview() {
    const type = selectedMpesaType();
    if (type === "paybill") {
      const business = (fieldBusiness?.value || "").trim();
      const account = (fieldAccount?.value || "").trim();
      if (business.length < 5) return { label: "", lines: [] };
      const lines = [`Business No: ${business}`];
      if (account) lines.push(`Account No: ${account}`);
      return { label: "Paybill", lines };
    }
    if (type === "buy_goods") {
      const till = (fieldTill?.value || "").trim();
      if (till.length < 5) return { label: "", lines: [] };
      return { label: "Buy Goods", lines: [`Till No: ${till}`] };
    }
    if (type === "send_money" || type === "pochi") {
      const phone = (activePhoneField()?.value || "").trim();
      const digits = phone.replace(/\D/g, "");
      if (digits.length < 9) return { label: "", lines: [] };
      return {
        label: type === "pochi" ? "Pochi la Biashara" : "Send Money",
        lines: [`Phone: ${phone}`],
      };
    }
    return { label: "", lines: [] };
  }

  function paymentMethodsEnabled() {
    const toggle = root.querySelector(
      '[data-pos-toggle][data-field="enable_receipt_payment_methods"]'
    );
    return toggle ? toggle.checked : true;
  }

  function copyRoleFor(el) {
    return (
      el?.closest?.("[data-receipt-copy-ticket]")?.getAttribute(
        "data-receipt-copy-ticket"
      ) || ""
    );
  }

  function activatedPaymentMethodsLabel() {
    // Prefer the sample ticket value from the server (reflects POS toggles).
    const sample = readReceiptSample();
    const fromSample = String(sample?.ticket?.payment || "").trim();
    if (fromSample) return fromSample;
    return "Cash · M-Pesa";
  }

  function syncPaidRowPreview(enabled) {
    const show = Boolean(enabled);
    const label = activatedPaymentMethodsLabel();
    root.querySelectorAll("[data-receipt-paid-row]").forEach((row) => {
      const valueEl = row.querySelector("[data-receipt-paid-value]");
      const labelEl = row.querySelector("span");
      if (labelEl) labelEl.textContent = "Pay by";
      if (show) {
        if (valueEl) valueEl.textContent = label;
        row.hidden = !label;
      } else {
        row.hidden = true;
      }
    });
  }

  function renderPaymentPreview(details) {
    const showMethods = paymentMethodsEnabled();
    syncPaidRowPreview(showMethods);
    if (!showMethods) {
      details = { label: "", lines: [] };
    }
    const hasLines = Boolean(details?.lines?.length);
    const { blocks, titles, lines } = paymentPreviewNodes();
    blocks.forEach((block) => {
      // Staff copies omit M-Pesa collection instructions.
      if (copyRoleFor(block) === "staff") {
        block.hidden = true;
        return;
      }
      block.hidden = !hasLines;
    });
    titles.forEach((title) => {
      if (copyRoleFor(title) === "staff") {
        title.textContent = "";
        return;
      }
      title.textContent = hasLines ? `M-Pesa ${details.label}` : "";
    });
    lines.forEach((wrap) => {
      wrap.innerHTML = "";
      if (copyRoleFor(wrap) === "staff") return;
      (details?.lines || []).forEach((line) => {
        const p = document.createElement("p");
        p.textContent = line;
        wrap.appendChild(p);
      });
    });
  }

  if (typeInputs.length) {
    let paymentTimer = 0;

    const savePaymentDetails = async () => {
      const type = selectedMpesaType();
      const phoneInput = activePhoneField();
      const body = new URLSearchParams({
        action: "set_mpesa_payment_details",
        mpesa_collection_type: type,
        mpesa_business_number: fieldBusiness?.value || "",
        mpesa_account_number: fieldAccount?.value || "",
        mpesa_till_number: fieldTill?.value || "",
        mpesa_phone_number: phoneInput?.value || "",
      });
      const savingInputs = [fieldBusiness, fieldAccount, fieldTill, ...fieldPhones];
      savingInputs.forEach((input) => input?.classList.add("is-saving"));
      try {
        const data = await postSettings(body);
        if (fieldBusiness) fieldBusiness.value = data.mpesa_business_number || "";
        if (fieldAccount) fieldAccount.value = data.mpesa_account_number || "";
        if (fieldTill) fieldTill.value = data.mpesa_till_number || "";
        fieldPhones.forEach((input) => {
          input.value = data.mpesa_phone_number || "";
        });
        savingInputs.forEach((input) => input?.classList.remove("is-error"));
        renderPaymentPreview(data.mpesa_payment_details || buildPaymentPreview());
      } catch (error) {
        savingInputs.forEach((input) => input?.classList.add("is-error"));
        window.alert(error.message || "Could not save payment details.");
      } finally {
        savingInputs.forEach((input) => input?.classList.remove("is-saving"));
      }
    };

    const queuePaymentSave = () => {
      renderPaymentPreview(buildPaymentPreview());
      window.clearTimeout(paymentTimer);
      paymentTimer = window.setTimeout(savePaymentDetails, 700);
    };

    typeInputs.forEach((input) => {
      input.addEventListener("change", () => {
        if (!input.checked) return;
        syncMpesaFields(input.value);
        queuePaymentSave();
      });
    });

    fieldBusiness?.addEventListener("input", () => {
      fieldBusiness.value = fieldBusiness.value.replace(/\D+/g, "").slice(0, 8);
      queuePaymentSave();
    });
    fieldTill?.addEventListener("input", () => {
      fieldTill.value = fieldTill.value.replace(/\D+/g, "").slice(0, 8);
      queuePaymentSave();
    });
    fieldAccount?.addEventListener("input", () => {
      fieldAccount.value = fieldAccount.value.toUpperCase().slice(0, 40);
      queuePaymentSave();
    });
    fieldPhones.forEach((input) => {
      input.addEventListener("input", () => {
        // Keep both phone inputs in sync (shared stored value).
        const value = input.value.slice(0, 40);
        fieldPhones.forEach((other) => {
          if (other !== input) other.value = value;
        });
        queuePaymentSave();
      });
    });
    [fieldBusiness, fieldAccount, fieldTill, ...fieldPhones].forEach((input) => {
      input?.addEventListener("change", savePaymentDetails);
      input?.addEventListener("blur", savePaymentDetails);
    });

    syncMpesaFields(selectedMpesaType());
    renderPaymentPreview(buildPaymentPreview());
  }

  const fontSizeSelect = root.querySelector("[data-receipt-font-size]");
  const fontWeightSelect = root.querySelector("[data-receipt-font-weight]");
  const tickets = () => root.querySelectorAll("[data-receipt-ticket]");

  function applyFontStyle({ sizePx80, sizePx58, weightCss }) {
    if (sizePx80) root.dataset.receiptFontPx80 = sizePx80;
    if (sizePx58) root.dataset.receiptFontPx58 = sizePx58;
    if (weightCss) root.dataset.receiptFontWeight = weightCss;
    const width =
      root.querySelector("[data-receipt-paper]:checked")?.value || "80";
    tickets().forEach((ticket) => {
      const size = width === "58" ? sizePx58 : sizePx80;
      if (size) ticket.style.setProperty("--receipt-font-size", size);
      if (weightCss) ticket.style.setProperty("--receipt-font-weight", weightCss);
    });
  }

  COPY_FIELDS.forEach((field) => {
    const input = root.querySelector(`[data-pos-toggle][data-field="${field}"]`);
    input?.addEventListener("change", () => {
      window.setTimeout(() => {
        syncCopyPreviews();
        paintLiveCopyPreviews();
      }, 0);
    });
  });

  // Keep the ticket preview aligned with any receipt toggle that affects printed details.
  root.addEventListener("change", (event) => {
    const input = event.target?.closest?.("[data-pos-toggle][data-field]");
    if (!input || !root.contains(input)) return;
    const field = input.getAttribute("data-field") || "";
    if (!PREVIEW_TOGGLE_FIELDS.has(field)) return;
    window.setTimeout(() => {
      paintLiveCopyPreviews();
      syncCopyPreviews();
    }, 0);
  });

  if (fontSizeSelect && fontWeightSelect) {
    let fontTimer = 0;
    const saveFontStyle = async () => {
      try {
        const data = await postSettings(
          new URLSearchParams({
            action: "set_receipt_font_style",
            receipt_font_size: fontSizeSelect.value,
            receipt_font_weight: fontWeightSelect.value,
          })
        );
        applyFontStyle({
          sizePx80: data.size_px_80,
          sizePx58: data.size_px_58,
          weightCss: data.weight_css,
        });
        fontSizeSelect.classList.remove("is-error");
        fontWeightSelect.classList.remove("is-error");
      } catch (error) {
        fontSizeSelect.classList.add("is-error");
        fontWeightSelect.classList.add("is-error");
        window.alert(error.message || "Could not save font style.");
      }
    };

    const queueFontSave = () => {
      window.clearTimeout(fontTimer);
      fontTimer = window.setTimeout(saveFontStyle, 250);
    };

    fontSizeSelect.addEventListener("change", queueFontSave);
    fontWeightSelect.addEventListener("change", queueFontSave);
  }

  const qrEnabledInput = root.querySelector("[data-receipt-qr-enabled]");
  const qrEnabledLabel = root.querySelector("[data-receipt-qr-enabled-label]");
  const qrOptions = root.querySelector("[data-receipt-qr-options]");
  const qrWebsiteRow = root.querySelector("[data-receipt-qr-website-row]");
  const qrWebsiteInput = root.querySelector("[data-receipt-qr-website]");
  const qrContentInputs = [...root.querySelectorAll("[data-receipt-qr-content]")];

  function selectedQrContent() {
    return root.querySelector("[data-receipt-qr-content]:checked")?.value || "website";
  }

  function syncQrUi() {
    const enabled = Boolean(qrEnabledInput?.checked);
    const content = selectedQrContent();
    if (qrOptions) qrOptions.hidden = !enabled;
    if (qrWebsiteRow) qrWebsiteRow.hidden = !enabled || content !== "website";
    if (qrEnabledLabel) qrEnabledLabel.textContent = enabled ? "On" : "Off";
    root.querySelectorAll(".receipt-qr-content .receipt-paper-option").forEach((option) => {
      const input = option.querySelector("[data-receipt-qr-content]");
      option.classList.toggle("is-active", Boolean(input?.checked));
    });
  }

  function renderQrPreview(qr) {
    if (qr && typeof qr === "object") {
      const image = qr.image_data_url || "";
      if (image) {
        liveQrState = {
          ready: true,
          image_data_url: image,
          label: qr.label || liveQrState.label || "",
          payload: qr.payload || qr.url || liveQrState.payload || "",
        };
      } else if (qr.ready === false) {
        // Keep the last usable image so turning QR back on still previews
        // immediately; paintLiveCopyPreviews respects the QR toggle for visibility.
        liveQrState = {
          ...liveQrState,
          ready: Boolean(liveQrState.image_data_url),
        };
      } else {
        liveQrState = {
          ready: Boolean(qr.ready && image),
          image_data_url: image,
          label: qr.label || "",
          payload: qr.payload || qr.url || "",
        };
      }
    }
    // Prefer a full ticket re-paint so every copy picks up QR + other toggles.
    if (paintLiveCopyPreviews()) return;
    const showQr = Boolean(
      (qrEnabledInput ? qrEnabledInput.checked : true) && liveQrState.ready
    );
    root.querySelectorAll("[data-receipt-qr-block]").forEach((block) => {
      if (copyRoleFor(block) === "staff") {
        block.hidden = true;
        return;
      }
      block.hidden = !showQr;
    });
    root.querySelectorAll("[data-receipt-qr-image]").forEach((img) => {
      if (copyRoleFor(img) === "staff") {
        img.removeAttribute("src");
        img.hidden = true;
        return;
      }
      if (showQr) {
        img.src = liveQrState.image_data_url;
        img.hidden = false;
      } else {
        img.removeAttribute("src");
        img.hidden = true;
      }
    });
    root.querySelectorAll("[data-receipt-qr-label]").forEach((label) => {
      if (copyRoleFor(label) === "staff") {
        label.textContent = "";
        return;
      }
      label.textContent = showQr ? liveQrState.label || "" : "";
    });
  }

  if (qrEnabledInput) {
    let qrTimer = 0;
    let savedEnabled = Boolean(qrEnabledInput.checked);
    let savedContent = selectedQrContent();
    let savedWebsite = qrWebsiteInput?.value || "";

    function websiteUrlRequired() {
      return (
        Boolean(qrEnabledInput.checked) &&
        selectedQrContent() === "website" &&
        !(qrWebsiteInput?.value || "").trim()
      );
    }

    const saveQrSettings = async () => {
      if (websiteUrlRequired()) {
        qrWebsiteInput?.classList.add("is-error");
        qrWebsiteInput?.focus();
        return;
      }
      const body = new URLSearchParams({
        action: "set_receipt_qr_settings",
        enable_receipt_qr: qrEnabledInput.checked ? "1" : "0",
        receipt_qr_content: selectedQrContent(),
        receipt_qr_website: qrWebsiteInput?.value || "",
      });
      qrWebsiteInput?.classList.add("is-saving");
      try {
        const data = await postSettings(body);
        savedEnabled = Boolean(data.enable_receipt_qr);
        savedContent = data.receipt_qr_content || savedContent;
        savedWebsite =
          typeof data.receipt_qr_website === "string"
            ? data.receipt_qr_website
            : savedWebsite;
        qrEnabledInput.checked = savedEnabled;
        const contentInput = root.querySelector(
          `[data-receipt-qr-content][value="${savedContent}"]`
        );
        if (contentInput) contentInput.checked = true;
        if (qrWebsiteInput) qrWebsiteInput.value = savedWebsite;
        qrWebsiteInput?.classList.remove("is-error");
        syncQrUi();
        renderQrPreview(data.receipt_qr);
      } catch (error) {
        qrEnabledInput.checked = savedEnabled;
        const contentInput = root.querySelector(
          `[data-receipt-qr-content][value="${savedContent}"]`
        );
        if (contentInput) contentInput.checked = true;
        if (qrWebsiteInput) qrWebsiteInput.value = savedWebsite;
        qrWebsiteInput?.classList.add("is-error");
        syncQrUi();
        window.alert(error.message || "Could not save QR settings.");
        if (qrEnabledInput.checked && selectedQrContent() === "website") {
          qrWebsiteInput?.focus();
        }
      } finally {
        qrWebsiteInput?.classList.remove("is-saving");
      }
    };

    const queueQrSave = () => {
      syncQrUi();
      window.clearTimeout(qrTimer);
      // Wait for a website URL before saving — avoids the alert when enabling QR.
      if (websiteUrlRequired()) {
        qrWebsiteInput?.classList.add("is-error");
        return;
      }
      qrTimer = window.setTimeout(saveQrSettings, 500);
    };

    qrEnabledInput.addEventListener("change", () => {
      syncQrUi();
      // Instantly hide/show QR in the preview while the save request runs.
      paintLiveCopyPreviews();
      if (websiteUrlRequired()) {
        qrWebsiteInput?.classList.add("is-error");
        qrWebsiteInput?.focus();
        return;
      }
      queueQrSave();
    });

    qrContentInputs.forEach((input) => {
      input.addEventListener("change", () => {
        if (!input.checked) return;
        syncQrUi();
        if (websiteUrlRequired()) {
          qrWebsiteInput?.classList.add("is-error");
          qrWebsiteInput?.focus();
          return;
        }
        queueQrSave();
      });
    });

    qrWebsiteInput?.addEventListener("input", () => {
      if ((qrWebsiteInput.value || "").trim()) {
        qrWebsiteInput.classList.remove("is-error");
      }
      queueQrSave();
    });
    qrWebsiteInput?.addEventListener("change", saveQrSettings);
    qrWebsiteInput?.addEventListener("blur", () => {
      if (websiteUrlRequired()) {
        // Keep the last saved URL if the field was cleared while QR is on.
        if (savedWebsite) {
          qrWebsiteInput.value = savedWebsite;
          qrWebsiteInput.classList.remove("is-error");
        }
        return;
      }
      saveQrSettings();
    });

    // Keep server-rendered preview visible after JS binds.
    const bootImage = root.querySelector("[data-receipt-qr-image]");
    const bootSrc = bootImage?.getAttribute("src") || "";
    if (bootSrc) {
      renderQrPreview({
        ready: true,
        image_data_url: bootSrc,
        label: root.querySelector("[data-receipt-qr-label]")?.textContent?.trim() || "",
      });
    }

    syncQrUi();
  }

  const checked = root.querySelector("[data-receipt-paper]:checked");
  if (checked) {
    checked.dataset.previous = "1";
    syncSelection(checked.value);
  }

  const printSampleBtn = root.querySelector("[data-receipt-print-sample]");
  printSampleBtn?.addEventListener("click", async () => {
    const sample = readReceiptSample();
    if (!sample?.ticket) {
      window.alert("Sample receipt data is missing. Refresh the page.");
      return;
    }
    const showClientPhone =
      root.querySelector(
        '[data-pos-toggle][data-field="show_client_phone_on_receipt"]'
      )?.checked !== false;
    const fullPhone = String(
      sample.ticket.party_phone_full || sample.ticket.party_phone || ""
    );
    const displayPhone = showClientPhone ? fullPhone : maskClientPhoneDigits(fullPhone);
    const returnNoticeOn = Boolean(
      root.querySelector(
        '[data-pos-toggle][data-field="show_return_notice_on_receipt"]'
      )?.checked
    );
    const maintainerOn = Boolean(
      root.querySelector(
        '[data-pos-toggle][data-field="show_maintainer_on_receipt"]'
      )?.checked
    );
    const maintainerPhone = String(
      root.querySelector("[data-maintainer-phone]")?.value || ""
    ).trim();
    const maintainer =
      maintainerOn && maintainerPhone
        ? `System maintained by ${maintainerPhone}`
        : "";
    const paperWidth =
      root.querySelector("[data-receipt-paper]:checked")?.value ||
      sample.paper_width ||
      "80";
    const font = {
      ...(sample.font || {}),
      paper_width: paperWidth,
    };
    const enabledRoles = new Set(
      COPY_FIELDS.filter((field) =>
        Boolean(
          root.querySelector(`[data-pos-toggle][data-field="${field}"]`)?.checked
        )
      ).map((field) => field.replace("print_", "").replace("_receipt", ""))
    );
    const sampleCopies = (sample.copies || []).filter((copy) =>
      enabledRoles.has(copy.role)
    );
    const copies =
      sampleCopies.length > 0
        ? sampleCopies
        : [
            {
              role: "customer",
              label: "Customer",
              ticket: sample.ticket,
              qr: sample.qr || {},
            },
          ];
    if (!copies.length) {
      window.alert("Turn on at least one receipt copy before printing a sample.");
      return;
    }
    printSampleBtn.disabled = true;
    try {
      if (typeof window.RichcomPrinter?.browserPrint !== "function") {
        window.alert("Print helper is not loaded. Refresh the page.");
        return;
      }
      for (const copy of copies) {
        const ticket = {
          ...(copy.ticket || sample.ticket),
          party_phone: displayPhone,
          party_phone_print: displayPhone,
          return_notice: returnNoticeOn ? readReturnNoticeText() : "",
          maintainer,
        };
        const qrToggleOn = root.querySelector("[data-receipt-qr-enabled]")
          ? Boolean(root.querySelector("[data-receipt-qr-enabled]").checked)
          : true;
        const qrSource =
          liveQrState?.ready && liveQrState?.image_data_url
            ? liveQrState
            : copy.qr || sample.qr || {};
        const includeQr = ticket.include_qr !== false && qrToggleOn;
        await window.RichcomPrinter.browserPrint(
          "",
          includeQr
            ? {
                payload: qrSource.payload || qrSource.url || "",
                label: qrSource.label || "",
                ready: Boolean(qrSource.ready && qrSource.image_data_url),
                image_data_url: qrSource.image_data_url || "",
              }
            : { ready: false },
          copy.label || "Sample",
          font,
          ticket
        );
      }
    } catch (error) {
      window.alert(error?.message || "Could not open the print dialog.");
    } finally {
      printSampleBtn.disabled = false;
    }
  });

  const clientPhoneToggle = root.querySelector(
    '[data-pos-toggle][data-field="show_client_phone_on_receipt"]'
  );
  if (clientPhoneToggle) {
    const syncClientPhonePreview = () => {
      if (paintLiveCopyPreviews()) return;
      const show = clientPhoneToggle.checked;
      root.querySelectorAll("[data-receipt-client-phone]").forEach((el) => {
        const full = el.getAttribute("data-full-phone") || "";
        const value = show ? full : maskClientPhoneDigits(full);
        el.hidden = !value;
        el.querySelectorAll(".receipt-phone-screen, .receipt-phone-print").forEach((span) => {
          span.textContent = value;
        });
      });
    };
    clientPhoneToggle.addEventListener("change", () => {
      window.setTimeout(syncClientPhonePreview, 0);
    });
    syncClientPhonePreview();
  }

  const paymentMethodToggle = root.querySelector(
    '[data-pos-toggle][data-field="enable_receipt_payment_methods"]'
  );
  if (paymentMethodToggle) {
    paymentMethodToggle.addEventListener("change", () => {
      window.setTimeout(() => {
        paintLiveCopyPreviews();
        renderPaymentPreview(buildPaymentPreview());
      }, 0);
    });
    paintLiveCopyPreviews();
    renderPaymentPreview(buildPaymentPreview());
  }

  const receiptPhoneInput = root.querySelector("[data-receipt-phone]");
  if (receiptPhoneInput) {
    const phoneHint = root.querySelector("[data-receipt-phone-hint]");
    let phoneTimer = 0;
    let savedPhone = receiptPhoneInput.value || "";

    function syncTicketPhone(displayPhone) {
      const value = String(displayPhone || "").trim();
      root.querySelectorAll("[data-receipt-ticket-phone]").forEach((el) => {
        el.textContent = value;
        el.hidden = !value;
      });
    }

    function syncPhoneHint(usingCompany, companyPhone) {
      if (!phoneHint) return;
      if (!usingCompany) {
        phoneHint.textContent = "Using this shop’s phone on receipts.";
        return;
      }
      const company = String(companyPhone || receiptPhoneInput.dataset.companyPhone || "").trim();
      phoneHint.textContent = company
        ? `Currently using company phone: ${company}`
        : "No company phone set yet — add one here or in company profile.";
    }

    const saveReceiptPhone = async () => {
      const next = (receiptPhoneInput.value || "").trim();
      if (next === savedPhone) return;
      receiptPhoneInput.classList.add("is-saving");
      try {
        const data = await postSettings(
          new URLSearchParams({
            action: "set_shop_receipt_phone",
            receipt_phone_number: next,
          })
        );
        savedPhone = data.receipt_phone_number || "";
        receiptPhoneInput.value = savedPhone;
        if (data.company_phone) {
          receiptPhoneInput.dataset.companyPhone = data.company_phone;
          if (!receiptPhoneInput.placeholder) {
            receiptPhoneInput.placeholder = data.company_phone;
          }
        }
        receiptPhoneInput.classList.remove("is-error");
        syncTicketPhone(data.shop_phone || "");
        syncPhoneHint(Boolean(data.using_company_phone), data.company_phone || "");
      } catch (error) {
        receiptPhoneInput.classList.add("is-error");
        receiptPhoneInput.value = savedPhone;
        window.alert(error.message || "Could not save shop phone.");
      } finally {
        receiptPhoneInput.classList.remove("is-saving");
      }
    };

    receiptPhoneInput.addEventListener("input", () => {
      window.clearTimeout(phoneTimer);
      phoneTimer = window.setTimeout(saveReceiptPhone, 700);
    });
    receiptPhoneInput.addEventListener("change", saveReceiptPhone);
    receiptPhoneInput.addEventListener("blur", saveReceiptPhone);
  }

  const returnNoticeToggle = root.querySelector(
    '[data-pos-toggle][data-field="show_return_notice_on_receipt"]'
  );
  const returnNoticeTextInput = root.querySelector("[data-return-notice-text]");
  const returnNoticeTextRow = root.querySelector("[data-return-notice-text-row]");

  function syncReturnNoticePreview() {
    const show = Boolean(returnNoticeToggle?.checked);
    const text = readReturnNoticeText();
    if (returnNoticeTextRow) returnNoticeTextRow.hidden = !show;
    if (paintLiveCopyPreviews()) return;
    root.querySelectorAll("[data-receipt-return-notice]").forEach((el) => {
      el.textContent = text;
      el.hidden = !show;
    });
  }

  if (returnNoticeToggle) {
    returnNoticeToggle.addEventListener("change", () => {
      window.setTimeout(syncReturnNoticePreview, 0);
    });
  }

  if (returnNoticeTextInput) {
    let returnNoticeTimer = 0;
    let savedReturnNoticeText = returnNoticeTextInput.value || "";

    const saveReturnNoticeText = async () => {
      const next = (returnNoticeTextInput.value || "").trim();
      if (!next) {
        returnNoticeTextInput.classList.add("is-error");
        returnNoticeTextInput.value = savedReturnNoticeText || DEFAULT_RETURN_NOTICE;
        return;
      }
      if (next === savedReturnNoticeText) {
        syncReturnNoticePreview();
        return;
      }
      returnNoticeTextInput.classList.add("is-saving");
      try {
        const data = await postSettings(
          new URLSearchParams({
            action: "set_return_notice_text",
            return_notice_text: next,
          })
        );
        savedReturnNoticeText = data.return_notice_text || next;
        returnNoticeTextInput.value = savedReturnNoticeText;
        returnNoticeTextInput.classList.remove("is-error");
      } catch (error) {
        returnNoticeTextInput.classList.add("is-error");
        returnNoticeTextInput.value = savedReturnNoticeText || DEFAULT_RETURN_NOTICE;
        window.alert(error.message || "Could not save return policy text.");
      } finally {
        returnNoticeTextInput.classList.remove("is-saving");
        syncReturnNoticePreview();
      }
    };

    returnNoticeTextInput.addEventListener("input", () => {
      syncReturnNoticePreview();
      window.clearTimeout(returnNoticeTimer);
      returnNoticeTimer = window.setTimeout(saveReturnNoticeText, 700);
    });
    returnNoticeTextInput.addEventListener("change", saveReturnNoticeText);
    returnNoticeTextInput.addEventListener("blur", saveReturnNoticeText);
  }

  const maintainerPhoneInput = root.querySelector("[data-maintainer-phone]");
  const maintainerPhoneRow = root.querySelector("[data-maintainer-phone-row]");
  const maintainerToggle = root.querySelector(
    '[data-pos-toggle][data-field="show_maintainer_on_receipt"]'
  );

  function syncMaintainerPreview() {
    const show = Boolean(maintainerToggle?.checked);
    const phone = String(maintainerPhoneInput?.value || "").trim();
    const text = show && phone ? `System maintained by ${phone}` : "";
    if (maintainerPhoneRow) maintainerPhoneRow.hidden = !show;
    if (paintLiveCopyPreviews()) return;
    root.querySelectorAll("[data-receipt-maintainer]").forEach((el) => {
      el.textContent = text;
      el.hidden = !text;
    });
  }

  if (maintainerToggle) {
    maintainerToggle.addEventListener("change", () => {
      window.setTimeout(syncMaintainerPreview, 0);
    });
  }

  if (maintainerPhoneInput) {
    let maintainerTimer = 0;
    let savedMaintainerPhone = maintainerPhoneInput.value || "";

    const saveMaintainerPhone = async () => {
      const next = (maintainerPhoneInput.value || "").trim();
      if (next === savedMaintainerPhone) return;
      maintainerPhoneInput.classList.add("is-saving");
      try {
        const data = await postSettings(
          new URLSearchParams({
            action: "set_maintainer_phone",
            maintainer_phone: next,
          })
        );
        savedMaintainerPhone = data.maintainer_phone || "";
        maintainerPhoneInput.value = savedMaintainerPhone;
        maintainerPhoneInput.classList.remove("is-error");
      } catch (error) {
        maintainerPhoneInput.classList.add("is-error");
        maintainerPhoneInput.value = savedMaintainerPhone;
        window.alert(error.message || "Could not save maintainer phone.");
      } finally {
        maintainerPhoneInput.classList.remove("is-saving");
        syncMaintainerPreview();
      }
    };

    maintainerPhoneInput.addEventListener("input", () => {
      syncMaintainerPreview();
      window.clearTimeout(maintainerTimer);
      maintainerTimer = window.setTimeout(saveMaintainerPhone, 700);
    });
    maintainerPhoneInput.addEventListener("change", saveMaintainerPhone);
    maintainerPhoneInput.addEventListener("blur", saveMaintainerPhone);
  }

  syncReturnNoticePreview();
  syncMaintainerPreview();
})();
