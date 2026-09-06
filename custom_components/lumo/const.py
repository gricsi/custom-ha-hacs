# ruff: noqa: E501
"""Constants for the Lumo integration."""

import logging

from homeassistant.helpers import llm

DOMAIN = "lumo"
LOGGER: logging.Logger = logging.getLogger(__package__)

DEFAULT_CONVERSATION_NAME = "Lumo Conversation"
DEFAULT_AI_TASK_NAME = "Lumo AI Task"
DEFAULT_NAME = "Lumo"

CONF_BASE_URL = "base_url"
DEFAULT_CONF_BASE_URL = "https://lumo.proton.me/api/ai/v1"
CONF_SKIP_AUTHENTICATION = "skip_authentication"
DEFAULT_SKIP_AUTHENTICATION = False

CONF_CHAT_MODEL = "chat_model"
CONF_FILENAMES = "filenames"
CONF_MAX_TOKENS = "max_tokens"
CONF_PROMPT = "prompt"
CONF_ENTITIES_PROMPT = "entities_prompt"
CONF_REASONING_EFFORT = "reasoning_effort"
CONF_RECOMMENDED = "recommended"
CONF_TEMPERATURE = "temperature"
CONF_TOP_P = "top_p"
CONF_PAYLOAD_TEMPLATE = "payload_template"

CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION = "max_function_calls_per_conversation"
CONF_FUNCTIONS = "functions"
CONF_ATTACH_USERNAME = "attach_username"
CONF_CONTEXT_THRESHOLD = "context_threshold"
CONF_CONTEXT_TRUNCATE_STRATEGY = "context_truncate_strategy"
CONF_PERFORMANCE_TRACING = "performance_tracing"

# Fallbacks only. The live model list is fetched from GET {base_url}/models, which
# is what the config flow offers in its dropdown. Keep these in sync loosely; the
# API silently falls back to lumo-lite for an unknown model id rather than erroring,
# so a stale value here degrades quietly instead of failing loudly.
LUMO_MODELS: list[str] = ["lumo-lite", "lumo-max"]

DEFAULT_CHAT_MODEL = "lumo-max"
RECOMMENDED_CHAT_MODEL = "lumo-max"

# Setup's credential probe deliberately does not use the default model: it only
# needs a one-token round trip, and on the anonymous tier each tier has its own
# separate budget, so checking a key should not spend from the max allowance.
VALIDATION_CHAT_MODEL = "lumo-lite"

# The API docs give 0.3 as the default for the temperature parameter, while
# /models reports default_model_temperature: 1 for both models. 0.3 is the better
# choice here regardless: a smart-home agent wants deterministic device control.
DEFAULT_TEMPERATURE = 0.3
RECOMMENDED_TEMPERATURE = 0.3
DEFAULT_TOP_P = 1.0
RECOMMENDED_TOP_P = 1.0

# lumo-max advertises a 131072 token context (lumo-lite 262144). 150 output tokens
# (OmniConv's default) truncates mid-sentence on anything but a one-line answer.
DEFAULT_MAX_TOKENS = 1500
RECOMMENDED_MAX_TOKENS = 3000

DEFAULT_REASONING_EFFORT = "medium"
RECOMMENDED_REASONING_EFFORT = "medium"
REASONING_EFFORTS: list[str] = ["none", "medium", "high", "max"]

DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION = 1
DEFAULT_ATTACH_USERNAME = False
DEFAULT_CONTEXT_THRESHOLD = 13000

CONF_LLM_HASS_API = "llm_hass_api"

RECOMMENDED_CONVERSATION_OPTIONS = {
    CONF_RECOMMENDED: True,
    CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
    CONF_PROMPT: llm.DEFAULT_INSTRUCTIONS_PROMPT,
}
RECOMMENDED_AI_TASK_OPTIONS = {
    CONF_RECOMMENDED: True,
}

CONTEXT_TRUNCATE_STRATEGIES = [{"key": "clear", "label": "Clear All Messages"}]
DEFAULT_CONTEXT_TRUNCATE_STRATEGY = CONTEXT_TRUNCATE_STRATEGIES[0]["key"]

EVENT_AUTOMATION_REGISTERED = "automation_registered_via_lumo"
EVENT_CONVERSATION_FINISHED = "lumo.conversation.finished"

# Proton returns its own error envelope rather than OpenAI's {"error": {...}}:
#   {"Code": 2000, "Error": "Missing required attributes model,messages", "Details": {}}
# The openai SDK cannot find a message in that shape, so errors surface as bare
# status codes unless we unwrap it ourselves. See _lumo_error_detail in entity.py.
LUMO_ERROR_CODE_KEY = "Code"
LUMO_ERROR_MESSAGE_KEY = "Error"

DEFAULT_PROMPT = """I want you to act as smart home manager of Home Assistant.
I will provide information of smart home along with a question, you will truthfully make correction or answer using information provided in one sentence in everyday language.

Current Time: {{now()}}
Current Area: {{area_name(current_device_id)}}

The current state of devices is provided below. Use execute_services function only for requested action, not for current states.
Do not execute services without user's confirmation.
Do not restate or appreciate what user says, rather make a quick inquiry.
"""

# state_attr() rather than states[...].attributes.options: only select-like
# entities carry an "options" attribute, and attribute access on a state that
# lacks it makes Home Assistant log a template warning *per entity, per render*.
# With the 15 second background refresh that is hundreds of warnings a minute.
# state_attr() returns None quietly instead. Joining with "/" also keeps a
# multi-option list from injecting commas into the CSV, as aliases already do.
DEFAULT_ENTITIES_PROMPT = """Available Devices:
```csv
entity_id,name,area_name,state,state_options,aliases
{% for entity in exposed_entities -%}
{%   if states[entity.entity_id] -%}
{{      entity.entity_id }},{{ entity.name }},{{area_name(entity.entity_id)}},{{ entity.state }},{{ (state_attr(entity.entity_id, 'options') or []) | join('/') }},{{entity.aliases | join('/')}}
{%   endif -%}
{% endfor -%}
```
"""

DEFAULT_CONF_FUNCTIONS = [
    {
        "spec": {
            "name": "execute_services",
            "description": "Use this function to execute service of devices in Home Assistant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "domain": {
                                    "type": "string",
                                    "description": "The domain of the service",
                                },
                                "service": {
                                    "type": "string",
                                    "description": "The service to be called",
                                },
                                "service_data": {
                                    "type": "object",
                                    "description": "The service data object to indicate what to" " control.",
                                    "properties": {
                                        "entity_id": {
                                            "type": "string",
                                            "description": (
                                                "The entity_id retrieved from available"
                                                " devices. It must start with domain,"
                                                " followed by dot character."
                                            ),
                                        }
                                    },
                                    "required": ["entity_id"],
                                },
                            },
                            "required": ["domain", "service", "service_data"],
                        },
                    }
                },
            },
        },
        "function": {"type": "native", "name": "execute_service"},
    }
]
