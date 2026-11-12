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
    const reconnected = this._hass?.connection !== hass.connection;
    const languageChanged = this._hass?.language !== hass.language;
    this._hass = hass;
    if (languageChanged) {
      this._updateLabels();
      if (!this._messages?.length) this._renderMessages();
    }
    if (!this._loaded || (reconnected && this._historyFailed && !this._busy)) {
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
      this._historyFailed = false;
    } catch (err) {
      this._historyFailed = true;
      this._messages = [{ role: "error", content: `${this._text("historyError")} ${err.message || err}` }];
    }
    this._fetched = true;
    this._renderMessages();
    this._scrollToEnd(true);
  }

  async _send() {
    const input = this.shadowRoot.querySelector("textarea");
    const text = input.value.trim();
    if (!text || this._busy) return;
    input.value = "";
    this._resizeInput();
    this._busy = true;
    this._updateComposer();
    input.focus({ preventScroll: true });
    const reply = { role: "assistant", content: "" };
    this._messages.push({ role: "user", content: text }, reply);
    this._renderMessages();
    this._scrollToEnd(true); // you sent it: show it, even if you had scrolled up
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
        { resubscribe: false },
      );
      unsubscribe().catch(() => {});
      if (done) {
        this._conversationId = done.conversation_id;
        reply.content = done.response?.speech?.plain?.speech || reply.content;
        this._updateLastBubble(reply.content);
      }
    } catch (err) {
      this._messages.pop();
      this._messages.push({ role: "error", content: err.message || String(err) });
    }
    if (!reply.content && this._messages.includes(reply)) {
      reply.content = this._text("noReply");
      this._updateLastBubble(reply.content);
    }
    this._busy = false;
    this._updateComposer();
    this._renderMessages();
  }

  async _newConversation() {
    if (this._busy) return;
    try {
      if (this._agentId) await this._hass.callWS({ type: "lumo/history/clear", agent_id: this._agentId });
      this._messages = [];
      this._conversationId = null;
    } catch (err) {
      this._messages.push({ role: "error", content: `${this._text("newError")} ${err.message || err}` });
    }
    this._renderMessages();
  }

  _text(key) {
    const messages = this._hass?.language?.startsWith("hu") ? {
      new: "Új beszélgetés", send: "Küldés", placeholder: "Írj a Lumónak…", loading: "Beszélgetés betöltése…",
      greeting: "Miben segíthetek?", welcome: "Kérdezz az otthonodról, vagy kérj egy műveletet.",
      thinking: "Lumo válaszol…", hint: "Enter: küldés · Shift + Enter: új sor",
      historyError: "Nem sikerült betölteni a beszélgetést.", newError: "Nem sikerült új beszélgetést indítani.",
      noReply: "Nem érkezett szöveges válasz.",
    } : {
      new: "New chat", send: "Send message", placeholder: "Message Lumo…", loading: "Loading your conversation…",
      greeting: "How can I help?", welcome: "Ask about your home, or tell me what you’d like to do.",
      thinking: "Lumo is replying…", hint: "Enter to send · Shift + Enter for a new line",
      historyError: "Could not load your conversation.", newError: "Could not start a new chat.",
      noReply: "No text response was received.",
    };
    return messages[key];
  }

  _icon(name) {
    const paths = {
      spark: '<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z"/>',
      new: '<path d="M12 5v14M5 12h14"/>',
      send: '<path d="M12 19V5m-6 6 6-6 6 6"/>',
    };
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
  }

  _updateLabels() {
    if (!this.shadowRoot) return;
    const input = this.shadowRoot.querySelector("textarea");
    if (input) {
      input.placeholder = this._text("placeholder");
      input.setAttribute("aria-label", this._text("placeholder"));
    }
    const newLabel = this.shadowRoot.querySelector(".new span");
    if (newLabel) newLabel.textContent = this._text("new");
    const hint = this.shadowRoot.querySelector(".composer-hint");
    if (hint) hint.textContent = this._text("hint");
    this._updateComposer();
  }

  _resizeInput() {
    const input = this.shadowRoot?.querySelector("textarea");
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }

  _updateComposer() {
    const input = this.shadowRoot?.querySelector("textarea");
    const send = this.shadowRoot?.querySelector("button.send");
    if (send) {
      send.disabled = !!this._busy || !input?.value.trim();
      send.title = this._text(this._busy ? "thinking" : "send");
      send.setAttribute("aria-label", send.title);
    }
    const start = this.shadowRoot?.querySelector("button.new");
    if (start) start.disabled = !!this._busy;
    this.shadowRoot?.querySelector("form")?.setAttribute("aria-busy", String(!!this._busy));
  }

  _handleComposerKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      this._send();
    }
  }

  _render() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const title = this._escape(this._config.title ?? "Lumo");
    const newButton = `<button type="button" class="new">${this._icon("new")}<span>${this._text("new")}</span></button>`;
    const brand = `<div class="brand"><span class="brand-mark" aria-hidden="true">${this._icon("spark")}</span><span>${title}</span></div>`;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; color: var(--primary-text-color, #20232b); font-family: var(--paper-font-body1_-_font-family, inherit);
          ${this._full ? "height: 100vh; height: 100dvh; background: var(--primary-background-color, #f7f8fa);" : ""} }
        * { box-sizing: border-box; }
        svg { width: 20px; height: 20px; flex: none; }
        .toolbar, .card-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 22px; }
        .toolbar { height: var(--header-height, 64px); padding: 0 20px 0 8px; border-bottom: 1px solid var(--divider-color, #e8ebef);
          background: var(--card-background-color, #fff); }
        .toolbar .brand { margin-right: auto; }
        .brand { display: flex; align-items: center; gap: 10px; min-width: 0; font-size: 17px; font-weight: 650; letter-spacing: -.3px; }
        .brand-mark { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 10px; color: var(--primary-color, #7254d2);
          background: color-mix(in srgb, var(--primary-color, #7254d2) 10%, transparent); }
        .page { height: calc(100% - var(--header-height, 64px)); padding: 24px; max-width: 1040px; margin: 0 auto; }
        ha-card { display: flex; flex-direction: column; height: 100%; min-height: 0; overflow: hidden;
          background: var(--card-background-color, #fff); border-radius: 20px; border: 1px solid var(--divider-color, #e8ebef); box-shadow: none; }
        .messages { flex: 1; min-height: 0; overflow-y: auto; overflow-anchor: none; scroll-behavior: auto;
          max-height: ${this._full ? "none" : "520px"}; padding: 28px 28px 20px; scrollbar-width: thin; scrollbar-color: var(--divider-color, #d9dfe7) transparent; }
        .messages:focus-visible { outline: 2px solid var(--primary-color, #7254d2); outline-offset: -3px; }
        .content { display: flex; flex-direction: column; gap: 26px; max-width: 780px; margin: 0 auto; min-height: 120px; }
        .message { min-width: 0; max-width: 100%; line-height: 1.65; font-size: 15px; overflow-wrap: anywhere; }
        .message-body { min-width: 0; white-space: pre-wrap; }
        ha-markdown.message-body { display: block; white-space: normal; font: inherit; }
        .message-label { display: flex; align-items: center; gap: 7px; margin-bottom: 8px; color: var(--secondary-text-color, #727985); font-size: 12px; font-weight: 600; }
        .message-label svg { width: 15px; height: 15px; color: var(--primary-color, #7254d2); }
        .user { align-self: flex-end; max-width: 85%; padding: 11px 17px; border-radius: 18px 18px 5px 18px;
          background: color-mix(in srgb, var(--primary-color, #7254d2) 9%, var(--card-background-color, #fff)); }
        .assistant { align-self: stretch; padding: 0 2px; }
        .error { align-self: stretch; padding: 12px 16px; border-radius: 12px; color: var(--error-color, #c53a42);
          background: color-mix(in srgb, var(--error-color, #c53a42) 7%, transparent); }
        .empty { align-self: center; text-align: center; padding: 44px 16px 32px; color: var(--secondary-text-color, #727985); }
        .empty .brand-mark { width: 52px; height: 52px; margin: 0 auto 18px; border-radius: 18px; }
        .empty .brand-mark svg { width: 27px; height: 27px; }
        .empty h2 { margin: 0 0 7px; color: var(--primary-text-color, #20232b); font-size: 23px; font-weight: 600; letter-spacing: -.6px; }
        .empty p { margin: 0; font-size: 14px; }
        .typing { display: inline-flex; align-items: center; gap: 5px; height: 28px; }
        .typing i { width: 6px; height: 6px; border-radius: 50%; background: var(--secondary-text-color, #727985); animation: pulse 1.3s ease-in-out infinite; }
        .typing i:nth-child(2) { animation-delay: .16s; } .typing i:nth-child(3) { animation-delay: .32s; }
        @keyframes pulse { 0%, 65%, 100% { opacity: .3; transform: translateY(0); } 32% { opacity: .9; transform: translateY(-3px); } }
        .composer-area { flex-shrink: 0; padding: 12px 24px 16px; }
        form { display: flex; align-items: flex-end; gap: 12px; padding: 10px 10px 10px 16px; max-width: 780px; margin: 0 auto;
          border: 1px solid var(--divider-color, #dce1e7); border-radius: 18px; background: var(--primary-background-color, #f7f8fa); transition: border-color .15s, box-shadow .15s; }
        form:focus-within { border-color: color-mix(in srgb, var(--primary-color, #7254d2) 55%, var(--divider-color, #dce1e7));
          box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary-color, #7254d2) 8%, transparent); }
        textarea { display: block; flex: 1; min-width: 0; min-height: 38px; max-height: 180px; padding: 7px 0; resize: none; border: 0; outline: 0;
          background: transparent; color: var(--primary-text-color, #20232b); font: inherit; font-size: 15px; line-height: 24px; }
        textarea::placeholder { color: var(--secondary-text-color, #858d99); }
        button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; font: inherit; cursor: pointer; transition: background .15s, opacity .15s; }
        button:focus-visible { outline: 2px solid var(--primary-color, #7254d2); outline-offset: 3px; }
        button:disabled { cursor: default; opacity: .4; }
        .new { min-height: 36px; flex-shrink: 0; padding: 6px 10px; border: 0; border-radius: 10px; background: transparent;
          color: var(--secondary-text-color, #727985); font-size: 13px; }
        .new:not(:disabled):hover { background: var(--secondary-background-color, #f0f2f5); color: var(--primary-text-color, #20232b); }
        .send { width: 38px; height: 38px; flex-shrink: 0; padding: 0; border: 0; border-radius: 12px;
          background: var(--primary-color, #7254d2); color: var(--text-primary-color, #fff); }
        .send:not(:disabled):hover { filter: brightness(.94); }
        .composer-hint { max-width: 780px; margin: 7px auto 0; text-align: center; color: var(--secondary-text-color, #858d99); font-size: 11px; }
        @media (max-width: 600px) {
          .page { padding: 0; } ha-card { border: 0; border-radius: ${this._full ? "0" : "16px"}; }
          .toolbar, .card-heading { padding-right: 12px; } .card-heading { padding-left: 16px; }
          .messages { padding: 20px 16px; } .content { gap: 22px; } .user { max-width: 92%; }
          .composer-area { padding: 10px 12px max(12px, env(safe-area-inset-bottom)); } .composer-hint { display: none; }
          .empty { padding-top: 30px; } .empty h2 { font-size: 21px; }
        }
        @media (prefers-reduced-motion: reduce) { .typing i { animation: none; opacity: .6; } }
      </style>
      ${this._full ? `<div class="toolbar"><ha-menu-button></ha-menu-button>${brand}${newButton}</div><div class="page">` : ""}
      <ha-card>
        ${this._full ? "" : `<header class="card-heading">${brand}${newButton}</header>`}
        <div class="messages" tabindex="0" role="log" aria-live="polite"><div class="content"></div></div>
        <div class="composer-area">
          <form><textarea rows="1" autocomplete="off" aria-label="${this._text("placeholder")}" placeholder="${this._text("placeholder")}"></textarea><button class="send" type="submit" aria-label="${this._text("send")}" title="${this._text("send")}">${this._icon("send")}</button></form>
          <div class="composer-hint">${this._text("hint")}</div>
        </div>
      </ha-card>
      ${this._full ? "</div>" : ""}`;
    this.shadowRoot.querySelector("form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      this._send();
    });
    this.shadowRoot.querySelector("button.new").addEventListener("click", () => this._newConversation());
    const input = this.shadowRoot.querySelector("textarea");
    input.addEventListener("input", () => { this._resizeInput(); this._updateComposer(); });
    input.addEventListener("keydown", (event) => this._handleComposerKeyDown(event));
    this._updateComposer();

    // Stay at the newest message. <ha-markdown> renders after a moment, and Home Assistant
    // redraws the panel when the browser tab comes back, so the content grows after any
    // scroll made when it was added -- which used to leave the view part-way up. Whenever
    // the content or the list's own size changes, scroll to the end again, unless the user
    // has scrolled up to read; scrolling back down resumes following.
    const list = this.shadowRoot.querySelector(".messages");
    this._stick = true;
    list.addEventListener("scroll", () => this._handleScroll());
    list.addEventListener("wheel", (event) => {
      if (event.deltaY < 0) this._stick = false;
    }, { passive: true });
    list.addEventListener("keydown", (event) => {
      if (["ArrowUp", "PageUp", "Home"].includes(event.key)) this._stick = false;
    });
    this._observeMessages();
    this._renderMessages();
  }

  connectedCallback() {
    this._observeMessages();
    this._scrollToEnd(true);
  }

  disconnectedCallback() {
    this._resizeObserver?.disconnect();
    if (this._scrollFrame != null) window.cancelAnimationFrame(this._scrollFrame);
    this._scrollFrame = null;
  }

  _observeMessages() {
    this._resizeObserver?.disconnect();
    const list = this.shadowRoot?.querySelector(".messages");
    if (window.ResizeObserver && list) {
      this._listVisible = list.clientHeight > 0;
      this._resizeObserver = new ResizeObserver(() => {
        const visible = list.clientHeight > 0;
        const reopened = visible && !this._listVisible;
        this._listVisible = visible;
        this._scrollToEnd(reopened);
      });
      this._resizeObserver.observe(list);
      this._resizeObserver.observe(list.querySelector(".content"));
    }
  }

  _scrollToEnd(force = false) {
    if (force) this._stick = true;
    this._applyScrollToEnd();
    // Opening a panel and rendering markdown can change its layout after this
    // call. Retry after layout, while still allowing an upward user scroll.
    if (this._stick && this._scrollFrame == null && window.requestAnimationFrame) {
      this._scrollFrame = window.requestAnimationFrame(() => {
        this._scrollFrame = null;
        this._applyScrollToEnd();
      });
    }
  }

  _applyScrollToEnd() {
    const list = this.shadowRoot?.querySelector(".messages");
    if (!list) return;
    if (this._stick) {
      list.scrollTop = list.scrollHeight;
      this._lastScrollTop = list.scrollTop;
    }
  }

  _handleScroll() {
    const list = this.shadowRoot?.querySelector(".messages");
    if (!list) return;
    if (list.scrollHeight - list.scrollTop - list.clientHeight < 48) {
      this._stick = true;
    } else if (list.scrollTop < (this._lastScrollTop ?? list.scrollTop)) {
      this._stick = false;
    }
    // Content growth can move the bottom without the user scrolling up. A
    // queued scroll event from our last adjustment must not disable following.
    this._lastScrollTop = list.scrollTop;
  }

  // Adds and removes bubbles rather than rebuilding the list: a rebuild re-renders every
  // earlier reply's markdown, and each one changing height moves the view.
  _renderMessages() {
    const content = this.shadowRoot?.querySelector(".content");
    if (!content) return;
    if (!this._messages.length) {
      content.replaceChildren(this._bubble({ role: "empty" }));
      this._scrollToEnd();
      return;
    }
    const shown = [...content.children];
    let kept = 0;
    while (kept < shown.length && shown[kept]._message === this._messages[kept]) kept++;
    shown.slice(kept).forEach((bubble) => bubble.remove());
    content.append(...this._messages.slice(kept).map((message) => this._bubble(message)));
    this._scrollToEnd();
  }

  // While a reply streams in, only its bubble changes.
  _updateLastBubble(text) {
    const last = this.shadowRoot?.querySelector(".content")?.lastElementChild;
    if (!last) return;
    let body = last.querySelector(".message-body");
    if (!body) {
      last.querySelector(".typing")?.remove();
      body = this._messageBody({ role: "assistant", content: text });
      last.append(body);
    } else if (body.tagName === "HA-MARKDOWN") body.content = text;
    else body.textContent = text;
    this._scrollToEnd();
  }

  _bubble(message) {
    const { role } = message;
    const el = document.createElement("div");
    el._message = message;
    el.className = `message ${role}`;
    if (role === "empty") {
      el.innerHTML = `<span class="brand-mark" aria-hidden="true">${this._icon("spark")}</span><h2>${this._text(this._fetched ? "greeting" : "loading")}</h2>${this._fetched ? `<p>${this._text("welcome")}</p>` : ""}`;
      return el;
    }
    if (role === "assistant") {
      const label = document.createElement("div");
      label.className = "message-label";
      label.innerHTML = this._icon("spark");
      const name = document.createElement("span");
      name.textContent = this._config?.title ?? "Lumo";
      label.append(name);
      el.append(label);
      if (!message.content) {
        const typing = document.createElement("span");
        typing.className = "typing";
        typing.setAttribute("role", "status");
        typing.setAttribute("aria-label", this._text("thinking"));
        typing.innerHTML = "<i></i><i></i><i></i>";
        el.append(typing);
        return el;
      }
    }
    el.append(this._messageBody(message));
    return el;
  }

  _messageBody({ role, content }) {
    // Replies are model output: never innerHTML. <ha-markdown> sanitises what it renders;
    // where it is not loaded, plain text.
    const markdown = role === "assistant" && customElements.get("ha-markdown");
    const el = document.createElement(markdown ? "ha-markdown" : "div");
    el.className = "message-body";
    if (markdown) {
      el.breaks = true;
      el.content = content || "";
    } else {
      el.textContent = content || "";
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
