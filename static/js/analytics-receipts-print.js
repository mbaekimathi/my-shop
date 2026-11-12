/**
 * Analytics receipts print popup — paper size, then Windows print or A4 PDF.
 * Mirrors stock-print.js: fetch print HTML into a same-origin srcdoc iframe.
 */
(function () {
  "use strict";

  const modal = document.querySelector("[data-modal='print-receipts']");
  if (!modal) return;

  const form = modal.querySelector("[data-receipts-print-form]");
  const printUrl = modal.getAttribute("data-receipts-print-url") || "";
  const statusEl = modal.querySelector("[data-receipts-print-status]");
  const pagesEl = modal.querySelector("[data-receipts-print-pages]");
  const downloadBtn = modal.querySelector("[data-receipts-print-download-a4]");
  const filtersForm = document.querySelector("[data-analytics-filters]");

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

  const selectedPaper = () => {
    const value =
      form?.querySelector('input[name="print_paper"]:checked')?.value || "a4";
    return value === "80" || value === "50" ? value : "a4";
  };

  const currentFilterParams = () => {
    const params = new URLSearchParams();
    if (filtersForm) {
      new FormData(filtersForm).forEach((value, key) => {
        if (value == null) return;
        const text = String(value).trim();
        if (!text && key !== "shop_id") return;
        params.append(key, text);
      });
    } else {
      const pageParams = new URLSearchParams(window.location.search);
      pageParams.forEach((value, key) => {
        if (key === "download") return;
        params.append(key, value);
      });
    }
    return params;
  };

  const buildParams = ({
    paper,
    download = false,
    estimate = false,
  } = {}) => {
    const params = currentFilterParams();
    params.set("paper", paper || "a4");
    params.delete("download");
    params.delete("estimate");
    if (download) params.set("download", "1");
    if (estimate) params.set("estimate", "1");
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
      const receipts = Number(data.receipt_count) || 0;
      const pageLabel = pages === 1 ? "page" : "pages";
      const receiptLabel = receipts === 1 ? "receipt" : "receipts";
      const kind = data.kind_label || "All types";
      setPagesHint(
        `A4 estimate: about ${pages} ${pageLabel} (${receipts} ${receiptLabel} · ${kind}).`
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
    const plain =
      /filename\s*=\s*"([^"]+)"/i.exec(header) ||
      /filename\s*=\s*([^;]+)/i.exec(header);
    return plain?.[1]?.trim() || fallback;
  };

  const openModal = () => {
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("workspace-modal-open");
    setStatus("");
    scheduleA4PageEstimate();
    try {
      window.lucide?.createIcons?.();
    } catch (_) {
      /* ignore */
    }
    window.setTimeout(() => {
      form?.querySelector('input[name="print_paper"]:checked')?.focus();
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

  document.querySelectorAll('[data-modal-open="print-receipts"]').forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      openModal();
    });
  });

  modal.querySelectorAll("[data-modal-close], [data-receipts-print-close]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.preventDefault();
      closeModal();
    });
  });

  form?.addEventListener("change", (event) => {
    if (event.target?.name === "print_paper") scheduleA4PageEstimate();
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });

  const looksLikePdfBytes = async (blob) => {
    if (!blob || blob.size < 4) return false;
    try {
      const header = new Uint8Array(await blob.slice(0, 5).arrayBuffer());
      return (
        header[0] === 0x25 &&
        header[1] === 0x50 &&
        header[2] === 0x44 &&
        header[3] === 0x46
      );
    } catch (_) {
      return false;
    }
  };

  const triggerNativeDownload = (url) => {
    const link = document.createElement("a");
    link.href = url;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const downloadA4Pdf = async (button) => {
    if (!printUrl) {
      setStatus("Print URL is missing.", { error: true });
      return;
    }

    const built = buildParams({ paper: "a4", download: true });
    const url = `${printUrl}?${built.params.toString()}`;
    button.disabled = true;
    setStatus("Preparing A4 PDF…");

    try {
      let response;
      try {
        response = await fetch(url, {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/pdf" },
        });
      } catch (_) {
        triggerNativeDownload(url);
        closeModal();
        return;
      }

      const contentType = (response.headers.get("Content-Type") || "").toLowerCase();
      if (!response.ok) {
        let message = "Could not download the A4 PDF.";
        if (contentType.includes("application/json")) {
          const data = await response.json().catch(() => null);
          if (data?.error) message = data.error;
        }
        setStatus(message, { error: true });
        return;
      }

      const blob = await response.blob();
      if (!(await looksLikePdfBytes(blob))) {
        triggerNativeDownload(url);
        closeModal();
        return;
      }

      const fallbackName = `receipts-a4-${new Date().toISOString().slice(0, 10)}.pdf`;
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
      button.disabled = false;
    }
  };

  downloadBtn?.addEventListener("click", (event) => {
    event.preventDefault();
    downloadA4Pdf(downloadBtn);
  });

  const extractPrintError = (html) => {
    const match = /class="print-error"[^>]*>([^<]+)/i.exec(html || "");
    return match?.[1]?.trim() || "";
  };

  const printViaWindowsDialog = async (url) => {
    document.querySelectorAll("[data-receipts-print-frame]").forEach((el) => el.remove());

    const response = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "text/html,application/xhtml+xml" },
    });
    const html = await response.text();
    if (!response.ok) {
      throw new Error(
        extractPrintError(html) || "Could not load receipts for printing."
      );
    }
    const pageError = extractPrintError(html);
    if (pageError) throw new Error(pageError);

    const frame = document.createElement("iframe");
    frame.setAttribute("data-receipts-print-frame", "1");
    frame.setAttribute("aria-hidden", "true");
    frame.title = "Receipts print";
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
    const built = buildParams({ paper });
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    setStatus("Opening Windows print…");

    const url = `${printUrl}?${built.params.toString()}`;
    try {
      await printViaWindowsDialog(url);
      closeModal();
    } catch (err) {
      setStatus(err?.message || "Could not print receipts.", { error: true });
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
})();
