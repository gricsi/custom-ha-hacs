import logging
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any
from urllib import parse

import homeassistant.util.dt as dt_util
import voluptuous as vol
import yaml
from bs4 import BeautifulSoup
from homeassistant.components import (
    automation,
    conversation,
    energy,
    recorder,
    rest,
    scrape,
)
from homeassistant.components.automation.config import _async_validate_config_item
from homeassistant.components.script.config import SCRIPT_ENTITY_SCHEMA
from homeassistant.config import AUTOMATION_CONFIG_PATH
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_METHOD,
    CONF_NAME,
    CONF_PAYLOAD,
    CONF_RESOURCE,
    CONF_RESOURCE_TEMPLATE,
    CONF_TIMEOUT,
    CONF_VALUE_TEMPLATE,
    CONF_VERIFY_SSL,
    SERVICE_RELOAD,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound, TemplateError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.json import json_dumps
from homeassistant.helpers.script import Script
from homeassistant.helpers.template import Template
from homeassistant.util.json import json_loads

from .const import CONF_PAYLOAD_TEMPLATE, DOMAIN, EVENT_AUTOMATION_REGISTERED
from .exceptions import (
    CallServiceError,
    ConfigFileNotReadable,
    EntityNotExposed,
    EntityNotFound,
    FunctionNotFound,
    InvalidFunction,
    InvalidTraceTarget,
    NativeNotFound,
    SystemLogUnavailable,
    TraceUnavailable,
    ValueTemplateError,
)

_LOGGER = logging.getLogger(__name__)

# Files `read_config` may read. Deliberately hardcoded rather than configurable: the config
# directory also holds secrets.yaml and .storage/ (access tokens, every integration's
# credentials), and anything read here is sent to Lumo as prompt context.
READABLE_CONFIG_FILES = (
    "configuration.yaml",
    "automations.yaml",
    "scripts.yaml",
    "scenes.yaml",
)

# A long automations.yaml would swamp the context window, so truncate rather than fail.
MAX_CONFIG_FILE_CHARS = 60000

# The Logs page (Settings -> System -> Logs, /config/logs) shows two different
# things, and read_logs serves both: the deduplicated WARNING-and-above records the
# system_log integration keeps in memory, and -- behind "Load full logs" -- the raw
# home-assistant.log file.
SYSTEM_LOG_DOMAIN = "system_log"

# Where bootstrap stores the resolved log file path. homeassistant.const calls this
# key KEY_DATA_LOGGING now and DATA_LOGGING before that, so the literal string is
# the stable part -- importing the name is what breaks across versions.
LOG_FILE_DATA_KEY = "logging"
DEFAULT_LOG_FILE = "home-assistant.log"

# Ordered least to most severe, so a `level` argument becomes an index comparison.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

DEFAULT_LOG_ENTRIES = 25
MAX_LOG_ENTRIES = 100
DEFAULT_RAW_LOG_LINES = 100
MAX_RAW_LOG_LINES = 500

# Per-field and whole-result ceilings. A single stack trace can run thousands of
# characters and one unhappy integration can log fifty of them, so bound both: this
# result is a dict, which conversation.py's MAX_FUNCTION_RESULT_CHARS guard does not
# cover -- it only truncates string results.
MAX_LOG_MESSAGE_CHARS = 1500
MAX_LOG_EXCEPTION_CHARS = 2500
MAX_LOG_RESULT_CHARS = 20000
MAX_RAW_LOG_CHARS = 20000

# How much of the tail of the log file to pull off disk before splitting it into
# lines. Debug logging for one chatty integration takes this file into the tens of
# megabytes, so never read it whole.
MAX_RAW_LOG_TAIL_BYTES = 512 * 1024

# render_template evaluates a template the model wrote, so bound it the way the Developer
# tools Template page does: refuse one that would not finish in time, rather than let a loop
# over every state tie up a worker thread.
TEMPLATE_RENDER_TIMEOUT = 3.0
MAX_TEMPLATE_RESULT_CHARS = 20000

# get_traces reads hass.data["trace"], the store behind the automation editor's Traces page.
# The literal key for the same reason as LOG_FILE_DATA_KEY: the constant has moved before.
TRACE_DATA_KEY = "trace"
TRACEABLE_DOMAINS = ("automation", "script")
DEFAULT_TRACE_RUNS = 5
MAX_TRACE_RUNS = 20
# One trigger step's variables carry the full old and new state of the entity that fired,
# attributes and all, so clip each value rather than let one step crowd out the rest.
MAX_TRACE_VALUE_CHARS = 1000
MAX_TRACE_CONFIG_CHARS = 6000
MAX_TRACE_RESULT_CHARS = 20000


def get_function_executor(value: str):
    function_executor = FUNCTION_EXECUTORS.get(value)
    if function_executor is None:
        raise FunctionNotFound(value)
    return function_executor


def convert_to_template(
    settings,
    template_keys=["data", "event_data", "target", "service"],
    hass: HomeAssistant | None = None,
):
    _convert_to_template(settings, template_keys, hass, [])


