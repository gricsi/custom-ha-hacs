const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");
const source = readFileSync(require("node:path").join(__dirname, "../../custom_components/lumo/frontend/lumo-chat-card.js"), "utf8");

function load() {
  const elements = new Map();
  const context = vm.createContext({
    HTMLElement: class {},
    customElements: {
      get: (name) => elements.get(name),
      define: (name, value) => {
        assert.ok(!elements.has(name), `duplicate element ${name}`);
        elements.set(name, value);
      },
    },
    window: {},
  });
  const evaluate = () => vm.runInContext(`{${source}\n}`, context);
  evaluate();
  const card = new (elements.get("lumo-chat-card"))();
  const input = { value: "hello" };
  const list = { scrollTop: 0, scrollHeight: 500, querySelector: () => ({}) };
  card.shadowRoot = { querySelector: (selector) => selector === "input" ? input : selector === ".messages" ? list : null };
  card._messages = [];
  card._renderMessages = () => {};
  card._updateLastBubble = () => {};
  return { card, context, input, list, evaluate };
}

test("loading the module twice registers one card and one panel", () => {
  const { context, evaluate } = load();
  evaluate();
  assert.equal(context.window.customCards.length, 1);
});

test("streams the reply and retains the conversation for the next message", async () => {
  const { card, input } = load();
  const requests = [];
  let unsubscribed = 0;
  card._agentId = "conversation.lumo";
  card._hass = { language: "en", connection: { subscribeMessage: async (onEvent, request) => {
    requests.push(request);
    onEvent({ delta: "Hel" });
    onEvent({ delta: "lo" });
    onEvent({ done: { conversation_id: "cid", response: { speech: { plain: { speech: "Hello" } } } } });
    return async () => { unsubscribed++; };
  } } };
  await card._send();
  assert.equal(card._messages[1].content, "Hello");
  assert.equal(card._conversationId, "cid");
  input.value = "again";
  await card._send();
  assert.equal(requests[1].conversation_id, "cid");
  assert.equal(unsubscribed, 2);
  assert.equal(card._busy, false);
});

test("a failed stream allows another request", async () => {
  const { card } = load();
  card._hass = { connection: { subscribeMessage: async () => { throw new Error("Disconnected"); } } };
  await card._send();
  assert.equal(card._busy, false);
  assert.equal(card._messages.at(-1).content, "Disconnected");
});

test("New clears the selected agent and the current conversation", async () => {
  const { card } = load();
  const calls = [];
  card._agentId = "conversation.lumo";
  card._conversationId = "old";
  card._messages.push({ role: "user", content: "old" });
  card._hass = { callWS: async (request) => calls.push(request) };
  await card._newConversation();
  assert.equal(calls[0].agent_id, "conversation.lumo");
  assert.equal(card._messages.length, 0);
  assert.equal(card._conversationId, null);
});

test("scroll follows replies only while the user is at the bottom", () => {
  const { card, list } = load();
  card._stick = true;
  card._scrollToEnd();
  assert.equal(list.scrollTop, 500);
  card._stick = false;
  list.scrollTop = 10;
  card._scrollToEnd();
  assert.equal(list.scrollTop, 10);
  card._scrollToEnd(true);
  assert.equal(list.scrollTop, 500);
});

test("a queued scroll event after markdown grows does not disable following", () => {
  const { card, list } = load();
  list.clientHeight = 100;
  card._scrollToEnd(true);
  list.scrollHeight = 1200;
  card._handleScroll();
  assert.equal(card._stick, true);
  card._scrollToEnd();
  assert.equal(list.scrollTop, 1200);
  list.scrollTop = 300;
  card._handleScroll();
  assert.equal(card._stick, false);
});

test("opening the chat returns to the last message after reading older messages", () => {
  const { card, list } = load();
  card._stick = false;
  list.scrollTop = 10;
  card.connectedCallback();
  assert.equal(list.scrollTop, list.scrollHeight);
  assert.equal(card._stick, true);
});

test("the opening scroll retries after layout and respects an upward scroll", () => {
  const { card, context, list } = load();
  let frame;
  context.window.requestAnimationFrame = (callback) => { frame = callback; return 1; };
  card._scrollToEnd(true);
  list.scrollHeight = 1500;
  frame();
  assert.equal(list.scrollTop, 1500);
  card._scrollToEnd();
  list.scrollTop = 100;
  card._handleScroll();
  frame();
  assert.equal(list.scrollTop, 100);
});

test("revealing a cached panel opens at the bottom", () => {
  const { card, context, list } = load();
  let resized;
  context.window.ResizeObserver = context.ResizeObserver = class {
    constructor(callback) { resized = callback; }
    disconnect() {}
    observe() {}
  };
  list.clientHeight = 0;
  card._observeMessages();
  card._stick = false;
  list.scrollTop = 10;
  list.clientHeight = 300;
  resized();
  assert.equal(list.scrollTop, list.scrollHeight);
  assert.equal(card._stick, true);
});

test("failed history is retried on a replacement connection", async () => {
  const { card } = load();
  card.hass = { connection: {}, callWS: async () => { throw new Error("offline"); } };
  await new Promise(setImmediate);
  assert.equal(card._historyFailed, true);
  card.hass = { connection: {}, callWS: async () => ({ agent_id: "conversation.lumo", messages: [] }) };
  await new Promise(setImmediate);
  assert.equal(card._historyFailed, false);
  assert.equal(card._agentId, "conversation.lumo");
});

test("removal disconnects the size observer and reconnect restores it", () => {
  const { card, context } = load();
  let disconnected = 0, observed = 0;
  context.window.ResizeObserver = context.ResizeObserver = class {
    disconnect() { disconnected++; }
    observe() { observed++; }
  };
  card.connectedCallback();
  assert.equal(observed, 2);
  card.disconnectedCallback();
  assert.equal(disconnected, 1);
  card.connectedCallback();
  assert.equal(observed, 4);
});
