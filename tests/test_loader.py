"""Tests for config/loader.py and config/schema.py."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.config.schema import RootConfig


VALID_YAML = textwrap.dedent("""\
    name: test_wf
    models:
      local:
        provider: ollama
        model: ollama/gemma4:e4b
    agents:
      step_one:
        model: local
        instruction: "Say hello to {{state.user_input.name}}."
    workflow:
      nodes: [step_one]
      edges: []
      entry: step_one
""")


def test_valid_yaml_loads(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(VALID_YAML)
    cfg = load_workflow(p)
    assert isinstance(cfg, RootConfig)
    assert cfg.name == "test_wf"
    assert "step_one" in cfg.agents


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        load_workflow(tmp_path / "nonexistent.yaml")


def test_bad_yaml_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("key: [unclosed")
    with pytest.raises(ValueError, match="YAML parse error"):
        load_workflow(p)


def test_unknown_model_ref_raises(tmp_path: Path) -> None:
    bad = textwrap.dedent("""\
        name: wf
        models:
          mymodel:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          step:
            model: DOES_NOT_EXIST
            instruction: hi
        workflow:
          nodes: [step]
          edges: []
          entry: step
    """)
    p = tmp_path / "wf.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="DOES_NOT_EXIST"):
        load_workflow(p)


def test_model_prefix_validation_fails(tmp_path: Path) -> None:
    bad = textwrap.dedent("""\
        name: wf
        models:
          bad_model:
            provider: ollama
            model: openai/gpt-4  # wrong prefix for ollama
        agents:
          step:
            model: bad_model
            instruction: hi
        workflow:
          nodes: [step]
          edges: []
          entry: step
    """)
    p = tmp_path / "wf.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError):
        load_workflow(p)


def test_entry_not_in_nodes_raises(tmp_path: Path) -> None:
    bad = textwrap.dedent("""\
        name: wf
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          step:
            model: m
            instruction: hi
        workflow:
          nodes: [step]
          edges: []
          entry: MISSING
    """)
    p = tmp_path / "wf.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="entry"):
        load_workflow(p)


def test_error_message_contains_path(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text("name: wf\nagents: {}\nmodels: {}\nworkflow: {nodes: [], edges: [], entry: x}")
    with pytest.raises(ValueError) as exc_info:
        load_workflow(p)
    assert str(p) in str(exc_info.value)
