const appState = {
  cursor: 0,
  messages: [],
  conversations: [],
  selectedConversation: "lobby",
  state: null,
  unread: {},
  busy: false,
};

const elements = {
  conversationList: document.getElementById("conversation-list"),
  conversationTitle: document.getElementById("conversation-title"),
  conversationSubtitle: document.getElementById("conversation-subtitle"),
  messageFeed: document.getElementById("message-feed"),
  banner: document.getElementById("banner"),
  messageForm: document.getElementById("message-form"),
  messageInput: document.getElementById("message-input"),
  sendButton: document.getElementById("send-button"),
  composerHint: document.getElementById("composer-hint"),
};

function conversationMeta(id) {
  return appState.conversations.find((item) => item.id === id) || null;
}

function visibleMessages(conversationId) {
  if (conversationId === "lobby") {
    return appState.messages.filter((message) => message.in_lobby);
  }
  if (conversationId === "broadcast") {
    return appState.messages.filter((message) => message.in_broadcast);
  }
  return appState.messages.filter((message) => message.conversation_id === conversationId);
}

function markConversationSeen(conversationId) {
  const messages = visibleMessages(conversationId);
  if (!messages.length) {
    appState.unread[conversationId] = 0;
    return;
  }
  const latestId = messages[messages.length - 1].id;
  appState.unread[conversationId] = latestId;
}

function unreadCount(conversationId) {
  const seen = appState.unread[conversationId] || 0;
  return visibleMessages(conversationId).filter((message) => message.id > seen).length;
}

