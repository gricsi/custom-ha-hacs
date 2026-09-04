# Lumo for Home Assistant

A Home Assistant conversation agent and AI Task provider backed by [Proton Lumo](https://lumo.proton.me).

Forked from [mLupine/OmniConv](https://github.com/mLupine/OmniConv), which is itself a rebase of
`extended_openai_conversation` onto the modern native Home Assistant integration. The fork exists
because Lumo and OpenAI do not share a wire protocol — see [What changed](#what-changed-from-omniconv).
Licensed MIT; see [NOTICE.md](NOTICE.md) for what is inherited from upstream and under what terms.

> **Status: pre-GA.** The Lumo API is not publicly released. Model ids, quotas, and the endpoint path
> may all move before launch. The base URL is an editable field precisely so a path change does not
> require a new release.

## Install

**HACS** → three-dot menu → *Custom repositories* → add this repo as category **Integration** →
install **Lumo** → restart Home Assistant → *Settings → Devices & Services → Add Integration → Lumo*.

Or copy `custom_components/lumo/` into your Home Assistant `config/custom_components/` directory.

## Configure

Setup asks for three things, all optional except in the obvious case:

| Field | Default | Notes |
| --- | --- | --- |
| API key | *(blank)* | From the *Manage keys* page at lumo.proton.me. Leave blank to use the anonymous tier. |
| Base URL | `https://lumo.proton.me/api/ai/v1` | Editable, so a pre-GA path change is a UI edit, not a release. |
| Skip authentication | off | Skips the reachability and key check during setup. |

**`GET /models` does not authenticate.** It answers 200 for a valid key, a revoked key, and no key
alike, so listing models proves reachability and nothing about your credential. Setup therefore
validates a supplied key by completing a one-token request instead, which distinguishes three cases:

| Key | Result |
| --- | --- |
| Valid | Accepted. Responses omit `usage.remaining_limits` — no reported quota. |
| Well-formed but revoked/expired | `401 {"Code": 0, "Error": "Invalid or expired API key"}` → *invalid auth* |
| Not recognised as a credential at all | **HTTP 200, served anonymously.** Caught by checking for the anonymous `remaining_limits` payload, and reported rather than silently accepted. |

That third row is the trap: without the check, a mistyped key looks like it worked and quietly caps
you at the anonymous quota. The model dropdown in the conversation/AI-task options is populated live
from `GET /models`, labelled with each model's context window.

## What works

| Capability | Lumo | Notes |
| --- | --- | --- |
| Streaming chat | ✅ | SSE, `chat.completion.chunk` |
| Function calling | ✅ | Full tool-call round trip, so Assist can control devices |
| Vision | ✅ | Image attachments as data URIs |
| Reasoning trace | ✅ | Lumo's non-standard `reasoning` field maps to HA's thinking content |
| Structured output | ✅ | `response_format: json_schema`, used by AI Task |
| Image generation | ❌ | No image endpoint exists |
| Web search / code interpreter | ❌ | Responses-API-only features, not available |
| Embeddings, audio | ❌ | No endpoint |

## What changed from OmniConv

The load-bearing difference: **Lumo has no Responses API.** `POST /api/ai/v1/responses` returns 404.
OmniConv is built entirely on `client.responses.create`, so pointing it at Lumo by changing the base
URL would 404 every single request. The whole entity layer was ported to `chat/completions`.

- `entity.py` — rewritten. Chat-completions message conversion, a new SSE delta transformer
  (accumulating tool-call argument fragments by index), and Lumo's `reasoning` field surfaced as HA
  thinking content.
- Error handling — Proton returns `{"Code": 2000, "Error": "...", "Details": {}}`, not OpenAI's
  `{"error": {...}}`. The OpenAI SDK cannot find a message in that envelope, so `err.type` is always
  `None` and OmniConv's `err.type == "insufficient_quota"` branch is dead code. `lumo_error_detail()`
  unwraps the Proton shape so failures surface readably in the HA log.
- Quota — anonymous responses carry `usage.remaining_limits` (`{"lite": N, "max": N, "images": N}`),
  roughly 20 requests per window, which is tight for an always-on Assist pipeline. The integration
  logs it and warns when the applied tier is nearly spent. Authenticated responses omit the field
  entirely, so with a valid key this logging stays quiet.
- Removed — Azure support, DALL-E `generate_image`, `query_image`, web search, code interpreter, and
  the `o*`/`gpt-5`-specific option branching. All of it targets endpoints Lumo does not serve.
- `manifest.json` — `openai` pinned to `==2.45.0`, matching Home Assistant core's own pin, instead of
  OmniConv's `>=2.2.0` range.
- Defaults — `lumo-max`, temperature `0.3` (the documented API default; deterministic suits device
  control), and `max_tokens` raised from OmniConv's 150, which truncates anything longer than one
  sentence. Setup's credential probe still uses `lumo-lite`, since checking a key should not spend
  from the `max` allowance.

Retained from OmniConv unchanged: the YAML function/tool system (`native`, `script`, `template`,
`rest`, `scrape`, `composite`, `sqlite` executors), the cached exposed-entities prompt, and the
subentry-based conversation/AI-task configuration model.

## Known quirks of the pre-GA API

- An **unknown model id is silently downgraded** to `lumo-lite` rather than rejected. A typo in the
  model field costs you quality with no error — hence the live-fetched dropdown.
- The `model` field echoed in a response is **always `lumo-lite`**, even for a `lumo-max` request.
  On the anonymous tier `usage.applied_limit_category` correctly reports `max`, so the request does
  route differently — but authenticated responses carry no such field, meaning there is currently no
  way to confirm from a response which model actually served it.
- The docs and the API disagree on two values. Docs say both models have a 128k context; `/models`
  reports 262144 for `lumo-lite` and 131072 for `lumo-max`. Docs give `temperature` a default of 0.3;
  `/models` reports `default_model_temperature: 1`. The dropdown uses the live `/models` figures.
- `reasoning_effort` is honoured: `max` populates a `reasoning` field on the response, the default
  does not. Unknown parameters are ignored rather than rejected, so an out-of-range value is
  harmless rather than a 400.
- Attachments are images only. There is no document/file content part on `chat/completions`.

## Examples

Ready-made functions and prompts live in [`examples/`](examples/) — a home-inventory tool that lets
the agent give advice about the devices you actually own, richer light control, named scene modes,
recorder history lookups, and prompts that turn the agent into a dashboard designer or a terse
everyday assistant.

## Install via HACS

HACS → three-dot menu → **Custom repositories** → URL `https://github.com/gricsi/custom-ha-hacs`,
category **Integration** → Add. Then find **Lumo** in HACS, install, and restart Home Assistant.
