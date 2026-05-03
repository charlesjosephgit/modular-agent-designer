"""Resolve {{state.x.y}} template references against a state dict.

Also supports conditional blocks:
    {{#if state.key}}...{{/if}}
The inner content is included only when the key exists in state and is truthy.
"""
from __future__ import annotations

import json
import re
from typing import Any

_TEMPLATE_RE = re.compile(r"\{\{\s*state\.([\w.]+)\s*\}\}")
_CONDITIONAL_RE = re.compile(
    r"\{\{#if\s+state\.([\w.]+)\s*\}\}(.*?)\{\{/if\}\}",
    re.DOTALL,
)


class StateReferenceError(KeyError):
    """Raised when a {{state.x.y}} reference cannot be resolved."""


def _walk(path: str, state: dict[str, Any]) -> tuple[Any, bool]:
    """Walk a dotted path into *state*.

    Returns ``(value, True)`` on success, ``(None, False)`` if any segment
    is missing.
    """
    keys = path.split(".")
    current: Any = state
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None, False
        current = current[key]
    return current, True


def resolve(
    text: str,
    state: dict[str, Any],
    *,
    missing: str | None = None,
) -> str:
    """Replace all ``{{state.<dotted.path>}}`` in *text* with values from *state*.

    Also processes conditional blocks::

        {{#if state.key}}…{{/if}}

    The inner content is included only when the key exists and is truthy.

    When *missing* is ``None`` (default), raises ``StateReferenceError`` for any
    unresolvable ``{{state.x}}`` reference outside a conditional block.
    When *missing* is a string, that string is substituted instead and no error
    is raised — callers can log warnings as needed.

    Non-string values are stringified:
      - Pydantic models: model_dump_json()
      - dicts: json.dumps()
      - everything else: str()
    """

    # --- 1. Resolve conditional blocks first ---
    def _resolve_conditional(m: re.Match) -> str:
        path = m.group(1)
        body = m.group(2)
        value, found = _walk(path, state)
        if found and value:
            return body
        return ""

    text = _CONDITIONAL_RE.sub(_resolve_conditional, text)

    # --- 2. Resolve value templates ---
    def _replace(m: re.Match) -> str:
        path = m.group(1)
        value, found = _walk(path, state)
        if not found:
            if missing is not None:
                return missing
            keys = path.split(".")
            # Build a helpful error
            current: Any = state
            for i, key in enumerate(keys):
                if not isinstance(current, dict) or key not in current:
                    parent = (
                        "state" if i == 0 else "state." + ".".join(keys[:i])
                    )
                    available = (
                        list(current.keys())
                        if isinstance(current, dict)
                        else []
                    )
                    raise StateReferenceError(
                        f"{{{{state.{path}}}}} — key '{key}' not found "
                        f"under '{parent}' (available: {available})"
                    )
                current = current[key]
        return _stringify(value)

    return _TEMPLATE_RE.sub(_replace, text)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)
