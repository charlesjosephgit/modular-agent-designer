"""Standardized error record for all MAD layers.

WorkflowError is the single error format used by:
- nodes/agent_node.py      — written to ``_error_<agent>`` in state, read by the error router
- tools/registry.py        — returned from _McpUnavailableTool.run_async()
- plugins/tool_availability.py — returned from on_tool_error_callback()
- workflow/builder.py      — _error_router reads error_type / error_message via from_dict()
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowError:
    """Standard error record shared across all MAD layers."""

    error_type: str
    error_message: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for state storage and tool return values."""
        d: dict[str, Any] = {
            "error_type": self.error_type,
            "error_message": self.error_message,
        }
        if self.context:
            d["context"] = self.context
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorkflowError":
        """Construct from any historical error dict format (legacy or current)."""
        error_type = str(d.get("error_type") or d.get("error") or "UNKNOWN")
        error_message = str(d.get("error_message") or d.get("message") or "")
        context = {
            k: v for k, v in d.items()
            if k not in ("error_type", "error_message", "error", "message")
        }
        return cls(error_type=error_type, error_message=error_message, context=context)


def append_error_to_state(state: Any, key: str, error: "WorkflowError") -> list[dict]:
    """Append *error* to the named error list in *state*, returning the new list.

    Used to accumulate tool errors into a staging key (``_tool_errors_<agent>``)
    during agent execution. The staging list is merged into ``_error_<agent>``
    only if the agent ultimately fails, so the error router is not triggered
    on agents that succeed despite encountering tool errors.
    """
    try:
        existing = state[key]
    except (KeyError, TypeError):
        existing = None
    new_list = (list(existing) if isinstance(existing, list) else []) + [error.to_dict()]
    state[key] = new_list
    return new_list
