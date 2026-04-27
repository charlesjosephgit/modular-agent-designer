"""Unit tests for retry configuration and on_error routing."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.config.schema import RetryConfig
from modular_agent_designer.nodes.agent_node import _compute_retry_delay
from modular_agent_designer.workflow.builder import build_workflow


def _load(tmp_path: Path, content: str):
    p = tmp_path / "wf.yaml"
    p.write_text(content)
    return load_workflow(p)


# ---------------------------------------------------------------------------
# RetryConfig schema validation
# ---------------------------------------------------------------------------


def test_retry_config_defaults():
    rc = RetryConfig()
    assert rc.max_retries == 3
    assert rc.backoff == "fixed"
    assert rc.delay_seconds == 1.0


def test_retry_config_custom():
    rc = RetryConfig(max_retries=5, backoff="exponential", delay_seconds=2.0)
    assert rc.max_retries == 5
    assert rc.backoff == "exponential"
    assert rc.delay_seconds == 2.0


def test_retry_config_min_retries():
    with pytest.raises(ValidationError):
        RetryConfig(max_retries=0)


def test_retry_config_max_retries():
    with pytest.raises(ValidationError):
        RetryConfig(max_retries=11)


def test_retry_config_negative_delay():
    with pytest.raises(ValidationError):
        RetryConfig(delay_seconds=-1)


# ---------------------------------------------------------------------------
# Retry delay computation
# ---------------------------------------------------------------------------


def test_fixed_delay():
    cfg = RetryConfig(backoff="fixed", delay_seconds=2.0)
    assert _compute_retry_delay(cfg, 1) == 2.0
    assert _compute_retry_delay(cfg, 2) == 2.0
    assert _compute_retry_delay(cfg, 3) == 2.0


def test_exponential_delay():
    cfg = RetryConfig(backoff="exponential", delay_seconds=1.0)
    assert _compute_retry_delay(cfg, 1) == 1.0   # 1 * 2^0
    assert _compute_retry_delay(cfg, 2) == 2.0   # 1 * 2^1
    assert _compute_retry_delay(cfg, 3) == 4.0   # 1 * 2^2


def test_delay_none_config():
    assert _compute_retry_delay(None, 1) == 0


# ---------------------------------------------------------------------------
# Agent with retry in YAML
# ---------------------------------------------------------------------------


def test_agent_retry_config_parsed(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: retry_test
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller:
            model: m
            instruction: call API
            retry:
              max_retries: 5
              backoff: exponential
              delay_seconds: 0.5
        workflow:
          nodes: [caller]
          edges: []
          entry: caller
    """)
    cfg = _load(tmp_path, yaml)
    caller = cfg.agents["caller"]
    assert caller.retry is not None
    assert caller.retry.max_retries == 5
    assert caller.retry.backoff == "exponential"
    assert caller.retry.delay_seconds == 0.5


def test_agent_without_retry(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: no_retry
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          simple:
            model: m
            instruction: simple agent
        workflow:
          nodes: [simple]
          edges: []
          entry: simple
    """)
    cfg = _load(tmp_path, yaml)
    assert cfg.agents["simple"].retry is None


# ---------------------------------------------------------------------------
# on_error edge validation
# ---------------------------------------------------------------------------


def test_on_error_cannot_have_condition(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: bad_on_error
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
              condition: "something"
          entry: a
    """)
    with pytest.raises((ValidationError, ValueError), match="on_error"):
        _load(tmp_path, yaml)


# ---------------------------------------------------------------------------
# Build-level: on_error edge wiring
# ---------------------------------------------------------------------------


def test_on_error_edge_creates_error_router(tmp_path: Path):
    yaml = textwrap.dedent("""\
        name: error_route_test
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          caller:
            model: m
            instruction: call
            retry:
              max_retries: 2
          success:
            model: m
            instruction: success
          error:
            model: m
            instruction: error
        workflow:
          nodes: [caller, success, error]
          edges:
            - from: caller
              to: success
            - from: caller
              to: error
              on_error: true
          entry: caller
    """)
    cfg = _load(tmp_path, yaml)
    wf = build_workflow(cfg)

    # Edges: START→caller, caller→success, caller→error_router, error_router→error
    routes = [e.route for e in wf.edges if e.route is not None]
    assert "_error_0" in routes
