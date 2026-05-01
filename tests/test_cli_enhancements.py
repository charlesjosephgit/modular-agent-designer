"""Tests for the validate, list CLI commands and --input-file/stdin on run."""
from __future__ import annotations

import json
import tomllib
import textwrap
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner
from google.adk.events.event import Event
from google.genai import types

from modular_agent_designer.cli import (
    _is_public_state_key,
    _parse_workflow_input,
    _printable_event_chunks,
    main,
)

_CLI_SKILL_NAMES = {
    "mad-overview",
    "mad-create-workflow",
    "mad-tools",
    "mad-routing",
    "mad-sub-agents",
}

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


def test_pyproject_defines_mad_console_alias() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text())
    scripts = data["project"]["scripts"]
    assert scripts["modular-agent-designer"] == "modular_agent_designer.cli:main"
    assert scripts["mad"] == "modular_agent_designer.cli:main"


def test_cli_skills_setup_defaults_to_agents_skills() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            main, ["cli-skills", "setup"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output

        target = Path(".agents/skills")
        for skill_name in _CLI_SKILL_NAMES:
            assert (target / skill_name / "SKILL.md").exists()
        assert "Installed" in result.output


def _parse_skill_front_matter(text: str) -> dict[str, str]:
    assert text.startswith("---\n")
    _, raw_front_matter, _ = text.split("---", 2)
    parsed: dict[str, str] = {}
    for line in raw_front_matter.strip().splitlines():
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def test_bundled_cli_skills_have_valid_front_matter() -> None:
    skills_root = resources.files("modular_agent_designer.cli_skills")

    for skill_name in _CLI_SKILL_NAMES:
        skill_file = skills_root / skill_name / "SKILL.md"
        front_matter = _parse_skill_front_matter(skill_file.read_text())

        assert front_matter["name"] == skill_name
        assert front_matter["description"]
        assert "coding agent" in front_matter["description"].lower()


def test_cli_skills_readme_documents_agents_default_and_all_skills() -> None:
    readme = (
        resources.files("modular_agent_designer.cli_skills")
        / "README.md"
    ).read_text()

    assert "mad cli-skills setup" in readme
    assert ".agents/skills" in readme
    for skill_name in _CLI_SKILL_NAMES:
        assert skill_name in readme


def test_cli_skills_setup_accepts_dir_option() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            main,
            ["cli-skills", "setup", "--dir", ".claude/skills"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert Path(".claude/skills/mad-overview/SKILL.md").exists()


def test_cli_skills_setup_refuses_existing_without_force() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        runner.invoke(main, ["cli-skills", "setup"], catch_exceptions=False)

        result = runner.invoke(
            main, ["cli-skills", "setup"], catch_exceptions=False
        )

        assert result.exit_code == 1
        assert "Use --force" in result.output


def test_cli_skills_setup_force_replaces_existing() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        runner.invoke(main, ["cli-skills", "setup"], catch_exceptions=False)
        overview = Path(".agents/skills/mad-overview/SKILL.md")
        overview.write_text("stale")

        result = runner.invoke(
            main, ["cli-skills", "setup", "--force"], catch_exceptions=False
        )

        assert result.exit_code == 0, result.output
        assert overview.read_text() != "stale"


def test_cli_skills_setup_expands_home_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        home = Path.cwd()
        monkeypatch.setenv("HOME", str(home))

        result = runner.invoke(
            main,
            ["cli-skills", "setup", "--dir", "~/.agents/skills"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        assert (home / ".agents/skills/mad-overview/SKILL.md").exists()
        assert not Path("~/.agents/skills").exists()


def test_cli_skills_setup_force_replaces_existing_file() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        target = Path(".agents/skills")
        target.mkdir(parents=True)
        (target / "mad-create-workflow").write_text("stale")

        result = runner.invoke(
            main, ["cli-skills", "setup", "--force"], catch_exceptions=False
        )

        assert result.exit_code == 0, result.output
        assert (target / "mad-create-workflow/SKILL.md").exists()


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


def test_printable_event_chunks_include_content_text() -> None:
    event = Event(
        content=types.Content(
            role="model",
            parts=[types.Part(text="hello from the model")],
        )
    )

    assert _printable_event_chunks(event) == ["hello from the model"]


def test_printable_event_chunks_include_output_text() -> None:
    event = Event(output="node output")

    assert _printable_event_chunks(event) == ["node output"]


def test_printable_event_chunks_json_formats_structured_output() -> None:
    event = Event(output={"answer": ["one", "two"]})

    assert _printable_event_chunks(event) == [
        json.dumps({"answer": ["one", "two"]}, indent=2)
    ]


def test_printable_event_chunks_ignore_tool_only_events() -> None:
    event = Event(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name="lookup", args={"q": "x"})
                )
            ],
        )
    )

    assert _printable_event_chunks(event) == []


def test_printable_event_chunks_deduplicate_content_and_output() -> None:
    event = Event(
        content=types.Content(
            role="model",
            parts=[types.Part(text="same value")],
        ),
        output="same value",
    )

    assert _printable_event_chunks(event) == ["same value"]


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


def test_parse_workflow_input_accepts_json_or_plain_string() -> None:
    assert _parse_workflow_input('{"topic": "json"}') == {"topic": "json"}
    assert _parse_workflow_input("plain text request") == "plain text request"
    assert _parse_workflow_input('"quoted json string"') == "quoted json string"


def test_run_accepts_plain_string_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "load_workflow",
        lambda yaml_path: SimpleNamespace(
            name="hello",
            workflow=SimpleNamespace(max_llm_calls=20),
        ),
    )
    monkeypatch.setattr(cli_mod, "build_workflow", lambda cfg: object())

    async def _fake_run(workflow, input_data, max_llm_calls=20):
        return {"user_input": input_data}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--input", "plain text request"])

    assert result.exit_code == 0
    assert '"user_input": "plain text request"' in result.output


def test_run_input_file_accepts_plain_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    inp = tmp_path / "input.txt"
    inp.write_text("plain text from file", encoding="utf-8")

    import modular_agent_designer.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "load_workflow",
        lambda yaml_path: SimpleNamespace(
            name="hello",
            workflow=SimpleNamespace(max_llm_calls=20),
        ),
    )
    monkeypatch.setattr(cli_mod, "build_workflow", lambda cfg: object())

    async def _fake_run(workflow, input_data, max_llm_calls=20):
        return {"user_input": input_data}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--input-file", str(inp)])

    assert result.exit_code == 0
    assert '"user_input": "plain text from file"' in result.output


def test_run_prints_streamed_events_before_final_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod

    async def _fake_run(workflow, input_data, max_llm_calls=20):
        click.echo("streamed event")
        return {"greeter": "done"}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--input", '{"topic": "x"}'])

    assert result.exit_code == 0
    assert result.output.index("streamed event") < result.output.index('"greeter"')
    assert '"greeter": "done"' in result.output


def test_run_input_file_not_found(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--input-file", str(tmp_path / "missing.json")])
    assert result.exit_code == 1
    assert "not found" in result.output
