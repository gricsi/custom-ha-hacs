"""Tests for the read_logs native function.

Two kinds of test here, deliberately:

- Most build fake system_log records, because that is cheap and lets a test pin an
  exact filter or limit.
- test_reads_real_system_log_records drives the *real* LogErrorHandler with real
  logging.LogRecords instead. read_logs reaches into hass.data["system_log"].records
  and trusts the shape of LogEntry.to_dict(), neither of which is a public API, so
  that test is the one that fails if Home Assistant reshapes the store.

The functions under test are async, and asyncio.run() is enough for them -- nothing
here touches a running event loop -- so this suite needs no async pytest plugin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import types
from typing import Any

import pytest

import homeassistant.util.dt as dt_util
from homeassistant import __path__ as HOMEASSISTANT_PATH
from homeassistant.components import system_log

import custom_components.lumo.helpers as helpers
from custom_components.lumo.exceptions import NativeNotFound, SystemLogUnavailable
from custom_components.lumo.helpers import (
    DEFAULT_LOG_ENTRIES,
    DEFAULT_RAW_LOG_LINES,
    MAX_LOG_ENTRIES,
    MAX_LOG_EXCEPTION_CHARS,
    MAX_LOG_MESSAGE_CHARS,
    MAX_LOG_RESULT_CHARS,
    MAX_RAW_LOG_CHARS,
    MAX_RAW_LOG_LINES,
    MAX_RAW_LOG_TAIL_BYTES,
    NativeFunctionExecutor,
)

TIMESTAMP = 1757923200.0  # 2025-09-15T08:00:00Z


class FakeHass:
    """The three things read_logs asks of hass, and nothing else."""

    def __init__(self, data: dict[str, Any] | None = None, config_dir: str = "/config") -> None:
        self.data = data if data is not None else {}
        self.config = types.SimpleNamespace(path=lambda *parts: os.path.join(config_dir, *parts))

    async def async_add_executor_job(self, target, *args):
        """Run inline. Real hass hands this to a thread pool; the result is the same."""
        return target(*args)


def fake_store(records: list[dict[str, Any]]) -> types.SimpleNamespace:
    """Stand in for hass.data["system_log"], down to the records.to_list() call."""
    return types.SimpleNamespace(records=types.SimpleNamespace(to_list=lambda: list(records)))


def record(
    level: str = "ERROR",
    name: str = "homeassistant.components.mqtt",
    message: Any = "Error talking to the broker",
    exception: str = "",
    count: int = 1,
    timestamp: float = TIMESTAMP,
    source: tuple[str, int] = ("custom_components/foo/sensor.py", 42),
) -> dict[str, Any]:
    """One record in the shape LogEntry.to_dict() produces."""
    return {
        "name": name,
        "message": message if isinstance(message, list) else [message],
        "level": level,
        "source": source,
        "timestamp": timestamp,
        "exception": exception,
        "count": count,
        "first_occurred": timestamp - 60,
    }


def read(hass: FakeHass, **arguments) -> dict[str, Any]:
    """Call read_logs the way the tool layer does."""
    return asyncio.run(NativeFunctionExecutor().read_logs(hass, {}, arguments, None, []))


def write_log(
    path,
    lines: int,
    prefix: str = "2026-09-15 10:00:00 ERROR (MainThread) [pkg.mod]",
    pad: int = 0,
) -> str:
    """Write a log file whose every line is identifiable by its number."""
    with open(path, "w", encoding="utf-8") as f:
        for i in range(lines):
            f.write(f"{prefix} event number {i} padding-padding-padding{'x' * pad}\n")
    return str(path)


# --------------------------------------------------------------------------- errors


def test_returns_every_record_by_default():
    hass = FakeHass({"system_log": fake_store([record(), record(level="WARNING"), record(level="INFO")])})

    result = read(hass)

    assert result["source"] == "errors"
    assert result["returned"] == result["matched"] == result["available"] == 3
    assert result["truncated"] is False
    # level_counts covers the whole store, so the agent can summarise what it did not read.
    assert result["level_counts"] == {"ERROR": 1, "WARNING": 1, "INFO": 1}


@pytest.fixture
def budapest_time():
    """Run a test in a non-UTC zone, the way a real install almost always is."""
    original = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Budapest"))
    yield
    dt_util.set_default_time_zone(original)


def test_timestamps_are_reported_in_local_time(budapest_time):
    """The Logs page shows local time, so an entry must not read back as UTC."""
    hass = FakeHass({"system_log": fake_store([record()])})

    [entry] = read(hass)["entries"]

    assert entry["last_occurred"] == "2025-09-15T10:00:00+02:00"
    assert entry["first_occurred"] == "2025-09-15T09:59:00+02:00"


def test_an_empty_store_is_an_answer_not_an_error():
    """The common case on a healthy install, and the one the agent must report plainly."""
    hass = FakeHass({"system_log": fake_store([])})

    result = read(hass)

    assert result["entries"] == []
    assert result["returned"] == result["matched"] == result["available"] == 0
    assert result["truncated"] is False
    assert result["level_counts"] == {}


def test_flattens_a_record_into_readable_fields():
    hass = FakeHass(
        {
            "system_log": fake_store(
                [
                    record(
                        message=["first message", "second message"],
                        exception="Traceback (most recent call last):\n  ConnectionRefusedError",
                        count=7,
                    )
                ]
            )
        }
    )

    [entry] = read(hass)["entries"]

    assert entry["level"] == "ERROR"
    assert entry["logger"] == "homeassistant.components.mqtt"
    assert entry["message"] == "first message\nsecond message"
    assert entry["source"] == "custom_components/foo/sensor.py:42"
    assert entry["count"] == 7
    assert entry["exception"].endswith("ConnectionRefusedError")
    # Epoch floats become local ISO strings; assert the instant, not the offset,
    # so the test does not depend on the timezone it runs in.
    assert dt_util.parse_datetime(entry["last_occurred"]).timestamp() == TIMESTAMP
    assert dt_util.parse_datetime(entry["first_occurred"]).timestamp() == TIMESTAMP - 60


def test_omits_the_exception_key_when_there_is_no_traceback():
    hass = FakeHass({"system_log": fake_store([record(exception="")])})

    assert "exception" not in read(hass)["entries"][0]


@pytest.mark.parametrize("level", ["ERROR", "error", "Error"])
def test_level_filters_by_severity_whatever_the_casing(level):
    records = [record(level="WARNING"), record(level="ERROR"), record(level="CRITICAL"), record(level="INFO")]
    hass = FakeHass({"system_log": fake_store(records)})

    result = read(hass, level=level)

    assert [entry["level"] for entry in result["entries"]] == ["ERROR", "CRITICAL"]
    assert result["matched"] == 2
    assert result["available"] == 4


def test_an_unknown_level_falls_back_to_everything():
    hass = FakeHass({"system_log": fake_store([record(level="WARNING"), record(level="INFO")])})

    assert read(hass, level="LOUD")["returned"] == 2


def test_a_record_with_an_unrecognised_level_survives_filtering():
    """An unknown level sorts highest, so a level filter must not hide it."""
    hass = FakeHass({"system_log": fake_store([record(level="FATAL")])})

    assert read(hass, level="CRITICAL")["returned"] == 1


def test_logger_filters_on_a_case_insensitive_substring():
    # SQLAlchemy is the realistic case for this: plenty of library loggers are not
    # lower-case, so the record name has to be folded as well as the search term.
    records = [
        record(name="homeassistant.components.mqtt"),
        record(name="custom_components.foo"),
        record(name="SQLAlchemy.engine.Engine"),
    ]
    hass = FakeHass({"system_log": fake_store(records)})

    assert read(hass, logger="MQTT")["returned"] == 1
    assert read(hass, logger="custom_components")["returned"] == 1
    assert read(hass, logger="sqlalchemy")["returned"] == 1
    assert read(hass, logger="SQLALCHEMY")["returned"] == 1
    assert read(hass, logger="zwave")["returned"] == 0


def test_limit_caps_the_entries_and_reports_what_was_left():
    hass = FakeHass({"system_log": fake_store([record(name=f"logger.{i}") for i in range(10)])})

    result = read(hass, limit=3)

    assert result["returned"] == 3
    assert result["matched"] == 10
    assert result["truncated"] is True


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        ("4", 4),  # models send numbers as strings often enough
        (0, 1),
        (-5, 1),
        (10_000, MAX_LOG_ENTRIES),
        ("nonsense", DEFAULT_LOG_ENTRIES),
        (None, DEFAULT_LOG_ENTRIES),
    ],
)
def test_limit_is_clamped_rather_than_trusted(limit, expected):
    assert NativeFunctionExecutor()._log_limit(limit, "errors") == expected


@pytest.mark.parametrize(
    ("limit", "expected"),
    [("nonsense", DEFAULT_RAW_LOG_LINES), (10_000, MAX_RAW_LOG_LINES)],
)
def test_the_raw_source_has_its_own_limits(limit, expected):
    assert NativeFunctionExecutor()._log_limit(limit, "raw") == expected


def test_long_messages_and_tracebacks_are_clipped_with_a_marker():
    hass = FakeHass({"system_log": fake_store([record(message="m" * 9000, exception="t" * 9000)])})

    [entry] = read(hass)["entries"]

    assert len(entry["message"]) < 9000
    assert entry["message"].startswith("m" * MAX_LOG_MESSAGE_CHARS)
    assert entry["message"].endswith("more characters]")
    assert entry["exception"].startswith("t" * MAX_LOG_EXCEPTION_CHARS)
    assert entry["exception"].endswith("more characters]")


def test_the_whole_result_stays_within_the_char_budget():
    """A dict result skips conversation.py's string-only truncation, so bound it here."""
    records = [record(name=f"logger.{i}", message="m" * 4000, exception="t" * 6000) for i in range(MAX_LOG_ENTRIES)]
    hass = FakeHass({"system_log": fake_store(records)})

    result = read(hass, limit=MAX_LOG_ENTRIES)

    assert 0 < result["returned"] < MAX_LOG_ENTRIES
    assert result["truncated"] is True
    assert len(json.dumps(result)) < MAX_LOG_RESULT_CHARS * 1.5


