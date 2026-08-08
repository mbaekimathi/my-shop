/**
 * WhatsApp Web bridge for MY-SHOP.
 * Local VPS: WHATSAPP_BRIDGE_HOST=127.0.0.1 (default)
 * Remote (cPanel Django → bridge on VPS/PC): HOST=0.0.0.0 + SECRET required
 *
 * GET  /health
 * GET  /status
 * GET  /inbound — live-scan recent 1:1 client replies
 * GET  /contacts
 * POST /send  — text and/or mediaPath or mediaBase64
 * POST /logout
 */

const path = require("path");
const fs = require("fs");
const express = require("express");
const qrcode = require("qrcode");
const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");

const PORT = Number(process.env.WHATSAPP_BRIDGE_PORT || 3100);
const SECRET = (process.env.WHATSAPP_BRIDGE_SECRET || "").trim();
const HOST = (process.env.WHATSAPP_BRIDGE_HOST || "127.0.0.1").trim() || "127.0.0.1";
const AUTH_DIR = path.join(__dirname, ".wwebjs_auth");
const IS_REMOTE_BIND =
  HOST !== "127.0.0.1" && HOST !== "localhost" && HOST !== "::1";

if (IS_REMOTE_BIND && !SECRET) {
  console.error(
    "[bridge] WHATSAPP_BRIDGE_SECRET is required when WHATSAPP_BRIDGE_HOST is not localhost."
  );
  process.exit(1);
}

const state = {
  status: "disconnected",
  qrDataUrl: "",
  phone: "",
  error: "",
  ready: false,
};

/** Recent inbound 1:1 replies (not from us). Newest last. */
const inboundReplies = [];
const MAX_INBOUND = 500;
let unreadPollBusy = false;

function pushInbound(entry) {
  if (!entry?.id) return;
  if (inboundReplies.some((row) => row.id === entry.id)) return;
  inboundReplies.push(entry);
  if (inboundReplies.length > MAX_INBOUND) {
    inboundReplies.splice(0, inboundReplies.length - MAX_INBOUND);
  }
  console.log(
    "[bridge] Inbound reply saved:",
    entry.phone || entry.chatId,
    String(entry.body || "").slice(0, 80)
  );
}

async function resolveInboundPhone(msg, chatId) {
  let phone = digitsPhone(chatId);
  if (phone) return phone;
  try {
    const contact = await msg.getContact();
    phone = digitsPhone(
      contact?.number || contact?.id?.user || contact?.id?._serialized || ""
    );
    if (phone) return phone;
  } catch (_) {
    /* ignore */
  }
  try {
    const chat = await msg.getChat();
    phone = digitsPhone(chat?.id?.user || chat?.id?._serialized || "");
  } catch (_) {
    /* ignore */
  }
  return phone || "";
}

async function captureInbound(msg) {
  if (!msg || msg.fromMe) return false;
  if (msg.isStatus || msg.broadcast) return false;

  const chatId = String(msg.from || msg.author || "");
  if (!chatId || chatId.endsWith("@g.us") || chatId.includes("broadcast")) {
    return false;
  }

  const phone = await resolveInboundPhone(msg, chatId);
  let name = "";
  try {
    const contact = await msg.getContact();
    name = String(
      contact?.pushname || contact?.name || contact?.shortName || contact?.number || ""
    ).trim();
  } catch (_) {
    /* ignore contact lookup failures */
  }

  let body = String(msg.body || "").trim();
  if (!body && msg.hasMedia) body = "[media]";
  if (!body) body = "[message]";

  const id =
    msg.id?._serialized ||
    `${chatId}:${msg.timestamp || Date.now()}:${body.slice(0, 24)}`;

  // Keep chat id even when phone is missing (LID accounts).
  pushInbound({
    id,
    chatId,
    phone: phone || digitsPhone(chatId) || String(chatId).replace(/@.*/, ""),
    name,
    body: body.slice(0, 4000),
    timestamp: Number(msg.timestamp || 0) * 1000 || Date.now(),
  });
  return true;
}

