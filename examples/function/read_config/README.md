# read_config

Lets the agent read your YAML configuration files so it can review them and suggest fixes — the
"can you read my automations.yaml and tell me what's wrong with it?" case.

Read-only by design: this function cannot modify anything, so the agent hands you corrected YAML and
you decide whether to paste it in.

Worth knowing it isn't the only file-touching function, though — the separate `add_automation`
native function *appends* to `automations.yaml`. It is off unless you added it to your Functions box,
and enabling both gives the agent read access plus append access to that one file.

## Function

```yaml
- spec:
    name: read_config
    description: >-
      Read one of the user's Home Assistant YAML configuration files, so you can review it and
      suggest changes. Use when the user asks you to check, explain, debug or improve their
      configuration, automations, scripts or scenes. This is read-only — you cannot modify these
      files, so give the user corrected YAML to paste in themselves.
    parameters:
      type: object
      properties:
        filename:
          type: string
          enum:
            - configuration.yaml
            - automations.yaml
            - scripts.yaml
            - scenes.yaml
          description: Which configuration file to read.
      required: [filename]
  function:
    type: native
    name: read_config
```

## Try it

> Read my automations.yaml and tell me if anything looks wrong.

> Is anything in my configuration.yaml using deprecated syntax?

> My scripts.yaml has grown messy — suggest how to tidy it up.

## What it returns

```json
{"filename": "automations.yaml", "exists": true, "truncated": false, "content": "- id: '167...'"}
```

A file you don't have (many installs have no `scenes.yaml`) returns `exists: false` rather than an
error, so the agent can say so instead of retrying.

## Notes

- **The allowlist is hardcoded**, in `READABLE_CONFIG_FILES` in `helpers.py` — those four files and
  nothing else. The `enum` above only stops the model from *asking* for other paths; the Python side
  refuses them regardless, so a jailbroken prompt still cannot reach `secrets.yaml` or `.storage/`.
- **Symlinks are rejected outright.** Each path must resolve to itself, so an allowlisted name
  standing in for something else is refused — including `ln -s secrets.yaml scenes.yaml`, which a
  merely "is it inside /config?" check would happily follow. The tradeoff: if you legitimately
  symlink `automations.yaml` out to a git-managed file, this function will refuse to read it.
- **`!secret` references stay references.** The file is read as raw text rather than through Home
  Assistant's YAML loader, so `password: !secret mqtt_password` is sent as exactly that — the
  secret's *name*, never its value.
- **No per-user permission check, by design.** Anyone who can reach the assistant can read these
  four files — non-admin users and voice satellites included — on the grounds that the function is
  read-only and the allowlist holds no credentials. If you later share the assistant with people you
  would not hand `configuration.yaml` to, gate it on `user_input.context.user_id` and `.is_admin`
  (the `get_user_from_user_id` native function shows the pattern).
- **What you do send is your whole setup.** `configuration.yaml` typically carries your latitude and
  longitude, and any credential someone pasted inline instead of into `secrets.yaml` goes to Proton
  with it. Worth a skim before enabling this.
- **Long files are truncated** at `MAX_CONFIG_FILE_CHARS` (60k characters, roughly 15–20k tokens)
  with `truncated: true` set, rather than failing. A big `automations.yaml` is the usual reason —
  and note the whole file lands in the context window on every call.
