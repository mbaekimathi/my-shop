(() => {
  const STORAGE_KEY = "richcom.myShop.printer";
  const BT_SERVICES = [
    "000018f0-0000-1000-8000-00805f9b34fb",
    "0000ff00-0000-1000-8000-00805f9b34fb",
    "0000ffe0-0000-1000-8000-00805f9b34fb",
    "49535343-fe7d-4ae5-8fa9-9fafd205e455",
    "e7810a71-73ae-499d-8c15-faa9aef0c3f2",
  ];
  const CHANNEL_META = {
    bluetooth: { label: "Bluetooth", icon: "bluetooth" },
    usb: { label: "USB", icon: "usb" },
    wifi: { label: "Wi‑Fi", icon: "wifi" },
  };

  const textEncoder = new TextEncoder();

  const withTimeout = (promise, ms, message) => {
    let timer = null;
    return Promise.race([
      Promise.resolve(promise).finally(() => {
        if (timer) window.clearTimeout(timer);
      }),
      new Promise((_, reject) => {
        timer = window.setTimeout(() => {
          reject(new Error(message || `Timed out after ${ms}ms.`));
        }, ms);
      }),
    ]);
  };

  const getReceiptPrintStyle = (override = null) => {
    const root = document.querySelector("[data-shop-cart]");
    const ds = root?.dataset || {};
    const ov = override && typeof override === "object" ? override : {};
    const paperMm =
      ov.paper_width === "58" || ov.paper_width === "80"
        ? ov.paper_width
        : ds.posReceiptWidth === "58"
          ? "58"
          : "80";
    // Prefer explicit size key from settings (avoids broken data-*-80 dataset keys).
    const sizeKeyRaw = String(ov.size || ds.posReceiptFontSize || "medium").toLowerCase();
    const sizeKey = ["small", "medium", "large", "xlarge"].includes(sizeKeyRaw)
      ? sizeKeyRaw
      : "medium";
    const pxMap = {
      small: { "80": "10px", "58": "8.5px" },
      medium: { "80": "11.5px", "58": "9.5px" },
      large: { "80": "13px", "58": "11px" },
      xlarge: { "80": "14.5px", "58": "12px" },
    };
    const fontSize =
      ov[`size_px_${paperMm}`] ||
      (paperMm === "58"
        ? ds.posReceiptFontPx58 || pxMap[sizeKey]["58"]
        : ds.posReceiptFontPx80 || pxMap[sizeKey]["80"]);
    const weightKeyRaw = String(
      ov.weight || ds.posReceiptFontWeight || "regular"
    ).toLowerCase();
    const weightKey = ["regular", "medium", "bold", "extrabold"].includes(weightKeyRaw)
      ? weightKeyRaw
      : "regular";
    const weightCssMap = {
      regular: "400",
      medium: "600",
      bold: "700",
      extrabold: "800",
    };
    const fontWeight =
      ov.weight_css || ds.posReceiptFontWeightCss || weightCssMap[weightKey];
    const weightNum = Number(fontWeight) || 400;
    // GS ! n — width in high nibble, height in low nibble (0=1x, 1=2x).
    const escPosMagnification =
      sizeKey === "xlarge" ? 0x11 : sizeKey === "large" ? 0x01 : 0x00;
    return {
      paperMm,
      paperWidth: `${paperMm}mm`,
      fontSize,
      fontWeight,
      sizeKey,
      weightKey,
      bold: weightNum >= 600,
      doubleStrike: weightKey === "extrabold" || weightNum >= 800,
      qrSize: paperMm === "58" ? "28mm" : "32mm",
      headingSize:
        paperMm === "58" ? `calc(${fontSize} + 1px)` : `calc(${fontSize} + 2px)`,
      escPosMagnification,
    };
  };

  const encodeEscPosQr = (payload) => {
    const data = textEncoder.encode(String(payload || ""));
    if (!data.length) return [];
    const chunks = [];
    // Model 2
    chunks.push(new Uint8Array([0x1d, 0x28, 0x6b, 0x04, 0x00, 0x31, 0x41, 0x32, 0x00]));
    // Module size
    chunks.push(new Uint8Array([0x1d, 0x28, 0x6b, 0x03, 0x00, 0x31, 0x43, 0x06]));
    // Error correction M
    chunks.push(new Uint8Array([0x1d, 0x28, 0x6b, 0x03, 0x00, 0x31, 0x45, 0x31]));
    // Store data
    const storeLen = data.length + 3;
    const pL = storeLen & 0xff;
    const pH = (storeLen >> 8) & 0xff;
    const store = new Uint8Array(8 + data.length);
    store.set([0x1d, 0x28, 0x6b, pL, pH, 0x31, 0x50, 0x30], 0);
    store.set(data, 8);
    chunks.push(store);
    // Print
    chunks.push(new Uint8Array([0x1d, 0x28, 0x6b, 0x03, 0x00, 0x31, 0x51, 0x30]));
    return chunks;
  };

  const loadImageFromDataUrl = (dataUrl) =>
    new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("Could not load QR image."));
      img.src = dataUrl;
    });

  /** Raster QR via GS v 0 — works on printers that ignore native QR commands. */
  const encodeEscPosRasterQr = async (dataUrl, paperMm = "80") => {
    const src = String(dataUrl || "");
    if (!src.startsWith("data:image")) return [];
    try {
      const img = await loadImageFromDataUrl(src);
      const targetPx = paperMm === "58" ? 184 : 240;
      const scale = targetPx / Math.max(img.width, 1);
      const width = Math.max(8, Math.floor(img.width * scale));
      const height = Math.max(8, Math.floor(img.height * scale));
      const bytesPerRow = Math.ceil(width / 8);
      const canvas = document.createElement("canvas");
      canvas.width = bytesPerRow * 8;
      canvas.height = height;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) return [];
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(img, 0, 0, width, height);
      const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const raster = new Uint8Array(bytesPerRow * height);
      for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < canvas.width; x += 1) {
          const i = (y * canvas.width + x) * 4;
          const lum = data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114;
          if (lum < 128) {
            raster[y * bytesPerRow + (x >> 3)] |= 0x80 >> (x & 7);
          }
        }
      }
      const header = new Uint8Array(8);
      header.set([
        0x1d,
        0x76,
        0x30,
        0x00,
        bytesPerRow & 0xff,
        (bytesPerRow >> 8) & 0xff,
        height & 0xff,
        (height >> 8) & 0xff,
      ]);
      const out = new Uint8Array(header.length + raster.length);
      out.set(header, 0);
      out.set(raster, header.length);
      return [out];
    } catch (_) {
      return [];
    }
  };

  const concatChunks = (chunks) => {
    let total = 0;
    for (const part of chunks) total += part.length;
    const out = new Uint8Array(total);
    let offset = 0;
    for (const part of chunks) {
      out.set(part, offset);
      offset += part.length;
    }
    return out;
  };

  const encodeEscPos = async (text, qr = null, styleOverride = null) => {
    const style = getReceiptPrintStyle(styleOverride);
    const chunks = [];
    chunks.push(new Uint8Array([0x1b, 0x40])); // init
    // Character size from receipt font setting
    chunks.push(new Uint8Array([0x1d, 0x21, style.escPosMagnification]));
    // Always bold + double-strike on hardware so thermal ink stays readable.
    chunks.push(new Uint8Array([0x1b, 0x45, 0x01]));
    chunks.push(new Uint8Array([0x1b, 0x47, 0x01]));
    chunks.push(new Uint8Array([0x1b, 0x61, 0x00])); // left
    const body = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    chunks.push(textEncoder.encode(`${body}\n`));
    // Reset size/bold before QR
    chunks.push(new Uint8Array([0x1d, 0x21, 0x00]));
    chunks.push(new Uint8Array([0x1b, 0x45, 0x00]));
    chunks.push(new Uint8Array([0x1b, 0x47, 0x00]));
    const payload = qr?.payload || qr?.url || "";
    const imageUrl = qr?.image_data_url || "";
    if (payload || imageUrl) {
      chunks.push(new Uint8Array([0x1b, 0x61, 0x01])); // center
      const raster = await encodeEscPosRasterQr(imageUrl, style.paperMm);
      if (raster.length) {
        chunks.push(...raster);
      } else if (payload) {
        chunks.push(...encodeEscPosQr(payload));
      }
      if (qr?.label) {
        chunks.push(textEncoder.encode(`\n${String(qr.label)}\n`));
      }
      chunks.push(new Uint8Array([0x1b, 0x61, 0x00]));
    } else if (qr?.label) {
      chunks.push(textEncoder.encode(`\n${String(qr.label)}\n`));
    }
    chunks.push(new Uint8Array([0x0a, 0x0a, 0x0a, 0x0a]));
    // Partial cut — full cut can hang some cheap USB bridges waiting for status.
    chunks.push(new Uint8Array([0x1d, 0x56, 0x01]));
    return concatChunks(chunks);
  };

  const bytesToBase64 = (bytes) => {
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  };

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const ticketCss = (style) => `
  @page { size: ${style.paperWidth} auto; margin: 2mm; }
  html, body {
    width: ${style.paperWidth};
    margin: 0;
    padding: 0;
    background: #fff !important;
    color: #000 !important;
  }
  * {
    box-sizing: border-box;
    color: #000 !important;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
  }
  body {
    font-family: "Courier New", ui-monospace, Menlo, Consolas, monospace;
    font-size: ${style.fontSize};
    font-weight: 900 !important;
    line-height: 1.4;
    padding: ${style.paperMm === "58" ? "2.5mm 2mm 3mm" : "3.5mm 3mm 4mm"};
    -webkit-font-smoothing: none;
    -moz-osx-font-smoothing: unset;
    text-rendering: geometricPrecision;
    text-shadow: 0.35px 0 0 #000, -0.35px 0 0 #000, 0 0.35px 0 #000, 0 -0.35px 0 #000;
  }
  .receipt-ticket-inner { width: 100%; font-weight: 900 !important; }
  .receipt-ticket-brand { text-align: center; }
  .receipt-ticket-logo {
    display: block;
    width: auto;
    max-width: 42%;
    max-height: 14mm;
    margin: 0 auto 0.4em;
    object-fit: contain;
  }
  .receipt-ticket-mark {
    margin: 0 0 0.3em;
    font-size: 0.78em;
    font-weight: 900 !important;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }
  .receipt-ticket-brand h4 {
    margin: 0;
    font-size: 1.22em;
    font-weight: 900 !important;
    letter-spacing: 0.02em;
  }
  .receipt-ticket-brand p {
    margin: 0.12em 0 0;
    font-size: 0.98em;
    font-weight: 900 !important;
  }
  .receipt-ticket-branch {
    font-size: 0.92em;
    font-weight: 900 !important;
  }
  .receipt-ticket-rule {
    height: 0;
    margin: 0.65em 0;
    border: 0;
    border-top: 2px solid #000 !important;
  }
  .receipt-ticket-meta { display: grid; gap: 0.28em; }
  .receipt-ticket-meta > div {
    display: grid;
    grid-template-columns: ${style.paperMm === "58" ? "3.6em" : "4.2em"} 1fr;
    gap: 0.35em;
    align-items: baseline;
  }
  .receipt-ticket-meta span {
    font-size: 0.9em;
    font-weight: 900 !important;
  }
  .receipt-ticket-meta strong {
    font-weight: 900 !important;
    word-break: break-word;
  }
  .receipt-ticket-lines { display: grid; gap: 0.45em; }
  .receipt-ticket-line {
    display: grid;
    grid-template-columns: ${
      style.paperMm === "58"
        ? "minmax(0, 1fr) 3.6em 1.6em 3.2em"
        : "minmax(0, 1fr) 4.2em 2em 3.8em"
    };
    gap: 0.28em;
    align-items: baseline;
    font-weight: 900 !important;
  }
  .receipt-ticket-line--qty {
    grid-template-columns: minmax(0, 1fr) ${
      style.paperMm === "58" ? "2.2em" : "2.6em"
    };
  }
  .receipt-ticket-line > span:nth-child(2),
  .receipt-ticket-line > span:nth-child(3),
  .receipt-ticket-line > span:nth-child(4) {
    text-align: left;
    font-variant-numeric: tabular-nums;
    font-weight: 900 !important;
  }
  .receipt-ticket-line--head {
    font-size: 0.88em;
    font-weight: 900 !important;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border-bottom: 1.5px solid #000;
    padding-bottom: 0.15em;
    margin-bottom: 0.1em;
  }
  .receipt-ticket-item { min-width: 0; overflow: hidden; }
  .receipt-ticket-item strong {
    display: block;
    font-weight: 900 !important;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .receipt-ticket-serial {
    display: block;
    margin-top: 0.08em;
    font-style: normal;
    font-size: 0.86em;
    font-weight: 900 !important;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .receipt-ticket-price {
    white-space: nowrap;
    font-size: 0.98em;
    font-weight: 900 !important;
  }
  .receipt-ticket-cancelled {
    margin: 0.3em 0;
    text-align: center;
    font-weight: 900 !important;
    letter-spacing: 0.04em;
  }
  .receipt-ticket-totals {
    display: grid;
    gap: 0.28em;
    font-weight: 900 !important;
  }
  .receipt-ticket-totals > div {
    display: flex;
    justify-content: space-between;
    gap: 0.7em;
    font-weight: 900 !important;
  }
  .receipt-ticket-totals span,
  .receipt-ticket-totals strong {
    font-weight: 900 !important;
  }
  .receipt-ticket-grand {
    margin-top: 0.12em;
    padding-top: 0.35em;
    border-top: 2.5px solid #000 !important;
    font-size: 1.12em;
    font-weight: 900 !important;
  }
  .receipt-ticket-grand strong { font-weight: 900 !important; }
  .receipt-ticket-payment { text-align: center; font-weight: 900 !important; }
  .receipt-ticket-payment-title {
    margin: 0 0 0.3em;
    font-weight: 900 !important;
    font-size: 1em;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .receipt-ticket-payment-lines p {
    margin: 0.08em 0;
    font-weight: 900 !important;
  }
  .receipt-ticket-footer {
    margin: 0;
    text-align: center;
    font-size: 0.95em;
    font-weight: 900 !important;
  }
  .receipt-ticket-qr {
    display: grid;
    justify-items: center;
    gap: 0.3rem;
    margin-top: 0.5rem;
    padding-top: 0.3rem;
  }
  .receipt-ticket-qr img {
    width: ${style.qrSize};
    height: ${style.qrSize};
    image-rendering: pixelated;
    background: #fff;
  }
  .receipt-ticket-qr p {
    margin: 0;
    text-align: center;
    font-size: 0.88em;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    font-weight: 900 !important;
  }
  pre {
    margin: 0;
    font: inherit;
    font-weight: 900 !important;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .qr { margin-top: 8px; text-align: center; white-space: normal; }
  .qr img { width: ${style.qrSize}; height: ${style.qrSize}; image-rendering: pixelated; }
  .qr p {
    margin: 4px 0 0;
    font-size: 0.9em;
    text-transform: uppercase;
    font-weight: 900 !important;
  }
  .qr-text { margin-top: 6px; font-size: 0.95em; font-weight: 900 !important; }
`;

  const renderTicketHtml = (ticket, qr = null) => {
    const t = ticket && typeof ticket === "object" ? ticket : null;
    if (!t || !t.shop_name) return "";

    const metaRow = (label, value) =>
      value
        ? `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
        : "";

    const qtyOnly = Boolean(t.qty_only);
    const linesHtml = t.cancelled
      ? `<p class="receipt-ticket-cancelled">*** CANCELLED / RETURNED ***</p>`
      : (t.lines || [])
          .map((line) => {
            const serials = (line.serials || [])
              .map((s) => `<em class="receipt-ticket-serial">SN ${escapeHtml(s)}</em>`)
              .join("");
            const extra = Number(line.serials_extra || 0);
            const extraHtml =
              extra > 0
                ? `<em class="receipt-ticket-serial">+${extra} more serials</em>`
                : "";
            if (qtyOnly) {
              return `<div class="receipt-ticket-line receipt-ticket-line--qty">
  <span class="receipt-ticket-item">
    <strong>${escapeHtml(line.name || "Item")}</strong>
    ${serials}${extraHtml}
  </span>
  <span>${escapeHtml(line.qty ?? "")}</span>
</div>`;
            }
            return `<div class="receipt-ticket-line">
  <span class="receipt-ticket-item">
    <strong>${escapeHtml(line.name || "Item")}</strong>
    ${serials}${extraHtml}
  </span>
  <span class="receipt-ticket-price">@ ${escapeHtml(line.price || "0")}</span>
  <span>${escapeHtml(line.qty ?? "")}</span>
  <span>${escapeHtml(line.total || "0")}</span>
</div>`;
          })
          .join("");

    const paymentDetails = t.payment_details || {};
    const paymentLines = paymentDetails.lines || [];
    const paymentBlock = paymentLines.length
      ? `<div class="receipt-ticket-payment">
  <div class="receipt-ticket-rule" aria-hidden="true"></div>
  <p class="receipt-ticket-payment-title">M-Pesa ${escapeHtml(
    paymentDetails.label || ""
  )}</p>
  <div class="receipt-ticket-payment-lines">
    ${paymentLines.map((line) => `<p>${escapeHtml(line)}</p>`).join("")}
  </div>
</div>`
      : "";

    const qrReady = Boolean(qr?.ready && qr?.image_data_url);
    const qrBlock = qrReady
      ? `<div class="receipt-ticket-qr">
  <img src="${String(qr.image_data_url).replace(/"/g, "&quot;")}" alt="QR">
  <p>${escapeHtml(qr.label || "Scan QR")}</p>
