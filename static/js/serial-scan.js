/**
 * Serial barcode / QR / image scanner for MY-SHOP serial inputs.
 * Enhances matching inputs with a Scan button; fills value + fires input/change
 * so existing type/search/submit behaviour stays unchanged.
 *
 * Optional extraction (only keep the serial portion of a scan):
 *   - Script tag: data-serial-scan-extract="…"
 *   - Form/container: data-serial-scan-extract="…"
 *   - Input: data-serial-scan-extract="…"
 *   - JS hook: MyShopSerialScan.setExtractor(fn)
 *
 * Rule can be a regex (use capture group 1 for the serial) or a preset:
 *   alnum, url-path, after-colon, gs1-21, gpon-sn
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

  const BARCODE_FORMATS = [
    "code_128",
    "code_39",
    "code_93",
    "ean_13",
    "ean_8",
    "upc_a",
    "upc_e",
    "itf",
    "codabar",
    "qr_code",
    "data_matrix",
  ];

  let libPromise = null;
  let activeTarget = null;
  let html5Scanner = null;
  let stream = null;
  let nativeVideo = null;
  let nativeLoopId = 0;
  let nativeDetecting = false;
  let scanLocked = false;
  let modal = null;
  let statusEl = null;
  let readerEl = null;
  let previewEl = null;
  let fileInput = null;
  let cameraInput = null;
  let barcodeDetector = null;
  let customExtractor = null;

  const normalizeSerial = (value) =>
    String(value || "")
      .trim()
      .toUpperCase()
      .replace(/\s+/g, "");

  const EXTRACT_PRESETS = {
    /** Keep only letters, digits, and hyphens. */
    alnum(raw) {
      return String(raw || "").replace(/[^A-Za-z0-9-]/g, "");
    },
    /** Last path segment when the scan is a URL. */
    "url-path"(raw) {
      try {
        const parts = new URL(String(raw || "").trim()).pathname.split("/").filter(Boolean);
        return parts.at(-1) || "";
      } catch (_) {
        return "";
      }
    },
    /** Text after the last colon (e.g. SN:ABC123 → ABC123). */
    "after-colon"(raw) {
      const text = String(raw || "").trim();
      const idx = text.lastIndexOf(":");
      return idx >= 0 ? text.slice(idx + 1) : text;
    },
    /** GS1 application identifier 21 (serial number). */
    "gs1-21"(raw) {
      const text = String(raw || "");
      const match =
        text.match(/\(21\)([^(\s]+)/i) ||
        text.match(/(?:^|[^0-9])21([A-Z0-9-]{1,20})(?:[^A-Z0-9-]|$)/i);
      return match ? match[1] : "";
    },
    /**
     * GPON/XPON device labels with PROD ID, MAC, and SN barcodes — SN only.
     * Accepts SN:48575443A9F07783 or bare 13–24 hex chars; rejects MAC/PROD ID.
     */
    "gpon-sn"(raw) {
      const source = String(raw || "").trim();
      const text = normalizeSerial(raw);
      if (!text) return "";

      if (/^MAC[:=\s]/i.test(source)) return "";
      if (/PROD\s*ID|\(1P\)|P\/N:/i.test(source)) return "";

      const prefixed = text.match(/^SN[:=]?([A-F0-9]{8,24})$/);
      if (prefixed) return prefixed[1];

      // Bare SN barcode (13+ hex — excludes typical 12-char MAC).
      if (/^[A-F0-9]{13,24}$/.test(text)) return text;

      return "";
    },
  };

  const getExtractRule = (input) => {
    if (input instanceof HTMLInputElement) {
      const own = input.dataset.serialScanExtract?.trim();
      if (own) return own;
    }
    const scoped =
      input?.closest?.("[data-serial-scan-extract]")?.dataset?.serialScanExtract?.trim() ||
      "";
    if (scoped) return scoped;
    return (
      document.currentScript?.dataset?.serialScanExtract?.trim() ||
      document
        .querySelector("script[src*='serial-scan.js'][data-serial-scan-extract]")
        ?.dataset?.serialScanExtract?.trim() ||
      ""
    );
  };

  const extractSerial = (raw, input) => {
    const rule = getExtractRule(input);
    const normalized = normalizeSerial(raw);

    if (typeof customExtractor === "function") {
      const custom = customExtractor(raw, normalized, input, rule);
      if (custom != null) return normalizeSerial(custom);
    }

    if (!rule) return normalized;

    const preset = EXTRACT_PRESETS[rule.toLowerCase()];
    if (preset) return normalizeSerial(preset(raw));

    try {
      const match =
        new RegExp(rule, "i").exec(String(raw || "")) ||
        new RegExp(rule, "i").exec(normalized);
      if (!match) return "";
      return normalizeSerial(match[1] ?? match[0]);
    } catch (_) {
      return normalized;
    }
  };

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

  const getBarcodeDetector = () => {
    if (!("BarcodeDetector" in window)) return null;
    if (barcodeDetector) return barcodeDetector;
    try {
      barcodeDetector = new window.BarcodeDetector({ formats: BARCODE_FORMATS });
      return barcodeDetector;
    } catch (_) {
      try {
        barcodeDetector = new window.BarcodeDetector();
        return barcodeDetector;
      } catch (_) {
        return null;
      }
    }
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

  const stopNativeLoop = () => {
    nativeLoopId += 1;
    nativeDetecting = false;
    if (nativeVideo) {
      try {
        nativeVideo.pause();
      } catch (_) {
        /* ignore */
      }
      nativeVideo.srcObject = null;
      nativeVideo.remove();
      nativeVideo = null;
    }
  };

  const stopCamera = async () => {
    stopNativeLoop();
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
    const input = target || activeTarget;
    if (!input || scanLocked) return false;

    const rule = getExtractRule(input);
    const serial = extractSerial(raw, input);
    if (!serial) {
      setStatus(
        rule === "gpon-sn"
          ? "Scan the SN barcode — not MAC or PROD ID"
          : rule
            ? "No serial number found in that scan"
            : "Empty scan — try again",
        { error: true }
      );
      return false;
    }

    scanLocked = true;
    input.value = serial;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.dispatchEvent(
      new CustomEvent("myshop:serial-applied", {
        bubbles: true,
        detail: { serial, source: "scan" },
      })
    );

    try {
      input.focus({ preventScroll: true });
    } catch (_) {
      input.focus?.();
    }

    setStatus(`Scanned: ${serial}`);
    window.setTimeout(() => {
      closeModal().finally(() => {
        scanLocked = false;
      });
    }, 120);
    return true;
  };

  const decodeWithBarcodeDetector = async (source) => {
    const detector = getBarcodeDetector();
    if (!detector) return null;
    try {
      const codes = await detector.detect(source);
      const raw = codes?.[0]?.rawValue;
      return raw ? extractSerial(raw, activeTarget) : null;
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
      const decoded = await fileScanner.scanFile(file, /* showImage= */ false);
      try {
        fileScanner.clear();
      } catch (_) {
        /* ignore */
      }
      return extractSerial(decoded, activeTarget);
    } finally {
      host.remove();
    }
  };

  const onScanSuccess = (decodedText) => {
    applySerial(decodedText);
  };

  const videoConstraints = () => ({
    audio: false,
    video: {
      facingMode: { ideal: "environment" },
      width: { ideal: 1280 },
      height: { ideal: 720 },
      frameRate: { ideal: 30, max: 30 },
    },
  });

  const buildScanGuide = () => {
    const guide = document.createElement("div");
    guide.className = "serial-scan-guide";
    guide.setAttribute("aria-hidden", "true");
    return guide;
  };

  const startNativeCamera = async () => {
    const detector = getBarcodeDetector();
    if (!detector || !readerEl || !navigator.mediaDevices?.getUserMedia) {
      throw new Error("Native scanner unavailable");
    }

    setStatus("Starting camera…");
    await stopCamera();

    stream = await navigator.mediaDevices.getUserMedia(videoConstraints());
    const video = document.createElement("video");
    video.className = "serial-scan-video";
    video.setAttribute("playsinline", "");
    video.setAttribute("muted", "");
    video.muted = true;
    video.autoplay = true;
    video.srcObject = stream;

    readerEl.innerHTML = "";
    readerEl.append(video, buildScanGuide());
    nativeVideo = video;

    await video.play();

    // Continuous autofocus when the device supports it.
    try {
      const track = stream.getVideoTracks()?.[0];
      const caps = track?.getCapabilities?.() || {};
      if (caps.focusMode?.includes?.("continuous")) {
        await track.applyConstraints({
          advanced: [{ focusMode: "continuous" }],
        });
      }
    } catch (_) {
      /* ignore */
    }

    const loopToken = ++nativeLoopId;
    nativeDetecting = false;
    setStatus("Point at a barcode or QR code");

    const tick = async () => {
      if (loopToken !== nativeLoopId || scanLocked) return;
      if (!video || video.readyState < 2) {
        window.setTimeout(tick, 40);
        return;
      }
      if (!nativeDetecting) {
        nativeDetecting = true;
        try {
          const codes = await detector.detect(video);
          const raw = codes?.[0]?.rawValue;
          if (raw && loopToken === nativeLoopId) {
            onScanSuccess(raw);
            return;
          }
        } catch (_) {
          /* keep looping */
        } finally {
          nativeDetecting = false;
        }
      }
      // ~25 detect attempts/sec without stacking awaits.
      window.setTimeout(tick, 40);
    };

    tick();
  };

  const html5Formats = () => {
    const Supported = window.Html5QrcodeSupportedFormats;
    if (!Supported) return undefined;
    return [
      Supported.CODE_128,
      Supported.CODE_39,
      Supported.CODE_93,
      Supported.EAN_13,
      Supported.EAN_8,
      Supported.UPC_A,
      Supported.UPC_E,
      Supported.ITF,
      Supported.CODABAR,
      Supported.QR_CODE,
      Supported.DATA_MATRIX,
    ].filter((value) => value != null);
  };

  const startHtml5Camera = async () => {
    setStatus("Starting camera…");
    await stopCamera();
    if (readerEl) readerEl.innerHTML = "";

    const Html5Qrcode = await ensureLib();
    if (!Html5Qrcode || !readerEl) {
      throw new Error("Scanner not ready");
    }

    const formats = html5Formats();
    html5Scanner = new Html5Qrcode(readerEl.id, {
      verbose: false,
      ...(formats ? { formatsToSupport: formats } : {}),
    });

    // Wide rectangular box fits 1D barcodes much better than a square.
    const config = {
      fps: 24,
      qrbox: (viewfinderWidth, viewfinderHeight) => {
        const width = Math.floor(Math.min(viewfinderWidth * 0.92, viewfinderWidth - 16));
        const height = Math.floor(
          Math.min(
            Math.max(viewfinderHeight * 0.28, 110),
            viewfinderHeight * 0.42,
            width * 0.45
          )
        );
        return {
          width: Math.max(180, width),
          height: Math.max(90, height),
        };
      },
      disableFlip: false,
      experimentalFeatures: { useBarCodeDetectorIfSupported: true },
      videoConstraints: {
        facingMode: { ideal: "environment" },
        width: { ideal: 1280 },
        height: { ideal: 720 },
        frameRate: { ideal: 30, max: 30 },
      },
    };

    await html5Scanner.start(
      { facingMode: "environment" },
      config,
      (text) => onScanSuccess(text),
      () => {
        /* ignore frame miss */
      }
    );
    setStatus("Point at a barcode or QR code");
  };

  const startCamera = async () => {
    scanLocked = false;
    if (getBarcodeDetector()) {
      try {
        await startNativeCamera();
        return;
      } catch (err) {
        // Fall through to html5-qrcode if native path fails (permission, etc.).
        if (/NotAllowed|Permission|secure/i.test(String(err?.name || err?.message || ""))) {
          throw err;
        }
      }
    }
    await startHtml5Camera();
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
          Supports QR and barcodes. Hold steady — capture is continuous.
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
    scanLocked = false;
    ensureModal();
    // Keep above any open workspace / buy-stock modal stacking context.
    document.body.appendChild(modal);
    modal.style.zIndex = "220";
    modal.classList.add("is-open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("serial-scan-open");
    setStatus("Starting camera…");
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
    // Warm native detector + preload library so first scan is snappy.
    getBarcodeDetector();
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(() => {
        ensureLib().catch(() => {});
      }, { timeout: 2500 });
    } else {
      window.setTimeout(() => ensureLib().catch(() => {}), 900);
    }
  };

  // Public hooks for tests / manual use.
  window.MyShopSerialScan = {
    enhance: scanTree,
    open: openModal,
    close: closeModal,
    normalize: normalizeSerial,
    extract: extractSerial,
    setExtractor(fn) {
      customExtractor = typeof fn === "function" ? fn : null;
    },
    apply: applySerial,
    decodeImageFile,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
