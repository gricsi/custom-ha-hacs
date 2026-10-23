"""Tests for the usage sensors.

Lumo reports token counts on every response and, on the anonymous tier, how many requests
are left per tier (usage.remaining_limits). That used to go to the log only. The stream now
hands each usage payload to the entry's sensors through the dispatcher.
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

from custom_components.lumo.entity import LumoBaseLLMEntity
from custom_components.lumo.sensor import SENSORS, LumoUsageSensor, async_record_usage


def usage(prompt: int = 0, completion: int = 0, **limits: int) -> SimpleNamespace:
    payload = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
    if limits:
        payload.remaining_limits = limits
    return payload


def run(body) -> Any:
    async def main() -> Any:
        hass = HomeAssistant(tempfile.mkdtemp())
        # Writing state for an entity added without a platform looks the integration up.
        loader.async_setup(hass)
        try:
            return await body(hass)
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(main())


async def add_sensors(hass: HomeAssistant, entry_id: str = "e") -> dict[str, LumoUsageSensor]:
    sensors = {}
    for description in SENSORS:
        sensor = LumoUsageSensor(entry_id, description)
        sensor.hass = hass
        sensor.entity_id = f"sensor.lumo_{description.key}"
        await sensor.async_added_to_hass()
        sensors[description.key] = sensor
    return sensors


def states(hass: HomeAssistant) -> dict[str, str]:
    return {s.entity_id.removeprefix("sensor.lumo_"): s.state for s in hass.states.async_all("sensor")}


def test_tokens_accumulate_across_responses() -> None:
    async def body(hass: HomeAssistant) -> dict[str, str]:
        await add_sensors(hass)
        async_record_usage(hass, "e", usage(100, 20))
        async_record_usage(hass, "e", usage(50, 5))
        return states(hass)

    result = run(body)
    assert result["input_tokens"] == "150"
    assert result["output_tokens"] == "25"


def test_remaining_requests_follow_the_latest_report() -> None:
    """An authenticated key omits remaining_limits, so the last reported value stands."""

    async def body(hass: HomeAssistant) -> dict[str, str]:
        await add_sensors(hass)
        async_record_usage(hass, "e", usage(1, 1, lite=19, max=9, images=4))
        async_record_usage(hass, "e", usage(1, 1, lite=18, max=9, images=4))
        async_record_usage(hass, "e", usage(1, 1))
        return states(hass)

    result = run(body)
    assert (result["remaining_lite"], result["remaining_max"], result["remaining_images"]) == ("18", "9", "4")


def test_entries_do_not_share_counters() -> None:
    async def body(hass: HomeAssistant) -> None:
        mine = await add_sensors(hass, "e")
        async_record_usage(hass, "other", usage(100, 20))
        assert mine["input_tokens"].native_value is None

    run(body)


class StreamingClient:
    """Answers with one text chunk and a usage chunk, as Lumo's SSE stream ends."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs: Any):
        base = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "lumo-max"}
        chunks = [
            {
                **base,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
            },
            {
                **base,
                "choices": [],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                    "remaining_limits": {"lite": 5},
                },
            },
        ]

        async def stream():
            for chunk in chunks:
                yield ChatCompletionChunk.model_validate(chunk)

        return stream()


def test_a_response_updates_its_entrys_sensors() -> None:
    async def body(hass: HomeAssistant) -> dict[str, str]:
        await add_sensors(hass, "e")
        entity = LumoBaseLLMEntity(
            SimpleNamespace(entry_id="e", runtime_data=StreamingClient()),
            SimpleNamespace(subentry_id="s", title="Lumo", data={}),
        )
        entity.hass = hass
        entity.entity_id = "conversation.lumo"
        chat_log = conversation.ChatLog(hass, "cid")
        chat_log.async_add_user_content(conversation.UserContent(content="hello"))
        await entity._async_handle_chat_log(chat_log)
        return states(hass)

    result = run(body)
    assert (result["input_tokens"], result["output_tokens"], result["remaining_lite"]) == ("7", "3", "5")
