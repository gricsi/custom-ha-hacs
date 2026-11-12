"""The websocket binds sessions to callers and cleans up failed subscriptions."""

import asyncio
import tempfile
from inspect import unwrap
from types import SimpleNamespace

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized

from custom_components.lumo import history


def connection(user_id, events):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        subscriptions={},
        context=lambda msg: Context(user_id=user_id),
        send_message=lambda msg: events.append(msg["event"]),
        send_result=lambda *args: None,
    )


def test_failed_turn_removes_the_subscription(monkeypatch):
    async def main():
        hass = HomeAssistant(tempfile.mkdtemp())
        conn = connection("alice", [])

        async def fail(*args):
            raise RuntimeError("failed request")

        monkeypatch.setattr(history.conversation, "async_converse", fail)
        try:
            with pytest.raises(RuntimeError, match="failed request"):
                await unwrap(history.websocket_chat)(
                    hass, conn, {"id": 1, "text": "hi", "agent_id": "conversation.lumo"}
                )
            assert conn.subscriptions == {}
        finally:
            await hass.async_stop(force=True)

    asyncio.run(main())


def test_another_user_cannot_continue_the_session(monkeypatch):
    async def main():
        hass = HomeAssistant(tempfile.mkdtemp())
        events, users = [], []

        async def converse(hass, text, conversation_id, context, *args):
            users.append(context.user_id)
            return SimpleNamespace(as_dict=lambda: {"conversation_id": conversation_id})

        monkeypatch.setattr(history.conversation, "async_converse", converse)
        try:
            first = connection("alice", events)
            await unwrap(history.websocket_chat)(hass, first, {"id": 1, "text": "hi", "agent_id": "conversation.lumo"})
            second = connection("bob", [])
            with pytest.raises(Unauthorized):
                await unwrap(history.websocket_chat)(
                    hass,
                    second,
                    {
                        "id": 2,
                        "text": "continue",
                        "agent_id": "conversation.lumo",
                        "conversation_id": events[-1]["done"]["conversation_id"],
                    },
                )
            assert users == ["alice"]
            assert first.subscriptions == second.subscriptions == {}
        finally:
            await hass.async_stop(force=True)

    asyncio.run(main())
