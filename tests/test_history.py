"""Tests for the per-user conversation history.

Home Assistant keeps a chat log in memory for five minutes and the Assist dialog drops its
conversation id on close, so reopening the dialog starts from nothing. history.py keeps the
text of each user's recent turns in `.storage` and seeds a fresh chat log with them.

Real HomeAssistant and real ChatLog, as in test_render_template.py: the restore writes into
ChatLog.content, which is not public API, so only the genuine class notices core reshaping it.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from custom_components.lumo.history import MAX_MESSAGES, STORAGE_KEY, ConversationHistory

AGENT = "conversation.lumo"


def run(body: Callable[[HomeAssistant], Awaitable[Any]], config_dir: str | None = None) -> Any:
    """Run body against a fresh HomeAssistant, stopping it afterwards."""

    async def main() -> Any:
        hass = HomeAssistant(config_dir or tempfile.mkdtemp())
        try:
            return await body(hass)
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(main())


def new_log(hass: HomeAssistant, text: str) -> conversation.ChatLog:
    """A chat log as core hands it over: system prompt plus the current user message."""
    chat_log = conversation.ChatLog(hass, "cid")
    chat_log.async_add_user_content(conversation.UserContent(content=text))
    return chat_log


def answer(chat_log: conversation.ChatLog, text: str) -> None:
    chat_log.async_add_assistant_content_without_tools(conversation.AssistantContent(agent_id=AGENT, content=text))


def texts(chat_log: conversation.ChatLog) -> list[tuple[str, str]]:
    return [(c.role, c.content) for c in chat_log.content if c.role != "system"]


def test_fresh_log_is_seeded_with_the_users_previous_turns() -> None:
    async def body(hass: HomeAssistant) -> None:
        history = ConversationHistory(hass)
        first = new_log(hass, "Open the garage")
        answer(first, "Opened.")
        await history.async_save("alice", first)

        second = new_log(hass, "Close it again")
        await history.async_restore("alice", second, AGENT)

        assert second.content[0].role == "system"
        assert texts(second) == [
            ("user", "Open the garage"),
            ("assistant", "Opened."),
            ("user", "Close it again"),
        ]

    run(body)


def test_live_log_is_left_alone() -> None:
    """Within core's five minutes the log already holds the history; restoring would double it."""

    async def body(hass: HomeAssistant) -> None:
        history = ConversationHistory(hass)
        chat_log = new_log(hass, "Open the garage")
        answer(chat_log, "Opened.")
        await history.async_save("alice", chat_log)

        chat_log.async_add_user_content(conversation.UserContent(content="Close it"))
        await history.async_restore("alice", chat_log, AGENT)

        assert texts(chat_log) == [("user", "Open the garage"), ("assistant", "Opened."), ("user", "Close it")]

    run(body)


def test_history_is_per_user_and_skipped_without_one() -> None:
    async def body(hass: HomeAssistant) -> None:
        history = ConversationHistory(hass)
        chat_log = new_log(hass, "My secret")
        answer(chat_log, "Noted.")
        await history.async_save("alice", chat_log)
        await history.async_save(None, chat_log)

        bob = new_log(hass, "Hi")
        await history.async_restore("bob", bob, AGENT)
        anonymous = new_log(hass, "Hi")
        await history.async_restore(None, anonymous, AGENT)

        assert texts(bob) == [("user", "Hi")]
        assert texts(anonymous) == [("user", "Hi")]

    run(body)


def test_only_text_is_kept_and_capped() -> None:
    """Tool calls and results are left out: a restored tool call without its result is invalid."""

    async def body(hass: HomeAssistant) -> None:
        history = ConversationHistory(hass)
        chat_log = new_log(hass, "turn 0")
        chat_log.content.append(
            conversation.AssistantContent(
                agent_id=AGENT,
                content=None,
                tool_calls=[llm.ToolInput(id="t1", tool_name="get_state", tool_args={})],
            )
        )
        for i in range(1, MAX_MESSAGES):
            answer(chat_log, f"reply {i}")
            chat_log.async_add_user_content(conversation.UserContent(content=f"turn {i}"))
        await history.async_save("alice", chat_log)

        fresh = new_log(hass, "now")
        await history.async_restore("alice", fresh, AGENT)
        restored = texts(fresh)[:-1]

        assert MAX_MESSAGES - 1 <= len(restored) <= MAX_MESSAGES
        assert restored[-1] == ("user", f"turn {MAX_MESSAGES - 1}")
        assert all(content for _, content in restored)
        assert restored[0][0] == "user", "a restored history must not open on an orphaned assistant reply"

    run(body)


