"""Tests for duplicate tool-call suppression."""
from __future__ import annotations

from typing import Any

from google.adk.tools.base_tool import BaseTool

from modular_agent_designer.plugins.dedup import DeduplicateToolCallsPlugin


class _Tool(BaseTool):
    def __init__(self, name: str = "fetch_url") -> None:
        super().__init__(name=name, description="test tool")


class _ToolContext:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {}


async def test_failed_tool_call_does_not_block_retry() -> None:
    plugin = DeduplicateToolCallsPlugin()
    tool = _Tool()
    ctx = _ToolContext()
    args = {"url": "https://google.com"}

    await plugin.after_tool_callback(
        tool=tool,
        tool_args=args,
        tool_context=ctx,  # type: ignore[arg-type]
        result={"error": "Tool 'fetch' failed with Exception: bad request"},
    )

    duplicate = await plugin.before_tool_callback(
        tool=tool,
        tool_args=args,
        tool_context=ctx,  # type: ignore[arg-type]
    )

    assert duplicate is None


async def test_error_string_tool_call_does_not_block_retry() -> None:
    plugin = DeduplicateToolCallsPlugin()
    tool = _Tool()
    ctx = _ToolContext()
    args = {"url": "https://google.com"}

    await plugin.after_tool_callback(
        tool=tool,
        tool_args=args,
        tool_context=ctx,  # type: ignore[arg-type]
        result=(  # type: ignore[arg-type]
            "ERROR fetching https://google.com: bad request"
        ),
    )

    duplicate = await plugin.before_tool_callback(
        tool=tool,
        tool_args=args,
        tool_context=ctx,  # type: ignore[arg-type]
    )

    assert duplicate is None


async def test_successful_duplicate_returns_previous_result_context() -> None:
    plugin = DeduplicateToolCallsPlugin()
    tool = _Tool()
    ctx = _ToolContext()
    args = {"url": "https://google.com"}
    result = {"body": "ok"}

    await plugin.after_tool_callback(
        tool=tool,
        tool_args=args,
        tool_context=ctx,  # type: ignore[arg-type]
        result=result,
    )

    duplicate = await plugin.before_tool_callback(
        tool=tool,
        tool_args=args,
        tool_context=ctx,  # type: ignore[arg-type]
    )

    assert duplicate is not None
    assert duplicate["error"] == "DUPLICATE_TOOL_CALL"
    assert duplicate["duplicate_tool_call"] == {
        "tool_name": "fetch_url",
        "arguments": args,
        "previous_result": result,
        "instruction": (
            "Do not call this tool again with the same arguments. "
            "Use previous_result as the tool result."
        ),
    }
