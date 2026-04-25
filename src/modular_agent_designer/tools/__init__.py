"""Tool registry and bundled native tools for modular-agent-designer."""
from .native import fetch_url

BUILTIN_TOOLS: dict[str, object] = {
    "fetch_url": fetch_url,
}

__all__ = ["fetch_url", "BUILTIN_TOOLS"]
