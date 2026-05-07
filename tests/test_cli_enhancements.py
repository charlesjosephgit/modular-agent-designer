"""Tests for the validate, list CLI commands and --input-file/stdin on run."""
from __future__ import annotations

import json
import logging
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
    _resolve_final_output_author,
    _resolve_final_output,
    _final_state_for_display,
    main,
)
from modular_agent_designer.cli_output import (
    EventPrinter,
    print_final_output,
    print_final_state,
)
from modular_agent_designer.plugins.tool_availability import (
    TOOL_UNAVAILABLE_OUTPUT_KEY,
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


def test_run_verbose_does_not_enable_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    calls: list[dict] = []

    import modular_agent_designer.cli as cli_mod

    monkeypatch.setattr(
        cli_mod.logging,
        "basicConfig",
        lambda **kwargs: calls.append(kwargs),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--dry-run", "--verbose"])

    assert result.exit_code == 0
    assert calls == []


def test_run_log_level_enables_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    calls: list[dict] = []

    import modular_agent_designer.cli as cli_mod

    monkeypatch.setattr(
        cli_mod.logging,
        "basicConfig",
        lambda **kwargs: calls.append(kwargs),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--dry-run", "--log-level", "INFO"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["level"] == logging.INFO


def test_internal_state_keys_are_filtered() -> None:
    assert _is_public_state_key("result") is True
    assert _is_public_state_key(TOOL_UNAVAILABLE_OUTPUT_KEY) is True
    assert _is_public_state_key("__mda_dedup__fetch") is False
    assert _is_public_state_key("_loop_writer_reviewer_iter") is False
    assert _is_public_state_key("_error_worker") is False
    assert _is_public_state_key("_dispatch_router_0") is False
    assert _is_public_state_key("worker__thinking") is False


def _printer(agent_names=None, workflow_node_names=None) -> EventPrinter:
    return EventPrinter(
        color=False,
        agent_names=agent_names,
        workflow_node_names=workflow_node_names,
    )


def test_event_printer_labels_content_text_with_author(capsys) -> None:
    event = Event(
        author="greeter",
        content=types.Content(
            role="model",
            parts=[types.Part(text="hello from the model")],
        ),
    )

    printer = _printer()
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "Workflow Node: greeter" in out
    assert "[greeter] hello from the model" in out


def test_event_printer_renders_thinking_parts(capsys) -> None:
    event = Event(
        author="greeter",
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="considering the request", thought=True),
                types.Part(text="visible answer"),
            ],
        ),
    )

    printer = _printer()
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "[thinking: greeter] considering the request" in out
    assert "[greeter] visible answer" in out


def test_event_printer_styles_thinking_body_as_dim() -> None:
    printer = EventPrinter(color=False)
    row = printer._make_row(
        "[thinking: greeter]",
        "considering",
        "italic bright_yellow",
        body_style="dim",
    )

    assert row.spans[0].style == "italic bright_yellow"
    assert row.spans[1].style == "dim"


def test_event_printer_renders_thinking_before_tool_calls(capsys) -> None:
    event = Event(
        author="coordinator",
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="I should delegate this.", thought=True),
                types.Part(
                    function_call=types.FunctionCall(
                        name="search_specialist",
                        args={"request": "summarize AI"},
                    )
                ),
            ],
        ),
    )

    printer = _printer(
        agent_names={"coordinator", "search_specialist"},
        workflow_node_names={"coordinator"},
    )
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert out.index("[thinking: coordinator]") < out.index("[tool: search_specialist]")


def test_event_printer_skips_duplicate_final_text_after_partial_stream(capsys) -> None:
    printer = _printer(workflow_node_names={"greeter"})

    printer.handle(
        Event(
            author="greeter",
            node_path="wf@1/greeter@1",
            partial=True,
            content=types.Content(
                role="model",
                parts=[types.Part(text="hello ")],
            ),
        )
    )
    printer.handle(
        Event(
            author="greeter",
            node_path="wf@1/greeter@1",
            partial=True,
            content=types.Content(
                role="model",
                parts=[types.Part(text="world")],
            ),
        )
    )
    printer.handle(
        Event(
            author="greeter",
            node_path="wf@1/greeter@1",
            partial=False,
            content=types.Content(
                role="model",
                parts=[types.Part(text="hello world")],
            ),
        )
    )
    printer.close()
    out = capsys.readouterr().out

    assert out.count("[greeter] hello world") == 1


