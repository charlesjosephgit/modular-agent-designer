"""Tests for ADK 2.0 agent params: description, input/output_key, static_instruction,
generate_content_config, thinking/BuiltInPlanner, and parallel_worker."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.config.schema import (
    AgentConfig,
    AgentGenerateContentConfig,
    AgentThinkingConfig,
    SafetySettingConfig,
)
from modular_agent_designer.nodes.agent_node import (
    build_agent_node,
    build_sub_agent,
    _build_generate_content_config,
    _build_planner,
)
from modular_agent_designer.workflow.builder import build_workflow


def _load(tmp_path: Path, content: str):
    p = tmp_path / "wf.yaml"
    p.write_text(content)
    return load_workflow(p)


def _make_agent(**kwargs) -> AgentConfig:
    defaults = {"model": "m", "instruction": "Do work."}
    defaults.update(kwargs)
    return AgentConfig(**defaults)


BASE_YAML = """\
name: wf
models:
  fast:
    provider: ollama
    model: ollama/gemma4:e4b
{agents}
workflow:
  nodes: [{nodes}]
  edges: []
  entry: {entry}
"""


# ---------------------------------------------------------------------------
# description
# ---------------------------------------------------------------------------

class TestDescription:

    def test_description_parsed_from_yaml(self, tmp_path):
        yaml = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  worker:
                    model: fast
                    description: "Handles search tasks."
                    instruction: "Search the web."
            """),
            nodes="worker", entry="worker",
        )
        cfg = _load(tmp_path, yaml)
        assert cfg.agents["worker"].description == "Handles search tasks."

    def test_description_none_by_default(self, tmp_path):
        yaml = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  worker:
                    model: fast
                    instruction: "Search the web."
            """),
            nodes="worker", entry="worker",
        )
        cfg = _load(tmp_path, yaml)
        assert cfg.agents["worker"].description is None

    def test_description_wired_to_sub_agent(self):
        from google.adk import Agent
        from google.adk.models.lite_llm import LiteLlm

        cfg = _make_agent(description="I am a specialist.")
        model = LiteLlm(model="ollama/gemma4:e4b")
        agent = build_sub_agent("specialist", cfg, model, [])
        assert isinstance(agent, Agent)
        assert agent.description == "I am a specialist."

    def test_description_wired_to_workflow_node(self):
        from google.adk import Agent
        from google.adk.models.lite_llm import LiteLlm
        from google.adk.workflow._function_node import FunctionNode

        cfg = _make_agent(description="I am the main agent.")
        model = LiteLlm(model="ollama/gemma4:e4b")
        node = build_agent_node("main", cfg, model, [])
        assert isinstance(node, FunctionNode)


# ---------------------------------------------------------------------------
# input_schema
# ---------------------------------------------------------------------------

class TestInputSchema:

    def test_input_schema_parsed(self, tmp_path):
        yaml = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  worker:
                    model: fast
                    instruction: "Do work."
                    input_schema: "pydantic.BaseModel"
            """),
            nodes="worker", entry="worker",
        )
        cfg = _load(tmp_path, yaml)
        assert cfg.agents["worker"].input_schema == "pydantic.BaseModel"

    def test_input_schema_wired_to_agent(self):
        from google.adk import Agent
        from google.adk.models.lite_llm import LiteLlm
        from pydantic import BaseModel

        class MyInput(BaseModel):
            query: str

        # Use a dotted-path import. We register MyInput on the test module.
        import sys
        sys.modules[__name__].MyInput = MyInput  # type: ignore[attr-defined]

        cfg = _make_agent(input_schema=f"{__name__}.MyInput")
        model = LiteLlm(model="ollama/gemma4:e4b")
        agent = build_sub_agent("worker", cfg, model, [])
        assert isinstance(agent, Agent)
        assert agent.input_schema is MyInput


# ---------------------------------------------------------------------------
# output_key
# ---------------------------------------------------------------------------

