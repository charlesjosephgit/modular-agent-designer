"""Wrap a YAML AgentConfig into an ADK node callable.

Each wrapper is a @node(rerun_on_resume=True) async generator that:
  1. Converts ctx.state (ADK State object) to a plain dict.
  2. Resolves {{state.x.y}} templates in the instruction.
  3. Constructs a fresh Agent with the resolved instruction (cached by LRU).
  4. Calls ctx.run_node(agent, ...) — requires rerun_on_resume=True on caller.
  5. Yields the result; Agent's output_key writes it to ctx.state[agent_name].

Optionally wraps the call in a retry loop when the agent has a ``retry`` config.
"""
from __future__ import annotations

import asyncio

import logging
from collections import OrderedDict
from typing import Any, AsyncGenerator

from google.adk import Agent, Context
from google.adk.models.lite_llm import LiteLlm
from google.adk.workflow import node as adk_node

from ..config.schema import AgentConfig
from ..plugins.thinking import make_capture_thinking_callback
from ..state.template import resolve
from ..utils.imports import import_dotted_ref

logger = logging.getLogger(__name__)

_AGENT_CACHE_MAXSIZE = 32


class _LRUCache:
    """Minimal OrderedDict-based LRU cache with a fixed capacity."""

    def __init__(self, maxsize: int = _AGENT_CACHE_MAXSIZE) -> None:
        self._maxsize = maxsize
        self._data: OrderedDict[str, Any] = OrderedDict()

    def get(self, key: str) -> Any | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def set(self, key: str, value: Any) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        else:
            if len(self._data) >= self._maxsize:
                self._data.popitem(last=False)
        self._data[key] = value

    def __len__(self) -> int:
        return len(self._data)


def build_sub_agent(
    agent_name: str,
    cfg: AgentConfig,
    model: LiteLlm,
    tools: list[Any],
    sub_agents: list[Any] = [],
    skill_toolset: Any = None,
) -> Agent:
    """Build a plain ADK Agent for use as a sub-agent (not a workflow node).

    Sub-agents have no output_key (they don't write to workflow state directly)
    and no after_model_callback (thinking capture is for workflow nodes only).
    Their instructions are static — {{state.x}} templates are not supported.
    """
    all_tools = tools + [skill_toolset] if skill_toolset is not None else tools
    agent_kwargs: dict[str, Any] = dict(
        name=agent_name,
        instruction=cfg.instruction,
        model=model,
        tools=all_tools,
        sub_agents=sub_agents,
    )
    if cfg.mode is not None:
        agent_kwargs["mode"] = cfg.mode
    if cfg.include_contents != "default":
        agent_kwargs["include_contents"] = cfg.include_contents
    if cfg.disallow_transfer_to_parent:
        agent_kwargs["disallow_transfer_to_parent"] = True
    if cfg.disallow_transfer_to_peers:
        agent_kwargs["disallow_transfer_to_peers"] = True
    if cfg.output_schema is not None:
        agent_kwargs["output_schema"] = _load_output_schema(cfg.output_schema)
    return Agent(**agent_kwargs)


def build_agent_node(
    agent_name: str,
    cfg: AgentConfig,
    model: LiteLlm,
    tools: list[Any],
    sub_agents: list[Any] = [],
    skill_toolset: Any = None,
) -> Any:
    """Return an ADK-compatible node for a single YAML agent entry."""
    all_tools = tools + [skill_toolset] if skill_toolset is not None else tools
    instruction_template = cfg.instruction
    output_schema_class = _load_output_schema(cfg.output_schema)
    # LRU-bounded cache of Agent instances keyed by resolved instruction.
    # rerun_on_resume re-invokes this generator on every tool-call cycle;
    # recreating Agent each time triggers McpToolset.get_tools() → list_tools()
    # RPC for every turn.  Caching avoids that round-trip while staying correct
    # across workflow invocations with different state (different cache key).
    # Bounded at _AGENT_CACHE_MAXSIZE entries to prevent unbounded growth in
    # long-running processes where state values vary widely.
    _agent_cache: _LRUCache = _LRUCache()

    async def _wrapper(ctx: Context, node_input: Any) -> AsyncGenerator:
        if not hasattr(ctx.state, "to_dict"):
            raise RuntimeError(
                f"Agent '{agent_name}': ADK state object has no to_dict() method "
                f"(got {type(ctx.state).__name__}). This indicates an ADK version mismatch."
            )
        state_dict = ctx.state.to_dict()
        resolved_instruction = resolve(instruction_template, state_dict)

        logger.info("node '%s' start", agent_name)

        agent = _agent_cache.get(resolved_instruction)
        if agent is None:
            agent_kwargs: dict[str, Any] = dict(
                name=agent_name,
                instruction=resolved_instruction,
                model=model,
                tools=all_tools,
                output_key=agent_name,
                after_model_callback=make_capture_thinking_callback(agent_name),
                sub_agents=sub_agents,
            )
            if cfg.mode is not None:
                agent_kwargs["mode"] = cfg.mode
            elif tools or sub_agents:
                agent_kwargs["mode"] = "chat"
            if cfg.include_contents != "default":
                agent_kwargs["include_contents"] = cfg.include_contents
            if output_schema_class is not None:
                agent_kwargs["output_schema"] = output_schema_class
            agent = Agent(**agent_kwargs)
            _agent_cache.set(resolved_instruction, agent)
            logger.debug(
                "node '%s': created new Agent instance (cache size=%d)",
                agent_name, len(_agent_cache),
            )

        # Retry wrapper: if retry config is set, catch exceptions and retry.
        retry_cfg = cfg.retry
        max_attempts = (retry_cfg.max_retries + 1) if retry_cfg else 1
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                result = await ctx.run_node(agent, node_input=node_input)
                output = state_dict.get(agent_name, "")
                logger.info(
                    "node '%s' done (output_len=%d)",
                    agent_name, len(str(output)),
                )
                yield result
                return
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts:
                    delay = _compute_retry_delay(retry_cfg, attempt)
                    logger.warning(
                        "node '%s': attempt %d/%d failed (%s: %s), "
                        "retrying in %.1fs",
                        agent_name, attempt, max_attempts,
                        type(exc).__name__, exc, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "node '%s': all %d attempts failed (%s: %s)",
                        agent_name, max_attempts,
                        type(exc).__name__, exc,
                    )

        # All retries exhausted — write error info to state for on_error routing.
        error_key = f"_error_{agent_name}"
        error_info = {
            "error_type": type(last_exc).__name__,
            "error_message": str(last_exc),
            "attempts": max_attempts,
        }
        from google.adk.events.event import Event as AdkEvent
        yield AdkEvent(state={error_key: error_info}, output=str(last_exc))

    _wrapper.__name__ = agent_name
    _wrapper.__qualname__ = agent_name

    # rerun_on_resume=True is required by ADK when the node calls ctx.run_node.
    return adk_node(rerun_on_resume=True)(_wrapper)


def _compute_retry_delay(retry_cfg, attempt: int) -> float:
    """Compute delay in seconds for the given retry attempt."""
    if retry_cfg is None:
        return 0
    if retry_cfg.backoff == "exponential":
        return retry_cfg.delay_seconds * (2 ** (attempt - 1))
    return retry_cfg.delay_seconds


def _load_output_schema(ref: str | None):
    """Dynamically import a Pydantic class by dotted path, or return None."""
    if ref is None:
        return None
    return import_dotted_ref(ref, context=f"output_schema '{ref}'")
