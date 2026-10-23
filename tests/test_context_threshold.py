"""Tests for the context threshold option ("Clear All Messages").

The option was offered in the UI but never read, so a long conversation -- and now a
restored history -- was sent whole no matter what it was set to. Over the threshold,
the earlier turns are dropped: what is sent is the system prompt and the current turn,
from its user message on, so a tool call is never separated from its result.
"""

from __future__ import annotations

import asyncio
import tempfile
from types import SimpleNamespace
from typing import Any

import openai
import pytest

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.lumo.const import CONF_CONTEXT_THRESHOLD
from custom_components.lumo.entity import LumoBaseLLMEntity, trim_messages

SYSTEM = {"role": "system", "content": "You are Lumo."}
OLD = [{"role": "user", "content": "x" * 400}, {"role": "assistant", "content": "y" * 400}]
CURRENT = [
    {"role": "user", "content": "Is the garage open?"},
    {"role": "assistant", "content": None, "tool_calls": [{"id": "t1", "type": "function"}]},
    {"role": "tool", "tool_call_id": "t1", "content": "{}"},
]


def test_under_the_threshold_nothing_is_dropped() -> None:
    messages = [SYSTEM, *OLD, *CURRENT]
    assert trim_messages(messages, 10_000) == messages


def test_over_the_threshold_only_the_system_prompt_and_current_turn_remain() -> None:
    assert trim_messages([SYSTEM, *OLD, *CURRENT], 100) == [SYSTEM, *CURRENT]


def test_a_current_turn_alone_over_the_threshold_is_still_sent() -> None:
    """There is nothing left to drop; refusing to send would just fail the turn."""
    assert trim_messages([SYSTEM, *CURRENT], 1) == [SYSTEM, *CURRENT]


def test_zero_disables_trimming() -> None:
    messages = [SYSTEM, *OLD, *CURRENT]
    assert trim_messages(messages, 0) == messages


class CapturingClient:
    """Records the request, then fails it so the test needs no streaming fake."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs: Any) -> None:
        self.sent.append(kwargs)
        raise openai.OpenAIError("stop here")


@pytest.mark.parametrize(("threshold", "expected_users"), [(100, ["now"]), (10_000, ["x" * 400, "now"])])
def test_the_option_is_applied_to_the_request(threshold: int, expected_users: list[str]) -> None:
    client = CapturingClient()

    async def main() -> None:
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            entity = LumoBaseLLMEntity(
                SimpleNamespace(runtime_data=client),
                SimpleNamespace(subentry_id="s", title="Lumo", data={CONF_CONTEXT_THRESHOLD: threshold}),
            )
            entity.hass = hass
            entity.entity_id = "conversation.lumo"
            chat_log = conversation.ChatLog(hass, "cid")
            chat_log.async_add_user_content(conversation.UserContent(content="x" * 400))
            chat_log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(agent_id="conversation.lumo", content="y" * 400)
            )
            chat_log.async_add_user_content(conversation.UserContent(content="now"))
            with pytest.raises(HomeAssistantError):
                await entity._async_handle_chat_log(chat_log)
        finally:
            await hass.async_stop(force=True)

    asyncio.run(main())

    users = [m["content"] for m in client.sent[0]["messages"] if m["role"] == "user"]
    assert users == expected_users


def test_a_large_system_prompt_does_not_count() -> None:
    """Exposed entities can make the system prompt alone exceed the threshold. It is never
    dropped, so counting it would discard every conversation's history, every turn."""
    big_system = {"role": "system", "content": "e" * 100_000}
    messages = [big_system, *OLD, *CURRENT]
    assert trim_messages(messages, 1_000) == messages