class TestOutputKey:

    def test_output_key_parsed(self, tmp_path):
        yaml = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  worker:
                    model: fast
                    instruction: "Do work."
                    output_key: "custom_result"
            """),
            nodes="worker", entry="worker",
        )
        cfg = _load(tmp_path, yaml)
        assert cfg.agents["worker"].output_key == "custom_result"

    def test_output_key_default_is_none(self):
        cfg = _make_agent()
        assert cfg.output_key is None

    def test_output_key_passed_to_workflow_node(self):
        from google.adk.models.lite_llm import LiteLlm
        from google.adk.workflow._function_node import FunctionNode

        cfg = _make_agent(output_key="my_output")
        model = LiteLlm(model="ollama/gemma4:e4b")
        node = build_agent_node("worker", cfg, model, [])
        assert isinstance(node, FunctionNode)

    def test_default_output_key_is_agent_name(self):
        from google.adk import Agent
        from google.adk.models.lite_llm import LiteLlm

        cfg = _make_agent()
        model = LiteLlm(model="ollama/gemma4:e4b")
        agent = build_sub_agent("worker", cfg, model, [])
        # sub-agents don't get an output_key by default
        assert isinstance(agent, Agent)


# ---------------------------------------------------------------------------
# static_instruction
# ---------------------------------------------------------------------------

class TestStaticInstruction:

    def test_static_instruction_parsed(self, tmp_path):
        yaml = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  worker:
                    model: fast
                    instruction: "Dynamic part."
                    static_instruction: "You are a helpful assistant. Always be concise."
            """),
            nodes="worker", entry="worker",
        )
        cfg = _load(tmp_path, yaml)
        assert cfg.agents["worker"].static_instruction == "You are a helpful assistant. Always be concise."

    def test_static_instruction_file_resolved(self, tmp_path):
        static_file = tmp_path / "static_prompt.md"
        static_file.write_text("You are cached.")
        # Write a dotted-ref-style file under cwd — simulate with a real file
        yaml_content = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  worker:
                    model: fast
                    instruction: "Dynamic."
                    static_instruction: "Cached system prompt."
            """),
            nodes="worker", entry="worker",
        )
        cfg = _load(tmp_path, yaml_content)
        assert cfg.agents["worker"].static_instruction == "Cached system prompt."

    def test_static_instruction_and_file_mutually_exclusive(self, tmp_path):
        yaml = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  worker:
                    model: fast
                    instruction: "Dynamic."
                    static_instruction: "inline"
                    static_instruction_file: prompts.something
            """),
            nodes="worker", entry="worker",
        )
        with pytest.raises(ValueError, match="static_instruction"):
            _load(tmp_path, yaml)

    def test_static_instruction_wired_to_sub_agent(self):
        from google.adk import Agent
        from google.adk.models.lite_llm import LiteLlm

        cfg = _make_agent(static_instruction="You are a cached assistant.")
        model = LiteLlm(model="ollama/gemma4:e4b")
        agent = build_sub_agent("worker", cfg, model, [])
        assert isinstance(agent, Agent)
        assert agent.static_instruction == "You are a cached assistant."

    def test_static_instruction_wired_to_workflow_node(self):
        from google.adk.models.lite_llm import LiteLlm
        from google.adk.workflow._function_node import FunctionNode

        cfg = _make_agent(static_instruction="Stable system context.")
        model = LiteLlm(model="ollama/gemma4:e4b")
        node = build_agent_node("worker", cfg, model, [])
        assert isinstance(node, FunctionNode)


# ---------------------------------------------------------------------------
# generate_content_config
# ---------------------------------------------------------------------------

