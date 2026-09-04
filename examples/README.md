# Examples

Drop-in **functions** (tools the model may call) and **prompts** for the Lumo conversation agent.

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

## Prompts

| Example | What it does |
| --- | --- |
| [dashboard_designer](prompt/dashboard_designer/) | Turns the agent into a Lovelace author that emits YAML you can paste |
| [smart_home_manager](prompt/smart_home_manager/) | A terser, action-biased everyday assistant |

## A note on cost and context

Every function result is fed back into the model as tokens. `home_inventory` on a large install can
be several thousand tokens per call — fine on `lumo-max` (131k context), but keep the filters tight
and prefer a `domain` or `area` argument over dumping everything.
