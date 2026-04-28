"""Tests for dynamic destination routing (to: "{{state.x.y}}")."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.config.schema import EdgeConfig, _is_dynamic_to
from modular_agent_designer.workflow.builder import build_workflow


def _load(tmp_path: Path, content: str):
    p = tmp_path / "wf.yaml"
    p.write_text(content)
    return load_workflow(p)


def _dyn_yaml(edges_block: str) -> str:
    return textwrap.dedent("""\
        name: dyn_test
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          router:
            model: m
            instruction: pick next
          node_a:
            model: m
            instruction: a
          node_b:
            model: m
            instruction: b
        workflow:
          nodes: [router, node_a, node_b]
          entry: router
          edges:
    """) + edges_block


# ---------------------------------------------------------------------------
# _is_dynamic_to helper
# ---------------------------------------------------------------------------

def test_is_dynamic_to_template():
    assert _is_dynamic_to("{{state.router.next_node}}") is True


def test_is_dynamic_to_plain_string():
    assert _is_dynamic_to("node_a") is False


def test_is_dynamic_to_list():
    assert _is_dynamic_to(["a", "b"]) is False


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_dynamic_to_parses_in_edge_config():
    e = EdgeConfig(**{"from": "router", "to": "{{state.router.next_node}}"})
    assert e.to == "{{state.router.next_node}}"


def test_dynamic_to_with_allowed_targets():
    e = EdgeConfig(**{
        "from": "router",
        "to": "{{state.router.next}}",
        "allowed_targets": ["node_a", "node_b"],
    })
    assert e.allowed_targets == ["node_a", "node_b"]


def test_allowed_targets_on_static_to_raises():
    with pytest.raises(ValidationError, match="allowed_targets"):
        EdgeConfig(**{"from": "a", "to": "b", "allowed_targets": ["c"]})


def test_dynamic_to_skips_node_set_validation(tmp_path: Path):
    content = textwrap.dedent("""\
        name: dyn_skip
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          router: {model: m, instruction: pick}
          node_a: {model: m, instruction: a}
          node_b: {model: m, instruction: b}
        workflow:
          nodes: [router, node_a, node_b]
          entry: router
          edges:
            - from: router
              to: "{{state.router.next_node}}"
    """)
    cfg = _load(tmp_path, content)
    assert cfg.workflow.edges[0].to == "{{state.router.next_node}}"


def test_dynamic_to_allowed_targets_unknown_node_raises(tmp_path: Path):
    content = textwrap.dedent("""\
        name: dyn_bad
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          router: {model: m, instruction: pick}
          node_a: {model: m, instruction: a}
          node_b: {model: m, instruction: b}
        workflow:
          nodes: [router, node_a, node_b]
          entry: router
          edges:
            - from: router
              to: "{{state.router.next_node}}"
              allowed_targets: [node_a, nonexistent]
    """)
    with pytest.raises((ValidationError, ValueError)):
        _load(tmp_path, content)


# ---------------------------------------------------------------------------
# build_workflow: dispatch node injection
# ---------------------------------------------------------------------------

def test_dynamic_to_injects_dispatch_node(tmp_path: Path):
    content = textwrap.dedent("""\
        name: dyn_test2
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          router: {model: m, instruction: pick}
          node_a: {model: m, instruction: a}
          node_b: {model: m, instruction: b}
        workflow:
          nodes: [router, node_a, node_b]
          entry: router
          edges:
            - from: router
              to: "{{state.router.next_node}}"
              allowed_targets: [node_a, node_b]
    """)
    cfg = _load(tmp_path, content)
    wf = build_workflow(cfg)

    # Dispatch node should add edges for each candidate target route
    routes = {e.route for e in wf.edges if e.route is not None}
    assert "node_a" in routes
    assert "node_b" in routes


def test_dynamic_to_without_allowed_targets_wires_all_nodes(tmp_path: Path):
    content = textwrap.dedent("""\
        name: dyn_test3
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          router: {model: m, instruction: pick}
          node_a: {model: m, instruction: a}
          node_b: {model: m, instruction: b}
        workflow:
          nodes: [router, node_a, node_b]
          entry: router
          edges:
            - from: router
              to: "{{state.router.next_node}}"
    """)
    cfg = _load(tmp_path, content)
    wf = build_workflow(cfg)

    # All 3 workflow nodes (router, node_a, node_b) should be reachable as routes
    routes = {e.route for e in wf.edges if e.route is not None}
    assert "node_a" in routes
    assert "node_b" in routes


# ---------------------------------------------------------------------------
# EdgeConfig: loop + dynamic to is rejected
# ---------------------------------------------------------------------------

def test_loop_not_allowed_with_dynamic_to():
    with pytest.raises(ValidationError, match="loop"):
        EdgeConfig(**{
            "from": "a",
            "to": "{{state.a.next}}",
            "loop": {"max_iterations": 3},
        })
