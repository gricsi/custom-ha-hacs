"""A per-entry Assist API that injects a user-templated exposed-entities prompt.

Home Assistant moved AssistAPI out of homeassistant.helpers.llm and into the
homeassistant.components.llm integration, and replaced its overridable private
hooks (_async_get_tools / _async_get_api_prompt / _async_get_preable) with a
single platform-aggregating async_get_tools(). Upstream OmniConv subclasses the
old AssistAPI and overrides those hooks, which no longer exist -- so this builds
the APIInstance directly on top of the public helpers instead.
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import cache, partial

import slugify as unicode_slug
from homeassistant.components.llm import async_get_tools
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from homeassistant.helpers.llm import API, APIInstance, LLMContext, Tool, selector_serializer
from homeassistant.helpers.template import Template

from .const import CONF_ENTITIES_PROMPT, DEFAULT_ENTITIES_PROMPT

_LOGGER = logging.getLogger(__name__)

LLM_API_FLEX_ASSIST = "flex_assist"
LLM_API_FLEX_ASSIST_NAME = "Lumo Assist API"

# Tool names to withhold from the model, matched on the unprefixed suffix since
# core namespaces platform tools as <domain>__<tool>.
#
# Upstream OmniConv dropped get_home_state here, because this integration feeds
# entity state in through its own entities_prompt template. That tool no longer
# exists: core replaced it with homeassistant__GetLiveContext, which is kept,
# because a live lookup is still worth having when the cached prompt is stale.
EXCLUDED_TOOLS: set[str] = set()


class FlexAssistAPI(API):
    """Assist API variant whose prompt is rendered from a user template."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        id_suffix: str = "",
        name_suffix: str = "",
    ) -> None:
        """Init the class."""
        super().__init__(
            hass=hass,
            id=f"{LLM_API_FLEX_ASSIST}_{id_suffix}" if id_suffix else LLM_API_FLEX_ASSIST,
            name=(f"{LLM_API_FLEX_ASSIST_NAME} — {name_suffix}" if name_suffix else LLM_API_FLEX_ASSIST_NAME),
        )
        self.cached_slugify = cache(partial(unicode_slug.slugify, separator="_", lowercase=False))
        self.config_entry = config_entry
        self._entities_cache: dict[str, tuple[float, str]] = {}
        self._cache_lock = asyncio.Lock()
        self._background_task: asyncio.Task | None = None
        self._stop_background = False

    def _cached_entities_prompt(self) -> str | None:
        """Return the most recently rendered entities prompt, if any."""
        if not self._entities_cache:
            return None
        timestamp, rendered = max(self._entities_cache.values(), key=lambda item: item[0])
        _LOGGER.debug("Using cached entities prompt (age: %.1fs)", time.time() - timestamp)
        return rendered

    async def async_get_api_instance(self, llm_context: LLMContext) -> APIInstance:
        """Build an API instance from core's Assist tools plus our own prompt."""
        # Ask for LLM_API_ASSIST rather than self.id: core's tool platforms check
        # the api_id and return nothing for an id they do not recognise, so
        # passing our own id would yield an agent with zero tools.
        llm_tools = await async_get_tools(self.hass, llm_context, llm.LLM_API_ASSIST)

        tools: list[Tool] = [tool for tool in llm_tools.tools if tool.name.split("__")[-1] not in EXCLUDED_TOOLS]

        prompt_parts: list[str] = []
        if llm_tools.prompt:
            prompt_parts.append(llm_tools.prompt)
        if entities_prompt := self._cached_entities_prompt():
            prompt_parts.append(entities_prompt)
        else:
            _LOGGER.debug("Entities prompt cache is cold; background refresh will populate it")

        return APIInstance(
            api=self,
            api_prompt="\n".join(prompt_parts),
            llm_context=llm_context,
            tools=tools,
            custom_serializer=selector_serializer,
        )

    async def start_background_refresh(self) -> None:
        """Start background task to refresh entities cache."""
        self._background_task = asyncio.create_task(self._background_refresh_loop())
        _LOGGER.info("Started background entities cache refresh task")

    async def stop_background_refresh(self) -> None:
        """Stop background refresh task."""
        self._stop_background = True
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
        _LOGGER.info("Stopped background entities cache refresh task")

    async def _background_refresh_loop(self) -> None:
        """Background loop to refresh entities cache every 15 seconds."""
        while not self._stop_background:
            try:
                await self._refresh_all_caches()
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error("Error in background cache refresh: %s", err)
                await asyncio.sleep(15)

    async def _refresh_all_caches(self) -> None:
        """Re-render the entities prompt for the currently exposed entities."""
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose

        all_states = self.hass.states.async_all()
        exposed_states = [
            state for state in all_states if async_should_expose(self.hass, "conversation", state.entity_id)
        ]

        if not exposed_states:
            return

        entity_registry = er.async_get(self.hass)
        area_registry = ar.async_get(self.hass)

        area_lookup = {}
        for state in exposed_states:
            entity_entry = entity_registry.async_get(state.entity_id)
            if entity_entry and entity_entry.area_id:
                area = area_registry.async_get_area(entity_entry.area_id)
                area_lookup[state.entity_id] = area.name if area else None
            else:
                area_lookup[state.entity_id] = None

        template_entities = []
        for state in exposed_states:
            entity = entity_registry.async_get(state.entity_id)
            template_entities.append(
                {
                    "entity_id": state.entity_id,
                    "name": state.name,
                    "state": state.state,
                    "aliases": entity.aliases if entity and entity.aliases else [],
                }
            )

        entities_prompt_template = self.config_entry.options.get(CONF_ENTITIES_PROMPT, DEFAULT_ENTITIES_PROMPT)
        template_vars = {
            "ha_name": self.hass.config.location_name,
            "exposed_entities": template_entities,
            "exposed": {"entities": {state.entity_id: {} for state in exposed_states}},
            "current_device_id": None,
            "area_name": lambda eid: area_lookup.get(eid),
        }

        template_obj = Template(entities_prompt_template, self.hass)
        try:
            rendered_prompt = await self.hass.async_add_executor_job(template_obj.render, template_vars, False, False)
        except Exception as err:
            _LOGGER.error("Error rendering entities prompt template: %s", err)
            return

        entity_ids = sorted([state.entity_id for state in exposed_states])
        cache_key = f"exposed:{hash(tuple(entity_ids))}"

        async with self._cache_lock:
            self._entities_cache[cache_key] = (time.time(), rendered_prompt)

            if len(self._entities_cache) > 10:
                sorted_keys = sorted(self._entities_cache.keys(), key=lambda k: self._entities_cache[k][0])
                for old_key in sorted_keys[:-10]:
                    del self._entities_cache[old_key]

        _LOGGER.debug("Refreshed entities cache (%s entities)", len(exposed_states))


async def async_setup_api(hass: HomeAssistant, entry: ConfigEntry) -> FlexAssistAPI:
    """Set up the FlexAssistAPI with background caching."""
    api_instance = FlexAssistAPI(hass, entry, entry.entry_id, entry.title)
    unreg = llm.async_register_api(hass, api_instance)
    entry.async_on_unload(unreg)

    await api_instance.start_background_refresh()
    entry.async_on_unload(api_instance.stop_background_refresh)

    return api_instance