def _convert_to_template(settings, template_keys, hass, parents: list[str]):
    if isinstance(settings, dict):
        for key, value in settings.items():
            if isinstance(value, str) and (key in template_keys or set(parents).intersection(template_keys)):
                settings[key] = Template(value, hass)
            if isinstance(value, dict):
                parents.append(key)
                _convert_to_template(value, template_keys, hass, parents)
                parents.pop()
            if isinstance(value, list):
                parents.append(key)
                for item in value:
                    _convert_to_template(item, template_keys, hass, parents)
                parents.pop()
    if isinstance(settings, list):
        for setting in settings:
            _convert_to_template(setting, template_keys, hass, parents)


def _as_template(value: Any, hass: HomeAssistant) -> Template | None:
    """Coerce a function-config value into a Template.

    Function configs reach the executors straight from yaml.safe_load, so every
    template key arrives as a plain string: nothing calls convert_to_template()
    on them, and the voluptuous schemas that declare cv.template are only applied
    to functions nested inside a `composite`. Rendering a str would raise
    AttributeError, so coerce here instead.
    """
    if value is None or isinstance(value, Template):
        return value
    return Template(value, hass)


def _render_value_template(value_template: Template, value: Any, arguments, function_type: str) -> Any:
    """Render a function's value_template against a response body.

    Deliberately passes no error_value: supplying one makes Home Assistant
    swallow the Jinja error *and* skip logging it, so a broken template fails
    silently and leaves nothing in the log. Omitting it logs the real message
    and hands the raw body back instead, which we detect by identity so a whole
    HTTP response never reaches the model as if it were the rendered result.
    """
    rendered = value_template.async_render_with_possible_json_value(value, variables=arguments)
    if rendered is value:
        raise ValueTemplateError(function_type)
    return rendered


def _clip(value: str, limit: int) -> str:
    """Bound one field of a log entry, saying so rather than silently cutting."""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n[... {len(value) - limit} more characters]"


def _as_local_iso(timestamp: Any) -> Any:
    """Turn system_log's epoch float into the local time the Logs page displays."""
    if not isinstance(timestamp, (int, float)):
        return timestamp
    return dt_util.as_local(dt_util.utc_from_timestamp(timestamp)).isoformat(timespec="seconds")


def _plain(value: Any) -> Any:
    """Round-trip through Home Assistant's JSON encoder into plain dicts, lists and strings.

    A trace holds datetimes, Contexts and whole State objects. The tool result is later
    serialised with bare orjson, which knows none of those, so convert here -- with the
    encoder that does -- rather than fail at the end of the turn.
    """
    return json_loads(json_dumps(value))


def _clip_json(value: Any, limit: int) -> Any:
    """Keep a JSON value whole if it fits, otherwise hand back its clipped serialisation."""
    text = json_dumps(value)
    if len(text) <= limit:
        return value
    return _clip(text, limit)


def _iso_to_local(value: Any) -> Any:
    """Show a trace's UTC ISO timestamp in local time, the way the Traces page does."""
    if not isinstance(value, str) or (parsed := dt_util.parse_datetime(value)) is None:
        return value
    return dt_util.as_local(parsed).isoformat(timespec="seconds")


def _get_rest_data(hass, rest_config, arguments):
    rest_config.setdefault(CONF_METHOD, rest.const.DEFAULT_METHOD)
    rest_config.setdefault(CONF_VERIFY_SSL, rest.const.DEFAULT_VERIFY_SSL)
    rest_config.setdefault(CONF_TIMEOUT, rest.data.DEFAULT_TIMEOUT)
    rest_config.setdefault(rest.const.CONF_ENCODING, rest.const.DEFAULT_ENCODING)

    resource_template = _as_template(rest_config.get(CONF_RESOURCE_TEMPLATE), hass)
    if resource_template is not None:
        rest_config.pop(CONF_RESOURCE_TEMPLATE)
        rest_config[CONF_RESOURCE] = resource_template.async_render(arguments, parse_result=False)

    payload_template = _as_template(rest_config.get(CONF_PAYLOAD_TEMPLATE), hass)
    if payload_template is not None:
        rest_config.pop(CONF_PAYLOAD_TEMPLATE)
        rest_config[CONF_PAYLOAD] = payload_template.async_render(arguments, parse_result=False)

    return rest.create_rest_data_from_config(hass, rest_config)


