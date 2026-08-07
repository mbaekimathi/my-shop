/**
 * Pure session helpers for Richcom printer persistence.
 * Browser: attached to window.RichcomPrinterSession
 * Node: module.exports / named exports via dynamic import of this file as .cjs twin.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.RichcomPrinterSession = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : undefined, function () {
  const CHANNELS = ["bluetooth", "usb", "wifi"];

  const normalizeStore = (raw) => {
    const store = raw && typeof raw === "object" && !Array.isArray(raw) ? { ...raw } : {};
    const channel = String(store.activeChannel || "").trim();
    if (channel && CHANNELS.includes(channel)) {
      store.activeChannel = channel;
    } else {
      delete store.activeChannel;
    }
    if (typeof store.wantConnected !== "boolean") {
      store.wantConnected = Boolean(store.activeChannel);
    }
    if (store.wifi && typeof store.wifi === "object") {
      const host = String(store.wifi.host || "").trim();
      const port = Number(store.wifi.port || 9100);
      store.wifi = {
        host,
        port: Number.isFinite(port) && port > 0 && port <= 65535 ? port : 9100,
      };
      if (!store.wifi.host) delete store.wifi;
    }
    if (store.usb && typeof store.usb === "object") {
      const printMode = store.usb.printMode === "raw" ? "raw" : "system";
      const baudRate = Number(store.usb.baudRate || 9600);
      store.usb = {
        printMode,
        baudRate: Number.isFinite(baudRate) && baudRate > 0 ? baudRate : 9600,
        windowsPrinterName: String(store.usb.windowsPrinterName || "").trim(),
        vendorId: store.usb.vendorId != null ? Number(store.usb.vendorId) : undefined,
        productId: store.usb.productId != null ? Number(store.usb.productId) : undefined,
      };
    }
    if (store.bluetooth && typeof store.bluetooth === "object") {
      const deviceId = String(store.bluetooth.deviceId || "").trim();
      if (deviceId) {
        store.bluetooth = {
          deviceId,
          name: String(store.bluetooth.name || "").trim(),
        };
      } else {
        delete store.bluetooth;
      }
    }
    return store;
  };

  const markConnected = (raw, channel, name, extras = {}) => {
    const store = normalizeStore(raw);
    if (!CHANNELS.includes(channel)) {
      return store;
    }
    store.activeChannel = channel;
    store.name = String(name || channel);
    store.wantConnected = true;
    delete store.transportLostAt;
    if (extras.wifi) store.wifi = extras.wifi;
    if (extras.usb) store.usb = { ...(store.usb || {}), ...extras.usb };
    if (extras.bluetooth) store.bluetooth = extras.bluetooth;
    return normalizeStore(store);
  };

  const markExplicitDisconnect = (raw) => {
    const store = normalizeStore(raw);
    delete store.activeChannel;
    delete store.name;
    delete store.transportLostAt;
    store.wantConnected = false;
    return store;
  };

  const markTransportLost = (raw, channel) => {
    const store = normalizeStore(raw);
    if (!store.wantConnected) return store;
    if (store.activeChannel && channel && store.activeChannel !== channel) {
      return store;
    }
    store.transportLostAt = Date.now();
    // Keep activeChannel + wantConnected so refresh / auto-reconnect can recover.
    return store;
  };

  const shouldAttemptRestore = (raw, enabledChannels) => {
    const store = normalizeStore(raw);
    if (!store.wantConnected || !store.activeChannel) return null;
    if (Array.isArray(enabledChannels) && enabledChannels.length) {
      if (!enabledChannels.includes(store.activeChannel)) return null;
    }
    return store.activeChannel;
  };

  const shouldClearStoreOnTransportLost = (explicitDisconnect, pageUnloading) => {
    // Never wipe persisted session on unexpected drops or page refresh.
    if (explicitDisconnect) return true;
    if (pageUnloading) return false;
    return false;
  };

  const restorePlan = (raw, enabledChannels) => {
    const store = normalizeStore(raw);
    const channel = shouldAttemptRestore(store, enabledChannels);
    if (!channel) {
      return { action: "none", store, channel: "" };
    }
    if (channel === "wifi") {
      if (!store.wifi?.host) {
        return { action: "needs_setup", store, channel };
      }
      return {
        action: "reconnect",
        store,
        channel,
        target: { host: store.wifi.host, port: store.wifi.port || 9100 },
      };
    }
    if (channel === "usb") {
      const printMode = store.usb?.printMode === "raw" ? "raw" : "system";
      if (printMode === "system") {
        return {
          action: "restore_local",
          store,
          channel,
          target: {
            kind: "system",
            name: store.usb?.windowsPrinterName || store.name || "Windows USB printer",
          },
        };
      }
      return {
        action: "reconnect",
        store,
        channel,
        target: {
          kind: "serial",
          baudRate: store.usb?.baudRate || 9600,
          vendorId: store.usb?.vendorId,
          productId: store.usb?.productId,
        },
      };
    }
    if (channel === "bluetooth") {
      if (!store.bluetooth?.deviceId) {
        return { action: "needs_setup", store, channel };
      }
      return {
        action: "reconnect",
        store,
        channel,
        target: {
          deviceId: store.bluetooth.deviceId,
          name: store.bluetooth.name || store.name || "Bluetooth printer",
        },
      };
    }
    return { action: "none", store, channel: "" };
  };

  return {
    CHANNELS,
    normalizeStore,
    markConnected,
    markExplicitDisconnect,
    markTransportLost,
    shouldAttemptRestore,
    shouldClearStoreOnTransportLost,
    restorePlan,
  };
});