def test_event_printer_accumulates_partial_thinking_in_one_row(capsys) -> None:
    printer = _printer(workflow_node_names={"coordinator"})

    for chunk in ("The ", "user ", "wants ", "AI"):
        printer.handle(
            Event(
                author="coordinator",
                node_path="wf@1/coordinator@1",
                partial=True,
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=chunk, thought=True)],
                ),
            )
        )
    printer.close()
    out = capsys.readouterr().out

    assert out.count("[thinking: coordinator]") == 1
    assert "[thinking: coordinator] The user wants AI" in out


def test_event_printer_starts_new_thinking_row_after_tool_response(capsys) -> None:
    printer = _printer(
        agent_names={"coordinator", "search_specialist"},
        workflow_node_names={"coordinator"},
    )

    printer.handle(
        Event(
            author="coordinator",
            node_path="wf@1/coordinator@1",
            partial=True,
            content=types.Content(
                role="model",
                parts=[types.Part(text="First thought", thought=True)],
            ),
        )
    )
    printer.handle(
        Event(
            author="coordinator",
            node_path="wf@1/coordinator@1",
            partial=False,
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            name="search_specialist",
                            response={"result": "background summary"},
                        )
                    )
                ],
            ),
        )
    )
    printer.handle(
        Event(
            author="coordinator",
            node_path="wf@1/coordinator@1",
            partial=True,
            content=types.Content(
                role="model",
                parts=[types.Part(text="Second thought", thought=True)],
            ),
        )
    )
    printer.close()
    out = capsys.readouterr().out

    assert out.count("[thinking: coordinator]") == 2
    assert out.index("First thought") < out.index("[tool: search_specialist]")
    assert out.index("[tool: search_specialist]") < out.index("Second thought")


def test_event_printer_cleans_stream_token_spacing(capsys) -> None:
    printer = _printer(workflow_node_names={"coordinator"})

    for chunk in ("search", "_", " special", " ist", " 2", " 0", " 0", "-", " word"):
        printer.handle(
            Event(
                author="coordinator",
                node_path="wf@1/coordinator@1",
                partial=True,
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=chunk, thought=True)],
                ),
            )
        )
    printer.close()
    out = capsys.readouterr().out

    assert "search_specialist 200-word" in out


def test_event_printer_skips_partial_function_call_chunks(capsys) -> None:
    printer = _printer(workflow_node_names={"greeter"})

    printer.handle(
        Event(
            author="greeter",
            node_path="wf@1/greeter@1",
            partial=True,
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="lookup",
                            args={"q": "incomplete"},
                        )
                    )
                ],
            ),
        )
    )
    printer.close()
    out = capsys.readouterr().out

    assert "[tool: lookup]" not in out


def test_event_printer_renders_section_header(capsys) -> None:
    printer = EventPrinter(color=False)

    printer._ensure_section("greeter")
    out = capsys.readouterr().out

    assert "Workflow Node: greeter" in out
    assert "-" * 40 in out


def test_event_printer_appends_multiple_events_to_one_panel(capsys) -> None:
    printer = _printer(workflow_node_names={"greeter"})

    printer.handle(
        Event(
            author="greeter",
            node_path="wf@1/greeter@1",
            content=types.Content(
                role="model",
                parts=[types.Part(text="first chunk")],
            ),
        )
    )
    printer.handle(
        Event(
            author="greeter",
            node_path="wf@1/greeter@1",
            content=types.Content(
                role="model",
                parts=[types.Part(text="second chunk")],
            ),
        )
    )
    printer.close()
    out = capsys.readouterr().out

    assert out.count("Workflow Node: greeter") == 1
    assert "[greeter] first chunk" in out
    assert "[greeter] second chunk" in out


