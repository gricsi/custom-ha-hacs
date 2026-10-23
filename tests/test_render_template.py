"""Tests for the render_template native function.

These run against a real HomeAssistant instance rather than a FakeHass: rendering goes
through Template, its sandboxed environment and the timeout thread, none of which can be
faked without testing the fake. A bare HomeAssistant needs no config entry or integration
for states() to work, and it must be built inside the running loop, hence run().
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

import custom_components.lumo.helpers as helpers
from custom_components.lumo.helpers import MAX_TEMPLATE_RESULT_CHARS, NativeFunctionExecutor


def run(body: Callable[[HomeAssistant], Awaitable[Any]]) -> Any:
    """Run body against a fresh HomeAssistant, stopping it afterwards."""

    async def main() -> Any:
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            return await body(hass)
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(main())


def render(template: Any, states: dict[str, tuple[str, dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Set the given states, then call render_template the way the tool layer does."""

    async def body(hass: HomeAssistant) -> dict[str, Any]:
        for entity_id, (state, attributes) in (states or {}).items():
            hass.states.async_set(entity_id, state, attributes)
        return await NativeFunctionExecutor().render_template(hass, {}, {"template": template}, None, [])

    return run(body)


def test_renders_states_and_attributes():
    # The 2026-10-20 heating debug in miniature: read two helpers and compute the curve.
    result = render(
        "{% set lo = states('input_number.lo') | float %}{% set hi = states('input_number.hi') | float %}"
        "{{ (lo + (hi - lo) / 23 * 7.66) | round(1) }} max={{ state_attr('input_number.hi', 'max') }}",
        {"input_number.lo": ("22.0", {}), "input_number.hi": ("35.0", {"max": 35})},
    )

    assert result == {"ok": True, "result": "26.3 max=35", "truncated": False}


def test_reads_entities_regardless_of_exposure():
    # No exposed_entities list is consulted: debugging needs helpers nobody exposed to Assist.
    result = render("{{ states('input_number.hidden') }}", {"input_number.hidden": ("7", {})})

    assert result["result"] == "7"


def test_result_is_a_string_not_a_parsed_value():
    # parse_result=False, so "22.0" stays "22.0" rather than becoming a float the model sees as 22.
    assert render("{{ '22.0' }}")["result"] == "22.0"


def test_syntax_error_is_returned_not_raised():
    result = render("{{ states('x' }}")

    assert result["ok"] is False
    assert "TemplateSyntaxError" in result["error"]


def test_undefined_name_is_returned_not_raised():
    result = render("{{ no_such_function() }}")

    assert result["ok"] is False
    assert "no_such_function" in result["error"]


@pytest.mark.parametrize("template", ["", "   ", None, 42])
def test_empty_or_non_string_template_is_rejected(template):
    assert render(template) == {"ok": False, "error": "template is empty"}


def test_slow_template_is_refused(monkeypatch):
    # Bounded by the same will-timeout check the Developer tools page uses. The real ceiling
    # is seconds; shrink it so the test does not wait that long.
    monkeypatch.setattr(helpers, "TEMPLATE_RENDER_TIMEOUT", 0.05)

    result = render("{% for i in range(100000) %}{% for j in range(100000) %}{% endfor %}{% endfor %}")

    assert result["ok"] is False
    assert "longer than" in result["error"]


def test_long_result_is_clipped_and_flagged():
    result = render(f"{{{{ 'x' * {MAX_TEMPLATE_RESULT_CHARS + 500} }}}}")

    assert result["ok"] is True
    assert result["truncated"] is True
    assert result["result"].startswith("x" * MAX_TEMPLATE_RESULT_CHARS)
    assert "[... 500 more characters]" in result["result"]


def test_dispatched_by_name():
    async def body(hass: HomeAssistant) -> dict[str, Any]:
        return await NativeFunctionExecutor().execute(
            hass, {"name": "render_template"}, {"template": "{{ 1 + 1 }}"}, None, []
        )

    assert run(body)["result"] == "2"
