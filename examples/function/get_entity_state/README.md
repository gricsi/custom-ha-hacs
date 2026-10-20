# get_entity_state

Lets the agent read the full state of any entity — the state, **every attribute**, and when it
last changed. The same thing *Developer tools → States* shows, for the entities it asks about.

Assist's built-in context only carries each exposed entity's name and state. That is enough to
turn a light on and not enough to debug anything: the answer is usually in an attribute nobody
looks at. A real example — bathroom thermostats came back at 18 °C after every restart, and the
cause was `prev_target_temp: 18.0`, an attribute the thermostat card never displays.

Read-only, and needs no code: it is a `template` function.

## Function

```yaml
- spec:
    name: get_entity_state
    title: Read entity states and attributes
    annotations:
      read_only: true
      destructive: false
      idempotent: true
      open_world: false
    description: >-
      Read the current state, all attributes, and last-changed time of one or more Home Assistant
      entities - including helpers, automations and entities not exposed to you. Use when the user
      asks why something behaves the way it does, or when debugging an automation, thermostat,
      helper or template: the cause is often an attribute (a preset, a saved previous value, a
      min/max range). Pass exact entity ids; ask for several at once to compare them.
    parameters:
      type: object
      properties:
        entity_ids:
          type: array
          items:
            type: string
          description: Exact entity ids, for example climate.living_room or input_number.flow_max.
      required: [entity_ids]
  function:
    type: template
    value_template: |-
      {%- for e in entity_ids | default([], true) %}
      {%- set s = states[e] if '.' in e else none %}
      {%- if s %}
      {{ e }}: {{ {'state': s.state, 'attributes': s.attributes,
                   'last_changed': s.last_changed.isoformat(),
                   'last_updated': s.last_updated.isoformat()} | tojson }}
      {%- else %}
      {{ e }}: not found
      {%- endif %}
      {%- endfor %}
```

## Try it

> Compare the attributes of climate.bathroom and climate.living_room — why does the bathroom
> come back at 18 °C after a restart?

> What range is input_number.flow_temperature_max set to?

## What it returns

One line per entity:

```text
climate.em_furdo_termosztat: {"attributes": {"preset_mode": "comfort", "prev_target_temp": 18.0, ...}, "last_changed": "2026-10-20T20:55:00+00:00", "state": "heat"}
sensor.typo: not found
```

## Notes

- **It ignores exposure.** A template can read every state in Home Assistant, so this function
  can too — that reach is why it is useful for debugging. Do not add it if some entity holds
  something you would not send to Lumo. Camera entities, for example, carry an `access_token`
  attribute.
- For anything more than reading states — computing a value, checking what a template helper
  would produce — use [render_template](../render_template/).