def test_event_printer_marks_agent_section_switches(capsys) -> None:
    printer = _printer()

    printer.handle(
        Event(
            author="coordinator",
            content=types.Content(
                role="model",
                parts=[types.Part(text="delegating")],
            ),
        )
    )
    printer.handle(
        Event(
            author="search_specialist",
            content=types.Content(
                role="model",
                parts=[types.Part(text="searching")],
            ),
        )
    )
    printer.close()
    out = capsys.readouterr().out

    assert "Workflow Node: coordinator" in out
    assert "Workflow Node: search_specialist" in out


def test_event_printer_labels_output_with_author(capsys) -> None:
    event = Event(author="greeter", output="node output")

    printer = _printer()
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "[greeter] node output" in out


def test_event_printer_json_formats_structured_output(capsys) -> None:
    event = Event(author="greeter", output={"answer": ["one", "two"]})

    printer = _printer()
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert '"answer": [' in out
    assert '"one"' in out
    assert '"two"' in out
    assert "[greeter]" in out


def test_event_printer_renders_tool_calls(capsys) -> None:
    event = Event(
        author="greeter",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name="lookup", args={"q": "x"})
                )
            ],
        ),
    )

    printer = _printer()
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "[tool: lookup] ->" in out
    assert "q=" in out


def test_event_printer_renders_tool_responses_with_author(capsys) -> None:
    event = Event(
        author="greeter",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="lookup",
                        response={"result": "ok"},
                    )
                )
            ],
        ),
    )

    printer = _printer()
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "[tool: lookup] <-" in out
    assert '"result": "ok"' in out


def test_event_printer_truncates_tool_response_on_clear_marker(capsys) -> None:
    event = Event(
        author="greeter",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="analysis_specialist",
                        response={
                            "result": (
                                "Please analyze Artificial Intelligence, machine "
                                "learning, deep learning, natural language "
                                "processing, and computer vision in detail."
                            )
                        },
                    )
                )
            ],
        ),
    )

    printer = EventPrinter(color=False, max_line_chars=80)
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "[tool: analysis_specialist] <-" in out
    assert "... truncated" in out
    assert "more chars" not in out
    assert "detail." not in out


def test_event_printer_truncates_tool_call_args(capsys) -> None:
    event = Event(
        author="coordinator",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="transfer_to_agent",
                        args={"request": "x" * 80},
                    )
                )
            ],
        ),
    )

    printer = EventPrinter(color=False, max_line_chars=20)
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "[tool: transfer_to_agent] ->" in out
    assert "... truncated" in out
    assert "x" * 80 not in out


def test_event_printer_wraps_long_tool_call_args(capsys) -> None:
    request = (
        "Provide a comprehensive factual background summary about Artificial "
        "Intelligence (AI), covering its definition, history, and current uses."
    )
    event = Event(
        author="coordinator",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="search_specialist",
                        args={"request": request},
                    )
                )
            ],
        ),
    )

    printer = EventPrinter(color=False, max_line_chars=0)
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "[tool: search_specialist] ->" in out
    assert "Artificial Intelligence" in out
    assert "current uses" in out


def test_event_printer_omits_tool_call_closing_paren_when_truncated(capsys) -> None:
    event = Event(
        author="coordinator",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="transfer_to_agent",
                        args={"request": "x" * 80},
                    )
                )
            ],
        ),
    )

    printer = EventPrinter(color=True, max_line_chars=20)
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "... truncated" in out
    assert "x" * 80 not in out


def test_event_printer_deduplicates_content_and_output(capsys) -> None:
    event = Event(
        author="greeter",
        content=types.Content(
            role="model",
            parts=[types.Part(text="same value")],
        ),
        output="same value",
    )

    printer = _printer()
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert out.count("[greeter] same value") == 1