</div>`
      : "";

    const logo = t.logo_url
      ? `<img class="receipt-ticket-logo" src="${escapeHtml(t.logo_url)}" alt="">`
      : "";

    const taxBlock = t.show_tax
      ? `<div><span>Subtotal</span><strong>KSh ${escapeHtml(
          t.subtotal || "0"
        )}</strong></div>
<div><span>Tax (${escapeHtml(t.tax_percent || "0")}%)</span><strong>KSh ${escapeHtml(
          t.tax_amount || "0"
        )}</strong></div>`
      : "";

    const paidBlock = t.payment
      ? `<div><span>Paid</span><strong>${escapeHtml(t.payment)}</strong></div>`
      : "";

    const partyRow =
      t.client || (t.party_label && !t.route_from)
        ? metaRow(t.party_label || "Client", t.client || "—")
        : "";
    const totalsBlock = qtyOnly
      ? `<div class="receipt-ticket-grand">
      <span>Units</span><strong>${escapeHtml(
        t.total_units ??
          (t.lines || []).reduce((sum, line) => sum + Number(line.qty || 0), 0)
      )}</strong>
    </div>`
      : `${taxBlock}
    <div class="receipt-ticket-grand">
      <span>Total</span><strong>KSh ${escapeHtml(t.total || "0")}</strong>
    </div>
    ${paidBlock}`;
    const linesHead = qtyOnly
      ? `<div class="receipt-ticket-line receipt-ticket-line--head receipt-ticket-line--qty">
      <span>Item</span><span>Qty</span>
    </div>`
      : `<div class="receipt-ticket-line receipt-ticket-line--head">
      <span>Item</span><span>Price</span><span>Qty</span><span>Total</span>
    </div>`;

    return `<div class="receipt-ticket-inner">
  <div class="receipt-ticket-brand">
    ${logo}
    <p class="receipt-ticket-mark">${escapeHtml(t.mark || "MY-SHOP")}</p>
    <h4>${escapeHtml(t.shop_name)}</h4>
    ${t.shop_location ? `<p>${escapeHtml(t.shop_location)}</p>` : ""}
    ${t.shop_phone ? `<p>${escapeHtml(t.shop_phone)}</p>` : ""}
    ${
      t.shop_branch
        ? `<p class="receipt-ticket-branch">${escapeHtml(t.shop_branch)}</p>`
        : ""
    }
  </div>
  <div class="receipt-ticket-rule" aria-hidden="true"></div>
  <div class="receipt-ticket-meta">
    ${metaRow("Receipt", t.receipt_number)}
    ${metaRow("Type", t.kind)}
    ${metaRow("Date", t.date)}
    ${metaRow("From", t.route_from)}
    ${metaRow("To", t.route_to)}
    ${partyRow}
    ${metaRow("Status", t.status)}
    ${metaRow("Cashier", t.cashier)}
  </div>
  <div class="receipt-ticket-rule" aria-hidden="true"></div>
  <div class="receipt-ticket-lines">
    ${linesHead}
    ${linesHtml}
  </div>
  <div class="receipt-ticket-rule" aria-hidden="true"></div>
  <div class="receipt-ticket-totals">
    ${totalsBlock}
  </div>
  ${paymentBlock}
  <div class="receipt-ticket-rule" aria-hidden="true"></div>
  <p class="receipt-ticket-footer">${escapeHtml(
    t.footer || "Thank you for shopping with us"
  )}</p>
  ${qrBlock}
