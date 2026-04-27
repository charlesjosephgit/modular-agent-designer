"""Tests for NodeRefConfig.config and build_custom_node kwarg forwarding."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from modular_agent_designer.config.schema import NodeRefConfig
from modular_agent_designer.nodes.custom import build_custom_node


def test_node_ref_config_has_empty_config_by_default() -> None:
    cfg = NodeRefConfig(type="node", ref="os.getcwd")
    assert cfg.config == {}


def test_node_ref_config_accepts_config_dict() -> None:
    cfg = NodeRefConfig(type="node", ref="os.getcwd", config={"threshold": 0.8, "label": "primary"})
    assert cfg.config == {"threshold": 0.8, "label": "primary"}


def test_build_custom_node_passes_config_to_base_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BaseNode subclasses (Pydantic models) receive config as field values."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "nodes.py").write_text(
        textwrap.dedent("""\
            from google.adk.workflow import BaseNode

            class ConfigurableNode(BaseNode):
                threshold: float = 0.5
                label: str = "default"

                async def execute(self, ctx, node_input):
                    yield None
        """)
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    cfg = NodeRefConfig(
        type="node",
        ref="mypkg.nodes.ConfigurableNode",
        config={"threshold": 0.9, "label": "primary"},
    )
    node = build_custom_node("test_node", cfg)
    assert node.threshold == 0.9
    assert node.label == "primary"
    assert node.name == "test_node"


def test_build_custom_node_no_config_uses_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "mypkg2"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "nodes.py").write_text(
        textwrap.dedent("""\
            from google.adk.workflow import BaseNode

            class DefaultNode(BaseNode):
                value: int = 42

                async def execute(self, ctx, node_input):
                    yield None
        """)
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    cfg = NodeRefConfig(type="node", ref="mypkg2.nodes.DefaultNode")
    node = build_custom_node("test_node", cfg)
    assert node.value == 42
    assert node.name == "test_node"


def test_build_custom_node_function_ignores_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For plain function refs, config is silently ignored (functions don't take kwargs)."""
    pkg = tmp_path / "mypkg3"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "nodes.py").write_text("async def my_node(ctx, node_input): yield None\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    cfg = NodeRefConfig(
        type="node",
        ref="mypkg3.nodes.my_node",
        config={"ignored": True},
    )
    node = build_custom_node("test_node", cfg)
    import inspect
    assert callable(node) or inspect.isfunction(node)