def test_event_printer_uses_node_info_name_over_author(capsys) -> None:
    """ADK's Workflow stamps `event.author` with the workflow name; the actual
    agent that emitted the event lives on `event.node_info.path`. The printer
    must prefer that so multi-agent runs show per-agent labels, not the
    workflow name on every line.
    """
    event = Event(
        author="my_workflow",
        node_path="my_workflow@1/researcher@1",
        content=types.Content(
            role="model",
            parts=[types.Part(text="searching now")],
        ),
    )

    printer = _printer()
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "[researcher] searching now" in out
    assert "[my_workflow]" not in out


def test_event_printer_falls_back_to_author_when_node_info_empty(capsys) -> None:
    event = Event(
        author="user_supplied",
        content=types.Content(
            role="model",
            parts=[types.Part(text="raw event")],
        ),
    )

    printer = _printer()
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "[user_supplied] raw event" in out


def test_event_printer_attributes_subagent_event_to_subagent(capsys) -> None:
    """Sub-agents declared in YAML run inside their parent agent's wrapper.
    ADK preserves the sub-agent's name on `event.author` but stamps
    `node_info.path` with the parent wrapper's path. Without the agent_names
    hint we'd label these as the parent. With it, the printer should pick
    the sub-agent's actual name.
    """
    event = Event(
        author="search_specialist",
        node_path="wf@1/coordinator@1",
        content=types.Content(
            role="model",
            parts=[types.Part(text="found 3 articles")],
        ),
    )

    printer = _printer(
        agent_names={"coordinator", "search_specialist", "analysis_specialist"}
    )
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "[search_specialist] found 3 articles" in out
    assert "[coordinator]" not in out


def test_event_printer_groups_subagent_under_workflow_node(capsys) -> None:
    printer = _printer(
        agent_names={"coordinator", "search_specialist"},
        workflow_node_names={"coordinator"},
    )

    printer.handle(
        Event(
            author="coordinator",
            node_path="wf@1/coordinator@1",
            content=types.Content(
                role="model",
                parts=[types.Part(text="delegating")],
            ),
        )
    )
    printer.handle(
        Event(
            author="search_specialist",
            node_path="wf@1/coordinator@1",
            content=types.Content(
                role="model",
                parts=[types.Part(text="found 3 articles")],
            ),
        )
    )
    printer.close()
    out = capsys.readouterr().out

    assert out.count("Workflow Node: coordinator") == 1
    assert "Workflow Node: search_specialist" not in out
    assert "[coordinator] delegating" in out
    assert "[sub-agent: search_specialist] found 3 articles" in out


def test_event_printer_keeps_subagent_in_current_workflow_section(capsys) -> None:
    printer = _printer(
        agent_names={"coordinator", "search_specialist"},
        workflow_node_names={"coordinator"},
    )

    printer.handle(
        Event(
            author="coordinator",
            node_path="wf@1/coordinator@1",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="search_specialist",
                            args={"request": "summarize AI"},
                        )
                    )
                ],
            ),
        )
    )
    printer.handle(
        Event(
            author="search_specialist",
            node_path="wf@1/search_specialist@1",
            content=types.Content(
                role="model",
                parts=[types.Part(text="AI summary")],
            ),
        )
    )
    printer.close()
    out = capsys.readouterr().out

    assert out.count("Workflow Node: coordinator") == 1
    assert "Workflow Node: search_specialist" not in out
    assert "[tool: search_specialist] ->" in out
    assert "[sub-agent: search_specialist] AI summary" in out


def test_event_printer_falls_through_synthetic_router_with_agent_names(capsys) -> None:
    """A synthetic `<src>_router` node has `event.author == workflow_name`
    (not in agent_names) and `node_info.name == "<src>_router"`. The printer
    must still label it as the router, not the workflow.
    """
    event = Event(
        author="my_workflow",
        node_path="my_workflow@1/validator_router@1",
        content=types.Content(
            role="model",
            parts=[types.Part(text="routing")],
        ),
    )

    printer = _printer(
        agent_names={"validator", "process_node", "reject_node"},
        workflow_node_names={"validator", "process_node", "reject_node"},
    )
    printer.handle(event)
    printer.close()
    out = capsys.readouterr().out

    assert "[node: validator_router] routing" in out
    assert "[my_workflow]" not in out
    assert "[sub-agent: validator_router]" not in out