</div>`;
  };

  const browserPrint = (text, qr = null, label = "USB", styleOverride = null, ticket = null) =>
    new Promise((resolve, reject) => {
      try {
        const style = getReceiptPrintStyle(styleOverride);
        const ticketHtml = renderTicketHtml(ticket, qr);
        let bodyHtml = ticketHtml;
        if (!bodyHtml) {
          const qrReady = Boolean(qr?.ready && qr?.image_data_url);
          const qrHtml = qrReady
            ? `<div class="qr"><img src="${String(qr.image_data_url).replace(
                /"/g,
                "&quot;"
              )}" alt="QR"><p>${escapeHtml(qr.label || "Scan QR")}</p></div>`
            : qr?.payload
              ? `<p class="qr-text">QR: ${escapeHtml(qr.payload)}</p>`
              : "";
          bodyHtml = `<pre>${escapeHtml(text || "")}</pre>${qrHtml}`;
        }
        const frame = document.createElement("iframe");
        frame.setAttribute("aria-hidden", "true");
        frame.style.cssText =
          "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
        document.body.appendChild(frame);
        const doc = frame.contentDocument || frame.contentWindow?.document;
        if (!doc) {
          frame.remove();
          reject(new Error("Could not open the Windows print dialog."));
          return;
        }
        const cleanup = () => {
          window.setTimeout(() => frame.remove(), 1500);
        };
        const win = frame.contentWindow;
        doc.open();
        doc.write(`<!doctype html><html><head><title>Receipt · ${escapeHtml(label)}</title>
<style>${ticketCss(style)}</style></head><body>
${bodyHtml}
</body></html>`);
        doc.close();
        const runPrint = () => {
          try {
            win.focus();
            win.print();
            cleanup();
            resolve(true);
          } catch (err) {
            cleanup();
            reject(err);
          }
        };
        if (doc.readyState === "complete") runPrint();
        else win.onload = runPrint;
      } catch (err) {
        reject(err);
      }
    });

  const getCsrfToken = () =>
    document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const Session = () => window.RichcomPrinterSession || null;

  const loadStore = () => {
    try {
      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") || {};
      return Session()?.normalizeStore?.(raw) || raw;
    } catch (_) {
      return {};
    }
  };

  const saveStore = (store) => {
    try {
      const normalized = Session()?.normalizeStore?.(store) || store;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
    } catch (_) {
      /* ignore */
    }
  };

  let pageUnloading = false;
  const markPageUnloading = () => {
    pageUnloading = true;
  };
  window.addEventListener("pagehide", markPageUnloading);
  window.addEventListener("beforeunload", markPageUnloading);

  const createBluetoothDriver = () => {
    let device = null;
    let server = null;
    let characteristic = null;
    let scanned = null;
    let explicitDisconnect = false;
    let reconnectTimer = null;

    const findWritableCharacteristic = async (gattServer) => {
      for (const serviceUuid of BT_SERVICES) {
        try {
          const service = await gattServer.getPrimaryService(serviceUuid);
          const chars = await service.getCharacteristics();
          for (const ch of chars) {
            if (ch.properties.write || ch.properties.writeWithoutResponse) {
              return ch;
            }
          }
        } catch (_) {
          /* try next service */
        }
      }
      const services = await gattServer.getPrimaryServices();
      for (const service of services) {
        const chars = await service.getCharacteristics();
        for (const ch of chars) {
          if (ch.properties.write || ch.properties.writeWithoutResponse) {
            return ch;
          }
        }
      }
      return null;
    };

    return {
      channel: "bluetooth",
      async isSupported() {
        return Boolean(navigator.bluetooth?.requestDevice);
      },
      async scan() {
        if (!(await this.isSupported())) {
          throw new Error("Web Bluetooth is not supported in this browser.");
        }
        scanned = await navigator.bluetooth.requestDevice({
          acceptAllDevices: true,
          optionalServices: BT_SERVICES,
        });
        return [
          {
            id: scanned.id,
            name: scanned.name || "Bluetooth printer",
            device: scanned,
          },
        ];
      },
      async connect(selected) {
        const target = selected?.device || scanned;
        if (!target) {
          throw new Error("Scan for a Bluetooth printer first.");
        }
        if (!target.gatt) {
          throw new Error("Selected device does not support GATT.");
        }
        explicitDisconnect = false;
        const onGone = () => {
          if (explicitDisconnect || pageUnloading) return;
          device = null;
          server = null;
          characteristic = null;
          window.RichcomPrinter?.notifyTransportLost?.("bluetooth");
        };
        target.removeEventListener("gattserverdisconnected", onGone);
        target.addEventListener("gattserverdisconnected", onGone);
        server = await target.gatt.connect();
        characteristic = await findWritableCharacteristic(server);
        if (!characteristic) {
          try {
            target.gatt.disconnect();
          } catch (_) {
            /* ignore */
          }
          throw new Error("Connected, but no writable printer characteristic was found.");
        }
        device = target;
        scanned = target;
        const store = loadStore();
        const next = Session()?.markConnected
          ? Session().markConnected(store, "bluetooth", target.name || "Bluetooth printer", {
              bluetooth: { deviceId: target.id, name: target.name || "Bluetooth printer" },
            })
          : {
              ...store,
              activeChannel: "bluetooth",
              wantConnected: true,
              bluetooth: { deviceId: target.id, name: target.name || "Bluetooth printer" },
            };
        saveStore(next);
        return { name: target.name || "Bluetooth printer", id: target.id };
      },
      async disconnect({ explicit = true } = {}) {
        explicitDisconnect = Boolean(explicit);
        if (reconnectTimer) {
          window.clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        try {
          if (device?.gatt?.connected) device.gatt.disconnect();
        } catch (_) {
          /* ignore */
        }
        device = null;
        server = null;
        characteristic = null;
      },
      async restore(saved) {
        const deviceId = saved?.deviceId || loadStore().bluetooth?.deviceId;
        const savedName = saved?.name || loadStore().bluetooth?.name || "Bluetooth printer";
        if (!deviceId) {
          return { ok: false, deferred: true, reason: "missing_device", name: savedName };
        }
        if (!navigator.bluetooth?.getDevices) {
          return { ok: false, deferred: true, reason: "unsupported", name: savedName };
        }
        let devices = [];
        try {
          devices = await navigator.bluetooth.getDevices();
        } catch (_) {
          return { ok: false, deferred: true, reason: "get_devices_failed", name: savedName };
        }
        const target = devices.find((row) => row.id === deviceId) || null;
        if (!target) {
          return { ok: false, deferred: true, reason: "not_permitted", name: savedName };
        }
        try {
          const info = await this.connect({ device: target, id: target.id, name: target.name });
          return { ok: true, info };
        } catch (err) {
          return {
            ok: false,
            deferred: true,
            reason: "connect_failed",
            name: savedName,
            error: err?.message || String(err),
          };
        }
      },
      isConnected() {
        return Boolean(device?.gatt?.connected && characteristic);
      },
      getName() {
        return device?.name || loadStore().bluetooth?.name || "";
      },
      async print(bytes) {
        if (!this.isConnected()) {
          throw new Error("Bluetooth printer is not connected.");
        }
        const chunkSize = 100;
        for (let i = 0; i < bytes.length; i += chunkSize) {
          const slice = bytes.slice(i, i + chunkSize);
          const writePromise = characteristic.properties.writeWithoutResponse
            ? characteristic.writeValueWithoutResponse(slice)
            : characteristic.writeValue(slice);
          await withTimeout(
            writePromise,
            5000,
            "Bluetooth write timed out. Disconnect and reconnect the printer."
          );
        }
      },
    };
  };

  const createUsbDriver = () => {
    let port = null;
    let usbDevice = null;
    let endpointOut = null;
    let mode = ""; // "system" | "serial" | "usb"
    let printMode = "system"; // "system" | "raw"
    let scannedPort = null;
    let scannedUsb = null;
    let label = "";
    let baudRate = 9600;
    let windowsPrinterName = "";

    const serialOpenOptions = (rate) => ({
      baudRate: rate,
      dataBits: 8,
      stopBits: 1,
      parity: "none",
      bufferSize: 8192,
      flowControl: "none",
    });

    const readCardOptions = (card) => {
      const modeValue =
        card?.querySelector("[data-printer-usb-mode]")?.value || "system";
      const baudValue = Number(
        card?.querySelector("[data-printer-usb-baud]")?.value || 9600
      );
      printMode = modeValue === "raw" ? "raw" : "system";
      baudRate =
        Number.isFinite(baudValue) && baudValue > 0 ? baudValue : 9600;
      return { printMode, baudRate };
    };

    const forceClosePort = async (target) => {
      if (!target) return;
      try {
        if (target.readable) {
          const reader = target.readable.getReader();
          try {
            await reader.cancel();
          } catch (_) {
            /* ignore */
          } finally {
            try {
              reader.releaseLock();
            } catch (_) {
              /* ignore */
            }
          }
        }
      } catch (_) {
        /* ignore */
      }
      try {
        if (target.writable) {
          const writer = target.writable.getWriter();
          try {
            await writer.close();
          } catch (_) {
            try {
              writer.releaseLock();
            } catch (_) {
              /* ignore */
            }
          }
        }
      } catch (_) {
        /* ignore */
      }
      try {
        await target.close();
      } catch (_) {
        /* ignore */
      }
    };

    const serialOpenErrorMessage = (err) => {
      const raw = String(err?.message || err || "");
      if (/Failed to open serial port|NetworkError|InvalidStateError|open/i.test(raw)) {
        return (
          "Could not open the COM port — Windows (or another app) likely has it locked. " +
          'Switch USB mode to "Windows printer (shows in Print Queue)", click Connect, then Test print. ' +
          "Raw COM only works if no Windows driver is using that port."
        );
      }
      return raw || "Could not open the USB serial port.";
    };

    const releaseSerial = async () => {
      await forceClosePort(port);
      port = null;
    };

    const releaseUsb = async () => {
      try {
        if (usbDevice?.opened) await usbDevice.close();
      } catch (_) {
        /* ignore */
      }
      usbDevice = null;
      endpointOut = null;
    };

    const writeSerial = async (bytes) => {
      if (!port?.writable) {
        throw new Error("USB serial port is not writable.");
      }
      const writer = port.writable.getWriter();
      try {
        const chunkSize = 512;
        for (let i = 0; i < bytes.length; i += chunkSize) {
          const slice = bytes.slice(i, i + chunkSize);
          await withTimeout(
            writer.write(slice),
            6000,
            "USB write timed out. Check the cable/baud rate, then reconnect."
          );
        }
        await withTimeout(
          writer.ready,
          4000,
          "USB printer buffer did not drain. Try Disconnect, then Connect again."
        );
      } finally {
        try {
          writer.releaseLock();
        } catch (_) {
          /* ignore */
        }
      }
    };

    const openSerialPort = async (target, rates) => {
      await forceClosePort(target);
      // Brief pause so Windows releases the handle after close.
      await new Promise((r) => window.setTimeout(r, 200));
      let lastError = null;
      for (const rate of rates) {
        try {
          await forceClosePort(target);
          await target.open(serialOpenOptions(rate));
          return rate;
        } catch (err) {
          lastError = err;
          await forceClosePort(target);
          await new Promise((r) => window.setTimeout(r, 150));
        }
      }
      throw new Error(serialOpenErrorMessage(lastError));
    };

    return {
      channel: "usb",
      usesBrowserPrint() {
        return mode === "system";
      },
      async isSupported() {
        return true;
      },
      async scan(card) {
        readCardOptions(card);
        if (printMode === "system") {
          return [
            {
              id: "usb-windows",
              name: "Windows USB printer",
              kind: "system",
            },
          ];
        }
        if (!navigator.serial?.requestPort) {
          if (navigator.usb?.requestDevice) {
            scannedUsb = await navigator.usb.requestDevice({
              filters: [{ classCode: 7 }, {}],
            });
            return [
              {
                id: `usb-${scannedUsb.vendorId}-${scannedUsb.productId}`,
                name: scannedUsb.productName || "USB printer",
                kind: "usb",
                device: scannedUsb,
              },
            ];
          }
          throw new Error(
            "Raw COM needs Chrome/Edge Web Serial. Prefer Windows printer mode for driver-installed USB printers."
          );
        }
        try {
          scannedPort = await navigator.serial.requestPort();
        } catch (err) {
          if (err?.name === "NotFoundError") {
            throw new Error("No serial port selected.");
          }
          throw new Error(serialOpenErrorMessage(err));
        }
        const info = scannedPort.getInfo?.() || {};
        return [
          {
            id: `serial-${info.usbVendorId || "port"}-${info.usbProductId || "0"}`,
            name: "USB serial printer",
            kind: "serial",
            port: scannedPort,
          },
        ];
      },
      async connect(selected, card) {
        readCardOptions(card);
        await this.disconnect({ explicit: false });

        if (printMode === "system" || selected?.kind === "system" || selected?.kind === "usb_windows") {
          mode = "system";
          windowsPrinterName = String(
            selected?.windowsName || selected?.name || "Windows USB printer"
          ).replace(/^USB\s*·\s*/i, "");
          label = windowsPrinterName || "Windows USB printer";
          const store = loadStore();
          const next = Session()?.markConnected
            ? Session().markConnected(store, "usb", label, {
                usb: { printMode: "system", windowsPrinterName: label },
              })
            : { ...store, usb: { printMode: "system", windowsPrinterName: label } };
          saveStore(next);
          return { name: label, id: selected?.id || "usb-windows" };
        }

        let kind = selected?.kind || "";
        let target = selected?.port || scannedPort || null;

        if (!kind && !target && navigator.serial?.requestPort) {
          // Connect without Scan: prompt for a port now.
          try {
            target = await navigator.serial.requestPort();
            scannedPort = target;
            kind = "serial";
          } catch (err) {
            if (err?.name === "NotFoundError") {
              throw new Error("No serial port selected.");
            }
            throw new Error(serialOpenErrorMessage(err));
          }
        }

        if (!kind) {
          kind = target ? "serial" : scannedUsb || selected?.device ? "usb" : "";
        }

        if (kind === "serial" || target) {
          target = target || selected?.port || scannedPort;
          if (!target) {
            throw new Error("Scan for a USB COM port first, or use Windows printer mode.");
          }
          const preferred = [baudRate, 9600, 115200, 57600, 38400].filter(
            (v, i, arr) => arr.indexOf(v) === i
          );
          try {
            baudRate = await openSerialPort(target, preferred);
          } catch (err) {
            // Most USB receipt printers with Windows drivers cannot be opened via Web Serial.
            throw new Error(serialOpenErrorMessage(err));
          }
          port = target;
          scannedPort = target;
          mode = "serial";
          label = `USB serial printer (${baudRate})`;
          const info = target.getInfo?.() || {};
          const store = loadStore();
          const usbMeta = {
            printMode: "raw",
            baudRate,
            vendorId: info.usbVendorId,
            productId: info.usbProductId,
          };
          const next = Session()?.markConnected
            ? Session().markConnected(store, "usb", label, { usb: usbMeta })
            : { ...store, usb: usbMeta };
          saveStore(next);
          return { name: label, id: selected?.id || "serial" };
        }

        const usbTarget = selected?.device || scannedUsb;
        if (!usbTarget) {
          throw new Error(
            "No USB device selected. Use Windows printer mode, or Scan a raw COM port first."
          );
        }
        try {
          await usbTarget.open();
        } catch (err) {
          throw new Error(
            "Could not open the USB device. Switch to Windows printer mode if this printer has a Windows driver."
          );
        }
        if (!usbTarget.configuration) {
          await usbTarget.selectConfiguration(1);
        }
        const iface = [...(usbTarget.configuration?.interfaces || [])].find((item) =>
          item.alternates.some((alt) =>
            alt.endpoints.some((ep) => ep.direction === "out")
          )
        );
        if (!iface) {
          await usbTarget.close();
          throw new Error("No USB OUT endpoint found on this device.");
        }
        const alt =
          iface.alternates.find((a) =>
            a.endpoints.some((ep) => ep.direction === "out")
          ) || iface.alternates[0];
        await usbTarget.claimInterface(iface.interfaceNumber);
        if (alt.alternateSetting != null) {
          try {
            await usbTarget.selectAlternateInterface(
              iface.interfaceNumber,
              alt.alternateSetting
            );
          } catch (_) {
            /* ignore */
          }
        }
        endpointOut = alt.endpoints.find((ep) => ep.direction === "out");
        if (!endpointOut) {
          await usbTarget.close();
          throw new Error("No USB OUT endpoint found on this device.");
        }
        usbDevice = usbTarget;
        mode = "usb";
        label = usbTarget.productName || selected?.name || "USB printer";
        const store = loadStore();
        const usbMeta = {
          printMode: "raw",
          baudRate,
          vendorId: usbTarget.vendorId,
          productId: usbTarget.productId,
        };
        const next = Session()?.markConnected
          ? Session().markConnected(store, "usb", label, { usb: usbMeta })
          : { ...store, usb: usbMeta };
        saveStore(next);
        return { name: label, id: selected?.id || `usb-${usbTarget.vendorId}` };
      },
      async disconnect({ explicit = true } = {}) {
        await releaseSerial();
        await releaseUsb();
        if (explicit && scannedPort) {
          await forceClosePort(scannedPort);
        }
        mode = "";
        label = "";
        windowsPrinterName = "";
      },
      async restore(saved) {
        const usb = saved || loadStore().usb || {};
        const printMode = usb.printMode === "raw" ? "raw" : "system";
        if (printMode === "system") {
          mode = "system";
          windowsPrinterName = String(
            usb.windowsPrinterName || loadStore().name || "Windows USB printer"
          );
          label = windowsPrinterName;
          return { ok: true, info: { name: label, id: "usb-windows" } };
        }
        baudRate = Number(usb.baudRate || 9600) || 9600;
        if (navigator.serial?.getPorts) {
          try {
            const ports = await navigator.serial.getPorts();
            let target = null;
            if (usb.vendorId != null) {
              target =
                ports.find((p) => {
                  const info = p.getInfo?.() || {};
                  return (
                    Number(info.usbVendorId) === Number(usb.vendorId) &&
                    (usb.productId == null ||
                      Number(info.usbProductId) === Number(usb.productId))
                  );
                }) || null;
            }
            if (!target && ports.length === 1) target = ports[0];
            if (!target && ports.length > 1) target = ports[0];
            if (target) {
              const preferred = [baudRate, 9600, 115200, 57600, 38400].filter(
                (v, i, arr) => arr.indexOf(v) === i
              );
              baudRate = await openSerialPort(target, preferred);
              port = target;
              scannedPort = target;
              mode = "serial";
              label = `USB serial printer (${baudRate})`;
              return { ok: true, info: { name: label, id: "serial" } };
            }
          } catch (err) {
            return {
              ok: false,
              deferred: true,
              reason: "serial_restore_failed",
              name: loadStore().name || "USB serial printer",
              error: err?.message || String(err),
            };
          }
        }
        return {
          ok: false,
          deferred: true,
          reason: "needs_user_gesture",
          name: loadStore().name || "USB serial printer",
        };
      },
      isConnected() {
        if (mode === "system") return true;
        if (mode === "serial") return Boolean(port);
        return Boolean(usbDevice && endpointOut);
      },
      getName() {
        return label || loadStore().usb?.windowsPrinterName || "";
      },
      async print(bytes) {
        if (!this.isConnected()) {
          throw new Error("USB printer is not connected.");
        }
        if (mode === "system") {
          throw new Error("SYSTEM_PRINT");
        }
        if (mode === "serial") {
          await writeSerial(bytes);
          return;
        }
        const chunkSize = endpointOut.packetSize || 64;
        for (let i = 0; i < bytes.length; i += chunkSize) {
          const slice = bytes.slice(i, i + chunkSize);
          await withTimeout(
            usbDevice.transferOut(endpointOut.endpointNumber, slice),
            6000,
            "USB transfer timed out."
          );
        }
      },
    };
  };

  const createWifiDriver = () => {
    let host = "";
    let port = 9100;
    let connected = false;
    let relayUrl = "";
    let scanUrl = "";

    return {
      channel: "wifi",
      setRelayUrl(url) {
        relayUrl = url || "";
      },
      setScanUrl(url) {
        scanUrl = url || "";
      },
      async isSupported() {
        return true;
      },
      async scan(card) {
        const inputHost = card?.querySelector("[data-printer-wifi-host]");
        const inputPort = card?.querySelector("[data-printer-wifi-port]");
        if (!scanUrl) {
          throw new Error("Printer scan URL is missing. Refresh the page.");
        }

        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), 20000);
        let response;
        try {
          response = await fetch(scanUrl, {
            method: "POST",
            headers: {
              Accept: "application/json",
              "Content-Type": "application/json",
              "X-CSRFToken": getCsrfToken(),
            },
            credentials: "same-origin",
            signal: controller.signal,
            body: JSON.stringify({ thorough: false }),
          });
        } catch (err) {
          if (err?.name === "AbortError") {
            throw new Error(
              "Scan timed out. Keep the printer powered on the same Wi‑Fi as this PC."
            );
          }
          throw new Error("Network error while scanning for Wi‑Fi printers.");
        } finally {
          window.clearTimeout(timer);
        }

        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          throw new Error(data.error || "Wi‑Fi printer scan failed.");
        }

        const printers = Array.isArray(data.printers) ? data.printers : [];

        if (!printers.length) {
          const hint = String(data.hint || "").trim();
          // Do not list USB printers under Wi‑Fi — keep channels separate.
          throw new Error(
            hint.replace(/\s*USB POS printers were found — connect one below \(Windows print\)\./i, "").trim() ||
              "No Wi‑Fi printers found on this network. Keep the Wi‑Fi printer powered on and on the same Wi‑Fi, then scan again. For POS-80C / USB printers, use the USB channel."
          );
        }

        return printers.map((row) => {
          const nextHost = String(row.host || "").trim();
          const nextPort = Number(row.port || 9100);
          return {
            id: row.id || `wifi-${nextHost}:${nextPort}`,
            name: row.detail
              ? `${row.name || nextHost} · ${row.detail}`
              : row.name || `Printer ${nextHost}:${nextPort}`,
            host: nextHost,
            port: Number.isFinite(nextPort) && nextPort > 0 ? nextPort : 9100,
            kind: "printer",
            onSelect: () => {
              if (inputHost) inputHost.value = nextHost;
              if (inputPort) inputPort.value = String(nextPort || 9100);
            },
          };
        });
      },
      async connect(selected, card) {
        if (selected?.host) host = selected.host;
        if (selected?.port) port = selected.port;
        if (card) {
          const inputHost = card.querySelector("[data-printer-wifi-host]");
          const inputPort = card.querySelector("[data-printer-wifi-port]");
          if (selected?.host && inputHost) inputHost.value = selected.host;
          if (selected?.port && inputPort) inputPort.value = String(selected.port);
          host = (inputHost?.value || host || "").trim();
          port = Number(inputPort?.value || port || 9100);
        }
        if (!host) {
          throw new Error("Scan for a printer or enter the printer IP / host before connecting.");
        }
        if (!Number.isFinite(port) || port < 1 || port > 65535) {
          throw new Error("Enter a valid printer port (default 9100).");
        }
        if (!relayUrl) {
          throw new Error("Print relay URL is missing. Refresh the page.");
        }
        const probe = new Uint8Array([0x1b, 0x40]);
        const response = await fetch(relayUrl, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken(),
          },
          credentials: "same-origin",
          body: JSON.stringify({
            host,
            port,
            data: bytesToBase64(probe),
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          throw new Error(data.error || `Could not reach printer at ${host}:${port}.`);
        }
        connected = true;
        const store = loadStore();
        const next = Session()?.markConnected
          ? Session().markConnected(store, "wifi", `Wi‑Fi printer ${host}:${port}`, {
              wifi: { host, port },
            })
          : { ...store, wifi: { host, port } };
        saveStore(next);
        return { name: `Wi‑Fi printer ${host}:${port}`, id: `wifi-${host}:${port}` };
      },
      async disconnect({ explicit = true } = {}) {
        connected = false;
        if (explicit) {
          // Keep host/port in form store for convenience; session intent cleared upstream.
        }
      },
      async restore(saved) {
        const wifi = saved || loadStore().wifi || {};
        const nextHost = String(wifi.host || "").trim();
        const nextPort = Number(wifi.port || 9100);
        if (!nextHost) {
          return { ok: false, deferred: true, reason: "missing_host" };
        }
        host = nextHost;
        port = Number.isFinite(nextPort) && nextPort > 0 ? nextPort : 9100;
        if (!relayUrl) {
          const modal = document.querySelector("[data-printer-modal]");
          relayUrl = modal?.dataset.printRelayUrl || "";
        }
        if (!relayUrl) {
          return {
            ok: false,
            deferred: true,
            reason: "missing_relay",
            name: `Wi‑Fi printer ${host}:${port}`,
          };
        }
        try {
          const probe = new Uint8Array([0x1b, 0x40]);
          const response = await fetch(relayUrl, {
            method: "POST",
            headers: {
              Accept: "application/json",
              "Content-Type": "application/json",
              "X-CSRFToken": getCsrfToken(),
            },
            credentials: "same-origin",
            body: JSON.stringify({
              host,
              port,
              data: bytesToBase64(probe),
            }),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok || !data.ok) {
            connected = false;
            return {
              ok: false,
              deferred: true,
              reason: "unreachable",
              name: `Wi‑Fi printer ${host}:${port}`,
              error: data.error || `Could not reach printer at ${host}:${port}.`,
            };
          }
          connected = true;
          return { ok: true, info: { name: `Wi‑Fi printer ${host}:${port}`, id: `wifi-${host}:${port}` } };
        } catch (err) {
          connected = false;
          return {
            ok: false,
            deferred: true,
            reason: "network",
            name: `Wi‑Fi printer ${host}:${port}`,
            error: err?.message || String(err),
          };
        }
      },
      isConnected() {
        return connected && Boolean(host);
      },
      getName() {
        if (this.isConnected()) return `Wi‑Fi printer ${host}:${port}`;
        const saved = loadStore().wifi;
        return saved?.host ? `Wi‑Fi printer ${saved.host}:${saved.port || 9100}` : "";
      },
      getConfig() {
        return { host, port };
      },
      async print(bytes) {
        if (!this.isConnected()) {
          throw new Error("Wi‑Fi printer is not connected.");
        }
        const response = await fetch(relayUrl, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken(),
          },
          credentials: "same-origin",
          body: JSON.stringify({
            host,
            port,
            data: bytesToBase64(bytes),
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          throw new Error(data.error || "Wi‑Fi print relay failed.");
        }
      },
    };
  };

  const drivers = {};
  const CHANNELS = ["bluetooth", "usb", "wifi"];
  for (const channel of CHANNELS) {
    if (channel === "bluetooth") drivers[channel] = createBluetoothDriver();
    else if (channel === "usb") drivers[channel] = createUsbDriver();
    else drivers[channel] = createWifiDriver();
  }

  const connection = {
    activeChannel: "",
    name: "",
    restoring: false,
    lastRestoreError: "",
  };

  const listeners = new Set();
  let reconnectTimer = null;
  let restoreAttempt = 0;

  const emit = () => {
    const snapshot = window.RichcomPrinter.getStatus();
    listeners.forEach((fn) => {
      try {
        fn(snapshot);
      } catch (_) {
        /* ignore */
      }
    });
  };

  const setConnected = (channel, name) => {
    connection.activeChannel = channel;
    connection.name = name || CHANNEL_META[channel]?.label || channel;
    connection.restoring = false;
    connection.lastRestoreError = "";
    const store = loadStore();
    const next = Session()?.markConnected
      ? Session().markConnected(store, channel, connection.name)
      : { ...store, activeChannel: channel, name: connection.name, wantConnected: true };
    saveStore(next);
    emit();
  };

  const clearConnected = ({ explicit = false, channel = "" } = {}) => {
    if (
      channel &&
      connection.activeChannel &&
      connection.activeChannel !== channel &&
      !explicit
    ) {
      return;
    }
    if (explicit) {
      const store = loadStore();
      const next = Session()?.markExplicitDisconnect
        ? Session().markExplicitDisconnect(store)
        : { ...store, wantConnected: false };
      delete next.activeChannel;
      delete next.name;
      delete next.transportLostAt;
      saveStore(next);
      connection.activeChannel = "";
      connection.name = "";
      connection.restoring = false;
      connection.lastRestoreError = "";
      emit();
      return;
    }
    // Non-explicit: keep persisted intent; only clear live in-memory if matching.
    if (!channel || connection.activeChannel === channel) {
      // Keep connection.activeChannel so UI can show "Reconnecting…"
      connection.restoring = true;
      const store = loadStore();
      const next = Session()?.markTransportLost
        ? Session().markTransportLost(store, channel || connection.activeChannel)
        : store;
      saveStore(next);
      emit();
    }
  };

  const scheduleReconnect = (reason = "transport") => {
    if (pageUnloading) return;
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    const delay = Math.min(15000, 700 + restoreAttempt * 900);
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      window.RichcomPrinter?.restoreSession?.({ reason, silent: true });
    }, delay);
  };

  window.RichcomPrinter = {
    encodeEscPos,
    browserPrint,
    renderTicketHtml,
    onChange(fn) {
      if (typeof fn === "function") listeners.add(fn);
      return () => listeners.delete(fn);
    },
    notifyDisconnected(channel) {
      // Back-compat: treat as transport loss unless page is unloading.
      this.notifyTransportLost(channel);
    },
    notifyTransportLost(channel) {
      if (pageUnloading) return;
      clearConnected({ explicit: false, channel });
      scheduleReconnect("transport_lost");
    },
    getStatus() {
      const store = loadStore();
      const channel = connection.activeChannel || store.activeChannel || "";
      const driver = channel ? drivers[channel] : null;
      const connected = Boolean(driver?.isConnected?.());
      const wantConnected = store.wantConnected !== false && Boolean(store.activeChannel);
      return {
        connected,
        channel: connected ? channel : "",
        preferredChannel: store.activeChannel || "",
        name: connected
          ? driver.getName() || connection.name || store.name || ""
          : wantConnected
            ? store.name || connection.name || ""
            : "",
        usesBrowserPrint: Boolean(driver?.usesBrowserPrint?.()),
        wantConnected,
        restoring: Boolean(connection.restoring) && wantConnected && !connected,
        lastRestoreError: connection.lastRestoreError || "",
        channels: CHANNELS.slice(),
      };
    },
    getDriver(channel) {
      return drivers[channel] || null;
    },
    async scan(channel, card) {
      const driver = drivers[channel];
      if (!driver) throw new Error("Unknown print channel.");
      return driver.scan(card);
    },
    async connect(channel, selected, card) {
      const driver = drivers[channel];
      if (!driver) throw new Error("Unknown print channel.");
      if (channel === "wifi") {
        const modal = document.querySelector("[data-printer-modal]");
        driver.setRelayUrl(modal?.dataset.printRelayUrl || "");
      }
      const info = await driver.connect(selected, card);
      for (const other of CHANNELS) {
        if (other === channel) continue;
        try {
          await drivers[other].disconnect({ explicit: false });
        } catch (_) {
          /* ignore */
        }
      }
      setConnected(channel, info?.name || CHANNEL_META[channel].label);
      return info;
    },
    async disconnect(channel) {
      const store = loadStore();
      const target = channel || connection.activeChannel || store.activeChannel;
      if (!target) return;
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      await drivers[target]?.disconnect?.({ explicit: true });
      clearConnected({ explicit: true, channel: target });
    },
    async restoreSession({ reason = "boot", silent = false, enabledChannels = null } = {}) {
      if (pageUnloading) return { ok: false, skipped: true };
      const store = loadStore();
      const enabled =
        enabledChannels ||
        String(document.querySelector("[data-printer-modal]")?.dataset.printChannels || "")
          .split(",")
          .map((v) => v.trim())
          .filter(Boolean);
      const plan = Session()?.restorePlan
        ? Session().restorePlan(store, enabled.length ? enabled : CHANNELS)
        : null;
      const channel = plan?.channel || store.activeChannel || "";
      if (!channel || (plan && plan.action === "none")) {
        connection.restoring = false;
        return { ok: false, skipped: true };
      }
      if (drivers[channel]?.isConnected?.()) {
        setConnected(channel, drivers[channel].getName() || store.name || channel);
        return { ok: true, channel, restored: true, already: true };
      }

      connection.restoring = true;
      connection.activeChannel = channel;
      connection.name = store.name || CHANNEL_META[channel]?.label || channel;
      emit();

      const modal = document.querySelector("[data-printer-modal]");
      if (channel === "wifi") {
        drivers.wifi.setRelayUrl(modal?.dataset.printRelayUrl || "");
        drivers.wifi.setScanUrl(modal?.dataset.printScanUrl || "");
      }

      let result = { ok: false };
      try {
        if (plan?.action === "restore_local" || plan?.action === "reconnect" || !plan) {
          const target =
            plan?.target ||
            (channel === "wifi"
              ? store.wifi
              : channel === "usb"
                ? store.usb
                : store.bluetooth);
          result = (await drivers[channel].restore?.(target)) || { ok: false };
        } else if (plan?.action === "needs_setup") {
          result = { ok: false, deferred: true, reason: "needs_setup" };
        }
      } catch (err) {
        result = { ok: false, deferred: true, error: err?.message || String(err) };
      }

      if (result.ok) {
        restoreAttempt = 0;
        setConnected(channel, result.info?.name || store.name || channel);
        return { ok: true, channel, restored: true, reason };
      }

      connection.restoring = Boolean(result.deferred);
      connection.lastRestoreError = result.error || result.reason || "";
      emit();
      restoreAttempt += 1;
      if (result.deferred && restoreAttempt < 8) {
        scheduleReconnect(silent ? reason : "retry");
      }
      return { ok: false, channel, deferred: Boolean(result.deferred), result };
    },
    async ensureConnected(channel) {
      const status = this.getStatus();
      const target = channel || status.preferredChannel || status.channel;
      if (status.connected && (!channel || status.channel === channel)) {
        return true;
      }
      if (!status.wantConnected) return false;
      const restored = await this.restoreSession({ reason: "ensure", silent: true });
      return Boolean(restored?.ok) && this.getStatus().connected;
    },
    async print(channel, text, qr = null, styleOverride = null, ticket = null) {
      const store = loadStore();
      const target = channel || connection.activeChannel || store.activeChannel;
      let driver = drivers[target];
      if (!driver || !driver.isConnected()) {
        const ok = await this.ensureConnected(target);
        driver = drivers[target];
        if (!ok || !driver?.isConnected()) {
          throw new Error("No printer connected for this channel.");
        }
      }
      if (driver.usesBrowserPrint?.()) {
        await browserPrint(
          text,
          qr,
          CHANNEL_META[target]?.label || target.toUpperCase(),
          styleOverride,
          ticket
        );
        return true;
      }
      const bytes = await encodeEscPos(text, qr, styleOverride);
      await withTimeout(
        driver.print(bytes),
        20000,
        "Print timed out. Disconnect, reconnect, then try Test print again."
      );
      return true;
    },
    canAutoPrint(channel) {
      const status = this.getStatus();
      if (!status.connected) return false;
      if (channel && status.channel !== channel) return false;
      return true;
    },
    /**
     * Print a receipt payload (sale / supplier / expense).
     * Tries the connected thermal channel, then browser print.
     */
    async printReceipt({
      text = "",
      channel = "",
      qr = null,
      fontStyle = null,
      ticket = null,
      paperWidth = "",
    } = {}) {
      const styleOverride =
        fontStyle && typeof fontStyle === "object"
          ? {
              ...fontStyle,
              ...(paperWidth === "58" || paperWidth === "80"
                ? { paper_width: paperWidth }
                : {}),
            }
          : paperWidth === "58" || paperWidth === "80"
            ? { paper_width: paperWidth }
            : null;
      const qrPayload = {
        payload: qr?.payload || qr?.url || "",
        label: qr?.label || "",
        ready: Boolean(qr?.ready),
        image_data_url: qr?.image_data_url || "",
      };
      const status = this.getStatus();
      const targetChannel =
        (status.connected && status.channel) || channel || status.preferredChannel || "";

      if (text && this.canAutoPrint(targetChannel)) {
        try {
          await this.print(targetChannel, text, qrPayload, styleOverride, ticket);
          return { ok: true, via: targetChannel || "printer" };
        } catch (_) {
          /* fall through to browser print */
        }
      }

      if (typeof browserPrint === "function" && text) {
        try {
          await browserPrint(
            text,
            qrPayload,
            "Receipt",
            styleOverride,
            ticket
          );
          return { ok: true, via: "browser" };
        } catch (_) {
          /* fall through */
        }
      }
      return { ok: false, via: "" };
    },
    async printTestPage(channel) {
      const status = this.getStatus();
      const target = channel || status.channel || status.preferredChannel;
      if (!target) {
        throw new Error("Connect a printer before running a test print.");
      }
      await this.ensureConnected(target);
      if (!this.getStatus().connected) {
        throw new Error("Connect a printer before running a test print.");
      }
      const live = this.getStatus();
      const when = new Date().toLocaleString();
      const name = live.name || CHANNEL_META[target]?.label || target;
      const modeNote = live.usesBrowserPrint
        ? "Mode    : Windows print queue"
        : "Mode    : Raw device (no Windows queue)";
      const text = [
        "PRINTER TEST PAGE",
        "========================",
        `Channel : ${CHANNEL_META[target]?.label || target}`,
        `Printer : ${name}`,
        modeNote,
        `Time    : ${when}`,
        "------------------------",
        "If you can read this,",
        "the printer is connected",
        "and ready for receipts.",
        "========================",
      ].join("\n");
      await this.print(target, text);
      return true;
    },
  };

  const bindUi = () => {
    const modal = document.querySelector("[data-printer-modal]");
    if (!modal) return;

    const enabled = String(modal.dataset.printChannels || "")
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);

    drivers.wifi.setRelayUrl(modal.dataset.printRelayUrl || "");
    drivers.wifi.setScanUrl(modal.dataset.printScanUrl || "");

    const badgeState = (badge) => {
      const key = String(badge || "idle").toLowerCase();
      if (key === "connected") return "connected";
      if (key === "error") return "error";
      if (key === "scanning") return "scanning";
      if (key === "connecting") return "connecting";
      if (key === "found") return "found";
      return "idle";
    };

    const setCardState = (card, { badge, status, hint, connected, showDisconnect }) => {
      const badgeEl = card.querySelector("[data-printer-badge]");
      const statusEl = card.querySelector("[data-printer-status]");
      const hintEl = card.querySelector("[data-printer-hint]");
      const connectBtn = card.querySelector("[data-printer-connect]");
      const testBtn = card.querySelector("[data-printer-test]");
      const disconnectBtn = card.querySelector("[data-printer-disconnect]");
      const state = badgeState(badge);
      if (badgeEl) {
        badgeEl.textContent = badge;
        badgeEl.dataset.state = state;
      }
      if (statusEl) statusEl.textContent = status;
      if (hintEl) hintEl.textContent = hint || "";
      card.classList.toggle("is-connected", Boolean(connected));
      card.classList.toggle("is-error", state === "error");
      if (connectBtn) connectBtn.hidden = Boolean(connected);
      if (testBtn) {
        testBtn.hidden = !connected;
        testBtn.disabled = false;
      }
      if (disconnectBtn) disconnectBtn.hidden = !(connected || showDisconnect);
    };

    const renderDevices = (card, devices) => {
      const list = card.querySelector("[data-printer-devices]");
      if (!list) return;
      list.innerHTML = "";
      if (!devices?.length) {
        list.hidden = true;
        return;
      }
      list.hidden = false;
      devices.forEach((device, index) => {
        const li = document.createElement("li");
        li.className = "printer-device-item";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "printer-device-btn";
        btn.textContent = device.name || `Printer ${index + 1}`;
        btn.addEventListener("click", async () => {
          const channel = card.dataset.printerChannel;
          if (typeof device.onSelect === "function") {
            device.onSelect();
          } else if (device.host) {
            const hostInput = card.querySelector("[data-printer-wifi-host]");
            const portInput = card.querySelector("[data-printer-wifi-port]");
            if (hostInput) hostInput.value = device.host;
            if (portInput) portInput.value = String(device.port || 9100);
          }
          try {
            setCardState(card, {
              badge: "Connecting",
              status: `Connecting to ${device.name}…`,
              hint: "",
              connected: false,
            });
            await window.RichcomPrinter.connect(channel, device, card);
            refreshAll();
          } catch (err) {
            setCardState(card, {
              badge: "Error",
              status: "Not connected",
              hint: err?.message || "Could not connect.",
              connected: false,
            });
          }
        });
        li.appendChild(btn);
        list.appendChild(li);
      });
    };

    const refreshAll = () => {
      const status = window.RichcomPrinter.getStatus();
      enabled.forEach((channel) => {
        const card = modal.querySelector(`[data-printer-channel="${channel}"]`);
        if (!card) return;
        const connected = status.connected && status.channel === channel;
        const preferred = status.wantConnected && status.preferredChannel === channel;
        let hint = "";
        let badge = "Idle";
        let statusText = "Not connected";
        if (connected) {
          badge = "Connected";
          statusText = status.name || `${CHANNEL_META[channel].label} connected`;
          hint = status.usesBrowserPrint
            ? "Ready — Test print opens the Windows dialog and uses the Print Queue."
            : "Ready — raw ESC/POS (does not appear in the Windows Print Queue).";
        } else if (preferred && status.restoring) {
          badge = "Connecting";
          statusText = status.name || `Reconnecting ${CHANNEL_META[channel].label}…`;
          hint =
            status.lastRestoreError ||
            "Restoring previous printer session after refresh…";
        } else if (preferred) {
          badge = "Idle";
          statusText = status.name || "Saved — tap Connect if auto-reconnect needs help";
          hint =
            status.lastRestoreError ||
            "Session remembered. Auto-reconnect runs in the background.";
        }
        setCardState(card, {
          badge,
          status: statusText,
          hint,
          connected,
          showDisconnect: preferred,
        });
      });
      const global = modal.querySelector("[data-printer-global-status]");
      if (global) {
        if (status.connected) {
          const via = CHANNEL_META[status.channel]?.label || status.channel;
          global.textContent = status.usesBrowserPrint
            ? `Connected via ${via}: ${status.name}. Jobs go through the Windows Print Queue.`
            : `Connected via ${via}: ${status.name}. Raw mode — no Windows Print Queue entry.`;
        } else if (status.wantConnected && status.restoring) {
          global.textContent = `Reconnecting ${
            CHANNEL_META[status.preferredChannel]?.label || "printer"
          }… Session survives page refresh until you disconnect.`;
        } else if (status.wantConnected) {
          global.textContent =
            "Printer session saved. Auto-reconnect will keep trying until you disconnect.";
        } else {
          global.textContent =
            "Auto-print runs after a validated checkout when items are in the cart and a printer is connected.";
        }
      }
      if (window.lucide?.createIcons) window.lucide.createIcons();
    };

    document.querySelectorAll(`[data-modal-open="connect-printer"]`).forEach((trigger) => {
      trigger.addEventListener("click", () => {
        modal.hidden = false;
        document.body.classList.add("workspace-modal-open");
        // Clear stale scan lists so old USB fallback results never linger under Wi‑Fi.
        modal.querySelectorAll("[data-printer-devices]").forEach((list) => {
          list.innerHTML = "";
          list.hidden = true;
        });
        refreshAll();
        if (window.lucide?.createIcons) window.lucide.createIcons();
      });
    });

    modal.querySelectorAll("[data-printer-close], [data-modal-close]").forEach((el) => {
      el.addEventListener("click", () => {
        modal.hidden = true;
        const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
        document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
      });
    });

    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || modal.hidden) return;
      modal.hidden = true;
      const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
      document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
    });

    for (const channel of enabled) {
      const card = modal.querySelector(`[data-printer-channel="${channel}"]`);
      if (!card) continue;

      card.querySelector("[data-printer-scan]")?.addEventListener("click", async () => {
        try {
          setCardState(card, {
            badge: "Scanning",
            status:
              channel === "wifi"
                ? "Scanning Wi‑Fi for printers…"
                : "Scanning for printers…",
            hint:
              channel === "wifi"
                ? "Checking live devices only (fast)."
                : "Pick a device when prompted.",
            connected: false,
          });
          const devices = await window.RichcomPrinter.scan(channel, card);
          renderDevices(card, devices);

          setCardState(card, {
            badge: "Found",
            status:
              channel === "wifi"
                ? `${devices.length} Wi‑Fi printer${devices.length === 1 ? "" : "s"} found`
                : devices[0]?.name || "Device found",
            hint:
              channel === "wifi"
                ? devices.length === 1
                  ? "Connecting to the Wi‑Fi printer…"
                  : "Tap a Wi‑Fi printer to connect."
                : "Select a device or press Connect.",
            connected: false,
          });

          const connectFound = async (device) => {
            if (typeof device.onSelect === "function") device.onSelect();
            await window.RichcomPrinter.connect(channel, device, card);
            refreshAll();
            const hintEl = card.querySelector("[data-printer-hint]");
            if (hintEl) hintEl.textContent = "Connected. Use Test print to confirm.";
          };

          // One Wi‑Fi result → connect immediately.
          if (channel === "wifi" && devices.length === 1) {
            await connectFound(devices[0]);
          }

          if (channel === "wifi") {
            card.querySelectorAll(".printer-device-btn").forEach((btn, index) => {
              const device = devices[index];
              if (!device) return;
              const clone = btn.cloneNode(true);
              btn.parentNode.replaceChild(clone, btn);
              clone.addEventListener("click", async () => {
                try {
                  setCardState(card, {
                    badge: "Connecting",
                    status: `Connecting to ${device.name}…`,
                    hint: "",
                    connected: false,
                  });
                  await connectFound(device);
                } catch (connectErr) {
                  setCardState(card, {
                    badge: "Error",
                    status: "Not connected",
                    hint: connectErr?.message || "Could not connect.",
                    connected: false,
                  });
                }
              });
            });
          }
        } catch (err) {
          renderDevices(card, []);
          setCardState(card, {
            badge: "Error",
            status: "Not connected",
            hint: err?.message || "Scan failed.",
            connected: false,
          });
        }
      });

      card.querySelector("[data-printer-connect]")?.addEventListener("click", async () => {
        try {
          setCardState(card, {
            badge: "Connecting",
            status: "Connecting…",
            hint: "",
            connected: false,
          });
          let selected = null;
          if (channel === "wifi") {
            const host = card.querySelector("[data-printer-wifi-host]")?.value?.trim();
            const port = Number(card.querySelector("[data-printer-wifi-port]")?.value || 9100);
            if (!host) {
              throw new Error(
                "Enter the Wi‑Fi printer IP first (or tap Scan). USB POS printers use the USB channel."
              );
            }
            selected = {
              id: `wifi-${host}:${port}`,
              name: `Wi‑Fi printer ${host}:${port}`,
              host,
              port,
            };
          } else if (channel === "usb") {
            const usbMode =
              card.querySelector("[data-printer-usb-mode]")?.value || "system";
            if (usbMode === "system") {
              selected = {
                id: "usb-windows",
                name: "Windows USB printer",
                kind: "system",
              };
            }
          }
          await window.RichcomPrinter.connect(channel, selected, card);
          renderDevices(card, []);
          refreshAll();
          const hintEl = card.querySelector("[data-printer-hint]");
          if (hintEl) {
            hintEl.textContent =
              channel === "usb"
                ? "Connected. Click Test print and choose your POS printer in Windows."
                : "Connected. Click Test print to confirm.";
          }
        } catch (err) {
          setCardState(card, {
            badge: "Error",
            status: "Not connected",
            hint: err?.message || "Connect failed.",
            connected: false,
          });
        }
      });

      card.querySelector("[data-printer-disconnect]")?.addEventListener("click", async () => {
        try {
          await window.RichcomPrinter.disconnect(channel);
        } catch (_) {
          /* ignore */
        }
        renderDevices(card, []);
        refreshAll();
      });

      card.querySelector("[data-printer-test]")?.addEventListener("click", async () => {
        const testBtn = card.querySelector("[data-printer-test]");
        const hintEl = card.querySelector("[data-printer-hint]");
        try {
          if (testBtn) testBtn.disabled = true;
          if (hintEl) hintEl.textContent = "Sending test page…";
          await window.RichcomPrinter.printTestPage(channel);
          const status = window.RichcomPrinter.getStatus();
          if (hintEl) {
            hintEl.textContent = status.usesBrowserPrint
              ? "Windows print dialog opened — choose your USB printer. It should appear in Print Queue."
              : "Raw test bytes sent (will not show in Windows Print Queue). Check the printer paper.";
          }
        } catch (err) {
          if (hintEl) {
            hintEl.textContent = err?.message || "Test print failed.";
          }
        } finally {
          if (testBtn) testBtn.disabled = false;
          if (window.lucide?.createIcons) window.lucide.createIcons();
        }
      });

      const usbModeSelect = card.querySelector("[data-printer-usb-mode]");
      const baudWrap = card.querySelector("[data-printer-usb-baud-wrap]");
      const syncUsbModeUi = () => {
        if (!usbModeSelect || !baudWrap) return;
        baudWrap.hidden = usbModeSelect.value !== "raw";
      };
      usbModeSelect?.addEventListener("change", syncUsbModeUi);
      syncUsbModeUi();
    }

    const store = loadStore();
    if (store.wifi) {
      const wifiCard = modal.querySelector('[data-printer-channel="wifi"]');
      const hostInput = wifiCard?.querySelector("[data-printer-wifi-host]");
      const portInput = wifiCard?.querySelector("[data-printer-wifi-port]");
      if (hostInput && store.wifi.host) hostInput.value = store.wifi.host;
      if (portInput && store.wifi.port) portInput.value = String(store.wifi.port);
    }
    if (store.usb) {
      const usbCard = modal.querySelector('[data-printer-channel="usb"]');
      const modeSelect = usbCard?.querySelector("[data-printer-usb-mode]");
      const baudSelect = usbCard?.querySelector("[data-printer-usb-baud]");
      const baudWrap = usbCard?.querySelector("[data-printer-usb-baud-wrap]");
      if (modeSelect && store.usb.printMode) {
        modeSelect.value = store.usb.printMode === "raw" ? "raw" : "system";
      }
      if (baudSelect && store.usb.baudRate) {
        baudSelect.value = String(store.usb.baudRate);
      }
      if (baudWrap && modeSelect) {
        baudWrap.hidden = modeSelect.value !== "raw";
      }
    }

    window.RichcomPrinter.onChange(refreshAll);
    refreshAll();
    window.RichcomPrinter.restoreSession({ reason: "boot", enabledChannels: enabled }).finally(
      () => {
        refreshAll();
      }
    );

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") return;
      const status = window.RichcomPrinter.getStatus();
      if (status.wantConnected && !status.connected) {
        window.RichcomPrinter.restoreSession({ reason: "visible", enabledChannels: enabled });
      }
    });
    window.addEventListener("online", () => {
      const status = window.RichcomPrinter.getStatus();
      if (status.wantConnected && !status.connected) {
        window.RichcomPrinter.restoreSession({ reason: "online", enabledChannels: enabled });
      }
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindUi);
  } else {
    bindUi();
  }
})();
