"""Tests for `modular-agent-designer create`."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from modular_agent_designer.cli import main
from modular_agent_designer.config.loader import load_workflow


def _invoke(args: list[str], cwd: Path | None = None) -> object:
    runner = CliRunner()
    return runner.invoke(main, args, catch_exceptions=False)


def test_create_generates_expected_files():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["create", "demo_agent"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        folder = Path("demo_agent")
        assert (folder / "agent.py").exists()
        assert (folder / "demo_agent.yaml").exists()
        assert (folder / "__init__.py").exists()
        assert (folder / "README.md").exists()
        assert (folder / "tools" / "__init__.py").exists()


def test_create_agent_py_content():
    runner = CliRunner()
    with runner.isolated_filesystem():
        runner.invoke(main, ["create", "my_bot"], catch_exceptions=False)
        agent_py = Path("my_bot/agent.py").read_text()
        assert "root_agent = build_workflow(cfg)" in agent_py
        assert "my_bot.yaml" in agent_py
        assert "Path(__file__).parent" in agent_py


def test_create_yaml_content():
    runner = CliRunner()
    with runner.isolated_filesystem():
        runner.invoke(main, ["create", "my_bot"], catch_exceptions=False)
        yaml_text = Path("my_bot/my_bot.yaml").read_text()
        assert "name: my_bot" in yaml_text
        assert "ollama_chat/mistral:7b" in yaml_text
        assert "responder" in yaml_text


def test_create_yaml_is_valid():
    """Parsed YAML must satisfy Pydantic schema without building the workflow."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        runner.invoke(main, ["create", "valid_agent"], catch_exceptions=False)
        cfg = load_workflow("valid_agent/valid_agent.yaml")
        assert cfg.name == "valid_agent"


def test_create_refuses_existing_files_without_force():
    runner = CliRunner()
    with runner.isolated_filesystem():
        runner.invoke(main, ["create", "demo"], catch_exceptions=False)
        result = runner.invoke(main, ["create", "demo"], catch_exceptions=False)
        assert result.exit_code != 0
        assert "already exist" in result.output or "already exist" in (result.stderr or "")


def test_create_force_overwrites():
    runner = CliRunner()
    with runner.isolated_filesystem():
        runner.invoke(main, ["create", "demo"], catch_exceptions=False)
        result = runner.invoke(main, ["create", "--force", "demo"], catch_exceptions=False)
        assert result.exit_code == 0


def test_create_rejects_invalid_name():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["create", "1bad-name"], catch_exceptions=False)
        assert result.exit_code != 0


def test_create_rejects_hyphenated_name():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["create", "my-agent"], catch_exceptions=False)
        assert result.exit_code != 0


def test_create_custom_parent_dir():
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("projects").mkdir()
        result = runner.invoke(
            main, ["create", "sub_agent", "--dir", "projects"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        assert Path("projects/sub_agent/agent.py").exists()


def test_create_tools_init_content():
    runner = CliRunner()
    with runner.isolated_filesystem():
        runner.invoke(main, ["create", "demo"], catch_exceptions=False)
        tools_init = Path("demo/tools/__init__.py").read_text()
        assert "tools/__init__.py" in tools_init
        assert "demo.yaml" in tools_init
        assert "type: python" in tools_init
        assert "ref:" in tools_init


def test_create_success_message_shows_next_steps():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["create", "demo"], catch_exceptions=False)
        assert "ollama" in result.output.lower()
        assert "adk web" in result.output
