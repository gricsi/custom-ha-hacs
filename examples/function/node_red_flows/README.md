# node_red_flows

Lets the agent read your Node-RED flows over the add-on's admin API, so it can explain or debug
them — "read my *Keverőszelep - PID* tab and tell me what isn't wired up".

Uses the `rest` executor rather than file reading, deliberately. The Node-RED add-on moved its data
out of `/config` into `/addon_configs/a0d7b954_nodered/` in version 16.0.0, which the Home Assistant
Core container does not mount — so `read_config` cannot reach `flows.json` at all. HTTP does not care
where the file lives.

## Prerequisites

1. **Enable direct port access.** Settings → Add-ons → Node-RED → Configuration → *Network* → set
   port `1880`. Without a port set, the add-on serves ingress only and there is nothing to call.
2. **Note whether SSL is on.** The add-on's `ssl` option defaults to `true`, in which case the URL
   is `https://` and you want `verify_ssl: false` (the cert will not match the host IP). With
   `ssl: false` it is plain `http://`.
3. **Create a dedicated Home Assistant user** — a non-admin one is enough (verified: the Supervisor
   auth gate accepts non-admin accounts). Do not use your own login; see the note below.

Confirm all three from a browser before touching the Functions box: open
`http://<HA-IP>:1880/flows`, log in when prompted, and check that JSON comes back. That one test
validates the port, the protocol, the credentials and the API in a single step.

## Function

```yaml
- spec:
    name: node_red_flows
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
    resource: http://192.168.1.10:1880/flows   # your HA host's IP, not a container name
    method: GET
    timeout: 30
    authentication: basic
    username: lumo_reader
    password: REPLACE_ME
    headers:
      Node-RED-API-Version: v2
    value_template: |-
      {%- set nodes = value_json.flows if value_json.flows is defined else value_json %}
      {%- set want = (tab | default('', true)) | trim | lower %}
      {%- set one = (node | default('', true)) | trim %}
      {%- if one != '' %}
      {% for n in nodes if n.id == one %}{% for k, v in n.items() if k not in ('x','y','info') %}{{ k }}: {{ v }}
      {% endfor %}{% endfor %}
      {%- elif want == '' %}
      {% for n in nodes if n.type == 'tab' %}{{ n.label | trim }}
      {% endfor %}
      {%- else %}
      {%- set t = namespace(id='') %}
      {%- for n in nodes if n.type == 'tab' and (n.label | trim | lower) == want %}{% set t.id = n.id %}{% endfor %}
      {%- if t.id == '' %}No tab named "{{ tab }}". Call with no arguments to list the tab names.{% else %}
      {% for n in nodes if n.z == t.id %}{{ n.id }} {{ n.type }} "{{ n.name | default(n.label, true) }}"{% if n.d %} DISABLED{% endif %}{% if n.action %} action={{ n.action }}{% endif %}{% if n.entityId %} entity={{ n.entityId | join(',') }}{% endif %}{% if n.entity_id %} entity={{ n.entity_id }}{% endif %}{% if n.data %} data={{ n.data }}{% endif %}{% if n.halt_if %} halt_if={{ n.halt_if_compare }} {{ n.halt_if }}{% endif %}{% if n.property %} prop={{ n.property }}{% endif %}{% if n.rules %} rules={{ n.rules }}{% endif %}{% if n.adr is defined %} modbus={{ n.dataType }} unit={{ n.unitid }} adr={{ n.adr }} qty={{ n.quantity }}{% endif %}{% if n.func %} code={{ n.func | replace('\n', '; ') | truncate(160, true, ' …') }}{% endif %} wires={{ n.wires | default([], true) }}
      {% endfor %}
      {%- endif %}
      {%- endif %}
```

## Try it

> Which Node-RED flows do I have?

> Read the *Keverőszelep - PID* tab. Is anything left unwired?

> In the *F-H nappali* tab, does every node target the entity its name claims?

## Why the template matters

`GET /flows` returns the **entire** workspace — every node's `x`/`y` position, every wire, every
node id. A mid-sized install is comfortably over 100 KB of dense JSON. Sent raw, one call would
exhaust the context window and a good chunk of the request quota, and the model would be reasoning
about canvas coordinates.

The template drops the geometry and keeps what a review needs: id, type, name, target entity,
service action, service data, switch/state conditions, Modbus register, function body, and `wires`.
Scoping to one tab keeps a single call small enough to reason about.

Keep `wires` and `id` if you edit this. Without them the model sees a bag of nodes with no topology,
and the whole class of "this node is never reached" bugs becomes invisible.

**Function code is truncated to 160 characters, and that is not cosmetic.** One 17-branch `if/else`
in a `function` node is over a kilobyte, and the template puts it on a single line. A tab full of
those produced a payload large enough to break the frontend: `SyntaxError: JSON Parse error:
Unterminated string` out of `home-assistant-js-websocket`, i.e. the assistant's reply arrived at the
browser cut off mid-string. The tab list was small enough to survive; the tab body was not. That is
what the `node` parameter is for — get the shape from a `tab` call, then pull one node in full.

**Tab lookup is trimmed and case-insensitive.** Node-RED happily stores a tab label with a trailing
space, and one real install had exactly that (`"F-H emelet fürdő "`). A model naturally emits the
name without it, so an `n.label == tab` comparison silently found nothing.

**`value_template` must be `|-`, not `>-`.** The folded style collapses every newline into a space,
so the one-node-per-line output arrives as a single run-on line — and since flow tab names contain
spaces, even the tab list becomes ambiguous. The literal style keeps the line breaks. This is the
opposite of the [recent_activity](../recent_activity/) example, where `>-` is fine because SQL does
not care about newlines.

## Notes

- **Basic auth is the only option.** The add-on's direct-port nginx gates `location /` behind
  `auth_request` → `http://supervisor/auth`, and that endpoint accepts Basic auth, a JSON body or
  form data — it has no Bearer branch at all. A Home Assistant long-lived access token therefore
  does **not** work here, which is the first thing most people try.
- **The password is stored in the clear.** It lands in the config subentry under `.storage`, and
  travels in any backup. That is the reason for a dedicated non-admin account: the credential is
  still real, but it is not your admin login.
- **`leave_front_door_open: true` avoids the password** and is a bad trade — it leaves the Node-RED
  editor open, unauthenticated, to everyone on the LAN.
- **Port 1880 is a host port.** The add-on declares `ports: 80/tcp: 1880` and runs with
  `host_network: true`, so nginx listens on the host's 1880 while the container-internal port is 80.
  Address the HA host's IP; a container hostname will not resolve.
- **Read-only.** There is no write counterpart, and adding one would be unwise: the Node-RED admin
  API's `POST /flows` replaces the whole workspace in one shot. The agent hands you corrected
  config; you edit in the Node-RED editor.
- **Design only, not runtime.** The agent sees what the flow *is*, not what it is *doing* — no debug
  output, no `msg` values, no indication that Modbus is timing out. Pair it with `get_history` or
  [recent_activity](../recent_activity/) for "why did this misbehave just now".
- **Domain knowledge is still yours.** The agent can spot internal inconsistency — a node whose name
  disagrees with its entity, an unreachable branch, a service call with the wrong data key. It
  cannot tell you whether holding register 676 is really the baudrate on your particular unit.
