"""Per-user conversation history that outlives Home Assistant's chat log.

Core keeps a chat log in memory for five minutes, and the Assist dialog forgets its
conversation id when closed, so every reopen starts from nothing. This keeps the text of
each user's last turns with each agent in `.storage/lumo.history` and seeds a fresh chat
log with them.

Only user and assistant text is kept. Tool calls are left out because a restored call
without its result is an invalid request, and tool results can carry far more than the
conversation itself. History is per agent, so what an agent with tools said is never
replayed into another -- which may even talk to another backend. It expires after
RETENTION and goes with the user when they are deleted. Nothing outside `.storage` is
ever written.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import conversation, frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import chat_session
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STORAGE_KEY = f"{DOMAIN}.history"
MAX_MESSAGES = 20
RETENTION = timedelta(days=30)
# Batches the writes of a burst of turns into one; history is a convenience, so losing
# the last few seconds of it to a power cut is an acceptable trade for the SD card.
SAVE_DELAY = 10
CARD_URL = f"/{DOMAIN}/lumo-chat-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "lumo-chat-card.js"
PANEL_URL_PATH = DOMAIN
SIDEBAR_VISIBILITIES = ("hidden", "admin", "everyone")

# {agent_id: {user_id: {"updated": iso timestamp, "turns": [{"role", "content"}]}}}
HistoryData = dict[str, dict[str, dict[str, Any]]]


class ConversationHistory:
    """Load, seed and save the recent text turns of each user with each agent."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[HistoryData] = Store(hass, 1, STORAGE_KEY)
        self._data: HistoryData | None = None

    async def _async_load(self) -> HistoryData:
        if self._data is None:
            loaded = await self._store.async_load() or {}
            # A pre-release build stored a bare list per user; it is not worth migrating.
            self._data = {agent: users for agent, users in loaded.items() if isinstance(users, dict)}
        return self._data

    async def _async_turns(self, user_id: str, agent_id: str) -> list[dict[str, str]]:
        """The user's unexpired turns with the agent."""
        saved = (await self._async_load()).get(agent_id, {}).get(user_id)
        if not saved:
            return []
        updated = dt_util.parse_datetime(saved["updated"]) or datetime.min.replace(tzinfo=dt_util.UTC)
        if dt_util.utcnow() - updated > RETENTION:
            del self._data[agent_id][user_id]
            self._schedule_save()
            return []
        return list(saved["turns"])

    async def async_restore(self, user_id: str | None, chat_log: conversation.ChatLog, agent_id: str) -> None:
        """Insert the user's saved turns after the system prompt of a fresh chat log."""
        # A log with a reply in it is still live in core and already holds its history.
        if not user_id or any(c.role == "assistant" for c in chat_log.content):
            return
        chat_log.content[1:1] = [
            conversation.UserContent(content=turn["content"])
            if turn["role"] == "user"
            else conversation.AssistantContent(agent_id=agent_id, content=turn["content"])
            for turn in await self._async_turns(user_id, agent_id)
        ]

    async def async_save(self, user_id: str | None, chat_log: conversation.ChatLog, agent_id: str) -> None:
        """Keep the last text turns of the chat log for the user and agent."""
        if not user_id:
            return
        turns = [
            {"role": c.role, "content": c.content}
            for c in chat_log.content
            if c.role in ("user", "assistant") and c.content
        ][-MAX_MESSAGES:]
        # Opening on a reply would leave the model answering a question it never saw.
        if turns and turns[0]["role"] == "assistant":
            turns = turns[1:]
        data = await self._async_load()
        data.setdefault(agent_id, {})[user_id] = {"updated": dt_util.utcnow().isoformat(), "turns": turns}
        self._schedule_save()

    async def async_get_messages(self, user_id: str, agent_id: str | None) -> tuple[str | None, list[dict[str, str]]]:
        """Return an agent and the user's turns with it -- the most recent agent if none is given."""
        if agent_id is None:
            data = await self._async_load()
            recent = [(users[user_id]["updated"], agent) for agent, users in data.items() if user_id in users]
            if not recent:
                return None, []
            agent_id = max(recent)[1]
        return agent_id, await self._async_turns(user_id, agent_id)

    async def async_clear(self, user_id: str | None, agent_id: str | None = None) -> None:
        """Forget one user's turns -- with one agent, or with all -- or everyone's when no user is given."""
        data = await self._async_load()
        if user_id is None:
            data.clear()
        else:
            for agent, users in data.items():
                if agent_id is None or agent == agent_id:
                    users.pop(user_id, None)
        self._schedule_save()

    async def async_flush(self) -> None:
        """Write any pending change now."""
        if self._data is not None:
            await self._store.async_save(self._data)

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY)