def test_one_oversized_entry_is_still_returned(monkeypatch):
    """Never answer with an empty list just because the single match is huge.

    Per-field clipping means a real entry cannot reach MAX_LOG_RESULT_CHARS on its
    own, so the budget is squeezed here to make the guard observable.
    """
    monkeypatch.setattr(helpers, "MAX_LOG_RESULT_CHARS", 10)
    hass = FakeHass({"system_log": fake_store([record(message="m" * 99999, exception="t" * 99999)])})

    result = read(hass)

    assert result["returned"] == 1
    assert result["entries"][0]["message"].endswith("more characters]")


@pytest.mark.parametrize(
    "data",
    [
        {},  # system_log not set up
        {"system_log": object()},  # loaded, but no .records
        {"system_log": types.SimpleNamespace(records=object())},  # .records without .to_list
    ],
    ids=["missing", "no-records", "no-to-list"],
)
def test_a_missing_system_log_points_the_model_at_the_raw_source(data):
    with pytest.raises(SystemLogUnavailable) as err:
        read(FakeHass(data))

    assert "raw" in str(err.value)


# ------------------------------------------------------------------ errors, for real


def test_reads_real_system_log_records():
    """Drive the genuine LogErrorHandler, which is the contract read_logs depends on."""
    # Built the way system_log.async_setup builds it: group(1) is the path relative to
    # the Home Assistant package or the config dir.
    paths_re = re.compile(rf"(?:{re.escape(HOMEASSISTANT_PATH[0])}|{re.escape('/config')})/(.*)")
    handler = system_log.LogErrorHandler(
        FakeHass(), maxlen=system_log.DEFAULT_MAX_ENTRIES, fire_event=False, paths_re=paths_re
    )
    # async_setup attaches this handler to logging.root without a formatter, relying on
    # the console and file handlers to format first and cache exc_text on the record.
    # Nothing formats anything here, so give it its own formatter to reach the same
    # state -- otherwise LogEntry.exception stays empty and the traceback assertion
    # below would be testing the test rather than the reader.
    handler.setFormatter(logging.Formatter("%(message)s"))

    def emit(level, name, message, exc_info=None):
        handler.emit(
            logging.LogRecord(
                name=name,
                level=level,
                pathname="/config/custom_components/foo/sensor.py",
                lineno=42,
                msg=message,
                args=(),
                exc_info=exc_info,
            )
        )

    emit(logging.WARNING, "homeassistant.helpers.template", "Template variable warning")
    emit(logging.ERROR, "custom_components.foo", "Cannot reach device")
    emit(logging.ERROR, "custom_components.foo", "Cannot reach device")  # dedupes into a count
    emit(logging.ERROR, "custom_components.foo", "Different message, same key")
    try:
        raise ValueError("no such entity")
    except ValueError:
        emit(logging.ERROR, "homeassistant.components.automation", "Automation failed", sys.exc_info())

    result = read(FakeHass({"system_log": handler}), level="WARNING")

    assert result["available"] == result["matched"] == 3
    assert result["level_counts"] == {"ERROR": 2, "WARNING": 1}

    by_logger = {entry["logger"]: entry for entry in result["entries"]}
    assert set(by_logger) == {
        "homeassistant.helpers.template",
        "custom_components.foo",
        "homeassistant.components.automation",
    }

    deduped = by_logger["custom_components.foo"]
    assert deduped["count"] == 3
    assert deduped["message"] == "Cannot reach device\nDifferent message, same key"
    assert deduped["source"].endswith(":42"), deduped["source"]
    assert dt_util.parse_datetime(deduped["last_occurred"]) >= dt_util.parse_datetime(deduped["first_occurred"])

    failed = by_logger["homeassistant.components.automation"]
    assert "ValueError: no such entity" in failed["exception"]
    assert "exception" not in by_logger["homeassistant.helpers.template"]


