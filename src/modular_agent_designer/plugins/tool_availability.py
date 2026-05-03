"""ADK plugin: convert unknown tool calls into agent-visible responses."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool

if TYPE_CHECKING:
    from google.adk.tools.tool_context import ToolContext

TOOL_UNAVAILABLE_OUTPUT_KEY = "tool_unavailable_message"


class ToolAvailabilityPlugin(BasePlugin):
    """Return a static response when the model calls an unavailable tool."""

    def __init__(self) -> None:
        super().__init__(name="tool_availability")

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: "ToolContext",
        error: Exception,
    ) -> Optional[dict]:
        message = str(error)
        if "not found" not in message:
            return None
        available_tools = _parse_available_tools(message)
        mcp_unavailable_tool = _matching_mcp_unavailable_tool(
            tool.name, available_tools
        )
        output_message = _tool_unavailable_message(
            tool.name, available_tools, mcp_unavailable_tool
        )
        tool_context.state[TOOL_UNAVAILABLE_OUTPUT_KEY] = output_message
        return {
            "error": "TOOL_NOT_AVAILABLE",
            "message": output_message,
            "tool_not_available": {
                "requested_tool": tool.name,
                "arguments": tool_args,
                "available_tools": available_tools,
                "mcp_unavailable_tool": mcp_unavailable_tool,
                "details": message,
                "instruction": (
                    "Do not retry this unavailable tool name. Use another "
                    "available tool if it can satisfy the request; otherwise "
                    "explain that the requested tool is not available."
                ),
            },
        }


def _tool_unavailable_message(
    requested_tool: str,
    available_tools: list[str],
    mcp_unavailable_tool: str | None = None,
) -> str:
    if mcp_unavailable_tool is not None:
        return (
            f"MCP tool '{requested_tool}' is not available because the MCP "
            "server/toolset is unavailable."
        )
    if available_tools:
        available = ", ".join(available_tools)
        return (
            f"Tool '{requested_tool}' is not available. Available tools: "
            f"{available}."
        )
    return f"Tool '{requested_tool}' is not available."


def _parse_available_tools(error_message: str) -> list[str]:
    marker = "Available tools:"
    if marker not in error_message:
        return []
    tail = error_message.split(marker, 1)[1].split("\n", 1)[0]
    return [tool.strip() for tool in tail.split(",") if tool.strip()]


def _matching_mcp_unavailable_tool(
    requested_tool: str, available_tools: list[str]
) -> str | None:
    for available_tool in available_tools:
        suffix = "_mcp_unavailable"
        if not available_tool.endswith(suffix):
            continue
        prefix = available_tool[: -len(suffix)]
        if requested_tool.startswith(f"{prefix}_"):
            return available_tool
    return None
