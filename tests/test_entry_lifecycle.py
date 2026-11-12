"""Drive credential updates through real config entry update listeners."""

import asyncio
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
import openai

from custom_components.lumo import async_setup_entry, async_update_options
from custom_components.lumo.config_flow import LumoConfigFlow


@pytest.mark.parametrize("source", ["reconfigure", "reauth"])
@pytest.mark.parametrize("loaded", [False, True])
def test_credential_update_reloads_once_and_preserves_agents(monkeypatch, source, loaded):
    async def main():
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            hass.config_entries = ConfigEntries(hass, {})
            await hass.config_entries.async_initialize()
            entry = ConfigEntry(
                domain="lumo",
                title="Lumo",
                data={"api_key": "old", "sidebar": "everyone"},
                source="user",
                version=2,
                minor_version=1,
                options={},
                unique_id=None,
                discovery_keys={},
                subentries_data=[
                    {
                        "subentry_type": "conversation",
                        "title": "Voice",
                        "unique_id": None,
                        "data": {"prompt": "Keep this", "functions": "Keep these"},
                    }
                ],
            )
            hass.config_entries._entries[entry.entry_id] = entry
            original_subentries = dict(entry.subentries)
            reload = AsyncMock(return_value=True)
            monkeypatch.setattr(hass.config_entries, "async_reload", reload)
            if loaded:
                entry.add_update_listener(async_update_options)
            flow = LumoConfigFlow()
            flow.hass, flow.context = hass, {"source": source}
            flow._get_reconfigure_entry = lambda: entry
            flow._get_reauth_entry = lambda: entry
            monkeypatch.setattr(flow, "_async_probe", AsyncMock(return_value={}))
            data = {"api_key": "", "sidebar": "everyone", "skip_authentication": False}
            step = flow.async_step_reconfigure if source == "reconfigure" else flow.async_step_reauth_confirm
            result = await step(data)
            await hass.async_block_till_done()
            assert result["type"] == "abort"
            assert result["reason"] == f"{source}_successful"
            assert entry.data["api_key"] == ""
            assert entry.data["sidebar"] == "everyone"
            assert dict(entry.subentries) == original_subentries
            reload.assert_awaited_once_with(entry.entry_id)
        finally:
            await hass.async_stop(force=True)

    asyncio.run(main())


@pytest.mark.parametrize("authenticated", [False, "connection_error"])
def test_setup_rejects_silent_anonymous_fallback_and_retries_connection_errors(monkeypatch, authenticated):
    import custom_components.lumo as integration

    async def main():
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            monkeypatch.setattr(integration, "async_create_client", AsyncMock(return_value=object()))
            probe = AsyncMock(return_value=False)
            expected = ConfigEntryAuthFailed
            if authenticated == "connection_error":
                import httpx

                probe.side_effect = openai.APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
                expected = ConfigEntryNotReady
            monkeypatch.setattr(integration, "async_validate_connection", probe)
            with pytest.raises(expected):
                await async_setup_entry(hass, SimpleNamespace(data={"api_key": "bad"}))
        finally:
            await hass.async_stop(force=True)

    asyncio.run(main())
