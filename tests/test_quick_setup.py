"""Tests for the quick setup of a new conversation agent.

A new user picks tools and, for Node-RED, gives a URL and login; everything else takes
the recommended values. The function YAML is generated from presets, so these tests check
what the generated YAML contains, that the read-only tools say so, and that the presets
have not drifted from the documented examples they were copied from.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from custom_components.lumo.const import (
    CONF_FUNCTIONS,
    CONF_PROMPT,
    CONF_RECOMMENDED,
    DEFAULT_CONF_FUNCTIONS,
)
from custom_components.lumo.presets import (
    CONF_NODE_RED_PASSWORD,
    CONF_NODE_RED_URL,
    CONF_NODE_RED_USERNAME,
    CONF_TOOLS,
    TOOL_PRESETS,
    quick_setup_options,
)

EXAMPLES = Path(__file__).parent.parent / "examples" / "function"


def functions(options: dict) -> dict[str, dict]:
    return {f["spec"]["name"]: f for f in yaml.safe_load(options[CONF_FUNCTIONS])}


def test_defaults_give_device_control_and_recommended_settings() -> None:
    options = quick_setup_options({CONF_TOOLS: ["execute_services"]})
    assert options[CONF_RECOMMENDED] is True
    assert CONF_PROMPT in options
    assert list(functions(options).values()) == DEFAULT_CONF_FUNCTIONS


def test_node_red_takes_the_url_and_login() -> None:
    options = quick_setup_options(
        {
            CONF_TOOLS: ["node_red_flows"],
            CONF_NODE_RED_URL: "http://192.168.1.10:1880/",
            CONF_NODE_RED_USERNAME: "reader",
            CONF_NODE_RED_PASSWORD: "secret",
        }
    )
    function = functions(options)["node_red_flows"]["function"]
    assert function["resource"] == "http://192.168.1.10:1880/flows"
    assert (function["username"], function["password"]) == ("reader", "secret")


def test_node_red_without_a_login_sends_none() -> None:
    options = quick_setup_options({CONF_TOOLS: ["node_red_flows"], CONF_NODE_RED_URL: "http://nr:1880"})
    function = functions(options)["node_red_flows"]["function"]
    assert function["resource"] == "http://nr:1880/flows"
    assert not {"authentication", "username", "password"} & set(function)


def test_node_red_without_a_url_is_refused() -> None:
    with pytest.raises(ValueError, match="node_red_url"):
        quick_setup_options({CONF_TOOLS: ["node_red_flows"]})


def test_no_tools_gives_no_functions() -> None:
    assert functions(quick_setup_options({CONF_TOOLS: []})) == {}


@pytest.mark.parametrize("name", ["read_logs", "node_red_flows"])
def test_reading_tools_are_marked_read_only(name: str) -> None:
    annotations = TOOL_PRESETS[name]["spec"]["annotations"]
    assert annotations == {"read_only": True, "destructive": False, "idempotent": True, "open_world": False}


@pytest.mark.parametrize("name", ["read_logs", "node_red_flows"])
def test_presets_match_the_documented_examples(name: str) -> None:
    """The examples are the documentation; a preset that drifts from them is a silent fork."""
    readme = (EXAMPLES / name / "README.md").read_text()
    example = yaml.safe_load(re.search(r"```yaml\n(.*?)```", readme, re.S).group(1))[0]
    preset = TOOL_PRESETS[name]
    assert preset["spec"]["description"] == example["spec"]["description"]
    assert preset["spec"]["parameters"] == example["spec"]["parameters"]
    assert {k: v for k, v in preset["function"].items() if k not in ("resource", "username", "password")} == {
        k: v for k, v in example["function"].items() if k not in ("resource", "username", "password")
    }


def run_flow(user_input: dict | None, subentry_type: str = "conversation") -> dict:
    """Drive LumoSubentryFlowHandler.async_step_user on a bare HomeAssistant."""
    import asyncio
    import tempfile
    from types import SimpleNamespace

    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.core import HomeAssistant

    from custom_components.lumo.config_flow import LumoSubentryFlowHandler

    async def main() -> dict:
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            flow = LumoSubentryFlowHandler()
            flow.hass = hass
            flow.handler = ("entry", subentry_type)
            flow.context = {"source": "user"}
            flow._get_entry = lambda: SimpleNamespace(state=ConfigEntryState.LOADED)
            result = await flow.async_step_user()
            if user_input is not None:
                result = await flow.async_step_user(user_input)
            return {**result, "options": getattr(flow, "options", None)}
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(main())


def test_a_new_agent_starts_with_the_short_form() -> None:
    result = run_flow(None)
    assert result["step_id"] == "user"
    fields = {str(key) for key in result["data_schema"].schema}
    assert fields == {
        "name",
        CONF_TOOLS,
        CONF_NODE_RED_URL,
        CONF_NODE_RED_USERNAME,
        CONF_NODE_RED_PASSWORD,
        "customize",
    }


def test_submitting_the_short_form_creates_the_agent() -> None:
    result = run_flow({"name": "Lumo voice", CONF_TOOLS: ["execute_services", "read_logs"], "customize": False})
    assert (result["type"], result["title"]) == ("create_entry", "Lumo voice")
    assert set(functions(result["data"])) == {"execute_services", "read_logs"}
    assert not {CONF_NODE_RED_URL, CONF_NODE_RED_PASSWORD, "customize", "name"} & set(result["data"])


def test_node_red_without_a_url_shows_an_error() -> None:
    result = run_flow({"name": "Lumo", CONF_TOOLS: ["node_red_flows"], "customize": False})
    assert (result["type"], result["errors"]) == ("form", {CONF_NODE_RED_URL: "node_red_url_required"})


def test_customize_continues_into_the_full_settings_prefilled() -> None:
    result = run_flow({"name": "Lumo", CONF_TOOLS: ["read_logs"], "customize": True})
    assert result["step_id"] == "init"
    assert set(functions(result["options"])) == {"read_logs"}


def test_an_ai_task_skips_the_short_form() -> None:
    assert run_flow(None, "ai_task_data")["step_id"] == "init"


def test_reconfiguring_a_recommended_agent_keeps_its_functions_and_history_setting() -> None:
    """The init step used to save only its own fields on the recommended path, which threw
    away the function YAML -- and every quick-setup agent is a recommended one."""
    import asyncio
    import tempfile
    from types import SimpleNamespace

    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.core import HomeAssistant

    from custom_components.lumo.config_flow import LumoSubentryFlowHandler
    from custom_components.lumo.const import CONF_REMEMBER_HISTORY

    saved = quick_setup_options({CONF_TOOLS: ["read_logs"]}) | {CONF_REMEMBER_HISTORY: False}
    written: dict = {}

    async def main() -> None:
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            flow = LumoSubentryFlowHandler()
            flow.hass = hass
            flow.handler = ("entry", "conversation")
            flow.context = {"source": "reconfigure"}
            flow._get_entry = lambda: SimpleNamespace(state=ConfigEntryState.LOADED)
            flow._get_reconfigure_subentry = lambda: SimpleNamespace(data=saved)
            flow.async_update_and_abort = lambda entry, subentry, data: written.update(data)
            await flow.async_step_reconfigure()
            await flow.async_step_init({CONF_RECOMMENDED: True, CONF_PROMPT: "Be brief."})
        finally:
            await hass.async_stop(force=True)

    asyncio.run(main())
    assert written[CONF_PROMPT] == "Be brief."
    assert written[CONF_FUNCTIONS] == saved[CONF_FUNCTIONS]
    assert written[CONF_REMEMBER_HISTORY] is False


@pytest.mark.parametrize(
    "options",
    [quick_setup_options({CONF_TOOLS: ["execute_services"]}), {}],
    ids=["quick setup", "nothing saved"],
)
def test_an_agent_without_a_chosen_model_uses_lumo_max(options: dict) -> None:
    import asyncio
    import tempfile
    from types import SimpleNamespace

    import openai

    from homeassistant.components import conversation
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.lumo.entity import LumoBaseLLMEntity

    sent: list[dict] = []

    async def create(**kwargs) -> None:
        sent.append(kwargs)
        raise openai.OpenAIError("stop here")

    async def main() -> None:
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            entity = LumoBaseLLMEntity(
                SimpleNamespace(entry_id="e", title="Lumo", runtime_data=client),
                SimpleNamespace(subentry_id="s", title="Lumo", data=options),
            )
            entity.hass = hass
            entity.entity_id = "conversation.lumo"
            chat_log = conversation.ChatLog(hass, "cid")
            chat_log.async_add_user_content(conversation.UserContent(content="hi"))
            with pytest.raises(HomeAssistantError):
                await entity._async_handle_chat_log(chat_log)
        finally:
            await hass.async_stop(force=True)

    asyncio.run(main())
    assert sent[0]["model"] == "lumo-max"


@pytest.mark.parametrize(("tools", "control"), [(["execute_services", "read_logs"], True), (["read_logs"], False)])
def test_the_control_devices_tick_sets_device_control(tools: list[str], control: bool) -> None:
    from custom_components.lumo.const import CONF_DEVICE_CONTROL

    assert quick_setup_options({CONF_TOOLS: tools})[CONF_DEVICE_CONTROL] is control


def test_without_device_control_only_read_only_tools_remain() -> None:
    """Lumo always attaches core's Assist tools, so leaving out execute_services was not enough:
    HassTurnOn and friends stayed callable. Without device control, only tools that declare
    themselves read-only are offered."""
    from types import SimpleNamespace

    from homeassistant.helpers import llm

    from custom_components.lumo.conversation import CustomFunctionTool, without_control_tools

    turn_on = llm.IntentTool(
        "intent__HassTurnOn", SimpleNamespace(intent_type="HassTurnOn", description="", slot_schema=None)
    )
    script = llm.ActionTool.__new__(llm.ActionTool)
    script.name = "script__open_garage"

    class LiveContext(llm.Tool):
        name = "homeassistant__GetLiveContext"
        description = "Read states"

    read_logs = CustomFunctionTool(TOOL_PRESETS["read_logs"]["spec"], TOOL_PRESETS["read_logs"]["function"])
    execute = CustomFunctionTool(DEFAULT_CONF_FUNCTIONS[0]["spec"], DEFAULT_CONF_FUNCTIONS[0]["function"])
    undeclared = CustomFunctionTool({"name": "mystery"}, {"type": "script"})

    kept = without_control_tools([turn_on, script, LiveContext(), read_logs, execute, undeclared])
    assert [tool.name for tool in kept] == ["homeassistant__GetLiveContext", "read_logs"]


@pytest.mark.parametrize(("device_control", "advertised"), [(True, True), (False, False)])
def test_the_control_feature_follows_device_control(device_control: bool, advertised: bool) -> None:
    """Home Assistant offers an agent that advertises CONTROL for device commands."""
    from types import SimpleNamespace

    from homeassistant.components import conversation

    from custom_components.lumo.const import CONF_DEVICE_CONTROL, CONF_LLM_HASS_API
    from custom_components.lumo.conversation import LumoEntity

    entity = LumoEntity(
        SimpleNamespace(entry_id="e"),
        SimpleNamespace(
            subentry_id="s", title="Lumo", data={CONF_LLM_HASS_API: ["assist"], CONF_DEVICE_CONTROL: device_control}
        ),
    )
    assert bool(entity.supported_features & conversation.ConversationEntityFeature.CONTROL) is advertised


def test_the_agent_created_with_the_integration_matches_the_short_form_defaults() -> None:
    """Adding the integration creates a conversation agent without showing the short form.
    It got the bare recommended options -- no function YAML, no device_control -- so its
    Reconfigure looked nothing like an agent made through the short form."""
    import asyncio
    import tempfile

    from homeassistant.core import HomeAssistant

    from custom_components.lumo.config_flow import LumoConfigFlow
    from custom_components.lumo.const import CONF_SKIP_AUTHENTICATION

    async def main() -> dict:
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            flow = LumoConfigFlow()
            flow.hass = hass
            flow.context = {"source": "user"}
            return await flow.async_step_user({CONF_SKIP_AUTHENTICATION: True})
        finally:
            await hass.async_stop(force=True)

    result = asyncio.run(main())
    agent = next(s for s in result["subentries"] if s["subentry_type"] == "conversation")
    assert agent["data"] == quick_setup_options({CONF_TOOLS: ["execute_services"]})
