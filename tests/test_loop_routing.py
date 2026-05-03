"""Unit tests for loop / cycle routing."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.config.schema import EvalCondition, LoopConfig
from modular_agent_designer.workflow.builder import (
    _loop_iter_key,
    _matches,
    build_workflow,
)


def _load(tmp_path: Path, content: str):
    p = tmp_path / "wf.yaml"
    p.write_text(content)
    return load_workflow(p)


# ---------------------------------------------------------------------------
# LoopConfig schema validation
# ---------------------------------------------------------------------------


def test_loop_config_defaults():
    lc = LoopConfig()
    assert lc.max_iterations == 3
    assert lc.on_exhausted is None


def test_loop_config_custom_values():
    lc = LoopConfig(max_iterations=5, on_exhausted="fallback")
    assert lc.max_iterations == 5
    assert lc.on_exhausted == "fallback"


def test_loop_config_min_iterations():
    with pytest.raises(ValidationError):
        LoopConfig(max_iterations=0)


def test_loop_config_max_iterations():
    with pytest.raises(ValidationError):
        LoopConfig(max_iterations=101)


# ---------------------------------------------------------------------------
# Loop iter key helper
# ---------------------------------------------------------------------------


def test_loop_iter_key():
    key = _loop_iter_key("reviewer", "writer")
    assert key == "_loop_reviewer_writer_iter"


# ---------------------------------------------------------------------------
# Edge validation: loop constraints
# ---------------------------------------------------------------------------


def test_loop_incompatible_with_list_to(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: bad_loop_fanout
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          a:
            model: m
            instruction: a
          b:
            model: m
            instruction: b
          c:
            model: m
            instruction: c
        workflow:
          nodes: [a, b, c]
          edges:
            - from: a
              to: [b, c]
              loop:
                max_iterations: 3
          entry: a
    """)
    with pytest.raises((ValidationError, ValueError)):
        _load(tmp_path, yaml)


def test_loop_incompatible_with_on_error(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: bad_loop_error
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          a:
            model: m
            instruction: a
          b:
            model: m
            instruction: b
        workflow:
          nodes: [a, b]
          edges:
            - from: a
              to: b
              on_error: true
              loop:
                max_iterations: 3
          entry: a
    """)
    with pytest.raises((ValidationError, ValueError)):
        _load(tmp_path, yaml)


# ---------------------------------------------------------------------------
# Accidental cycle detection
# ---------------------------------------------------------------------------


def test_accidental_cycle_detected(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: accidental_cycle
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          a:
            model: m
            instruction: a
          b:
            model: m
            instruction: b
        workflow:
          nodes: [a, b]
          edges:
            - from: a
              to: b
            - from: b
              to: a
          entry: a
    """)
    with pytest.raises((ValidationError, ValueError), match="[Cc]ycle"):
        _load(tmp_path, yaml)


def test_intentional_loop_allowed(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: intentional_loop
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          writer:
            model: m
            instruction: write
          reviewer:
            model: m
            instruction: review
        workflow:
          nodes: [writer, reviewer]
          edges:
            - from: writer
              to: reviewer
            - from: reviewer
              to: writer
              condition: "revise"
              loop:
                max_iterations: 3
          entry: writer
    """)
    cfg = _load(tmp_path, yaml)
    assert cfg.name == "intentional_loop"


# ---------------------------------------------------------------------------
# Build-level: loop workflow edges
# ---------------------------------------------------------------------------


def test_loop_workflow_builds_with_exhausted_route(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: loop_build_test
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          writer:
            model: m
            instruction: write
          reviewer:
            model: m
            instruction: review
          finalizer:
            model: m
            instruction: finalize
        workflow:
          nodes: [writer, reviewer, finalizer]
          edges:
            - from: writer
              to: reviewer
            - from: reviewer
              to: writer
              condition: "revise"
              loop:
                max_iterations: 3
                on_exhausted: finalizer
            - from: reviewer
              to: finalizer
              condition: "approved"
          entry: writer
    """)
    cfg = _load(tmp_path, yaml)
    wf = build_workflow(cfg)

    # Edges should include guarded normal routes and reviewer conditional
    # routes: reviewer_router→writer (_route_0),
    # reviewer_router→finalizer (_route_1).
    # Note: on_exhausted=finalizer reuses _route_1 (no duplicate edge).
    routes = [e.route for e in wf.edges if e.route is not None]
    assert "_ok" in routes
    assert "_route_0" in routes
    assert "_route_1" in routes


def test_on_exhausted_unknown_node_rejected(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: bad_exhausted
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          a:
            model: m
            instruction: a
          b:
            model: m
            instruction: b
        workflow:
          nodes: [a, b]
          edges:
            - from: b
              to: a
              condition: "retry"
              loop:
                max_iterations: 3
                on_exhausted: nonexistent
          entry: a
    """)
    with pytest.raises((ValidationError, ValueError), match="nonexistent"):
        _load(tmp_path, yaml)