def test_print_final_state_wraps_json_in_banner(capsys) -> None:
    print_final_state({"answer": "42"}, color=False)
    out = capsys.readouterr().out

    assert "Final State" in out
    assert "Final Output" not in out
    assert '"answer": "42"' in out


def test_print_final_output_with_author(capsys) -> None:
    print_final_output("the answer is 42", author="writer", color=False)
    out = capsys.readouterr().out

    assert "Final Output (writer)" in out
    assert "the answer is 42" in out


def test_print_final_output_handles_none_value(capsys) -> None:
    print_final_output(None, color=False)
    out = capsys.readouterr().out

    assert "Final Output" in out
    assert "(no output)" in out


def _agent_final_event(author_path: str, *, text: str = "", output=None) -> Event:
    """Build an event that mimics an LlmAgent's final response (the kind ADK
    marks with `node_info.message_as_output=True` in
    `_llm_agent_wrapper.process_llm_agent_output`).
    """
    parts = [types.Part(text=text)] if text else []
    event = Event(
        author="wf",
        node_path=author_path,
        content=types.Content(role="model", parts=parts),
        output=output,
    )
    event.node_info.message_as_output = True
    return event


def test_event_printer_tracks_last_output(capsys) -> None:
    printer = EventPrinter(color=False)

    printer.handle(_agent_final_event(
        "wf@1/researcher@1", output="intermediate",
    ))
    printer.handle(_agent_final_event(
        "wf@1/writer@1", output={"final": "answer"},
    ))
    capsys.readouterr()

    assert printer.last_output == {"final": "answer"}
    assert printer.last_output_author == "writer"


def test_event_printer_ignores_subagent_last_output_when_workflow_nodes_set(capsys) -> None:
    printer = _printer(
        agent_names={"coordinator", "search_specialist", "analysis_specialist"},
        workflow_node_names={"coordinator"},
    )

    coordinator = _agent_final_event(
        "wf@1/coordinator@1", output="coordinator final"
    )
    coordinator.author = "coordinator"
    search = _agent_final_event(
        "wf@1/coordinator@1", output="search specialist final"
    )
    search.author = "search_specialist"

    printer.handle(coordinator)
    printer.handle(search)
    capsys.readouterr()

    assert printer.last_output == "coordinator final"
    assert printer.last_output_author == "coordinator"


def test_event_printer_skips_router_events_for_last_output(capsys) -> None:
    """Synthetic router nodes (validator_router, _join_*, etc.) re-emit the
    source agent's value as their own `event.output` but do NOT set
    `node_info.message_as_output`. Those must be skipped so the workflow's
    real final answer (the last agent's response) wins.
    """
    printer = EventPrinter(color=False)

    printer.handle(_agent_final_event(
        "wf@1/validator@1",
        output={"validation_result": "fail"},
    ))
    # Router event — has output set but no message_as_output flag.
    router_event = Event(
        author="wf",
        node_path="wf@1/validator_router@1",
        output={"validation_result": "fail"},
    )
    printer.handle(router_event)
    printer.handle(_agent_final_event(
        "wf@1/reject_node@1",
        text="Sorry, that input did not validate.",
    ))
    capsys.readouterr()

    assert printer.last_output == "Sorry, that input did not validate."
    assert printer.last_output_author == "reject_node"


def test_event_printer_uses_text_when_output_unset(capsys) -> None:
    """For agents without `output_schema`, the final answer is in
    `content.parts[].text` and `event.output` is unset. The printer should
    fall back to the joined text."""
    printer = EventPrinter(color=False)

    printer.handle(_agent_final_event(
        "wf@1/writer@1", text="Hello, world!",
    ))
    capsys.readouterr()

    assert printer.last_output == "Hello, world!"
    assert printer.last_output_author == "writer"


