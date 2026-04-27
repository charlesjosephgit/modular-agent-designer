"""Compile a RootConfig into a runnable ADK Workflow."""
from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from typing import Any

from google.adk import Workflow
from google.adk.events.event import Event as AdkEvent
from google.adk.workflow import Edge, START, node as adk_node

from ..config.schema import (
    AgentConfig,
    EdgeConfig,
    EvalCondition,
    LoopConfig,
    NodeRefConfig,
    RootConfig,
)
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

# State key prefix for loop iteration counters.
_LOOP_ITER_PREFIX = "_loop_"


def _loop_iter_key(from_: str, to: str) -> str:
    """Return the state key used to track loop iteration count."""
    return f"{_LOOP_ITER_PREFIX}{from_}_{to}_iter"


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

    # Expand fan-out edges (to: [list]) into individual scalar edges,
    # and inject join-barrier nodes where needed.
    expanded_edges = _expand_list_edges(cfg.workflow.edges, node_callables)

    # Separate on_error edges from normal edges.
    normal_edges = [e for e in expanded_edges if not e.on_error]
    error_edges = [e for e in expanded_edges if e.on_error]

    # Identify sources that have on_error edges — these need unified routing.
    error_src_names: set[str] = {e.from_ for e in error_edges}

    # Group ALL edges by source node.
    all_edges_by_src: dict[str, list] = defaultdict(list)
    for edge_cfg in expanded_edges:
        all_edges_by_src[edge_cfg.from_].append(edge_cfg)

    # Group normal-only edges by source (for nodes WITHOUT on_error edges).
    normal_edges_by_src: dict[str, list] = defaultdict(list)
    for edge_cfg in normal_edges:
        if edge_cfg.from_ not in error_src_names:
            normal_edges_by_src[edge_cfg.from_].append(edge_cfg)

    # --- Wire nodes WITHOUT on_error edges (original logic) ---
    for src_name, src_edges in normal_edges_by_src.items():
        src_node = node_callables[src_name]
        conditional = [e for e in src_edges if e.condition is not None]
        unconditional = [e for e in src_edges if e.condition is None]

        # Wire unconditional edges directly.
        for edge_cfg in unconditional:
            assert isinstance(edge_cfg.to, str)
            dst = node_callables[edge_cfg.to]
            adk_edges.append(Edge(from_node=src_node, to_node=dst))

        if conditional:
            # Collect loop configs for conditional edges.
            loop_configs: dict[int, LoopConfig] = {}
            for i, edge_cfg in enumerate(conditional):
                if edge_cfg.loop is not None:
                    loop_configs[i] = edge_cfg.loop

            # Build a map of destination name → route label for dedup.
            dst_to_route: dict[str, str] = {}
            for i, edge_cfg in enumerate(conditional):
                assert isinstance(edge_cfg.to, str)
                dst_to_route[edge_cfg.to] = f"_route_{i}"

            exhausted_route_map: dict[int, str] = {}
            for i, loop_cfg in loop_configs.items():
                if loop_cfg.on_exhausted is not None:
                    if loop_cfg.on_exhausted in dst_to_route:
                        exhausted_route_map[i] = dst_to_route[loop_cfg.on_exhausted]
                    else:
                        exhausted_route_map[i] = f"_exhausted_{i}"

            router = _build_router_node(
                src_name, conditional, loop_configs, exhausted_route_map,
            )
            adk_edges.append(Edge(from_node=src_node, to_node=router))

            for i, edge_cfg in enumerate(conditional):
                assert isinstance(edge_cfg.to, str)
                dst = node_callables[edge_cfg.to]
                adk_edges.append(
                    Edge(from_node=router, to_node=dst, route=f"_route_{i}")
                )

            for i, route_label in exhausted_route_map.items():
                loop_cfg = loop_configs[i]
                if loop_cfg.on_exhausted is not None:
                    if loop_cfg.on_exhausted in dst_to_route:
                        continue
                    exhausted_dst = node_callables[loop_cfg.on_exhausted]
                    adk_edges.append(
                        Edge(
                            from_node=router,
                            to_node=exhausted_dst,
                            route=route_label,
                        )
                    )

    # --- Wire nodes WITH on_error edges (unified error-aware router) ---
    for src_name in error_src_names:
        src_node = node_callables[src_name]
        src_all_edges = all_edges_by_src[src_name]
        src_normal = [e for e in src_all_edges if not e.on_error]
        src_errors = [e for e in src_all_edges if e.on_error]

        # Build a unified error-aware router.
        error_router = _build_unified_error_router(
            src_name, src_normal, src_errors,
        )
        adk_edges.append(Edge(from_node=src_node, to_node=error_router))

        # Normal targets get _ok_N routes.
        for i, edge_cfg in enumerate(src_normal):
            assert isinstance(edge_cfg.to, str)
            dst = node_callables[edge_cfg.to]
            adk_edges.append(
                Edge(from_node=error_router, to_node=dst, route=f"_ok_{i}")
            )

        # Error targets get _error_N routes.
        for i, edge_cfg in enumerate(src_errors):
            assert isinstance(edge_cfg.to, str)
            dst = node_callables[edge_cfg.to]
            adk_edges.append(
                Edge(from_node=error_router, to_node=dst, route=f"_error_{i}")
            )

    return Workflow(
        name=cfg.name,
        edges=adk_edges,
    )


