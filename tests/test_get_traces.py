"""Tests for the get_traces native function.

The traces here are the real AutomationTrace and ScriptTrace classes, filled with real
TraceElements, in the real TraceBuckets container -- not dicts shaped like their output.
get_traces reads hass.data["trace"] and trusts as_short_dict()/as_extended_dict(), none of
which is public API, so faking them would let these tests pass while Home Assistant
reshapes the store underneath. A real HomeAssistant supplies hass.states.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections import deque
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.components.automation.trace import AutomationTrace
from homeassistant.components.script.trace import ScriptTrace
from homeassistant.components.trace.models import TraceBuckets
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.helpers.trace import TraceElement, variables_cv
from homeassistant.util.limited_size_dict import LimitedSizeDict

from custom_components.lumo.exceptions import EntityNotFound, InvalidTraceTarget, TraceUnavailable
from custom_components.lumo.helpers import (
    DEFAULT_TRACE_RUNS,
    MAX_TRACE_RESULT_CHARS,
    MAX_TRACE_VALUE_CHARS,
    NativeFunctionExecutor,
)

PUMP_CONFIG = {
    "id": "1700000000001",
    "alias": "Padló szivattyú start",
    "triggers": [{"trigger": "state", "entity_id": ["switch.padlo_szelepek"], "to": "on"}],
    "actions": [{"action": "switch.turn_on", "target": {"entity_id": "switch.r1_1_padlo_szivattyu"}}],
}


def automation_trace(
    item_id: str = "1700000000001",
    trigger: str = "state of switch.padlo_szelepek",
    steps: dict[str, list[dict[str, Any]]] | None = None,
    error: Exception | None = None,
    not_triggered: bool = False,
    started_minutes_ago: int = 0,
    config: dict[str, Any] | None = None,
) -> AutomationTrace:
    """A finished automation trace. Each step is {"variables": ..., "result": ...}."""
    variables_cv.set(None)
    trace = AutomationTrace(item_id, config or PUMP_CONFIG, None, Context(), not_triggered=not_triggered)
    trace.set_trigger_description(trigger)
    trace._timestamp_start -= timedelta(minutes=started_minutes_ago)
    elements: dict[str, deque[TraceElement]] = {}
    for path, items in (steps or {"action/0": [{"result": {"params": {"service": "turn_on"}}}]}).items():
        elements[path] = deque()
        for item in items:
            element = TraceElement(item.get("variables"), path)
            if "result" in item:
                element.set_result(**item["result"])
            if "error" in item:
                element.set_error(item["error"])
            elements[path].append(element)
    trace.set_trace(elements)
    if error is not None:
        trace.set_error(error)
    trace.finished()
    return trace


def store_of(*traces: Any, legacy: bool = False) -> dict[str, Any]:
    """Arrange traces into hass.data["trace"]: runs and not-triggered kept apart, as core does."""
    store: dict[str, Any] = {}
    for trace in traces:
        if legacy:
            store.setdefault(trace.key, LimitedSizeDict(size_limit=5))[trace.run_id] = trace
            continue
        if trace.key not in store:
            store[trace.key] = TraceBuckets(
                runs=LimitedSizeDict(size_limit=5), not_triggered=LimitedSizeDict(size_limit=5)
            )
        store[trace.key].bucket(trace.not_triggered)[trace.run_id] = trace
    return store


def run(
    store: dict[str, Any] | None,
    arguments: dict[str, Any],
    states: dict[str, tuple[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Call get_traces the way the tool layer does, against a fresh HomeAssistant."""

    async def main() -> dict[str, Any]:
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            if store is not None:
                hass.data["trace"] = store
                # Saved traces were "already restored", so async_restore_traces is a no-op.
                hass.data["trace_traces_restored"] = True
            for entity_id, (state, attributes) in (states or {}).items():
                hass.states.async_set(entity_id, state, attributes)
            return await NativeFunctionExecutor().get_traces(hass, {}, arguments, None, [])
        finally:
            await hass.async_stop(force=True)

    return asyncio.run(main())


