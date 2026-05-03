"""Tests for unavailable tool-call handling."""
from __future__ import annotations

from typing import Any

from google.adk.tools.base_tool import BaseTool

from modular_agent_designer.plugins.tool_availability import (
    TOOL_UNAVAILABLE_OUTPUT_KEY,
    ToolAvailabilityPlugin,
)


class _Tool(BaseTool):
    def __init__(self, name: str) -> None:
        super().__init__(name=name, description="test tool")


class _ToolContext:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {}


async def test_unknown_tool_error_returns_agent_visible_response() -> None:
    plugin = ToolAvailabilityPlugin()
    tool = _Tool("fs_list_directory")
    ctx = _ToolContext()
    error = ValueError(
        "Tool 'fs_list_directory' not found.\n"
        "Available tools: fs_mcp_unavailable, finish_task\n\n"
        "Possible causes:\n"
    )

    response = await plugin.on_tool_error_callback(
        tool=tool,
        tool_args={"path": "/tmp"},
        tool_context=ctx,  # type: ignore[arg-type]
        error=error,
    )

    assert response is not None
    assert response["error"] == "TOOL_NOT_AVAILABLE"
    assert response["message"] == (
        "MCP tool 'fs_list_directory' is not available because the MCP "
        "server/toolset is unavailable."
    )
    assert ctx.state[TOOL_UNAVAILABLE_OUTPUT_KEY] == response["message"]
    assert response["tool_not_available"]["requested_tool"] == (
        "fs_list_directory"
    )
    assert response["tool_not_available"]["arguments"] == {"path": "/tmp"}
    assert response["tool_not_available"]["available_tools"] == [
        "fs_mcp_unavailable",
        "finish_task",
    ]
    assert response["tool_not_available"]["mcp_unavailable_tool"] == (
        "fs_mcp_unavailable"
    )


async def test_unknown_non_mcp_tool_error_returns_generic_response() -> None:
    plugin = ToolAvailabilityPlugin()
    tool = _Tool("lookup")
    ctx = _ToolContext()
    error = ValueError(
        "Tool 'lookup' not found.\n"
        "Available tools: fetch_url, finish_task\n\n"
    )

    response = await plugin.on_tool_error_callback(
        tool=tool,
        tool_args={"q": "x"},
        tool_context=ctx,  # type: ignore[arg-type]
        error=error,
    )

    assert response is not None
    assert response["message"] == (
        "Tool 'lookup' is not available. Available tools: "
        "fetch_url, finish_task."
    )
    assert response["tool_not_available"]["mcp_unavailable_tool"] is None


async def test_non_lookup_tool_error_still_raises() -> None:
    plugin = ToolAvailabilityPlugin()

    response = await plugin.on_tool_error_callback(
        tool=_Tool("fetch_url"),
        tool_args={},
        tool_context=_ToolContext(),  # type: ignore[arg-type]
        error=RuntimeError("network failed"),
    )

    assert response is None
