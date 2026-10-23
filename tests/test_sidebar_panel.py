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

from custom_components.lumo.history import CARD_URL, PANEL_URL_PATH, async_set_panel_visibility


def panels_after(*changes: str | tuple[str, str | None]) -> dict[str, Any]:
    """Apply sidebar settings in order: a bare visibility is entry "e", a tuple names the entry."""
    async def main() -> dict[str, Any]:
        hass = HomeAssistant(tempfile.mkdtemp())
        hass.data[DATA_PANELS] = {}
        try:
            for change in changes:
                entry_id, visibility = change if isinstance(change, tuple) else ("e", change)
                await async_set_panel_visibility(hass, entry_id, visibility)
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


def test_two_entries_get_the_wider_setting_and_unloading_one_keeps_the_other() -> None:
    """There is one /lumo panel; neither entry may hide or remove it for the other."""
    assert panels_after(("a", "everyone"), ("b", "hidden"))[PANEL_URL_PATH]["require_admin"] is False
    assert panels_after(("a", "admin"), ("b", "everyone"), ("b", None))[PANEL_URL_PATH]["require_admin"] is True
    assert panels_after(("a", "admin"), ("a", None)) == {}


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


def test_the_panel_is_sized_to_the_window() -> None:
    """height: 100% resolved to nothing inside Home Assistant's panel container, so the chat
    sat at the top of an empty page. jsdom does no layout, so this checks the rule itself."""
    from custom_components.lumo.history import CARD_PATH

    assert "height: 100dvh" in CARD_PATH.read_text()


def test_the_panel_and_dashboards_load_the_card_from_one_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two URLs mean the browser runs the file twice, and the second customElements.define
    throws "lumo-chat-card has already been used with this registry". The version used to be
    the file's mtime read at each call, so a file replaced while Home Assistant ran -- an
    update without a restart, then a reload -- gave the panel a different URL than dashboards."""
    from types import SimpleNamespace

    import custom_components.lumo.history as history

    served: list[str] = []

    async def register(configs: list[Any]) -> None:
        served.extend(c.url_path for c in configs)

    async def main() -> tuple[set[str], str]:
        hass = HomeAssistant(tempfile.mkdtemp())
        hass.data[DATA_PANELS] = {}
        hass.data["frontend_extra_module_url"] = set()
        hass.http = SimpleNamespace(async_register_static_paths=register)
        try:
            await history.async_register_card(hass)
            real_stat = history.CARD_PATH.stat
            monkeypatch.setattr(
                type(history.CARD_PATH),
                "stat",
                lambda self: SimpleNamespace(st_mtime_ns=real_stat().st_mtime_ns + 1),
            )
            await async_set_panel_visibility(hass, "e", "admin")
            panel = hass.data[DATA_PANELS][PANEL_URL_PATH].to_response()
            return hass.data["frontend_extra_module_url"], panel["config"]["_panel_custom"]["module_url"]
        finally:
            await hass.async_stop(force=True)

    dashboard_urls, panel_url = asyncio.run(main())
    assert dashboard_urls == {panel_url}


def test_loading_the_card_twice_does_not_redefine_its_elements() -> None:
    """A manually added dashboard resource also loads it a second time. jsdom is not part of
    this suite, so this checks the guards themselves."""
    from custom_components.lumo.history import CARD_PATH

    source = CARD_PATH.read_text()
    for element in ("lumo-chat-card", "lumo-chat-panel"):
        assert f'if (!customElements.get("{element}"))' in source
