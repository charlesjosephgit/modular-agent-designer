"""Wrap a YAML AgentConfig into an ADK node callable.

Each wrapper is a @node(rerun_on_resume=True) async generator that:
  1. Converts ctx.state (ADK State object) to a plain dict.
  2. Resolves {{state.x.y}} templates in the instruction.
  3. Constructs a fresh Agent with the resolved instruction.
  4. Calls ctx.run_node(agent, ...) — requires rerun_on_resume=True on caller.
  5. Yields the result; Agent's output_key writes it to ctx.state[agent_name].
"""
from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk import Agent, Context
from google.adk.models.lite_llm import LiteLlm
from google.adk.workflow import node as adk_node

from ..config.schema import AgentConfig
from ..plugins.thinking import make_capture_thinking_callback
from ..state.template import resolve
from ..utils.imports import import_dotted_ref


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
    # Cache Agent instances keyed by resolved instruction.  rerun_on_resume
    # re-invokes this entire generator on every tool-call cycle; recreating
    # Agent each time triggers McpToolset.get_tools() → list_tools() RPC for
    # every turn.  Caching avoids that round-trip while staying correct across
    # workflow invocations with different state (different cache key).
    _agent_cache: dict[str, Agent] = {}

    async def _wrapper(ctx: Context, node_input: Any) -> AsyncGenerator:
        if not hasattr(ctx.state, "to_dict"):
            raise RuntimeError(
                f"Agent '{agent_name}': ADK state object has no to_dict() method "
                f"(got {type(ctx.state).__name__}). This indicates an ADK version mismatch."
            )
        state_dict = ctx.state.to_dict()
        resolved_instruction = resolve(instruction_template, state_dict)

        if resolved_instruction not in _agent_cache:
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
                agent_kwargs["mode"] = "task"
            if output_schema_class is not None:
                agent_kwargs["output_schema"] = output_schema_class
            _agent_cache[resolved_instruction] = Agent(**agent_kwargs)

        result = await ctx.run_node(_agent_cache[resolved_instruction], node_input=node_input)
        yield result

    _wrapper.__name__ = agent_name
    _wrapper.__qualname__ = agent_name

    # rerun_on_resume=True is required by ADK when the node calls ctx.run_node.
    return adk_node(rerun_on_resume=True)(_wrapper)


def _load_output_schema(ref: str | None):
    """Dynamically import a Pydantic class by dotted path, or return None."""
    if ref is None:
        return None
    return import_dotted_ref(ref, context=f"output_schema '{ref}'")
