"""ADK plugin: short-circuit duplicate tool calls within the same session.

When a model calls the same successfully completed tool with identical
arguments more than once, the plugin intercepts the second call and returns a
stop message instead of re-executing the tool RPC. Failed calls are not marked
as seen, so the model can retry them.
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
    """Return a stop-hint for repeated tool calls in the same session."""

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
        previous_result = tool_context.state.get(key)
        if previous_result is not None:
            return {
                "error": "DUPLICATE_TOOL_CALL",
                "message": (
                    f"You already called '{tool.name}' with these exact "
                    "arguments "
                    "and the result is already in the conversation context. "
                    "Do NOT repeat this tool call. "
                    "Use the result you already received to write your final "
                    "answer now."
                ),
                "duplicate_tool_call": {
                    "tool_name": tool.name,
                    "arguments": tool_args,
                    "previous_result": previous_result,
                    "instruction": (
                        "Do not call this tool again with the same arguments. "
                        "Use previous_result as the tool result."
                    ),
                },
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
        if _is_error_result(result):
            return None
        key = _state_key(tool.name, tool_args)
        tool_context.state[key] = result
        return None


def _is_error_result(result: Any) -> bool:
    if isinstance(result, str):
        return result.strip().upper().startswith("ERROR")
    if not isinstance(result, dict):
        return False
    error = result.get("error")
    return isinstance(error, str) and bool(error.strip())


def _state_key(tool_name: str, tool_args: dict[str, Any]) -> str:
    args_hash = hashlib.md5(
        json.dumps(tool_args, sort_keys=True, default=str).encode()
    ).hexdigest()[:8]
    return f"{_STATE_PREFIX}{tool_name}__{args_hash}"
