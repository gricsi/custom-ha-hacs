"""Regression checks for reused APIs and concurrent conversation turns."""

import asyncio
import tempfile
from types import SimpleNamespace

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant

from custom_components.lumo.const import CONF_FUNCTIONS, CONF_REMEMBER_HISTORY
from custom_components.lumo.conversation import CustomFunctionTool, LumoEntity


@pytest.mark.parametrize("selected", [None, "homeassistant", ["assist", "homeassistant"]])
def test_reused_api_has_one_copy_of_each_custom_tool(monkeypatch, selected):
    async def main():
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            data = {
                CONF_REMEMBER_HISTORY: False,
                CONF_FUNCTIONS: "- spec: {name: example}\n  function: {type: template, value_template: ok}",
            }
            data["llm_hass_api"] = selected
            entity = LumoEntity(
                SimpleNamespace(entry_id="e"), SimpleNamespace(subentry_id="s", title="Lumo", data=data)
            )
            entity.hass, entity.entity_id = hass, "conversation.lumo"
            api = SimpleNamespace(tools=[SimpleNamespace(name="core_tool")])
            counts = []

            async def provide(log, *args):
                assert args[1] == (["flex_assist_e", "homeassistant"] if selected else ["flex_assist_e"])
                log.llm_api = api

            async def model(self, log):
                counts.append([tool.name for tool in log.llm_api.tools])
                log.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(agent_id=self.entity_id, content="ok")
                )

            monkeypatch.setattr(conversation.ChatLog, "async_provide_llm_data", provide)
            monkeypatch.setattr(LumoEntity, "_async_handle_chat_log", model)
            for _ in range(3):
                user_input = conversation.ConversationInput(
                    text="hi",
                    context=Context(user_id="alice"),
                    conversation_id=None,
                    device_id=None,
                    satellite_id=None,
                    language="en",
                    agent_id=entity.entity_id,
                )
                log = conversation.ChatLog(hass, "cid")
                log.async_add_user_content(conversation.UserContent(content="hi"))
                await entity._async_handle_message(user_input, log)
            assert counts == [["core_tool", "example"]] * 3
        finally:
            await hass.async_stop(force=True)

    asyncio.run(main())


def test_schema_changes_with_the_same_tool_name_reach_the_next_request():
    import openai
    from homeassistant.exceptions import HomeAssistantError
    from custom_components.lumo.entity import LumoBaseLLMEntity

    async def main():
        hass = HomeAssistant(tempfile.mkdtemp())
        requests = []

        async def create(**kwargs):
            requests.append(kwargs)
            raise openai.OpenAIError("stop before streaming")

        try:
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            entity = LumoBaseLLMEntity(
                SimpleNamespace(entry_id="e", title="Lumo", runtime_data=client),
                SimpleNamespace(subentry_id="s", title="Lumo", data={}),
            )
            entity.hass, entity.entity_id = hass, "conversation.lumo"
            log = conversation.ChatLog(hass, "cid")
            log.async_add_user_content(conversation.UserContent(content="hi"))
            tool = CustomFunctionTool({"name": "example"}, {})
            log.llm_api = SimpleNamespace(tools=[tool], custom_serializer=None)
            for field in ("old", "new"):
                tool.raw_parameters = {"type": "object", "properties": {field: {"type": "string"}}}
                with pytest.raises(HomeAssistantError):
                    await entity._async_handle_chat_log(log)
            assert list(requests[0]["tools"][0]["function"]["parameters"]["properties"]) == ["old"]
            assert list(requests[1]["tools"][0]["function"]["parameters"]["properties"]) == ["new"]
        finally:
            await hass.async_stop(force=True)

    asyncio.run(main())


@pytest.mark.parametrize("fail", [False, True])
def test_overlapping_turns_keep_their_own_user_and_reset_after_failure(monkeypatch, fail):
    async def main():
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            entity = LumoEntity(SimpleNamespace(entry_id="e"), SimpleNamespace(subentry_id="s", title="Lumo", data={}))
            both_started = asyncio.Event()
            started = 0
            seen = {}
            tool = CustomFunctionTool({"name": "example"}, {}, get_user_input=entity._current_user_input.get)

            async def turn(self, user_input, log):
                nonlocal started
                started += 1
                if started == 2:
                    both_started.set()
                await both_started.wait()
                await asyncio.sleep(0)
                seen[user_input.context.user_id] = tool._get_user_input().context.user_id
                if fail:
                    raise RuntimeError("failed turn")

            monkeypatch.setattr(LumoEntity, "_async_handle_turn", turn)

            async def invoke(user_id):
                await entity._async_handle_message(SimpleNamespace(context=Context(user_id=user_id)), None)

            results = await asyncio.gather(invoke("alice"), invoke("bob"), return_exceptions=True)
            assert seen == {"alice": "alice", "bob": "bob"}
            assert all(isinstance(result, RuntimeError) for result in results) if fail else results == [None, None]
            assert entity._current_user_input.get() is None
        finally:
            await hass.async_stop(force=True)

    asyncio.run(main())
