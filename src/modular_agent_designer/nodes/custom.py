"""Load custom BaseNode subclasses or @node-decorated functions from YAML refs.

Custom nodes declared as `type: node` are the escape hatch for logic that
isn't a plain LLM call (branching, loops, side-effects, etc.).

Custom nodes are responsible for their own state writes.  The framework
does NOT wrap their return value into ctx.state automatically — this
gives full control to the implementor.

An optional `config:` mapping in the YAML is forwarded as keyword arguments
to the class constructor when the ref points to a BaseNode subclass, allowing
parameterised nodes without needing a new subclass per use-case:

    agents:
      router:
        type: node
        ref: mypackage.nodes.RouterNode
        config:
          threshold: 0.8
          label: primary
"""
from __future__ import annotations

from typing import Any

from google.adk.workflow import BaseNode

from ..config.schema import NodeRefConfig
from ..utils.imports import import_dotted_ref


def build_custom_node(node_name: str, cfg: NodeRefConfig) -> Any:
    """Import and return the node object described by *cfg*.

    The ref may point to:
    - A BaseNode subclass  → instantiated with cfg.config as kwargs and returned.
    - A plain callable or @node-decorated function → returned as-is (config ignored).
    """
    obj = import_dotted_ref(cfg.ref, context=f"Custom node '{node_name}'")

    if isinstance(obj, type) and issubclass(obj, BaseNode):
        return obj(name=node_name, **cfg.config)

    return obj
