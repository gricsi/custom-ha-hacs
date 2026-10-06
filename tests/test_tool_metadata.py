"""Tests for the LLM tool metadata on CustomFunctionTool.

Core grew three bits of tool metadata after 2026.9 -- `integration`, `title` and
`annotations` -- and this integration's floor is 2026.8.0, so the code has to set them
without requiring them. That split shapes the suite:

- `integration` and `title` are plain attributes. They are set on every core, so those
  tests assert unconditionally.
- `annotations` needs `llm.ToolAnnotations`, which does not exist on the floor. Most
  tests here stub it, because a stub is the only way to exercise the parsing on a core
  that predates the class, and test_annotations_are_left_unset_on_a_core_without_them
  covers the other branch by taking the class away.

test_declared_annotation_keys_exist_on_the_real_core is different in kind: it is the
tripwire for core renaming or dropping a field, and it is the one test here that the
stub cannot stand in for. It skips on a core without ToolAnnotations, which includes
the version this suite currently pins -- so it is inert today and starts running the
moment Home Assistant is upgraded past it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields as dataclass_fields

import pytest

from homeassistant.helpers import llm

from custom_components.lumo.const import DEFAULT_CONF_FUNCTIONS, DOMAIN
from custom_components.lumo.conversation import CustomFunctionTool, build_tool_annotations


@dataclass(frozen=True, slots=True, kw_only=True)
class StubToolAnnotations:
    """Stand-in for llm.ToolAnnotations, with core's own defaults.

    Core documents those defaults as "the least safe case", so a tool that declares
    nothing is taken to write, to be destructive, and to reach outside Home Assistant.
    The stub repeats them rather than importing them, so that a test asserting on a
    default is asserting on an expectation this repo states, not on whatever core
    happens to ship.
    """

    read_only: bool = False
    destructive: bool = True
    idempotent: bool = False
    open_world: bool = True


@pytest.fixture
def tool_annotations(monkeypatch: pytest.MonkeyPatch) -> type[StubToolAnnotations]:
    """Give the code under test a ToolAnnotations class on any core."""
    monkeypatch.setattr(llm, "ToolAnnotations", StubToolAnnotations, raising=False)
    return StubToolAnnotations


def make_tool(spec_extra: dict | None = None) -> CustomFunctionTool:
    """Build a tool from a minimal spec, plus whatever the test is exercising."""
    spec: dict = {"name": "read_thing", "description": "Read a thing."}
    spec.update(spec_extra or {})
    return CustomFunctionTool(function_spec=spec, function_impl={"type": "native", "name": "read_thing"})


def test_tool_records_the_integration_that_provides_it() -> None:
    """Core stops accepting untagged tools in 2027.10, and will not tag these for us.

    The fallback tagger runs in APIInstance.__post_init__, and the entity appends these
    tools to llm_api.tools after that, so an untagged one would never be caught.
    """
    assert make_tool().integration == DOMAIN


def test_title_is_taken_from_the_spec() -> None:
    """A spec may give a human-readable label alongside the model-facing name."""
    assert make_tool({"title": "Read a thing"}).title == "Read a thing"


def test_title_is_none_when_the_spec_omits_it() -> None:
    """Title is optional; core's own default is None."""
    assert make_tool().title is None


