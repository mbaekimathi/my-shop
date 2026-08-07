/**
 * Test loops for printer session persistence / reconnect planning.
 * Run: node --test static/js/tests/shop-printer-session.test.mjs
 */
import { createRequire } from "node:module";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const sessionPath = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "shop-printer-session.js"
);
const S = require(sessionPath);

const simulateRefresh = (store) => S.normalizeStore(JSON.parse(JSON.stringify(store)));

describe("printer session persistence", () => {
  it("normalizeStore treats missing wantConnected as true when channel set", () => {
    for (let i = 0; i < 8; i++) {
      const store = S.normalizeStore({
        activeChannel: "wifi",
        wifi: { host: "10.0.0.5", port: 9100 },
      });
      assert.equal(store.wantConnected, true);
      assert.equal(store.activeChannel, "wifi");
    }
  });

  it("explicit disconnect clears intent and survives refresh loops", () => {
    let store = S.markConnected({}, "usb", "Windows USB printer", {
      usb: { printMode: "system", windowsPrinterName: "POS-80" },
    });
    assert.equal(store.wantConnected, true);
    for (let i = 0; i < 12; i++) {
      store = S.markExplicitDisconnect(store);
      assert.equal(store.wantConnected, false);
      assert.equal(store.activeChannel, undefined);
      store = simulateRefresh(store);
      assert.equal(S.shouldAttemptRestore(store, ["usb", "wifi"]), null);
    }
  });

  it("transport lost does not clear wantConnected (refresh-safe)", () => {
    let store = S.markConnected({}, "bluetooth", "BT Printer", {
      bluetooth: { deviceId: "dev-1", name: "BT Printer" },
    });
    for (let i = 0; i < 20; i++) {
      store = S.markTransportLost(store, "bluetooth");
      assert.equal(store.wantConnected, true);
      assert.equal(store.activeChannel, "bluetooth");
      assert.ok(store.transportLostAt);
      assert.equal(S.shouldClearStoreOnTransportLost(false, i % 2 === 0), false);
      assert.equal(S.shouldClearStoreOnTransportLost(true, false), true);
      store = simulateRefresh(store);
      assert.equal(S.shouldAttemptRestore(store, ["bluetooth", "usb", "wifi"]), "bluetooth");
    }
  });

  it("page refresh loop keeps wifi session until explicit disconnect", () => {
    let store = S.markConnected({}, "wifi", "Wi‑Fi printer 192.168.100.171:9100", {
      wifi: { host: "192.168.100.171", port: 9100 },
    });
    for (let cycle = 0; cycle < 25; cycle++) {
      // Simulate GATT/page unload noise that used to wipe localStorage.
      store = S.markTransportLost(store, "wifi");
      store = simulateRefresh(store);
      const plan = S.restorePlan(store, ["bluetooth", "usb", "wifi"]);
      assert.equal(plan.action, "reconnect");
      assert.equal(plan.target.host, "192.168.100.171");
      assert.equal(store.wantConnected, true);
    }
    store = S.markExplicitDisconnect(store);
    store = simulateRefresh(store);
    assert.equal(S.restorePlan(store, ["wifi"]).action, "none");
  });

  it("usb system restore_local works after many refresh cycles", () => {
    let store = S.markConnected({}, "usb", "POS-80C", {
      usb: { printMode: "system", windowsPrinterName: "POS-80C" },
    });
    for (let i = 0; i < 30; i++) {
      store = simulateRefresh(store);
      const plan = S.restorePlan(store, ["usb", "wifi"]);
      assert.equal(plan.action, "restore_local");
      assert.equal(plan.target.kind, "system");
      store = S.markConnected(store, "usb", plan.target.name, {
        usb: { printMode: "system", windowsPrinterName: plan.target.name },
      });
      assert.equal(store.wantConnected, true);
    }
  });

  it("restorePlan wifi reconnect loops", () => {
    for (const host of ["192.168.1.10", "192.168.100.171", "10.0.0.8"]) {
      const store = S.markConnected({}, "wifi", `Wi‑Fi ${host}`, {
        wifi: { host, port: 9100 },
      });
      const plan = S.restorePlan(store, ["wifi", "usb"]);
      assert.equal(plan.action, "reconnect");
      assert.equal(plan.channel, "wifi");
      assert.equal(plan.target.host, host);
      assert.equal(plan.target.port, 9100);
    }
  });

  it("restorePlan usb system is local restore (no hardware session)", () => {
    for (let i = 0; i < 10; i++) {
      const store = S.markConnected({}, "usb", "POS", {
        usb: { printMode: "system", windowsPrinterName: "POS-80C" },
      });
      const plan = S.restorePlan(JSON.parse(JSON.stringify(store)), ["usb"]);
      assert.equal(plan.action, "restore_local");
      assert.equal(plan.target.kind, "system");
      assert.match(plan.target.name, /POS/);
    }
  });

  it("restorePlan usb raw requests reconnect", () => {
    const store = S.markConnected({}, "usb", "COM", {
      usb: { printMode: "raw", baudRate: 115200, vendorId: 1234, productId: 5678 },
    });
    const plan = S.restorePlan(store, ["usb"]);
    assert.equal(plan.action, "reconnect");
    assert.equal(plan.target.kind, "serial");
    assert.equal(plan.target.baudRate, 115200);
  });

  it("restorePlan bluetooth needs device id", () => {
    assert.equal(
      S.restorePlan({ activeChannel: "bluetooth", wantConnected: true }, ["bluetooth"])
        .action,
      "needs_setup"
    );
    const store = S.markConnected({}, "bluetooth", "X", {
      bluetooth: { deviceId: "abc", name: "X" },
    });
    const plan = S.restorePlan(store, ["bluetooth"]);
    assert.equal(plan.action, "reconnect");
    assert.equal(plan.target.deviceId, "abc");
  });

  it("disabled channel blocks restore", () => {
    const store = S.markConnected({}, "wifi", "W", {
      wifi: { host: "1.2.3.4", port: 9100 },
    });
    assert.equal(S.shouldAttemptRestore(store, ["usb"]), null);
    assert.equal(S.restorePlan(store, ["usb"]).action, "none");
  });

  it("markConnected overwrites channel across switch loops", () => {
    let store = {};
    const sequence = ["wifi", "usb", "bluetooth", "wifi", "usb"];
    for (const channel of sequence) {
      store = S.markConnected(store, channel, channel, {
        wifi: { host: "192.168.0.2", port: 9100 },
        usb: { printMode: "system", windowsPrinterName: "P" },
        bluetooth: { deviceId: "bt-1", name: "B" },
      });
      assert.equal(store.activeChannel, channel);
      assert.equal(store.wantConnected, true);
      assert.equal(S.shouldAttemptRestore(store, ["bluetooth", "usb", "wifi"]), channel);
    }
  });

  it("corrects until ok: random connect/drop/refresh/disconnect stress", () => {
    const channels = ["wifi", "usb", "bluetooth"];
    let store = {};
    for (let i = 0; i < 80; i++) {
      const channel = channels[i % 3];
      const extras =
        channel === "wifi"
          ? { wifi: { host: `192.168.1.${(i % 200) + 1}`, port: 9100 } }
          : channel === "usb"
            ? {
                usb: {
                  printMode: i % 2 ? "raw" : "system",
                  baudRate: 9600,
                  windowsPrinterName: "P",
                },
              }
            : { bluetooth: { deviceId: `bt-${i}`, name: `BT ${i}` } };
      store = S.markConnected(store, channel, `${channel}-${i}`, extras);
      if (i % 4 === 0) store = S.markTransportLost(store, channel);
      store = simulateRefresh(store);
      assert.equal(store.wantConnected, true);
      assert.equal(S.shouldAttemptRestore(store, channels), channel);
      if (i % 11 === 10) {
        store = S.markExplicitDisconnect(store);
        store = simulateRefresh(store);
        assert.equal(S.shouldAttemptRestore(store, channels), null);
        store = S.markConnected(store, channel, `${channel}-again`, extras);
      }
    }
    assert.equal(store.wantConnected, true);
  });
});
