"""Config flow for the Lumo integration."""

from __future__ import annotations

import logging
from typing import Any

import openai
import voluptuous as vol
import yaml
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import llm
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
)
from homeassistant.helpers.typing import VolDictType

from .const import (
    CONF_ATTACH_USERNAME,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONF_ENTITIES_PROMPT,
    CONF_FUNCTIONS,
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    CONF_MAX_TOKENS,
    CONF_PERFORMANCE_TRACING,
    CONF_PROMPT,
    CONF_REASONING_EFFORT,
    CONF_RECOMMENDED,
    CONF_SKIP_AUTHENTICATION,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONTEXT_TRUNCATE_STRATEGIES,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_ATTACH_USERNAME,
    DEFAULT_CONF_BASE_URL,
    DEFAULT_CONF_FUNCTIONS,
    DEFAULT_CONTEXT_THRESHOLD,
    DEFAULT_CONTEXT_TRUNCATE_STRATEGY,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_ENTITIES_PROMPT,
    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    DEFAULT_NAME,
    DEFAULT_SKIP_AUTHENTICATION,
    DOMAIN,
    LUMO_MODELS,
    REASONING_EFFORTS,
    RECOMMENDED_AI_TASK_OPTIONS,
    RECOMMENDED_CHAT_MODEL,
    RECOMMENDED_CONVERSATION_OPTIONS,
    RECOMMENDED_MAX_TOKENS,
    RECOMMENDED_REASONING_EFFORT,
    RECOMMENDED_TEMPERATURE,
    RECOMMENDED_TOP_P,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_CONF_FUNCTIONS_STR = yaml.dump(DEFAULT_CONF_FUNCTIONS, sort_keys=False)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        # Lumo answers anonymous requests on a lower quota tier, so the key is
        # optional. Base URL stays editable: the API is pre-GA and the path may
        # move, and this also lets you point the integration at a local model.
        vol.Optional(CONF_API_KEY): str,
        vol.Optional(CONF_BASE_URL, default=DEFAULT_CONF_BASE_URL): str,
        vol.Optional(CONF_SKIP_AUTHENTICATION, default=DEFAULT_SKIP_AUTHENTICATION): bool,
    }
)


async def async_validate_input(hass: HomeAssistant, api_key: str | None, base_url: str | None) -> bool:
    """Validate the endpoint and key; return True if the key was accepted."""
    from . import async_validate_connection, create_client

    client = create_client(hass, api_key, base_url)
    return await async_validate_connection(client, bool(api_key))


class LumoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lumo."""

    VERSION = 2
    MINOR_VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

        errors: dict[str, str] = {}

        if CONF_API_KEY in user_input:
            self._async_abort_entries_match({CONF_API_KEY: user_input[CONF_API_KEY]})

        if not user_input.get(CONF_SKIP_AUTHENTICATION, DEFAULT_SKIP_AUTHENTICATION):
            api_key = user_input.get(CONF_API_KEY)
            try:
                authenticated = await async_validate_input(
                    self.hass,
                    api_key,
                    user_input.get(CONF_BASE_URL),
                )
            except openai.APIConnectionError:
                errors["base"] = "cannot_connect"
            except openai.AuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception validating the Lumo endpoint")
                errors["base"] = "unknown"
            else:
                # Lumo serves an unrecognised credential anonymously with HTTP 200
                # rather than rejecting it, so this is the only way to tell the
                # user their key is not actually being used.
                if api_key and not authenticated:
                    errors[CONF_API_KEY] = "key_not_accepted"

        if not errors:
            return self.async_create_entry(
                title=DEFAULT_NAME,
                data=user_input,
                subentries=[
                    {
                        "subentry_type": "conversation",
                        "data": RECOMMENDED_CONVERSATION_OPTIONS,
                        "title": DEFAULT_CONVERSATION_NAME,
                        "unique_id": None,
                    },
                    {
                        "subentry_type": "ai_task_data",
                        "data": RECOMMENDED_AI_TASK_OPTIONS,
                        "title": DEFAULT_AI_TASK_NAME,
                        "unique_id": None,
                    },
                ],
            )

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {
            "conversation": LumoSubentryFlowHandler,
            "ai_task_data": LumoSubentryFlowHandler,
        }


class LumoSubentryFlowHandler(ConfigSubentryFlow):
    """Flow for managing Lumo subentries."""

    options: dict[str, Any]

    @property
    def _is_new(self) -> bool:
        """Return if this is a new subentry."""
        return self.source == "user"

    async def _async_model_options(self) -> list[SelectOptionDict]:
        """Offer the models Lumo advertises, falling back to the known ids.

        Worth fetching live: Lumo silently downgrades an unrecognised model id to
        lumo-lite instead of returning an error, so a typo would otherwise be
        invisible.
        """
        entry = self._get_entry()
        try:
            listing = await entry.runtime_data.with_options(timeout=10.0).models.list()
        except openai.OpenAIError as err:
            _LOGGER.warning("Could not fetch the Lumo model list, using built-in defaults: %s", err)
            return [SelectOptionDict(value=model, label=model) for model in LUMO_MODELS]

        options: list[SelectOptionDict] = []
        for model in listing.data:
            if getattr(model, "archived", False):
                continue
            name = getattr(model, "name", None) or model.id
            context = getattr(model, "max_context_length", None)
            label = f"{name} ({model.id})" if name != model.id else model.id
            if context:
                label = f"{label} — {context // 1024}k context"
            options.append(SelectOptionDict(value=model.id, label=label))

        return options or [SelectOptionDict(value=model, label=model) for model in LUMO_MODELS]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a subentry."""
        if self._subentry_type == "ai_task_data":
            self.options = RECOMMENDED_AI_TASK_OPTIONS.copy()
        else:
            self.options = RECOMMENDED_CONVERSATION_OPTIONS.copy()
        return await self.async_step_init()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Handle reconfiguration of a subentry."""
        self.options = self._get_reconfigure_subentry().data.copy()
        return await self.async_step_init()

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Manage initial options."""
        if self._get_entry().state != ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        options = self.options

        hass_apis: list[SelectOptionDict] = [
            SelectOptionDict(label=api.name, value=api.id) for api in llm.async_get_apis(self.hass)
        ]
        if (suggested_llm_apis := options.get(CONF_LLM_HASS_API)) and isinstance(suggested_llm_apis, str):
            options[CONF_LLM_HASS_API] = [suggested_llm_apis]

        step_schema: VolDictType = {}

        if self._is_new:
            default_name = DEFAULT_AI_TASK_NAME if self._subentry_type == "ai_task_data" else DEFAULT_CONVERSATION_NAME
            step_schema[vol.Required(CONF_NAME, default=default_name)] = str

        if self._subentry_type == "conversation":
            step_schema.update(
                {
                    vol.Optional(
                        CONF_PROMPT,
                        description={"suggested_value": options.get(CONF_PROMPT, llm.DEFAULT_INSTRUCTIONS_PROMPT)},
                    ): TemplateSelector(),
                    vol.Optional(
                        CONF_ENTITIES_PROMPT,
                        description={"suggested_value": options.get(CONF_ENTITIES_PROMPT, DEFAULT_ENTITIES_PROMPT)},
                    ): TemplateSelector(),
                    vol.Optional(CONF_LLM_HASS_API): SelectSelector(
                        SelectSelectorConfig(options=hass_apis, multiple=True)
                    ),
                }
            )

        step_schema[vol.Required(CONF_RECOMMENDED, default=options.get(CONF_RECOMMENDED, False))] = bool
        step_schema[vol.Optional(CONF_PERFORMANCE_TRACING, default=options.get(CONF_PERFORMANCE_TRACING, False))] = bool

        if user_input is not None:
            if not user_input.get(CONF_LLM_HASS_API):
                user_input.pop(CONF_LLM_HASS_API, None)

            if user_input[CONF_RECOMMENDED]:
                if self._is_new:
                    return self.async_create_entry(title=user_input.pop(CONF_NAME), data=user_input)
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    data=user_input,
                )

            options.update(user_input)
            if CONF_LLM_HASS_API in options and CONF_LLM_HASS_API not in user_input:
                options.pop(CONF_LLM_HASS_API)
            return await self.async_step_advanced()

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(step_schema), options),
        )

    async def async_step_advanced(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Manage advanced options."""
        options = self.options

        step_schema: VolDictType = {
            vol.Optional(CONF_CHAT_MODEL, default=RECOMMENDED_CHAT_MODEL): SelectSelector(
                SelectSelectorConfig(
                    options=await self._async_model_options(),
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=True,
                )
            ),
            vol.Optional(CONF_MAX_TOKENS, default=RECOMMENDED_MAX_TOKENS): int,
            vol.Optional(CONF_TOP_P, default=RECOMMENDED_TOP_P): NumberSelector(
                NumberSelectorConfig(min=0, max=1, step=0.05)
            ),
            vol.Optional(CONF_TEMPERATURE, default=RECOMMENDED_TEMPERATURE): NumberSelector(
                NumberSelectorConfig(min=0, max=2, step=0.05)
            ),
            vol.Optional(CONF_REASONING_EFFORT, default=RECOMMENDED_REASONING_EFFORT): SelectSelector(
                SelectSelectorConfig(
                    options=REASONING_EFFORTS,
                    translation_key=CONF_REASONING_EFFORT,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }

        if user_input is not None:
            options.update(user_input)
            return await self.async_step_model()

        return self.async_show_form(
            step_id="advanced",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(step_schema), options),
        )

    async def async_step_model(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Manage conversation-specific options."""
        options = self.options
        errors: dict[str, str] = {}

        step_schema: VolDictType = {}

        if self._subentry_type == "conversation":
            step_schema.update(
                {
                    vol.Optional(CONF_FUNCTIONS, default=DEFAULT_CONF_FUNCTIONS_STR): TemplateSelector(),
                    vol.Optional(
                        CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
                        default=DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
                    ): int,
                    vol.Optional(CONF_ATTACH_USERNAME, default=DEFAULT_ATTACH_USERNAME): BooleanSelector(),
                    vol.Optional(CONF_CONTEXT_THRESHOLD, default=DEFAULT_CONTEXT_THRESHOLD): int,
                    vol.Optional(
                        CONF_CONTEXT_TRUNCATE_STRATEGY,
                        default=DEFAULT_CONTEXT_TRUNCATE_STRATEGY,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=strategy["key"], label=strategy["label"])
                                for strategy in CONTEXT_TRUNCATE_STRATEGIES
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            )

        if user_input is not None:
            if CONF_FUNCTIONS in user_input and isinstance(user_input[CONF_FUNCTIONS], str):
                try:
                    yaml.safe_load(user_input[CONF_FUNCTIONS])
                except yaml.YAMLError as err:
                    errors[CONF_FUNCTIONS] = f"Invalid YAML: {err}"

            options.update(user_input)
            if not errors:
                if self._is_new:
                    return self.async_create_entry(title=options.pop(CONF_NAME), data=options)
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    data=options,
                )

        if not step_schema:
            # AI Task subentries have nothing left to configure here.
            if self._is_new:
                return self.async_create_entry(title=options.pop(CONF_NAME), data=options)
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=options,
            )

        return self.async_show_form(
            step_id="model",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(step_schema), options),
            errors=errors,
        )