def test_event_printer_tracks_last_rendered_tool_result(capsys) -> None:
    printer = _printer(
        agent_names={"coordinator", "analysis_specialist"},
        workflow_node_names={"coordinator"},
    )
    event = Event(
        author="coordinator",
        node_path="wf@1/coordinator@1",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="analysis_specialist",
                        response={"result": "analysis complete"},
                    )
                )
            ],
        ),
    )

    printer.handle(event)
    capsys.readouterr()

    assert printer.last_output is None
    assert printer.last_rendered_output == "analysis complete"
    assert printer.last_workflow_node == "coordinator"


def test_resolve_final_output_prefers_state_value() -> None:
    cfg = SimpleNamespace(
        agents={"coordinator": SimpleNamespace(output_key=None)}
    )

    assert (
        _resolve_final_output(
            {"coordinator": "committed final state"},
            cfg,
            "coordinator",
            "event payload",
        )
        == "committed final state"
    )


def test_resolve_final_output_uses_fallback_when_state_value_blank() -> None:
    cfg = SimpleNamespace(
        agents={"coordinator": SimpleNamespace(output_key=None)}
    )

    assert (
        _resolve_final_output(
            {"coordinator": ""},
            cfg,
            "coordinator",
            "streamed fallback",
        )
        == "streamed fallback"
    )


def test_resolve_final_output_honors_output_key() -> None:
    cfg = SimpleNamespace(
        agents={"coordinator": SimpleNamespace(output_key="final_brief")}
    )

    assert (
        _resolve_final_output(
            {"final_brief": {"answer": "from state"}},
            cfg,
            "coordinator",
            {"answer": "from event"},
        )
        == {"answer": "from state"}
    )


def test_resolve_final_output_author_falls_back_to_single_workflow_node() -> None:
    cfg = SimpleNamespace(
        agents={"coordinator": SimpleNamespace(output_key=None)},
        workflow=SimpleNamespace(nodes=["coordinator"]),
    )

    assert (
        _resolve_final_output_author(
            {"coordinator": "final brief"},
            cfg,
            event_author=None,
            last_workflow_node=None,
        )
        == "coordinator"
    )


def test_resolve_final_output_author_ignores_subagent_author() -> None:
    cfg = SimpleNamespace(
        agents={
            "coordinator": SimpleNamespace(output_key=None),
            "search_specialist": SimpleNamespace(output_key=None),
        },
        workflow=SimpleNamespace(nodes=["coordinator"]),
    )

    assert (
        _resolve_final_output_author(
            {"coordinator": "final brief"},
            cfg,
            event_author="search_specialist",
            last_workflow_node=None,
        )
        == "coordinator"
    )


def test_final_state_for_display_includes_fallback_output() -> None:
    cfg = SimpleNamespace(
        agents={"coordinator": SimpleNamespace(output_key=None)}
    )

    state = {"user_input": {"topic": "AI"}}
    display = _final_state_for_display(
        state,
        cfg,
        "coordinator",
        "fallback answer",
    )

    assert display == {
        "user_input": {"topic": "AI"},
        "coordinator": "fallback answer",
    }
    assert state == {"user_input": {"topic": "AI"}}


def test_final_state_for_display_replaces_blank_output() -> None:
    cfg = SimpleNamespace(
        agents={"coordinator": SimpleNamespace(output_key=None)}
    )

    display = _final_state_for_display(
        {"user_input": {"topic": "AI"}, "coordinator": ""},
        cfg,
        "coordinator",
        "fallback answer",
    )

    assert display["coordinator"] == "fallback answer"


def test_final_state_for_display_honors_output_key() -> None:
    cfg = SimpleNamespace(
        agents={"coordinator": SimpleNamespace(output_key="final_brief")}
    )

    assert _final_state_for_display(
        {"user_input": {"topic": "AI"}},
        cfg,
        "coordinator",
        "fallback answer",
    )["final_brief"] == "fallback answer"


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

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
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

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
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
            agents={},
            workflow=SimpleNamespace(max_llm_calls=20),
        ),
    )
    monkeypatch.setattr(cli_mod, "build_workflow", lambda cfg: object())

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        return {"user_input": input_data}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--input", "plain text request"])

    assert result.exit_code == 0
    assert '"user_input": "plain text request"' in result.output