class FunctionExecutor(ABC):
    def __init__(self, data_schema=vol.Schema({})) -> None:
        """initialize function executor"""
        self.data_schema = data_schema.extend({vol.Required("type"): str})

    def to_arguments(self, arguments):
        """to_arguments function"""
        try:
            return self.data_schema(arguments)
        except vol.error.Error as e:
            function_type = next(
                (key for key, value in FUNCTION_EXECUTORS.items() if value == self),
                None,
            )
            raise InvalidFunction(function_type) from e

    def validate_entity_ids(self, hass: HomeAssistant, entity_ids, exposed_entities):
        if any(hass.states.get(entity_id) is None for entity_id in entity_ids):
            raise EntityNotFound(entity_ids)
        exposed_entity_ids = map(lambda e: e["entity_id"], exposed_entities)
        if not set(entity_ids).issubset(exposed_entity_ids):
            raise EntityNotExposed(entity_ids)

    @abstractmethod
    async def execute(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        """execute function"""


class NativeFunctionExecutor(FunctionExecutor):
    def __init__(self) -> None:
        """initialize native function"""
        super().__init__(vol.Schema({vol.Required("name"): str}))

    async def execute(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        name = function["name"]
        if name == "execute_service":
            return await self.execute_service(hass, function, arguments, user_input, exposed_entities)
        if name == "execute_service_single":
            return await self.execute_service_single(hass, function, arguments, user_input, exposed_entities)
        if name == "add_automation":
            return await self.add_automation(hass, function, arguments, user_input, exposed_entities)
        if name == "read_config":
            return await self.read_config(hass, function, arguments, user_input, exposed_entities)
        if name == "read_logs":
            return await self.read_logs(hass, function, arguments, user_input, exposed_entities)
        if name == "render_template":
            return await self.render_template(hass, function, arguments, user_input, exposed_entities)
        if name == "get_traces":
            return await self.get_traces(hass, function, arguments, user_input, exposed_entities)
        if name == "get_history":
            return await self.get_history(hass, function, arguments, user_input, exposed_entities)
        if name == "get_energy":
            return await self.get_energy(hass, function, arguments, user_input, exposed_entities)
        if name == "get_statistics":
            return await self.get_statistics(hass, function, arguments, user_input, exposed_entities)
        if name == "get_user_from_user_id":
            return await self.get_user_from_user_id(hass, function, arguments, user_input, exposed_entities)

        raise NativeNotFound(name)

    async def execute_service_single(
        self,
        hass: HomeAssistant,
        function,
        service_argument,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        domain = service_argument["domain"]
        service = service_argument["service"]
        service_data = service_argument.get("service_data", service_argument.get("data", {}))
        entity_id = service_data.get("entity_id", service_argument.get("entity_id"))
        area_id = service_data.get("area_id")
        device_id = service_data.get("device_id")

        if isinstance(entity_id, str):
            entity_id = [e.strip() for e in entity_id.split(",")]
        service_data["entity_id"] = entity_id

        if entity_id is None and area_id is None and device_id is None:
            raise CallServiceError(domain, service, service_data)
        if not hass.services.has_service(domain, service):
            raise ServiceNotFound(domain, service)
        self.validate_entity_ids(hass, entity_id or [], exposed_entities)

        try:
            await hass.services.async_call(
                domain=domain,
                service=service,
                service_data=service_data,
            )
            return {"success": True}
        except HomeAssistantError as e:
            _LOGGER.error(e)
            return {"error": str(e)}

    async def execute_service(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        result = []
        for service_argument in arguments.get("list", []):
            result.append(
                await self.execute_service_single(hass, function, service_argument, user_input, exposed_entities)
            )
        return result

    async def add_automation(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        automation_config = yaml.safe_load(arguments["automation_config"])
        config = {"id": str(round(time.time() * 1000))}
        if isinstance(automation_config, list):
            config.update(automation_config[0])
        if isinstance(automation_config, dict):
            config.update(automation_config)

        await _async_validate_config_item(hass, config, True, False)

        automations = [config]
        with open(
            os.path.join(hass.config.config_dir, AUTOMATION_CONFIG_PATH),
            "r",
            encoding="utf-8",
        ) as f:
            current_automations = yaml.safe_load(f.read())

        with open(
            os.path.join(hass.config.config_dir, AUTOMATION_CONFIG_PATH),
            "a" if current_automations else "w",
            encoding="utf-8",
        ) as f:
            raw_config = yaml.dump(automations, allow_unicode=True, sort_keys=False)
            f.write("\n" + raw_config)

        await hass.services.async_call(automation.config.DOMAIN, SERVICE_RELOAD)
        hass.bus.async_fire(
            EVENT_AUTOMATION_REGISTERED,
            {"automation_config": config, "raw_config": raw_config},
        )
        return "Success"

    async def read_config(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        filename = arguments.get("filename")
        if filename not in READABLE_CONFIG_FILES:
            raise ConfigFileNotReadable(filename, list(READABLE_CONFIG_FILES))

        config_dir = os.path.realpath(hass.config.config_dir)
        path = os.path.join(config_dir, filename)

        # An allowlisted name can still be a symlink aimed at secrets.yaml or somewhere outside the
        # config directory, so require it to resolve to itself rather than merely land in-tree.
        if os.path.realpath(path) != path:
            raise ConfigFileNotReadable(filename, list(READABLE_CONFIG_FILES))

        return await hass.async_add_executor_job(self._read_config_file, path, filename)

    def _read_config_file(self, path: str, filename: str) -> dict[str, Any]:
        """Read an allowlisted config file. Runs in the executor, since this blocks."""
        if not os.path.isfile(path):
            return {
                "filename": filename,
                "exists": False,
                "truncated": False,
                "content": "",
            }

        # errors="replace" because a config file written by an editor in a legacy encoding is
        # common enough, and losing an accent beats failing the whole tool call.
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read(MAX_CONFIG_FILE_CHARS + 1)

        return {
            "filename": filename,
            "exists": True,
            "truncated": len(content) > MAX_CONFIG_FILE_CHARS,
            "content": content[:MAX_CONFIG_FILE_CHARS],
        }

    async def read_logs(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        """Read what the Logs page shows, so the agent can diagnose a broken install."""
        # Normalised because a model happily sends "RAW" or "Warning" for an enum it was
        # given in lower or upper case, and a case mismatch would silently mean "default".
        source = str(arguments.get("source") or "errors").lower()
        limit = self._log_limit(arguments.get("limit"), source)

        if source == "raw":
            return await self._read_raw_log(hass, limit)

        level = arguments.get("level")
        return self._read_system_log(hass, str(level).upper() if level else None, arguments.get("logger"), limit)

    @staticmethod
    def _log_limit(value: Any, source: str) -> int:
        """Clamp the model's requested size, tolerating the string form of a number."""
        default, maximum = (
            (DEFAULT_RAW_LOG_LINES, MAX_RAW_LOG_LINES) if source == "raw" else (DEFAULT_LOG_ENTRIES, MAX_LOG_ENTRIES)
        )
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return default
        return max(1, min(limit, maximum))

    @staticmethod
    def _log_severity(level: Any) -> int:
        """Rank a level. An unrecognised one sorts highest, so a filter never hides it."""
        try:
            return LOG_LEVELS.index(level)
        except ValueError:
            return len(LOG_LEVELS)

    def _read_system_log(self, hass: HomeAssistant, level: Any, logger: Any, limit: int) -> dict[str, Any]:
        """Return the deduplicated error list, newest first.

        Reads the same in-memory store the frontend reads over the system_log/list
        websocket command. Nothing is copied to disk and nothing older than the last
        restart is available -- system_log holds its last max_entries records (50 by
        default) and loses them all on restart.
        """
        handler = hass.data.get(SYSTEM_LOG_DOMAIN)
        to_list = getattr(getattr(handler, "records", None), "to_list", None)
        if to_list is None:
            raise SystemLogUnavailable()

        records = to_list()

        level_counts: dict[str, int] = {}
        for record in records:
            name = record.get("level") or "UNKNOWN"
            level_counts[name] = level_counts.get(name, 0) + 1

        # No level argument means everything the page shows. The handler itself only
        # captures WARNING and above, but the system_log.write service can add records
        # at any level, and silently dropping those would be surprising.
        threshold = self._log_severity(level) if level in LOG_LEVELS else 0
        needle = str(logger).lower() if logger else ""

        matched = [
            record
            for record in records
            if self._log_severity(record.get("level")) >= threshold
            and (not needle or needle in str(record.get("name") or "").lower())
        ]

        entries: list[dict[str, Any]] = []
        budget = MAX_LOG_RESULT_CHARS
        for record in matched[:limit]:
            entry = self._as_log_entry(record)
            budget -= sum(len(str(value)) for value in entry.values())
            if budget < 0 and entries:
                break
            entries.append(entry)

        return {
            "source": "errors",
            "entries": entries,
            "returned": len(entries),
            "matched": len(matched),
            "available": len(records),
            "truncated": len(entries) < len(matched),
            "level_counts": level_counts,
        }

    def _as_log_entry(self, record: dict[str, Any]) -> dict[str, Any]:
        """Flatten one system_log record into something a model reads without help.

        Its to_dict() hands back epoch floats, source as a (file, line) pair, and
        message as a *list* -- one entry per distinct message sharing the record's
        dedup key, which is why a single entry can carry several.
        """
        message = record.get("message") or []
        if isinstance(message, str):
            message = [message]

        source = record.get("source")
        if isinstance(source, (list, tuple)) and len(source) == 2:
            source = f"{source[0]}:{source[1]}"

        entry = {
            "level": record.get("level"),
            "logger": record.get("name"),
            "message": _clip("\n".join(str(item) for item in message), MAX_LOG_MESSAGE_CHARS),
            "source": source,
            "count": record.get("count", 1),
            "first_occurred": _as_local_iso(record.get("first_occurred")),
            "last_occurred": _as_local_iso(record.get("timestamp")),
        }

        if exception := record.get("exception"):
            entry["exception"] = _clip(str(exception), MAX_LOG_EXCEPTION_CHARS)

        return entry

    async def _read_raw_log(self, hass: HomeAssistant, line_count: int) -> dict[str, Any]:
        """Return the tail of home-assistant.log -- the page's "Load full logs" view."""
        path = hass.data.get(LOG_FILE_DATA_KEY) or hass.config.path(DEFAULT_LOG_FILE)
        return await hass.async_add_executor_job(self._read_log_tail, str(path), line_count)

    def _read_log_tail(self, path: str, line_count: int) -> dict[str, Any]:
        """Read the end of the log file. Runs in the executor, since this blocks."""
        if not os.path.isfile(path):
            return {
                "source": "raw",
                "path": path,
                "exists": False,
                "truncated": False,
                "content": "",
                "reason": (
                    "no log file at this path. Home Assistant is probably not writing one"
                    " (file logging can be disabled), so only source 'errors' is available"
                ),
            }

        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - MAX_RAW_LOG_TAIL_BYTES))
            tail = f.read()

        # errors="replace" because a traceback can carry a device name in any encoding,
        # and losing one character beats failing the whole tool call.
        text = tail.decode("utf-8", errors="replace")
        if size > MAX_RAW_LOG_TAIL_BYTES:
            # The first line of the window starts mid-way through a line; drop it.
            text = text.split("\n", 1)[-1]

        all_lines = text.splitlines()
        kept = all_lines[-line_count:]
        content = "\n".join(kept)

        if len(content) > MAX_RAW_LOG_CHARS:
            # Cutting by characters lands mid-line, so drop that first fragment too.
            content = content[-MAX_RAW_LOG_CHARS:].split("\n", 1)[-1]
            dropped = True
        else:
            dropped = False

        return {
            "source": "raw",
            "path": path,
            "exists": True,
            "file_size": size,
            "lines": len(content.splitlines()),
            "truncated": dropped or size > MAX_RAW_LOG_TAIL_BYTES or len(all_lines) > len(kept),
            "content": content,
        }

    async def render_template(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        """Evaluate a Jinja template, the way Developer tools > Template does.

        Read-only: a template can read any state and attribute but cannot call a service.
        That reach is the point -- debugging needs the input_number nobody exposed to Assist --
        so this deliberately does not check exposure, unlike get_history.

        Errors come back as data rather than raising, because a broken template is the
        model's own mistake and the message is what it needs to write a working one.
        """
        source = arguments.get("template")
        if not isinstance(source, str) or not source.strip():
            return {"ok": False, "error": "template is empty"}

        template = Template(source, hass)
        try:
            template.ensure_valid()
            if await template.async_render_will_timeout(TEMPLATE_RENDER_TIMEOUT, strict=False):
                return {
                    "ok": False,
                    "error": (
                        f"rendering took longer than {TEMPLATE_RENDER_TIMEOUT:g} seconds. Narrow it --"
                        " look up the entities you need by id instead of looping over every state"
                    ),
                }
            rendered = template.async_render(parse_result=False)
        except TemplateError as err:
            return {"ok": False, "error": str(err)}

        rendered = str(rendered)
        return {
            "ok": True,
            "result": _clip(rendered, MAX_TEMPLATE_RESULT_CHARS),
            "truncated": len(rendered) > MAX_TEMPLATE_RESULT_CHARS,
        }

    async def get_traces(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        """Read automation and script traces, the editor's Traces page.

        Three widening steps, like node_red_flows: no entity lists recent runs across every
        automation and script; an entity lists that one's runs; an entity and a run_id return
        that run step by step. Automations are rarely exposed to Assist and debugging one is
        the whole point, so this does not check exposure.
        """
        store = hass.data.get(TRACE_DATA_KEY)
        if store is None:
            raise TraceUnavailable()

        await self._restore_traces(hass)

        limit = self._trace_limit(arguments.get("limit"))
        entity_id = str(arguments.get("entity_id") or "").strip().lower()

        if not entity_id:
            runs = [run for key in list(store) for run in self._trace_runs(store, key)]
            runs.sort(key=lambda run: str(run["start"] or ""), reverse=True)
            return {
                "runs": runs[:limit],
                "returned": min(limit, len(runs)),
                "available": len(runs),
            }

        key = self._trace_key(hass, entity_id)
        run_id = str(arguments.get("run_id") or "").strip()

        if not run_id:
            runs = self._trace_runs(store, key)
            return {
                "entity_id": entity_id,
                "runs": runs[:limit],
                "returned": min(limit, len(runs)),
                "available": len(runs),
                **({} if runs else {"reason": self._no_traces_reason(entity_id)}),
            }

        return self._trace_detail(store, key, entity_id, run_id)

    @staticmethod
    async def _restore_traces(hass: HomeAssistant) -> None:
        """Load the traces saved at the last shutdown, as the Traces page does on first open."""
        try:
            from homeassistant.components.trace.util import async_restore_traces
        except ImportError:
            return
        await async_restore_traces(hass)

    @staticmethod
    def _trace_limit(value: Any) -> int:
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return DEFAULT_TRACE_RUNS
        return max(1, min(limit, MAX_TRACE_RUNS))

    @staticmethod
    def _trace_key(hass: HomeAssistant, entity_id: str) -> str:
        """Map an entity id to the key its traces are stored under.

        Automations are keyed by their config id, not their entity id -- renaming an
        automation keeps its traces -- and the id is on the state as an attribute. Scripts
        are keyed by object id.
        """
        domain, _, object_id = entity_id.partition(".")
        if domain not in TRACEABLE_DOMAINS or not object_id:
            raise InvalidTraceTarget(entity_id)

        state = hass.states.get(entity_id)
        if state is None:
            raise EntityNotFound(entity_id)

        if domain == "automation":
            return f"automation.{state.attributes.get('id')}"
        return f"script.{object_id}"

    @staticmethod
    def _no_traces_reason(entity_id: str) -> str:
        if entity_id.startswith("automation."):
            return (
                "no traces stored. Either it has not run since its traces were last cleared, or it"
                " has no id: field -- Home Assistant only keeps traces for automations that have one"
            )
        return "no traces stored. It has not run since its traces were last cleared"

    @staticmethod
    def _bucket_traces(bucket: Any) -> list[Any]:
        """Every trace in one store entry, oldest first.

        From 2026.9 an entry splits runs from not-triggered evaluations; before that it was
        one LimitedSizeDict of runs. Handle both, since manifest.json allows 2026.8.
        """
        if bucket is None:
            return []
        if hasattr(bucket, "runs") and hasattr(bucket, "not_triggered"):
            return [*bucket.runs.values(), *bucket.not_triggered.values()]
        return list(bucket.values())

    def _trace_runs(self, store: dict[str, Any], key: str) -> list[dict[str, Any]]:
        """Summarise one automation's or script's traces, newest first."""
        runs = []
        for trace in self._bucket_traces(store.get(key)):
            short = _plain(trace.as_short_dict())
            timestamp = short.get("timestamp") or {}
            run = {
                "entity": f"{short.get('domain')}.{short.get('item_id')}",
                "run_id": short.get("run_id"),
                "start": _iso_to_local(timestamp.get("start")),
                "finish": _iso_to_local(timestamp.get("finish")),
                "state": short.get("state"),
                "script_execution": short.get("script_execution"),
                "last_step": short.get("last_step"),
            }
            if short.get("trigger"):
                run["trigger"] = short["trigger"]
            if short.get("not_triggered"):
                run["not_triggered"] = True
            if short.get("error"):
                run["error"] = short["error"]
            runs.append(run)

        runs.sort(key=lambda run: str(run["start"] or ""), reverse=True)
        return runs

    def _trace_detail(self, store: dict[str, Any], key: str, entity_id: str, run_id: str) -> dict[str, Any]:
        """Return one run step by step. run_id "latest" means the newest run."""
        traces = self._bucket_traces(store.get(key))
        if run_id.lower() == "latest":
            runs = self._trace_runs(store, key)
            run_id = runs[0]["run_id"] if runs else ""

        trace = next((t for t in traces if t.run_id == run_id), None)
        if trace is None:
            return {
                "entity_id": entity_id,
                "error": f"no run {run_id!r}. Call without run_id to list the stored runs",
            }

        full = _plain(trace.as_extended_dict())
        timestamp = full.get("timestamp") or {}

        steps: list[dict[str, Any]] = []
        budget = MAX_TRACE_RESULT_CHARS
        truncated = False
        for path, elements in (full.get("trace") or {}).items():
            for element in elements:
                step: dict[str, Any] = {"path": path, "time": _iso_to_local(element.get("timestamp"))}
                for field in ("result", "error", "child_id", "template_errors"):
                    if field in element:
                        step[field] = _clip_json(element[field], MAX_TRACE_VALUE_CHARS)
                if variables := element.get("changed_variables"):
                    step["changed_variables"] = {
                        name: _clip_json(value, MAX_TRACE_VALUE_CHARS) for name, value in variables.items()
                    }
                budget -= len(json_dumps(step))
                if budget < 0 and steps:
                    truncated = True
                    break
                steps.append(step)
            if truncated:
                break

        result = {
            "entity_id": entity_id,
            "run_id": full.get("run_id"),
            "start": _iso_to_local(timestamp.get("start")),
            "finish": _iso_to_local(timestamp.get("finish")),
            "state": full.get("state"),
            "script_execution": full.get("script_execution"),
            "trigger": full.get("trigger"),
            "error": full.get("error"),
            "steps": steps,
            "truncated": truncated,
            "config": _clip_json(full.get("config"), MAX_TRACE_CONFIG_CHARS),
        }
        if full.get("blueprint_inputs"):
            result["blueprint_inputs"] = _clip_json(full["blueprint_inputs"], MAX_TRACE_CONFIG_CHARS)
        return {key: value for key, value in result.items() if value is not None}

    async def get_history(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        start_time = arguments.get("start_time")
        end_time = arguments.get("end_time")
        entity_ids = arguments.get("entity_ids", [])
        include_start_time_state = arguments.get("include_start_time_state", True)
        significant_changes_only = arguments.get("significant_changes_only", True)
        minimal_response = arguments.get("minimal_response", True)
        no_attributes = arguments.get("no_attributes", True)

        now = dt_util.utcnow()
        one_day = timedelta(days=1)
        start_time = self.as_utc(start_time, now - one_day, "start_time not valid")
        end_time = self.as_utc(end_time, start_time + one_day, "end_time not valid")

        self.validate_entity_ids(hass, entity_ids, exposed_entities)

        with recorder.util.session_scope(hass=hass, read_only=True) as session:
            result = await recorder.get_instance(hass).async_add_executor_job(
                recorder.history.get_significant_states_with_session,
                hass,
                session,
                start_time,
                end_time,
                entity_ids,
                None,
                include_start_time_state,
                significant_changes_only,
                minimal_response,
                no_attributes,
            )

        return [[self.as_dict(item) for item in sublist] for sublist in result.values()]

    async def get_energy(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        energy_manager: energy.data.EnergyManager = await energy.async_get_manager(hass)
        return energy_manager.data

    async def get_user_from_user_id(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        user = await hass.auth.async_get_user(user_input.context.user_id)
        return {"name": user.name if user and hasattr(user, "name") else "Unknown"}

    async def get_statistics(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        statistic_ids = arguments.get("statistic_ids", [])
        start_time = dt_util.as_utc(dt_util.parse_datetime(arguments["start_time"]))
        end_time = dt_util.as_utc(dt_util.parse_datetime(arguments["end_time"]))

        return await recorder.get_instance(hass).async_add_executor_job(
            recorder.statistics.statistics_during_period,
            hass,
            start_time,
            end_time,
            statistic_ids,
            arguments.get("period", "day"),
            arguments.get("units"),
            arguments.get("types", {"change"}),
        )

    def as_utc(self, value: str, default_value, parse_error_message: str):
        if value is None:
            return default_value

        parsed_datetime = dt_util.parse_datetime(value)
        if parsed_datetime is None:
            raise HomeAssistantError(parse_error_message)

        return dt_util.as_utc(parsed_datetime)

    def as_dict(self, state: State | dict[str, Any]):
        if isinstance(state, State):
            return state.as_dict()
        return state


class ScriptFunctionExecutor(FunctionExecutor):
    def __init__(self) -> None:
        """initialize script function"""
        super().__init__(SCRIPT_ENTITY_SCHEMA)

    async def execute(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        script = Script(
            hass,
            function["sequence"],
            "lumo",
            DOMAIN,
            running_description="[lumo] function",
            logger=_LOGGER,
        )

        result = await script.async_run(run_variables=arguments, context=user_input.context)
        return result.variables.get("_function_result", "Success")


class TemplateFunctionExecutor(FunctionExecutor):
    def __init__(self) -> None:
        """initialize template function"""
        super().__init__(
            vol.Schema(
                {
                    vol.Required("value_template"): cv.template,
                    vol.Optional("parse_result"): bool,
                }
            )
        )

    async def execute(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        value_template = function.get("value_template")
        # Ensure value_template is a Template object, not a string
        if isinstance(value_template, str):
            value_template = Template(value_template, hass)

        return value_template.async_render(
            arguments,
            parse_result=function.get("parse_result", False),
        )


class RestFunctionExecutor(FunctionExecutor):
    def __init__(self) -> None:
        """initialize Rest function"""
        super().__init__(
            vol.Schema(rest.RESOURCE_SCHEMA).extend(
                {
                    vol.Optional("value_template"): cv.template,
                    vol.Optional("payload_template"): cv.template,
                }
            )
        )

    async def execute(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        config = function
        rest_data = _get_rest_data(hass, config, arguments)

        await rest_data.async_update()
        value = rest_data.data_without_xml()
        value_template = _as_template(config.get(CONF_VALUE_TEMPLATE), hass)

        if value is not None and value_template is not None:
            value = _render_value_template(value_template, value, arguments, "rest")

        return value


class ScrapeFunctionExecutor(FunctionExecutor):
    def __init__(self) -> None:
        """initialize Scrape function"""
        super().__init__(
            scrape.COMBINED_SCHEMA.extend(
                {
                    vol.Optional("value_template"): cv.template,
                    vol.Optional("payload_template"): cv.template,
                }
            )
        )

    async def execute(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        config = function
        rest_data = _get_rest_data(hass, config, arguments)
        coordinator = scrape.coordinator.ScrapeCoordinator(
            hass,
            rest_data,
            scrape.const.DEFAULT_SCAN_INTERVAL,
        )
        await coordinator.async_config_entry_first_refresh()

        new_arguments = dict(arguments)

        for sensor_config in config["sensor"]:
            name = _as_template(sensor_config.get(CONF_NAME), hass)
            value = self._async_update_from_rest_data(hass, coordinator.data, sensor_config, arguments)
            new_arguments["value"] = value
            if name:
                new_arguments[name.async_render()] = value

        result = new_arguments["value"]
        value_template = _as_template(config.get(CONF_VALUE_TEMPLATE), hass)

        if value_template is not None:
            result = _render_value_template(value_template, result, new_arguments, "scrape")

        return result

    def _async_update_from_rest_data(
        self,
        hass: HomeAssistant,
        data: BeautifulSoup,
        sensor_config: dict[str, Any],
        arguments: dict[str, Any],
    ) -> None:
        """Update state from the rest data."""
        value = self._extract_value(data, sensor_config)
        value_template = _as_template(sensor_config.get(CONF_VALUE_TEMPLATE), hass)

        if value_template is not None:
            value = _render_value_template(value_template, value, arguments, "scrape sensor")

        return value

    def _extract_value(self, data: BeautifulSoup, sensor_config: dict[str, Any]) -> Any:
        """Parse the html extraction in the executor."""
        value: str | list[str] | None
        select = sensor_config[scrape.const.CONF_SELECT]
        index = sensor_config.get(scrape.const.CONF_INDEX, 0)
        attr = sensor_config.get(CONF_ATTRIBUTE)
        try:
            if attr is not None:
                value = data.select(select)[index][attr]
            else:
                tag = data.select(select)[index]
                if tag.name in ("style", "script", "template"):
                    value = tag.string
                else:
                    value = tag.text
        except IndexError:
            _LOGGER.warning("Index '%s' not found", index)
            value = None
        except KeyError:
            _LOGGER.warning("Attribute '%s' not found", attr)
            value = None
        _LOGGER.debug("Parsed value: %s", value)
        return value


class CompositeFunctionExecutor(FunctionExecutor):
    def __init__(self) -> None:
        """initialize composite function"""
        super().__init__(vol.Schema({vol.Required("sequence"): vol.All(cv.ensure_list, [self.function_schema])}))

    def function_schema(self, value: Any) -> dict:
        """Validate a composite function schema."""
        if not isinstance(value, dict):
            raise vol.Invalid("expected dictionary")

        composite_schema = {vol.Optional("response_variable"): str}
        function_executor = get_function_executor(value["type"])

        return function_executor.data_schema.extend(composite_schema)(value)

    async def execute(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        config = function
        sequence = config["sequence"]

        for executor_config in sequence:
            function_executor = get_function_executor(executor_config["type"])
            result = await function_executor.execute(hass, executor_config, arguments, user_input, exposed_entities)

            response_variable = executor_config.get("response_variable")
            if response_variable:
                arguments[response_variable] = result

        return result


class SqliteFunctionExecutor(FunctionExecutor):
    def __init__(self) -> None:
        """initialize sqlite function"""
        super().__init__(
            vol.Schema(
                {
                    vol.Optional("query"): str,
                    vol.Optional("db_url"): str,
                    vol.Optional("single"): bool,
                }
            )
        )

    def is_exposed(self, entity_id, exposed_entities) -> bool:
        return any(exposed_entity["entity_id"] == entity_id for exposed_entity in exposed_entities)

    def is_exposed_entity_in_query(self, query: str, exposed_entities) -> bool:
        exposed_entity_ids = list(map(lambda e: f"'{e['entity_id']}'", exposed_entities))
        return any(exposed_entity_id in query for exposed_entity_id in exposed_entity_ids)

    def raise_error(self, msg="Unexpected error occurred."):
        raise HomeAssistantError(msg)

    def get_default_db_url(self, hass: HomeAssistant) -> str:
        db_file_path = os.path.join(hass.config.config_dir, recorder.DEFAULT_DB_FILE)
        return f"file:{db_file_path}?mode=ro"

    def set_url_read_only(self, url: str) -> str:
        scheme, netloc, path, query_string, fragment = parse.urlsplit(url)
        query_params = parse.parse_qs(query_string)

        query_params["mode"] = ["ro"]
        new_query_string = parse.urlencode(query_params, doseq=True)

        return parse.urlunsplit((scheme, netloc, path, new_query_string, fragment))

    async def execute(
        self,
        hass: HomeAssistant,
        function,
        arguments,
        user_input: conversation.ConversationInput,
        exposed_entities,
    ):
        db_url = self.set_url_read_only(function.get("db_url", self.get_default_db_url(hass)))
        query = function.get("query", "{{query}}")

        template_arguments = {
            "is_exposed": lambda e: self.is_exposed(e, exposed_entities),
            "is_exposed_entity_in_query": lambda q: self.is_exposed_entity_in_query(q, exposed_entities),
            "exposed_entities": exposed_entities,
            "raise": self.raise_error,
        }
        template_arguments.update(arguments)

        q = Template(query, hass).async_render(template_arguments)
        _LOGGER.info("Rendered query: %s", q)

        with sqlite3.connect(db_url, uri=True) as conn:
            cursor = conn.cursor().execute(q)
            names = [description[0] for description in cursor.description]

            if function.get("single") is True:
                row = cursor.fetchone()
                return {name: val for name, val in zip(names, row)}

            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({name: val for name, val in zip(names, row)})
            return result


FUNCTION_EXECUTORS: dict[str, FunctionExecutor] = {
    "native": NativeFunctionExecutor(),
    "script": ScriptFunctionExecutor(),
    "template": TemplateFunctionExecutor(),
    "rest": RestFunctionExecutor(),
    "scrape": ScrapeFunctionExecutor(),
    "composite": CompositeFunctionExecutor(),
    "sqlite": SqliteFunctionExecutor(),
}
