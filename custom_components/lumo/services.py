"""Services for the Lumo integration.

Only lumo.generate_content is registered. Upstream OmniConv also ships
generate_image (DALL-E) and query_image; Lumo has no image endpoint, and
query_image is subsumed by generate_content now that both models support vision.
"""

from __future__ import annotations

import logging
from pathlib import Path

import openai
import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CHAT_MODEL,
    CONF_FILENAMES,
    CONF_MAX_TOKENS,
    CONF_PROMPT,
    CONF_REASONING_EFFORT,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DOMAIN,
    RECOMMENDED_CHAT_MODEL,
    RECOMMENDED_MAX_TOKENS,
    RECOMMENDED_REASONING_EFFORT,
    RECOMMENDED_TEMPERATURE,
    RECOMMENDED_TOP_P,
)
from .entity import lumo_error_detail, async_prepare_files_for_prompt

SERVICE_GENERATE_CONTENT = "generate_content"

_LOGGER = logging.getLogger(__package__)


async def async_setup_services(hass: HomeAssistant, config: ConfigType) -> None:
    """Set up services for the Lumo component."""

    async def send_prompt(call: ServiceCall) -> ServiceResponse:
        """Send a prompt to Lumo and return the response."""
        entry_id = call.data["config_entry"]
        entry = hass.config_entries.async_get_entry(entry_id)

        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_config_entry",
                translation_placeholders={"config_entry": entry_id},
            )

        conversation_subentry = next(
            (subentry for subentry in entry.subentries.values() if subentry.subentry_type == "conversation"),
            None,
        )
        settings = conversation_subentry.data if conversation_subentry else {}

        model: str = settings.get(CONF_CHAT_MODEL, RECOMMENDED_CHAT_MODEL)
        max_tokens: int = settings.get(CONF_MAX_TOKENS, RECOMMENDED_MAX_TOKENS)
        top_p: float = settings.get(CONF_TOP_P, RECOMMENDED_TOP_P)
        temperature: float = settings.get(CONF_TEMPERATURE, RECOMMENDED_TEMPERATURE)
        reasoning_effort: str = settings.get(CONF_REASONING_EFFORT, RECOMMENDED_REASONING_EFFORT)

        client: openai.AsyncClient = entry.runtime_data

        content: list[dict] = [{"type": "text", "text": call.data[CONF_PROMPT]}]

        if filenames := call.data.get(CONF_FILENAMES):
            for filename in filenames:
                if not hass.config.is_allowed_path(filename):
                    raise HomeAssistantError(
                        f"Cannot read `{filename}`, no access to path; "
                        "`allowlist_external_dirs` may need to be adjusted in "
                        "`configuration.yaml`"
                    )

            content.extend(
                await async_prepare_files_for_prompt(hass, [(Path(filename), None) for filename in filenames])
            )

        model_args = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "top_p": top_p,
            "temperature": temperature,
            "user": call.context.user_id,
        }

        if reasoning_effort:
            model_args["reasoning_effort"] = reasoning_effort

        try:
            response = await client.chat.completions.create(**model_args)
        except openai.APIStatusError as err:
            raise HomeAssistantError(f"Error generating content: {lumo_error_detail(err)}") from err
        except openai.OpenAIError as err:
            raise HomeAssistantError(f"Error generating content: {err}") from err
        except FileNotFoundError as err:
            raise HomeAssistantError(f"Error generating content: {err}") from err

        if not response.choices:
            raise HomeAssistantError("Lumo returned no choices")

        return {"text": response.choices[0].message.content or ""}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_CONTENT,
        send_prompt,
        schema=vol.Schema(
            {
                vol.Required("config_entry"): selector.ConfigEntrySelector({"integration": DOMAIN}),
                vol.Required(CONF_PROMPT): cv.string,
                vol.Optional(CONF_FILENAMES, default=[]): vol.All(cv.ensure_list, [cv.string]),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
