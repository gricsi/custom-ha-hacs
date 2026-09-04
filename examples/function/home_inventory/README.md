# home_inventory

Gives the agent a picture of **your actual house** — areas, and the entities in each — so it can
make suggestions grounded in the devices you own instead of generic smart-home advice.

This is the one to install first. Without it the agent only sees the entities you exposed to Assist,
with no sense of how they are grouped, so "suggest a better dashboard for my living room" gets a
generic answer. With it, you get real entity IDs you can paste.

## Function

```yaml
- spec:
    name: home_inventory
    description: >-
      Look up which areas exist in this home and which devices/entities are in each one.
      Use this whenever the user asks what devices they have, asks for suggestions about
      their home, or asks you to design, review or improve a dashboard, scene or automation.
      Always call this before proposing entity IDs, so you use real ones.
    parameters:
      type: object
      properties:
        area:
          type: string
          description: >-
            Restrict to one area by name, e.g. "Living room". Omit to list every area.
        domain:
          type: string
          description: >-
            Restrict to one entity domain, e.g. light, cover, sensor, media_player.
            Omit for all domains. Prefer setting this when you only care about one kind
            of device, as the full inventory can be long.
  function:
    type: template
    value_template: >-
      {% set want_area = area | default('') | lower %}
      {% set want_domain = domain | default('') | lower %}
      {% for a in areas() %}
      {%- if not want_area or area_name(a) | lower == want_area %}
      ## {{ area_name(a) }}
      {% for e in area_entities(a) %}
      {%- if not want_domain or e.split('.')[0] == want_domain %}
      - {{ e }} ({{ state_attr(e, 'friendly_name') or e }}) = {{ states(e) }}
      {%- endif %}
      {%- endfor %}
      {%- endif %}
      {%- endfor %}

      ## Not assigned to any area
      {% for e in states | map(attribute='entity_id') | reject('in', areas() | map('area_entities') | sum(start=[])) | list %}
      {%- if not want_domain or e.split('.')[0] == want_domain %}
      - {{ e }} = {{ states(e) }}
      {%- endif %}
      {%- endfor %}
```

## Try it

> What do I actually have in the living room?

> Suggest three automations that would be useful given my devices.

> I want a nicer dashboard for upstairs — what should go on it?

## Notes

- Unlike most functions here this ignores Assist exposure and reports **everything** Home Assistant
  knows about. That is the point — it is for planning — but it does mean entity names for every
  device in your home get sent to Lumo on each call. Set `domain` to keep it small.
- The trailing "not assigned to any area" block is usually where the interesting mess lives.
