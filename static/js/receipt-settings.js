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
  const badge80 = root.querySelector("[data-receipt-badge-80]");
  const badge58 = root.querySelector("[data-receipt-badge-58]");

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

  function syncSelection(width) {
    root.querySelectorAll("[data-receipt-preview]").forEach((card) => {
      const selected = card.getAttribute("data-receipt-preview") === width;
      card.classList.toggle("is-selected", selected);
    });
    root.querySelectorAll(".receipt-paper-option").forEach((option) => {
      const input = option.querySelector("[data-receipt-paper]");
      option.classList.toggle("is-active", Boolean(input?.checked));
    });
    if (activeLabel) {
      activeLabel.textContent = `Active: ${width} mm`;
    }
    if (badge80) badge80.textContent = width === "80" ? "Selected" : "Preview";
    if (badge58) badge58.textContent = width === "58" ? "Selected" : "Preview";
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
    ["sale", "credit", "quotation"].forEach((kind) => {
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
      };
      const unchanged =
        payload.receipt_format_sale === saved.sale &&
        payload.receipt_format_credit === saved.credit &&
        payload.receipt_format_quotation === saved.quotation;
      if (unchanged) return;

      formatInputs.forEach((input) => input.classList.add("is-saving"));
      try {
        const data = await postSettings(new URLSearchParams(payload));
        saved.sale = data.receipt_format_sale;
        saved.credit = data.receipt_format_credit;
        saved.quotation = data.receipt_format_quotation;
        const saleInput = root.querySelector('[data-receipt-format="sale"]');
        const creditInput = root.querySelector('[data-receipt-format="credit"]');
        const quotationInput = root.querySelector(
          '[data-receipt-format="quotation"]'
        );
        if (saleInput) saleInput.value = saved.sale;
        if (creditInput) creditInput.value = saved.credit;
        if (quotationInput) quotationInput.value = saved.quotation;
        formatInputs.forEach((input) => input.classList.remove("is-error"));
        updateFormatPreviews({
          sale: data.preview_sale,
          credit: data.preview_credit,
          quotation: data.preview_quotation,
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
  const paymentBlocks = root.querySelectorAll("[data-receipt-payment-block]");
  const paymentTitles = root.querySelectorAll("[data-receipt-payment-title]");
  const paymentLines = root.querySelectorAll("[data-receipt-payment-lines]");

  function selectedMpesaType() {
    return root.querySelector("[data-mpesa-type]:checked")?.value || "";
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
    return { label: "", lines: [] };
  }

  function renderPaymentPreview(details) {
    const hasLines = Boolean(details?.lines?.length);
    paymentBlocks.forEach((block) => {
      block.hidden = !hasLines;
    });
    paymentTitles.forEach((title) => {
      title.textContent = hasLines ? `M-Pesa ${details.label}` : "";
    });
    paymentLines.forEach((wrap) => {
      wrap.innerHTML = "";
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
      const body = new URLSearchParams({
        action: "set_mpesa_payment_details",
        mpesa_collection_type: type,
        mpesa_business_number: fieldBusiness?.value || "",
        mpesa_account_number: fieldAccount?.value || "",
        mpesa_till_number: fieldTill?.value || "",
      });
      [fieldBusiness, fieldAccount, fieldTill].forEach((input) =>
        input?.classList.add("is-saving")
      );
      try {
        const data = await postSettings(body);
        if (fieldBusiness) fieldBusiness.value = data.mpesa_business_number || "";
        if (fieldAccount) fieldAccount.value = data.mpesa_account_number || "";
        if (fieldTill) fieldTill.value = data.mpesa_till_number || "";
        [fieldBusiness, fieldAccount, fieldTill].forEach((input) =>
          input?.classList.remove("is-error")
        );
        renderPaymentPreview(data.mpesa_payment_details || buildPaymentPreview());
      } catch (error) {
        [fieldBusiness, fieldAccount, fieldTill].forEach((input) =>
          input?.classList.add("is-error")
        );
        window.alert(error.message || "Could not save payment details.");
      } finally {
        [fieldBusiness, fieldAccount, fieldTill].forEach((input) =>
          input?.classList.remove("is-saving")
        );
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
    [fieldBusiness, fieldAccount, fieldTill].forEach((input) => {
      input?.addEventListener("change", savePaymentDetails);
      input?.addEventListener("blur", savePaymentDetails);
    });

    syncMpesaFields(selectedMpesaType());
    renderPaymentPreview(buildPaymentPreview());
  }

  const fontSizeSelect = root.querySelector("[data-receipt-font-size]");
  const fontWeightSelect = root.querySelector("[data-receipt-font-weight]");
  const tickets = root.querySelectorAll("[data-receipt-ticket]");

  function applyFontStyle({ sizePx80, sizePx58, weightCss }) {
    tickets.forEach((ticket) => {
      const width = ticket.getAttribute("data-receipt-ticket");
      const size = width === "58" ? sizePx58 : sizePx80;
      if (size) ticket.style.setProperty("--receipt-font-size", size);
      if (weightCss) ticket.style.setProperty("--receipt-font-weight", weightCss);
    });
  }

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
  const qrBlocks = root.querySelectorAll("[data-receipt-qr-block]");
  const qrImages = root.querySelectorAll("[data-receipt-qr-image]");
  const qrLabels = root.querySelectorAll("[data-receipt-qr-label]");

  function selectedQrContent() {
    return root.querySelector("[data-receipt-qr-content]:checked")?.value || "website";
  }

  function syncQrUi() {
    const enabled = Boolean(qrEnabledInput?.checked);
    const content = selectedQrContent();
    if (qrOptions) qrOptions.hidden = !enabled;
    if (qrWebsiteRow) qrWebsiteRow.hidden = !enabled || content !== "website";
    if (qrEnabledLabel) qrEnabledLabel.textContent = enabled ? "Enabled" : "Disabled";
    root.querySelectorAll(".receipt-qr-content .receipt-paper-option").forEach((option) => {
      const input = option.querySelector("[data-receipt-qr-content]");
      option.classList.toggle("is-active", Boolean(input?.checked));
    });
  }

  function renderQrPreview(qr) {
    const ready = Boolean(qr?.ready && qr?.image_data_url);
    qrBlocks.forEach((block) => {
      block.hidden = !ready;
    });
    qrImages.forEach((img) => {
      if (ready) {
        img.src = qr.image_data_url;
        img.hidden = false;
      } else {
        img.removeAttribute("src");
        img.hidden = true;
      }
    });
    qrLabels.forEach((label) => {
      label.textContent = ready ? qr.label || "" : "";
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
    const sampleEl = document.getElementById("receipt-sample-data");
    let sample = null;
    try {
      sample = sampleEl ? JSON.parse(sampleEl.textContent || "{}") : null;
    } catch (_) {
      sample = null;
    }
    if (!sample?.ticket) {
      window.alert("Sample receipt data is missing. Refresh the page.");
      return;
    }
    const paperWidth =
      root.querySelector("[data-receipt-paper]:checked")?.value ||
      sample.paper_width ||
      "80";
    const font = {
      ...(sample.font || {}),
      paper_width: paperWidth,
    };
    const qr = sample.qr || {};
    printSampleBtn.disabled = true;
    try {
      if (typeof window.RichcomPrinter?.browserPrint === "function") {
        await window.RichcomPrinter.browserPrint(
          "",
          {
            payload: qr.payload || qr.url || "",
            label: qr.label || "",
            ready: Boolean(qr.ready),
            image_data_url: qr.image_data_url || "",
          },
          "Sample",
          font,
          sample.ticket
        );
      } else {
        window.alert("Print helper is not loaded. Refresh the page.");
      }
    } catch (error) {
      window.alert(error?.message || "Could not open the print dialog.");
    } finally {
      printSampleBtn.disabled = false;
    }
  });
})();
