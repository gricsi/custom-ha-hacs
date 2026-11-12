"""Authorization at the executor boundary and exposure of resolved service targets."""

import asyncio
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

from custom_components.lumo.exceptions import EntityNotExposed
from custom_components.lumo.helpers import NativeFunctionExecutor


@pytest.mark.parametrize("name", ["read_logs", "read_config", "render_template", "get_traces", "add_automation"])
@pytest.mark.parametrize("identity", ["admin", "member", "deleted", None])
def test_installation_tools_require_an_administrator(name, identity):
    async def main():
        executor = NativeFunctionExecutor()
        implementation = AsyncMock(return_value={"ok": True})
        setattr(executor, name, implementation)
        get_user = AsyncMock(
            return_value=None if identity == "deleted" else SimpleNamespace(is_admin=identity == "admin")
        )
        hass = SimpleNamespace(auth=SimpleNamespace(async_get_user=get_user))
        user_input = SimpleNamespace(context=Context(user_id=identity))
        if identity == "admin":
            assert await executor.execute(hass, {"name": name}, {}, user_input, []) == {"ok": True}
            implementation.assert_awaited_once()
        else:
            with pytest.raises(Unauthorized):
                await executor.execute(hass, {"name": name}, {}, user_input, [])
            implementation.assert_not_awaited()

    asyncio.run(main())


@pytest.mark.parametrize("selector", ["entity_id", "area_id", "device_id"])
@pytest.mark.parametrize("expose_second", [False, True])
def test_resolved_targets_are_exposed_and_keep_the_callers_context(selector, expose_second):
    async def main():
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            from homeassistant import loader
            from homeassistant.config_entries import ConfigEntries, ConfigEntry

            loader.async_setup(hass)
            hass.config_entries = ConfigEntries(hass, {})
            await hass.config_entries.async_initialize()
            config_entry = ConfigEntry(
                domain="test",
                title="Test",
                data={},
                source="user",
                version=1,
                minor_version=1,
                options={},
                unique_id=None,
                discovery_keys={},
                subentries_data=None,
            )
            hass.config_entries._entries[config_entry.entry_id] = config_entry
            from homeassistant.helpers import floor_registry as fr, label_registry as lr

            for registry in (fr, lr, ar, dr, er):
                if hasattr(registry, "async_setup"):
                    registry.async_setup(hass)
                await registry.async_load(hass)
            area = ar.async_get(hass).async_create("Kitchen")
            device = dr.async_get(hass).async_get_or_create(
                config_entry_id=config_entry.entry_id, identifiers={("test", "device")}
            )
            dr.async_get(hass).async_update_device(device.id, area_id=area.id)
            entities = []
            for unique_id in ("one", "two"):
                entry = er.async_get(hass).async_get_or_create("light", "test", unique_id, device_id=device.id)
                hass.states.async_set(entry.entity_id, "off")
                entities.append(entry.entity_id)
            received = []
            hass.services.async_register("light", "turn_on", lambda call: received.append(call))
            targets = {"entity_id": entities, "area_id": area.id, "device_id": device.id}
            arguments = {"domain": "light", "service": "turn_on", "service_data": {selector: targets[selector]}}
            original = dict(arguments["service_data"])
            context = Context(user_id="alice")
            exposed = [{"entity_id": entity_id} for entity_id in (entities if expose_second else entities[:1])]
            invocation = NativeFunctionExecutor().execute_service_single(
                hass, {}, arguments, SimpleNamespace(context=context), exposed
            )
            if expose_second:
                assert await invocation == {"success": True}
                assert len(received) == 1
                assert received[0].context is context
                assert set(received[0].data["entity_id"]) == set(entities)
                assert "area_id" not in received[0].data and "device_id" not in received[0].data
            else:
                with pytest.raises(EntityNotExposed):
                    await invocation
                assert not received
            assert arguments["service_data"] == original
        finally:
            await hass.async_stop(force=True)

    asyncio.run(main())
