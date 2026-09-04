# Attribution and provenance

This project is a fork of [OmniConv](https://github.com/mLupine/OmniConv), which is itself derived
from [extended_openai_conversation](https://github.com/jekalmin/extended_openai_conversation).

At the time of forking, **neither upstream project published a license file**. Unlicensed work is
all-rights-reserved by default, so the MIT grant in [`LICENSE`](LICENSE) is offered by the copyright
holder of this fork's own changes and cannot extend to the inherited upstream code, whose authors
retain their rights.

If you are an upstream author and would like attribution changed, a license applied, or this fork
taken down, please [open an issue](https://github.com/gricsi/custom-ha-hacs/issues).

## What is inherited vs. new

Substantially inherited from OmniConv:

- the YAML function/tool system and its executors (`native`, `script`, `template`, `rest`,
  `scrape`, `composite`, `sqlite`) in `helpers.py`
- `exceptions.py`, and the subentry-based configuration model
- the cached exposed-entities prompt concept in `api.py`

Substantially rewritten or new in this fork:

- `entity.py` — ported from the OpenAI Responses API to chat completions, which Lumo requires
- `api.py` — rebuilt against `homeassistant.components.llm`, since the `AssistAPI` class OmniConv
  subclasses no longer exists
- `config_flow.py`, `const.py`, `__init__.py`, `services.py`, `ai_task.py` — Lumo-specific
  configuration, credential validation, error handling and defaults
- everything under `examples/`
