"""Print the pip requirements needed to import this integration in a test run.

The tests import `custom_components.lumo.helpers`, which imports several Home
Assistant components, each of which imports its own pinned third-party packages at
module level. Installing `homeassistant` alone is therefore not enough to collect
the test suite.

The list is read off the *installed* Home Assistant rather than pinned in a
requirements file, because those pins move with every HA release -- a hand-written
list would quietly drift into installing versions that the installed core does not
use. Run it after installing homeassistant:

    python scripts/collect_test_requirements.py > /tmp/reqs.txt
    pip install -r /tmp/reqs.txt
"""

from __future__ import annotations

import json
import pathlib
import sys

import homeassistant

# Every component helpers.py, entity.py or conversation.py imports. Their own
# `dependencies` are walked transitively, so adding one here is usually enough.
SEEDS = [
    "automation",
    "conversation",
    "energy",
    "history",
    "recorder",
    "rest",
    "scrape",
    "script",
    "system_log",
]

COMPONENTS = pathlib.Path(homeassistant.__file__).parent / "components"
MANIFEST = pathlib.Path(__file__).parent.parent / "custom_components" / "lumo" / "manifest.json"


def main() -> None:
    """Write one requirement per line to stdout, and a count to stderr."""
    seen: set[str] = set()
    requirements: set[str] = set()
    queue = list(SEEDS)

    while queue:
        domain = queue.pop()
        if domain in seen:
            continue
        seen.add(domain)

        manifest = COMPONENTS / domain / "manifest.json"
        if not manifest.is_file():
            # A domain that moved or was renamed in this HA version. Not worth
            # failing the run over -- the import error, if any, will say so.
            print(f"# no manifest for {domain}", file=sys.stderr)
            continue

        data = json.loads(manifest.read_text())
        requirements.update(data.get("requirements") or [])
        queue.extend(data.get("dependencies") or [])

    # The integration's own requirements count too: importing custom_components.lumo
    # runs its package __init__, which reaches entity.py and therefore openai.
    requirements.update(json.loads(MANIFEST.read_text())["requirements"])

    print("\n".join(sorted(requirements)))
    print(f"# {len(seen)} components -> {len(requirements)} requirements", file=sys.stderr)


if __name__ == "__main__":
    main()
