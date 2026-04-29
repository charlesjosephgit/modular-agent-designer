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
import threading
from collections import OrderedDict
from typing import Any, AsyncGenerator

from google.adk import Agent, Context
from google.adk.models.lite_llm import LiteLlm
from google.adk.workflow import node as adk_node

from ..config.schema import AgentConfig, AgentGenerateContentConfig, AgentThinkingConfig
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
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            else:
                if len(self._data) >= self._maxsize:
                    self._data.popitem(last=False)
            self._data[key] = value

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


def build_sub_agent(
    agent_name: str,
    cfg: AgentConfig,
    model: LiteLlm,
    tools: list[Any],
    sub_agents: list[Any] | None = None,
    skill_toolset: Any = None,
) -> Agent:
    """Build a plain ADK Agent for use as a sub-agent (not a workflow node).

    Sub-agents have no output_key (they don't write to workflow state directly)
    and no after_model_callback (thinking capture is for workflow nodes only).
    Their instructions are static — {{state.x}} templates are not supported.
    """
    sub_agents = sub_agents or []
    all_tools = tools + [skill_toolset] if skill_toolset is not None else tools
    agent_kwargs: dict[str, Any] = dict(
        name=agent_name,
        model=model,
        tools=all_tools,
        sub_agents=sub_agents,
    )
    if cfg.instruction is not None:
        agent_kwargs["instruction"] = cfg.instruction
    if cfg.description is not None:
        agent_kwargs["description"] = cfg.description
    if cfg.static_instruction is not None:
        agent_kwargs["static_instruction"] = cfg.static_instruction
    if cfg.mode is not None:
        agent_kwargs["mode"] = cfg.mode
    if cfg.include_contents != "default":
        agent_kwargs["include_contents"] = cfg.include_contents
    if cfg.disallow_transfer_to_parent:
        agent_kwargs["disallow_transfer_to_parent"] = True
    if cfg.disallow_transfer_to_peers:
        agent_kwargs["disallow_transfer_to_peers"] = True
    if cfg.parallel_worker is not None:
        agent_kwargs["parallel_worker"] = cfg.parallel_worker
    if cfg.input_schema is not None:
        agent_kwargs["input_schema"] = _load_input_schema(cfg.input_schema)
    if cfg.output_schema is not None:
        agent_kwargs["output_schema"] = _load_output_schema(cfg.output_schema)
    if cfg.output_key is not None:
        agent_kwargs["output_key"] = cfg.output_key
    if cfg.generate_content_config is not None:
        agent_kwargs["generate_content_config"] = _build_generate_content_config(
            cfg.generate_content_config
        )
    if cfg.thinking is not None:
        agent_kwargs["planner"] = _build_planner(cfg.thinking)
    return Agent(**agent_kwargs)


