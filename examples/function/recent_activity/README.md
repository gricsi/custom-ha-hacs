# recent_activity

Answers "why did the hallway light come on at 3am?" by reading state history straight out of the
recorder database.

Complements the built-in `get_history` native function: that one goes through the recorder API and
returns verbose JSON, while this returns a compact table that costs far fewer tokens.

## Function

```yaml
- spec:
    name: recent_activity
    description: >-
      Look up the recent state changes of one entity, most recent first. Use when the user
      asks why something happened, when something last changed, or how often it has been
      triggering.
    parameters:
      type: object
      properties:
        entity_id:
          type: string
          description: The entity to inspect, e.g. light.hallway.
        limit:
          type: integer
          description: How many changes to return. Defaults to 20, keep under 50.
      required: [entity_id]
  function:
    type: sqlite
    query: >-
      {%- if not is_exposed(entity_id) -%}
        {{ raise("Entity " ~ entity_id ~ " is not exposed to the assistant") }}
      {%- endif -%}
      SELECT sm.entity_id,
             s.state,
             datetime(s.last_updated_ts, 'unixepoch', 'localtime') AS changed_at
      FROM states s
      JOIN states_meta sm ON s.metadata_id = sm.metadata_id
      WHERE sm.entity_id = '{{ entity_id }}'
        AND s.state IS NOT NULL
      ORDER BY s.last_updated_ts DESC
      LIMIT {{ limit | default(20) | int }}
```

## Try it

> When did the front door last open?

> Has the boiler been cycling more than usual today?

## Notes

- The database is opened **read-only** by the executor, so a malformed query can fail but cannot
  damage the recorder.
- The `is_exposed` guard matters. Without it the model can read history for any entity in the
  database, including ones you deliberately kept away from the assistant.
- The `states` / `states_meta` join is the modern recorder schema. Home Assistant installs older
  than 2023.4 kept `entity_id` directly on `states` and need `SELECT entity_id, state FROM states`
  instead.
- Only works with the SQLite recorder (the default). Point `db_url` at your database if you moved
  the recorder to MariaDB or PostgreSQL — or skip this function, since the executor speaks SQLite.
