"""Load custom BaseNode subclasses or @node-decorated functions from YAML refs.

Custom nodes declared as `type: node` are the escape hatch for logic that
isn't a plain LLM call (branching, loops, side-effects, etc.).

Custom nodes are responsible for their own state writes.  The framework
does NOT wrap their return value into ctx.state automatically — this
gives full control to the implementor.
"""
from __future__ import annotations

from typing import Any

from google.adk.workflow import BaseNode

from ..config.schema import NodeRefConfig
from ..utils.imports import import_dotted_ref


def build_custom_node(node_name: str, cfg: NodeRefConfig) -> Any:
    """Import and return the node object described by *cfg*.

    The ref may point to:
    - A BaseNode subclass  → instantiated with no args and returned.
    - A plain callable or @node-decorated function → returned as-is.
    """
    obj = import_dotted_ref(cfg.ref, context=f"Custom node '{node_name}'")

    # If it's a class (BaseNode subclass), instantiate it.
    if isinstance(obj, type) and issubclass(obj, BaseNode):
        return obj()

    # Otherwise return as-is (function, @node-decorated, or instance).
    return obj
