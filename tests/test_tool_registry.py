"""Tests for tools/registry.py and ToolConfig schema variants."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    SseConnectionParams,
    StreamableHTTPConnectionParams,
)
from mcp import StdioServerParameters

from modular_agent_designer.config.loader import load_workflow
from modular_agent_designer.config.schema import (
    BuiltinToolConfig,
    McpHttpToolConfig,
    McpSseToolConfig,
    McpStdioToolConfig,
    PythonToolConfig,
)
from modular_agent_designer.tools.registry import build_tool_registry, resolve_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_YAML = textwrap.dedent("""\
    name: wf
    models:
      m:
        provider: ollama
        model: ollama/gemma3:4b
    agents:
      step:
        model: m
        instruction: hi
        tools: [{tools}]
    workflow:
      nodes: [step]
      edges: []
      entry: step
""")

_TOOLS_BLOCK = textwrap.dedent("""\
    tools:
      {name}:
{body}
""")


def _yaml_with_tool(tool_name: str, tool_body: str) -> str:
    indented = textwrap.indent(textwrap.dedent(tool_body).strip(), "        ")
    tools_block = f"tools:\n  {tool_name}:\n{indented}\n"
    base = textwrap.dedent("""\
        name: wf
        models:
          m:
            provider: ollama
            model: ollama/gemma3:4b
        {tools}
        agents:
          step:
            model: m
            instruction: hi
            tools: [{tool_name}]
        workflow:
          nodes: [step]
          edges: []
          entry: step
    """)
    return base.format(tools=tools_block, tool_name=tool_name)


# ---------------------------------------------------------------------------
# builtin / python
# ---------------------------------------------------------------------------


def test_python_tool_resolves() -> None:
    # Re-exported at the package root via tools/__init__.py
    cfg = PythonToolConfig(type="python", ref="modular_agent_designer.tools.fetch_url")
    tool = resolve_tool("fetch", cfg)
    assert callable(tool)


def test_builtin_tool_resolves_by_name() -> None:
    cfg = BuiltinToolConfig(type="builtin", name="fetch_url")
    tool = resolve_tool("fetch", cfg)
    assert callable(tool)


def test_builtin_tool_resolves_by_ref() -> None:
    cfg = BuiltinToolConfig(type="builtin", ref="modular_agent_designer.tools.fetch_url")
    tool = resolve_tool("fetch", cfg)
    assert callable(tool)


def test_builtin_unknown_name_raises_with_available_names() -> None:
    cfg = BuiltinToolConfig(type="builtin", name="nonexistent_tool")
    with pytest.raises(ValueError, match="fetch_url"):
        resolve_tool("bad", cfg)


def test_builtin_schema_rejects_neither() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        BuiltinToolConfig(type="builtin")


def test_builtin_schema_rejects_both() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        BuiltinToolConfig(
            type="builtin",
            name="fetch_url",
            ref="modular_agent_designer.tools.fetch_url",
        )


# ---------------------------------------------------------------------------
# mcp_stdio
# ---------------------------------------------------------------------------


def test_mcp_stdio_produces_toolset() -> None:
    cfg = McpStdioToolConfig(
        type="mcp_stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        tool_name_prefix="fs",
    )
    toolset = resolve_tool("fs", cfg)
    assert isinstance(toolset, McpToolset)
    params = toolset._connection_params
    assert isinstance(params, StdioServerParameters)
    assert params.command == "npx"
    assert "-y" in params.args


# ---------------------------------------------------------------------------
# mcp_sse
# ---------------------------------------------------------------------------


def test_mcp_sse_produces_toolset() -> None:
    cfg = McpSseToolConfig(
        type="mcp_sse",
        url="http://localhost:8080/sse",
    )
    toolset = resolve_tool("svc", cfg)
    assert isinstance(toolset, McpToolset)
    assert isinstance(toolset._connection_params, SseConnectionParams)


# ---------------------------------------------------------------------------
# mcp_http
# ---------------------------------------------------------------------------


def test_mcp_http_produces_toolset() -> None:
    cfg = McpHttpToolConfig(
        type="mcp_http",
        url="https://example.com/mcp/",
    )
    toolset = resolve_tool("svc", cfg)
    assert isinstance(toolset, McpToolset)
    assert isinstance(toolset._connection_params, StreamableHTTPConnectionParams)


# ---------------------------------------------------------------------------
# env-var expansion
# ---------------------------------------------------------------------------


def test_header_env_var_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOK", "abc123")
    cfg = McpHttpToolConfig(
        type="mcp_http",
        url="https://example.com/mcp/",
        headers={"Authorization": "Bearer ${TOK}"},
    )
    # Headers are expanded at validation time (in the @model_validator).
    assert cfg.headers["Authorization"] == "Bearer abc123"


def test_stdio_env_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "s3cr3t")
    cfg = McpStdioToolConfig(
        type="mcp_stdio",
        command="myserver",
        env={"API_KEY": "${MY_SECRET}"},
    )
    assert cfg.env["API_KEY"] == "s3cr3t"


def test_missing_env_var_fails_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR_XYZ", raising=False)
    with pytest.raises(ValueError, match=r"\$\{MISSING_VAR_XYZ\}"):
        McpHttpToolConfig(
            type="mcp_http",
            url="https://example.com/mcp/",
            headers={"Authorization": "Bearer ${MISSING_VAR_XYZ}"},
        )


# ---------------------------------------------------------------------------
# schema validation — bad configs
# ---------------------------------------------------------------------------


def test_old_mcp_type_rejected(tmp_path: Path) -> None:
    yaml_text = _yaml_with_tool(
        "my_tool",
        """\
        type: mcp
        ref: some.module.func
        """,
    )
    p = tmp_path / "wf.yaml"
    p.write_text(yaml_text)
    with pytest.raises(ValueError):
        load_workflow(p)


def test_stdio_missing_command_rejected() -> None:
    with pytest.raises(ValueError, match="command"):
        McpStdioToolConfig(type="mcp_stdio")  # type: ignore[call-arg]


def test_extra_field_rejected() -> None:
    with pytest.raises(ValueError):
        PythonToolConfig(type="python", ref="os.getcwd", unknown_field="oops")  # type: ignore[call-arg]


def test_mcp_http_extra_field_rejected() -> None:
    with pytest.raises(ValueError):
        McpHttpToolConfig(  # type: ignore[call-arg]
            type="mcp_http",
            url="https://example.com/mcp/",
            unexpected="nope",
        )


# ---------------------------------------------------------------------------
# build_tool_registry integration
# ---------------------------------------------------------------------------


def test_build_tool_registry_mixed() -> None:
    registry = build_tool_registry(
        {
            "fetch": BuiltinToolConfig(type="builtin", name="fetch_url"),
            "fs": McpStdioToolConfig(
                type="mcp_stdio",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            ),
        }
    )
    assert callable(registry["fetch"])
    assert isinstance(registry["fs"], McpToolset)


# ---------------------------------------------------------------------------
# External package support
# ---------------------------------------------------------------------------


import sys


def _make_ext_pkg(tmp_path: Path, pkg_name: str, source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write a minimal package under tmp_path and prepend it to sys.path.

    Uses a unique pkg_name per test to avoid sys.modules caching collisions.
    """
    pkg = tmp_path / pkg_name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "tools.py").write_text(source)
    monkeypatch.syspath_prepend(str(tmp_path))
    # Ensure any previously cached version of this package is evicted.
    monkeypatch.delitem(sys.modules, pkg_name, raising=False)
    monkeypatch.delitem(sys.modules, f"{pkg_name}.tools", raising=False)


