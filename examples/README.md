# Examples

## Where does each example go?

Three kinds of example, three different destinations. Putting one in the wrong place is the most
common mistake:

| Kind | Looks like | Paste it into |
| --- | --- | --- |
| **Function** (`function/`) | a YAML **list**, starting `- spec:` | Lumo Conversation service → gear → **Functions** box |
| **Prompt** (`prompt/`) | plain English | Lumo Conversation service → gear → **Instructions** box |
| **Automation** (`automation/`) | a YAML **mapping**, starting `alias:` | Settings → Automations → Create → **Edit in YAML** |

> **`Message malformed: not a valid option at '['0']'`**
>
> You pasted a function definition (or `configuration.yaml`-style automation) into the automation
> editor. The editor wants a single mapping — `alias:` / `triggers:` / `actions:` — and reports
> `['0']` because it found a list instead. Functions belong in the Functions box, not here. See
> [automation/README.md](automation/) for the format the editor accepts.

## More examples elsewhere

This fork kept OmniConv's function executor system unchanged, so the ~22 function specs shipped by
OmniConv and extended_openai_conversation work here **verbatim** — shopping list, calendar, weather,
notify, TTS, web search and more. See [UPSTREAM.md](UPSTREAM.md) for a curated list, the two that
need an edit first, and external projects (LLM Vision, ha-ai-memory) that can point at Lumo.

## How to install a function

Settings → Devices & Services → **Lumo** → your *Lumo Conversation* service → gear icon →
untick *Use recommended model settings* → step through to the **Functions** box → paste the YAML.

Functions are a YAML **list**, so append to what is already there rather than replacing it — the
default `execute_services` entry is what lets the agent control devices at all.

Each function is a `spec` (what the model sees — name, description, JSON Schema parameters) plus a
`function` (how Home Assistant executes it). Executor types available: `native`, `script`,
`template`, `rest`, `scrape`, `sqlite`, `composite`.

**Write descriptions for the model, not for yourself.** The `description` field is the only thing
telling Lumo when to reach for a tool. "Get entity inventory" gets ignored; "Use this when the user
asks what devices exist, or asks you to design or improve a dashboard" gets called.

## Functions

| Example | Executor | What it unlocks |
| --- | --- | --- |
| [home_inventory](function/home_inventory/) | `template` | The agent learns your real areas, devices and entities — the prerequisite for any advice specific to *your* house |
| [light_control](function/light_control/) | `native` | Brightness, colour temperature and named colours, not just on/off |
| [scene_mode](function/scene_mode/) | `script` | "Movie mode", "good night" — multi-device scenes in one call |
| [recent_activity](function/recent_activity/) | `sqlite` | "Why did the hallway light come on at 3am?" — reads recorder history |
| [ask_user](function/ask_user/) | `script` | The agent can push a notification and ask a follow-up question |
| [read_config](function/read_config/) | `native` | "Read my automations.yaml and suggest fixes" — read-only access to four config files |
| [read_logs](function/read_logs/) | `native` | "What's broken?" — reads the Logs page's error list, and the raw log file behind it |
| [node_red_flows](function/node_red_flows/) | `rest` | "Which of my Node-RED nodes is never reached?" — reads flows over the add-on's admin API |

### Telling Home Assistant how a function behaves

Besides `name`, `description` and `parameters`, a `spec` may carry two optional keys that
describe the function rather than its arguments:

```yaml
- spec:
    name: read_logs
    title: Read the Home Assistant log   # human-readable label
    annotations:
      read_only: true      # does not change anything
      destructive: false   # cannot damage anything
      idempotent: true     # calling twice is the same as calling once
      open_world: false    # reaches no further than Home Assistant
```

Both are optional and both are ignored by Home Assistant 2026.9 and earlier, which has no
place to put them — the function works the same either way.

`annotations` is worth setting. Home Assistant's defaults assume the worst of a function
that says nothing: it writes, it is destructive, and it reaches outside your house. That is
the right assumption for an arbitrary bit of YAML, but it understates nothing and overstates
plenty — `read_logs` and [read_config](function/read_config/) genuinely cannot change a thing,
and saying so is better than describing it in prose and hoping the model reads it.

Keys the running Home Assistant does not recognise, and values that are not `true`/`false`,
are dropped with a warning in the log rather than applied. A typo therefore leaves a function
described pessimistically — it can never quietly promote one to read-only.

## Prompts

| Example | What it does |
| --- | --- |
| [dashboard_designer](prompt/dashboard_designer/) | Turns the agent into a Lovelace author that emits YAML you can paste |
| [smart_home_manager](prompt/smart_home_manager/) | A terser, action-biased everyday assistant |
| [log_doctor](prompt/log_doctor/) | Triages the error log and hands back pasteable fixes (pairs with `read_logs` + `read_config`) |

## Automations

Paste these into the automation editor, not the Functions box. See [automation/](automation/).

| Example | What it does |
| --- | --- |
| [sunset_shutter](automation/sunset_shutter.yaml) | Close a cover at sunset — no AI, deliberately |
| [doorbell_vision](automation/doorbell_vision.yaml) | Camera snapshot → Lumo describes who is at the door |
| [daily_digest](automation/daily_digest.yaml) | Evening summary written by Lumo |

## A note on cost and context

Every function result is fed back into the model as tokens. `home_inventory` on a large install can
be several thousand tokens per call — fine on `lumo-max` (131k context), but keep the filters tight
and prefer a `domain` or `area` argument over dumping everything.
