// Lumo chat card: an Assist conversation that is still there after you look away.
//
// The Assist dialog throws its messages away when it closes. This card loads the
// caller's saved turns from lumo/history when it is shown, and sends new ones through
// Home Assistant's own conversation/process, so the agent restores and saves history
// exactly as it does for the dialog. It only reads; it has no way to change anything.
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
    this._messages.push({ role: "user", content: text });
    this._renderMessages();
    try {
      const result = await this._hass.callWS({
        type: "conversation/process",
        text,
        language: this._hass.language,
        conversation_id: this._conversationId,
        ...(this._agentId ? { agent_id: this._agentId } : {}),
      });
      this._conversationId = result.conversation_id;
      const speech = result.response?.speech?.plain?.speech ?? "";
      this._messages.push({ role: "assistant", content: speech });
    } catch (err) {
      this._messages.push({ role: "error", content: err.message || String(err) });
    }
    this._busy = false;
    this._renderMessages();
  }

  _render() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const title = this._escape(this._config.title ?? "Lumo");
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; ${this._full ? "height: 100%; background: var(--primary-background-color);" : ""} }
        .toolbar { display: flex; align-items: center; height: var(--header-height, 56px); padding: 0 12px; gap: 8px; font-size: 20px;
          background: var(--app-header-background-color); color: var(--app-header-text-color, white); }
        .page { padding: 16px; height: calc(100% - var(--header-height, 56px) - 32px); max-width: 960px; margin: 0 auto; }
        ha-card { display: flex; flex-direction: column; height: 100%; }
        .messages { flex: 1; overflow-y: auto; max-height: ${this._full ? "none" : "480px"}; padding: 8px 16px; display: flex; flex-direction: column; gap: 8px; }
        .message { padding: 8px 12px; border-radius: 12px; max-width: 80%; white-space: pre-wrap; overflow-wrap: anywhere; }
        .user { align-self: flex-end; background: var(--primary-color); color: var(--text-primary-color); }
        .assistant { align-self: flex-start; background: var(--secondary-background-color); color: var(--primary-text-color); }
        .error { align-self: center; color: var(--error-color); }
        .empty { color: var(--secondary-text-color); align-self: center; }
        form { display: flex; gap: 8px; padding: 8px 16px 16px; }
        input { flex: 1; padding: 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); font: inherit; }
        button { border: none; border-radius: 8px; padding: 0 16px; background: var(--primary-color); color: var(--text-primary-color); font: inherit; cursor: pointer; }
      </style>
      ${this._full ? `<div class="toolbar"><ha-menu-button></ha-menu-button><span>${title}</span></div><div class="page">` : ""}
      <ha-card ${this._full ? "" : `header="${title}"`}>
        <div class="messages"></div>
        <form><input placeholder="Enter a request" autocomplete="off" /><button type="submit">Send</button></form>
      </ha-card>
      ${this._full ? "</div>" : ""}`;
    this.shadowRoot.querySelector("form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      this._send();
    });
    this._renderMessages();
  }

  _renderMessages() {
    const list = this.shadowRoot?.querySelector(".messages");
    if (!list) return;
    list.replaceChildren(
      ...(this._messages.length
        ? this._messages.map(({ role, content }) => this._bubble(role, content))
        : [this._bubble("empty", this._fetched ? "No saved conversation yet." : "Loading…")]),
      ...(this._busy ? [this._bubble("assistant", "…")] : []),
    );
    list.scrollTop = list.scrollHeight;
  }

  _bubble(role, content) {
    const el = document.createElement("div");
    el.className = `message ${role}`;
    el.textContent = content; // never innerHTML: replies are model output
    return el;
  }

  _escape(text) {
    const el = document.createElement("div");
    el.textContent = text;
    return el.innerHTML.replace(/"/g, "&quot;");
  }
}

// The same chat, full page, behind the sidebar entry the integration registers (see
// async_update_panel). Home Assistant hands a panel hass and narrow, never a config, and
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

customElements.define("lumo-chat-card", LumoChatCard);
customElements.define("lumo-chat-panel", LumoChatPanel);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "lumo-chat-card",
  name: "Lumo chat",
  description: "An Assist conversation that keeps its history after you close it.",
});