def _expand_list_edges(
    edges: list[EdgeConfig],
    node_callables: dict[str, Any],
) -> list[EdgeConfig]:
    """Expand fan-out edges (to: [list]) into individual scalar edges.

    For each fan-out edge with ``to: [a, b, c]``, creates individual edges
    ``from → a``, ``from → b``, ``from → c``.

    If the edge has a ``join`` target, injects a join-barrier node into
    ``node_callables`` and adds edges from each fan-out target to the barrier.
    """
    expanded: list[EdgeConfig] = []

    for edge in edges:
        if isinstance(edge.to, str):
            expanded.append(edge)
            continue

        # Fan-out: to is a list.
        fan_out_targets = edge.to

        # Create individual edges for each target.
        for target in fan_out_targets:
            expanded.append(
                EdgeConfig(
                    from_=edge.from_,
                    to=target,
                    condition=None,
                    on_error=False,
                )
            )

        # If join is specified, inject a join-barrier node.
        if edge.join is not None:
            join_name = f"_join_{edge.from_}_{'_'.join(fan_out_targets)}"
            join_node = _build_join_node(
                join_name, fan_out_targets, edge.join
            )
            node_callables[join_name] = join_node

            # Each fan-out target → join barrier (unconditional).
            for target in fan_out_targets:
                expanded.append(
                    EdgeConfig(from_=target, to=join_name, condition=None)
                )

            # Join barrier → actual join target (unconditional).
            expanded.append(
                EdgeConfig(from_=join_name, to=edge.join, condition=None)
            )

    return expanded


def _build_join_node(
    join_name: str,
    source_nodes: list[str],
    join_target: str,
) -> Any:
    """Build a @node that waits until all source nodes have written output to state.

    The join node checks ``ctx.state`` for keys matching each source node name.
    If any source hasn't written output yet, the join node yields an event
    without routing — effectively blocking until the next invocation when
    all sources have completed.
    """

    async def _join(ctx: Any, node_input: Any):
        state_dict = (
            ctx.state.to_dict()
            if hasattr(ctx.state, "to_dict")
            else dict(ctx.state)
        )

        # Check that all source nodes have produced output.
        missing = [
            name for name in source_nodes
            if name not in state_dict or state_dict[name] is None
        ]

        if missing:
            logger.info(
                "join '%s': waiting for %d source(s): %s",
                join_name, len(missing), missing,
            )
            # Don't route — the workflow will re-enter when more sources complete.
            return

        logger.info(
            "join '%s': all %d sources completed → proceeding to '%s'",
            join_name, len(source_nodes), join_target,
        )
        yield AdkEvent(output=f"join_complete:{join_target}")

    _join.__name__ = join_name
    _join.__qualname__ = join_name

    return adk_node()(_join)


