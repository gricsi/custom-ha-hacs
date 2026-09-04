# Automations

These are the only examples in this folder that belong in the **automation editor**.

Settings → Automations & scenes → *Create automation* → *Create new automation* → three-dot menu
(top right) → **Edit in YAML** → select everything in the box and replace it.

## The format matters

The UI editor expects a **single mapping** — `alias`, `triggers`, `actions` at the top level. It
does **not** accept the `configuration.yaml` form:

```yaml
# WRONG in the UI editor -- this is configuration.yaml format
automation:
  - alias: My automation
    triggers: ...
```

```yaml
# RIGHT in the UI editor
alias: My automation
triggers: ...
```

Pasting a **list** (anything starting with `-` at column 0) produces:

```
Message malformed: not a valid option at '['0']'
```

The `['0']` is the editor pointing at the first item of a list it never expected. If you see that
error, you have pasted either the `configuration.yaml` form above, or a function definition from
[`../function/`](../function/) — those go in the Lumo **Functions** box, not here.

## Examples

| File | What it does |
| --- | --- |
| [sunset_shutter.yaml](sunset_shutter.yaml) | Close a cover at sunset. No AI involved — deliberately |
| [doorbell_vision.yaml](doorbell_vision.yaml) | Snapshot the doorbell camera, have Lumo describe who is there |
| [daily_digest.yaml](daily_digest.yaml) | An evening summary written by Lumo and pushed as a notification |

## Substitute your own entity IDs

Every file uses placeholders such as `cover.rolling_shutter` and `camera.front_door`. Ask your Lumo
agent "what covers and cameras do I have?" once you have installed the
[home_inventory](../function/home_inventory/) function, and swap in the real ones.