def test_external_sync_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_ext_pkg(tmp_path, "ext_sync", "def double(x: int) -> int:\n    return x * 2\n", monkeypatch)

    cfg = PythonToolConfig(type="python", ref="ext_sync.tools.double")
    tool = resolve_tool("double", cfg)
    assert callable(tool)
    assert tool(4) == 8


def test_external_async_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_ext_pkg(tmp_path, "ext_async", "async def fetch(url: str) -> str:\n    return url\n", monkeypatch)

    cfg = PythonToolConfig(type="python", ref="ext_async.tools.fetch")
    tool = resolve_tool("fetch", cfg)
    assert callable(tool)


def test_external_callable_class(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_ext_pkg(
        tmp_path,
        "ext_class",
        "class MyTool:\n    def __call__(self, x):\n        return x\n\nmy_tool = MyTool()\n",
        monkeypatch,
    )

    cfg = PythonToolConfig(type="python", ref="ext_class.tools.my_tool")
    tool = resolve_tool("my_tool", cfg)
    assert callable(tool)


def test_noncallable_ref_raises_type_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_ext_pkg(tmp_path, "ext_ncall", "CONFIG = {'key': 'value'}\n", monkeypatch)

    cfg = PythonToolConfig(type="python", ref="ext_ncall.tools.CONFIG")
    with pytest.raises(TypeError, match="not callable"):
        resolve_tool("cfg_tool", cfg)


def test_missing_attribute_raises_attribute_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_ext_pkg(tmp_path, "ext_miss", "def real_fn(): pass\n", monkeypatch)

    cfg = PythonToolConfig(type="python", ref="ext_miss.tools.nonexistent")
    with pytest.raises(AttributeError):
        resolve_tool("missing", cfg)


def test_builtin_ref_form_equivalent_to_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `ref:` form of type: builtin resolves identically to type: python."""
    _make_ext_pkg(tmp_path, "ext_equiv_builtin", "def fn(x): return x\n", monkeypatch)

    builtin_cfg = BuiltinToolConfig(type="builtin", ref="ext_equiv_builtin.tools.fn")
    python_cfg = PythonToolConfig(type="python", ref="ext_equiv_builtin.tools.fn")
    assert resolve_tool("fn", builtin_cfg)(99) == resolve_tool("fn", python_cfg)(99) == 99