def _build_router_node(
    src_name: str,
    conditional_edges: list,
    loop_configs: dict[int, LoopConfig] | None = None,
    exhausted_route_map: dict[int, str] | None = None,
) -> Any:
    """Build a @node that evaluates edge conditions and emits Event(route=...).

    ADK requires nodes to yield ``Event(route=value)`` for downstream edge
    matching.  Simply returning a string only sets ``Event.output``, not the
    ``actions.route`` field that the workflow engine checks.

    When ``loop_configs`` is provided, the router also tracks iteration counters
    in state and enforces ``max_iterations``. If a loop edge exceeds its limit,
    the router either routes to ``on_exhausted`` (if configured) or skips the
    edge (letting subsequent conditions or default handle it).
    """
    if loop_configs is None:
        loop_configs = {}
    if exhausted_route_map is None:
        exhausted_route_map = {}

    # Separate default (fallback) from non-default conditions.
    # Default is always checked last.
    non_default: list[tuple[int, Any]] = []
    default_idx: int | None = None

    for i, edge_cfg in enumerate(conditional_edges):
        if edge_cfg.condition == "__DEFAULT__":
            default_idx = i
        else:
            non_default.append((i, edge_cfg.condition))

    # Pre-compute loop state keys for edges with loop configs.
    loop_state_keys: dict[int, str] = {}
    for i, loop_cfg in loop_configs.items():
        assert isinstance(conditional_edges[i].to, str)
        loop_state_keys[i] = _loop_iter_key(src_name, conditional_edges[i].to)

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
                # Check loop iteration limit if applicable.
                if idx in loop_configs:
                    loop_cfg = loop_configs[idx]
                    iter_key = loop_state_keys[idx]
                    current_iter = int(state_dict.get(iter_key, 0))

                    if current_iter >= loop_cfg.max_iterations:
                        logger.info(
                            "router '%s': loop edge _route_%d exhausted "
                            "(%d/%d iterations)",
                            src_name, idx, current_iter,
                            loop_cfg.max_iterations,
                        )
                        if loop_cfg.on_exhausted is not None:
                            route_label = exhausted_route_map.get(
                                idx, f"_exhausted_{idx}"
                            )
                            yield AdkEvent(
                                route=route_label,
                                output=raw_output,
                                state={iter_key: 0},  # reset counter
                            )
                            return
                        # No on_exhausted — skip this edge and continue
                        # checking other conditions.
                        continue

                    # Increment the iteration counter.
                    logger.info(
                        "router '%s': loop iteration %d/%d → route _route_%d",
                        src_name, current_iter + 1,
                        loop_cfg.max_iterations, idx,
                    )
                    yield AdkEvent(
                        route=f"_route_{idx}",
                        output=raw_output,
                        state={iter_key: current_iter + 1},
                    )
                    return

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


def _build_unified_error_router(
    src_name: str,
    normal_edges: list[EdgeConfig],
    error_edges: list[EdgeConfig],
) -> Any:
    """Build a @node that routes to success OR error handlers.

    Checks for an error marker in state (``_error_{src_name}``) written by the
    retry wrapper in agent_node.py:
    - If error exists → routes to error handlers via ``_error_N``
    - If no error → routes to normal handlers via ``_ok_N``

    This ensures that when a node has both normal and on_error edges,
    only ONE path fires (not both).
    """

    async def _error_router(ctx: Any, node_input: Any):
        state_dict = (
            ctx.state.to_dict()
            if hasattr(ctx.state, "to_dict")
            else dict(ctx.state)
        )
        error_key = f"_error_{src_name}"
        error_info = state_dict.get(error_key)

        if error_info is not None:
            logger.info(
                "error_router '%s': error detected → routing to error handler",
                src_name,
            )
            yield AdkEvent(route="_error_0", output=str(error_info))
        else:
            logger.info(
                "error_router '%s': no error → routing to success handler",
                src_name,
            )
            raw_output = state_dict.get(src_name, "")
            yield AdkEvent(route="_ok_0", output=raw_output)

    _error_router.__name__ = f"{src_name}_error_router"
    _error_router.__qualname__ = f"{src_name}_error_router"

    return adk_node()(_error_router)


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
# Node-building helpers
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
