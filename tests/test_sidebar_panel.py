"""Tests for the Lumo sidebar panel.

The chat card, full page, behind a sidebar entry -- so nobody has to edit a dashboard to
see their saved conversation. Visibility is set on the Lumo entry: hidden, admins only, or
everyone. Reconfiguring reloads the entry, so registering must replace, not collide.
"""

from __future__ import annotations

import asyncio
import tempfile
from typing import Any

import pytest

from homeassistant.components.frontend import DATA_PANELS
from homeassistant.core import HomeAssistant

from custom_components.lumo.history import CARD_URL, PANEL_URL_PATH, async_update_panel


def panels_after(*visibilities: str) -> dict[str, Any]:
    async def main() -> dict[str, Any]:
        hass = HomeAssistant(tempfile.mkdtemp())
        hass.data[DATA_PANELS] = {}
        try:
            for visibility in visibilities:
                await async_update_panel(hass, visibility)
            return {path: panel.to_response() for path, panel in hass.data[DATA_PANELS].items()}
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(main())


@pytest.mark.parametrize(("visibility", "admin_only"), [("admin", True), ("everyone", False)])
def test_the_panel_is_registered_with_its_visibility(visibility: str, admin_only: bool) -> None:
    panel = panels_after(visibility)[PANEL_URL_PATH]
    assert panel["require_admin"] is admin_only
    assert panel["title"] == "Lumo"
    custom = panel["config"]["_panel_custom"]
    assert custom["name"] == "lumo-chat-panel"
    assert custom["module_url"].startswith(CARD_URL)


def test_hidden_registers_nothing() -> None:
    assert panels_after("hidden") == {}


def test_changing_visibility_replaces_the_panel() -> None:
    """A reload registers again; panel_custom refuses to overwrite, so the old one must go first."""
    assert panels_after("admin", "everyone")[PANEL_URL_PATH]["require_admin"] is False
    assert panels_after("everyone", "hidden") == {}


def test_the_card_file_defines_the_panel_element() -> None:
    from custom_components.lumo.history import CARD_PATH

    assert 'customElements.define("lumo-chat-panel"' in CARD_PATH.read_text()


def test_the_entry_form_offers_the_sidebar_setting_defaulting_to_admins() -> None:
    from custom_components.lumo.config_flow import STEP_USER_DATA_SCHEMA
    from custom_components.lumo.const import CONF_SIDEBAR

    validated = STEP_USER_DATA_SCHEMA({})
    assert validated[CONF_SIDEBAR] == "admin"
    for visibility in ("hidden", "admin", "everyone"):
        assert STEP_USER_DATA_SCHEMA({CONF_SIDEBAR: visibility})[CONF_SIDEBAR] == visibility
