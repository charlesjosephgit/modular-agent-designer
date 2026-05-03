"""Compile a RootConfig into a runnable ADK Workflow."""
from __future__ import annotations

import ast
import logging
import re
from collections import defaultdict, deque
from typing import Any

from google.adk import Workflow
from google.adk.events.event import Event as AdkEvent
from google.adk.workflow import Edge, START, node as adk_node

from ..config.schema import (
    A2aAgentConfig,
    AgentConfig,
    EdgeConfig,
    EvalCondition,
    LoopConfig,
    NodeRefConfig,
    RootConfig,
    _is_dynamic_to,
)
from ..state.template import resolve as resolve_template
from ..models.registry import build_model_registry
from ..nodes.a2a import build_a2a_agent_node, build_remote_a2a_agent
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

_SAFE_ATTRS = {
    "get",
    "lower",
    "upper",
    "strip",
    "startswith",
    "endswith",
    "search",
    "match",
    "fullmatch",
    "IGNORECASE",
    "MULTILINE",
    "DOTALL",
}

_SAFE_AST_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.IfExp,
    ast.Compare,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.Subscript,
    ast.Slice,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.ListComp,
    ast.GeneratorExp,
    ast.comprehension,
    ast.Attribute,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
)


class _AttrView:
    """Read-only dot-access view over dict/list data for eval conditions."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if isinstance(self._value, dict):
            return _wrap_attr_value(self._value.get(name))
        raise AttributeError(name)

    def __getitem__(self, key: Any) -> Any:
        if isinstance(self._value, (dict, list, tuple)):
            return _wrap_attr_value(self._value[key])
        raise TypeError(f"{type(self._value).__name__!r} is not subscriptable")

    def get(self, key: Any, default: Any = None) -> Any:
        if isinstance(self._value, dict):
            return _wrap_attr_value(self._value.get(key, default))
        return default

    def __iter__(self):
        if isinstance(self._value, (list, tuple)):
            return iter(_wrap_attr_value(item) for item in self._value)
        if isinstance(self._value, dict):
            return iter(self._value)
        raise TypeError(f"{type(self._value).__name__!r} is not iterable")

    def __len__(self) -> int:
        if isinstance(self._value, (dict, list, tuple, str)):
            return len(self._value)
        raise TypeError(f"{type(self._value).__name__!r} has no len()")

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, _AttrView):
            return self._value == other._value
        return self._value == other

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)

    def __str__(self) -> str:
        return str(self._value)

    def __repr__(self) -> str:
        return repr(self._value)


def _wrap_attr_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return _AttrView(value)
    return value


class _SafeEvalValidator(ast.NodeVisitor):
    def __init__(self) -> None:
        self._names = {
            "state", "input", "output", "raw_input", "re",
            *_SAFE_BUILTINS.keys(),
        }
        self._bound_names: set[str] = set()

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, _SAFE_AST_NODES):
            raise ValueError(
                f"eval condition uses unsupported syntax: {type(node).__name__}"
            )
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self._bound_names.add(node.id)
            return
        if node.id not in self._names and node.id not in self._bound_names:
            raise ValueError(f"eval condition references unknown name '{node.id}'")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise ValueError(f"eval condition attribute '{node.attr}' is not allowed")
        if _is_attr_view_access(node):
            self.visit(node.value)
            return
        if node.attr not in _SAFE_ATTRS:
            raise ValueError(f"eval condition attribute '{node.attr}' is not allowed")
        self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id not in _SAFE_BUILTINS:
                raise ValueError(
                    f"eval condition call to '{node.func.id}' is not allowed"
                )
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr not in _SAFE_ATTRS:
                raise ValueError(
                    f"eval condition call to '{node.func.attr}' is not allowed"
                )
            self.visit(node.func)
        else:
            raise ValueError("eval condition call target is not allowed")
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.elt, node.generators)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.elt, node.generators)

    def _visit_comprehension(
        self, elt: ast.AST, generators: list[ast.comprehension]
    ) -> None:
        old_bound = set(self._bound_names)
        try:
            for gen in generators:
                self._collect_targets(gen.target)
                self.visit(gen.iter)
                for if_expr in gen.ifs:
                    self.visit(if_expr)
            self.visit(elt)
        finally:
            self._bound_names = old_bound

    def _collect_targets(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self._bound_names.add(target.id)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._collect_targets(item)
            return
        raise ValueError("eval condition comprehension target is not allowed")


def _safe_eval(expr: str, state_dict: dict, output: str, raw_output: Any) -> Any:
    parsed = ast.parse(expr, mode="eval")
    _SafeEvalValidator().visit(parsed)
    compiled = compile(parsed, "<workflow condition>", "eval")
    return eval(
        compiled,
        {"__builtins__": _SAFE_BUILTINS, "re": re},
        {
            "state": _AttrView(state_dict),
            "input": output,
            "output": _wrap_attr_value(raw_output),
            "raw_input": raw_output,
        },
    )


def _is_attr_view_access(node: ast.Attribute) -> bool:
    value = node.value
    while isinstance(value, ast.Attribute):
        value = value.value
    return isinstance(value, ast.Name) and value.id in {"state", "output"}


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
    workflow_agent_names = {
        name
        for name in cfg.workflow.nodes
        if isinstance(cfg.agents.get(name), AgentConfig)
    }

    node_callables = _build_node_callables(
        cfg, model_registry, tool_registry, skill_registry, workflow_agent_names
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

    # Wire dynamic destination edges (to: template) separately.
    # Each gets a dispatcher node injected between src and all candidate nodes.
    all_workflow_node_names = list(node_callables.keys())
    static_edges: list[EdgeConfig] = []
    for dispatch_idx, edge_cfg in enumerate(expanded_edges):
        if isinstance(edge_cfg.to, str) and _is_dynamic_to(edge_cfg.to):
            candidate_names = (
                edge_cfg.allowed_targets
                if edge_cfg.allowed_targets is not None
                else all_workflow_node_names
            )
            dispatch_key = f"_dispatch_{edge_cfg.from_}_{dispatch_idx}"
            dispatch_node = _build_dispatch_node(
                edge_cfg.from_, edge_cfg.to, candidate_names, dispatch_key
            )
            node_callables[dispatch_key] = dispatch_node
            for cand_name in candidate_names:
                if cand_name in node_callables:
                    adk_edges.append(
                        Edge(
                            from_node=dispatch_node,
                            to_node=node_callables[cand_name],
                            route=cand_name,
                        )
                    )
            static_edges.append(
                edge_cfg.model_copy(
                    update={"to": dispatch_key, "allowed_targets": None}
                )
            )
        else:
            static_edges.append(edge_cfg)

    static_edges = _apply_default_routes(cfg, static_edges)

    # Separate on_error edges from normal edges.
    normal_edges = [e for e in static_edges if not e.on_error]
    error_edges = [e for e in static_edges if e.on_error]

    # Identify sources that need unified error-aware routing. All workflow
    # agents need this gate so a handled failure does not continue through
    # normal outgoing edges. Explicit on_error sources are included for the
    # existing typed-error routing behavior.
    expanded_error_src_names: set[str] = {
        e.from_ for e in error_edges
    } | {
        e.from_ for e in normal_edges if e.from_ in workflow_agent_names
    }

    # Group ALL edges by source node.
    all_edges_by_src: dict[str, list] = defaultdict(list)
    for edge_cfg in static_edges:
        all_edges_by_src[edge_cfg.from_].append(edge_cfg)

    # Group normal-only edges by source (for nodes WITHOUT on_error edges).
    normal_edges_by_src: dict[str, list] = defaultdict(list)
    for edge_cfg in normal_edges:
        if edge_cfg.from_ not in expanded_error_src_names:
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

            # Build a map of destination name → canonical route label.
            # First occurrence wins so that when multiple conditions share a
            # target (e.g. a case AND the default both point to the same node),
            # all conditions emit the same route and only one ADK edge is added.
            dst_to_route: dict[str, str] = {}
            for i, edge_cfg in enumerate(conditional):
                assert isinstance(edge_cfg.to, str)
                if edge_cfg.to not in dst_to_route:
                    dst_to_route[edge_cfg.to] = f"_route_{i}"

            # Map every edge index to the canonical route for its destination.
            idx_to_route: dict[int, str] = {
                i: dst_to_route[edge_cfg.to]
                for i, edge_cfg in enumerate(conditional)
                if isinstance(edge_cfg.to, str)
            }

            exhausted_route_map: dict[int, str] = {}
            for i, loop_cfg in loop_configs.items():
                if loop_cfg.on_exhausted is not None:
                    if loop_cfg.on_exhausted in dst_to_route:
                        exhausted_route_map[i] = dst_to_route[loop_cfg.on_exhausted]
                    else:
                        exhausted_route_map[i] = f"_exhausted_{i}"

            router = _build_router_node(
                src_name, conditional, loop_configs, exhausted_route_map,
                idx_to_route,
            )
            adk_edges.append(Edge(from_node=src_node, to_node=router))

            # Add one ADK edge per unique destination (dedup by target node).
            seen_dsts: set[str] = set()
            for i, edge_cfg in enumerate(conditional):
                assert isinstance(edge_cfg.to, str)
                if edge_cfg.to in seen_dsts:
                    continue
                seen_dsts.add(edge_cfg.to)
                dst = node_callables[edge_cfg.to]
                adk_edges.append(
                    Edge(from_node=router, to_node=dst, route=idx_to_route[i])
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
    for src_name in expanded_error_src_names:
        src_node = node_callables[src_name]
        src_all_edges = all_edges_by_src[src_name]
        src_normal = [e for e in src_all_edges if not e.on_error]
        src_errors = [e for e in src_all_edges if e.on_error]

        error_router = _build_unified_error_router(
            src_name, src_errors,
        )
        adk_edges.append(Edge(from_node=src_node, to_node=error_router))

        if src_normal:
            success_gate = _build_success_gate_node(src_name)
            adk_edges.append(
                Edge(from_node=error_router, to_node=success_gate, route="_ok")
            )

            conditional = [e for e in src_normal if e.condition is not None]
            unconditional = [e for e in src_normal if e.condition is None]

            for edge_cfg in unconditional:
                assert isinstance(edge_cfg.to, str)
                dst = node_callables[edge_cfg.to]
                adk_edges.append(Edge(from_node=success_gate, to_node=dst))

            if conditional:
                loop_configs: dict[int, LoopConfig] = {}
                for i, edge_cfg in enumerate(conditional):
                    if edge_cfg.loop is not None:
                        loop_configs[i] = edge_cfg.loop

                dst_to_route: dict[str, str] = {}
                for i, edge_cfg in enumerate(conditional):
                    assert isinstance(edge_cfg.to, str)
                    if edge_cfg.to not in dst_to_route:
                        dst_to_route[edge_cfg.to] = f"_route_{i}"

                idx_to_route: dict[int, str] = {
                    i: dst_to_route[edge_cfg.to]
                    for i, edge_cfg in enumerate(conditional)
                    if isinstance(edge_cfg.to, str)
                }

                exhausted_route_map: dict[int, str] = {}
                for i, loop_cfg in loop_configs.items():
                    if loop_cfg.on_exhausted is not None:
                        if loop_cfg.on_exhausted in dst_to_route:
                            exhausted_route_map[i] = dst_to_route[loop_cfg.on_exhausted]
                        else:
                            exhausted_route_map[i] = f"_exhausted_{i}"

                router = _build_router_node(
                    src_name, conditional, loop_configs, exhausted_route_map,
                    idx_to_route,
                )
                adk_edges.append(Edge(from_node=success_gate, to_node=router))

                seen_dsts: set[str] = set()
                for i, edge_cfg in enumerate(conditional):
                    assert isinstance(edge_cfg.to, str)
                    if edge_cfg.to in seen_dsts:
                        continue
                    seen_dsts.add(edge_cfg.to)
                    dst = node_callables[edge_cfg.to]
                    adk_edges.append(
                        Edge(from_node=router, to_node=dst, route=idx_to_route[i])
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


def _apply_default_routes(
    cfg: RootConfig,
    edges: list[EdgeConfig],
) -> list[EdgeConfig]:
    """Inject workflow-level conditional fallback routes.

    Default routes are intentionally conservative:
    - they do not apply to sources with explicit ``condition: default`` of the
      same kind (normal or on_error);
    - they do not apply to sources with unconditional routes of the same kind;
    - they do not apply from the handler node to itself.

    When ``on_error: true`` is set on the default route, the injected edges are
    on_error edges that fire when the source node raises an exception.
    """
    if not cfg.workflow.default_routes:
        return edges

    result = list(edges)
    normal_by_src: dict[str, list[EdgeConfig]] = defaultdict(list)
    error_by_src: dict[str, list[EdgeConfig]] = defaultdict(list)
    for edge in result:
        if edge.on_error:
            error_by_src[edge.from_].append(edge)
        else:
            normal_by_src[edge.from_].append(edge)

    for default_route in cfg.workflow.default_routes:
        source_names = (
            default_route.from_
            if default_route.from_ is not None
            else cfg.workflow.nodes
        )
        excluded = set(default_route.exclude)

        if default_route.on_error:
            for src_name in source_names:
                if src_name == default_route.to or src_name in excluded:
                    continue
                src_errors = error_by_src.get(src_name, [])
                has_explicit_default = any(
                    e.condition == "__DEFAULT__" for e in src_errors
                )
                has_unconditional = any(e.condition is None for e in src_errors)
                if has_explicit_default or has_unconditional:
                    continue
                edge = EdgeConfig(
                    from_=src_name,
                    to=default_route.to,
                    on_error=True,
                    condition=default_route.condition,
                )
                result.append(edge)
                error_by_src[src_name].append(edge)
        else:
            for src_name in source_names:
                if src_name == default_route.to or src_name in excluded:
                    continue
                src_normal = normal_by_src.get(src_name, [])
                has_explicit_default = any(
                    e.condition == "__DEFAULT__" for e in src_normal
                )
                has_unconditional = any(e.condition is None for e in src_normal)
                if has_explicit_default or has_unconditional:
                    continue
                edge = EdgeConfig(
                    from_=src_name,
                    to=default_route.to,
                    condition=default_route.condition,
                )
                result.append(edge)
                normal_by_src[src_name].append(edge)

    return result


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


def _build_dispatch_node(
    src_name: str,
    to_template: str,
    candidate_names: list[str],
    node_name: str,
) -> Any:
    """Build a @node that resolves a template destination from state at runtime.

    Reads ``to_template`` (e.g. ``{{state.router.next_node}}``) from state,
    validates the resolved name is among *candidate_names*, and yields
    ``AdkEvent(route=resolved_name)``.  The caller wires one ``Edge`` per
    candidate so ADK can route to it.
    """
    async def _dispatch(ctx: Any, node_input: Any):
        state_dict = (
            ctx.state.to_dict()
            if hasattr(ctx.state, "to_dict")
            else dict(ctx.state)
        )
        try:
            destination = resolve_template(to_template, state_dict)
        except Exception as exc:
            logger.error(
                "dispatch '%s': failed to resolve template %r: %s",
                src_name, to_template, exc,
            )
            return

        if destination not in candidate_names:
            logger.error(
                "dispatch '%s': resolved destination %r is not a known node "
                "(candidates: %s) — workflow terminates",
                src_name, destination, candidate_names,
            )
            return

        logger.info(
            "dispatch '%s': resolved %r → '%s'",
            src_name, to_template, destination,
        )
        yield AdkEvent(route=destination, output=destination)

    _dispatch.__name__ = node_name
    _dispatch.__qualname__ = node_name

    return adk_node()(_dispatch)


def _build_router_node(
    src_name: str,
    conditional_edges: list,
    loop_configs: dict[int, LoopConfig] | None = None,
    exhausted_route_map: dict[int, str] | None = None,
    idx_to_route: dict[int, str] | None = None,
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
    if idx_to_route is None:
        idx_to_route = {i: f"_route_{i}" for i in range(len(conditional_edges))}

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
                        "router '%s': loop iteration %d/%d → route %s",
                        src_name, current_iter + 1,
                        loop_cfg.max_iterations, idx_to_route[idx],
                    )
                    yield AdkEvent(
                        route=idx_to_route[idx],
                        output=raw_output,
                        state={iter_key: current_iter + 1},
                    )
                    return

                logger.info(
                    "router '%s': matched condition %r → route %s",
                    src_name, condition, idx_to_route[idx],
                )
                yield AdkEvent(route=idx_to_route[idx], output=raw_output)
                return

        # Fall back to default if nothing matched.
        if default_idx is not None:
            logger.info(
                "router '%s': no condition matched → default route %s",
                src_name, idx_to_route[default_idx],
            )
            yield AdkEvent(route=idx_to_route[default_idx], output=raw_output)
        else:
            logger.info(
                "router '%s': no condition matched and no default — workflow terminates",
                src_name,
            )

    _router.__name__ = f"{src_name}_router"
    _router.__qualname__ = f"{src_name}_router"

    return adk_node()(_router)


def _error_edge_matches(edge: EdgeConfig, err_type: str, err_msg: str) -> bool:
    """Return True if *edge* should fire for the given error type and message.

    An edge with neither ``error_type`` nor ``error_match`` is a wildcard that
    matches any error. ``condition: "__DEFAULT__"`` edges are excluded here
    (they are handled separately as the fallback).
    """
    if edge.condition == "__DEFAULT__":
        return False
    type_ok = edge.error_type is None or edge.error_type == err_type
    match_ok = edge.error_match is None or bool(re.search(edge.error_match, err_msg))
    return type_ok and match_ok


def _build_success_gate_node(src_name: str) -> Any:
    """Build a quiet pass-through node after error-aware routing succeeds."""
    node_name = f"{src_name}_success_gate"

    async def _success_gate(ctx: Any, node_input: Any):
        yield AdkEvent()

    _success_gate.__name__ = node_name
    _success_gate.__qualname__ = node_name

    return adk_node()(_success_gate)


def _build_unified_error_router(
    src_name: str,
    error_edges: list[EdgeConfig],
) -> Any:
    """Build a @node that routes to success OR error handlers.

    On error, iterates error_edges in declaration order and picks the first
    edge whose ``error_type`` / ``error_match`` criteria are satisfied.
    An edge with neither field is a wildcard that matches any error.
    ``condition: default`` edges are evaluated last regardless of order.

    On success, routes to the internal success gate via ``_ok``. The success
    gate then preserves the source's normal routing semantics.
    """
    # Separate default error edge (condition == __DEFAULT__) from typed/wildcard ones.
    typed_error_edges: list[tuple[int, EdgeConfig]] = []
    default_error_idx: int | None = None
    for i, edge in enumerate(error_edges):
        if edge.condition == "__DEFAULT__":
            default_error_idx = i
        else:
            typed_error_edges.append((i, edge))

    async def _error_router(ctx: Any, node_input: Any):
        state_dict = (
            ctx.state.to_dict()
            if hasattr(ctx.state, "to_dict")
            else dict(ctx.state)
        )
        error_key = f"_error_{src_name}"
        error_info = state_dict.get(error_key)

        if error_info is not None:
            is_dict = isinstance(error_info, dict)
            err_type = error_info.get("error_type", "") if is_dict else ""
            err_msg = error_info.get("error_message", "") if is_dict else str(error_info)

            # Try typed/wildcard error edges in declaration order.
            for idx, edge in typed_error_edges:
                if _error_edge_matches(edge, err_type, err_msg):
                    logger.info(
                        "error_router '%s': error '%s' matched edge %d → _error_%d",
                        src_name, err_type, idx, idx,
                    )
                    yield AdkEvent(route=f"_error_{idx}", output=str(error_info))
                    return

            # Fall back to default error edge if present.
            if default_error_idx is not None:
                logger.info(
                    "error_router '%s': no typed match → default error edge _error_%d",
                    src_name, default_error_idx,
                )
                yield AdkEvent(route=f"_error_{default_error_idx}", output=str(error_info))
                return

            logger.warning(
                "error_router '%s': error '%s' matched no error edge — workflow terminates",
                src_name, err_type,
            )
            yield AdkEvent(output=str(error_info))
        else:
            logger.info(
                "error_router '%s': no error → routing to success gate",
                src_name,
            )
            yield AdkEvent(route="_ok")

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
                _safe_eval(condition.eval, state_dict, output, raw_output)
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
    error_src_names: set[str],
) -> dict[str, Any]:
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

        if isinstance(agent_cfg, A2aAgentConfig):
            if agent_name in all_sub_agent_names:
                built_agents[agent_name] = build_remote_a2a_agent(
                    agent_name, agent_cfg
                )
            else:
                node = build_a2a_agent_node(agent_name, agent_cfg)
                built_agents[agent_name] = node
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
                skill_toolset, agent_name in error_src_names,
            )
            built_agents[agent_name] = node
            callables[agent_name] = node

    return callables
