# get_traces

Lets the agent read automation and script traces — the *Traces* page in the automation editor —
so it can answer "did it run, what triggered it, which branch did it take, and where did it stop?"

That page is the single most useful debugging tool Home Assistant has, and nothing else gives the
agent access to it. A real example: "the floor pump did not start". The trace showed the
automation fired at 18:19:56, five minutes after the valves opened — exactly as its `for: 5 min`
said — and the user had simply looked too early.

Read-only, in three widening steps, like [node_red_flows](../node_red_flows/):

| Arguments | Returns |
| --- | --- |
| none | Recent runs across every automation and script, newest first |
| `entity_id` | That automation's or script's stored runs: start, trigger, last step, state, error |
| `entity_id` + `run_id` | One run step by step: each step's result, error and changed variables, plus the config it ran with. `run_id: latest` means the newest. |

## Function

```yaml
- spec:
    name: get_traces
    title: Read automation and script traces
    annotations:
      read_only: true
      destructive: false
      idempotent: true
      open_world: false
    description: >-
      Read automation and script traces - the Traces page of the automation editor. Use when the
      user asks whether an automation or script ran, why it did not, what triggered it, which
      branch it took, or where it failed. With no arguments it lists recent runs across everything.
      With entity_id (automation.* or script.*) it lists that one's stored runs. With entity_id and
      run_id it returns that run step by step; run_id "latest" means the newest run. Start broad and
      narrow down; take run ids from a previous call rather than guessing. Read-only.
    parameters:
      type: object
      properties:
        entity_id:
          type: string
          description: An automation.* or script.* entity id.
        run_id:
          type: string
          description: A run_id from a previous call, or "latest". Needs entity_id.
        limit:
          type: integer
          description: How many runs to list - default 5, maximum 20.
  function:
    type: native
    name: get_traces
```

## Try it

> Did the "floor pump start" automation run today? What triggered it?

> Why didn't my startup automation set the bathroom thermostats to heat? Look at its last run.

> What ran in the last hour?

## What it returns

Listing:

```json
{"entity_id": "automation.padlo_szivattyu_start", "returned": 1, "available": 1,
 "runs": [{"run_id": "1a2b…", "start": "2026-10-20T18:19:56+02:00", "finish": "2026-10-20T18:19:56+02:00",
           "state": "stopped", "script_execution": "finished", "last_step": "action/0",
           "trigger": "state of switch.padlo_szelepek"}]}
```

One run adds `steps` (`path`, `time`, `result`, `error`, `changed_variables`) and `config`.

## Notes

- **It ignores exposure.** Automations are rarely exposed to Assist, and debugging them is the
  point. A run's variables include the full old and new state of whatever triggered it.
- **Only automations with an `id:` have traces.** Home Assistant does not trace the others; the
  agent is told so instead of being handed an empty list it cannot explain.
- **Home Assistant keeps 5 runs per automation** by default (`trace: stored_traces:` in the
  automation raises it), plus, from 2026.9, up to 5 evaluations that did *not* fire — marked
  `not_triggered`, and often the answer to "why didn't it run?".
- **Bounded.** Each variable or result is clipped at 1 000 characters, the config at 6 000, and a
  run's steps at 20 000 in total — a long `repeat` loop is cut and flagged `truncated`.
- Needs the `trace` integration, which `default_config` loads.