def test_non_string_title_is_dropped_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A YAML author who writes `title: 12` gets None and a complaint, not a crash."""
    with caplog.at_level(logging.WARNING):
        tool = make_tool({"title": 12})

    assert tool.title is None
    assert "expected a string" in caplog.text


def test_annotations_are_left_unset_on_a_core_without_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """On the 2026.8 floor there is no ToolAnnotations, and nothing may be set.

    Checked against the instance dict rather than hasattr, because on a newer core the
    class attribute exists and would mask the difference.
    """
    monkeypatch.delattr(llm, "ToolAnnotations", raising=False)

    tool = make_tool({"annotations": {"read_only": True}})

    assert "annotations" not in tool.__dict__


def test_annotations_default_to_cores_least_safe_case(tool_annotations: type[StubToolAnnotations]) -> None:
    """A function that declares nothing must not be presented as safer than it is."""
    assert make_tool().annotations == tool_annotations()


def test_declared_annotations_are_applied(tool_annotations: type[StubToolAnnotations]) -> None:
    """The whole point: a read-only function can say so."""
    tool = make_tool(
        {
            "annotations": {
                "read_only": True,
                "destructive": False,
                "idempotent": True,
                "open_world": False,
            }
        }
    )

    assert tool.annotations == tool_annotations(
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )


def test_unknown_annotation_is_dropped_with_a_warning(
    tool_annotations: type[StubToolAnnotations],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unrecognised key must not reach the dataclass and must not pass silently."""
    with caplog.at_level(logging.WARNING):
        tool = make_tool({"annotations": {"readonly": True}})

    assert tool.annotations == tool_annotations()
    assert "unknown annotation" in caplog.text


def test_non_boolean_annotation_is_dropped_with_a_warning(
    tool_annotations: type[StubToolAnnotations],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`read_only: "yes"` is a string, and a truthy string must not claim read-only."""
    with caplog.at_level(logging.WARNING):
        tool = make_tool({"annotations": {"read_only": "yes"}})

    assert tool.annotations.read_only is False
    assert "expected true or false" in caplog.text


def test_non_mapping_annotations_fall_back_to_defaults(
    tool_annotations: type[StubToolAnnotations],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A list where a mapping belongs falls back rather than raising mid-setup."""
    with caplog.at_level(logging.WARNING):
        tool = make_tool({"annotations": ["read_only"]})

    assert tool.annotations == tool_annotations()
    assert "expected a mapping" in caplog.text


def test_builder_returns_none_without_the_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """The builder signals "this core cannot express annotations" with None."""
    monkeypatch.delattr(llm, "ToolAnnotations", raising=False)

    assert build_tool_annotations("read_thing", {"read_only": True}) is None


def test_default_function_declares_itself_inside_home_assistant(
    tool_annotations: type[StubToolAnnotations],
) -> None:
    """execute_services calls arbitrary services, but never leaves Home Assistant.

    The other three fields match core's pessimistic defaults and are correct for it, so
    open_world is the one that has to be stated to be right.
    """
    spec = DEFAULT_CONF_FUNCTIONS[0]["spec"]
    tool = CustomFunctionTool(function_spec=spec, function_impl=DEFAULT_CONF_FUNCTIONS[0]["function"])

    assert tool.annotations == tool_annotations(
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )


@pytest.mark.skipif(
    not hasattr(llm, "ToolAnnotations"),
    reason="llm.ToolAnnotations arrived after 2026.9; nothing to check against",
)
def test_declared_annotation_keys_exist_on_the_real_core() -> None:
    """Tripwire for core renaming or dropping an annotation field.

    build_tool_annotations drops unknown keys with a warning rather than raising, so a
    renamed field would not break a conversation -- the built-in function would just
    quietly go back to being described as reaching outside Home Assistant. This is what
    turns that into a failing test instead.
    """
    declared = set(DEFAULT_CONF_FUNCTIONS[0]["spec"]["annotations"])
    real = {field.name for field in dataclass_fields(llm.ToolAnnotations)}

    assert declared <= real, f"core no longer has {sorted(declared - real)}"


@pytest.mark.skipif(
    not hasattr(llm, "ToolAnnotations"),
    reason="llm.ToolAnnotations arrived after 2026.9; nothing to check against",
)
def test_stub_still_matches_the_real_defaults() -> None:
    """Keep StubToolAnnotations honest.

    Every other annotation test here asserts against the stub, so if core changed a
    default and the stub did not follow, this file would keep passing while describing
    behaviour the integration no longer has.
    """
    stub = {field.name: field.default for field in dataclass_fields(StubToolAnnotations)}
    real = {field.name: field.default for field in dataclass_fields(llm.ToolAnnotations)}

    assert stub == real
