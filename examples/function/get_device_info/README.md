# get_device_info

Lets the agent read what a device's page in Home Assistant shows: the maker and model, firmware
and hardware version, which integration it belongs to, its configuration URL and MAC address, its
area, and every entity on it with its current state.

Entity names and states are not enough to troubleshoot hardware. Asked why a device went offline,
an agent that sees only those guesses at what the device is — its power supply, its radio, how it
talks to Home Assistant — and gives advice for hardware the user does not have. With this, the
answer starts from the real device: who made it, which integration runs it, the web page to open,
and the state of every one of its entities. It works for any device in the device registry,
whatever the integration.

Read-only, and needs no code: it is a `template` function.

## Function

```yaml
- spec:
    name: get_device_info
    title: Read device details
    annotations:
      read_only: true
      destructive: false
      idempotent: true
      open_world: false
    description: >-
      Read the details of the device behind an entity, or of a device by its name - what the
      device page in Home Assistant shows: manufacturer, model, firmware and hardware version,
      integration, configuration URL (its own web page), MAC address, area, and every entity on
      the device with its state. Use before troubleshooting or explaining any hardware - why a
      device is offline, how to restart or power it, what it is - instead of guessing what it is.
      Pass entity ids or device names; several at once is fine.
    parameters:
      type: object
      properties:
        targets:
          type: array
          items:
            type: string
          description: Entity ids (sensor.boiler_relay_temperature) or device names (Boiler Relay).
      required: [targets]
  function:
    type: template
    value_template: |-
      {%- for t in targets | default([], true) %}
      {%- set d = device_id(t) %}
      {%- if d %}
      {%- set ns = namespace(entities={}) %}
      {%- for e in device_entities(d) %}{% set ns.entities = dict(ns.entities, **{e: states(e)}) %}{% endfor %}
      {{ t }}: {{ {
          'device': device_attr(d, 'name_by_user') or device_attr(d, 'name'),
          'manufacturer': device_attr(d, 'manufacturer'),
          'model': device_attr(d, 'model'),
          'model_id': device_attr(d, 'model_id'),
          'sw_version': device_attr(d, 'sw_version'),
          'hw_version': device_attr(d, 'hw_version'),
          'integration': (device_attr(d, 'identifiers') | list | first | default(['unknown']))[0],
          'configuration_url': device_attr(d, 'configuration_url'),
          'connections': device_attr(d, 'connections') | map('join', ' ') | list,
          'area': area_name(d),
          'entities': ns.entities,
        } | tojson }}
      {%- elif '.' in t and states[t] %}
      {{ t }}: not part of a device
      {%- else %}
      {{ t }}: no device or entity by that name
      {%- endif %}
      {%- endfor %}
```

## Try it

> The boiler relay is offline. What is it, and how do I restart it?

> Which integration and firmware is sensor.boiler_relay_temperature on?

## What it returns

One line per target:

```text
sensor.boiler_relay_temperature: {"device": "Boiler Relay", "manufacturer": "Shelly", "model": "Shelly Plus 1", "model_id": "SNSW-001X16EU", "sw_version": "1.4.4", "hw_version": "gen2", "integration": "shelly", "configuration_url": "http://192.168.1.50", "connections": ["mac aa:bb:cc:dd:ee:ff"], "area": "Utility room", "entities": {"sensor.boiler_relay_temperature": "unavailable", ...}}
sun.sun: not part of a device
```

## Notes

- **It ignores exposure**, like [get_entity_state](../get_entity_state/): a template can read any
  device, and every entity on it. Do not add it if a device holds something you would not send to
  Lumo.
- The integration is read off the device's identifiers, the first one if it has several.
