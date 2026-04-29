"""Load and validate workflow YAML files into RootConfig."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .schema import RootConfig

_DOTTED_REF_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*)*$"
)

# Matches a sole {{state.x.y.z}} template reference.
_STATE_TEMPLATE_RE = re.compile(r"^\{\{\s*state\.([\w.]+)\s*\}\}$")


def _dotted_ref_to_path(ref: str, base_dir: Path | None = None) -> Path:
    """Convert a dotted ref like 'prompts.my_agent' to a Path with .md suffix.

    Resolved from the current working directory first. If not found and
    *base_dir* is provided, fall back to resolving relative to that directory.
    """
    if not _DOTTED_REF_RE.match(ref):
        raise ValueError(
            f"instruction_file '{ref}' is not a valid dotted ref "
            f"(e.g. 'prompts.my_workflow__my_agent')"
        )
    parts = ref.split(".")
    cwd_path = Path.cwd().joinpath(*parts).with_suffix(".md")
    if cwd_path.exists() or base_dir is None:
        return cwd_path
    base_path = base_dir.joinpath(*parts).with_suffix(".md")
    if base_path.exists():
        return base_path
    return cwd_path


def _resolve_file_field(
    agent: dict,
    name: str,
    file_key: str,
    inline_key: str,
    base_dir: Path | None = None,
) -> None:
    """Resolve a *_file dotted ref to file contents in-place."""
    ref = agent.get(file_key)
    if ref is None:
        return
    if agent.get(inline_key) is not None:
        raise ValueError(
            f"Agent '{name}': specify either '{inline_key}' or"
            f" '{file_key}', not both"
        )
    file_path = _dotted_ref_to_path(ref, base_dir)
    try:
        agent[inline_key] = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(
            f"Agent '{name}': {file_key} not found: {file_path}"
        )
    except OSError as exc:
        raise ValueError(
            f"Agent '{name}': error reading {file_key} {file_path}: {exc}"
        ) from exc
    del agent[file_key]


def _resolve_instruction_files(raw: dict, base_dir: Path | None = None) -> None:
    """Resolve instruction_file and static_instruction_file dotted refs to file contents."""
    agents = raw.get("agents", {})
    if not isinstance(agents, dict):
        return
    for name, agent in agents.items():
        if not isinstance(agent, dict):
            continue
        _resolve_file_field(agent, name, "instruction_file", "instruction", base_dir)
        _resolve_file_field(agent, name, "static_instruction_file", "static_instruction", base_dir)


def _switch_expr_to_eval(switch_val: Any, from_node: str) -> str:
    """Convert a switch: field value to a Python eval expression string.

    Accepted forms:
    - ``"{{state.a.b}}"`` — state template; converted to chained dict .get() calls.
    - ``{eval: "expr"}``  — arbitrary Python expression used as-is.
    """
    if isinstance(switch_val, dict):
        if "eval" in switch_val:
            return switch_val["eval"]
        raise ValueError(
            f"switch edge from '{from_node}': switch dict must have an 'eval' key,"
            f" got {switch_val!r}"
        )
    if isinstance(switch_val, str):
        m = _STATE_TEMPLATE_RE.match(switch_val.strip())
        if m:
            parts = m.group(1).split(".")
            # Build chained .get() — intermediate parts default to {}, leaf to None.
            expr = "state"
            for i, part in enumerate(parts):
                default = "{}" if i < len(parts) - 1 else "None"
                expr = f"{expr}.get({part!r}, {default})"
            return expr
        raise ValueError(
            f"switch edge from '{from_node}': switch string must be a "
            f"'{{{{state.x.y}}}}' template, got {switch_val!r}"
        )
    raise ValueError(
        f"switch edge from '{from_node}': switch must be a template string "
        f"or {{eval: 'expr'}}, got {type(switch_val).__name__!r}"
    )


def _expand_switch_edges(raw: dict) -> None:
    """Expand switch/case edge entries into regular condition edges in-place.

    A switch edge looks like::

        - from: node_a
          switch: "{{state.node_a.category}}"
          cases:
            urgent: handle_urgent
            normal: handle_normal
          default: handle_other        # optional

    Each case becomes a ``condition: {eval: ...}`` edge; the default becomes a
    ``condition: default`` edge. This runs before Pydantic validation so the
    builder only ever sees plain EdgeConfig entries.
    """
    workflow = raw.get("workflow")
    if not isinstance(workflow, dict):
        return
    edges = workflow.get("edges")
    if not isinstance(edges, list):
        return

    new_edges: list[Any] = []
    for entry in edges:
        if not isinstance(entry, dict) or "switch" not in entry:
            new_edges.append(entry)
            continue

        from_node = entry.get("from")
        if not from_node:
            raise ValueError("switch edge is missing a 'from' field")

        cases = entry.get("cases")
        if not isinstance(cases, dict) or not cases:
            raise ValueError(
                f"switch edge from '{from_node}' requires a non-empty 'cases' mapping"
            )

        default_target = entry.get("default")
        switch_expr = _switch_expr_to_eval(entry["switch"], from_node)

        for case_value, target in cases.items():
            eval_str = f"({switch_expr}) == {str(case_value)!r}"
            new_edges.append({"from": from_node, "to": target, "condition": {"eval": eval_str}})

        if default_target is not None:
            new_edges.append({"from": from_node, "to": default_target, "condition": "default"})

    workflow["edges"] = new_edges


def load_workflow(path: str | Path) -> RootConfig:
    """Parse a workflow YAML file and return a validated RootConfig.

    Raises ValueError with the file path and validation details on any error.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Workflow file not found: {p}")

    with p.open() as f:
        try:
            raw = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError(f"YAML parse error in {p}:\n{exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(
            f"Workflow file must be a YAML mapping, "
            f"got {type(raw).__name__}: {p}"
        )

    _resolve_instruction_files(raw, p.parent)
    _expand_switch_edges(raw)

    try:
        return RootConfig.model_validate(raw)
    except ValidationError as exc:
        # Format each error with its YAML dotted path for quick diagnosis.
        lines = [f"Invalid workflow config in {p}:"]
        for err in exc.errors():
            loc = (
                " -> ".join(str(s) for s in err["loc"])
                if err["loc"]
                else "(root)"
            )
            lines.append(f"  [{loc}] {err['msg']}")
        raise ValueError("\n".join(lines)) from exc
