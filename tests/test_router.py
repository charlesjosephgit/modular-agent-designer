"""Unit tests for _matches and edge-coherence schema validation."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.config.schema import EvalCondition
from modular_agent_designer.workflow.builder import _matches, build_workflow


# ---------------------------------------------------------------------------
# _matches — scalar / list
# ---------------------------------------------------------------------------

def test_scalar_exact_match():
    assert _matches("tech", "tech", {}, "tech") is True


def test_scalar_case_sensitive():
    assert _matches("tech", "Tech", {}, "Tech") is False


def test_scalar_strips_whitespace():
    assert _matches("tech", "tech", {}, " tech ") is True


def test_scalar_int_condition():
    assert _matches(42, "42", {}, "42") is True
    assert _matches(42, "43", {}, "43") is False


def test_list_or_match():
    cond = ["billing", "sales"]
    assert _matches(cond, "billing", {}, "billing") is True
    assert _matches(cond, "sales", {}, "sales") is True
    assert _matches(cond, "other", {}, "other") is False


def test_list_or_mixed_types():
    cond = ["yes", 1, True]
    assert _matches(cond, "yes", {}, "yes") is True
    assert _matches(cond, "1", {}, "1") is True
    assert _matches(cond, "True", {}, "True") is True


# ---------------------------------------------------------------------------
# _matches — EvalCondition: basic
# ---------------------------------------------------------------------------

def test_eval_true():
    cond = EvalCondition(eval="input == 'tech'")
    assert _matches(cond, "tech", {}, "tech") is True


def test_eval_false():
    cond = EvalCondition(eval="input == 'tech'")
    assert _matches(cond, "other", {}, "other") is False


def test_eval_state_access():
    cond = EvalCondition(eval="state.get('is_vip') == True")
    assert _matches(cond, "", {"is_vip": True}, None) is True
    assert _matches(cond, "", {"is_vip": False}, None) is False


def test_eval_nested_state():
    cond = EvalCondition(eval="state['user']['tier'] == 'gold'")
    assert _matches(cond, "", {"user": {"tier": "gold"}}, None) is True


# ---------------------------------------------------------------------------
# _matches — EvalCondition: safe builtins
# ---------------------------------------------------------------------------

def test_eval_len_builtin():
    cond = EvalCondition(eval="len(state['items']) > 2")
    assert _matches(cond, "", {"items": [1, 2, 3]}, None) is True
    assert _matches(cond, "", {"items": [1]}, None) is False


def test_eval_int_builtin():
    cond = EvalCondition(eval="int(input) > 5")
    assert _matches(cond, "10", {}, "10") is True
    assert _matches(cond, "3", {}, "3") is False


def test_eval_any_all_builtins():
    cond = EvalCondition(eval="any(x > 0 for x in state['vals'])")
    assert _matches(cond, "", {"vals": [-1, 0, 3]}, None) is True
    assert _matches(cond, "", {"vals": [-1, -2]}, None) is False


def test_eval_re_search():
    cond = EvalCondition(eval="bool(re.search(r'urgent', input))")
    assert _matches(cond, "urgent help needed", {}, None) is True
    assert _matches(cond, "normal question", {}, None) is False


def test_eval_re_ignorecase():
    cond = EvalCondition(eval="bool(re.search(r'URGENT', input, re.IGNORECASE))")
    assert _matches(cond, "urgent request", {}, None) is True


def test_eval_string_method_allowed():
    cond = EvalCondition(eval="input.lower() == 'urgent'")
    assert _matches(cond, "URGENT", {}, None) is True


# ---------------------------------------------------------------------------
# _matches — EvalCondition: error handling
# ---------------------------------------------------------------------------

def test_eval_missing_key_returns_false_and_warns(caplog):
    import logging
    cond = EvalCondition(eval="state['missing']['key'] == 'x'")
    with caplog.at_level(logging.WARNING, logger="modular_agent_designer.workflow.builder"):
        result = _matches(cond, "", {}, None)
    assert result is False
    assert len(caplog.records) == 1
    assert "missing" in caplog.records[0].message or "treating as False" in caplog.records[0].message


def test_eval_attribute_error_returns_false_and_warns(caplog):
    import logging
    cond = EvalCondition(eval="state.get('x').lower() == 'y'")  # .get returns None, .lower() raises
    with caplog.at_level(logging.WARNING, logger="modular_agent_designer.workflow.builder"):
        result = _matches(cond, "", {}, None)
    assert result is False
    assert caplog.records


def test_eval_name_error_propagates():
    cond = EvalCondition(eval="undefined_variable > 0")
    with pytest.raises(ValueError, match="not allowed|unknown name"):
        _matches(cond, "", {}, None)


def test_eval_syntax_error_propagates():
    cond = EvalCondition(eval="this is not valid python !!!")
    with pytest.raises(SyntaxError):
        _matches(cond, "", {}, None)


def test_eval_no_dangerous_builtins():
    # __import__ should not be accessible
    cond = EvalCondition(eval="__import__('os').getcwd()")
    with pytest.raises(ValueError, match="not allowed|unknown name"):
        _matches(cond, "", {}, None)


def test_eval_rejects_dunder_attribute():
    cond = EvalCondition(eval="state.__class__")
    with pytest.raises(ValueError, match="not allowed"):
        _matches(cond, "", {}, None)


def test_eval_rejects_unsupported_attribute():
    cond = EvalCondition(eval="state.keys()")
    with pytest.raises(ValueError, match="not allowed"):
        _matches(cond, "", {}, None)


# ---------------------------------------------------------------------------
# Schema-level edge-coherence validation
# ---------------------------------------------------------------------------

def _load(tmp_path: Path, content: str):
    p = tmp_path / "wf.yaml"
    p.write_text(content)
    return load_workflow(p)


def test_multiple_defaults_rejected(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: bad_defaults
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
              to: a
              condition: default
            - from: src
              to: b
              condition: default
          entry: src
    """)
    with pytest.raises((ValidationError, ValueError), match="multiple default"):
        _load(tmp_path, yaml)


def test_mixed_conditional_unconditional_rejected(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: bad_mix
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
              to: a
            - from: src
              to: b
              condition: "val"
          entry: src
    """)
    with pytest.raises((ValidationError, ValueError), match="mixes unconditional"):
        _load(tmp_path, yaml)


# ---------------------------------------------------------------------------
# Build-level: eval condition wired correctly through Workflow
# ---------------------------------------------------------------------------

def test_eval_condition_workflow_edges(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: eval_edge_test
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
          c:
            model: m
            instruction: c
        workflow:
          nodes: [src, a, b, c]
          edges:
            - from: src
              to: a
              condition:
                eval: "len(state.get('items', [])) > 3"
            - from: src
              to: b
              condition: "tech"
            - from: src
              to: c
              condition: default
          entry: src
    """)
    cfg = _load(tmp_path, yaml)
    wf = build_workflow(cfg)
    # START -> src, src -> src_error_router, error router -> success gate,
    # success gate -> src_router, router -> a/b/c.
    routes = [e.route for e in wf.edges]
    assert "_ok" in routes
    assert "_route_0" in routes
    assert "_route_1" in routes
    assert "_route_2" in routes
