/**
 * Print stock popup — choose layout, paper size, then print or download A4.
 * Shows an estimated A4 page count before printing.
 */
(function () {
  "use strict";

  const modal = document.querySelector("[data-modal='print-stock']");
  if (!modal) return;

  const form = modal.querySelector("[data-stock-print-form]");
  const printUrl = modal.getAttribute("data-stock-print-url") || "";
  const shopsPanel = modal.querySelector("[data-stock-print-shops]");
  const statusEl = modal.querySelector("[data-stock-print-status]");
  const pagesEl = modal.querySelector("[data-stock-print-pages]");
  const shopSelect = document.querySelector("[data-stock-shop-nav]");
  const downloadBtn = modal.querySelector("[data-stock-print-download-a4]");

  const PAPER_SIZES = {
    a4: { w: 920, h: 720 },
    80: { w: 420, h: 720 },
    50: { w: 340, h: 720 },
  };

  let estimateTimer = 0;
  let estimateSeq = 0;

  const setStatus = (message, { error = false } = {}) => {
    if (!statusEl) return;
    statusEl.hidden = !message;
    statusEl.textContent = message || "";
    statusEl.classList.toggle("is-error", Boolean(error));
  };

  const setPagesHint = (message) => {
    if (!pagesEl) return;
    pagesEl.hidden = !message;
    pagesEl.textContent = message || "";
  };

  const selectedLayout = () =>
    form?.querySelector('input[name="print_layout"]:checked')?.value || "items";

  const selectedPaper = () => {
    const value =
      form?.querySelector('input[name="print_paper"]:checked')?.value || "a4";
    return PAPER_SIZES[value] ? value : "a4";
  };

  const syncShopsVisibility = () => {
    const layout = selectedLayout();
    const needsShops = layout === "prices" || layout === "stock";
    if (shopsPanel) shopsPanel.hidden = !needsShops;
  };

  const preselectShopsFromPage = () => {
    const current = (shopSelect?.value || "").trim();
    const boxes = [...(modal.querySelectorAll("[data-stock-print-shop]") || [])];
    if (!boxes.length) return;
    if (current) {
      boxes.forEach((box) => {
        box.checked = String(box.value) === current;
      });
      return;
    }
    boxes.forEach((box) => {
      box.checked = false;
    });
  };

  const selectedShopIds = () =>
    [...(modal.querySelectorAll("[data-stock-print-shop]:checked") || [])].map(
      (el) => el.value
    );

  const buildParams = ({
    paper,
    auto = false,
    download = false,
    estimate = false,
  } = {}) => {
    const layout = selectedLayout();
    const params = new URLSearchParams();
    params.set("layout", layout);
    params.set("paper", paper || "a4");
    if (auto) params.set("auto", "1");
    if (download) params.set("download", "1");
    if (estimate) params.set("estimate", "1");

    if (layout === "prices" || layout === "stock") {
      const shopIds = selectedShopIds();
      if (!shopIds.length) {
        return { error: "Select at least one shop.", params: null };
      }
      shopIds.forEach((id) => params.append("shop_id", id));
    }
    return { error: "", params };
  };

  const refreshA4PageEstimate = async () => {
    if (!printUrl || !pagesEl) return;

    const paper = selectedPaper();
    if (paper !== "a4") {
      setPagesHint("Thermal rolls print as one continuous strip (not A4 pages).");
      return;
    }

    const built = buildParams({ paper: "a4", estimate: true });
    if (built.error) {
      setPagesHint(built.error);
      return;
    }

    const seq = ++estimateSeq;
    setPagesHint("Checking A4 page count…");

    try {
      const response = await fetch(`${printUrl}?${built.params.toString()}`, {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const data = await response.json().catch(() => null);
      if (seq !== estimateSeq) return;
      if (!response.ok || !data?.ok) {
        setPagesHint(data?.error || "Could not estimate A4 pages.");
        return;
      }
      const pages = Number(data.a4_page_estimate) || 1;
      const items = Number(data.item_count) || 0;
      const pageLabel = pages === 1 ? "page" : "pages";
      const itemLabel = items === 1 ? "item" : "items";
      setPagesHint(
        `A4 estimate: about ${pages} ${pageLabel} (${items} ${itemLabel}).`
      );
    } catch (_) {
      if (seq !== estimateSeq) return;
      setPagesHint("Could not estimate A4 pages.");
    }
  };

  const scheduleA4PageEstimate = () => {
    window.clearTimeout(estimateTimer);
    estimateTimer = window.setTimeout(() => {
      refreshA4PageEstimate();
    }, 180);
  };

  const filenameFromDisposition = (header, fallback) => {
    if (!header) return fallback;
    const utf = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
    if (utf?.[1]) {
      try {
        return decodeURIComponent(utf[1].trim());
      } catch (_) {
        /* ignore */
      }
    }
    const plain = /filename\s*=\s*"([^"]+)"/i.exec(header)
      || /filename\s*=\s*([^;]+)/i.exec(header);
    return plain?.[1]?.trim() || fallback;
  };

  const openModal = () => {
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("workspace-modal-open");
    preselectShopsFromPage();
    syncShopsVisibility();
    setStatus("");
    scheduleA4PageEstimate();
    try {
      window.lucide?.createIcons?.();
    } catch (_) {
      /* ignore */
    }
    window.setTimeout(() => {
      form?.querySelector('input[name="print_layout"]:checked')?.focus();
    }, 40);
  };

  const closeModal = () => {
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    const anyOpen = document.querySelector(".workspace-modal:not([hidden])");
    document.body.classList.toggle("workspace-modal-open", Boolean(anyOpen));
    setStatus("");
    setPagesHint("");
    window.clearTimeout(estimateTimer);
    estimateSeq += 1;
  };

  document.querySelectorAll('[data-modal-open="print-stock"]').forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      openModal();
    });
  });

  modal.querySelectorAll("[data-modal-close], [data-stock-print-close]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.preventDefault();
      closeModal();
    });
  });

  form?.addEventListener("change", (event) => {
    const name = event.target?.name;
    if (name === "print_layout") syncShopsVisibility();
    if (
      name === "print_layout"
      || name === "print_paper"
      || event.target?.matches?.("[data-stock-print-shop]")
    ) {
      scheduleA4PageEstimate();
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });

  downloadBtn?.addEventListener("click", async (event) => {
    event.preventDefault();
    if (!printUrl) {
      setStatus("Print URL is missing.", { error: true });
      return;
    }

    const built = buildParams({ paper: "a4", download: true });
    if (built.error) {
      setStatus(built.error, { error: true });
      return;
    }

    const url = `${printUrl}?${built.params.toString()}`;
    downloadBtn.disabled = true;
    setStatus("Preparing A4 PDF…");

    try {
      const response = await fetch(url, {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "application/pdf" },
      });
      if (!response.ok) {
        let message = "Could not download the A4 PDF.";
        try {
          const contentType = response.headers.get("Content-Type") || "";
          if (contentType.includes("application/json")) {
            const data = await response.json();
            if (data?.error) message = data.error;
          } else {
            const text = await response.text();
            const match = text.match(/class="print-error"[^>]*>([^<]+)/i);
            if (match?.[1]) message = match[1].trim();
          }
        } catch (_) {
          /* ignore */
        }
        throw new Error(message);
      }

      const blob = await response.blob();
      const fallbackName = `stock-list-a4-${new Date().toISOString().slice(0, 10)}.pdf`;
      const filename = filenameFromDisposition(
        response.headers.get("Content-Disposition"),
        fallbackName
      );
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename.endsWith(".pdf") ? filename : `${filename}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1500);
      closeModal();
    } catch (err) {
      setStatus(err?.message || "Could not download the A4 PDF.", { error: true });
    } finally {
      downloadBtn.disabled = false;
    }
  });

  const extractPrintError = (html) => {
    const match = /class="print-error"[^>]*>([^<]+)/i.exec(html || "");
    return match?.[1]?.trim() || "";
  };

  /**
   * Fetch the print HTML and print via a same-origin srcdoc iframe.
   * Avoids X-Frame-Options: DENY (iframe src would be blocked / cross-origin).
   * Uses the browser/Windows print dialog (USB printers appear there).
   */
  const printViaWindowsDialog = async (url) => {
    document.querySelectorAll("[data-stock-print-frame]").forEach((el) => el.remove());

    const response = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "text/html,application/xhtml+xml" },
    });
    const html = await response.text();
    if (!response.ok) {
      throw new Error(
        extractPrintError(html) || "Could not load the stock list for printing."
      );
    }
    const pageError = extractPrintError(html);
    if (pageError) throw new Error(pageError);

    const frame = document.createElement("iframe");
    frame.setAttribute("data-stock-print-frame", "1");
    frame.setAttribute("aria-hidden", "true");
    frame.title = "Stock print";
    frame.style.cssText =
      "position:fixed;right:0;bottom:0;width:0;height:0;border:0;opacity:0;pointer-events:none;";

    const loaded = new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        reject(new Error("Print sheet timed out. Try again."));
      }, 20000);
      frame.addEventListener(
        "load",
        () => {
          window.clearTimeout(timer);
          resolve();
        },
        { once: true }
      );
      frame.addEventListener(
        "error",
        () => {
          window.clearTimeout(timer);
          reject(new Error("Could not prepare the print sheet."));
        },
        { once: true }
      );
    });

    document.body.appendChild(frame);
    frame.srcdoc = html;
    await loaded;

    const win = frame.contentWindow;
    if (!win) {
      frame.remove();
      throw new Error("Could not prepare the print sheet.");
    }

    await new Promise((resolve) => window.setTimeout(resolve, 80));

    try {
      win.focus();
      win.print();
    } catch (err) {
      frame.remove();
      throw err instanceof Error
        ? err
        : new Error("Could not open the Windows print dialog.");
    }

    const cleanup = () => {
      window.setTimeout(() => frame.remove(), 400);
    };
    try {
      win.addEventListener("afterprint", cleanup, { once: true });
    } catch (_) {
      /* ignore */
    }
    window.setTimeout(cleanup, 120000);
  };

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!printUrl) {
      setStatus("Print URL is missing.", { error: true });
      return;
    }

    const paper = selectedPaper();
    const built = buildParams({ paper, auto: false });
    if (built.error) {
      setStatus(built.error, { error: true });
      return;
    }

    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    setStatus("Opening Windows print…");

    const url = `${printUrl}?${built.params.toString()}`;
    try {
      await printViaWindowsDialog(url);
      closeModal();
    } catch (err) {
      setStatus(err?.message || "Could not print stock.", { error: true });
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
})();
