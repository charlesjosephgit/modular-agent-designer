"""Tests for workflow/builder.py."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.workflow.builder import build_workflow


HELLO_WORLD_YAML = textwrap.dedent("""\
    name: hello_world
    models:
      local_fast:
        provider: ollama
        model: ollama/gemma4:e4b
    agents:
      greeter:
        model: local_fast
        instruction: "Greet {{state.user_input.topic}}"
    workflow:
      nodes: [greeter]
      edges: []
      entry: greeter
""")

THREE_STAGE_YAML = textwrap.dedent("""\
    name: three_stage
    models:
      m1:
        provider: ollama
        model: ollama/gemma4:e4b
    agents:
      a:
        model: m1
        instruction: "Step A"
      b:
        model: m1
        instruction: "Step B using {{state.a}}"
      c:
        model: m1
        instruction: "Step C using {{state.b}}"
    workflow:
      nodes: [a, b, c]
      edges:
        - from: a
          to: b
        - from: b
          to: c
      entry: a
""")


def _load(tmp_path: Path, content: str):
    p = tmp_path / "wf.yaml"
    p.write_text(content)
    return load_workflow(p)


def test_build_workflow_returns_workflow_instance(tmp_path: Path):
    from google.adk import Workflow

    cfg = _load(tmp_path, HELLO_WORLD_YAML)
    wf = build_workflow(cfg)
    assert isinstance(wf, Workflow)
    assert wf.name == "hello_world"


def test_complex_branching_supported(tmp_path: Path):
    complex_yaml = textwrap.dedent("""\
        name: complex_branching
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          router:
            model: m
            instruction: Router
          a:
            model: m
            instruction: A
          b:
            model: m
            instruction: B
          c:
            model: m
            instruction: C
        workflow:
          nodes: [router, a, b, c]
          edges:
            - from: router
              to: a
              condition: ["val1", "val2"]
            - from: router
              to: b
              condition: true
            - from: router
              to: c
              condition: default
          entry: router
    """)
    cfg = _load(tmp_path, complex_yaml)
    wf = build_workflow(cfg)

    # Edges include START -> router, router -> router_error_router,
    # router_error_router -> success gate, success gate -> router_router,
    # router_router -> a (_route_0), router_router -> b (_route_1),
    # router_router -> c (_route_2)

    # Check that deterministic route labels are used
    routes = [edge.route for edge in wf.edges]
    assert None in routes
    assert "_ok" in routes
    assert "_route_0" in routes
    assert "_route_1" in routes
    assert "_route_2" in routes


def test_eval_condition_in_schema(tmp_path: Path):
    eval_yaml = textwrap.dedent("""\
        name: eval_test
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          classifier:
            model: m
            instruction: Classify
          handler_a:
            model: m
            instruction: A
          handler_b:
            model: m
            instruction: B
        workflow:
          nodes: [classifier, handler_a, handler_b]
          edges:
            - from: classifier
              to: handler_a
              condition:
                eval: "state.get('user_input', {}).get('is_vip') == True"
            - from: classifier
              to: handler_b
              condition: default
          entry: classifier
    """)
    cfg = _load(tmp_path, eval_yaml)
    wf = build_workflow(cfg)

    # START -> classifier, classifier -> classifier_error_router,
    # classifier_error_router -> success gate, success gate -> classifier_router,
    # classifier_router -> handler_a (_route_0),
    # classifier_router -> handler_b (_route_1)

    routes = [edge.route for edge in wf.edges]
    assert "_ok" in routes
    assert "_route_0" in routes
    assert "_route_1" in routes