def test_the_log_file_key_is_the_one_home_assistant_writes():
    """hass.data["logging"] is read by literal string, since core renames the constant."""
    from homeassistant.const import KEY_DATA_LOGGING

    from custom_components.lumo.helpers import LOG_FILE_DATA_KEY

    assert str(KEY_DATA_LOGGING) == LOG_FILE_DATA_KEY


# ------------------------------------------------------------------------------ raw


def test_a_missing_log_file_is_reported_not_raised(tmp_path):
    result = read(FakeHass({}, config_dir=str(tmp_path)), source="raw")

    assert result["exists"] is False
    assert result["content"] == ""
    assert result["truncated"] is False
    assert result["path"] == str(tmp_path / "home-assistant.log")
    assert "errors" in result["reason"]


def test_reads_the_path_home_assistant_recorded(tmp_path):
    path = write_log(tmp_path / "elsewhere.log", lines=40)
    hass = FakeHass({"logging": path}, config_dir=str(tmp_path))

    result = read(hass, source="raw")

    assert result["path"] == path
    assert result["exists"] is True
    assert result["lines"] == 40
    assert result["truncated"] is False
    assert result["content"].splitlines()[-1].endswith("number 39 padding-padding-padding")


def test_falls_back_to_the_config_directory(tmp_path):
    write_log(tmp_path / "home-assistant.log", lines=5)

    result = read(FakeHass({}, config_dir=str(tmp_path)), source="raw")

    assert result["exists"] is True
    assert result["lines"] == 5


