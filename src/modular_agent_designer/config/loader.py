"""Load and validate workflow YAML files into RootConfig."""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from .schema import RootConfig

_DOTTED_REF_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*)*$"
)


def _dotted_ref_to_path(ref: str) -> Path:
    """Convert a dotted ref like 'prompts.my_agent' to a Path with .txt suffix.

    Resolved from the current working directory.
    """
    if not _DOTTED_REF_RE.match(ref):
        raise ValueError(
            f"instruction_file '{ref}' is not a valid dotted ref "
            f"(e.g. 'prompts.my_workflow__my_agent')"
        )
    return Path.cwd().joinpath(*ref.split(".")).with_suffix(".txt")


def _resolve_instruction_files(raw: dict) -> None:
    """Resolve instruction_file dotted refs to file contents."""
    agents = raw.get("agents", {})
    if not isinstance(agents, dict):
        return
    for name, agent in agents.items():
        if not isinstance(agent, dict):
            continue
        instruction_file = agent.get("instruction_file")
        if instruction_file is None:
            continue
        if agent.get("instruction") is not None:
            raise ValueError(
                f"Agent '{name}': specify either 'instruction' or"
                f" 'instruction_file', not both"
            )
        file_path = _dotted_ref_to_path(instruction_file)
        try:
            agent["instruction"] = file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ValueError(
                f"Agent '{name}': instruction_file not found: {file_path}"
            )
        except OSError as exc:
            raise ValueError(
                f"Agent '{name}': error reading instruction_file"
                f" {file_path}: {exc}"
            ) from exc
        del agent["instruction_file"]


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

    _resolve_instruction_files(raw)

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
