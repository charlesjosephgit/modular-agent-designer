"""Tests for duplicate tool-call suppression."""
from __future__ import annotations

from typing import Any

from google.adk.tools.base_tool import BaseTool

from modular_agent_designer.plugins.dedup import DeduplicateToolCallsPlugin


class _Tool(BaseTool):
    def __init__(self, name: str = "fetch_url") -> None:
        super().__init__(name=name, description="test tool")


class _ToolContext:
    def __init__(self, agent_name: str = "researcher") -> None:
        self.agent_name = agent_name
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

    assert duplicate == result


async def test_duplicate_detection_is_scoped_to_agent() -> None:
    plugin = DeduplicateToolCallsPlugin()
    tool = _Tool("load_skill")
    researcher_ctx = _ToolContext(agent_name="researcher")
    writer_ctx = _ToolContext(agent_name="writer")
    writer_ctx.state = researcher_ctx.state
    args = {"skill_name": "summarize"}
    result = {"skill": "summarize", "instructions": "Use short summaries."}

    await plugin.after_tool_callback(
        tool=tool,
        tool_args=args,
        tool_context=researcher_ctx,  # type: ignore[arg-type]
        result=result,
    )

    duplicate = await plugin.before_tool_callback(
        tool=tool,
        tool_args=args,
        tool_context=writer_ctx,  # type: ignore[arg-type]
    )

    assert duplicate is None
