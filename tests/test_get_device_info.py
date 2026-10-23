"""Tests for the get_device_info function.

Seeing only entity names and states, an agent asked why a device is offline guesses at the
hardware. get_device_info returns what the device page shows, for any device in the registry:
maker, model, firmware, integration, the configuration URL, and every entity with its state.

It is a template function, so these tests render the YAML in the example README itself,
against a real Home Assistant with real device and entity registries.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from homeassistant import config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

from custom_components.lumo.helpers import get_function_executor

README = Path(__file__).parent.parent / "examples" / "function" / "get_device_info" / "README.md"


def example() -> dict[str, Any]:
    return yaml.safe_load(re.search(r"```yaml\n(.*?)```", README.read_text(), re.S).group(1))[0]


def device_info(targets: list[str]) -> dict[str, Any]:
    """Render the function for `targets` against a home with one relay in the utility room."""

    async def main() -> str:
        hass = HomeAssistant(tempfile.mkdtemp())
        loader.async_setup(hass)
        try:
            for registry in (ar, dr, er):
                if hasattr(registry, "async_setup"):
                    registry.async_setup(hass)
                await registry.async_load(hass)
            hass.config_entries = config_entries.ConfigEntries(hass, {})
            await hass.config_entries.async_initialize()
            entry = config_entries.ConfigEntry(
                domain="shelly",
                title="Boiler Relay",
                data={},
                source="user",
                version=1,
                minor_version=1,
                options={},
                unique_id=None,
                discovery_keys={},
                subentries_data=None,
            )
            hass.config_entries._entries[entry.entry_id] = entry
            area = ar.async_get(hass).async_create("Utility room")
            device = dr.async_get(hass).async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={("shelly", "aabbccddeeff")},
                connections={("mac", "aa:bb:cc:dd:ee:ff")},
                manufacturer="Shelly",
                model="Shelly Plus 1",
                model_id="SNSW-001X16EU",
                sw_version="1.4.4",
                hw_version="gen2",
                name="Boiler Relay",
                configuration_url="http://192.168.1.50",
            )
            dr.async_get(hass).async_update_device(device.id, area_id=area.id)
            for object_id, state in (
                ("boiler_relay_temperature", "unavailable"),
                ("boiler_relay_uptime", "unavailable"),
            ):
                entity = er.async_get(hass).async_get_or_create(
                    "sensor", "shelly", object_id, device_id=device.id, suggested_object_id=object_id
                )
                hass.states.async_set(entity.entity_id, state)
            hass.states.async_set("sun.sun", "above_horizon")  # an entity with no device

            function = example()["function"]
            executor = get_function_executor(function["type"])
            return await executor.execute(hass, executor.to_arguments(function), {"targets": targets}, None, [])
        finally:
            await hass.async_stop(force=True)

    lines = [line for line in asyncio.run(main()).splitlines() if line.strip()]
    return {line.split(": ", 1)[0]: line.split(": ", 1)[1] for line in lines}


def test_an_entity_leads_to_its_device_and_hardware() -> None:
    info = json.loads(device_info(["sensor.boiler_relay_temperature"])["sensor.boiler_relay_temperature"])
    assert info == {
        "device": "Boiler Relay",
        "manufacturer": "Shelly",
        "model": "Shelly Plus 1",
        "model_id": "SNSW-001X16EU",
        "sw_version": "1.4.4",
        "hw_version": "gen2",
        "integration": "shelly",
        "configuration_url": "http://192.168.1.50",
        "connections": ["mac aa:bb:cc:dd:ee:ff"],
        "area": "Utility room",
        "entities": {"sensor.boiler_relay_temperature": "unavailable", "sensor.boiler_relay_uptime": "unavailable"},
    }


def test_a_device_can_be_named_directly() -> None:
    assert json.loads(device_info(["Boiler Relay"])["Boiler Relay"])["model"] == "Shelly Plus 1"


def test_unknown_targets_and_deviceless_entities_say_so() -> None:
    result = device_info(["sun.sun", "Nonexistent"])
    assert result["sun.sun"] == "not part of a device"
    assert result["Nonexistent"] == "no device or entity by that name"


def test_it_declares_itself_read_only() -> None:
    assert example()["spec"]["annotations"] == {
        "read_only": True,
        "destructive": False,
        "idempotent": True,
        "open_world": False,
    }
