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
from homeassistant.core import Context, HomeAssistant

from custom_components.lumo.history import async_get_history, websocket_history

CARD = Path(__file__).parent.parent / "custom_components" / "lumo" / "frontend" / "lumo-chat-card.js"


async def load_registries(hass: HomeAssistant) -> None:
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    # Whether a registry needs async_setup before async_load differs between cores.
    for registry_module in (dr, er):
        if hasattr(registry_module, "async_setup"):
            registry_module.async_setup(hass)
        await registry_module.async_load(hass)


def history_for(user: str, message: dict[str, Any] | None = None) -> list[Any]:
    async def main() -> list[Any]:
        hass = HomeAssistant(tempfile.mkdtemp())
        await load_registries(hass)  # always there in a running Home Assistant
        try:
            for owner in ("alice", "bob"):
                chat_log = conversation.ChatLog(hass, "cid")
                chat_log.async_add_user_content(conversation.UserContent(content=f"{owner} asks"))
                chat_log.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(agent_id="conversation.lumo", content=f"{owner} answered")
                )
                await async_get_history(hass).async_save(owner, chat_log, f"conversation.lumo_{owner}")

            sent: list[Any] = []
            connection = SimpleNamespace(
                user=SimpleNamespace(id=user),
                send_result=lambda msg_id, result: sent.append(result),
            )
            websocket_history(hass, connection, {"id": 1, "type": "lumo/history", **(message or {})})
            await hass.async_block_till_done(wait_background_tasks=True)  # async_response runs in one
            return sent
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(main())


def test_returns_the_callers_turns() -> None:
    assert history_for("alice") == [
        {
            "agent_id": "conversation.lumo_alice",
            "messages": [
                {"role": "user", "content": "alice asks"},
                {"role": "assistant", "content": "alice answered"},
            ],
        }
    ]


def test_a_user_without_history_gets_an_empty_list() -> None:
    assert history_for("carol") == [{"agent_id": None, "messages": []}]


def test_an_agent_can_be_asked_for_and_another_users_agent_gives_nothing() -> None:
    assert history_for("alice", {"agent_id": "conversation.lumo_bob"}) == [
        {"agent_id": "conversation.lumo_bob", "messages": []}
    ]


def test_the_card_ships_and_defines_its_element() -> None:
    source = CARD.read_text()
    assert 'customElements.define("lumo-chat-card"' in source
    assert "lumo/history" in source
    assert "lumo/chat" in source


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


def ws_call(handler, message: dict[str, Any], user: str = "alice", setup=None) -> list[tuple[str, Any]]:
    """Run a websocket handler on a fresh HomeAssistant; return what it sent, in order."""

    async def main() -> list[tuple[str, Any]]:
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            if setup:
                await setup(hass)
            else:
                await load_registries(hass)
            sent: list[tuple[str, Any]] = []
            connection = SimpleNamespace(
                user=SimpleNamespace(id=user),
                subscriptions={},
                send_result=lambda msg_id, result=None: sent.append(("result", result)),
                send_message=lambda message: sent.append(("event", message["event"])),
                context=lambda msg: Context(user_id=user),
            )
            handler(hass, connection, {"id": 1, **message})
            await hass.async_block_till_done(wait_background_tasks=True)  # async_response runs in one
            if setup and hasattr(setup, "after"):
                sent.append(("after", await setup.after(hass)))
            return sent
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(main())


def test_new_conversation_clears_only_the_callers_history_with_that_agent() -> None:
    from custom_components.lumo.history import websocket_clear_history

    async def setup(hass: HomeAssistant) -> None:
        for owner, agent in (("alice", "conversation.a"), ("alice", "conversation.b"), ("bob", "conversation.a")):
            chat_log = conversation.ChatLog(hass, "cid")
            chat_log.async_add_user_content(conversation.UserContent(content=f"{owner} asks"))
            chat_log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(agent_id=agent, content="answered")
            )
            await async_get_history(hass).async_save(owner, chat_log, agent)

    async def after(hass: HomeAssistant) -> dict[tuple[str, str], bool]:
        history = async_get_history(hass)
        return {
            (user, agent): bool((await history.async_get_messages(user, agent))[1])
            for user, agent in (("alice", "conversation.a"), ("alice", "conversation.b"), ("bob", "conversation.a"))
        }

    setup.after = after
    sent = ws_call(websocket_clear_history, {"type": "lumo/history/clear", "agent_id": "conversation.a"}, setup=setup)

    assert sent[0] == ("result", None)
    assert sent[1][1] == {
        ("alice", "conversation.a"): False,
        ("alice", "conversation.b"): True,
        ("bob", "conversation.a"): True,
    }


