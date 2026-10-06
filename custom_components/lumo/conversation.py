"""Conversation support for Lumo."""

from collections.abc import Callable
from dataclasses import fields as dataclass_fields
from typing import Any, Literal

import voluptuous as vol
import yaml

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LumoConfigEntry
from .const import (
    CONF_FUNCTIONS,
    CONF_LLM_HASS_API,
    CONF_PROMPT,
    DEFAULT_CONF_FUNCTIONS,
    DOMAIN,
    LOGGER,
    MAX_FUNCTION_RESULT_CHARS,
)
from .entity import LumoBaseLLMEntity


def build_tool_annotations(function_name: str, declared: Any) -> Any | None:
    """Turn a function spec's `annotations:` block into an llm.ToolAnnotations.

    Core gained ToolAnnotations after 2026.9, so this returns None on an older one and
    the caller leaves the attribute unset -- the tool keeps the three fields every
    supported core understands.

    The accepted keys are read off the dataclass rather than listed here, so a field
    core adds later works without a change on this side. Anything else is dropped with
    a warning: these describe how much damage a tool may do, and a typo must not be
    able to quietly claim a function is read-only.
    """
    annotations_cls = getattr(llm, "ToolAnnotations", None)
    if annotations_cls is None:
        return None

    try:
        allowed = {field.name for field in dataclass_fields(annotations_cls)}
    except TypeError:
        LOGGER.warning("llm.ToolAnnotations is not a dataclass; ignoring annotations for %s", function_name)
        return None

    # Core's defaults describe the least safe case -- a tool that declares nothing is
    # taken to write, to be destructive, and to reach outside Home Assistant. That is
    # the right default for an arbitrary user-defined function, so an absent or
    # unusable block leaves it alone rather than guessing something more permissive.
    if declared is None:
        return annotations_cls()

    if not isinstance(declared, dict):
        LOGGER.warning(
            "Function %s declares annotations as %s, expected a mapping of %s; using the safe defaults",
            function_name,
            type(declared).__name__,
            ", ".join(sorted(allowed)),
        )
        return annotations_cls()

    values: dict[str, bool] = {}
    for key, value in declared.items():
        if key not in allowed:
            LOGGER.warning(
                "Function %s declares unknown annotation %r; expected one of %s",
                function_name,
                key,
                ", ".join(sorted(allowed)),
            )
            continue
        if not isinstance(value, bool):
            LOGGER.warning(
                "Function %s declares annotation %s as %r, expected true or false; ignoring it",
                function_name,
                key,
                value,
            )
            continue
        values[key] = value

    return annotations_cls(**values)