def build_agent_node(
    agent_name: str,
    cfg: AgentConfig,
    model: LiteLlm,
    tools: list[Any],
    sub_agents: list[Any] | None = None,
    skill_toolset: Any = None,
    handles_errors: bool = False,
) -> Any:
    """Return an ADK-compatible node for a single YAML agent entry."""
    sub_agents = sub_agents or []
    all_tools = tools + [skill_toolset] if skill_toolset is not None else tools
    instruction_template = cfg.instruction
    output_schema_class = _load_output_schema(cfg.output_schema)
    input_schema_class = _load_input_schema(cfg.input_schema)
    generate_content_config = (
        _build_generate_content_config(cfg.generate_content_config)
        if cfg.generate_content_config is not None
        else None
    )
    planner = _build_planner(cfg.thinking) if cfg.thinking is not None else None
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
        resolved_instruction = (
            resolve(instruction_template, state_dict)
            if instruction_template is not None
            else None
        )

        logger.info("node '%s' start", agent_name)

        cache_key = resolved_instruction or ""
        agent = _agent_cache.get(cache_key)
        if agent is None:
            agent_kwargs: dict[str, Any] = dict(
                name=agent_name,
                model=model,
                tools=all_tools,
                output_key=cfg.output_key if cfg.output_key is not None else agent_name,
                after_model_callback=make_capture_thinking_callback(agent_name),
                sub_agents=sub_agents,
            )
            if resolved_instruction is not None:
                agent_kwargs["instruction"] = resolved_instruction
            if cfg.description is not None:
                agent_kwargs["description"] = cfg.description
            if cfg.static_instruction is not None:
                agent_kwargs["static_instruction"] = cfg.static_instruction
            if cfg.mode is not None:
                agent_kwargs["mode"] = cfg.mode
            elif tools or sub_agents:
                agent_kwargs["mode"] = "chat"
            if cfg.include_contents != "default":
                agent_kwargs["include_contents"] = cfg.include_contents
            if input_schema_class is not None:
                agent_kwargs["input_schema"] = input_schema_class
            if output_schema_class is not None:
                agent_kwargs["output_schema"] = output_schema_class
            if generate_content_config is not None:
                agent_kwargs["generate_content_config"] = generate_content_config
            if planner is not None:
                agent_kwargs["planner"] = planner
            agent = Agent(**agent_kwargs)
            _agent_cache.set(cache_key, agent)
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
                run_coro = ctx.run_node(agent, node_input=node_input)
                if cfg.timeout_seconds is not None:
                    result = await asyncio.wait_for(
                        run_coro, timeout=cfg.timeout_seconds
                    )
                else:
                    result = await run_coro
                effective_output_key = cfg.output_key if cfg.output_key is not None else agent_name
                output = state_dict.get(effective_output_key, "")
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

        if not handles_errors:
            assert last_exc is not None
            raise last_exc

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


def _load_input_schema(ref: str | None):
    """Dynamically import a Pydantic BaseModel subclass by dotted path, or return None."""
    if ref is None:
        return None
    return import_dotted_ref(ref, context=f"input_schema '{ref}'")


def _build_generate_content_config(acc: AgentGenerateContentConfig):
    """Build a google.genai.types.GenerateContentConfig from schema config."""
    from google.genai import types as genai_types
    kwargs: dict = {}
    if acc.temperature is not None:
        kwargs["temperature"] = acc.temperature
    if acc.top_p is not None:
        kwargs["top_p"] = acc.top_p
    if acc.top_k is not None:
        kwargs["top_k"] = acc.top_k
    if acc.max_output_tokens is not None:
        kwargs["max_output_tokens"] = acc.max_output_tokens
    if acc.candidate_count is not None:
        kwargs["candidate_count"] = acc.candidate_count
    if acc.stop_sequences is not None:
        kwargs["stop_sequences"] = acc.stop_sequences
    if acc.seed is not None:
        kwargs["seed"] = acc.seed
    if acc.presence_penalty is not None:
        kwargs["presence_penalty"] = acc.presence_penalty
    if acc.frequency_penalty is not None:
        kwargs["frequency_penalty"] = acc.frequency_penalty
    if acc.safety_settings is not None:
        kwargs["safety_settings"] = [
            genai_types.SafetySetting(
                category=ss.category,
                threshold=ss.threshold,
            )
            for ss in acc.safety_settings
        ]
    if acc.cached_content is not None:
        kwargs["cached_content"] = acc.cached_content
    if acc.response_mime_type is not None:
        kwargs["response_mime_type"] = acc.response_mime_type
    return genai_types.GenerateContentConfig(**kwargs)


def _build_planner(thinking_cfg: AgentThinkingConfig):
    """Build a BuiltInPlanner from per-agent thinking config."""
    from google.adk.planners import BuiltInPlanner
    from google.genai import types as genai_types
    thinking_config = genai_types.ThinkingConfig(
        include_thoughts=thinking_cfg.include_thoughts,
        thinking_budget=thinking_cfg.thinking_budget,
    )
    return BuiltInPlanner(thinking_config=thinking_config)
