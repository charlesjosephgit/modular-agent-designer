"""Compile a RootConfig into a runnable ADK Workflow."""
from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from typing import Any

from google.adk import Workflow
from google.adk.events.event import Event as AdkEvent
from google.adk.workflow import Edge, START, node as adk_node

from ..config.schema import AgentConfig, EvalCondition, NodeRefConfig, RootConfig
from ..models.registry import build_model_registry
from ..nodes.agent_node import build_agent_node, build_sub_agent
from ..nodes.custom import build_custom_node
from ..skills.registry import build_skill_registry, build_skill_toolset
from ..tools.registry import build_tool_registry

logger = logging.getLogger(__name__)

_SAFE_BUILTINS = {
    "len": len, "int": int, "float": float, "str": str, "bool": bool,
    "abs": abs, "min": min, "max": max, "any": any, "all": all,
    "isinstance": isinstance, "sorted": sorted, "sum": sum, "range": range,
    "list": list, "dict": dict, "set": set, "tuple": tuple,
    "enumerate": enumerate, "zip": zip, "reversed": reversed, "round": round,
}


def build_workflow(cfg: RootConfig) -> Workflow:
    """Build a runnable Workflow from a validated RootConfig."""
    model_registry = build_model_registry(cfg.models)
    tool_registry = build_tool_registry(cfg.tools)
    skill_registry = build_skill_registry(cfg.skills)

    node_callables = _build_node_callables(
        cfg, model_registry, tool_registry, skill_registry
    )

    adk_edges: list[Any] = []

    # Connect START to the entry node.
    if cfg.workflow.entry not in node_callables:
        raise ValueError(f"Entry node '{cfg.workflow.entry}' not defined.")

    entry_node = node_callables[cfg.workflow.entry]
    adk_edges.append(Edge(from_node=START, to_node=entry_node))

    # Group edges by source node.
    edges_by_src: dict[str, list] = defaultdict(list)
    for edge_cfg in cfg.workflow.edges:
        edges_by_src[edge_cfg.from_].append(edge_cfg)

    # Connect defined edges, injecting router nodes for conditional branches.
    for src_name, src_edges in edges_by_src.items():
        src_node = node_callables[src_name]
        conditional = [e for e in src_edges if e.condition is not None]
        unconditional = [e for e in src_edges if e.condition is None]

        # Wire unconditional edges directly.
        for edge_cfg in unconditional:
            dst = node_callables[edge_cfg.to]
            adk_edges.append(Edge(from_node=src_node, to_node=dst))

        if conditional:
            # Inject a router node for conditional routing.
            router = _build_router_node(src_name, conditional)

            # Source → Router (unconditional — always triggered after source)
            adk_edges.append(Edge(from_node=src_node, to_node=router))

            # Router → each destination (deterministic route labels)
            for i, edge_cfg in enumerate(conditional):
                dst = node_callables[edge_cfg.to]
                adk_edges.append(
                    Edge(from_node=router, to_node=dst, route=f"_route_{i}")
                )

    return Workflow(
        name=cfg.name,
        edges=adk_edges,
    )


def _build_router_node(src_name: str, conditional_edges: list) -> Any:
    """Build a @node that evaluates edge conditions and emits Event(route=...).

    ADK requires nodes to yield ``Event(route=value)`` for downstream edge
    matching.  Simply returning a string only sets ``Event.output``, not the
    ``actions.route`` field that the workflow engine checks.
    """
    # Separate default (fallback) from non-default conditions.
    # Default is always checked last.
    non_default: list[tuple[int, Any]] = []
    default_idx: int | None = None

    for i, edge_cfg in enumerate(conditional_edges):
        if edge_cfg.condition == "__DEFAULT__":
            default_idx = i
        else:
            non_default.append((i, edge_cfg.condition))

    async def _router(ctx: Any, node_input: Any):
        state_dict = (
            ctx.state.to_dict()
            if hasattr(ctx.state, "to_dict")
            else dict(ctx.state)
        )
        raw_output = state_dict.get(src_name, "")
        output = str(raw_output).strip() if raw_output is not None else ""

        # Evaluate non-default conditions in declaration order.
        for idx, condition in non_default:
            if _matches(condition, output, state_dict, raw_output):
                logger.info(
                    "router '%s': matched condition %r → route _route_%d",
                    src_name, condition, idx,
                )
                yield AdkEvent(route=f"_route_{idx}", output=raw_output)
                return

        # Fall back to default if nothing matched.
        if default_idx is not None:
            logger.info(
                "router '%s': no condition matched → default route _route_%d",
                src_name, default_idx,
            )
            yield AdkEvent(route=f"_route_{default_idx}", output=raw_output)
        else:
            logger.info(
                "router '%s': no condition matched and no default — workflow terminates",
                src_name,
            )

    _router.__name__ = f"{src_name}_router"
    _router.__qualname__ = f"{src_name}_router"

    return adk_node()(_router)


