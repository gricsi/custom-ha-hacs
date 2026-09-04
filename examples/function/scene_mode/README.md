# scene_mode

"Movie mode", "good night", "away" — named modes that touch several devices at once.

Deliberately **not** left to the model. Asking it to compose a movie scene from primitives means it
picks slightly different lights and brightnesses every time, and occasionally forgets the shutter.
Defining the modes here makes them deterministic; the model only chooses *which* mode.

## Function

Adjust the entity IDs to your own — `home_inventory` will tell you what they are.

```yaml
- spec:
    name: scene_mode
    description: >-
      Activate a named whole-home mode. Use when the user asks for movie mode, good night,
      good morning, or away, or says something that clearly means one of them
      ("we're watching a film", "I'm off to bed").
    parameters:
      type: object
      properties:
        mode:
          type: string
          enum: [movie, good_night, good_morning, away]
          description: Which mode to activate.
      required: [mode]
  function:
    type: script
    sequence:
      - choose:
          - conditions: "{{ mode == 'movie' }}"
            sequence:
              - action: light.turn_on
                target: {entity_id: light.living_room}
                data: {brightness_pct: 15, color_temp_kelvin: 2200}
              - action: cover.close_cover
                target: {entity_id: cover.rolling_shutter}
              - action: media_player.turn_on
                target: {entity_id: media_player.tv}

          - conditions: "{{ mode == 'good_night' }}"
            sequence:
              - action: light.turn_off
                target: {entity_id: all}
              - action: cover.close_cover
                target: {entity_id: cover.rolling_shutter}
              - action: lock.lock
                target: {entity_id: lock.front_door}

          - conditions: "{{ mode == 'good_morning' }}"
            sequence:
              - action: cover.open_cover
                target: {entity_id: cover.rolling_shutter}
              - action: light.turn_on
                target: {entity_id: light.kitchen}
                data: {brightness_pct: 80, color_temp_kelvin: 4000}

          - conditions: "{{ mode == 'away' }}"
            sequence:
              - action: light.turn_off
                target: {entity_id: all}
              - action: climate.set_preset_mode
                target: {entity_id: climate.house}
                data: {preset_mode: eco}
      - variables:
          _function_result: "{{ mode }} mode activated"
```

## Try it

> We're watching a film

> I'm off to bed

## Notes

- `_function_result` is what gets handed back to the model. Without it the model is told only
  "Success" and tends to narrate vaguely.
- If a referenced entity does not exist the script fails and the model reports the error, which is
  usually what you want while you are still tuning the modes.
