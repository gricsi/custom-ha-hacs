"""Tool presets for the quick setup of a new conversation agent.

A new user ticks the tools they want and, for Node-RED, gives a URL and a login; this turns
that into the function YAML the agent runs, so nobody has to write it to get started.

read_logs and node_red_flows are copied from examples/function/*/README.md, which stay the
documentation; tests/test_quick_setup.py fails if the two drift apart. Both only read, and
say so in their annotations.
"""

# The Node-RED value_template is one long Jinja line, verbatim from the example.
# ruff: noqa: E501

from __future__ import annotations

from typing import Any

import yaml

from .const import CONF_FUNCTIONS, DEFAULT_CONF_FUNCTIONS, RECOMMENDED_CONVERSATION_OPTIONS

CONF_TOOLS = "tools"
CONF_NODE_RED_URL = "node_red_url"
CONF_NODE_RED_USERNAME = "node_red_username"
CONF_NODE_RED_PASSWORD = "node_red_password"
CONF_CUSTOMIZE = "customize"

_READ_LOGS = r"""
- spec:
    name: read_logs
    title: Read the Home Assistant log
    annotations:
      read_only: true
      destructive: false
      idempotent: true
      open_world: false
    description: >-
      Read the Home Assistant log, the same thing the Settings > System > Logs page shows. Use
      this when the user asks what is broken, why an integration stopped working or failed to
      load, what an error or warning means, or asks you to diagnose or fix a problem. Returns
      deduplicated entries newest first, each with the logger name, level, message, source file
      and line, how many times it has happened, and the exception traceback when there is one.
      This is read-only - you cannot change anything, so identify the cause and give the user
      the exact configuration change or steps that fix it.
    parameters:
      type: object
      properties:
        source:
          type: string
          enum:
            - errors
            - raw
          description: >-
            errors (the default) returns the deduplicated warning and error list. raw returns
            the end of the log file verbatim, which is the only way to see debug-level lines or
            what happened during startup.
        level:
          type: string
          enum:
            - WARNING
            - ERROR
            - CRITICAL
          description: >-
            Lowest severity to include. Omit to get everything the page shows. Ignored when
            source is raw.
        logger:
          type: string
          description: >-
            Case-insensitive substring matched against the logger name, to narrow to one
            integration - for example mqtt, zwave_js, or custom_components. Ignored when
            source is raw.
        limit:
          type: integer
          description: >-
            How many entries to return, newest first - default 25, maximum 100. When source is
            raw this is a number of lines instead - default 100, maximum 500.
  function:
    type: native
    name: read_logs
"""

_NODE_RED_FLOWS = r"""
- spec:
    name: node_red_flows
    title: Read the Node-RED flows
    annotations:
      read_only: true
      destructive: false
      idempotent: true
      open_world: false
    description: >-
      Read the user's Node-RED flows, in three widening steps. With no arguments it lists the
      flow tab names. With `tab` it lists that tab's nodes: id, type, label, targeted entity,
      service, conditions, wires, and function code truncated to 160 characters. With `node`
      it dumps one node in full, including its complete function body. Use when the user asks
      what their flows do, or asks you to review or debug one. Always start with no arguments
      to learn the tab names — never guess one. Only reach for `node` when the truncated code
      is not enough, and pass the id from a previous `tab` call.
    parameters:
      type: object
      properties:
        tab:
          type: string
          description: Label of a flow tab, as returned by the no-argument call.
        node:
          type: string
          description: Id of a single node, as returned by a tab call. Overrides `tab`.
  function:
    type: rest
    method: GET
    timeout: 30
    authentication: basic
    headers:
      Node-RED-API-Version: v2
    value_template: |-
      {%- set nodes = value_json.flows if value_json is mapping else value_json %}
      {%- set want = (tab | default('', true)) | trim | lower %}
      {%- set one = (node | default('', true)) | trim %}
      {%- if one != '' %}
      {% for n in nodes if n.id == one %}{% for k, v in n.items() if k not in ('x','y','info') %}{{ k }}: {{ v }}
      {% endfor %}{% endfor %}
      {%- elif want == '' %}
      {% for n in nodes if n.get('type') == 'tab' %}{{ n.get('label', '') | trim }}
      {% endfor %}
      {%- else %}
      {%- set t = namespace(id='') %}
      {%- for n in nodes if n.get('type') == 'tab' and (n.get('label', '') | trim | lower) == want %}{% set t.id = n.id %}{% endfor %}
      {%- if t.id == '' %}No tab named "{{ tab }}". Call with no arguments to list the tab names.{% else %}
      {% for n in nodes if n.get('z') == t.id %}{{ n.id }} {{ n.type }} "{{ n.get('name') or n.get('label', '') }}"{% if n.get('d') %} DISABLED{% endif %}{% if n.get('action') %} action={{ n.action }}{% endif %}{% if n.get('entityId') %} entity={{ n.entityId | join(',') }}{% endif %}{% if n.get('entity_id') %} entity={{ n.entity_id }}{% endif %}{% if n.get('data') %} data={{ n.data }}{% endif %}{% if n.get('halt_if') %} halt_if={{ n.get('halt_if_compare', '') }} {{ n.halt_if }}{% endif %}{% if n.get('property') %} prop={{ n.property }}{% endif %}{% if n.get('rules') %} rules={{ n.rules }}{% endif %}{% if 'adr' in n %} modbus={{ n.get('dataType', '') }} unit={{ n.get('unitid', '') }} adr={{ n.adr }} qty={{ n.get('quantity', '') }}{% endif %}{% if n.get('func') %} code={{ n.func | replace('\n', '; ') | truncate(160, true, ' …') }}{% endif %} wires={{ n.get('wires') or [] }}
      {% endfor %}
      {%- endif %}
      {%- endif %}
"""

TOOL_PRESETS: dict[str, dict[str, Any]] = {
    "execute_services": DEFAULT_CONF_FUNCTIONS[0],
    "read_logs": yaml.safe_load(_READ_LOGS)[0],
    "node_red_flows": yaml.safe_load(_NODE_RED_FLOWS)[0],
}


def quick_setup_options(user_input: dict[str, Any]) -> dict[str, Any]:
    """Build a conversation agent's options from the quick setup form.

    Everything not on the form takes the recommended value. Raises ValueError naming the
    field when Node-RED is ticked without a URL.
    """
    functions = []
    for name in user_input.get(CONF_TOOLS, []):
        preset = {"spec": TOOL_PRESETS[name]["spec"], "function": dict(TOOL_PRESETS[name]["function"])}
        if name == "node_red_flows":
            if not (url := (user_input.get(CONF_NODE_RED_URL) or "").strip()):
                raise ValueError(CONF_NODE_RED_URL)
            function = preset["function"]
            function["resource"] = f"{url.rstrip('/')}/flows"
            if username := user_input.get(CONF_NODE_RED_USERNAME):
                function["username"] = username
                function["password"] = user_input.get(CONF_NODE_RED_PASSWORD, "")
            else:
                function.pop("authentication", None)
        functions.append(preset)

    return {
        **RECOMMENDED_CONVERSATION_OPTIONS,
        CONF_FUNCTIONS: yaml.safe_dump(functions, sort_keys=False, allow_unicode=True, width=1000),
    }