def _matches(
    condition: Any,
    output: str,
    state_dict: dict,
    raw_output: Any,
) -> bool:
    """Check whether *output* satisfies *condition*."""
    if isinstance(condition, EvalCondition):
        try:
            return bool(
                eval(
                    condition.eval,
                    {"__builtins__": _SAFE_BUILTINS, "re": re},
                    {
                        "state": state_dict,
                        "input": output,
                        "raw_input": raw_output,
                    },
                )
            )
        except (KeyError, AttributeError, IndexError, TypeError) as exc:
            logger.warning(
                "eval condition %r failed (%s: %s) — treating as False",
                condition.eval, type(exc).__name__, exc,
            )
            return False

    if isinstance(condition, list):
        return output in [str(v).strip() for v in condition]

    # Scalar: exact string match.
    return output == str(condition).strip()


# ---------------------------------------------------------------------------
# Node-building helpers (unchanged)
# ---------------------------------------------------------------------------


def _topological_sort_agents(
    agents: dict[str, Any],
) -> list[str]:
    """Return agent names in build order: sub-agents (leaves) before parents.

    Uses Kahn's algorithm. Assumes the graph is a DAG — cycle detection is
    already enforced by schema validation.
    """
    # in_degree[name] = number of sub-agents that name depends on
    in_degree: dict[str, int] = {name: 0 for name in agents}
    # dependents[child] = list of parents that list child as a sub-agent
    dependents: dict[str, list[str]] = {name: [] for name in agents}

    for name, cfg in agents.items():
        if isinstance(cfg, AgentConfig) and cfg.sub_agents:
            in_degree[name] = len(cfg.sub_agents)
            for sa in cfg.sub_agents:
                dependents[sa].append(name)

    queue: deque[str] = deque(
        name for name, deg in in_degree.items() if deg == 0
    )
    order: list[str] = []

    while queue:
        name = queue.popleft()
        order.append(name)
        for parent in dependents[name]:
            in_degree[parent] -= 1
            if in_degree[parent] == 0:
                queue.append(parent)

    return order


def _build_node_callables(
    cfg: RootConfig,
    model_registry: dict,
    tool_registry: dict,
    skill_registry: dict,
) -> dict[str, Any]:
    workflow_node_names = set(cfg.workflow.nodes)

    # Collect all names that appear as sub-agents.
    all_sub_agent_names: set[str] = set()
    for agent_cfg in cfg.agents.values():
        if isinstance(agent_cfg, AgentConfig):
            all_sub_agent_names.update(agent_cfg.sub_agents)

    # Build in topological order so sub-agents are ready before their parents.
    build_order = _topological_sort_agents(cfg.agents)

    # built_agents stores plain Agent instances for sub-agents so parents can
    # reference them.  callables stores @node-wrapped callables for workflow nodes.
    built_agents: dict[str, Any] = {}
    callables: dict[str, Any] = {}

    for agent_name in build_order:
        agent_cfg = cfg.agents[agent_name]

        if isinstance(agent_cfg, NodeRefConfig):
            node = build_custom_node(agent_name, agent_cfg)
            callables[agent_name] = node
            continue

        # AgentConfig path.
        model = model_registry[agent_cfg.model]
        tools = [tool_registry[t] for t in agent_cfg.tools]
        resolved_sub_agents = [built_agents[sa] for sa in agent_cfg.sub_agents]
        skill_toolset = build_skill_toolset(agent_cfg.skills, skill_registry)

        if agent_name in all_sub_agent_names:
            # Build as a plain Agent — not a workflow node.
            built_agents[agent_name] = build_sub_agent(
                agent_name, agent_cfg, model, tools, resolved_sub_agents,
                skill_toolset,
            )
        else:
            # Build as a @node-wrapped workflow node.
            node = build_agent_node(
                agent_name, agent_cfg, model, tools, resolved_sub_agents,
                skill_toolset,
            )
            built_agents[agent_name] = node
            callables[agent_name] = node

    return callables
