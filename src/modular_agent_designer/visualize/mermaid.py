"""Render a RootConfig as a Mermaid flowchart TD diagram."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config.schema import RootConfig

_MAX_EVAL_LEN = 40


def _sanitize(text: str) -> str:
    """Escape quotes and strip newlines so the string is safe inside Mermaid labels."""
    return text.replace('"', "&quot;").replace("\n", " ").strip()


def _edge_label(condition) -> str | None:
    """Return the Mermaid label string for a condition, or None for unconditional."""
    from ..config.schema import EvalCondition

    if condition is None:
        return None
    if condition == "__DEFAULT__":
        return "default"
    if isinstance(condition, EvalCondition):
        expr = _sanitize(condition.eval)
        if len(expr) > _MAX_EVAL_LEN:
            expr = expr[:_MAX_EVAL_LEN] + "…"
        return f"eval: {expr}"
    if isinstance(condition, list):
        return " | ".join(_sanitize(str(v)) for v in condition)
    return _sanitize(str(condition))


def render_mermaid(cfg: "RootConfig") -> str:
    from ..config.schema import A2aAgentConfig, AgentConfig, NodeRefConfig

    lines: list[str] = ["flowchart TD"]

    # Virtual START node
    lines.append("    START((start))")

    # Workflow nodes
    for node_name in cfg.workflow.nodes:
        entry = cfg.agents.get(node_name)
        safe_name = node_name
        if isinstance(entry, AgentConfig):
            model_alias = _sanitize(entry.model)
            label = f"{node_name}<br/>({model_alias})"
            if entry.mode == "chat":
                label += " · chat"
            # Show retry badge if configured.
            if entry.retry is not None:
                label += f" · retry×{entry.retry.max_retries}"
            lines.append(f'    {safe_name}["{label}"]')
        elif isinstance(entry, A2aAgentConfig):
            lines.append(f'    {safe_name}["{node_name}<br/>(A2A)"]')
        elif isinstance(entry, NodeRefConfig):
            ref = _sanitize(entry.ref)
            lines.append(f'    {safe_name}{{{{"{node_name}<br/>({ref})"}}}}')
        else:
            # Unknown — plain rectangle
            lines.append(f'    {safe_name}["{node_name}"]')

    # Sub-agent clusters
    for node_name in cfg.workflow.nodes:
        entry = cfg.agents.get(node_name)
        if isinstance(entry, AgentConfig) and entry.sub_agents:
            lines.append(f"    subgraph {node_name}_sub_agents [sub-agents of {node_name}]")
            for sub in entry.sub_agents:
                sub_entry = cfg.agents.get(sub)
                if isinstance(sub_entry, AgentConfig):
                    model_alias = _sanitize(sub_entry.model)
                    label = f"{sub}<br/>({model_alias})"
                    if sub_entry.mode:
                        label += f" · {sub_entry.mode}"
                    lines.append(f'        {sub}["{label}"]')
                elif isinstance(sub_entry, A2aAgentConfig):
                    lines.append(f'        {sub}["{sub}<br/>(A2A)"]')
                else:
                    lines.append(f'        {sub}["{sub}"]')
            lines.append("    end")
            # Dotted edges from parent to each sub-agent
            for sub in entry.sub_agents:
                lines.append(f"    {node_name} -.-> {sub}")

    # Entry edge
    lines.append(f"    START --> {cfg.workflow.entry}")

    # Workflow edges
    for edge in cfg.workflow.edges:
        src = edge.from_

        # Handle fan-out edges (to: [list])
        if isinstance(edge.to, list):
            for dst in edge.to:
                if edge.parallel:
                    # Parallel fan-out: thick arrows
                    lines.append(f'    {src} ==> {dst}')
                else:
                    lines.append(f'    {src} --> {dst}')
            # Show join target if specified
            if edge.join is not None:
                for dst in edge.to:
                    lines.append(f'    {dst} -.-> {edge.join}')
            continue

        dst = edge.to
        label = _edge_label(edge.condition)

        # on_error edges: red dashed style
        if edge.on_error:
            error_label = "on_error"
            lines.append(f'    {src} -. "{error_label}" .-> {dst}')
            continue

        # Loop edges: thick arrows with iteration label
        if edge.loop is not None:
            loop_label = f"loop ≤{edge.loop.max_iterations}×"
            if label:
                loop_label = f"{label} | {loop_label}"
            lines.append(f'    {src} == "{loop_label}" ==> {dst}')
            # Show on_exhausted target if specified
            if edge.loop.on_exhausted is not None:
                lines.append(
                    f'    {src} -. "exhausted" .-> {edge.loop.on_exhausted}'
                )
            continue

        # Normal edges
        if label is None:
            lines.append(f"    {src} --> {dst}")
        else:
            lines.append(f'    {src} -. "{label}" .-> {dst}')

    # Workflow-level default routes are injected at build time for selected
    # sources. Render them as dotted fallback edges.
    for route in cfg.workflow.default_routes:
        sources = route.from_ if route.from_ is not None else cfg.workflow.nodes
        excluded = set(route.exclude)
        label = _edge_label(route.condition) or "default route"
        for src in sources:
            if src == route.to or src in excluded:
                continue
            lines.append(f'    {src} -. "{label}" .-> {route.to}')

    return "\n".join(lines) + "\n"
