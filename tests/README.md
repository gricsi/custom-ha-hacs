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

Tests run against real Home Assistant. The `read_logs` tests use a `FakeHass` supplying the three
things that code actually uses (`hass.data`, `hass.config.path`, `hass.async_add_executor_job`);
the `render_template` and `get_traces` tests build a bare real `HomeAssistant` instead (see below).
Either way: no config entry, no async plugin — `asyncio.run()` is enough.

## What is covered

`test_read_logs.py` — the `read_logs` native function. Both sources (`errors`, `raw`), the filters
and limits, the character budgets, and the failure modes (no `system_log`, no log file, undecodable
bytes).

One test there is different in kind: `test_reads_real_system_log_records` drives the genuine
`LogErrorHandler` with real `logging.LogRecord`s, including a real traceback and a deduplicated
repeat. `read_logs` reads `hass.data["system_log"].records` and trusts the shape of
`LogEntry.to_dict()`, neither of which is public API, so that test is the tripwire for Home
Assistant reshaping the store — the fake-record tests would keep passing happily.

`test_tool_metadata.py` — the `integration`, `title` and `annotations` that `CustomFunctionTool`
puts on each tool. The parsing of a spec's `annotations:` block, including the rejections: an
unknown key, a non-boolean value and a non-mapping block all fall back to Home Assistant's
pessimistic defaults and log rather than raising.

Two tests there skip on the version this suite installs, and that is the point. `annotations`
needs `llm.ToolAnnotations`, which arrived after 2026.9, so the rest of the file stubs it — and
a stub cannot notice core renaming a field or changing a default. `test_declared_annotation_keys_exist_on_the_real_core`
and `test_stub_still_matches_the_real_defaults` check the genuine class when there is one. They
are inert today and start running the moment Home Assistant is upgraded past 2026.9.

There is deliberately no way to run those two early. Injecting a stand-in `ToolAnnotations` to
un-skip them would have them check a stub against a stub, which passes whatever core does — the
only thing they are for is the real class. Until then the values they guard are verified by
reading core, not by this suite.

`test_render_template.py` — the `render_template` native function: rendering states and
attributes, errors returned as data rather than raised, the will-timeout refusal of a slow
template, and the result clip. These run against a real `HomeAssistant` instance instead of a
`FakeHass`, because rendering goes through `Template`, its sandbox and its timeout thread — a fake
would only test itself. A bare instance needs no config entry; it must be built inside the running
loop, which is what each test's `run()` does.

`test_get_traces.py` — the `get_traces` native function, at all three levels (everything, one
entity, one run). The traces are the real `AutomationTrace`/`ScriptTrace` classes filled with real
`TraceElement`s in the real `TraceBuckets` container, for the same reason as the real-records test
in `test_read_logs.py`: `hass.data["trace"]` and the trace dict shapes are not public API, so only
the genuine classes notice core reshaping them. One test builds the pre-2026.9 store shape (a plain
`LimitedSizeDict` per entry), which `manifest.json` still allows.

`test_history.py` — the per-user history in `.storage/lumo.history`: seeding a fresh chat log,
leaving a live one alone, per-user isolation, text-only and capped storage, surviving a restart, the
agent's restore-then-save wiring, and that nothing but its own storage file is ever written (a
`configuration.yaml` and a Node-RED `flows.json` are checked byte for byte).

`test_tool_result.py` — the 2026.10 `ToolResult` migration in both directions, stubbed because the
suite pins 2026.9.

`test_context_threshold.py` — the context threshold option: what is dropped, what is always kept
(system prompt, the current turn with its tool calls), and that the option reaches the request.

`test_options_are_used.py` — every option the config flow offers is read somewhere else in the
integration, and the UI labels no option the flow has dropped. It reads the source rather than running
the flow; the allowlist at the top names the exceptions and why.

`test_usage_sensors.py` — token totals, the per-tier requests-remaining sensors, isolation between
config entries, and one streamed response reaching its entry's sensors. The harness calls
`loader.async_setup(hass)` because writing state for an entity added without a platform looks its
integration up.

The config flow itself, the rest of streaming and the remaining executors have no tests yet.
