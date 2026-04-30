"""Tests for YAML-declared remote A2A agents."""
from __future__ import annotations

import textwrap
import importlib.util
from pathlib import Path

import pytest

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.config.schema import A2aAgentConfig


def _load(tmp_path: Path, content: str):
    p = tmp_path / "wf.yaml"
    p.write_text(content)
    return load_workflow(p)


def test_a2a_agent_config_parses(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        textwrap.dedent("""\
            name: a2a_wf
            models: {}
            agents:
              remote_researcher:
                type: a2a
                agent_card: "https://example.com/.well-known/agent.json"
                description: "Remote research agent."
                output_key: remote_result
                timeout_seconds: 30
            workflow:
              nodes: [remote_researcher]
              edges: []
              entry: remote_researcher
        """),
    )

    agent = cfg.agents["remote_researcher"]
    assert isinstance(agent, A2aAgentConfig)
    assert agent.agent_card == "https://example.com/.well-known/agent.json"
    assert agent.description == "Remote research agent."
    assert agent.output_key == "remote_result"
    assert agent.timeout_seconds == 30


def test_a2a_agent_can_be_workflow_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google.adk.workflow import node as adk_node

    from modular_agent_designer.workflow import builder

    async def dummy(ctx, node_input):
        yield None

    dummy.__name__ = "remote_researcher"
    dummy.__qualname__ = "remote_researcher"

    def fake_build_a2a_agent_node(name, cfg):
        assert name == "remote_researcher"
        assert cfg.agent_card == "https://example.com/.well-known/agent.json"
        return adk_node()(dummy)

    monkeypatch.setattr(
        builder,
        "build_a2a_agent_node",
        fake_build_a2a_agent_node,
    )

    cfg = _load(
        tmp_path,
        textwrap.dedent("""\
            name: a2a_wf
            models: {}
            agents:
              remote_researcher:
                type: a2a
                agent_card: "https://example.com/.well-known/agent.json"
            workflow:
              nodes: [remote_researcher]
              edges: []
              entry: remote_researcher
        """),
    )

    wf = builder.build_workflow(cfg)
    assert wf.name == "a2a_wf"


def test_a2a_agent_can_be_sub_agent(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        textwrap.dedent("""\
            name: a2a_sub_agent_wf
            models:
              fast:
                provider: ollama
                model: ollama/gemma4:e4b
            agents:
              remote_specialist:
                type: a2a
                agent_card: "https://example.com/.well-known/agent.json"
                description: "Remote specialist."
              coordinator:
                model: fast
                instruction: "Delegate to the remote specialist."
                sub_agents: [remote_specialist]
            workflow:
              nodes: [coordinator]
              edges: []
              entry: coordinator
        """),
    )

    assert isinstance(cfg.agents["remote_specialist"], A2aAgentConfig)
    assert cfg.agents["coordinator"].sub_agents == ["remote_specialist"]


def test_missing_a2a_sdk_error_is_actionable() -> None:
    if importlib.util.find_spec("a2a") is not None:
        pytest.skip("a2a SDK is installed in this environment")

    from modular_agent_designer.nodes.a2a import build_remote_a2a_agent

    cfg = A2aAgentConfig(
        type="a2a",
        agent_card="https://example.com/.well-known/agent.json",
    )

    with pytest.raises(RuntimeError, match="A2A SDK"):
        build_remote_a2a_agent("remote", cfg)


def test_remote_a2a_fixture_serves_agent_card() -> None:
    pytest.importorskip("a2a")
    pytest.importorskip("starlette")

    from starlette.testclient import TestClient

    from tests.fixtures.remote_a2a_agent import build_app

    app = build_app("http://testserver")
    client = TestClient(app)

    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "MAD Test Echo A2A Agent"
    assert card["url"] == "http://testserver"
    assert card["skills"][0]["id"] == "echo"


def test_a2a_user_message_has_required_id() -> None:
    pytest.importorskip("a2a")

    from modular_agent_designer.nodes.a2a import _build_user_message

    message = _build_user_message("hello")
    assert message.message_id
    assert message.parts[0].text == "hello"
