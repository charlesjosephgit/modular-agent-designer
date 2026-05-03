"""Centralized registry of state keys reserved for internal MAD use.

User-defined output_key values are validated against these patterns at
build time via the RootConfig schema validator.
"""
from __future__ import annotations

import re
from typing import NamedTuple


class _ReservedPattern(NamedTuple):
    pattern: re.Pattern[str]
    description: str


_RESERVED: list[_ReservedPattern] = [
    _ReservedPattern(re.compile(r"^_error_"), "agent error state (_error_<agent_name>)"),
    _ReservedPattern(re.compile(r"^_tool_errors_"), "tool error staging (_tool_errors_<agent_name>)"),
    _ReservedPattern(re.compile(r"^_loop_"), "loop iteration counter (_loop_<from>_<to>_iter)"),
    _ReservedPattern(re.compile(r"^_dispatch_"), "internal dispatcher node (_dispatch_<src>_<idx>)"),
    _ReservedPattern(re.compile(r"^_join_"), "internal join barrier (_join_<src>_<targets>)"),
    _ReservedPattern(re.compile(r"^__mda_"), "MAD internal plugin state (__mda_*)"),
    _ReservedPattern(re.compile(r"__thinking$"), "agent thinking capture (<agent_name>__thinking)"),
    _ReservedPattern(re.compile(r"^workflow_error$"), "workflow error message"),
    _ReservedPattern(re.compile(r"^tool_unavailable_message$"), "tool unavailability message"),
]


def is_reserved(key: str) -> bool:
    """Return True if *key* matches any reserved state key pattern."""
    return any(rp.pattern.search(key) for rp in _RESERVED)


def check_user_key(key: str, *, context: str = "") -> None:
    """Raise ValueError if *key* collides with a reserved state key pattern.

    Args:
        key: The state key to validate.
        context: Short description for the error message (e.g. "agent 'foo' output_key").
    """
    for rp in _RESERVED:
        if rp.pattern.search(key):
            prefix = f"{context}: " if context else ""
            raise ValueError(
                f"{prefix}state key '{key}' conflicts with reserved pattern "
                f"({rp.description}). Choose a different key name."
            )
