(() => {
  const root = document.querySelector("[data-wa-app]");
  if (!root) return;

  const fitViewport = () => {
    const viewport = window.visualViewport;
    const height = Math.round(viewport?.height || window.innerHeight);
    document.documentElement.style.setProperty("--wa-vvh", `${height}px`);
  };
  fitViewport();
  window.visualViewport?.addEventListener("resize", fitViewport);
  window.visualViewport?.addEventListener("scroll", fitViewport);
  window.addEventListener("resize", fitViewport);
  window.addEventListener("orientationchange", fitViewport);

  const apiInbox = root.getAttribute("data-api-inbox") || "";
  const canSend = root.getAttribute("data-can-send") !== "0";
  const csrf =
    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="))
      ?.split("=")[1] ||
    "";

  const els = {
    chats: root.querySelector("[data-wa-chats]"),
    search: root.querySelector("[data-wa-search]"),
    listPane: root.querySelector("[data-wa-list-pane]"),
    chatPane: root.querySelector("[data-wa-chat-pane]"),
    empty: root.querySelector("[data-wa-empty]"),
    thread: root.querySelector("[data-wa-thread]"),
    stage: root.querySelector("[data-wa-stage]"),
    headName: root.querySelector("[data-wa-head-name]"),
    headMeta: root.querySelector("[data-wa-head-meta]"),
    headAvatar: root.querySelector("[data-wa-head-avatar]"),
    composer: root.querySelector("[data-wa-composer]"),
    input: root.querySelector("[data-wa-input]"),
    send: root.querySelector("[data-wa-send]"),
    file: root.querySelector("[data-wa-file]"),
    fileName: root.querySelector("[data-wa-file-name]"),
    emojiBtn: root.querySelector("[data-wa-emoji]"),
    emojiPop: root.querySelector("[data-wa-emoji-pop]"),
    back: root.querySelector("[data-wa-back]"),
    newChat: root.querySelector("[data-wa-new]"),
    contacts: root.querySelector("[data-wa-contacts]"),
    contactsClose: root.querySelector("[data-wa-contacts-close]"),
    contactSearch: root.querySelector("[data-wa-contact-search]"),
    contactList: root.querySelector("[data-wa-contact-list]"),
  };

  const EMOJI =
    "😀 😃 😄 😁 😆 😅 😂 🤣 😊 😇 🙂 😉 😍 🥰 😘 😗 😙 😚 😋 😛 😜 🤪 😝 🤗 🤭 🤫 🤔 😐 😑 😶 🙄 😏 😣 😥 😮 🤐 😯 😪 😫 🥱 😴 😌 🤓 😎 🥳 😕 😟 🙁 ☹️ 😲 😳 🥺 😭 😢 😤 😠 😡 🤬 👍 👎 👏 🙌 🙏 💪 🔥 ✨ ❤️ 🧡 💛 💚 💙 💜 🖤 🤍 ✅ ❌ 🎉 💯".split(
      " "
    );

  const readJson = (id, fallback) => {
    const node = document.getElementById(id);
    if (!node) return fallback;
    try {
      return JSON.parse(node.textContent || "null") ?? fallback;
    } catch (_) {
      return fallback;
    }
  };

  let threads = readJson("wa-inbox-data", {})?.threads || [];
  const contacts = readJson("wa-inbox-contacts", []) || [];
  let activePhone = root.getAttribute("data-selected-phone") || "";
  let query = "";
  let pollTimer = 0;

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const phoneKey = (phone) => {
    const digits = String(phone || "").replace(/\D+/g, "");
    if (digits.length >= 9) return digits.slice(-9);
    return String(phone || "").toLowerCase();
  };

  const samePhone = (a, b) => phoneKey(a) && phoneKey(a) === phoneKey(b);

  const tickIcon = (tick) => {
    if (tick === "error") return `<span class="wa-tick wa-tick--error">!</span>`;
    if (tick === "pending") return `<span class="wa-tick wa-tick--wait">·</span>`;
    if (tick === "read")
      return `<span class="wa-tick wa-tick--read" aria-label="Read">✓✓</span>`;
    if (tick === "delivered")
      return `<span class="wa-tick" aria-label="Delivered">✓✓</span>`;
    if (tick === "sent") return `<span class="wa-tick" aria-label="Sent">✓</span>`;
    return "";
  };

  const filteredThreads = () => {
    const needle = query.trim().toLowerCase();
    if (!needle) return threads;
    return threads.filter((thread) => {
      const hay = `${thread.full_name || ""} ${thread.phone || ""} ${thread.display_phone || ""} ${thread.body || ""}`.toLowerCase();
      return hay.includes(needle);
    });
  };

  const activeThread = () => threads.find((thread) => samePhone(thread.phone, activePhone));

  const renderChats = () => {
    if (!els.chats) return;
    const rows = filteredThreads();
    if (!rows.length) {
      els.chats.innerHTML = `<p class="wa-app__none">${
        threads.length ? "No chats match your search." : "No chats yet. Start one from New chat."
      }</p>`;
      return;
    }
    els.chats.innerHTML = rows
      .map((thread) => {
        const unread = Number(thread.unread_count || 0);
        const active = samePhone(thread.phone, activePhone) ? " is-active" : "";
        const unreadClass = unread ? " is-unread" : "";
        return `<button type="button" class="wa-chat${active}${unreadClass}" data-wa-open="${escapeHtml(
          thread.phone
        )}" role="listitem">
          <span class="comms-avatar" data-tone="${thread.tone || 1}" aria-hidden="true">${escapeHtml(
            thread.initials || "?"
          )}</span>
          <span class="wa-chat__main">
            <span class="wa-chat__top">
              <strong>${escapeHtml(thread.full_name || thread.phone)}</strong>
              <time>${escapeHtml(thread.last_label || "")}</time>
            </span>
            <span class="wa-chat__preview">${escapeHtml(thread.body || "")}</span>
          </span>
          ${unread ? `<span class="wa-chat__badge">${unread > 99 ? "99+" : unread}</span>` : ""}
        </button>`;
      })
      .join("");
  };

  const renderStage = (thread, { stick = true } = {}) => {
    if (!els.stage) return;
    if (!thread) {
      els.stage.innerHTML = "";
      return;
    }
    const nearBottom =
      els.stage.scrollHeight - els.stage.scrollTop - els.stage.clientHeight < 90;
    const messages = thread.messages || [];
    let lastDay = "";
    const bits = [];
    messages.forEach((msg) => {
      if (msg.day_key && msg.day_key !== lastDay) {
        lastDay = msg.day_key;
        bits.push(`<div class="wa-day"><span>${escapeHtml(msg.day_label || "")}</span></div>`);
      }
      const incoming = msg.direction === "in";
      const photo = msg.image_url
        ? `<img class="wa-bubble__photo" src="${escapeHtml(msg.image_url)}" alt="">`
        : "";
      const body = msg.body ? `<p>${escapeHtml(msg.body)}</p>` : "";
      const err = msg.error && msg.tick === "error"
        ? `<em class="wa-bubble__error">${escapeHtml(msg.error)}</em>`
        : "";
      bits.push(`<article class="wa-bubble-row ${incoming ? "is-in" : "is-out"}">
        <div class="wa-bubble ${incoming ? "is-in" : "is-out"}">
          ${photo}${body}${err}
          <span class="wa-bubble__meta">
            <time>${escapeHtml(msg.time_label || "")}</time>
            ${incoming ? "" : tickIcon(msg.tick)}
          </span>
        </div>
      </article>`);
    });
    els.stage.innerHTML = bits.join("") || `<p class="wa-app__none">No messages yet. Say hello.</p>`;
    if (stick || nearBottom) els.stage.scrollTop = els.stage.scrollHeight;
  };

  const showThread = (thread, { stick = true } = {}) => {
    const open = Boolean(thread);
    if (els.empty) els.empty.hidden = open;
    if (els.thread) els.thread.hidden = !open;
    root.classList.toggle("is-chat-open", open);
    if (!thread) return;
    if (els.headName) els.headName.textContent = thread.full_name || thread.phone;
    if (els.headMeta) els.headMeta.textContent = thread.display_phone || thread.phone || "";
    if (els.headAvatar) {
      els.headAvatar.textContent = thread.initials || "?";
      els.headAvatar.setAttribute("data-tone", String(thread.tone || 1));
    }
    renderStage(thread, { stick });
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const selectChat = async (phone, { markRead = true } = {}) => {
    activePhone = phone || "";
    if (!activePhone) {
      showThread(null);
      renderChats();
      return;
    }
    let thread = activeThread();
    if (!thread) {
      thread = {
        phone: activePhone,
        display_phone: activePhone,
        full_name: activePhone,
        initials: (activePhone || "?").slice(0, 1).toUpperCase(),
        tone: 1,
        body: "",
        unread_count: 0,
        messages: [],
      };
      threads = [thread, ...threads];
    }
    showThread(thread);
    renderChats();
    if (markRead && apiInbox) {
      try {
        const data = await fetchJson(apiInbox, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrf,
          },
          body: JSON.stringify({ action: "mark_read", phone: activePhone }),
        });
        if (Array.isArray(data.threads)) {
          threads = data.threads;
          showThread(activeThread() || thread);
          renderChats();
        }
      } catch (_) {
        /* keep local view */
      }
    }
    els.input?.focus();
  };

  async function fetchJson(url, options) {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || "Could not update.");
    }
    return data;
  }

  const refresh = async ({ silent = true } = {}) => {
    if (!apiInbox) return;
    try {
      const data = await fetchJson(apiInbox);
      if (Array.isArray(data.threads)) {
        threads = data.threads;
        renderChats();
        if (activePhone) showThread(activeThread(), { stick: false });
      }
    } catch (error) {
      if (!silent && els.chats) {
        els.chats.innerHTML = `<p class="wa-app__none">${escapeHtml(error.message)}</p>`;
      }
    }
  };

  const resizeInput = () => {
    if (!els.input) return;
    els.input.style.height = "auto";
    els.input.style.height = `${Math.min(els.input.scrollHeight, 120)}px`;
  };

  const sendMessage = async () => {
    if (!canSend || !els.composer) return;
    const text = (els.input?.value || "").trim();
    const file = els.file?.files?.[0];
    if (!activePhone) return;
    if (!text && !file) return;
    if (els.send) els.send.disabled = true;
    try {
      const form = new FormData();
      form.append("action", "send");
      form.append("phone", activePhone);
      form.append("body", text);
      if (file) form.append("image", file);
      const data = await fetchJson(apiInbox, {
        method: "POST",
        headers: { "X-CSRFToken": csrf },
        body: form,
      });
      if (Array.isArray(data.threads)) threads = data.threads;
      if (els.input) els.input.value = "";
      if (els.file) els.file.value = "";
      if (els.fileName) {
        els.fileName.hidden = true;
        els.fileName.textContent = "";
      }
      resizeInput();
      showThread(activeThread());
      renderChats();
    } catch (error) {
      if (els.fileName) {
        els.fileName.hidden = false;
        els.fileName.textContent = error.message || "Could not send.";
      }
    } finally {
      if (els.send) els.send.disabled = false;
      els.input?.focus();
    }
  };

  const renderEmoji = () => {
    if (!els.emojiPop) return;
    els.emojiPop.innerHTML = EMOJI.map(
      (item) => `<button type="button" data-wa-emo="${item}">${item}</button>`
    ).join("");
  };

  const renderContacts = () => {
    if (!els.contactList) return;
    const needle = (els.contactSearch?.value || "").trim().toLowerCase();
    const rows = contacts.filter((row) => {
      if (!needle) return true;
      return `${row.full_name || ""} ${row.phone || ""}`.toLowerCase().includes(needle);
    });
    if (!rows.length) {
      els.contactList.innerHTML = `<p class="wa-app__none">No contacts match.</p>`;
      return;
    }
    els.contactList.innerHTML = rows
      .map(
        (row) => `<button type="button" class="wa-chat" data-wa-pick="${escapeHtml(row.phone)}" data-wa-pick-name="${escapeHtml(
          row.full_name || ""
        )}">
          <span class="comms-avatar" data-tone="1" aria-hidden="true">${escapeHtml(
            (row.full_name || "?").slice(0, 1).toUpperCase()
          )}</span>
          <span class="wa-chat__main">
            <span class="wa-chat__top"><strong>${escapeHtml(row.full_name || "Contact")}</strong></span>
            <span class="wa-chat__preview">${escapeHtml(row.phone || "")}</span>
          </span>
        </button>`
      )
      .join("");
  };

  const setContactsOpen = (open) => {
    if (!els.contacts) return;
    els.contacts.hidden = !open;
    if (open) {
      renderContacts();
      els.contactSearch?.focus();
    }
  };

  els.chats?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-wa-open]");
    if (!btn) return;
    selectChat(btn.getAttribute("data-wa-open") || "");
  });

  els.search?.addEventListener("input", () => {
    query = els.search.value || "";
    renderChats();
  });

  els.back?.addEventListener("click", () => selectChat(""));

  els.newChat?.addEventListener("click", () => setContactsOpen(true));
  els.contactsClose?.addEventListener("click", () => setContactsOpen(false));
  els.contactSearch?.addEventListener("input", renderContacts);
  els.contactList?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-wa-pick]");
    if (!btn) return;
    const phone = btn.getAttribute("data-wa-pick") || "";
    const name = btn.getAttribute("data-wa-pick-name") || phone;
    if (!threads.some((thread) => samePhone(thread.phone, phone))) {
      threads = [
        {
          phone,
          display_phone: phone,
          full_name: name,
          initials: name.slice(0, 1).toUpperCase(),
          tone: 1,
          body: "",
          unread_count: 0,
          messages: [],
        },
        ...threads,
      ];
    }
    setContactsOpen(false);
    selectChat(phone, { markRead: false });
  });

  els.composer?.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });

  els.input?.addEventListener("input", resizeInput);
  els.input?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  els.file?.addEventListener("change", () => {
    const file = els.file.files?.[0];
    if (els.fileName) {
      els.fileName.hidden = !file;
      els.fileName.textContent = file ? file.name : "";
    }
  });

  els.emojiBtn?.addEventListener("click", () => {
    if (!els.emojiPop) return;
    els.emojiPop.hidden = !els.emojiPop.hidden;
  });
  els.emojiPop?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-wa-emo]");
    if (!btn || !els.input) return;
    const emo = btn.getAttribute("data-wa-emo") || "";
    const start = els.input.selectionStart || els.input.value.length;
    const end = els.input.selectionEnd || start;
    els.input.value = `${els.input.value.slice(0, start)}${emo}${els.input.value.slice(end)}`;
    els.input.focus();
    els.emojiPop.hidden = true;
    resizeInput();
  });

  document.addEventListener("click", (event) => {
    if (!els.emojiPop || els.emojiPop.hidden) return;
    if (event.target.closest("[data-wa-emoji]") || event.target.closest("[data-wa-emoji-pop]")) return;
    els.emojiPop.hidden = true;
  });

  renderEmoji();
  renderChats();
  if (activePhone) {
    selectChat(activePhone, { markRead: true });
  } else {
    showThread(null);
  }

  pollTimer = window.setInterval(() => refresh({ silent: true }), 10000);
  window.addEventListener("beforeunload", () => window.clearInterval(pollTimer));
  if (window.lucide?.createIcons) window.lucide.createIcons();
})();
