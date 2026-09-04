"""The Lumo integration."""

from __future__ import annotations

from types import MappingProxyType

import openai
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BASE_URL,
    CONF_SKIP_AUTHENTICATION,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_CONF_BASE_URL,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_NAME,
    DEFAULT_SKIP_AUTHENTICATION,
    LOGGER,
    RECOMMENDED_AI_TASK_OPTIONS,
    VALIDATION_CHAT_MODEL,
)
from .api import async_setup_api
from .services import async_setup_services

PLATFORMS = (Platform.AI_TASK, Platform.CONVERSATION)

type LumoConfigEntry = ConfigEntry[openai.AsyncClient]


def create_client(hass: HomeAssistant, api_key: str | None, base_url: str | None) -> openai.AsyncOpenAI:
    """Build an OpenAI-compatible async client pointed at Lumo.

    The SDK refuses an empty api_key, but Lumo currently serves anonymous
    requests (on a lower quota tier), so fall back to a placeholder token when
    the user has not pasted a key.
    """
    return openai.AsyncOpenAI(
        api_key=api_key or "-",
        base_url=base_url or DEFAULT_CONF_BASE_URL,
        http_client=get_async_client(hass),
    )


async def async_validate_connection(client: openai.AsyncOpenAI, has_api_key: bool) -> bool:
    """Check the endpoint is usable; return True if the request was authenticated.

    GET /models does not authenticate on Lumo -- it answers 200 for a valid key,
    a revoked key and no key alike -- so listing models proves reachability and
    nothing else. A key can only be checked by actually completing something:

      * a well-formed but invalid/revoked key -> 401 AuthenticationError
        ({"Code": 0, "Error": "Invalid or expired API key"})
      * a string Lumo does not recognise as a credential at all -> served on the
        anonymous tier, which betrays itself by returning usage.remaining_limits
        (authenticated responses omit that field entirely)

    Without a key we only ping /models, so setup does not spend one of the ~20
    anonymous requests per window.
    """
    if not has_api_key:
        await client.with_options(timeout=10.0).models.list()
        return False

    response = await client.with_options(timeout=30.0).chat.completions.create(
        model=VALIDATION_CHAT_MODEL,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=1,
    )
    usage = response.usage
    return usage is None or getattr(usage, "remaining_limits", None) is None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Lumo."""
    await async_setup_services(hass, config)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LumoConfigEntry) -> bool:
    """Set up Lumo from a config entry."""
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_CONF_BASE_URL)
    skip_authentication = entry.data.get(CONF_SKIP_AUTHENTICATION, DEFAULT_SKIP_AUTHENTICATION)

    api_key = entry.data.get(CONF_API_KEY)
    client = create_client(hass, api_key, base_url)

    if not skip_authentication:
        try:
            authenticated = await async_validate_connection(client, bool(api_key))
        except openai.AuthenticationError as err:
            LOGGER.error("Lumo rejected the API key: %s", err)
            return False
        except openai.OpenAIError as err:
            raise ConfigEntryNotReady(err) from err

        if api_key and not authenticated:
            LOGGER.warning(
                "Lumo did not recognise the configured API key and served the request "
                "anonymously. The integration will work but is capped at the anonymous "
                "quota (~20 requests per window). Check the key on the Manage keys page."
            )

    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await async_setup_api(hass, entry)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Lumo."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_update_options(hass: HomeAssistant, entry: LumoConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: LumoConfigEntry) -> bool:
    """Migrate entry."""
    LOGGER.debug("Migrating from version %s:%s", entry.version, entry.minor_version)

    if entry.version > 2:
        return False

    if entry.version == 1:
        new_data = entry.data.copy()
        conversation_subentry = ConfigSubentry(
            data=MappingProxyType(entry.options),
            subentry_type="conversation",
            title=entry.title or DEFAULT_CONVERSATION_NAME,
            unique_id=None,
        )
        ai_task_subentry = ConfigSubentry(
            data=MappingProxyType(RECOMMENDED_AI_TASK_OPTIONS),
            subentry_type="ai_task_data",
            title=DEFAULT_AI_TASK_NAME,
            unique_id=None,
        )

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            options={},
            title=DEFAULT_NAME,
            version=2,
            minor_version=1,
        )
        hass.config_entries.async_add_subentry(entry, conversation_subentry)
        hass.config_entries.async_add_subentry(entry, ai_task_subentry)

    LOGGER.debug("Migration to version %s:%s successful", entry.version, entry.minor_version)

    return True
