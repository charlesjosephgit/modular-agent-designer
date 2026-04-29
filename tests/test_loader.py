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


def test_schema_version_defaults_to_one(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(VALID_YAML)
    cfg = load_workflow(p)
    assert cfg.schema_version == 1


def test_schema_version_one_loads(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text("schema_version: 1\n" + VALID_YAML)
    cfg = load_workflow(p)
    assert cfg.schema_version == 1


def test_unsupported_schema_version_raises(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text("schema_version: 2\n" + VALID_YAML)
    with pytest.raises(ValueError, match="Unsupported schema_version"):
        load_workflow(p)


def test_agent_timeout_seconds_loads(tmp_path: Path) -> None:
    yaml_text = VALID_YAML.replace(
        'instruction: "Say hello to {{state.user_input.name}}."',
        'instruction: "Say hello to {{state.user_input.name}}."\n    timeout_seconds: 5',
    )
    p = tmp_path / "wf.yaml"
    p.write_text(yaml_text)
    cfg = load_workflow(p)
    assert cfg.agents["step_one"].timeout_seconds == 5


def test_agent_timeout_seconds_must_be_positive(tmp_path: Path) -> None:
    yaml_text = VALID_YAML.replace(
        'instruction: "Say hello to {{state.user_input.name}}."',
        'instruction: "Say hello to {{state.user_input.name}}."\n    timeout_seconds: 0',
    )
    p = tmp_path / "wf.yaml"
    p.write_text(yaml_text)
    with pytest.raises(ValueError, match="timeout_seconds"):
        load_workflow(p)


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


# ---------------------------------------------------------------------------
# instruction_file tests
# ---------------------------------------------------------------------------

_BASE_YAML = textwrap.dedent("""\
    name: test_wf
    models:
      local:
        provider: ollama
        model: ollama/gemma4:e4b
    agents:
      step_one:
        model: local
        {instruction_field}
    workflow:
      nodes: [step_one]
      edges: []
      entry: step_one
""")


def test_instruction_file_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "my_prompt.md").write_text("Hello {{state.user_input.name}}.")
    yaml_text = _BASE_YAML.format(
        instruction_field="instruction_file: prompts.my_prompt"
    )
    p = tmp_path / "wf.yaml"
    p.write_text(yaml_text)
    cfg = load_workflow(p)
    from modular_agent_designer.config.schema import AgentConfig
    agent = cfg.agents["step_one"]
    assert isinstance(agent, AgentConfig)
    assert agent.instruction == "Hello {{state.user_input.name}}."
    assert agent.instruction_file is None


def test_instruction_file_falls_back_to_yaml_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "cwd"
    project = tmp_path / "project"
    cwd.mkdir()
    prompts_dir = project / "prompts"
    prompts_dir.mkdir(parents=True)
    monkeypatch.chdir(cwd)
    (prompts_dir / "my_prompt.md").write_text("Hello from YAML dir.")
    yaml_text = _BASE_YAML.format(
        instruction_field="instruction_file: prompts.my_prompt"
    )
    p = project / "wf.yaml"
    p.write_text(yaml_text)
    cfg = load_workflow(p)
    from modular_agent_designer.config.schema import AgentConfig
    agent = cfg.agents["step_one"]
    assert isinstance(agent, AgentConfig)
    assert agent.instruction == "Hello from YAML dir."


def test_instruction_file_prefers_cwd_over_yaml_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "cwd"
    project = tmp_path / "project"
    (cwd / "prompts").mkdir(parents=True)
    (project / "prompts").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    (cwd / "prompts" / "my_prompt.md").write_text("Hello from cwd.")
    (project / "prompts" / "my_prompt.md").write_text("Hello from YAML dir.")
    yaml_text = _BASE_YAML.format(
        instruction_field="instruction_file: prompts.my_prompt"
    )
    p = project / "wf.yaml"
    p.write_text(yaml_text)
    cfg = load_workflow(p)
    from modular_agent_designer.config.schema import AgentConfig
    agent = cfg.agents["step_one"]
    assert isinstance(agent, AgentConfig)
    assert agent.instruction == "Hello from cwd."


def test_instruction_file_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    yaml_text = _BASE_YAML.format(
        instruction_field="instruction_file: prompts.does_not_exist"
    )
    p = tmp_path / "wf.yaml"
    p.write_text(yaml_text)
    with pytest.raises(ValueError, match="step_one"):
        load_workflow(p)


def test_instruction_file_invalid_dotted_ref_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    yaml_text = _BASE_YAML.format(
        instruction_field="instruction_file: ../prompts/my_agent.txt"
    )
    p = tmp_path / "wf.yaml"
    p.write_text(yaml_text)
    with pytest.raises(ValueError, match="not a valid dotted ref"):
        load_workflow(p)


def test_both_instruction_and_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "p.md").write_text("hi")
    yaml_text = textwrap.dedent("""\
        name: test_wf
        models:
          local:
            provider: ollama
            model: ollama/gemma4:e4b
        agents:
          step_one:
            model: local
            instruction: hi
            instruction_file: prompts.p
        workflow:
          nodes: [step_one]
          edges: []
          entry: step_one
    """)
    p = tmp_path / "wf.yaml"
    p.write_text(yaml_text)
    with pytest.raises(ValueError, match="not both"):
        load_workflow(p)


def test_neither_instruction_nor_file_is_valid(tmp_path: Path) -> None:
    yaml_text = _BASE_YAML.format(instruction_field="tools: []")
    p = tmp_path / "wf.yaml"
    p.write_text(yaml_text)
    cfg = load_workflow(p)
    assert cfg.agents["step_one"].instruction is None