def test_survives_a_restart() -> None:
    config_dir = tempfile.mkdtemp()

    async def save(hass: HomeAssistant) -> None:
        chat_log = new_log(hass, "Remember me")
        answer(chat_log, "Will do.")
        await ConversationHistory(hass).async_save("alice", chat_log)

    async def restore(hass: HomeAssistant) -> list[tuple[str, str]]:
        chat_log = new_log(hass, "Still there?")
        await ConversationHistory(hass).async_restore("alice", chat_log, AGENT)
        return texts(chat_log)

    run(save, config_dir)
    assert run(restore, config_dir)[:2] == [("user", "Remember me"), ("assistant", "Will do.")]


def test_writes_nothing_but_its_own_storage_file() -> None:
    """History must never touch configuration.yaml, packages, or Node-RED flows."""
    config_dir = tempfile.mkdtemp()
    user_files = {
        "configuration.yaml": "homeassistant:\n",
        "node-red/flows.json": json.dumps([{"id": "a", "type": "tab"}]),
    }
    for name, text in user_files.items():
        os.makedirs(os.path.dirname(os.path.join(config_dir, name)), exist_ok=True)
        with open(os.path.join(config_dir, name), "w") as f:
            f.write(text)

    async def body(hass: HomeAssistant) -> None:
        chat_log = new_log(hass, "hi")
        answer(chat_log, "hello")
        await ConversationHistory(hass).async_save("alice", chat_log)

    run(body, config_dir)

    written = {
        os.path.relpath(os.path.join(root, f), config_dir) for root, _, files in os.walk(config_dir) for f in files
    }
    assert written == {*user_files, os.path.join(".storage", STORAGE_KEY)}
    for name, text in user_files.items():
        with open(os.path.join(config_dir, name)) as f:
            assert f.read() == text


def test_agents_share_one_history() -> None:
    """Two Lumo agents with separate Stores on one file would overwrite each other's users."""
    from custom_components.lumo.history import async_get_history

    async def body(hass: HomeAssistant) -> None:
        assert async_get_history(hass) is async_get_history(hass)

    run(body)


def test_the_agent_restores_before_and_saves_after_each_turn(monkeypatch) -> None:
    """The wiring in LumoEntity._async_handle_message, with the model call replaced."""
    from types import SimpleNamespace

    from homeassistant.core import Context

    from custom_components.lumo.conversation import LumoEntity

    seen: list[list[tuple[str, str]]] = []

    async def fake_model(self, chat_log) -> None:
        seen.append(texts(chat_log))
        answer(chat_log, f"reply {len(seen)}")

    async def no_llm_data(*args) -> None:
        return None

    monkeypatch.setattr(LumoEntity, "_async_handle_chat_log", fake_model)

    async def turn(hass: HomeAssistant, entity: LumoEntity, text: str) -> None:
        user_input = conversation.ConversationInput(
            text=text,
            context=Context(user_id="alice"),
            conversation_id=None,
            device_id=None,
            satellite_id=None,
            language="en",
            agent_id=AGENT,
        )
        chat_log = new_log(hass, text)  # a new chat log per turn: the dialog was closed
        chat_log.async_provide_llm_data = no_llm_data
        await entity._async_handle_message(user_input, chat_log)

    async def body(hass: HomeAssistant) -> None:
        entity = LumoEntity(SimpleNamespace(entry_id="e"), SimpleNamespace(subentry_id="s", title="Lumo", data={}))
        entity.hass = hass
        entity.entity_id = AGENT
        await turn(hass, entity, "Open the garage")
        await turn(hass, entity, "Close it again")

    run(body)

    assert seen == [
        [("user", "Open the garage")],
        [("user", "Open the garage"), ("assistant", "reply 1"), ("user", "Close it again")],
    ]
