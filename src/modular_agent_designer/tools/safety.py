"""Exception-safe wrappers for tools passed to ADK agents."""
from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

from google.adk.tools import BaseTool


_WRAPPED_ATTR = "__mad_exception_safe_tool__"


def tool_error(tool_name: str, exc: Exception) -> dict[str, str]:
    """Return a tool-call result that an agent can inspect and recover from."""
    return {
        "error": (
            f"Tool '{tool_name}' failed with {type(exc).__name__}: {exc}"
        )
    }


def wrap_callable_tool(
    tool_name: str, func: Callable[..., Any]
) -> Callable[..., Any]:
    """Wrap a Python callable tool so invocation exceptions become results."""
    if getattr(func, _WRAPPED_ATTR, False):
        return func

    has_async_call = hasattr(func, "__call__") and inspect.iscoroutinefunction(
        func.__call__
    )
    is_async = inspect.iscoroutinefunction(func) or has_async_call

    if is_async:

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                return tool_error(tool_name, exc)

        wrapper: Callable[..., Any] = async_wrapper
    else:

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                return tool_error(tool_name, exc)

        wrapper = sync_wrapper

    try:
        signature = inspect.signature(func)
        wrapper.__signature__ = signature  # type: ignore[attr-defined]
    except (TypeError, ValueError):
        pass
    if not hasattr(func, "__name__") and hasattr(func, "__class__"):
        wrapper.__name__ = func.__class__.__name__
        wrapper.__qualname__ = func.__class__.__qualname__
    if not wrapper.__doc__ and hasattr(func, "__call__"):
        wrapper.__doc__ = getattr(func.__call__, "__doc__", None)
    setattr(wrapper, _WRAPPED_ATTR, True)
    return wrapper


def wrap_adk_base_tool(tool: BaseTool) -> BaseTool:
    """Wrap an ADK BaseTool instance in place so run_async returns errors."""
    if getattr(tool, _WRAPPED_ATTR, False):
        return tool

    original_run_async = tool.run_async
    tool_name = tool.name

    async def safe_run_async(
        *, args: dict[str, Any], tool_context: Any
    ) -> Any:
        try:
            return await original_run_async(
                args=args, tool_context=tool_context
            )
        except Exception as exc:
            return tool_error(tool_name, exc)

    tool.run_async = safe_run_async  # type: ignore[method-assign]
    setattr(tool, _WRAPPED_ATTR, True)
    return tool
