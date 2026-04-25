"""ADK plugin: short-circuit duplicate tool calls within the same session.

When a model calls the same tool with identical arguments more than once,
the plugin intercepts the second call via before_tool_callback and returns a
stop message instead of re-executing the tool RPC.  The first call is always
allowed through; after_tool_callback marks the (tool, args) pair as seen in
session state so the flag persists across rerun_on_resume cycles.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool

if TYPE_CHECKING:
    from google.adk.tools.tool_context import ToolContext

_STATE_PREFIX = "__mda_dedup__"


class DeduplicateToolCallsPlugin(BasePlugin):
    """Return a stop-hint for repeated (tool, args) pairs in the same session."""

    def __init__(self) -> None:
        super().__init__(name="dedup_tool_calls")

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: "ToolContext",
    ) -> Optional[dict]:
        key = _state_key(tool.name, tool_args)
        if tool_context.state.get(key):
            return {
                "error": "DUPLICATE_TOOL_CALL",
                "message": (
                    f"You already called '{tool.name}' with these exact arguments "
                    "and the result is already in the conversation context. "
                    "Do NOT repeat this tool call. "
                    "Use the result you already received to write your final answer now."
                ),
            }
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: "ToolContext",
        result: dict,
    ) -> Optional[dict]:
        key = _state_key(tool.name, tool_args)
        tool_context.state[key] = 1
        return None


def _state_key(tool_name: str, tool_args: dict[str, Any]) -> str:
    args_hash = hashlib.md5(
        json.dumps(tool_args, sort_keys=True, default=str).encode()
    ).hexdigest()[:8]
    return f"{_STATE_PREFIX}{tool_name}__{args_hash}"