class TestGenerateContentConfig:

    def test_generate_content_config_parsed(self, tmp_path):
        yaml = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  worker:
                    model: fast
                    instruction: "Do work."
                    generate_content_config:
                      temperature: 0.2
                      max_output_tokens: 512
                      seed: 42
            """),
            nodes="worker", entry="worker",
        )
        cfg = _load(tmp_path, yaml)
        gcc = cfg.agents["worker"].generate_content_config
        assert gcc is not None
        assert gcc.temperature == 0.2
        assert gcc.max_output_tokens == 512
        assert gcc.seed == 42

    def test_temperature_range_validated(self, tmp_path):
        yaml = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  worker:
                    model: fast
                    instruction: "Do work."
                    generate_content_config:
                      temperature: 3.0
            """),
            nodes="worker", entry="worker",
        )
        with pytest.raises(ValueError):
            _load(tmp_path, yaml)

    def test_build_generate_content_config_temperature(self):
        from google.genai.types import GenerateContentConfig

        acc = AgentGenerateContentConfig(temperature=0.0)
        result = _build_generate_content_config(acc)
        assert isinstance(result, GenerateContentConfig)
        assert result.temperature == 0.0

    def test_build_generate_content_config_all_fields(self):
        from google.genai.types import GenerateContentConfig

        acc = AgentGenerateContentConfig(
            temperature=0.5,
            top_p=0.9,
            top_k=40,
            max_output_tokens=256,
            candidate_count=1,
            stop_sequences=["STOP"],
            seed=7,
            presence_penalty=0.1,
            frequency_penalty=0.1,
            response_mime_type="application/json",
        )
        result = _build_generate_content_config(acc)
        assert isinstance(result, GenerateContentConfig)
        assert result.temperature == 0.5
        assert result.top_p == 0.9
        assert result.max_output_tokens == 256
        assert result.seed == 7

    def test_build_generate_content_config_safety_settings(self):
        from google.genai.types import GenerateContentConfig, SafetySetting

        acc = AgentGenerateContentConfig(
            safety_settings=[
                SafetySettingConfig(
                    category="HARM_CATEGORY_HARASSMENT",
                    threshold="BLOCK_NONE",
                )
            ]
        )
        result = _build_generate_content_config(acc)
        assert isinstance(result, GenerateContentConfig)
        assert result.safety_settings is not None
        assert len(result.safety_settings) == 1

    def test_per_agent_gcc_wired_to_sub_agent(self):
        from google.adk import Agent
        from google.adk.models.lite_llm import LiteLlm

        cfg = _make_agent(
            generate_content_config=AgentGenerateContentConfig(temperature=0.0)
        )
        model = LiteLlm(model="ollama/gemma4:e4b")
        agent = build_sub_agent("worker", cfg, model, [])
        assert isinstance(agent, Agent)
        assert agent.generate_content_config is not None
        assert agent.generate_content_config.temperature == 0.0

    def test_per_agent_gcc_wired_to_workflow_node(self):
        from google.adk.models.lite_llm import LiteLlm
        from google.adk.workflow._function_node import FunctionNode

        cfg = _make_agent(
            generate_content_config=AgentGenerateContentConfig(max_output_tokens=100)
        )
        model = LiteLlm(model="ollama/gemma4:e4b")
        node = build_agent_node("worker", cfg, model, [])
        assert isinstance(node, FunctionNode)


# ---------------------------------------------------------------------------
# thinking → BuiltInPlanner
# ---------------------------------------------------------------------------

