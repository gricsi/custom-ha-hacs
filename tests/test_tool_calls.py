"""Tests for a tool call whose arguments are not valid JSON.

That used to fail the whole turn. The call is now handed to core as external -- so core
never runs it, not even with empty arguments -- and answered with an error result the
model sees on its next round, so it can resend the call.
"""

from __future__ import annotations

import asyncio
import tempfile
from types import SimpleNamespace
from typing import Any

from openai.types.chat import ChatCompletionChunk

from homeassistant import loader
from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from custom_components.lumo.entity import LumoBaseLLMEntity, tool_result_data

BASE = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "lumo-max"}


def tool_call_chunk(arguments: str) -> dict[str, Any]:
    call = {"index": 0, "id": "t1", "type": "function", "function": {"name": "garage", "arguments": arguments}}
    return {
        **BASE,
        "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [call]}, "finish_reason": "tool_calls"}],
    }


def text_chunk(text: str) -> dict[str, Any]:
    return {
        **BASE,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
    }


class Client:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs: Any):
        self.requests.append(kwargs)
        chunk = ChatCompletionChunk.model_validate(self.responses.pop(0))

        async def stream():
            yield chunk

        return stream()


def converse(client: Client) -> tuple[list[Any], list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    class Garage(llm.Tool):
        name = "garage"
        description = "Opens the garage"

        async def async_call(self, hass, tool_input, llm_context):
            calls.append(tool_input.tool_args)
            return {"opened": True}

    async def main() -> list[Any]:
        hass = HomeAssistant(tempfile.mkdtemp())
        loader.async_setup(hass)
        try:
            entity = LumoBaseLLMEntity(
                SimpleNamespace(entry_id="e", title="Lumo", runtime_data=client),
                SimpleNamespace(subentry_id="s", title="Lumo", data={}),
            )
            entity.hass = hass
            entity.entity_id = "conversation.lumo"
            chat_log = conversation.ChatLog(hass, "cid")
            chat_log.async_add_user_content(conversation.UserContent(content="open the garage"))
            garage = Garage()

            async def call_tool(tool_input):
                return await garage.async_call(hass, tool_input, None)

            chat_log.llm_api = SimpleNamespace(tools=[garage], custom_serializer=None, async_call_tool=call_tool)
            await entity._async_handle_chat_log(chat_log)
            return chat_log.content
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(main()), calls


def test_malformed_arguments_go_back_to_the_model_and_the_tool_does_not_run() -> None:
    client = Client(tool_call_chunk('{"door": "left"'), tool_call_chunk('{"door": "left"}'), text_chunk("Opened."))

    content, calls = converse(client)

    assert calls == [{"door": "left"}], "only the resent, valid call ran"
    error = next(c for c in content if c.role == "tool_result")
    assert "not valid JSON" in tool_result_data(error)["error"]
    assert content[-1].content == "Opened."
    second_request_tool_messages = [m for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert "not valid JSON" in second_request_tool_messages[0]["content"]
