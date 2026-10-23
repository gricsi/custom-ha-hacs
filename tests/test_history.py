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
        await history.async_save("alice", first, AGENT)

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
        await history.async_save("alice", chat_log, AGENT)

        chat_log.async_add_user_content(conversation.UserContent(content="Close it"))
        await history.async_restore("alice", chat_log, AGENT)

        assert texts(chat_log) == [("user", "Open the garage"), ("assistant", "Opened."), ("user", "Close it")]

    run(body)


def test_history_is_per_user_and_skipped_without_one() -> None:
    async def body(hass: HomeAssistant) -> None:
        history = ConversationHistory(hass)
        chat_log = new_log(hass, "My secret")
        answer(chat_log, "Noted.")
        await history.async_save("alice", chat_log, AGENT)
        await history.async_save(None, chat_log, AGENT)

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
        await history.async_save("alice", chat_log, AGENT)

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
        await ConversationHistory(hass).async_save("alice", chat_log, AGENT)

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
        await ConversationHistory(hass).async_save("alice", chat_log, AGENT)

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


def test_clear_one_user_or_everyone() -> None:
    async def body(hass: HomeAssistant) -> list[list[tuple[str, str]]]:
        history = ConversationHistory(hass)
        for user in ("alice", "bob", "carol"):
            chat_log = new_log(hass, f"{user} here")
            answer(chat_log, "hi")
            await history.async_save(user, chat_log, AGENT)

        await history.async_clear("alice")
        after_one = []
        for user in ("alice", "bob"):
            chat_log = new_log(hass, "again")
            await history.async_restore(user, chat_log, AGENT)
            after_one.append(texts(chat_log))

        await history.async_clear(None)
        await history.async_flush()
        carol = new_log(hass, "again")
        await ConversationHistory(hass).async_restore("carol", carol, AGENT)  # re-read from disk
        return [*after_one, texts(carol)]

    alice, bob, carol = run(body)
    assert alice == [("user", "again")]
    assert bob[0] == ("user", "bob here")
    assert carol == [("user", "again")]


def test_clear_history_action() -> None:
    """The caller's history; nothing without a caller; everyone's only when asked, and not by a non-admin.

    "No user means everyone" used to apply to every automation, and a state-triggered one
    runs without a user -- so a non-admin's button could wipe the whole household's history.
    """
    from types import SimpleNamespace

    import pytest
    from homeassistant.core import Context
    from homeassistant.exceptions import Unauthorized

    from custom_components.lumo.const import DOMAIN
    from custom_components.lumo.history import async_get_history
    from custom_components.lumo.services import async_setup_services

    async def remaining(hass: HomeAssistant) -> set[str]:
        return {user for user in ("alice", "bob") if (await async_get_history(hass).async_get_messages(user, AGENT))[1]}

    async def seed(hass: HomeAssistant) -> None:
        for user in ("alice", "bob"):
            chat_log = new_log(hass, f"{user} here")
            answer(chat_log, "hi")
            await async_get_history(hass).async_save(user, chat_log, AGENT)

    async def clear(hass: HomeAssistant, data: dict, user_id: str | None) -> None:
        await hass.services.async_call(DOMAIN, "clear_history", data, blocking=True, context=Context(user_id=user_id))

    async def body(hass: HomeAssistant) -> list[set[str]]:
        users = {"alice": False, "admin": True}
        hass.auth = SimpleNamespace(
            async_get_user=lambda user_id: _done(SimpleNamespace(is_admin=users.get(user_id, False)))
        )
        await async_setup_services(hass, {})
        seen = []
        await seed(hass)
        await clear(hass, {}, "alice")
        seen.append(await remaining(hass))
        await clear(hass, {}, None)
        seen.append(await remaining(hass))
        with pytest.raises(Unauthorized):
            await clear(hass, {"all": True}, "alice")
        seen.append(await remaining(hass))
        await clear(hass, {"all": True}, "admin")
        seen.append(await remaining(hass))
        await seed(hass)
        await clear(hass, {"all": True}, None)
        seen.append(await remaining(hass))
        return seen

    assert run(body) == [{"bob"}, {"bob"}, {"bob"}, set(), set()]


async def _done(value):
    return value