def test_returns_only_the_last_requested_lines(tmp_path):
    hass = FakeHass({"logging": write_log(tmp_path / "ha.log", lines=40)})

    result = read(hass, source="raw", limit=5)

    assert result["lines"] == 5
    assert result["truncated"] is True
    lines = result["content"].splitlines()
    assert lines[0].endswith("number 35 padding-padding-padding")
    assert lines[-1].endswith("number 39 padding-padding-padding")


@pytest.mark.parametrize("source", ["raw", "RAW", "Raw"])
def test_the_source_argument_is_case_insensitive(tmp_path, source):
    hass = FakeHass({"logging": write_log(tmp_path / "ha.log", lines=3)})

    assert read(hass, source=source)["source"] == "raw"


def test_only_the_tail_of_a_huge_log_is_read(tmp_path):
    """Debug logging takes this file into the tens of megabytes; never read it whole."""
    path = write_log(tmp_path / "ha.log", lines=200_000)
    size = os.path.getsize(path)
    assert size > 10 * MAX_RAW_LOG_TAIL_BYTES, "test file is too small to prove anything"

    result = read(FakeHass({"logging": path}), source="raw", limit=MAX_RAW_LOG_LINES)

    assert result["file_size"] == size
    assert result["truncated"] is True
    assert len(result["content"]) <= MAX_RAW_LOG_CHARS
    lines = result["content"].splitlines()
    # Neither the byte window nor the char cap may leave a half-line at the top.
    assert lines[0].startswith("2026-09-15 10:00:00 ERROR")
    assert lines[-1].endswith("number 199999 padding-padding-padding")