async function pollUnreadReplies() {
  if (!state.ready || !client.pupPage || unreadPollBusy) return;
  unreadPollBusy = true;
  try {
    const rows = await withTimeout(
      client.pupPage.evaluate(() => {
        function modelsOf(col) {
          if (!col) return [];
          if (typeof col.getModelsArray === "function") return col.getModelsArray();
          if (col._models) return Object.values(col._models);
          if (typeof col.getModels === "function") return col.getModels();
          return [];
        }
        function serializeId(id) {
          if (!id) return "";
          if (typeof id === "string") return id;
          if (id._serialized) return id._serialized;
          if (id.user && id.server) return `${id.user}@${id.server}`;
          return String(id.user || "");
        }
        const collections =
          (window.require && window.require("WAWebCollections")) || {};
        const Chat = window.Store?.Chat || collections.Chat;
        const chats = modelsOf(Chat);
        const now = Math.floor(Date.now() / 1000);
        const WINDOW_SEC = 7 * 24 * 3600;
        const out = [];

        for (let i = 0; i < chats.length; i++) {
          const ch = chats[i];
          if (!ch || ch.archive) continue;
          const chatId = serializeId(ch.id);
          if (
            !chatId ||
            chatId.indexOf("@g.us") !== -1 ||
            chatId.indexOf("broadcast") !== -1 ||
            chatId === "status@broadcast"
          ) {
            continue;
          }
          if (ch.isGroup || chatId.indexOf("@g.us") !== -1) continue;

          const unread = Number(ch.unreadCount || 0);
          const msgs = ch.msgs;
          const arr =
            msgs && typeof msgs.getModelsArray === "function"
              ? msgs.getModelsArray() || []
              : [];
          const chatStamp = Number(ch.t || ch.timestamp || 0);
          const chatRecent = chatStamp > 0 && now - chatStamp < WINDOW_SEC;
          if (unread <= 0 && !chatRecent && !arr.length) continue;

          // Live pull: recent inbound messages from this chat (not only unread).
          const take = Math.min(Math.max(unread || 6, 6), 12);
          let found = 0;
          for (let j = arr.length - 1; j >= 0 && found < take; j--) {
            const m = arr[j];
            if (!m || m.id?.fromMe || m.fromMe) continue;
            const stamp = Number(m.t || 0);
            if (stamp > 0 && now - stamp > WINDOW_SEC) break;
            const mBody = String(m.body || m.caption || m.text || "").trim();
            out.push({
              id:
                m.id?._serialized ||
                `${chatId}:${m.t || stamp}:${(mBody || "m").slice(0, 20)}`,
              chatId,
              phone: chatId.indexOf("@c.us") !== -1 ? chatId.split("@")[0] : "",
              name: String(
                ch.name || ch.formattedTitle || ch.pushname || ""
              ).trim(),
              body: mBody || (m.isMedia || m.hasMedia ? "[media]" : "[message]"),
              timestamp: Number(m.t || stamp || 0) * 1000 || Date.now(),
            });
            found += 1;
          }
        }
        return out;
      }),
      25000,
      "inbound poll"
    );

    for (const row of rows || []) {
      if (!row?.id) continue;
      pushInbound({
        id: row.id,
        chatId: row.chatId || "",
        phone: row.phone || digitsPhone(row.chatId) || String(row.chatId || "").replace(/@.*/, ""),
        name: row.name || "",
        body: row.body || "[message]",
        timestamp: Number(row.timestamp || Date.now()),
      });
    }
  } catch (err) {
    console.warn(
      "[bridge] live inbound poll failed:",
      String((err && err.message) || err)
    );
  } finally {
    unreadPollBusy = false;
  }
}

function requireSecret(req, res, next) {
  if (!SECRET) return next();
  const provided = (req.get("X-Bridge-Secret") || "").trim();
  if (provided !== SECRET) {
    return res.status(401).json({ ok: false, error: "Unauthorized" });
  }
  return next();
}

