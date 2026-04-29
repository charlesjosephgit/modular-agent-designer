"""Tests for the validate, list CLI commands and --input-file/stdin on run."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from modular_agent_designer.cli import _is_public_state_key, main

_VALID_YAML = textwrap.dedent("""\
    name: hello
    models:
      local:
        provider: ollama
        model: ollama/gemma4:e4b
    agents:
      greeter:
        model: local
        instruction: Say hello.
    workflow:
      nodes: [greeter]
      edges: []
      entry: greeter
""")

_MULTI_YAML = textwrap.dedent("""\
    name: multi
    description: A multi-node workflow for testing.
    models:
      fast:
        provider: ollama
        model: ollama/gemma4:e4b
        temperature: 0.5
      smart:
        provider: ollama
        model: ollama_chat/llama3.2
        max_tokens: 512
    tools:
      fetcher:
        type: builtin
        name: fetch_url
    agents:
      researcher:
        model: fast
        instruction: Research.
        tools: [fetcher]
      writer:
        model: smart
        instruction: Write.
    workflow:
      nodes: [researcher, writer]
      edges:
        - from: researcher
          to: writer
      entry: researcher
""")


# ---------------------------------------------------------------------------
# validate command
# ---------------------------------------------------------------------------


def test_validate_valid_schema_only(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["validate", str(p), "--skip-build"])
    assert result.exit_code == 0
    assert "OK" in result.output
    assert "schema" in result.output
    assert "hello" in result.output


def test_top_level_version_option() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "modular-agent-designer" in result.output


def test_run_dry_run_does_not_require_input(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--dry-run"])
    assert result.exit_code == 0
    assert "Dry run OK" in result.output
    assert "Workflow graph" in result.output


def test_run_verbose_dry_run_outputs_plan(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--dry-run", "--verbose"])
    assert result.exit_code == 0
    assert "max_llm_calls" in result.output


def test_internal_state_keys_are_filtered() -> None:
    assert _is_public_state_key("result") is True
    assert _is_public_state_key("__mda_dedup__fetch") is False
    assert _is_public_state_key("_loop_writer_reviewer_iter") is False
    assert _is_public_state_key("_error_worker") is False
    assert _is_public_state_key("_dispatch_router_0") is False
    assert _is_public_state_key("worker__thinking") is False


def test_validate_missing_file(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["validate", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 1
    assert "Error" in result.output or "Error" in (result.output + (result.stderr or ""))


def test_validate_bad_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("key: [unclosed")
    runner = CliRunner()
    result = runner.invoke(main, ["validate", str(p), "--skip-build"])
    assert result.exit_code == 1


def test_validate_schema_error(tmp_path: Path) -> None:
    bad = textwrap.dedent("""\
        name: wf
        models:
          m:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          step:
            model: MISSING
            instruction: hi
        workflow:
          nodes: [step]
          edges: []
          entry: step
    """)
    p = tmp_path / "wf.yaml"
    p.write_text(bad)
    runner = CliRunner()
    result = runner.invoke(main, ["validate", str(p), "--skip-build"])
    assert result.exit_code == 1


def test_validate_reports_counts(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_MULTI_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["validate", str(p), "--skip-build"])
    assert result.exit_code == 0
    assert "2 agent" in result.output
    assert "1 tool" in result.output
    assert "2 model" in result.output


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------


def test_list_shows_models(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_MULTI_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["list", str(p)])
    assert result.exit_code == 0
    assert "fast" in result.output
    assert "smart" in result.output
    assert "gemma4" in result.output


def test_list_shows_tools(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_MULTI_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["list", str(p)])
    assert result.exit_code == 0
    assert "fetcher" in result.output
    assert "builtin" in result.output


def test_list_shows_agents(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_MULTI_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["list", str(p)])
    assert result.exit_code == 0
    assert "researcher" in result.output
    assert "writer" in result.output


def test_list_shows_entry_and_edges(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_MULTI_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["list", str(p)])
    assert result.exit_code == 0
    assert "entry: researcher" in result.output
    assert "researcher -> writer" in result.output


def test_list_missing_file(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# run --input-file and stdin
# ---------------------------------------------------------------------------


def test_run_rejects_both_input_and_input_file(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    runner = CliRunner()
    result = runner.invoke(
        main, ["run", str(p), "--input", '{"x": 1}', "--input-file", "some.json"]
    )
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_run_rejects_neither_input(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p)])
    assert result.exit_code == 1
    assert "required" in result.output


def test_run_input_file_reads_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    inp = tmp_path / "input.json"
    inp.write_text('{"topic": "test"}', encoding="utf-8")

    # Patch _run_workflow so we don't actually hit ADK
    import modular_agent_designer.cli as cli_mod
    import asyncio

    async def _fake_run(workflow, input_data, max_llm_calls=20):
        return {"greeter": "hello", "user_input": input_data}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--input-file", str(inp)])
    # Load and build may fail without Ollama but _run_workflow is patched after build
    # Just verify that the file was read (no "mutually exclusive" error)
    assert "mutually exclusive" not in result.output
    assert "required" not in result.output


def test_run_stdin_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod

    async def _fake_run(workflow, input_data, max_llm_calls=20):
        return {"user_input": input_data}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main, ["run", str(p), "--input-file", "-"],
        input='{"topic": "stdin"}'
    )
    assert "mutually exclusive" not in result.output
    assert "required" not in result.output


def test_run_input_file_not_found(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--input-file", str(tmp_path / "missing.json")])
    assert result.exit_code == 1
    assert "not found" in result.output
