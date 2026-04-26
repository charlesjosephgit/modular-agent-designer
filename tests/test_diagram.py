"""Tests for the Mermaid diagram generator."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from modular_agent_designer.cli import main
from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.visualize.mermaid import render_mermaid

WORKFLOWS = Path(__file__).parent.parent / "workflows"


def _render(filename: str) -> str:
    cfg = load_workflow(str(WORKFLOWS / filename))
    return render_mermaid(cfg)


def test_render_hello_world():
    out = _render("hello_world.yaml")
    assert out.startswith("flowchart TD\n")
    assert "START((start))" in out
    # Entry arrow exists
    cfg = load_workflow(str(WORKFLOWS / "hello_world.yaml"))
    assert f"START --> {cfg.workflow.entry}" in out
    # One node line per workflow node
    for node in cfg.workflow.nodes:
        assert node in out


def test_render_conditional():
    out = _render("conditional_workflow.yaml")
    assert 'classifier -. "technical" .-> tech_expert' in out
    assert 'classifier -. "creative" .-> creative_expert' in out


def test_render_complex_conditions():
    out = _render("complex_conditions.yaml")
    # Eval condition
    assert "eval:" in out
    # List condition (billing | sales)
    assert "billing" in out and "sales" in out
    # Default fallback
    assert "default" in out


def test_render_sub_agents():
    out = _render("sub_agent_example.yaml")
    # Sub-agent subgraph is emitted
    assert "subgraph" in out
    assert "search_specialist" in out
    assert "analysis_specialist" in out
    # Dotted edges from coordinator to sub-agents
    assert "coordinator -.-> search_specialist" in out
    assert "coordinator -.-> analysis_specialist" in out


def test_cli_diagram_stdout():
    runner = CliRunner()
    result = runner.invoke(main, ["diagram", str(WORKFLOWS / "hello_world.yaml")])
    assert result.exit_code == 0
    assert "flowchart TD" in result.output


def test_cli_diagram_output_file(tmp_path):
    out_file = tmp_path / "out.mmd"
    runner = CliRunner()
    result = runner.invoke(
        main, ["diagram", str(WORKFLOWS / "conditional_workflow.yaml"), "--output", str(out_file)]
    )
    assert result.exit_code == 0
    assert out_file.exists()
    content = out_file.read_text()
    assert "flowchart TD" in content
    assert "classifier" in content


def test_cli_diagram_missing_file():
    runner = CliRunner()
    result = runner.invoke(main, ["diagram", "nonexistent.yaml"])
    assert result.exit_code != 0
