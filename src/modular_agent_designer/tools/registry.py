"""Resolve tool references from YAML ToolConfig into callables or toolsets."""
from __future__ import annotations

from typing import Any, Callable, Union

from google.adk.tools import BaseTool
from google.adk.tools.mcp_tool import (
    McpToolset,
    SseConnectionParams,
    StreamableHTTPConnectionParams,
)
from google.genai import types
from mcp import StdioServerParameters

from ..errors import WorkflowError, append_error_to_state
from ..config.schema import (
    BuiltinToolConfig,
    McpHttpToolConfig,
    McpSseToolConfig,
    McpStdioToolConfig,
    PythonToolConfig,
    ToolConfig,
)
from ..plugins.tool_availability import TOOL_UNAVAILABLE_OUTPUT_KEY
from ..utils.imports import import_dotted_ref
from . import BUILTIN_TOOLS
from .safety import wrap_adk_base_tool, wrap_callable_tool


class SafeMcpToolset(McpToolset):
    """MCP toolset that returns tool execution errors as results."""

    async def get_tools(self, readonly_context: Any = None) -> list[Any]:
        try:
            tools = await super().get_tools(readonly_context=readonly_context)
        except Exception as exc:
            return [_McpUnavailableTool(exc)]
        return [wrap_adk_base_tool(tool) for tool in tools]


class _McpUnavailableTool(BaseTool):
    """Fallback tool exposed when an MCP server cannot be reached."""

    def __init__(self, exc: Exception) -> None:
        self._error_message = (
            f"MCP server unavailable: {type(exc).__name__}: {exc}"
        )
        super().__init__(
            name="mcp_unavailable",
            description=(
                f"{self._error_message}. Call this tool to report the MCP "
                "connection failure. Do not invent other MCP tool names."
            ),
        )

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
            ),
        )

    async def run_async(
        self, *, args: dict[str, Any], tool_context: Any
    ) -> dict[str, Any]:
        instruction = (
            "Tell the user the MCP server is unavailable and include "
            "this message. Do not retry unavailable MCP tools."
        )
        we = WorkflowError(
            error_type="MCP_UNAVAILABLE",
            error_message=self._error_message,
            context={"instruction": instruction},
        )
        if tool_context is not None:
            agent_name = getattr(tool_context, "agent_name", None)
            if agent_name:
                append_error_to_state(tool_context.state, f"_tool_errors_{agent_name}", we)
        # Return the original agent-facing format so LLM behavior is unchanged.
        return {
            "error": "MCP_UNAVAILABLE",
            "message": self._error_message,
            "mcp_unavailable": {"instruction": instruction},
        }


def _resolve_callable(name: str, ref: str) -> Callable[..., Any]:
    obj = import_dotted_ref(ref, context=f"Tool '{name}'")
    if not callable(obj):
        raise TypeError(
            f"Tool '{name}': ref '{ref}' resolved to "
            f"{type(obj).__name__}, which is not callable. "
            "Tool refs must point at a function or callable instance."
        )
    return wrap_callable_tool(name, obj)


def resolve_tool(
    name: str, cfg: ToolConfig
) -> Union[Callable[..., Any], McpToolset]:
    if isinstance(cfg, BuiltinToolConfig):
        if cfg.name is not None:
            tool = BUILTIN_TOOLS.get(cfg.name)
            if tool is None:
                available = ", ".join(sorted(BUILTIN_TOOLS))
                raise ValueError(
                    f"Tool '{name}': unknown builtin '{cfg.name}'. "
                    f"Available builtins: {available}"
                )
            return wrap_callable_tool(  # type: ignore[arg-type, return-value]
                name, tool
            )
        return _resolve_callable(name, cfg.ref)  # type: ignore[arg-type]
    if isinstance(cfg, PythonToolConfig):
        return _resolve_callable(name, cfg.ref)
    if isinstance(cfg, McpStdioToolConfig):
        params = StdioServerParameters(
            command=cfg.command,
            args=cfg.args,
            env=cfg.env or None,
        )
    elif isinstance(cfg, McpSseToolConfig):
        params = SseConnectionParams(
            url=cfg.url,
            headers=cfg.headers or None,
        )
    elif isinstance(cfg, McpHttpToolConfig):
        params = StreamableHTTPConnectionParams(
            url=cfg.url,
            headers=cfg.headers or None,
        )
    else:
        raise ValueError(
            f"Tool '{name}': unsupported config {type(cfg).__name__}"
        )
    return SafeMcpToolset(
        connection_params=params,
        tool_filter=cfg.tool_filter,
        tool_name_prefix=cfg.tool_name_prefix,
    )


def build_tool_registry(tools: dict[str, ToolConfig]) -> dict[str, Any]:
    """Resolve all tools from the YAML tools section."""
    return {name: resolve_tool(name, cfg) for name, cfg in tools.items()}
