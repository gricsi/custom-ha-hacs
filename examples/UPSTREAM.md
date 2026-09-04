# Reusing the upstream example library

This fork kept OmniConv's function executor system unchanged, so **function specs written for
[OmniConv](https://github.com/mLupine/OmniConv) and
[extended_openai_conversation](https://github.com/jekalmin/extended_openai_conversation) work here
verbatim**. That is roughly 22 more examples than this repo ships, for free.

Verified rather than assumed:

- all seven executors are implemented — `native`, `script`, `template`, `rest`, `scrape`,
  `composite`, `sqlite`
- every `native` function the upstream examples call exists here — `execute_service`,
  `add_automation`, `get_history`, `get_energy`, `get_statistics`, `get_user_from_user_id`

Copy any of them into the **Functions** box exactly as written.

## Two that need an edit first

| Upstream example | Problem | Fix |
| --- | --- | --- |
| `function/camera_image_query` | Calls `omniconv.query_image`, a service this fork removed — Lumo has no separate image endpoint, and the action is redundant now that both models do vision | Replace the service call with `lumo.generate_content`, passing the snapshot path in `filenames`. Or use [`automation/doorbell_vision.yaml`](automation/doorbell_vision.yaml), which does the same thing via `ai_task.generate_data` |
| `function/automation` | Listens for the `automation_registered_via_omniconv` event | Rename to `automation_registered_via_lumo` |

## Worth grabbing

| Upstream example | Why |
| --- | --- |
| `shopping_list` | Add/read items by voice — the canonical assistant task |
| `calendar` | "What's on tomorrow?" without exposing every calendar entity |
| `weather` | Forecasts, not just the current state |
| `notify` / `say_tts` | Let the agent speak or push to a device |
| `history` | Complements this repo's `recent_activity`; goes through the recorder API rather than SQL |
| `energy` | Pairs well with [`automation/daily_digest.yaml`](automation/daily_digest.yaml) |
| `area` / `attributes` | Cheap context helpers, similar in spirit to `home_inventory` |
| `google_search` | Lumo has no web search of its own, so this is the way to get one |
| `automation` | Lets the agent write automations. Powerful and worth thinking twice about |

The `component_function/` set (`grocy`, `o365`, `17track`, `ytube_music_player`) is only useful if
you run those integrations.

## External projects that fit

Lumo is an OpenAI-compatible endpoint with vision, so integrations that accept a custom base URL can
usually be pointed straight at it: `https://lumo.proton.me/api/ai/v1`.

**[LLM Vision](https://github.com/valentinfrlch/ha-llmvision)** — in the default HACS store, and the
strongest fit. Analyses images, video, live camera feeds and Frigate events; keeps an event timeline
you can put on a dashboard; ships a ready-made notification blueprint. It supports "any provider
with an OpenAI compatible endpoint", which Lumo is. Considerably more capable than
`doorbell_vision.yaml` here — if you want camera AI, start there rather than building it up from
this repo's example.

**[ha-ai-memory](https://github.com/Riscue/ha-ai-memory)** — long-term memory for Assist LLM agents,
so preferences and household facts survive across conversations. Lumo has no memory of its own
between turns.

Neither has been tested against Lumo. Both are inference from documented compatibility, so treat
them as "should work, try it" rather than "known good".

## Not a fit

[home-llm](https://github.com/acon96/home-llm),
[home-generative-agent](https://github.com/goruck/home-generative-agent),
[hass-agent-llm](https://github.com/aradlein/hass-agent-llm) and
[ai_agent_ha](https://github.com/sbenodiz/ai_agent_ha) are alternative conversation agents rather
than things to add on top — they replace this integration instead of extending it. Worth a look if
you want to compare approaches, not worth running alongside.
