# render_template

Lets the agent evaluate a Jinja template, exactly like *Developer tools → Template*. It can check
what a template helper should produce, compute a value from several helpers, or test a fix before
handing it to you.

A real example: a template sensor for a heating curve read 22 °C when it should have read 26 °C.
The agent rendered each input on its own — `states('input_number.flow_min')`,
`states('input_number.flow_max')` — saw both were 22, and found the cause: a new `input_number`
starts at its range minimum, and nobody had moved the second one.

Read-only: templates can read states but cannot call services or change anything.

## Function

```yaml
- spec:
    name: render_template
    title: Evaluate a Home Assistant template
    annotations:
      read_only: true
      destructive: false
      idempotent: true
      open_world: false
    description: >-
      Evaluate a Home Assistant Jinja template and return the result, the same as Developer tools >
      Template. Use it to read or compute values while debugging - check what a template sensor
      should output, inspect several helpers at once, or verify a template before you suggest it.
      It can read any entity, including ones not exposed to you. Look entities up by id; do not loop
      over every state. Errors are returned, not raised - read the message and fix the template.
      This is read-only and cannot change anything.
    parameters:
      type: object
      properties:
        template:
          type: string
          description: >-
            The Jinja template, for example
            "{{ states('sensor.outdoor_avg') }} / {{ state_attr('climate.bath', 'preset_mode') }}".
      required: [template]
  function:
    type: native
    name: render_template
```

## Try it

> My heating curve template says 22 °C — check each input and tell me why.

> Would `{{ (states('sensor.a') | float + states('sensor.b') | float) / 2 }}` work for my average sensor?

## What it returns

```json
{"ok": true, "result": "26.3", "truncated": false}
```

A broken template comes back as data, so the agent can read the message and try again:

```json
{"ok": false, "error": "TemplateSyntaxError: unexpected '}', expected ')'"}
```

## Notes

- **It ignores exposure**, like a `template` function always has — but here the *model* writes the
  template, so it can read anything in Home Assistant. Do not add it if some entity holds something
  you would not send to Lumo (camera entities carry an `access_token` attribute, for example).
- **Bounded like the Developer tools page.** A template that would take more than 3 seconds is
  refused rather than run, and results over 20 000 characters are clipped and flagged.
- The result is always a string (`parse_result` off), so `22.0` stays `22.0` instead of turning
  into a number the model reads as `22`.
