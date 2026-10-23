"""Per-user conversation history that outlives Home Assistant's chat log.

Core keeps a chat log in memory for five minutes, and the Assist dialog forgets its
conversation id when closed, so every reopen starts from nothing. This keeps the text of
each user's last turns in `.storage/lumo.history` and seeds a fresh chat log with them.

Only user and assistant text is kept. Tool calls are left out because a restored call
without its result is an invalid request, and tool results can carry far more than the
conversation itself. Nothing outside `.storage` is ever written.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import conversation, websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_KEY = f"{DOMAIN}.history"
MAX_MESSAGES = 20
CARD_URL = f"/{DOMAIN}/lumo-chat-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "lumo-chat-card.js"


class ConversationHistory:
    """Load, seed and save the recent text turns of each user."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, list[dict[str, str]]]] = Store(hass, 1, STORAGE_KEY)
        self._data: dict[str, list[dict[str, str]]] | None = None

    async def _async_load(self) -> dict[str, list[dict[str, str]]]:
        if self._data is None:
            self._data = await self._store.async_load() or {}
        return self._data

    async def async_restore(self, user_id: str | None, chat_log: conversation.ChatLog, agent_id: str) -> None:
        """Insert the user's saved turns after the system prompt of a fresh chat log."""
        # A log with a reply in it is still live in core and already holds its history.
        if not user_id or any(c.role == "assistant" for c in chat_log.content):
            return
        saved = (await self._async_load()).get(user_id, [])
        chat_log.content[1:1] = [
            conversation.UserContent(content=turn["content"])
            if turn["role"] == "user"
            else conversation.AssistantContent(agent_id=agent_id, content=turn["content"])
            for turn in saved
        ]

    async def async_save(self, user_id: str | None, chat_log: conversation.ChatLog) -> None:
        """Keep the last text turns of the chat log for the user."""
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
        data[user_id] = turns
        await self._store.async_save(data)

    async def async_get_messages(self, user_id: str) -> list[dict[str, str]]:
        """Return the user's saved turns as {"role", "content"} dicts."""
        return list((await self._async_load()).get(user_id, []))

    async def async_clear(self, user_id: str | None) -> None:
        """Forget one user's turns, or everyone's when no user is given."""
        data = await self._async_load()
        if user_id is None:
            data.clear()
        else:
            data.pop(user_id, None)
        await self._store.async_save(data)


@callback
def async_get_history(hass: HomeAssistant) -> ConversationHistory:
    """Return the one history every Lumo agent shares, so none overwrites another's users."""
    if STORAGE_KEY not in hass.data:
        hass.data[STORAGE_KEY] = ConversationHistory(hass)
    return hass.data[STORAGE_KEY]


@websocket_api.websocket_command({vol.Required("type"): "lumo/history"})
@websocket_api.async_response
async def websocket_history(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the caller's saved turns for the chat card. Read-only, and only ever the caller's."""
    messages = await async_get_history(hass).async_get_messages(connection.user.id)
    connection.send_result(msg["id"], {"messages": messages})


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the chat card and load it on every dashboard, so it needs no resource entry."""
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=False)])
    add_extra_js_url(hass, f"{CARD_URL}?v={CARD_PATH.stat().st_mtime_ns}")