function digitsPhone(raw) {
  let digits = String(raw || "").replace(/\D+/g, "");
  if (digits.startsWith("00")) digits = digits.slice(2);
  if (digits.startsWith("0") && digits.length === 10) {
    digits = `254${digits.slice(1)}`;
  } else if ((digits.startsWith("7") || digits.startsWith("1")) && digits.length === 9) {
    digits = `254${digits}`;
  }
  return digits;
}

function withTimeout(promise, ms, label) {
  let timer;
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(`${label || "operation"} timed out after ${ms}ms`)),
        ms
      );
    }),
  ]);
}

async function resolveChatId({ phone, chatId }) {
  const rawChat = String(chatId || "").trim();
  if (rawChat.includes("@g.us") || rawChat.includes("@lid")) {
    return rawChat;
  }
  if (rawChat.includes("@c.us")) {
    // Prefer live lookup so LID-linked accounts resolve correctly.
    const digits = digitsPhone(rawChat);
    if (digits && state.ready) {
      try {
        const wid = await client.getNumberId(digits);
        if (wid?._serialized) return wid._serialized;
      } catch (_) {
        /* fall through */
      }
    }
    return rawChat;
  }

  const digits = digitsPhone(phone || rawChat);
  if (!digits || digits.length < 10) return "";
  if (state.ready) {
    try {
      const wid = await client.getNumberId(digits);
      if (wid?._serialized) return wid._serialized;
    } catch (err) {
      console.warn("[bridge] getNumberId failed for", digits, err.message || err);
    }
  }
  return `${digits}@c.us`;
}

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: AUTH_DIR }),
  puppeteer: {
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-gpu",
    ],
  },
});

client.on("qr", async (qr) => {
  try {
    state.qrDataUrl = await qrcode.toDataURL(qr);
    state.status = "qr_pending";
    state.phone = "";
    state.error = "";
    state.ready = false;
    console.log("[bridge] QR pending — scan from WhatsApp UI");
  } catch (err) {
    state.status = "disconnected";
    state.error = err.message || String(err);
    console.error("[bridge] QR encode failed", err);
  }
});

client.on("authenticated", () => {
  state.error = "";
  console.log("[bridge] Authenticated");
});

client.on("ready", async () => {
  state.status = "connected";
  state.qrDataUrl = "";
  state.error = "";
  state.ready = true;
  try {
    const wid = client.info?.wid?._serialized || client.info?.wid?.user || "";
    state.phone = String(wid).replace(/@.*/, "") || "";
  } catch (_) {
    state.phone = "";
  }
  console.log("[bridge] Ready as", state.phone || "(unknown)");
  // Seed any currently unread 1:1 chats into the inbound queue.
  setTimeout(() => {
    pollUnreadReplies().catch(() => {});
  }, 2500);
});

client.on("auth_failure", (msg) => {
  state.status = "disconnected";
  state.ready = false;
  state.qrDataUrl = "";
  state.phone = "";
  state.error = String(msg || "Authentication failure");
  console.error("[bridge] Auth failure", msg);
});

client.on("disconnected", (reason) => {
  state.status = "disconnected";
  state.ready = false;
  state.qrDataUrl = "";
  state.phone = "";
  state.error = String(reason || "Disconnected");
  console.warn("[bridge] Disconnected", reason);
  setTimeout(() => {
    client.initialize().catch((err) => {
      state.error = err.message || String(err);
      console.error("[bridge] Re-init failed", err);
    });
  }, 2000);
});

client.on("message", async (msg) => {
  try {
    await captureInbound(msg);
  } catch (err) {
    console.warn("[bridge] inbound message handler", err.message || err);
  }
});

// Broader catch — some WA Web builds skip the plain "message" event.
client.on("message_create", async (msg) => {
  try {
    await captureInbound(msg);
  } catch (err) {
    console.warn("[bridge] inbound message_create handler", err.message || err);
  }
});

