"""Unit tests for parallel fan-out / fan-in routing."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.workflow.builder import build_workflow


def _load(tmp_path: Path, content: str):
    p = tmp_path / "wf.yaml"
    p.write_text(content)
    return load_workflow(p)


# ---------------------------------------------------------------------------
# Schema validation for fan-out edges
# ---------------------------------------------------------------------------


def test_to_list_accepted(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: fanout_test
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          src:
            model: m
            instruction: src
          a:
            model: m
            instruction: a
          b:
            model: m
            instruction: b
          join_target:
            model: m
            instruction: join
        workflow:
          nodes: [src, a, b, join_target]
          edges:
            - from: src
              to: [a, b]
              parallel: true
              join: join_target
          entry: src
    """)
    cfg = _load(tmp_path, yaml)
    edge = cfg.workflow.edges[0]
    assert isinstance(edge.to, list)
    assert edge.to == ["a", "b"]
    assert edge.parallel is True
    assert edge.join == "join_target"


def test_parallel_requires_list_to(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: bad_parallel
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
              parallel: true
          entry: a
    """)
    with pytest.raises((ValidationError, ValueError), match="list"):
        _load(tmp_path, yaml)


def test_join_requires_list_to(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: bad_join
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
              join: b
          entry: a
    """)
    with pytest.raises((ValidationError, ValueError), match="list"):
        _load(tmp_path, yaml)


def test_join_unknown_node_rejected(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: bad_join_target
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          src:
            model: m
            instruction: src
          a:
            model: m
            instruction: a
          b:
            model: m
            instruction: b
        workflow:
          nodes: [src, a, b]
          edges:
            - from: src
              to: [a, b]
              parallel: true
              join: nonexistent
          entry: src
    """)
    with pytest.raises((ValidationError, ValueError), match="nonexistent"):
        _load(tmp_path, yaml)


# ---------------------------------------------------------------------------
# Build-level: fan-out expansion and join node injection
# ---------------------------------------------------------------------------


def test_fanout_expands_edges_and_injects_join(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: fanout_build
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          dispatcher:
            model: m
            instruction: dispatch
          a:
            model: m
            instruction: a
          b:
            model: m
            instruction: b
          c:
            model: m
            instruction: c
          synth:
            model: m
            instruction: synthesize
        workflow:
          nodes: [dispatcher, a, b, c, synth]
          edges:
            - from: dispatcher
              to: [a, b, c]
              parallel: true
              join: synth
          entry: dispatcher
    """)
    cfg = _load(tmp_path, yaml)
    wf = build_workflow(cfg)

    routes = [e.route for e in wf.edges if e.route is not None]
    assert "_ok" in routes
    node_names = {
        getattr(node, "name", None)
        for edge in wf.edges
        for node in (edge.from_node, edge.to_node)
    }
    assert "_join_dispatcher_a_b_c" in node_names


def test_fanout_without_join(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: fanout_no_join
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          src:
            model: m
            instruction: src
          a:
            model: m
            instruction: a
          b:
            model: m
            instruction: b
        workflow:
          nodes: [src, a, b]
          edges:
            - from: src
              to: [a, b]
              parallel: true
          entry: src
    """)
    cfg = _load(tmp_path, yaml)
    wf = build_workflow(cfg)

    routes = [e.route for e in wf.edges if e.route is not None]
    assert "_ok" in routes
    destinations = {
        getattr(edge.to_node, "name", None)
        for edge in wf.edges
    }
    assert {"a", "b"}.issubset(destinations)


def test_to_list_targets_validated(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: bad_fanout_target
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          src:
            model: m
            instruction: src
          a:
            model: m
            instruction: a
        workflow:
          nodes: [src, a]
          edges:
            - from: src
              to: [a, nonexistent]
              parallel: true
          entry: src
    """)
    with pytest.raises((ValidationError, ValueError), match="nonexistent"):
        _load(tmp_path, yaml)
