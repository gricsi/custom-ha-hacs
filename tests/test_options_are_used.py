"""Every option the config flow offers must be read somewhere else in the integration.

`max_function_calls_per_conversation`, `attach_username` and `context_threshold` were all
offered in the UI, inherited from upstream, and read by nothing -- a setting that silently
does nothing is worse than no setting. This reads the source rather than running the flow,
so it costs nothing and covers every step at once.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "lumo"

# Options the flow offers that nothing needs to read, and why.
NOT_READ_ELSEWHERE = {
    # One strategy exists, "clear", and trim_messages is that strategy.
    "CONF_CONTEXT_TRUNCATE_STRATEGY",
    # Steers the flow itself: it decides whether the advanced step is shown.
    "CONF_RECOMMENDED",
}


def offered_options() -> set[str]:
    """CONF_* names passed as the key of a vol.Optional/vol.Required in config_flow.py."""
    tree = ast.parse((INTEGRATION / "config_flow.py").read_text())
    return {
        node.args[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("Optional", "Required")
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id.startswith("CONF_")
    }


def test_every_offered_option_is_read() -> None:
    elsewhere = "\n".join(
        path.read_text() for path in INTEGRATION.glob("*.py") if path.name not in ("config_flow.py", "const.py")
    )
    unread = {name for name in offered_options() - NOT_READ_ELSEWHERE if name not in elsewhere}
    assert not unread, f"offered in the config flow but read by nothing: {sorted(unread)}"


def test_no_labels_for_options_that_are_gone() -> None:
    """A label left behind for a removed option is harmless, but it is how this went unnoticed."""
    import custom_components.lumo.const as const

    offered = {getattr(const, name) for name in offered_options() if hasattr(const, name)}
    for path in (INTEGRATION / "strings.json", INTEGRATION / "translations" / "en.json"):
        model = json.loads(path.read_text())["config_subentries"]["conversation"]["step"]["model"]
        stale = (set(model["data"]) | set(model.get("data_description", {}))) - offered
        assert not stale, f"{path.name} labels options the model step no longer offers: {sorted(stale)}"