setInterval(() => {
  pollUnreadReplies().catch(() => {});
}, 7000);

const app = express();
app.use(express.json({ limit: "2mb" }));
app.use(requireSecret);

app.get("/debug-contacts-sample", async (_req, res) => {
  if (!state.ready || !client.pupPage) {
    return res.status(409).json({ ok: false, error: "not ready" });
  }
  try {
    const sample = await client.pupPage.evaluate(() => {
      function modelsOf(col) {
        if (!col) return [];
        if (typeof col.getModelsArray === "function") {
          try {
            return col.getModelsArray() || [];
          } catch (_) {}
        }
        if (Array.isArray(col._models)) return col._models;
        if (Array.isArray(col.models)) return col.models;
        return [];
      }
      let Contact = null;
      try {
        Contact = window.require("WAWebCollections").Contact;
      } catch (_) {}
      const book = modelsOf(Contact);
      const out = [];
      for (let i = 0; i < book.length && out.length < 40; i++) {
        const c = book[i];
        if (!c || c.isMe || c.isGroup) continue;
        out.push({
          keys: Object.keys(c).slice(0, 50),
          id: c.id && c.id._serialized,
          server: c.id && c.id.server,
          name: c.name || null,
          pushname: c.pushname || null,
          shortName: c.shortName || null,
          verifiedName: c.verifiedName || null,
          formattedName: c.formattedName || null,
          isMyContact: c.isMyContact,
          isContact: c.isContact,
          isAddressBookContact: c.isAddressBookContact,
          isBusiness: c.isBusiness,
          number: c.number || null,
          phoneNumber: c.phoneNumber
            ? c.phoneNumber._serialized || c.phoneNumber.user || null
            : null,
          type: c.type || null,
        });
      }
      const withName = book.filter(
        (c) => c && !c.isMe && !c.isGroup && (c.name || c.verifiedName)
      ).length;
      const addressBook = book.filter(
        (c) => c && !c.isMe && !c.isGroup && Number(c.isAddressBookContact) === 1
      ).length;
      const myContactTrue = book.filter((c) => c && c.isMyContact === true).length;
      const myContactTruthy = book.filter((c) => c && c.isMyContact).length;
      return {
        total: book.length,
        withName,
        addressBook,
        myContactTrue,
        myContactTruthy,
        sample: out,
      };
    });
    return res.json({ ok: true, sample });
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

app.get("/status", (_req, res) => {
  res.json({
    ok: true,
    state: state.status,
    qr: state.qrDataUrl || undefined,
    qr_data_url: state.qrDataUrl || undefined,
    phone: state.phone || undefined,
    error: state.error || undefined,
    inbound_count: inboundReplies.length,
  });
});

app.get("/inbound", async (req, res) => {
  // Always refresh from the live WhatsApp Store when connected.
  if (state.ready) {
    try {
      await pollUnreadReplies();
    } catch (_) {
      /* return whatever we already have */
    }
  }
  const since = Number(req.query.since || 0);
  const items = since
    ? inboundReplies.filter((row) => Number(row.timestamp || 0) > since)
    : inboundReplies.slice();
  return res.json({
    ok: true,
    live: true,
    count: items.length,
    items,
  });
});

app.post("/inbound/scan", async (_req, res) => {
  if (!state.ready) {
    return res.status(409).json({ ok: false, error: "not ready" });
  }
  try {
    await pollUnreadReplies();
    return res.json({
      ok: true,
      live: true,
      count: inboundReplies.length,
      items: inboundReplies.slice(-50),
    });
  } catch (err) {
    return res.status(500).json({
      ok: false,
      error: err.message || String(err),
    });
  }
});

/**
 * Load contacts/groups as they appear in the live WhatsApp chat list
 * (recent activity, not archived) — not the full historical address book.
 */
async function loadContactsFromStore(q = "") {
  const page = client.pupPage;
  if (!page) {
    throw new Error("WhatsApp page is not ready yet. Wait a few seconds and try again.");
  }

  const raw = await page.evaluate(() => {
    const result = { contacts: [], groups: [], error: "", stats: {} };
    try {
      function modelsOf(col) {
        if (!col) return [];
        if (typeof col.getModelsArray === "function") {
          try {
            return col.getModelsArray() || [];
          } catch (_) {
            /* fall through */
          }
        }
        if (Array.isArray(col._models)) return col._models;
        if (Array.isArray(col.models)) return col.models;
        return [];
      }

      function serializeId(id) {
        if (!id) return "";
        if (typeof id === "string") return id;
        return id._serialized || "";
      }

      function chatTime(ch) {
        if (!ch) return 0;
        const fromLast =
          (ch.lastMessage && (ch.lastMessage.t || ch.lastMessage.timestamp)) || 0;
        return Number(ch.t || ch.timestamp || fromLast || 0) || 0;
      }

      function isArchived(ch) {
        return Boolean(ch && (ch.archive || ch.isArchived || ch.archived));
      }

      function phoneFromId(chatId, contact) {
        if (contact) {
          if (contact.phoneNumber && contact.phoneNumber.user) {
            return String(contact.phoneNumber.user);
          }
          if (contact.phoneNumber && contact.phoneNumber._serialized) {
            return String(contact.phoneNumber._serialized).split("@")[0];
          }
          if (contact.number) return String(contact.number).replace(/\D+/g, "");
        }
        if (chatId && chatId.indexOf("@c.us") !== -1) return chatId.split("@")[0];
        return "";
      }

      function displayName(ch, contact, isGroup) {
        if (isGroup) {
          return String(
            (ch && (ch.name || ch.formattedTitle)) ||
              (contact && (contact.name || contact.shortName)) ||
              "Group"
          ).trim();
        }
        // Prefer the name saved on this phone, then chat title.
        const saved = String(
          (contact && (contact.name || contact.shortName)) || ""
        ).trim();
        if (saved) return saved;
        return String((ch && (ch.name || ch.formattedTitle)) || "").trim();
      }

      let Chat = null;
      let Contact = null;
      if (typeof window.require === "function") {
        try {
          const cols = window.require("WAWebCollections");
          Chat = cols && cols.Chat;
          Contact = cols && cols.Contact;
        } catch (_) {
          /* ignore */
        }
      }
      if (!Chat && window.Store) {
        Chat = window.Store.Chat;
        Contact = window.Store.Contact;
      }

      const chats = modelsOf(Chat);
      const book = modelsOf(Contact);
      result.stats = { chatLen: chats.length, contactLen: book.length };

      // Live window: chats active in about the last 6 months (phone chat list feel).
      const nowSec = Math.floor(Date.now() / 1000);
      const maxAgeSec = 180 * 24 * 60 * 60;

      const contactByPhone = {};
      const groupById = {};
      let skippedArchived = 0;
      let skippedStale = 0;
      let skippedNoName = 0;

      for (let i = 0; i < chats.length; i++) {
        const ch = chats[i];
        if (!ch || !ch.id) continue;
        const chatId = serializeId(ch.id);
        if (!chatId) continue;
        if (chatId.indexOf("@broadcast") !== -1 || chatId === "status@broadcast") {
          continue;
        }
        if (isArchived(ch)) {
          skippedArchived += 1;
          continue;
        }

        const t = chatTime(ch);
        if (t > 0 && nowSec - t > maxAgeSec) {
          skippedStale += 1;
          continue;
        }
        // Chats with no timestamp are usually stubs — skip them.
        if (!t) {
          skippedStale += 1;
          continue;
        }

        const isGroup =
          Boolean(ch.isGroup) ||
          chatId.indexOf("@g.us") !== -1 ||
          Boolean(ch.groupMetadata);

        const contact = ch.contact || null;
        const name = displayName(ch, contact, isGroup);
        if (!name) {
          skippedNoName += 1;
          continue;
        }

        if (isGroup) {
          const existing = groupById[chatId];
          if (!existing || t > existing.t) {
            groupById[chatId] = {
              id: chatId,
              name,
              phone: "",
              chatId,
              type: "group",
              source: "live_chat",
              t,
            };
          }
          continue;
        }

        const phone = phoneFromId(chatId, contact);
        if (!phone && chatId.indexOf("@lid") !== -1) {
          // Need a real phone to message; skip LID-only stubs without PN.
          if (!(contact && contact.phoneNumber)) {
            skippedNoName += 1;
            continue;
          }
        }
        const resolvedPhone = phone || phoneFromId("", contact);
        if (!resolvedPhone) {
          skippedNoName += 1;
          continue;
        }

        let sendId = chatId;
        if (contact && contact.phoneNumber && contact.phoneNumber._serialized) {
          sendId = contact.phoneNumber._serialized;
        } else if (resolvedPhone && sendId.indexOf("@c.us") === -1) {
          sendId = resolvedPhone + "@c.us";
        }

        const existing = contactByPhone[resolvedPhone];
        if (!existing || t > existing.t) {
          contactByPhone[resolvedPhone] = {
            id: sendId,
            name,
            phone: resolvedPhone,
            chatId: sendId,
            isMyContact: Boolean(
              contact &&
                (Number(contact.isAddressBookContact) === 1 || contact.isMyContact)
            ),
            type: "contact",
            source: "live_chat",
            t,
          };
        }
      }

      result.contacts = Object.keys(contactByPhone).map((k) => contactByPhone[k]);
      result.groups = Object.keys(groupById).map((k) => groupById[k]);
      result.stats.skippedArchived = skippedArchived;
      result.stats.skippedStale = skippedStale;
      result.stats.skippedNoName = skippedNoName;
      result.stats.liveContacts = result.contacts.length;
      result.stats.liveGroups = result.groups.length;
      result.stats.bookLen = book.length;
    } catch (err) {
      result.error = String((err && err.message) || err);
    }
    return result;
  });

  if (raw.error && !raw.contacts.length && !raw.groups.length) {
    throw new Error(raw.error || "Could not read WhatsApp store");
  }

  console.log(
    "[bridge] Live chats — contacts:",
    (raw.contacts || []).length,
    "groups:",
    (raw.groups || []).length,
    "stats:",
    JSON.stringify(raw.stats || {})
  );

  const qn = String(q || "").trim().toLowerCase();
  let contacts = raw.contacts || [];
  let groups = raw.groups || [];
  if (qn) {
    contacts = contacts.filter((c) =>
      `${c.name} ${c.phone}`.toLowerCase().includes(qn)
    );
    groups = groups.filter((g) => g.name.toLowerCase().includes(qn));
  }

  contacts = contacts.map((c) => {
    const phone = digitsPhone(c.phone) || String(c.phone || "").replace(/\D+/g, "");
    return {
      id: c.id || c.chatId,
      name: String(c.name || phone).trim(),
      phone,
      chatId: c.chatId || c.id,
      isMyContact: Boolean(c.isMyContact),
      type: "contact",
      source: c.source || "live_chat",
      t: c.t || 0,
    };
  });

  groups = groups.map((g) => ({
    id: g.id || g.chatId,
    name: String(g.name || "Group").trim(),
    phone: "",
    chatId: g.chatId || g.id,
    type: "group",
    source: g.source || "live_chat",
    t: g.t || 0,
  }));

  // Newest activity first — same feel as the phone chat list.
  contacts.sort((a, b) => (b.t || 0) - (a.t || 0) || a.name.localeCompare(b.name));
  groups.sort((a, b) => (b.t || 0) - (a.t || 0) || a.name.localeCompare(b.name));
  return { contacts, groups };
}

app.get("/contacts", async (req, res) => {
  if (!state.ready || state.status !== "connected") {
    return res.status(409).json({
      ok: false,
      error: "WhatsApp is not connected. Scan the QR code first.",
    });
  }

  const includeGroups = String(req.query.includeGroups || "1") !== "0";
  const q = String(req.query.q || req.query.search || "").trim();

  try {
    console.log("[bridge] Loading contacts from WhatsApp store…");
    const data = await withTimeout(
      loadContactsFromStore(q),
      60000,
      "loadContactsFromStore"
    );
    const contacts = data.contacts || [];
    const groups = includeGroups ? data.groups || [] : [];
    console.log(
      `[bridge] Contacts ready: ${contacts.length} people, ${groups.length} groups`
    );
    return res.json({
      ok: true,
      count: contacts.length,
      group_count: groups.length,
      contacts,
      groups,
    });
  } catch (err) {
    console.error("[bridge] contacts failed", err);
    return res.status(500).json({
      ok: false,
      error: err.message || String(err),
    });
  }
});

app.post("/send", async (req, res) => {
  if (!state.ready || state.status !== "connected") {
    return res.status(409).json({
      ok: false,
      error: "WhatsApp is not connected",
    });
  }
  const text = String(req.body?.text || "");
  const mediaPath = (req.body?.mediaPath || "").trim();
  const mediaBase64 = String(req.body?.mediaBase64 || "").trim();
  const mediaMime = String(req.body?.mediaMime || "image/jpeg").trim();
  const mediaFilename = String(req.body?.mediaFilename || "image.jpg").trim();

  let chatId;
  try {
    chatId = await resolveChatId({
      phone: req.body?.phone,
      chatId: req.body?.chatId,
    });
  } catch (err) {
    return res.status(400).json({
      ok: false,
      error: err.message || "Could not resolve WhatsApp chat id",
    });
  }

  if (!chatId) {
    return res.status(400).json({ ok: false, error: "Invalid phone or chat id" });
  }
  if (!text && !mediaPath && !mediaBase64) {
    return res.status(400).json({ ok: false, error: "text or media required" });
  }

  try {
    let result;
    if (mediaBase64 || mediaPath) {
      let media;
      if (mediaBase64) {
        const raw = mediaBase64.includes(",")
          ? mediaBase64.split(",").pop()
          : mediaBase64;
        media = new MessageMedia(mediaMime || "image/jpeg", raw, mediaFilename || "image.jpg");
      } else {
        if (!fs.existsSync(mediaPath)) {
          return res.status(400).json({ ok: false, error: `Media not found: ${mediaPath}` });
        }
        media = MessageMedia.fromFilePath(mediaPath);
      }
      result = await client.sendMessage(chatId, media, { caption: text || undefined });
    } else {
      result = await client.sendMessage(chatId, text);
    }
    return res.json({
      ok: true,
      messageId: result?.id?._serialized || result?.id?.id || null,
      chatId,
    });
  } catch (err) {
    console.error("[bridge] send failed", err);
    return res.status(500).json({
      ok: false,
      error: err.message || String(err),
    });
  }
});

app.post("/logout", async (_req, res) => {
  try {
    if (state.ready) {
      await client.logout();
    }
  } catch (err) {
    console.warn("[bridge] logout warning", err.message || err);
  }
  state.status = "disconnected";
  state.ready = false;
  state.qrDataUrl = "";
  state.phone = "";
  state.error = "";
  setTimeout(() => {
    client.initialize().catch((err) => {
      state.error = err.message || String(err);
    });
  }, 1500);
  return res.json({ ok: true });
});

app.listen(PORT, HOST, () => {
  console.log(`[bridge] Listening on http://${HOST}:${PORT}`);
  if (IS_REMOTE_BIND) {
    console.log("[bridge] Remote bind enabled — keep WHATSAPP_BRIDGE_SECRET private");
  }
  client.initialize().catch((err) => {
    state.status = "disconnected";
    state.error = err.message || String(err);
    console.error("[bridge] Initialize failed", err);
  });
});
