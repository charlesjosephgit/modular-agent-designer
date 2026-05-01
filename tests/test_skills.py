"""Tests for skills support: schema, registry, and integration."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from google.adk.skills import Skill
from google.adk.tools.skill_toolset import SkillToolset

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.config.schema import (
    AgentConfig,
    RootConfig,
    SkillConfig,
)
from modular_agent_designer.skills.registry import (
    build_skill_registry,
    build_skill_toolset,
    resolve_skill,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill(name: str, description: str = "test skill") -> Skill:
    """Create a minimal ADK Skill for testing."""
    from google.adk.skills import Frontmatter
    return Skill(
        frontmatter=Frontmatter(name=name, description=description),
        instructions=f"Instructions for {name}.",
    )


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestSkillSchema:
    """Test SkillConfig and AgentConfig.skills field."""

    def test_skill_config_valid(self) -> None:
        cfg = SkillConfig(ref="modular_agent_designer.skills.summarize-text")
        assert cfg.ref == "modular_agent_designer.skills.summarize-text"

    def test_skill_config_extra_fields_rejected(self) -> None:
        with pytest.raises(Exception):
            SkillConfig(ref="my.module.fn", unexpected="bad")

    def test_agent_config_skills_default_empty(self) -> None:
        cfg = AgentConfig(
            model="local",
            instruction="hi",
        )
        assert cfg.skills == []

    def test_agent_config_skills_list(self) -> None:
        cfg = AgentConfig(
            model="local",
            instruction="hi",
            skills=["summarizer", "analyzer"],
        )
        assert cfg.skills == ["summarizer", "analyzer"]


# ---------------------------------------------------------------------------
# YAML loading tests
# ---------------------------------------------------------------------------

VALID_YAML_WITH_SKILLS = textwrap.dedent("""\
    name: test_wf
    models:
      local:
        provider: ollama
        model: ollama/gemma4:e4b
    skills:
      summarizer:
        ref: modular_agent_designer.skills.summarize-text
    agents:
      step_one:
        model: local
        instruction: "Say hello."
        skills: [summarizer]
    workflow:
      nodes: [step_one]
      edges: []
      entry: step_one
""")


YAML_WITHOUT_SKILLS = textwrap.dedent("""\
    name: test_wf
    models:
      local:
        provider: ollama
        model: ollama/gemma4:e4b
    agents:
      step_one:
        model: local
        instruction: "Say hello."
    workflow:
      nodes: [step_one]
      edges: []
      entry: step_one
""")


YAML_BAD_SKILL_REF = textwrap.dedent("""\
    name: test_wf
    models:
      local:
        provider: ollama
        model: ollama/gemma4:e4b
    skills:
      summarizer:
        ref: modular_agent_designer.skills.summarize-text
    agents:
      step_one:
        model: local
        instruction: "Say hello."
        skills: [nonexistent_skill]
    workflow:
      nodes: [step_one]
      edges: []
      entry: step_one
""")


class TestSkillYamlLoading:
    """Test YAML loading with skills."""

    def test_valid_yaml_with_skills(self, tmp_path: Path) -> None:
        p = tmp_path / "wf.yaml"
        p.write_text(VALID_YAML_WITH_SKILLS)
        cfg = load_workflow(p)
        assert isinstance(cfg, RootConfig)
        assert "summarizer" in cfg.skills
        assert cfg.skills["summarizer"].ref == "modular_agent_designer.skills.summarize-text"
        assert cfg.agents["step_one"].skills == ["summarizer"]

    def test_yaml_without_skills_backward_compat(self, tmp_path: Path) -> None:
        p = tmp_path / "wf.yaml"
        p.write_text(YAML_WITHOUT_SKILLS)
        cfg = load_workflow(p)
        assert isinstance(cfg, RootConfig)
        assert cfg.skills == {}
        assert cfg.agents["step_one"].skills == []

    def test_bad_skill_alias_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "wf.yaml"
        p.write_text(YAML_BAD_SKILL_REF)
        with pytest.raises(ValueError, match="nonexistent_skill"):
            load_workflow(p)


# ---------------------------------------------------------------------------
# Registry tests (directory-based resolution)
# ---------------------------------------------------------------------------

class TestSkillRegistry:
    """Test resolve_skill with directory-based refs."""

    def test_resolve_internal_skill(self) -> None:
        """Resolve built-in skill: modular_agent_designer.skills.summarize-text"""
        cfg = SkillConfig(ref="modular_agent_designer.skills.summarize-text")
        skills = resolve_skill("summarizer", cfg)
        assert len(skills) == 1
        assert isinstance(skills[0], Skill)
        assert skills[0].name == "summarize-text"

    def test_resolve_example_skill(self) -> None:
        """Resolve repository example skill: examples.skills.summarize-text"""
        cfg = SkillConfig(ref="examples.skills.summarize-text")
        skills = resolve_skill("local_summary", cfg)
        assert len(skills) == 1
        assert isinstance(skills[0], Skill)
        assert skills[0].name == "summarize-text"

    def test_resolve_no_dot_raises(self) -> None:
        cfg = SkillConfig(ref="nodot")
        with pytest.raises(ValueError, match="dotted path"):
            resolve_skill("test", cfg)

    def test_resolve_bad_module_raises(self) -> None:
        cfg = SkillConfig(ref="nonexistent.module.skill-name")
        with pytest.raises(ImportError):
            resolve_skill("test", cfg)

    def test_resolve_missing_dir_raises(self) -> None:
        cfg = SkillConfig(ref="modular_agent_designer.skills.nonexistent-skill")
        with pytest.raises(FileNotFoundError, match="not found"):
            resolve_skill("test", cfg)

    def test_build_skill_registry(self) -> None:
        skills = {
            "a": SkillConfig(ref="modular_agent_designer.skills.summarize-text"),
        }
        registry = build_skill_registry(skills)
        assert len(registry) == 1
        assert len(registry["a"]) == 1
        assert registry["a"][0].name == "summarize-text"


# ---------------------------------------------------------------------------
# Toolset builder tests
# ---------------------------------------------------------------------------

class TestBuildSkillToolset:
    """Test build_skill_toolset."""

    def test_empty_skills_returns_none(self) -> None:
        result = build_skill_toolset([], {})
        assert result is None

    def test_empty_names_returns_none(self) -> None:
        registry = {"a": [_make_skill("test-skill")]}
        result = build_skill_toolset([], registry)
        assert result is None

    def test_builds_toolset(self) -> None:
        registry = {
            "a": [_make_skill("skill-a")],
            "b": [_make_skill("skill-b")],
        }
        result = build_skill_toolset(["a", "b"], registry)
        assert isinstance(result, SkillToolset)

    def test_single_skill(self) -> None:
        registry = {"a": [_make_skill("skill-a")]}
        result = build_skill_toolset(["a"], registry)
        assert isinstance(result, SkillToolset)

