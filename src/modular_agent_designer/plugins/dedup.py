"""ADK plugin: short-circuit duplicate tool calls within the same session.

When a model calls the same successfully completed tool with identical
arguments more than once within the same agent, the plugin intercepts the
second call and returns the previous tool result instead of re-executing the
tool RPC. Failed calls are not marked as seen, so the model can retry them.
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
    """Replay results for repeated tool calls by the same agent."""

    def __init__(self) -> None:
        super().__init__(name="dedup_tool_calls")

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: "ToolContext",
    ) -> Optional[dict]:
        key = _state_key(tool_context.agent_name, tool.name, tool_args)
        previous_result = tool_context.state.get(key)
        if previous_result is not None:
            return previous_result
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
        key = _state_key(tool_context.agent_name, tool.name, tool_args)
        tool_context.state[key] = result
        return None


def _is_error_result(result: Any) -> bool:
    if isinstance(result, str):
        return result.strip().upper().startswith("ERROR")
    if not isinstance(result, dict):
        return False
    error = result.get("error")
    return isinstance(error, str) and bool(error.strip())


def _state_key(
    agent_name: str, tool_name: str, tool_args: dict[str, Any]
) -> str:
    args_hash = hashlib.md5(
        json.dumps(tool_args, sort_keys=True, default=str).encode()
    ).hexdigest()[:8]
    return f"{_STATE_PREFIX}{agent_name}__{tool_name}__{args_hash}"
