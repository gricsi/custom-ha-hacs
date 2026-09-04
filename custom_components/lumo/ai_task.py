"""AI Task integration for Lumo.

Data generation only: Lumo has no image endpoint (/images/generations is a 404
and there is no image_generation tool on chat/completions), so the
GENERATE_IMAGE feature upstream OmniConv advertises is not offered here.
"""

from __future__ import annotations

import logging
from json import JSONDecodeError, loads as json_loads
from typing import TYPE_CHECKING

from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import LumoBaseLLMEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

    from . import LumoConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up AI Task entities."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "ai_task_data":
            continue

        async_add_entities(
            [LumoTaskEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class LumoTaskEntity(ai_task.AITaskEntity, LumoBaseLLMEntity):
    """Lumo AI Task entity."""

    def __init__(self, entry: LumoConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the entity."""
        super().__init__(entry, subentry)
        self._attr_supported_features = (
            ai_task.AITaskEntityFeature.GENERATE_DATA | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
        )

    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Handle a generate data task."""
        await self._async_handle_chat_log(chat_log, structure_name=task.name, structure=task.structure)

        if not isinstance(chat_log.content[-1], conversation.AssistantContent):
            raise HomeAssistantError("Last content in chat log is not an AssistantContent")

        text = chat_log.content[-1].content or ""

        if not task.structure:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id,
                data=text,
            )
        try:
            data = json_loads(text)
        except JSONDecodeError as err:
            _LOGGER.error("Failed to parse JSON response: %s. Response: %s", err, text)
            raise HomeAssistantError("Error with Lumo structured response") from err

        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )
