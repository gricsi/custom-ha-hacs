// Lumo chat card: an Assist conversation that is still there after you look away.
//
// The Assist dialog throws its messages away when it closes. This card loads the
// caller's saved turns from lumo/history when it is shown, and sends new ones through
// lumo/chat, which runs the agent as Assist does and streams the reply as it is written,
// so the agent restores and saves history exactly as it does for the dialog. Replies are
// rendered with Home Assistant's own sanitising <ha-markdown>. "New" forgets this agent's
// history for you (lumo/history/clear) and starts over.
//
//   type: custom:lumo-chat-card
//   agent_id: conversation.lumo   # optional, defaults to the Lumo agent you used last
//   title: Lumo                   # optional

class LumoChatCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._messages = [];
    this._conversationId = null;
    this._agentId = this._config.agent_id ?? null;
    this._loaded = false; // the fetch has started
    this._fetched = false; // ...and finished
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._loadHistory();
    }
  }

  getCardSize() {
    return 6;
  }

  static getStubConfig() {
    return {};
  }

  async _loadHistory() {
    try {
      // History is per agent. Without one configured, the server picks the Lumo agent
      // this user talked to last, and the card keeps talking to that one.
      const { agent_id, messages } = await this._hass.callWS({
        type: "lumo/history",
        ...(this._agentId ? { agent_id: this._agentId } : {}),
      });
      this._agentId = this._agentId ?? agent_id;
      this._messages = messages;
    } catch (err) {
      this._messages = [{ role: "error", content: `Could not load history: ${err.message || err}` }];
    }
    this._fetched = true;
    this._renderMessages();
  }

  async _send() {
    const input = this.shadowRoot.querySelector("input");
    const text = input.value.trim();
    if (!text || this._busy) return;
    input.value = "";
    this._busy = true;
    const reply = { role: "assistant", content: "" };
    this._messages.push({ role: "user", content: text }, reply);
    this._renderMessages();
    let done = null;
    try {
      // Events: {delta} as the reply is written, then {done: result}; the subscription's
      // own result arrives last, so awaiting it means everything has been received.
      const unsubscribe = await this._hass.connection.subscribeMessage(
        (event) => {
          if (event.delta) {
            reply.content += event.delta;
            this._updateLastBubble(reply.content);
          }
          if (event.done) done = event.done;
        },
        {
          type: "lumo/chat",
          text,
          language: this._hass.language,
          ...(this._conversationId ? { conversation_id: this._conversationId } : {}),
          ...(this._agentId ? { agent_id: this._agentId } : {}),
        },
      );
      unsubscribe().catch(() => {});
      if (done) {
        this._conversationId = done.conversation_id;
        reply.content = done.response?.speech?.plain?.speech || reply.content;
      }
    } catch (err) {
      this._messages.pop();
      this._messages.push({ role: "error", content: err.message || String(err) });
    }
    this._busy = false;
    this._renderMessages();
  }

  async _newConversation() {
    if (this._busy) return;
    try {
      if (this._agentId) await this._hass.callWS({ type: "lumo/history/clear", agent_id: this._agentId });
      this._messages = [];
      this._conversationId = null;
    } catch (err) {
      this._messages.push({ role: "error", content: `Could not start over: ${err.message || err}` });
    }
    this._renderMessages();
  }

  _render() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const title = this._escape(this._config.title ?? "Lumo");
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; ${this._full ? "height: 100vh; height: 100dvh; background: var(--primary-background-color);" : ""} }
        .toolbar { display: flex; align-items: center; height: var(--header-height, 56px); padding: 0 12px; gap: 8px; font-size: 20px;
          background: var(--app-header-background-color); color: var(--app-header-text-color, white); }
        .page { padding: 16px; height: calc(100% - var(--header-height, 56px) - 32px); max-width: 960px; margin: 0 auto; }
        ha-card { display: flex; flex-direction: column; height: 100%; }
        .messages { flex: 1; overflow-y: auto; max-height: ${this._full ? "none" : "480px"}; padding: 8px 16px; display: flex; flex-direction: column; gap: 8px; }
        .message { padding: 8px 12px; border-radius: 12px; max-width: 80%; white-space: pre-wrap; overflow-wrap: anywhere; }
        ha-markdown.message { white-space: normal; }
        ha-markdown.message > :first-child { margin-top: 0; }
        .user { align-self: flex-end; background: var(--primary-color); color: var(--text-primary-color); }
        .assistant { align-self: flex-start; background: var(--secondary-background-color); color: var(--primary-text-color); }
        .error { align-self: center; color: var(--error-color); }
        .empty { color: var(--secondary-text-color); align-self: center; }
        form { display: flex; gap: 8px; padding: 8px 16px 16px; }
        input { flex: 1; padding: 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); font: inherit; }
        button.new { background: none; color: var(--primary-color); padding: 0 8px; }
        button { border: none; border-radius: 8px; padding: 0 16px; background: var(--primary-color); color: var(--text-primary-color); font: inherit; cursor: pointer; }
      </style>
      ${this._full ? `<div class="toolbar"><ha-menu-button></ha-menu-button><span>${title}</span></div><div class="page">` : ""}
      <ha-card ${this._full ? "" : `header="${title}"`}>
        <div class="messages"></div>
        <form><button type="button" class="new" title="Start a new conversation">New</button><input placeholder="Enter a request" autocomplete="off" /><button type="submit">Send</button></form>
      </ha-card>
      ${this._full ? "</div>" : ""}`;
    this.shadowRoot.querySelector("form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      this._send();
    });
    this.shadowRoot.querySelector("button.new").addEventListener("click", () => this._newConversation());
    this._renderMessages();
  }

  _renderMessages() {
    const list = this.shadowRoot?.querySelector(".messages");
    if (!list) return;
    list.replaceChildren(
      ...(this._messages.length
        ? this._messages.map(({ role, content }) => this._bubble(role, content))
        : [this._bubble("empty", this._fetched ? "No saved conversation yet." : "Loading…")]),
    );
    list.scrollTop = list.scrollHeight;
  }

  // While a reply streams in, only its bubble changes; rebuilding the list on every
  // fragment would re-render every earlier reply's markdown each time.
  _updateLastBubble(content) {
    const list = this.shadowRoot?.querySelector(".messages");
    const last = list?.lastElementChild;
    if (!last) return;
    if (last.tagName === "HA-MARKDOWN") last.content = content;
    else last.textContent = content;
    list.scrollTop = list.scrollHeight;
  }

  _bubble(role, content) {
    // Replies are model output: never innerHTML. <ha-markdown> sanitises what it renders;
    // where it is not loaded, plain text.
    const markdown = role === "assistant" && customElements.get("ha-markdown");
    const el = document.createElement(markdown ? "ha-markdown" : "div");
    el.className = `message ${role}`;
    if (markdown) {
      el.breaks = true;
      el.content = content || "…";
    } else {
      el.textContent = content || (role === "assistant" ? "…" : "");
    }
    return el;
  }

  _escape(text) {
    const el = document.createElement("div");
    el.textContent = text;
    return el.innerHTML.replace(/"/g, "&quot;");
  }
}

// The same chat, full page, behind the sidebar entry the integration registers (see
// async_set_panel_visibility). Home Assistant hands a panel hass and narrow, never a config, and
// a panel has to draw its own menu button or the sidebar cannot be opened on a phone.
class LumoChatPanel extends LumoChatCard {
  constructor() {
    super();
    this._full = true;
    this.setConfig({ title: "Lumo" });
  }

  set hass(hass) {
    super.hass = hass;
    this._updateMenu();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    this._updateMenu();
  }

  _render() {
    super._render();
    this._updateMenu();
  }

  _updateMenu() {
    const menu = this.shadowRoot?.querySelector("ha-menu-button");
    if (menu) {
      menu.hass = this._hass;
      menu.narrow = this._narrow;
    }
  }
}

// Loaded twice -- say, also added by hand as a dashboard resource -- the second run must
// not define the elements again: that throws "has already been used with this registry".
if (!customElements.get("lumo-chat-card")) customElements.define("lumo-chat-card", LumoChatCard);
if (!customElements.get("lumo-chat-panel")) customElements.define("lumo-chat-panel", LumoChatPanel);
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "lumo-chat-card")) {
  window.customCards.push({
    type: "lumo-chat-card",
    name: "Lumo chat",
    description: "An Assist conversation that keeps its history after you close it.",
  });
}
