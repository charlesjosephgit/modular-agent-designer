"""Tool registry and bundled native tools for modular-agent-designer."""
from .native import fetch_url, http_get_json, read_text_file

BUILTIN_TOOLS: dict[str, object] = {
    "fetch_url": fetch_url,
    "http_get_json": http_get_json,
    "read_text_file": read_text_file,
}

__all__ = ["fetch_url", "http_get_json", "read_text_file", "BUILTIN_TOOLS"]
