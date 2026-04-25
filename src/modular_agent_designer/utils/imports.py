"""Shared helper for importing dotted 'pkg.module.attribute' refs."""
from __future__ import annotations

import importlib
from typing import Any


def import_dotted_ref(ref: str, *, context: str) -> Any:
    """Import 'pkg.module.attribute' and return the attribute.

    `context` is prefixed onto error messages so callers
    (tool / output_schema / custom node) produce consistent, traceable errors.
    """
    module_path, _, attr = ref.rpartition(".")
    if not module_path:
        raise ValueError(
            f"{context}: ref '{ref}' must be a dotted path "
            "like 'pkg.module.attribute'"
        )
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        raise ImportError(
            f"{context}: cannot import module '{module_path}' (ref '{ref}'): {exc}"
        ) from exc
    if not hasattr(module, attr):
        raise AttributeError(
            f"{context}: module '{module_path}' has no attribute '{attr}' (ref '{ref}')"
        )
    return getattr(module, attr)