class TestThinkingPlanner:

    def test_thinking_config_parsed(self, tmp_path):
        yaml = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  worker:
                    model: fast
                    instruction: "Do work."
                    thinking:
                      thinking_budget: 2048
                      include_thoughts: true
            """),
            nodes="worker", entry="worker",
        )
        cfg = _load(tmp_path, yaml)
        tc = cfg.agents["worker"].thinking
        assert tc is not None
        assert tc.thinking_budget == 2048
        assert tc.include_thoughts is True

    def test_build_planner_returns_builtin_planner(self):
        from google.adk.planners import BuiltInPlanner

        tc = AgentThinkingConfig(thinking_budget=1024, include_thoughts=True)
        planner = _build_planner(tc)
        assert isinstance(planner, BuiltInPlanner)
        assert planner.thinking_config is not None
        assert planner.thinking_config.thinking_budget == 1024

    def test_planner_wired_to_sub_agent(self):
        from google.adk import Agent
        from google.adk.models.lite_llm import LiteLlm
        from google.adk.planners import BuiltInPlanner

        cfg = _make_agent(thinking=AgentThinkingConfig(thinking_budget=512))
        model = LiteLlm(model="ollama/gemma4:e4b")
        agent = build_sub_agent("thinker", cfg, model, [])
        assert isinstance(agent, Agent)
        assert isinstance(agent.planner, BuiltInPlanner)

    def test_planner_wired_to_workflow_node(self):
        from google.adk.models.lite_llm import LiteLlm
        from google.adk.workflow._function_node import FunctionNode

        cfg = _make_agent(thinking=AgentThinkingConfig(thinking_budget=256))
        model = LiteLlm(model="ollama/gemma4:e4b")
        node = build_agent_node("thinker", cfg, model, [])
        assert isinstance(node, FunctionNode)


# ---------------------------------------------------------------------------
# parallel_worker
# ---------------------------------------------------------------------------

class TestParallelWorker:

    def test_parallel_worker_on_sub_agent_valid(self, tmp_path):
        yaml = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  child:
                    model: fast
                    instruction: "I run in parallel."
                    parallel_worker: true
                  parent:
                    model: fast
                    instruction: "I coordinate."
                    sub_agents: [child]
            """),
            nodes="parent", entry="parent",
        )
        cfg = _load(tmp_path, yaml)
        assert cfg.agents["child"].parallel_worker is True

    def test_parallel_worker_on_workflow_node_raises(self, tmp_path):
        yaml = BASE_YAML.format(
            agents=textwrap.dedent("""\
                agents:
                  worker:
                    model: fast
                    instruction: "I am a workflow node."
                    parallel_worker: true
            """),
            nodes="worker", entry="worker",
        )
        with pytest.raises(ValueError, match="parallel_worker"):
            _load(tmp_path, yaml)

    def test_parallel_worker_wired_to_sub_agent(self):
        from google.adk import Agent
        from google.adk.models.lite_llm import LiteLlm

        cfg = _make_agent(parallel_worker=True)
        model = LiteLlm(model="ollama/gemma4:e4b")
        agent = build_sub_agent("worker", cfg, model, [])
        assert isinstance(agent, Agent)
        assert agent.parallel_worker is True

    def test_parallel_worker_false_wired(self):
        from google.adk import Agent
        from google.adk.models.lite_llm import LiteLlm

        cfg = _make_agent(parallel_worker=False)
        model = LiteLlm(model="ollama/gemma4:e4b")
        agent = build_sub_agent("worker", cfg, model, [])
        assert isinstance(agent, Agent)
        assert agent.parallel_worker is False


# ---------------------------------------------------------------------------
# Full workflow build with new params
# ---------------------------------------------------------------------------

class TestFullWorkflowBuild:

    def test_build_workflow_with_all_new_params(self, tmp_path):
        from google.adk import Workflow

        yaml = textwrap.dedent("""\
            name: full_params_wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              specialist:
                model: fast
                description: "Handles data analysis tasks."
                instruction: "Analyze the data."
                mode: single_turn
                parallel_worker: true
                generate_content_config:
                  temperature: 0.3
                  max_output_tokens: 1024
              coordinator:
                model: fast
                description: "Delegates to specialists."
                instruction: "Coordinate the analysis pipeline."
                output_key: coordinator_result
                static_instruction: "You are a coordinator. Be decisive."
                generate_content_config:
                  temperature: 0.7
                  seed: 99
                thinking:
                  thinking_budget: 512
                sub_agents: [specialist]
            workflow:
              nodes: [coordinator]
              edges: []
              entry: coordinator
        """)
        p = tmp_path / "wf.yaml"
        p.write_text(yaml)
        cfg = load_workflow(p)
        wf = build_workflow(cfg)
        assert isinstance(wf, Workflow)
        assert wf.name == "full_params_wf"
