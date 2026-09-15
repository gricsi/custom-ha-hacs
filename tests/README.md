# Tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install "homeassistant>=2026.8.0" pytest
python scripts/collect_test_requirements.py > /tmp/reqs.txt && pip install -r /tmp/reqs.txt
pytest -q
```

The version floors are not cosmetic: `api.py` imports `homeassistant.components.llm`, which first
shipped in **2026.8.0**, and core requires **Python 3.14.2+**. On a Python 3.13 interpreter pip
silently caps out at homeassistant 2026.2.3 rather than failing, and the suite then dies at
collection with `No module named 'homeassistant.components.llm'`.

That second install step is not optional. The tests import the integration, which imports
`homeassistant.components.conversation`, `rest`, `scrape` and `recorder`, and each of those imports
its own pinned packages (`hassil`, `xmltodict`, `lxml`, `SQLAlchemy`, …) at module level — so
`pip install homeassistant` alone fails at collection. The script reads those pins off the core you
just installed rather than from a list here, which would drift with every HA release.

Tests run against real Home Assistant, with a `FakeHass` supplying the three things the code under
test actually uses (`hass.data`, `hass.config.path`, `hass.async_add_executor_job`). No event loop,
no config entry, no async plugin.

## What is covered

`test_read_logs.py` — the `read_logs` native function. Both sources (`errors`, `raw`), the filters
and limits, the character budgets, and the failure modes (no `system_log`, no log file, undecodable
bytes).

One test there is different in kind: `test_reads_real_system_log_records` drives the genuine
`LogErrorHandler` with real `logging.LogRecord`s, including a real traceback and a deduplicated
repeat. `read_logs` reads `hass.data["system_log"].records` and trusts the shape of
`LogEntry.to_dict()`, neither of which is public API, so that test is the tripwire for Home
Assistant reshaping the store — the fake-record tests would keep passing happily.

Nothing else in the integration has tests yet.
