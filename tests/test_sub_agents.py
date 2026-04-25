"""Tests for sub-agent support in YAML workflow config and builder."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.workflow.builder import build_workflow, _topological_sort_agents


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(tmp_path: Path, content: str):
    p = tmp_path / "wf.yaml"
    p.write_text(content)
    return load_workflow(p)


BASE_YAML = textwrap.dedent("""\
    name: {name}
    models:
      fast:
        provider: ollama
        model: ollama/gemma4:e4b
    {extra}
    workflow:
      nodes: [{nodes}]
      edges: []
      entry: {entry}
""")


COORDINATOR_YAML = textwrap.dedent("""\
    name: coordinator_wf
    models:
      fast:
        provider: ollama
        model: ollama/gemma4:e4b
    agents:
      search_specialist:
        model: fast
        instruction: "Search for information."
        mode: single_turn
      analysis_specialist:
        model: fast
        instruction: "Analyze the provided data."
        mode: single_turn
      coordinator:
        model: fast
        instruction: "Coordinate research about the given topic."
        sub_agents:
          - search_specialist
          - analysis_specialist
    workflow:
      nodes: [coordinator]
      edges: []
      entry: coordinator
""")


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------

class TestSubAgentSchemaValidation:

    def test_valid_sub_agent_config_parses(self, tmp_path):
        cfg = _load(tmp_path, COORDINATOR_YAML)
        assert "coordinator" in cfg.agents
        assert "search_specialist" in cfg.agents
        coordinator = cfg.agents["coordinator"]
        assert coordinator.sub_agents == ["search_specialist", "analysis_specialist"]

    def test_sub_agent_mode_field_parsed(self, tmp_path):
        cfg = _load(tmp_path, COORDINATOR_YAML)
        specialist = cfg.agents["search_specialist"]
        assert specialist.mode == "single_turn"

    def test_mode_chat_accepted(self, tmp_path):
        yaml = textwrap.dedent("""\
            name: wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              child:
                model: fast
                instruction: "I am a child."
                mode: chat
              parent:
                model: fast
                instruction: "I am a parent."
                sub_agents: [child]
            workflow:
              nodes: [parent]
              edges: []
              entry: parent
        """)
        cfg = _load(tmp_path, yaml)
        assert cfg.agents["child"].mode == "chat"

    def test_mode_task_accepted(self, tmp_path):
        yaml = textwrap.dedent("""\
            name: wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              child:
                model: fast
                instruction: "I am a child."
                mode: task
              parent:
                model: fast
                instruction: "I am a parent."
                sub_agents: [child]
            workflow:
              nodes: [parent]
              edges: []
              entry: parent
        """)
        cfg = _load(tmp_path, yaml)
        assert cfg.agents["child"].mode == "task"

    def test_disallow_flags_default_false(self, tmp_path):
        cfg = _load(tmp_path, COORDINATOR_YAML)
        specialist = cfg.agents["search_specialist"]
        assert specialist.disallow_transfer_to_parent is False
        assert specialist.disallow_transfer_to_peers is False

    def test_disallow_flags_configurable(self, tmp_path):
        yaml = textwrap.dedent("""\
            name: wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              child:
                model: fast
                instruction: "Do work."
                mode: single_turn
                disallow_transfer_to_parent: true
                disallow_transfer_to_peers: true
              parent:
                model: fast
                instruction: "I am a parent."
                sub_agents: [child]
            workflow:
              nodes: [parent]
              edges: []
              entry: parent
        """)
        cfg = _load(tmp_path, yaml)
        child = cfg.agents["child"]
        assert child.disallow_transfer_to_parent is True
        assert child.disallow_transfer_to_peers is True

    def test_nonexistent_sub_agent_raises(self, tmp_path):
        yaml = textwrap.dedent("""\
            name: wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              coordinator:
                model: fast
                instruction: "I coordinate."
                sub_agents: [DOES_NOT_EXIST]
            workflow:
              nodes: [coordinator]
              edges: []
              entry: coordinator
        """)
        with pytest.raises(ValueError, match="DOES_NOT_EXIST"):
            _load(tmp_path, yaml)

    def test_sub_agent_in_workflow_nodes_raises(self, tmp_path):
        yaml = textwrap.dedent("""\
            name: wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              specialist:
                model: fast
                instruction: "I am a specialist."
              coordinator:
                model: fast
                instruction: "I coordinate."
                sub_agents: [specialist]
            workflow:
              nodes: [coordinator, specialist]
              edges:
                - from: coordinator
                  to: specialist
              entry: coordinator
        """)
        with pytest.raises(ValueError, match="specialist"):
            _load(tmp_path, yaml)

    def test_circular_sub_agent_raises(self, tmp_path):
        yaml = textwrap.dedent("""\
            name: wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              a:
                model: fast
                instruction: "A"
                sub_agents: [b]
              b:
                model: fast
                instruction: "B"
                sub_agents: [a]
              root:
                model: fast
                instruction: "Root"
                sub_agents: [a]
            workflow:
              nodes: [root]
              edges: []
              entry: root
        """)
        with pytest.raises(ValueError, match="Circular sub_agent"):
            _load(tmp_path, yaml)

    def test_self_referencing_sub_agent_raises(self, tmp_path):
        yaml = textwrap.dedent("""\
            name: wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              agent_a:
                model: fast
                instruction: "Self-referencing"
                sub_agents: [agent_a]
            workflow:
              nodes: [agent_a]
              edges: []
              entry: agent_a
        """)
        with pytest.raises(ValueError):
            _load(tmp_path, yaml)

    def test_nested_sub_agents_valid(self, tmp_path):
        """A has sub-agent B, B has sub-agent C — valid, no cycle."""
        yaml = textwrap.dedent("""\
            name: wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              leaf:
                model: fast
                instruction: "I am a leaf."
                mode: single_turn
              middle:
                model: fast
                instruction: "I delegate to leaf."
                sub_agents: [leaf]
                mode: single_turn
              root:
                model: fast
                instruction: "I delegate to middle."
                sub_agents: [middle]
            workflow:
              nodes: [root]
              edges: []
              entry: root
        """)
        cfg = _load(tmp_path, yaml)
        assert cfg.agents["middle"].sub_agents == ["leaf"]
        assert cfg.agents["root"].sub_agents == ["middle"]

    def test_deep_cycle_raises(self, tmp_path):
        yaml = textwrap.dedent("""\
            name: wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              a:
                model: fast
                instruction: "A"
                sub_agents: [b]
              b:
                model: fast
                instruction: "B"
                sub_agents: [c]
              c:
                model: fast
                instruction: "C"
                sub_agents: [a]
              root:
                model: fast
                instruction: "Root"
                sub_agents: [a]
            workflow:
              nodes: [root]
              edges: []
              entry: root
        """)
        with pytest.raises(ValueError, match="Circular sub_agent"):
            _load(tmp_path, yaml)


# ---------------------------------------------------------------------------
# Topological sort tests
# ---------------------------------------------------------------------------

class TestTopologicalSort:

    def _make_agents(self, sub_agent_map: dict[str, list[str]]) -> dict:
        """Build a minimal agents dict from a sub_agent_map for sort testing."""
        from modular_agent_designer.config.schema import AgentConfig
        agents = {}
        for name, subs in sub_agent_map.items():
            agents[name] = AgentConfig(
                model="m",
                instruction="x",
                sub_agents=subs,
            )
        return agents

    def test_no_sub_agents_any_order(self):
        agents = self._make_agents({"a": [], "b": [], "c": []})
        order = _topological_sort_agents(agents)
        assert set(order) == {"a", "b", "c"}

    def test_single_sub_agent_child_before_parent(self):
        agents = self._make_agents({"parent": ["child"], "child": []})
        order = _topological_sort_agents(agents)
        assert order.index("child") < order.index("parent")

    def test_two_sub_agents_both_before_parent(self):
        agents = self._make_agents({
            "coordinator": ["s1", "s2"],
            "s1": [],
            "s2": [],
        })
        order = _topological_sort_agents(agents)
        assert order.index("s1") < order.index("coordinator")
        assert order.index("s2") < order.index("coordinator")

    def test_nested_three_levels(self):
        agents = self._make_agents({
            "root": ["middle"],
            "middle": ["leaf"],
            "leaf": [],
        })
        order = _topological_sort_agents(agents)
        assert order.index("leaf") < order.index("middle")
        assert order.index("middle") < order.index("root")


# ---------------------------------------------------------------------------
# Builder tests
# ---------------------------------------------------------------------------

class TestSubAgentBuilder:

    def test_build_workflow_with_sub_agents(self, tmp_path):
        from google.adk import Workflow
        cfg = _load(tmp_path, COORDINATOR_YAML)
        wf = build_workflow(cfg)
        assert isinstance(wf, Workflow)
        assert wf.name == "coordinator_wf"

    def test_sub_agents_not_in_workflow_edges(self, tmp_path):
        cfg = _load(tmp_path, COORDINATOR_YAML)
        wf = build_workflow(cfg)
        # Only coordinator is a workflow node, so edges are: START->coordinator
        assert len(wf.edges) == 1

    def test_nested_sub_agents_build_successfully(self, tmp_path):
        from google.adk import Workflow
        yaml = textwrap.dedent("""\
            name: nested_wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              leaf:
                model: fast
                instruction: "I am a leaf."
                mode: single_turn
              middle:
                model: fast
                instruction: "I delegate to leaf."
                sub_agents: [leaf]
                mode: single_turn
              root:
                model: fast
                instruction: "I delegate to middle."
                sub_agents: [middle]
            workflow:
              nodes: [root]
              edges: []
              entry: root
        """)
        cfg = _load(tmp_path, yaml)
        wf = build_workflow(cfg)
        assert isinstance(wf, Workflow)

    def test_build_sub_agent_returns_agent_instance(self):
        """build_sub_agent returns a plain ADK Agent, not a @node callable."""
        from google.adk import Agent
        from google.adk.models.lite_llm import LiteLlm
        from modular_agent_designer.config.schema import AgentConfig
        from modular_agent_designer.nodes.agent_node import build_sub_agent

        cfg = AgentConfig(
            model="m",
            instruction="Do something.",
            mode="single_turn",
        )
        model = LiteLlm(model="ollama/gemma4:e4b")
        agent = build_sub_agent("specialist", cfg, model, [])
        assert isinstance(agent, Agent)
        assert agent.name == "specialist"

    def test_workflow_node_with_sub_agents_wired(self, tmp_path):
        """The parent workflow node should be a FunctionNode wrapping the agent."""
        from google.adk import Agent
        from google.adk.models.lite_llm import LiteLlm
        from google.adk.workflow._function_node import FunctionNode
        from modular_agent_designer.config.schema import AgentConfig
        from modular_agent_designer.nodes.agent_node import build_sub_agent, build_agent_node

        child_cfg = AgentConfig(model="m", instruction="I am child.", mode="single_turn")
        model = LiteLlm(model="ollama/gemma4:e4b")
        child_agent = build_sub_agent("child", child_cfg, model, [])

        parent_cfg = AgentConfig(model="m", instruction="I am parent.", sub_agents=["child"])
        # Pass the pre-built child agent
        node = build_agent_node("parent", parent_cfg, model, [], [child_agent])
        # ADK @node decorator returns a FunctionNode (not a plain callable)
        assert isinstance(node, FunctionNode)
        assert node.name == "parent"
