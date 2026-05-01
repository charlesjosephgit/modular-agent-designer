"""End-to-end test against a running Ollama instance.

Skipped automatically if Ollama is not reachable at OLLAMA_API_BASE
(default: http://localhost:11434).
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.workflow.builder import build_workflow
from modular_agent_designer.cli import _run_workflow

WORKFLOWS_DIR = (
    __file__
    and __import__("pathlib").Path(__file__).parent.parent / "examples" / "workflows"
)


def _ollama_base() -> str:
    return os.environ.get("OLLAMA_API_BASE", "http://localhost:11434")


def _ollama_reachable() -> bool:
    try:
        r = httpx.get(f"{_ollama_base()}/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_reachable(),
    reason="Ollama not running at " + _ollama_base(),
)


@pytest.mark.asyncio
async def test_hello_world_writes_state():
    yaml_path = WORKFLOWS_DIR / "hello_world.yaml"
    cfg = load_workflow(yaml_path)
    input_data = {"topic": "tide pools"}
    wf = build_workflow(cfg)

    state = await _run_workflow(wf, input_data)

    assert "greeter" in state, f"Expected 'greeter' in state, got keys: {list(state.keys())}"
    assert isinstance(state["greeter"], str)
    assert len(state["greeter"]) > 0