def test_run_state_file_writes_display_final_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    store = tmp_path / "result.json"

    import modular_agent_designer.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "load_workflow",
        lambda yaml_path: SimpleNamespace(
            name="hello",
            agents={"greeter": SimpleNamespace(output_key=None)},
            workflow=SimpleNamespace(nodes=["greeter"], max_llm_calls=20),
        ),
    )
    monkeypatch.setattr(cli_mod, "build_workflow", lambda cfg: object())

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        return {"user_input": input_data, "greeter": "stored answer"}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            str(p),
            "--input",
            '{"topic": "x"}',
            "--state",
            str(store),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(store.read_text(encoding="utf-8")) == {
        "user_input": {"topic": "x"},
        "greeter": "stored answer",
    }


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
            agents={},
            workflow=SimpleNamespace(max_llm_calls=20),
        ),
    )
    monkeypatch.setattr(cli_mod, "build_workflow", lambda cfg: object())

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        return {"user_input": input_data}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--input-file", str(inp)])

    assert result.exit_code == 0
    assert '"user_input": "plain text from file"' in result.output


def test_run_suppresses_streamed_events_without_verbose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        event_handler = kwargs.get("event_handler")
        if event_handler is not None:
            event_handler(
                Event(
                    author="greeter",
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text="streamed event")],
                    ),
                )
            )
        return {"greeter": "done"}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--input", '{"topic": "x"}'])

    assert result.exit_code == 0
    assert "streamed event" not in result.output
    assert "Final Output (greeter)" in result.output
    assert "done" in result.output
    assert '"greeter": "done"' in result.output


def test_run_verbose_prints_streamed_events_before_final_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod

    seen: dict[str, Any] = {}

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        seen["stream_output"] = kwargs.get("stream_output")
        event_handler = kwargs.get("event_handler")
        if event_handler is not None:
            event_handler(
                Event(
                    author="greeter",
                    node_path="wf@1/greeter@1",
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text="streamed event")],
                    ),
                )
            )
        return {"greeter": "done"}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main, ["run", str(p), "--input", '{"topic": "x"}', "--verbose"]
    )

    assert result.exit_code == 0
    assert result.output.index("streamed event") < result.output.index("Final State")
    assert "Workflow Node: greeter" in result.output
    assert '"greeter": "done"' in result.output
    assert seen["stream_output"] is False


def test_run_verbose_truncates_streamed_events_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod

    long_text = "start " + ("x" * 700) + " full-tail"

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        event_handler = kwargs.get("event_handler")
        if event_handler is not None:
            event_handler(
                Event(
                    author="greeter",
                    node_path="wf@1/greeter@1",
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=long_text)],
                    ),
                )
            )
        return {"greeter": "done"}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main, ["run", str(p), "--input", '{"topic": "x"}', "--verbose"]
    )

    assert result.exit_code == 0
    assert "... truncated" in result.output
    assert "full-tail" not in result.output


def test_run_verbose_can_enable_streamed_event_truncation_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod

    long_text = "start " + ("x" * 700) + " full-tail"

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        event_handler = kwargs.get("event_handler")
        if event_handler is not None:
            event_handler(
                Event(
                    author="greeter",
                    node_path="wf@1/greeter@1",
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=long_text)],
                    ),
                )
            )
        return {"greeter": "done"}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            str(p),
            "--input",
            '{"topic": "x"}',
            "--verbose",
            "--truncate",
            "true",
        ],
    )

    assert result.exit_code == 0
    assert "... truncated" in result.output
    assert "full-tail" not in result.output


def test_run_verbose_stream_truncates_partial_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        event_handler = kwargs.get("event_handler")
        if event_handler is not None:
            for chunk in ("start ", "x" * 700, " full-tail"):
                event_handler(
                    Event(
                        author="greeter",
                        node_path="wf@1/greeter@1",
                        partial=True,
                        content=types.Content(
                            role="model",
                            parts=[types.Part(text=chunk)],
                        ),
                    )
                )
        return {"greeter": "done"}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            str(p),
            "--input",
            '{"topic": "x"}',
            "--verbose-stream",
            "--truncate",
            "true",
        ],
    )

    assert result.exit_code == 0
    assert "... truncated" in result.output
    assert "full-tail" not in result.output