def test_an_agent_with_history_off_neither_restores_nor_saves(monkeypatch) -> None:
    from types import SimpleNamespace

    from homeassistant.core import Context

    from custom_components.lumo.const import CONF_REMEMBER_HISTORY
    from custom_components.lumo.conversation import LumoEntity
    from custom_components.lumo.history import async_get_history

    async def fake_model(self, chat_log) -> None:
        answer(chat_log, "ok")

    async def no_llm_data(*args) -> None:
        return None

    monkeypatch.setattr(LumoEntity, "_async_handle_chat_log", fake_model)

    async def body(hass: HomeAssistant) -> list[tuple[str, str]]:
        seeded = new_log(hass, "from another agent")
        answer(seeded, "remembered")
        await async_get_history(hass).async_save("alice", seeded, AGENT)

        entity = LumoEntity(
            SimpleNamespace(entry_id="e"),
            SimpleNamespace(subentry_id="s", title="Lumo", data={CONF_REMEMBER_HISTORY: False}),
        )
        entity.hass = hass
        entity.entity_id = AGENT
        chat_log = new_log(hass, "private")
        chat_log.async_provide_llm_data = no_llm_data
        user_input = conversation.ConversationInput(
            text="private",
            context=Context(user_id="alice"),
            conversation_id=None,
            device_id=None,
            satellite_id=None,
            language="en",
            agent_id=AGENT,
        )
        await entity._async_handle_message(user_input, chat_log)
        assert texts(chat_log) == [("user", "private"), ("assistant", "ok")], "nothing was restored"

        fresh = new_log(hass, "later")
        await async_get_history(hass).async_restore("alice", fresh, AGENT)
        return texts(fresh)[:-1]

    assert run(body) == [("user", "from another agent"), ("assistant", "remembered")], "nothing was saved"


def test_history_is_kept_per_agent() -> None:
    """Text from an agent with tools must not be replayed into a different agent."""

    async def body(hass: HomeAssistant) -> list[tuple[str, str]]:
        history = ConversationHistory(hass)
        chat_log = new_log(hass, "read my logs")
        answer(chat_log, "the logs say ...")
        await history.async_save("alice", chat_log, "conversation.lumo_tools")

        other = new_log(hass, "hello")
        await history.async_restore("alice", other, "conversation.lumo_plain")
        return texts(other)

    assert run(body) == [("user", "hello")]


def test_old_conversations_and_deleted_users_are_forgotten(monkeypatch) -> None:
    import custom_components.lumo.history as history_module
    from homeassistant.util import dt as dt_util

    async def body(hass: HomeAssistant) -> tuple[list, list, list]:
        history = history_module.async_get_history(hass)
        for user in ("alice", "gone"):
            chat_log = new_log(hass, f"{user} here")
            answer(chat_log, "hi")
            await history.async_save(user, chat_log, AGENT)

        hass.bus.async_fire("user_removed", {"user_id": "gone"})
        await hass.async_block_till_done()

        later = dt_util.utcnow() + history_module.RETENTION / 2
        monkeypatch.setattr(dt_util, "utcnow", lambda: later)
        gone, alice_mid = new_log(hass, "x"), new_log(hass, "x")
        await history.async_restore("gone", gone, AGENT)
        await history.async_restore("alice", alice_mid, AGENT)

        much_later = later + history_module.RETENTION
        monkeypatch.setattr(dt_util, "utcnow", lambda: much_later)
        alice_late = new_log(hass, "x")
        await history.async_restore("alice", alice_late, AGENT)
        return texts(gone), texts(alice_mid), texts(alice_late)

    gone, alice_mid, alice_late = run(body)
    assert gone == [("user", "x")]
    assert alice_mid[0] == ("user", "alice here")
    assert alice_late == [("user", "x")]


def test_saving_batches_disk_writes() -> None:
    """Every turn rewrote the whole file at once; on an SD card that adds up."""

    async def body(hass: HomeAssistant) -> bool:
        history = ConversationHistory(hass)
        chat_log = new_log(hass, "hi")
        answer(chat_log, "hello")
        await history.async_save("alice", chat_log, AGENT)
        written_now = os.path.exists(hass.config.path(".storage", STORAGE_KEY))
        await history.async_flush()
        return written_now, os.path.exists(hass.config.path(".storage", STORAGE_KEY))

    assert run(body) == (False, True)
