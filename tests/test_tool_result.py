"""Tests for the 2026.10 ToolResult migration.

2026.10 replaced ToolResultContent.tool_result with `.result` (an llm.ToolResult) and wants
tools to return a ToolResult instead of bare JSON. The old forms only warn until 2027.11, and
this integration's floor predates ToolResult, so both directions are read off whatever core is
installed. The suite pins 2026.9, so the 2026.10 side is stubbed with the same field names as
core's dataclass (data, error: bool).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.helpers import llm

from custom_components.lumo.conversation import CustomFunctionTool, as_tool_result
from custom_components.lumo.entity import tool_result_data


@dataclass
class StubToolResult:
    data: Any
    error: bool = False


def test_reads_result_data_on_a_new_core() -> None:
    content = SimpleNamespace(result=StubToolResult(data={"state": "on"}))
    assert tool_result_data(content) == {"state": "on"}


def test_falls_back_to_tool_result_on_an_old_core() -> None:
    content = SimpleNamespace(tool_result={"state": "off"})
    assert tool_result_data(content) == {"state": "off"}


def test_wraps_in_tool_result_when_core_has_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "ToolResult", StubToolResult, raising=False)
    assert as_tool_result({"ok": 1}) == StubToolResult(data={"ok": 1})
    assert as_tool_result({"error": "boom"}, error=True) == StubToolResult(data={"error": "boom"}, error=True)


def test_returns_bare_json_on_an_old_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(llm, "ToolResult", raising=False)
    assert as_tool_result({"ok": 1}) == {"ok": 1}


def test_failing_function_reports_an_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "ToolResult", StubToolResult, raising=False)
    tool = CustomFunctionTool({"name": "broken"}, {"type": "no_such_executor"})
    context = SimpleNamespace(language="en", context=None, device_id=None, assistant=None)
    tool_input = llm.ToolInput(id="t1", tool_name="broken", tool_args={})

    result = asyncio.run(tool.async_call(SimpleNamespace(), tool_input, context))

    assert isinstance(result, StubToolResult)
    assert result.error is True
    assert "error" in result.data