function formatTime(timestamp) {
  const date = new Date(timestamp * 1000);
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function messageTag(message) {
  if (message.speaker_id === "broadcast") {
    return "广播";
  }
  if (message.speaker_id === "system") {
    return "系统";
  }
  if (message.visibility === "private") {
    return "私聊";
  }
  return "公开";
}

function renderConversations() {
  const fragment = document.createDocumentFragment();

  for (const conversation of appState.conversations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `conversation-item${conversation.id === appState.selectedConversation ? " active" : ""}`;
    button.addEventListener("click", () => {
      appState.selectedConversation = conversation.id;
      markConversationSeen(conversation.id);
      render();
    });

    const meta = document.createElement("div");
    meta.className = "conversation-meta";

    const titleWrap = document.createElement("div");
    const title = document.createElement("div");
    title.className = "conversation-title-line";
    title.textContent = conversation.title;
    const kind = document.createElement("div");
    kind.className = "conversation-kind";
    kind.textContent = conversation.kind === "private" ? "定向会话" : "公共会话";
    titleWrap.append(title, kind);

    meta.appendChild(titleWrap);

    const unread = unreadCount(conversation.id);
    if (unread > 0 && conversation.id !== appState.selectedConversation) {
      const badge = document.createElement("span");
      badge.className = "unread-dot";
      badge.textContent = String(unread);
      meta.appendChild(badge);
    }

    const summary = document.createElement("div");
    summary.className = "conversation-summary";
    summary.textContent = conversation.summary || "暂无摘要";

    button.append(meta, summary);
    fragment.appendChild(button);
  }

  elements.conversationList.replaceChildren(fragment);
}

function renderMessages() {
  const messages = visibleMessages(appState.selectedConversation);
  const fragment = document.createDocumentFragment();

  if (!messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "这里还没有消息。你可以先试探发言，或者切到别的会话。";
    fragment.appendChild(empty);
  } else {
    for (const message of messages) {
      const row = document.createElement("div");
      const mode = message.speaker_id === "system" ? "system" : message.is_player ? "player" : "npc";
      row.className = `message-row ${mode}`;

      const bubble = document.createElement("article");
      bubble.className = "message-bubble";

      const head = document.createElement("div");
      head.className = "message-head";

      const speaker = document.createElement("span");
      speaker.className = "speaker-name";
      speaker.textContent = `${message.speaker_id} ${message.speaker_name}`;

      const tag = document.createElement("span");
      tag.className = "speaker-tag";
      tag.textContent = messageTag(message);

      const time = document.createElement("span");
      time.textContent = formatTime(message.created_at);

      head.append(speaker, tag, time);

      const body = document.createElement("div");
      body.className = "message-text";
      body.textContent = message.text;

      bubble.append(head, body);
      row.appendChild(bubble);
      fragment.appendChild(row);
    }
  }

  const atBottom =
    elements.messageFeed.scrollTop + elements.messageFeed.clientHeight >= elements.messageFeed.scrollHeight - 80;
  elements.messageFeed.replaceChildren(fragment);
  if (atBottom) {
    elements.messageFeed.scrollTop = elements.messageFeed.scrollHeight;
  }
}

function renderHeader() {
  const conversation = conversationMeta(appState.selectedConversation);
  if (!conversation) {
    return;
  }
  elements.conversationTitle.textContent = conversation.title;
  elements.conversationSubtitle.textContent = conversation.summary || "";
  if (appState.state && appState.state.phase_id === "resolved") {
    elements.composerHint.textContent = "本局已结算，当前会话只读。";
  } else if (conversation.id === "broadcast") {
    elements.composerHint.textContent = "广播窗口中的发言仍会作为公开消息进入公共大厅。";
  } else if (conversation.id.startsWith("private:")) {
    elements.composerHint.textContent = "当前是单聊窗口，只有你和目标角色能看到这里的消息。";
  } else {
    elements.composerHint.textContent = "公开发言会进入公共大厅，并可能触发 NPC 的公开回应。";
  }
}

function renderComposerState() {
  const canSend = !appState.busy && (!appState.state || appState.state.phase_id !== "resolved");
  elements.messageInput.disabled = !canSend;
  elements.sendButton.disabled = !canSend;
}

function renderBanner(message = "", isError = false) {
  elements.banner.textContent = message;
  elements.banner.classList.toggle("error", Boolean(isError && message));
}

function render() {
  renderConversations();
  renderHeader();
  renderMessages();
  renderComposerState();
}

function mergePayload(payload) {
  if (payload.state) {
    appState.state = payload.state;
  }
  if (payload.conversations) {
    appState.conversations = payload.conversations;
  }
  if (payload.messages && payload.messages.length) {
    const existing = new Map(appState.messages.map((message) => [message.id, message]));
    for (const message of payload.messages) {
      existing.set(message.id, message);
    }
    appState.messages = [...existing.values()].sort((a, b) => a.id - b.id);
  }
  appState.cursor = payload.cursor ?? appState.cursor;
  if (!conversationMeta(appState.selectedConversation)) {
    appState.selectedConversation = "lobby";
  }
  markConversationSeen(appState.selectedConversation);
  render();
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "请求失败。");
  }
  return payload;
}

async function bootstrap() {
  const payload = await requestJson("/api/bootstrap");
  appState.messages = [];
  appState.unread = {};
  mergePayload(payload);
}

async function pollState() {
  try {
    const payload = await requestJson(`/api/state?cursor=${appState.cursor}`);
    mergePayload(payload);
  } catch (error) {
    renderBanner(error.message, true);
  }
}

async function withBusy(action) {
  if (appState.busy) {
    return;
  }
  appState.busy = true;
  renderComposerState();
  try {
    await action();
  } finally {
    appState.busy = false;
    renderComposerState();
  }
}

elements.messageForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = elements.messageInput.value.trim();
  if (!text) {
    return;
  }
  withBusy(async () => {
    renderBanner("");
    const payload = await requestJson("/api/send", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: appState.selectedConversation,
        text,
      }),
    });
    elements.messageInput.value = "";
    mergePayload(payload);
  }).catch((error) => renderBanner(error.message, true));
});

bootstrap()
  .then(() => {
    renderBanner("世界时钟已启动。页面会每秒轮询一次增量状态。");
    window.setInterval(pollState, 1000);
  })
  .catch((error) => {
    renderBanner(error.message, true);
  });
