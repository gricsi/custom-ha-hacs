"""Usage sensors: tokens spent and, on the anonymous tier, requests left.

Every Lumo response ends with a usage payload. The stream hands it to
async_record_usage, which fans it out to the entry's sensors over the dispatcher.
Token totals start from zero after a restart; TOTAL_INCREASING tells the recorder's
statistics to treat that as a reset rather than a drop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN

if TYPE_CHECKING:
    from . import LumoConfigEntry


@dataclass(frozen=True, kw_only=True)
class LumoUsageDescription(SensorEntityDescription):
    """A sensor and how a usage payload moves its value."""

    update: Callable[[Any, StateType], StateType]


def _total(field: str) -> Callable[[Any, StateType], StateType]:
    return lambda usage, previous: (previous or 0) + (getattr(usage, field, None) or 0)


def _remaining(tier: str) -> Callable[[Any, StateType], StateType]:
    # An authenticated key reports no remaining_limits, so the last value stands.
    return lambda usage, previous: (getattr(usage, "remaining_limits", None) or {}).get(tier, previous)


SENSORS: tuple[LumoUsageDescription, ...] = (
    *(
        LumoUsageDescription(
            key=f"{kind}_tokens",
            name=f"Lumo {kind} tokens",
            native_unit_of_measurement="tokens",
            state_class=SensorStateClass.TOTAL_INCREASING,
            update=_total(field),
        )
        for kind, field in (("input", "prompt_tokens"), ("output", "completion_tokens"))
    ),
    *(
        LumoUsageDescription(
            key=f"remaining_{tier}",
            name=f"Lumo {tier} requests remaining",
            native_unit_of_measurement="requests",
            state_class=SensorStateClass.MEASUREMENT,
            update=_remaining(tier),
        )
        for tier in ("lite", "max", "images")
    ),
)


def _signal(entry_id: str) -> str:
    return f"{DOMAIN}_usage_{entry_id}"


@callback
def async_record_usage(hass: HomeAssistant, entry_id: str, usage: Any) -> None:
    """Pass one response's usage payload to the entry's sensors."""
    async_dispatcher_send(hass, _signal(entry_id), usage)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LumoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the usage sensors."""
    async_add_entities(LumoUsageSensor(entry.entry_id, description) for description in SENSORS)


class LumoUsageSensor(SensorEntity):
    """One usage figure, moved by each response."""

    entity_description: LumoUsageDescription

    def __init__(self, entry_id: str, description: LumoUsageDescription) -> None:
        self.entity_description = description
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, _signal(self._entry_id), self._async_on_usage))

    @callback
    def _async_on_usage(self, usage: Any) -> None:
        self._attr_native_value = self.entity_description.update(usage, self.native_value)
        self.async_write_ha_state()
