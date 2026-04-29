"""A modular framework for designing and orchestrating complex agentic workflows with ease."""

from importlib.metadata import PackageNotFoundError, version

from .config.loader import load_workflow
from .workflow.builder import build_workflow
from .cli import _run_workflow as run_workflow_async

try:
    __version__ = version("modular-agent-designer")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = ["__version__", "load_workflow", "build_workflow", "run_workflow_async"]