def test_the_byte_window_bounds_the_read_and_leaves_no_half_line(tmp_path, monkeypatch):
    """The 512 KB window is invisible while MAX_RAW_LOG_CHARS is the smaller ceiling.

    Raising it out of the way is the only way to observe the window, so this is the
    test that fails if the reader ever slurps the whole file -- and the one that
    catches a partial first line surviving the seek.
    """
    monkeypatch.setattr(helpers, "MAX_RAW_LOG_CHARS", 20 * MAX_RAW_LOG_TAIL_BYTES)
    # 2 KB lines, so MAX_RAW_LOG_LINES of them are four times the window.
    path = write_log(tmp_path / "ha.log", lines=1500, pad=2000)
    assert os.path.getsize(path) > 4 * MAX_RAW_LOG_TAIL_BYTES

    result = read(FakeHass({"logging": path}), source="raw", limit=MAX_RAW_LOG_LINES)

    lines = result["content"].splitlines()
    assert 0 < len(lines) < MAX_RAW_LOG_LINES, "more lines than the window holds were read"
    assert all(line.startswith("2026-09-15 10:00:00 ERROR") for line in lines[:3])
    assert "number 1499 " in lines[-1] and lines[-1].endswith("x" * 2000)
    assert result["truncated"] is True


def test_an_empty_log_file_is_not_an_error(tmp_path):
    path = tmp_path / "ha.log"
    path.write_text("", encoding="utf-8")

    result = read(FakeHass({"logging": str(path)}), source="raw")

    assert result["exists"] is True
    assert result["content"] == ""
    assert result["lines"] == 0
    assert result["truncated"] is False


def test_undecodable_bytes_do_not_fail_the_call(tmp_path):
    """A traceback can carry a device name in any encoding. Replace, do not raise."""
    path = tmp_path / "ha.log"
    path.write_bytes(b"2026-09-15 10:00:00 ERROR [x] caf\xe9 broke\n")

    result = read(FakeHass({"logging": str(path)}), source="raw")

    assert result["lines"] == 1
    assert "broke" in result["content"]


# -------------------------------------------------------------------------- wiring


def test_execute_dispatches_the_function_name():
    hass = FakeHass({"system_log": fake_store([record()])})

    result = asyncio.run(NativeFunctionExecutor().execute(hass, {"name": "read_logs"}, {}, None, []))

    assert result["source"] == "errors"


def test_execute_still_rejects_an_unknown_name():
    with pytest.raises(NativeNotFound):
        asyncio.run(NativeFunctionExecutor().execute(FakeHass(), {"name": "read_logz"}, {}, None, []))