def test_run_verbose_can_disable_streamed_event_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod

    long_text = "start " + ("x" * 700) + " full-tail"

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        event_handler = kwargs.get("event_handler")
        if event_handler is not None:
            event_handler(
                Event(
                    author="greeter",
                    node_path="wf@1/greeter@1",
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=long_text)],
                    ),
                )
            )
        return {"greeter": "done"}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            str(p),
            "--input",
            '{"topic": "x"}',
            "--verbose",
            "--truncate",
            "false",
        ],
    )

    assert result.exit_code == 0
    assert "... truncated" not in result.output
    assert "full-tail" in result.output


def test_run_verbose_stream_prints_streamed_events_with_sse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod
    seen: dict[str, Any] = {}

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        seen["stream_output"] = kwargs.get("stream_output")
        event_handler = kwargs.get("event_handler")
        if event_handler is not None:
            event_handler(
                Event(
                    author="greeter",
                    node_path="wf@1/greeter@1",
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text="streamed with stream flag")],
                    ),
                )
            )
        return {"greeter": "done"}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main, ["run", str(p), "--input", '{"topic": "x"}', "--verbose-stream"]
    )

    assert result.exit_code == 0
    assert "streamed with stream flag" in result.output
    assert "Workflow Node: greeter" in result.output
    assert '"greeter": "done"' in result.output
    assert seen["stream_output"] is True


def test_run_verbose_falls_back_to_streamed_tool_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        event_handler = kwargs.get("event_handler")
        if event_handler is not None:
            event_handler(
                Event(
                    author="greeter",
                    node_path="wf@1/greeter@1",
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name="analysis_specialist",
                                    response={"result": "streamed fallback answer"},
                                )
                            )
                        ],
                    ),
                )
            )
        return {"user_input": input_data}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main, ["run", str(p), "--input", '{"topic": "x"}', "--verbose"]
    )

    assert result.exit_code == 0
    assert "Final Output (greeter)" in result.output
    assert "streamed fallback answer" in result.output
    assert '"greeter": "streamed fallback answer"' in result.output


def test_run_verbose_replaces_blank_state_with_streamed_tool_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)

    import modular_agent_designer.cli as cli_mod

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        event_handler = kwargs.get("event_handler")
        if event_handler is not None:
            event_handler(
                Event(
                    author="greeter",
                    node_path="wf@1/greeter@1",
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name="analysis_specialist",
                                    response={"result": "streamed answer"},
                                )
                            )
                        ],
                    ),
                )
            )
        return {"user_input": input_data, "greeter": ""}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main, ["run", str(p), "--input", '{"topic": "x"}', "--verbose"]
    )

    assert result.exit_code == 0
    assert "Final Output (greeter)" in result.output
    assert "streamed answer" in result.output
    assert '"greeter": "streamed answer"' in result.output
    assert '"greeter": ""' not in result.output


def test_run_state_file_uses_fallback_display_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    store = tmp_path / "nested" / "result.json"

    import modular_agent_designer.cli as cli_mod

    async def _fake_run(workflow, input_data, max_llm_calls=20, **kwargs):
        event_handler = kwargs.get("event_handler")
        if event_handler is not None:
            event_handler(
                Event(
                    author="greeter",
                    node_path="wf@1/greeter@1",
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name="analysis_specialist",
                                    response={"result": "streamed answer"},
                                )
                            )
                        ],
                    ),
                )
            )
        return {"user_input": input_data, "greeter": ""}

    monkeypatch.setattr(cli_mod, "_run_workflow", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            str(p),
            "--input",
            '{"topic": "x"}',
            "--verbose",
            "--state",
            str(store),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(store.read_text(encoding="utf-8")) == {
        "user_input": {"topic": "x"},
        "greeter": "streamed answer",
    }


def test_run_input_file_not_found(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(_VALID_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["run", str(p), "--input-file", str(tmp_path / "missing.json")])
    assert result.exit_code == 1
    assert "not found" in result.output