def test_chat_streams_the_reply_then_reports_the_result(monkeypatch) -> None:
    """The card used conversation/process, which answers only when the reply is complete."""
    from homeassistant.components.conversation import agent_manager
    from homeassistant.helpers import chat_session

    from custom_components.lumo.history import websocket_chat

    seen: dict[str, Any] = {}

    async def fake_agent(hass, text, conversation_id, context, language=None, agent_id=None, **kwargs):
        """What a ConversationEntity does: open its chat log -- the caller's -- and stream into it."""
        seen.update(user=context.user_id, agent=agent_id, text=text)
        user_input = conversation.ConversationInput(
            text=text,
            context=context,
            conversation_id=conversation_id,
            device_id=None,
            satellite_id=None,
            language=language or "en",
            agent_id=agent_id,
        )
        with (
            chat_session.async_get_chat_session(hass, conversation_id) as session,
            conversation.async_get_chat_log(hass, session, user_input) as chat_log,
        ):

            async def deltas():
                yield {"role": "assistant"}
                yield {"content": "Hel"}
                yield {"content": "lo"}

            async for _ in chat_log.async_add_delta_content_stream(agent_id, deltas()):
                pass
            return conversation.async_get_result_from_chat_log(user_input, chat_log)

    monkeypatch.setattr(agent_manager, "async_converse", fake_agent)
    monkeypatch.setattr(conversation, "async_converse", fake_agent)

    sent = ws_call(websocket_chat, {"type": "lumo/chat", "text": "hi", "agent_id": "conversation.lumo"})

    assert seen == {"user": "alice", "agent": "conversation.lumo", "text": "hi"}
    events = [payload for kind, payload in sent if kind == "event"]
    assert events[:2] == [{"delta": "Hel"}, {"delta": "lo"}]
    done = events[2]["done"]
    assert done["response"]["speech"]["plain"]["speech"] == "Hello"
    assert done["conversation_id"]
    assert sent[-1] == ("result", None)


def test_the_card_streams_renders_markdown_and_can_start_over() -> None:
    source = CARD.read_text()
    assert "lumo/chat" in source and "subscribeMessage" in source
    assert "ha-markdown" in source
    assert "lumo/history/clear" in source


async def register_lumo_agent(hass: HomeAssistant) -> None:
    """A Lumo conversation entity in the registry, plus another integration's agent."""
    from homeassistant.helpers import entity_registry as er

    await load_registries(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("conversation", "openai_conversation", "x", suggested_object_id="openai")
    registry.async_get_or_create("conversation", "lumo", "s", suggested_object_id="lumo_voice")


def test_without_history_the_card_is_given_a_lumo_agent() -> None:
    """With no agent configured and nothing saved, the card used to get none -- and core then
    answered with its own built-in agent: "I see you're targeting 'Home Assistant'..."."""
    sent = ws_call(websocket_history, {"type": "lumo/history"}, user="carol", setup=register_lumo_agent)
    assert sent[0] == ("result", {"agent_id": "conversation.lumo_voice", "messages": []})


def test_chat_without_an_agent_goes_to_lumo_not_the_built_in_agent(monkeypatch) -> None:
    from homeassistant.components.conversation import agent_manager

    from custom_components.lumo.history import websocket_chat

    seen: dict[str, Any] = {}

    async def fake_agent(hass, text, conversation_id, context, language=None, agent_id=None, **kwargs):
        seen["agent"] = agent_id
        raise RuntimeError("stop here")

    monkeypatch.setattr(agent_manager, "async_converse", fake_agent)
    monkeypatch.setattr(conversation, "async_converse", fake_agent)

    try:
        ws_call(websocket_chat, {"type": "lumo/chat", "text": "hi"}, setup=register_lumo_agent)
    except RuntimeError:
        pass

    assert seen == {"agent": "conversation.lumo_voice"}
