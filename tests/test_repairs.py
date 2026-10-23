"""Tests for the repair issues raised from a failed Lumo request.

A rejected key or a spent quota used to reach the user only as a failed Assist reply and
a log line. Each now raises a repair issue naming the fix (Reconfigure, or wait for the
window), and the next successful reply removes it.
"""

from __future__ import annotations

import asyncio
import tempfile
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from openai.types.chat import ChatCompletionChunk

from homeassistant import loader
from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from custom_components.lumo.const import DOMAIN
from custom_components.lumo.entity import LumoBaseLLMEntity


def lumo_error(cls: type[openai.APIStatusError], status: int, message: str) -> openai.APIStatusError:
    response = httpx.Response(status, request=httpx.Request("POST", "https://lumo.proton.me/api/ai/v1"))
    return cls(message, response=response, body={"Code": 0, "Error": message})


class Client:
    """Fails with the queued errors in turn, then answers."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs: Any):
        if self.errors:
            raise self.errors.pop(0)
        chunk = ChatCompletionChunk.model_validate(
            {
                "id": "c",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "lumo-max",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            }
        )

        async def stream():
            yield chunk

        return stream()


def run_turns(client: Client, turns: int) -> list[set[str]]:
    """Run `turns` requests, recording the open Lumo issues after each."""

    async def main() -> list[set[str]]:
        hass = HomeAssistant(tempfile.mkdtemp())
        loader.async_setup(hass)
        await ir.async_load(hass)
        try:
            entity = LumoBaseLLMEntity(
                SimpleNamespace(entry_id="e", title="Lumo", runtime_data=client),
                SimpleNamespace(subentry_id="s", title="Lumo", data={}),
            )
            entity.hass = hass
            entity.entity_id = "conversation.lumo"
            seen = []
            for _ in range(turns):
                chat_log = conversation.ChatLog(hass, "cid")
                chat_log.async_add_user_content(conversation.UserContent(content="hello"))
                try:
                    await entity._async_handle_chat_log(chat_log)
                except HomeAssistantError:
                    pass
                seen.append({i.issue_id for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN})
            return seen
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(main())


@pytest.mark.parametrize(
    ("error", "issue"),
    [
        (lumo_error(openai.AuthenticationError, 401, "Invalid or expired API key"), "invalid_api_key_e"),
        (lumo_error(openai.RateLimitError, 429, "Rate limit exceeded"), "quota_exhausted_e"),
    ],
)
def test_a_failure_raises_an_issue_and_the_next_reply_clears_it(error: Exception, issue: str) -> None:
    assert run_turns(Client(error), 2) == [{issue}, set()]


def test_other_errors_raise_no_issue() -> None:
    error = lumo_error(openai.InternalServerError, 500, "Upstream timeout")
    assert run_turns(Client(error), 1) == [set()]
