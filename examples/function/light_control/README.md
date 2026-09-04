# light_control

The default `execute_services` function can turn lights on and off, but models are inconsistent
about nesting `brightness_pct` and colour correctly inside `service_data`. Giving brightness and
colour their own named parameters makes "dim the kitchen to 20% and make it warm" reliable.

## Function

```yaml
- spec:
    name: light_control
    description: >-
      Turn lights on or off and set brightness, colour temperature or colour.
      Prefer this over execute_services for anything involving lights.
    parameters:
      type: object
      properties:
        entity_id:
          type: string
          description: The light entity to control, e.g. light.kitchen.
        state:
          type: string
          enum: [on, off]
          description: Whether the light should end up on or off.
        brightness_pct:
          type: integer
          description: Brightness from 1 to 100. Omit to leave unchanged.
        color_temp_kelvin:
          type: integer
          description: >-
            White temperature in kelvin, roughly 2000 (very warm) to 6500 (daylight).
            Use for "warm", "cosy", "cool", "daylight".
        color_name:
          type: string
          description: >-
            A CSS colour name such as red, coral, deepskyblue. Only for coloured lights,
            and never together with color_temp_kelvin.
      required: [entity_id, state]
  function:
    type: script
    sequence:
      - choose:
          - conditions: "{{ state == 'off' }}"
            sequence:
              - action: light.turn_off
                target:
                  entity_id: "{{ entity_id }}"
        default:
          - action: light.turn_on
            target:
              entity_id: "{{ entity_id }}"
            data: >-
              {{ {}
                 | combine({'brightness_pct': brightness_pct} if brightness_pct is defined else {})
                 | combine({'color_temp_kelvin': color_temp_kelvin} if color_temp_kelvin is defined else {})
                 | combine({'color_name': color_name} if color_name is defined else {}) }}
```

## Try it

> Dim the kitchen to 20% and make it warm

> Turn the lounge lamp deep blue

## Notes

- `combine` needs Home Assistant 2024.8+. On older versions build the dict with an
  `{% set %}` / `{% if %}` block instead.
- Colour and colour temperature are mutually exclusive in `light.turn_on`; the description tells the
  model that, but a stubborn model may still send both — the service call will simply reject it.
