"""Tests for switch/case edge sugar (loader expansion)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from modular_agent_designer.config.loader import _expand_switch_edges, _switch_expr_to_eval, load_workflow
from modular_agent_designer.workflow.builder import build_workflow


def _load(tmp_path: Path, content: str):
    p = tmp_path / "wf.yaml"
    p.write_text(content)
    return load_workflow(p)


def _switch_yaml(edges_block: str) -> str:
    return textwrap.dedent("""\
        name: switch_test
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          classifier:
            model: m
            instruction: classify
          handle_urgent:
            model: m
            instruction: urgent
          handle_normal:
            model: m
            instruction: normal
          handle_other:
            model: m
            instruction: other
        workflow:
          nodes: [classifier, handle_urgent, handle_normal, handle_other]
          entry: classifier
          edges:
    """) + edges_block


# ---------------------------------------------------------------------------
# _switch_expr_to_eval helpers
# ---------------------------------------------------------------------------

def test_switch_expr_state_template_single_key():
    expr = _switch_expr_to_eval("{{state.category}}", "src")
    assert expr == "state.get('category', None)"


def test_switch_expr_state_template_nested():
    expr = _switch_expr_to_eval("{{state.classifier.category}}", "src")
    assert "state.get('classifier'" in expr
    assert ".get('category'" in expr


def test_switch_expr_eval_dict():
    expr = _switch_expr_to_eval({"eval": "len(state['items'])"}, "src")
    assert expr == "len(state['items'])"


def test_switch_expr_invalid_plain_string():
    with pytest.raises(ValueError, match="template"):
        _switch_expr_to_eval("not_a_template", "src")


def test_switch_expr_invalid_dict_no_eval():
    with pytest.raises(ValueError, match="eval"):
        _switch_expr_to_eval({"other": "x"}, "src")


def test_switch_expr_invalid_type():
    with pytest.raises(ValueError, match="template"):
        _switch_expr_to_eval(42, "src")


# ---------------------------------------------------------------------------
# _expand_switch_edges raw-dict expansion
# ---------------------------------------------------------------------------

def test_expand_switch_creates_condition_edges():
    raw = {
        "workflow": {
            "edges": [
                {
                    "from": "classifier",
                    "switch": "{{state.category}}",
                    "cases": {"urgent": "handle_urgent", "normal": "handle_normal"},
                    "default": "handle_other",
                }
            ]
        }
    }
    _expand_switch_edges(raw)
    edges = raw["workflow"]["edges"]
    assert len(edges) == 3  # 2 cases + 1 default

    froms = {e["from"] for e in edges}
    assert froms == {"classifier"}

    tos = {e["to"] for e in edges}
    assert tos == {"handle_urgent", "handle_normal", "handle_other"}

    # Cases should have eval conditions; default should be "default" sentinel
    default_edges = [e for e in edges if e.get("condition") == "default"]
    eval_edges = [e for e in edges if isinstance(e.get("condition"), dict)]
    assert len(default_edges) == 1
    assert len(eval_edges) == 2


def test_expand_switch_no_default():
    raw = {
        "workflow": {
            "edges": [
                {
                    "from": "n",
                    "switch": "{{state.x}}",
                    "cases": {"a": "t1"},
                }
            ]
        }
    }
    _expand_switch_edges(raw)
    edges = raw["workflow"]["edges"]
    assert len(edges) == 1
    assert edges[0]["condition"] != "default"


def test_expand_switch_missing_from_raises():
    raw = {"workflow": {"edges": [{"switch": "{{state.x}}", "cases": {"a": "b"}}]}}
    with pytest.raises(ValueError, match="from"):
        _expand_switch_edges(raw)


def test_expand_switch_empty_cases_raises():
    raw = {"workflow": {"edges": [{"from": "n", "switch": "{{state.x}}", "cases": {}}]}}
    with pytest.raises(ValueError, match="cases"):
        _expand_switch_edges(raw)


def test_expand_switch_non_switch_edges_preserved():
    raw = {
        "workflow": {
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "c", "switch": "{{state.x}}", "cases": {"v": "d"}},
            ]
        }
    }
    _expand_switch_edges(raw)
    edges = raw["workflow"]["edges"]
    # First edge unchanged, second expanded to 1 case edge
    assert any(e.get("to") == "b" for e in edges)
    assert any(e.get("to") == "d" for e in edges)


# ---------------------------------------------------------------------------
# Full load_workflow + build_workflow with switch edges
# ---------------------------------------------------------------------------

def test_switch_edge_loads_and_builds(tmp_path: Path):
    content = textwrap.dedent("""\
        name: switch_test
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          classifier: {model: m, instruction: classify}
          handle_urgent: {model: m, instruction: urgent}
          handle_normal: {model: m, instruction: normal}
          handle_other: {model: m, instruction: other}
        workflow:
          nodes: [classifier, handle_urgent, handle_normal, handle_other]
          entry: classifier
          edges:
            - from: classifier
              switch: "{{state.classifier.category}}"
              cases:
                urgent: handle_urgent
                normal: handle_normal
              default: handle_other
    """)
    cfg = _load(tmp_path, content)
    # 2 case edges + 1 default → 3 edges from classifier
    classifier_edges = [e for e in cfg.workflow.edges if e.from_ == "classifier"]
    assert len(classifier_edges) == 3

    wf = build_workflow(cfg)
    routes = [e.route for e in wf.edges if e.route is not None]
    # Expect 3 conditional routes from the router
    assert len([r for r in routes if r.startswith("_route_")]) == 3


def test_switch_with_eval_expr_builds(tmp_path: Path):
    content = textwrap.dedent("""\
        name: switch_test2
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          classifier: {model: m, instruction: classify}
          handle_urgent: {model: m, instruction: urgent}
          handle_normal: {model: m, instruction: normal}
        workflow:
          nodes: [classifier, handle_urgent, handle_normal]
          entry: classifier
          edges:
            - from: classifier
              switch:
                eval: "input.lower()"
              cases:
                urgent: handle_urgent
                normal: handle_normal
    """)
    cfg = _load(tmp_path, content)
    assert len(cfg.workflow.edges) == 2
    build_workflow(cfg)  # should not raise


def test_switch_unknown_target_raises(tmp_path: Path):
    content = textwrap.dedent("""\
        name: switch_bad
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          classifier: {model: m, instruction: classify}
        workflow:
          nodes: [classifier]
          entry: classifier
          edges:
            - from: classifier
              switch: "{{state.x}}"
              cases:
                a: nonexistent_node
    """)
    with pytest.raises((ValidationError, ValueError)):
        _load(tmp_path, content)
