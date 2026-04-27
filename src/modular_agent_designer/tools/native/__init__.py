"""Native (bundled) tools shipped with modular-agent-designer."""
from .files import read_text_file
from .http import fetch_url, http_get_json

__all__ = ["fetch_url", "http_get_json", "read_text_file"]