@callback
def async_get_history(hass: HomeAssistant) -> ConversationHistory:
    """Return the one history every Lumo agent shares, so none overwrites another's users."""
    if STORAGE_KEY not in hass.data:
        history = hass.data[STORAGE_KEY] = ConversationHistory(hass)

        async def forget_removed_user(event: Event) -> None:
            await history.async_clear(event.data["user_id"])

        # The auth manager fires this when a user is deleted.
        hass.bus.async_listen("user_removed", forget_removed_user)
    return hass.data[STORAGE_KEY]


@websocket_api.websocket_command({vol.Required("type"): "lumo/history", vol.Optional("agent_id"): str})
@websocket_api.async_response
async def websocket_history(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the caller's saved turns for the chat card. Read-only, and only ever the caller's."""
    history = async_get_history(hass)
    agent_id, messages = await history.async_get_messages(connection.user.id, msg.get("agent_id"))
    agent_id = agent_id or _first_lumo_agent(hass)
    connection.send_result(msg["id"], {"agent_id": agent_id, "messages": messages})


@callback
def _first_lumo_agent(hass: HomeAssistant) -> str | None:
    """A Lumo conversation agent, for a user who has not talked to one yet.

    Without it the card has no agent to name, and core answers an unnamed request with its
    own built-in agent -- "I see you're targeting 'Home Assistant'..." -- not with Lumo.
    """
    registry = er.async_get(hass)
    agents = sorted(
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DOMAIN and entry.domain == "conversation" and not entry.disabled
    )
    return agents[0] if agents else None


@websocket_api.websocket_command({vol.Required("type"): "lumo/history/clear", vol.Required("agent_id"): str})
@websocket_api.async_response
async def websocket_clear_history(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Start a new conversation: forget the caller's history with one agent. Only ever the caller's."""
    await async_get_history(hass).async_clear(connection.user.id, msg["agent_id"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "lumo/chat",
        vol.Required("text"): str,
        vol.Optional("agent_id"): str,
        vol.Optional("conversation_id"): vol.Any(str, None),
        vol.Optional("language"): str,
    }
)
@websocket_api.async_response
async def websocket_chat(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Run one turn for the chat card, sending the reply as it is written.

    conversation/process answers only once the reply is complete. This does what the Assist
    pipeline does: open the chat log with a delta listener first, so the agent writes into
    that same log. Sent as {"delta": text} events, then {"done": result}, then the result
    that ends the subscription. The turn runs as the connection's user.
    """
    # Lets the card's unsubscribe after "done" succeed; there is nothing left to cancel.
    connection.subscriptions[msg["id"]] = lambda: None

    @callback
    def forward(chat_log: conversation.ChatLog, delta: dict[str, Any]) -> None:
        if content := delta.get("content"):
            connection.send_message(websocket_api.event_message(msg["id"], {"delta": content}))

    with (
        chat_session.async_get_chat_session(hass, msg.get("conversation_id")) as session,
        conversation.async_get_chat_log(hass, session, chat_log_delta_listener=forward),
    ):
        agent_id = msg.get("agent_id")
        if agent_id is None:
            agent_id, _ = await async_get_history(hass).async_get_messages(connection.user.id, None)
            agent_id = agent_id or _first_lumo_agent(hass)
        result = await conversation.async_converse(
            hass,
            msg["text"],
            session.conversation_id,
            connection.context(msg),
            msg.get("language"),
            agent_id,
        )
    connection.send_message(websocket_api.event_message(msg["id"], {"done": result.as_dict()}))
    connection.send_result(msg["id"])


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the chat card and load it on every dashboard, so it needs no resource entry."""
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=False)])
    frontend.add_extra_js_url(hass, _card_module_url())


def _card_module_url() -> str:
    """The card's URL, versioned by its mtime so an update is not served from cache."""
    return f"{CARD_URL}?v={CARD_PATH.stat().st_mtime_ns}"


async def async_set_panel_visibility(hass: HomeAssistant, entry_id: str, visibility: str | None) -> None:
    """Record one entry's sidebar setting (None when it unloads) and apply the widest one.

    There is one /lumo panel however many Lumo entries there are, so each entry's setting
    is kept and the most permissive wins; otherwise the last entry to load or unload would
    decide for all of them. panel_custom refuses to overwrite a panel, so any existing one
    is removed first.
    """
    settings: dict[str, str] = hass.data.setdefault(f"{DOMAIN}_panel", {})
    if visibility is None:
        settings.pop(entry_id, None)
    else:
        settings[entry_id] = visibility
    widest = max(settings.values(), key=SIDEBAR_VISIBILITIES.index, default="hidden")

    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    if widest == "hidden":
        return
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name="lumo-chat-panel",
        sidebar_title="Lumo",
        sidebar_icon="mdi:chat-processing",
        module_url=_card_module_url(),
        require_admin=widest == "admin",
    )