class CustomFunctionTool(llm.Tool):
    """Tool for executing custom functions defined in the configuration."""

    def __init__(
        self,
        function_spec: dict,
        function_impl: dict,
        get_user_input: Callable[[], conversation.ConversationInput | None] | None = None,
    ) -> None:
        """Initialize the tool with function specification and implementation."""
        self.name = function_spec["name"]
        self.description = function_spec.get("description", f"Execute {self.name} function")
        # Core types Tool.parameters as a voluptuous schema, and _format_tool pushes
        # it through probatio.to_openapi(). A YAML function's `parameters` block is
        # already JSON Schema -- the shape chat/completions wants -- so it is carried
        # separately and used verbatim. This used to be `self.parameters = {}` with the
        # spec's block dropped on the floor, which advertised every custom function to
        # the model as taking no arguments at all.
        self.parameters = vol.Schema({})
        self.raw_parameters = function_spec.get("parameters") or {"type": "object", "properties": {}}
        self.function_impl = function_impl
        self.function_spec = function_spec
        # Three bits of tool metadata core grew after 2026.9. They are set on the
        # instance, so on an older core they are inert attributes nothing reads.
        #
        # `integration` records who provides the tool. Core warns about untagged tools
        # and stops accepting them in 2027.10. Setting it here is not just early
        # compliance: core's fallback tagger runs in APIInstance.__post_init__, and
        # these tools are appended to llm_api.tools *after* that, so they would never
        # be tagged automatically and never trigger the warning either -- they would
        # stay untagged in silence until the day enforcement lands.
        self.integration = DOMAIN
        self.title = self._declared_title(function_spec)
        annotations = build_tool_annotations(self.name, function_spec.get("annotations"))
        if annotations is not None:
            self.annotations = annotations
        # The executors need the turn's ConversationInput -- script runs use its
        # Context for attribution, and get_user_from_user_id reads its user_id.
        # LLMContext no longer carries the user's prompt, so the entity hands us
        # the real input instead of us rebuilding a fake one.
        self._get_user_input = get_user_input

    @staticmethod
    def _declared_title(function_spec: dict) -> str | None:
        """Return the spec's human-readable title, if it gave a usable one."""
        title = function_spec.get("title")
        if title is None or isinstance(title, str):
            return title
        LOGGER.warning(
            "Function %s declares title as %s, expected a string; ignoring it",
            function_spec.get("name"),
            type(title).__name__,
        )
        return None

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict:
        """Execute the function when called as a tool."""
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose
        from homeassistant.helpers import entity_registry as er

        from .helpers import get_function_executor

        try:
            function_executor = get_function_executor(self.function_impl["type"])

            all_states = hass.states.async_all()
            exposed_entity_ids = {
                state.entity_id
                for state in all_states
                if async_should_expose(hass, conversation.DOMAIN, state.entity_id)
            }

            exposed_states = [state for state in all_states if state.entity_id in exposed_entity_ids]

            entity_registry = er.async_get(hass)
            exposed_entities = []
            for state in exposed_states:
                entity = entity_registry.async_get(state.entity_id)
                exposed_entities.append(
                    {
                        "entity_id": state.entity_id,
                        "name": state.name,
                        "state": state.state,
                        "aliases": entity.aliases if entity and entity.aliases else [],
                    }
                )

            user_input = self._get_user_input() if self._get_user_input else None
            if user_input is None:
                # Fallback for a tool invoked outside a conversation turn.
                user_input = conversation.ConversationInput(
                    text="",
                    conversation_id=tool_input.id,
                    language=llm_context.language,
                    context=llm_context.context,
                    device_id=llm_context.device_id,
                    satellite_id=None,
                    agent_id=llm_context.assistant,
                )

            result = await function_executor.execute(
                hass,
                self.function_impl,
                tool_input.tool_args,
                user_input,
                exposed_entities,
            )

            if isinstance(result, str) and len(result) > MAX_FUNCTION_RESULT_CHARS:
                LOGGER.warning(
                    "Function %s returned %s characters; truncating to %s. Every result stays in"
                    " the chat log and the whole log is re-sent on each tool iteration, so an"
                    " oversized one exhausts the model's context window",
                    self.name,
                    len(result),
                    MAX_FUNCTION_RESULT_CHARS,
                )
                result = (
                    result[:MAX_FUNCTION_RESULT_CHARS]
                    + f"\n\n[Truncated. The full result was {len(result)} characters. Narrow the"
                    " request -- ask for one item rather than everything -- and call again.]"
                )

            LOGGER.info(
                "Custom function %s executed successfully with result: %s",
                self.name,
                result,
            )
            return result

        except Exception as err:
            LOGGER.error("Error executing function %s: %s", self.name, err)
            return {"error": str(err)}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: LumoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up conversation entities."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue

        async_add_entities(
            [LumoEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class LumoEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
    LumoBaseLLMEntity,
):
    """Lumo conversation agent."""

    _attr_supports_streaming = True

    def __init__(self, entry: LumoConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the agent."""
        super().__init__(entry, subentry)
        if self.subentry.data.get(CONF_LLM_HASS_API):
            self._attr_supported_features = conversation.ConversationEntityFeature.CONTROL
        self._cached_tools: list[CustomFunctionTool] | None = None
        self._cached_yaml_hash: int | None = None
        self._current_user_input: conversation.ConversationInput | None = None

    def _get_custom_functions_as_tools(self) -> list[CustomFunctionTool]:
        """Get custom functions from configuration as Tools."""
        from .exceptions import FunctionNotFound, InvalidFunction

        try:
            function_yaml = self.subentry.data.get(CONF_FUNCTIONS)
            yaml_hash = hash(function_yaml) if function_yaml else 0

            if self._cached_tools is not None and self._cached_yaml_hash == yaml_hash:
                LOGGER.debug("Using cached function tools (%s tools)", len(self._cached_tools))
                return self._cached_tools

            functions = yaml.safe_load(function_yaml) if function_yaml else DEFAULT_CONF_FUNCTIONS

            if not functions:
                self._cached_tools = []
                self._cached_yaml_hash = yaml_hash
                return []

            tools = []
            for function in functions:
                tools.append(
                    CustomFunctionTool(
                        function_spec=function["spec"],
                        function_impl=function["function"],
                        get_user_input=lambda: self._current_user_input,
                    )
                )

            self._cached_tools = tools
            self._cached_yaml_hash = yaml_hash
            LOGGER.info("Created %s custom function tools", len(tools))
            return tools

        except (InvalidFunction, FunctionNotFound) as err:
            LOGGER.error("Error loading functions: %s", err)
            return []
        except Exception as err:
            LOGGER.error("Unexpected error loading functions: %s", err)
            return []

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """When entity is added to Home Assistant."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)
        self._cached_tools = self._get_custom_functions_as_tools()
        LOGGER.debug("Pre-parsed %s custom function tools", len(self._cached_tools))

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from Home Assistant."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Process the user input and call the API."""
        import time

        from .api import LLM_API_FLEX_ASSIST
        from .const import CONF_PERFORMANCE_TRACING, LOGGER

        options = self.subentry.data
        perf_enabled = options.get(CONF_PERFORMANCE_TRACING, False)
        self._current_user_input = user_input

        if perf_enabled:
            start_time = time.time()
            LOGGER.info("=" * 80)
            LOGGER.info("CONVERSATION START")
            LOGGER.info("=" * 80)

        # Force usage of the FlexAssistAPI
        flex_api_id = f"{LLM_API_FLEX_ASSIST}_{self.entry.entry_id}"

        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                flex_api_id,
                options.get(CONF_PROMPT),
                user_input.extra_system_prompt,
            )
            if perf_enabled:
                elapsed = time.time() - start_time
                LOGGER.info("⏱️  async_provide_llm_data: %.3fs", elapsed)
                start_time = time.time()
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        custom_function_tools = self._cached_tools or self._get_custom_functions_as_tools()
        if custom_function_tools and chat_log.llm_api:
            chat_log.llm_api.tools.extend(custom_function_tools)
            if perf_enabled:
                elapsed = time.time() - start_time
                LOGGER.info("⏱️  Load and extend custom function tools: %.3fs", elapsed)
                start_time = time.time()

        await self._async_handle_chat_log(chat_log)

        return conversation.async_get_result_from_chat_log(user_input, chat_log)
