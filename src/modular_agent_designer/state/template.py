"""Resolve {{state.x.y}} template references against a state dict."""
from __future__ import annotations

import json
import re
from typing import Any

_TEMPLATE_RE = re.compile(r"\{\{\s*state\.([\w.]+)\s*\}\}")


class StateReferenceError(KeyError):
    """Raised when a {{state.x.y}} reference cannot be resolved."""


def resolve(text: str, state: dict[str, Any]) -> str:
    """Replace all {{state.<dotted.path>}} in *text* with values from *state*.

    Raises StateReferenceError naming the exact missing key if any reference
    cannot be resolved.  Non-string values are stringified:
      - Pydantic models: model_dump_json()
      - dicts: json.dumps()
      - everything else: str()
    """

    def _replace(m: re.Match) -> str:
        path = m.group(1)
        keys = path.split(".")
        current: Any = state
        for i, key in enumerate(keys):
            if not isinstance(current, dict) or key not in current:
                parent = "state" if i == 0 else "state." + ".".join(keys[:i])
                available = (
                    list(current.keys()) if isinstance(current, dict) else []
                )
                raise StateReferenceError(
                    f"{{{{state.{path}}}}} — key '{key}' not found "
                    f"under '{parent}' (available: {available})"
                )
            current = current[key]
        return _stringify(current)

    return _TEMPLATE_RE.sub(_replace, text)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)
