"""Built-in file-system tool (read-only, sandboxed to cwd)."""
from __future__ import annotations

from pathlib import Path


def read_text_file(path: str) -> str:
    """Read a text file at *path* (relative to the current working directory).

    Absolute paths and paths containing '..' are rejected to prevent
    accidental access outside the project tree.  Returns the file contents
    as a string, or an 'ERROR:' prefixed message on failure.
    """
    p = Path(path)
    if p.is_absolute():
        return f"ERROR: absolute paths are not allowed (got '{path}')"
    try:
        resolved = (Path.cwd() / p).resolve()
    except Exception as exc:
        return f"ERROR resolving path '{path}': {exc}"

    cwd_resolved = Path.cwd().resolve()
    try:
        resolved.relative_to(cwd_resolved)
    except ValueError:
        return f"ERROR: path '{path}' escapes the working directory"

    if not resolved.exists():
        return f"ERROR: file not found: '{path}'"
    if not resolved.is_file():
        return f"ERROR: '{path}' is not a file"

    try:
        return resolved.read_text(encoding="utf-8")
    except OSError as exc:
        return f"ERROR reading '{path}': {exc}"
