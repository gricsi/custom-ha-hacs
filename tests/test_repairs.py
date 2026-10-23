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


class ToolLoopClient(Client):
    """Asks for a tool on every call until the no-tools retry, which is rate limited."""

    def __init__(self) -> None:
        super().__init__()

    async def create(self, **kwargs: Any):
        if "tools" not in kwargs:
            raise lumo_error(openai.RateLimitError, 429, "Rate limit exceeded")
        chunk = ChatCompletionChunk.model_validate(
            {
                "id": "c",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "lumo-max",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "t",
                                    "type": "function",
                                    "function": {"name": "noop", "arguments": "{}"},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        )

        async def stream():
            yield chunk

        return stream()


def test_a_rate_limit_on_the_final_retry_raises_the_quota_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-tools retry caught a rate limit as a plain API error, so no repair issue was raised."""
    from homeassistant.helpers import llm

    class Noop(llm.Tool):
        name = "noop"
        description = "Does nothing"

        async def async_call(self, hass, tool_input, llm_context):
            return {}

    async def main() -> set[str]:
        hass = HomeAssistant(tempfile.mkdtemp())
        loader.async_setup(hass)
        await ir.async_load(hass)
        try:
            entity = LumoBaseLLMEntity(
                SimpleNamespace(entry_id="e", title="Lumo", runtime_data=ToolLoopClient()),
                SimpleNamespace(subentry_id="s", title="Lumo", data={}),
            )
            entity.hass = hass
            entity.entity_id = "conversation.lumo"
            chat_log = conversation.ChatLog(hass, "cid")
            chat_log.async_add_user_content(conversation.UserContent(content="hello"))
            chat_log.llm_api = SimpleNamespace(tools=[Noop()], custom_serializer=None, async_call_tool=None)

            async def call_tool(tool_input):
                return {}

            chat_log.llm_api.async_call_tool = call_tool
            with pytest.raises(HomeAssistantError):
                await entity._async_handle_chat_log(chat_log)
            return {i.issue_id for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN}
        finally:
            await hass.async_stop(force=True)

    assert asyncio.run(main()) == {"quota_exhausted_e"}


def test_removing_the_entry_removes_its_issues() -> None:
    """Issues are cleared by a successful reply, which a deleted entry will never get."""
    from custom_components.lumo import async_remove_entry
    from custom_components.lumo.entity import _set_repair_issue

    async def main() -> set[str]:
        hass = HomeAssistant(tempfile.mkdtemp())
        loader.async_setup(hass)
        await ir.async_load(hass)
        try:
            entry = SimpleNamespace(entry_id="e", title="Lumo")
            _set_repair_issue(hass, entry, "invalid_api_key")
            await async_remove_entry(hass, entry)
            return {i.issue_id for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN}
        finally:
            await hass.async_stop(force=True)

    assert asyncio.run(main()) == set()
