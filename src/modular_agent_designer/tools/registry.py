"""Resolve tool references from YAML ToolConfig into callables or toolsets."""
from __future__ import annotations

from typing import Any, Callable, Union

from google.adk.tools.mcp_tool import (
    McpToolset,
    SseConnectionParams,
    StreamableHTTPConnectionParams,
)
from mcp import StdioServerParameters

from ..config.schema import (
    BuiltinToolConfig,
    McpHttpToolConfig,
    McpSseToolConfig,
    McpStdioToolConfig,
    PythonToolConfig,
    ToolConfig,
)
from ..utils.imports import import_dotted_ref
from . import BUILTIN_TOOLS


def _resolve_callable(name: str, ref: str) -> Callable[..., Any]:
    obj = import_dotted_ref(ref, context=f"Tool '{name}'")
    if not callable(obj):
        raise TypeError(
            f"Tool '{name}': ref '{ref}' resolved to "
            f"{type(obj).__name__}, which is not callable. "
            "Tool refs must point at a function or callable instance."
        )
    return obj


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
            return tool  # type: ignore[return-value]
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
    return McpToolset(
        connection_params=params,
        tool_filter=cfg.tool_filter,
        tool_name_prefix=cfg.tool_name_prefix,
    )


def build_tool_registry(tools: dict[str, ToolConfig]) -> dict[str, Any]:
    """Resolve all tools from the YAML tools section."""
    return {name: resolve_tool(name, cfg) for name, cfg in tools.items()}
