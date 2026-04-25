"""Load and validate workflow YAML files into RootConfig."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .schema import RootConfig


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