PUMP_STATE = {
    "automation.padlo_szivattyu_start": ("on", {"id": "1700000000001", "friendly_name": "Padló szivattyú start"})
}


# --------------------------------------------------------------------------- listing


def test_no_trace_store_raises():
    with pytest.raises(TraceUnavailable, match="default_config"):
        run(None, {})


def test_lists_recent_runs_across_everything_newest_first():
    older = automation_trace(started_minutes_ago=10)
    newer = automation_trace(item_id="1700000000002", trigger="time pattern", started_minutes_ago=1)

    result = run(store_of(older, newer), {})

    assert result["available"] == 2
    assert [r["run_id"] for r in result["runs"]] == [newer.run_id, older.run_id]
    assert result["runs"][0]["entity"] == "automation.1700000000002"
    assert result["runs"][0]["trigger"] == "time pattern"
    assert result["runs"][1]["state"] == "stopped"
    assert result["runs"][1]["last_step"] == "action/0"


def test_lists_one_automation_by_entity_id_through_its_id_attribute():
    # Traces are keyed by the config id, not the entity id; the id is a state attribute.
    mine = automation_trace()
    other = automation_trace(item_id="999")

    result = run(store_of(mine, other), {"entity_id": "automation.padlo_szivattyu_start"}, PUMP_STATE)

    assert result["entity_id"] == "automation.padlo_szivattyu_start"
    assert [r["run_id"] for r in result["runs"]] == [mine.run_id]
    assert "reason" not in result


def test_lists_a_script_by_object_id():
    variables_cv.set(None)
    trace = ScriptTrace("furdo_reggel", {"sequence": []}, None, Context())
    trace.set_trace({"sequence/0": deque([TraceElement(None, "sequence/0")])})
    trace.finished()

    result = run(store_of(trace), {"entity_id": "script.furdo_reggel"}, {"script.furdo_reggel": ("off", {})})

    assert [r["run_id"] for r in result["runs"]] == [trace.run_id]
    assert "trigger" not in result["runs"][0]


def test_limit_is_clamped_and_tolerates_strings():
    traces = [automation_trace(started_minutes_ago=i) for i in range(8)]

    assert len(run(store_of(*traces), {})["runs"]) == DEFAULT_TRACE_RUNS
    assert len(run(store_of(*traces), {"limit": "2"})["runs"]) == 2
    assert len(run(store_of(*traces), {"limit": 0})["runs"]) == 1
    assert run(store_of(*traces), {"limit": 500})["returned"] == 5  # one bucket holds at most 5


def test_error_and_not_triggered_are_flagged():
    failed = automation_trace(error=ValueError("entity switch.x not found"), started_minutes_ago=2)
    skipped = automation_trace(not_triggered=True, started_minutes_ago=1)

    runs = {r["run_id"]: r for r in run(store_of(failed, skipped), {})["runs"]}

    assert runs[failed.run_id]["error"] == "entity switch.x not found"
    assert runs[skipped.run_id]["not_triggered"] is True
    assert "not_triggered" not in runs[failed.run_id]


def test_automation_without_traces_explains_why():
    result = run(store_of(), {"entity_id": "automation.padlo_szivattyu_start"}, PUMP_STATE)

    assert result["runs"] == []
    assert "id:" in result["reason"]


def test_reads_the_pre_2026_9_store_shape():
    # Before TraceBuckets an entry was one LimitedSizeDict of runs; manifest.json allows 2026.8.
    trace = automation_trace()

    result = run(store_of(trace, legacy=True), {"entity_id": "automation.padlo_szivattyu_start"}, PUMP_STATE)

    assert [r["run_id"] for r in result["runs"]] == [trace.run_id]


@pytest.mark.parametrize("entity_id", ["light.kitchen", "automation.", "nonsense"])
def test_non_traceable_entity_is_rejected(entity_id):
    with pytest.raises(InvalidTraceTarget):
        run(store_of(), {"entity_id": entity_id})


def test_unknown_automation_raises():
    with pytest.raises(EntityNotFound):
        run(store_of(), {"entity_id": "automation.does_not_exist"})


