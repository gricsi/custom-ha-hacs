"""Tests for the chat card's server side.

The card shows the saved conversation when it opens, which is what the Assist dialog
cannot do. It reads it through lumo/history, which answers with the caller's own turns
only -- the user is taken from the websocket connection, never from the message.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant

from custom_components.lumo.history import async_get_history, websocket_history

CARD = Path(__file__).parent.parent / "custom_components" / "lumo" / "frontend" / "lumo-chat-card.js"


def history_for(user: str, message: dict[str, Any] | None = None) -> list[Any]:
    async def main() -> list[Any]:
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            for owner in ("alice", "bob"):
                chat_log = conversation.ChatLog(hass, "cid")
                chat_log.async_add_user_content(conversation.UserContent(content=f"{owner} asks"))
                chat_log.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(agent_id="conversation.lumo", content=f"{owner} answered")
                )
                await async_get_history(hass).async_save(owner, chat_log)

            sent: list[Any] = []
            connection = SimpleNamespace(
                user=SimpleNamespace(id=user),
                send_result=lambda msg_id, result: sent.append(result),
            )
            websocket_history(hass, connection, {"id": 1, "type": "lumo/history", **(message or {})})
            await hass.async_block_till_done()
            return sent
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(main())


def test_returns_the_callers_turns() -> None:
    assert history_for("alice") == [
        {
            "messages": [
                {"role": "user", "content": "alice asks"},
                {"role": "assistant", "content": "alice answered"},
            ]
        }
    ]


def test_a_user_without_history_gets_an_empty_list() -> None:
    assert history_for("carol") == [{"messages": []}]


def test_the_card_ships_and_defines_its_element() -> None:
    source = CARD.read_text()
    assert 'customElements.define("lumo-chat-card"' in source
    assert "lumo/history" in source
    assert "conversation/process" in source


def test_a_user_id_in_the_message_is_ignored() -> None:
    assert history_for("alice", {"user_id": "bob"}) == history_for("alice")


def test_setup_serves_the_card_and_loads_it_on_every_dashboard() -> None:
    """No manual resource step: the file is served and added to the frontend's extra modules."""
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL

    from custom_components.lumo.history import CARD_URL, async_register_card

    served: list[Any] = []

    async def register(configs: list[Any]) -> None:
        served.extend(configs)

    hass = SimpleNamespace(
        data={DATA_EXTRA_MODULE_URL: set()},
        http=SimpleNamespace(async_register_static_paths=register),
    )
    asyncio.run(async_register_card(hass))

    assert [(c.url_path, Path(c.path)) for c in served] == [(CARD_URL, CARD)]
    assert any(url.startswith(CARD_URL) for url in hass.data[DATA_EXTRA_MODULE_URL])
