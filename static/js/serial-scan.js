/**
 * Serial barcode / QR / image scanner for MY-SHOP serial inputs.
 * Enhances matching inputs with a Scan button; fills value + fires input/change
 * so existing type/search/submit behaviour stays unchanged.
 */
(function () {
  "use strict";

  const INPUT_SELECTOR = [
    "[data-stock-serial-input]",
    "[data-serial-sale-input]",
    "[data-serial-input]",
    "[data-serial-scan]",
  ].join(",");

  const LIB_URL =
    document.currentScript?.dataset?.html5QrcodeUrl ||
    "/static/vendor/html5-qrcode.min.js";

  let libPromise = null;
  let activeTarget = null;
  let html5Scanner = null;
  let stream = null;
  let modal = null;
  let statusEl = null;
  let readerEl = null;
  let previewEl = null;
  let fileInput = null;
  let cameraInput = null;

  const normalizeSerial = (value) =>
    String(value || "")
      .trim()
      .toUpperCase()
      .replace(/\s+/g, "");

  const loadScript = (src) =>
    new Promise((resolve, reject) => {
      if (window.Html5Qrcode) {
        resolve(window.Html5Qrcode);
        return;
      }
      const existing = document.querySelector(`script[data-serial-scan-lib]`);
      if (existing) {
        existing.addEventListener("load", () => resolve(window.Html5Qrcode), {
          once: true,
        });
        existing.addEventListener(
          "error",
          () => reject(new Error("Failed to load scanner library")),
          { once: true }
        );
        return;
      }
      const script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.dataset.serialScanLib = "1";
      script.onload = () => resolve(window.Html5Qrcode);
      script.onerror = () => reject(new Error("Failed to load scanner library"));
      document.head.appendChild(script);
    });

  const ensureLib = () => {
    if (!libPromise) libPromise = loadScript(LIB_URL);
    return libPromise;
  };

  const refreshIcons = () => {
    try {
      window.lucide?.createIcons?.();
    } catch (_) {
      /* ignore */
    }
  };

  const setStatus = (message, { error = false } = {}) => {
    if (!statusEl) return;
    statusEl.hidden = !message;
    statusEl.textContent = message || "";
    statusEl.classList.toggle("is-error", Boolean(error));
  };

  const stopCamera = async () => {
    if (html5Scanner) {
      try {
        const state = html5Scanner.getState?.();
        // 2 = SCANNING, 3 = PAUSED in html5-qrcode
        if (state === 2 || state === 3) {
          await html5Scanner.stop();
        }
      } catch (_) {
        /* ignore */
      }
      try {
        html5Scanner.clear();
      } catch (_) {
        /* ignore */
      }
      html5Scanner = null;
    }
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    if (previewEl) {
      previewEl.hidden = true;
      previewEl.removeAttribute("src");
    }
    if (readerEl) readerEl.innerHTML = "";
  };

  const applySerial = (raw, target) => {
    const serial = normalizeSerial(raw);
    const input = target || activeTarget;
    if (!serial || !input) return false;

    input.value = serial;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    // Keyboard-style burst helps listeners that key off keyup.
    input.dispatchEvent(
      new KeyboardEvent("keyup", { bubbles: true, key: "Enter" })
    );

    try {
      input.focus({ preventScroll: true });
    } catch (_) {
      input.focus?.();
    }

    setStatus(`Scanned: ${serial}`);
    window.setTimeout(() => closeModal(), 280);
    return true;
  };

  const decodeWithBarcodeDetector = async (source) => {
    if (!("BarcodeDetector" in window)) return null;
    try {
      const detector = new window.BarcodeDetector({
        formats: [
          "qr_code",
          "code_128",
          "code_39",
          "code_93",
          "ean_13",
          "ean_8",
          "upc_a",
          "upc_e",
          "itf",
          "codabar",
          "data_matrix",
        ],
      });
      const codes = await detector.detect(source);
      const raw = codes?.[0]?.rawValue;
      return raw ? normalizeSerial(raw) : null;
    } catch (_) {
      return null;
    }
  };

  const decodeImageFile = async (file) => {
    if (!file) throw new Error("No image selected");
    setStatus("Reading image…");

    // Prefer native BarcodeDetector on the bitmap when available.
    try {
      const bitmap = await createImageBitmap(file);
      const native = await decodeWithBarcodeDetector(bitmap);
      bitmap.close?.();
      if (native) return native;
    } catch (_) {
      /* fall through */
    }

    const Html5Qrcode = await ensureLib();
    if (!Html5Qrcode) throw new Error("Scanner library unavailable");

    // html5-qrcode expects a real element id in the DOM.
    const host = document.createElement("div");
    host.id = `serial-scan-file-${Date.now()}`;
    host.hidden = true;
    document.body.appendChild(host);
    try {
      const fileScanner = new Html5Qrcode(host.id, { verbose: false });
      const decoded = await fileScanner.scanFile(file, true);
      try {
        fileScanner.clear();
      } catch (_) {
        /* ignore */
      }
      return normalizeSerial(decoded);
    } finally {
      host.remove();
    }
  };

  const onScanSuccess = (decodedText) => {
    applySerial(decodedText);
  };

  const startCamera = async () => {
    setStatus("Starting camera…");
    await stopCamera();
    if (readerEl) readerEl.innerHTML = "";

    const Html5Qrcode = await ensureLib();
    if (!Html5Qrcode || !readerEl) {
      throw new Error("Scanner not ready");
    }

    html5Scanner = new Html5Qrcode(readerEl.id, { verbose: false });
    const config = {
      fps: 10,
      qrbox: (viewfinderWidth, viewfinderHeight) => {
        const edge = Math.floor(Math.min(viewfinderWidth, viewfinderHeight) * 0.72);
        return { width: edge, height: edge };
      },
      aspectRatio: 1,
      experimentalFeatures: { useBarCodeDetectorIfSupported: true },
    };

    const cameras = await Html5Qrcode.getCameras().catch(() => []);
    const back =
      cameras.find((c) => /back|rear|environment/i.test(c.label || "")) ||
      cameras[cameras.length - 1];

    const cameraConfig = back?.id
      ? back.id
      : { facingMode: "environment" };

    await html5Scanner.start(
      cameraConfig,
      config,
      (text) => onScanSuccess(text),
      () => {
        /* ignore frame miss */
      }
    );
    setStatus("Point at a barcode or QR code");
  };

  const ensureModal = () => {
    if (modal) return modal;

    modal = document.createElement("div");
    modal.className = "serial-scan-modal";
    modal.setAttribute("data-serial-scan-modal", "");
    modal.setAttribute("aria-hidden", "true");
    modal.innerHTML = `
      <div class="serial-scan-backdrop" data-serial-scan-close></div>
      <div
        class="serial-scan-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="serial-scan-title"
      >
        <header class="serial-scan-head">
          <div>
            <p class="serial-scan-kicker">Serial capture</p>
            <h2 id="serial-scan-title">Scan serial</h2>
          </div>
          <button
            type="button"
            class="serial-scan-close"
            data-serial-scan-close
            aria-label="Close scanner"
          >
            <i data-lucide="x" aria-hidden="true"></i>
          </button>
        </header>

        <div class="serial-scan-actions">
          <button type="button" class="serial-scan-action" data-serial-scan-camera>
            <i data-lucide="camera" aria-hidden="true"></i>
            <span>Live camera</span>
          </button>
          <button type="button" class="serial-scan-action" data-serial-scan-snap>
            <i data-lucide="aperture" aria-hidden="true"></i>
            <span>Take photo</span>
          </button>
          <button type="button" class="serial-scan-action" data-serial-scan-gallery>
            <i data-lucide="image" aria-hidden="true"></i>
            <span>Choose image</span>
          </button>
        </div>

        <div class="serial-scan-stage">
          <div id="serial-scan-reader" class="serial-scan-reader" data-serial-scan-reader></div>
          <img class="serial-scan-preview" data-serial-scan-preview alt="" hidden>
        </div>

        <p class="serial-scan-status" data-serial-scan-status hidden></p>
        <p class="serial-scan-hint">
          Supports QR and barcodes. You can still type the serial manually.
        </p>

        <input
          type="file"
          accept="image/*"
          capture="environment"
          data-serial-scan-camera-file
          hidden
        >
        <input
          type="file"
          accept="image/*"
          data-serial-scan-file
          hidden
        >
      </div>
    `;
    document.body.appendChild(modal);

    statusEl = modal.querySelector("[data-serial-scan-status]");
    readerEl = modal.querySelector("[data-serial-scan-reader]");
    previewEl = modal.querySelector("[data-serial-scan-preview]");
    fileInput = modal.querySelector("[data-serial-scan-file]");
    cameraInput = modal.querySelector("[data-serial-scan-camera-file]");

    modal.addEventListener("click", (event) => {
      if (event.target.closest("[data-serial-scan-close]")) {
        closeModal();
        return;
      }
      if (event.target.closest("[data-serial-scan-camera]")) {
        startCamera().catch((err) =>
          setStatus(err?.message || "Camera unavailable", { error: true })
        );
        return;
      }
      if (event.target.closest("[data-serial-scan-snap]")) {
        cameraInput?.click();
        return;
      }
      if (event.target.closest("[data-serial-scan-gallery]")) {
        fileInput?.click();
      }
    });

    const handleFile = async (event) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file) return;
      await stopCamera();
      if (previewEl) {
        const url = URL.createObjectURL(file);
        previewEl.src = url;
        previewEl.hidden = false;
        previewEl.onload = () => URL.revokeObjectURL(url);
      }
      try {
        const serial = await decodeImageFile(file);
        if (!serial) {
          setStatus("No barcode or QR found in that image", { error: true });
          return;
        }
        applySerial(serial);
      } catch (err) {
        setStatus(err?.message || "Could not read image", { error: true });
      }
    };

    fileInput?.addEventListener("change", handleFile);
    cameraInput?.addEventListener("change", handleFile);

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && modal?.classList.contains("is-open")) {
        closeModal();
      }
    });

    refreshIcons();
    return modal;
  };

  const openModal = (input) => {
    if (!input || input.disabled) return;
    activeTarget = input;
    ensureModal();
    modal.classList.add("is-open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("serial-scan-open");
    setStatus("Choose live camera, take a photo, or pick an image");
    if (previewEl) {
      previewEl.hidden = true;
      previewEl.removeAttribute("src");
    }
    refreshIcons();
    // Auto-start camera when permission likely available; ignore failure.
    startCamera().catch(() => {
      setStatus("Choose live camera, take a photo, or pick an image");
    });
  };

  const closeModal = async () => {
    await stopCamera();
    if (!modal) return;
    modal.classList.remove("is-open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("serial-scan-open");
    activeTarget = null;
    setStatus("");
  };

  const makeScanButton = (input) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "serial-scan-btn";
    btn.setAttribute("data-serial-scan-open", "");
    btn.setAttribute("aria-label", "Scan serial barcode or QR");
    btn.title = "Scan barcode / QR / image";
    btn.innerHTML = '<i data-lucide="scan-barcode" aria-hidden="true"></i>';
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openModal(input);
    });
    return btn;
  };

  const enhanceInput = (input) => {
    if (!(input instanceof HTMLInputElement)) return;
    if (input.dataset.serialScanReady === "1") return;
    if (input.type === "hidden") return;

    input.dataset.serialScanReady = "1";
    const btn = makeScanButton(input);
    const row = input.closest(".stock-serial-row, .shop-serial-row");
    const inputWrap = input.closest(
      ".stock-serial-input-wrap, .shop-serial-input-wrap, .stock-request-serial-wrap"
    );

    if (row) {
      row.classList.add("has-serial-scan");
      const removeBtn = row.querySelector(
        "[data-stock-serial-remove], [data-serial-sale-remove], .stock-serial-remove, .shop-serial-row-remove"
      );
      if (removeBtn) row.insertBefore(btn, removeBtn);
      else row.appendChild(btn);
    } else if (inputWrap) {
      // Keep suggest dropdown intact; pin scan control inside the wrap.
      inputWrap.classList.add("has-serial-scan");
      inputWrap.appendChild(btn);
    } else {
      const wrap = document.createElement("div");
      wrap.className = "serial-scan-field-wrap";
      input.parentNode?.insertBefore(wrap, input);
      wrap.append(input, btn);
    }

    refreshIcons();
  };

  const scanTree = (root = document) => {
    root.querySelectorAll?.(INPUT_SELECTOR)?.forEach(enhanceInput);
    if (root instanceof HTMLInputElement && root.matches(INPUT_SELECTOR)) {
      enhanceInput(root);
    }
  };

  const observe = () => {
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        mutation.addedNodes.forEach((node) => {
          if (!(node instanceof HTMLElement)) return;
          scanTree(node);
        });
      }
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
    });
  };

  const boot = () => {
    ensureModal();
    scanTree(document);
    observe();
    // Preload library in idle time so first scan is snappy.
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(() => {
        ensureLib().catch(() => {});
      }, { timeout: 4000 });
    } else {
      window.setTimeout(() => ensureLib().catch(() => {}), 1800);
    }
  };

  // Public hooks for tests / manual use.
  window.MyShopSerialScan = {
    enhance: scanTree,
    open: openModal,
    close: closeModal,
    normalize: normalizeSerial,
    apply: applySerial,
    decodeImageFile,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