# --------------------------------------------------------------------------- one run


def test_detail_shows_steps_results_and_config():
    trace = automation_trace(
        steps={
            "trigger/0": [{"variables": {"trigger": {"platform": "state", "entity_id": "switch.padlo_szelepek"}}}],
            "action/0": [{"result": {"params": {"domain": "switch", "service": "turn_on"}, "running_script": False}}],
        }
    )

    result = run(store_of(trace), {"entity_id": "automation.padlo_szivattyu_start", "run_id": trace.run_id}, PUMP_STATE)

    assert result["run_id"] == trace.run_id
    assert result["trigger"] == "state of switch.padlo_szelepek"
    assert [step["path"] for step in result["steps"]] == ["trigger/0", "action/0"]
    assert result["steps"][0]["changed_variables"]["trigger"]["entity_id"] == "switch.padlo_szelepek"
    assert result["steps"][1]["result"]["params"]["service"] == "turn_on"
    assert result["config"]["alias"] == "Padló szivattyú start"
    assert result["truncated"] is False
    assert "error" not in result


def test_latest_means_the_newest_run():
    older = automation_trace(started_minutes_ago=5)
    newer = automation_trace(started_minutes_ago=1)

    result = run(
        store_of(older, newer), {"entity_id": "automation.padlo_szivattyu_start", "run_id": "latest"}, PUMP_STATE
    )

    assert result["run_id"] == newer.run_id


def test_state_objects_in_variables_become_plain_and_are_clipped():
    # A state trigger's variables carry whole State objects. bare orjson -- which serialises the
    # tool result later -- cannot encode those, so they must arrive here as plain data.
    big = State("climate.em_furdo_termosztat", "heat", {"note": "x" * (MAX_TRACE_VALUE_CHARS * 2)})
    small = State("switch.padlo_szelepek", "on")
    trace = automation_trace(steps={"trigger/0": [{"variables": {"small": small, "big": big}}]})

    result = run(store_of(trace), {"entity_id": "automation.padlo_szivattyu_start", "run_id": trace.run_id}, PUMP_STATE)

    variables = result["steps"][0]["changed_variables"]
    assert variables["small"]["state"] == "on"
    assert isinstance(variables["big"], str)
    assert "more characters]" in variables["big"]


def test_step_error_is_shown():
    trace = automation_trace(steps={"action/0": [{"error": ValueError("Entity is not available")}]})

    result = run(store_of(trace), {"entity_id": "automation.padlo_szivattyu_start", "run_id": trace.run_id}, PUMP_STATE)

    assert result["steps"][0]["error"] == "Entity is not available"


def test_huge_run_is_cut_at_the_budget_but_keeps_a_step():
    steps = {f"action/{i}": [{"result": {"blob": "y" * (MAX_TRACE_VALUE_CHARS - 100)}}] for i in range(60)}
    trace = automation_trace(steps=steps)

    result = run(store_of(trace), {"entity_id": "automation.padlo_szivattyu_start", "run_id": trace.run_id}, PUMP_STATE)

    assert result["truncated"] is True
    assert 0 < len(result["steps"]) < 60
    steps_size = sum(len(str(step)) for step in result["steps"])
    assert steps_size < MAX_TRACE_RESULT_CHARS * 1.2


def test_unknown_run_id_is_reported_not_raised():
    result = run(
        store_of(automation_trace()), {"entity_id": "automation.padlo_szivattyu_start", "run_id": "nope"}, PUMP_STATE
    )

    assert "no run 'nope'" in result["error"]


def test_dispatched_by_name():
    async def main() -> dict[str, Any]:
        hass = HomeAssistant(tempfile.mkdtemp())
        try:
            hass.data["trace"] = store_of(automation_trace())
            hass.data["trace_traces_restored"] = True
            return await NativeFunctionExecutor().execute(hass, {"name": "get_traces"}, {}, None, [])
        finally:
            await hass.async_stop(force=True)

    assert asyncio.run(main())["available"] == 1
