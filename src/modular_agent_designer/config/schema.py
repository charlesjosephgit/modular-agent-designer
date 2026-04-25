"""Pydantic v2 config schemas for the YAML workflow format."""
from __future__ import annotations

import os
import re
from collections import defaultdict
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

_ENV_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand_env(value: str, *, context: str) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        val = os.environ.get(name)
        if val is None:
            raise ValueError(f"{context}: env var '${{{name}}}' is not set")
        return val

    return _ENV_VAR_RE.sub(repl, value)

# Map provider name to accepted model-string prefix(es).
# google provider uses "gemini/" prefix (LiteLLM convention).
# ollama accepts both "ollama/" (generate API) and "ollama_chat/" (chat API —
# required for native tool calling and reasoning_effort).
_PROVIDER_PREFIXES: dict[str, tuple[str, ...]] = {
    "ollama": ("ollama", "ollama_chat"),
    "anthropic": ("anthropic",),
    "google": ("gemini",),
    "openai": ("openai",),
}

# Environment variables required for each provider.
PROVIDER_ENV_VARS: dict[str, str | None] = {
    "ollama": None,            # optional: OLLAMA_API_BASE
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
}


class ModelConfig(BaseModel):
    provider: Literal["ollama", "anthropic", "google", "openai"]
    model: str
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # Provider-specific reasoning/thinking config, passed through to LiteLLM.
    # Anthropic: {"type": "enabled", "budget_tokens": 2048}
    # OpenAI o-series: {"reasoning_effort": "medium"}  (set as top-level kwarg, see registry)
    # Gemini 2.5: {"include_thoughts": true, "thinking_budget": 2048}
    thinking: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_model_prefix(self) -> "ModelConfig":
        expected = _PROVIDER_PREFIXES[self.provider]
        if not any(self.model.startswith(f"{p}/") for p in expected):
            allowed = " or ".join(f"'{p}/'" for p in expected)
            raise ValueError(
                f"model '{self.model}' must start with {allowed} "
                f"for provider '{self.provider}'"
            )
        return self


class _ToolBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuiltinToolConfig(_ToolBase):
    type: Literal["builtin"] = "builtin"
    name: Optional[str] = None
    ref: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "BuiltinToolConfig":
        if (self.name is None) == (self.ref is None):
            raise ValueError(
                "BuiltinToolConfig requires exactly one of 'name' or 'ref',"
                " not both or neither."
            )
        return self


class PythonToolConfig(_ToolBase):
    type: Literal["python"]
    ref: str


class McpStdioToolConfig(_ToolBase):
    type: Literal["mcp_stdio"]
    command: str
    args: list[str] = []
    env: dict[str, str] = {}
    tool_filter: Optional[list[str]] = None
    tool_name_prefix: Optional[str] = None

    @model_validator(mode="after")
    def _expand(self) -> "McpStdioToolConfig":
        self.env = {
            k: _expand_env(v, context=f"mcp_stdio env '{k}'")
            for k, v in self.env.items()
        }
        return self


class McpSseToolConfig(_ToolBase):
    type: Literal["mcp_sse"]
    url: str
    headers: dict[str, str] = {}
    tool_filter: Optional[list[str]] = None
    tool_name_prefix: Optional[str] = None

    @model_validator(mode="after")
    def _expand(self) -> "McpSseToolConfig":
        self.headers = {
            k: _expand_env(v, context=f"mcp_sse header '{k}'")
            for k, v in self.headers.items()
        }
        return self


class McpHttpToolConfig(_ToolBase):
    type: Literal["mcp_http"]
    url: str
    headers: dict[str, str] = {}
    tool_filter: Optional[list[str]] = None
    tool_name_prefix: Optional[str] = None

    @model_validator(mode="after")
    def _expand(self) -> "McpHttpToolConfig":
        self.headers = {
            k: _expand_env(v, context=f"mcp_http header '{k}'")
            for k, v in self.headers.items()
        }
        return self


ToolConfig = Annotated[
    Union[
        BuiltinToolConfig,
        PythonToolConfig,
        McpStdioToolConfig,
        McpSseToolConfig,
        McpHttpToolConfig,
    ],
    Field(discriminator="type"),
]


class AgentConfig(BaseModel):
    type: Literal["agent"] = "agent"
    model: str
    instruction: str
    tools: list[str] = []
    output_schema: Optional[str] = None
    sub_agents: list[str] = []
    mode: Optional[Literal["chat", "task", "single_turn"]] = None
    disallow_transfer_to_parent: bool = False
    disallow_transfer_to_peers: bool = False


class NodeRefConfig(BaseModel):
    type: Literal["node"]
    ref: str


NodeEntry = Annotated[
    Union[AgentConfig, NodeRefConfig],
    Field(discriminator="type"),
]


class EvalCondition(BaseModel):
    """A condition that evaluates a Python expression against state and input."""
    eval: str


class EdgeConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    to: str
    condition: Optional[
        Union[EvalCondition, str, int, bool, list[Union[str, int, bool]]]
    ] = None

    @model_validator(mode="after")
    def handle_default_condition(self) -> "EdgeConfig":
        if self.condition == "default":
            self.condition = "__DEFAULT__"
        return self


def _detect_sub_agent_cycles(agents: dict[str, Any]) -> None:
    """Raise ValueError if sub_agent references form a cycle."""
    adj: dict[str, list[str]] = {}
    for name, cfg in agents.items():
        if isinstance(cfg, AgentConfig):
            adj[name] = cfg.sub_agents

    UNVISITED, IN_PROGRESS, DONE = 0, 1, 2
    state: dict[str, int] = {name: UNVISITED for name in adj}
    path: list[str] = []

    def dfs(node: str) -> None:
        state[node] = IN_PROGRESS
        path.append(node)
        for child in adj.get(node, []):
            if child not in state:
                continue
            if state[child] == IN_PROGRESS:
                cycle_start = path.index(child)
                cycle = path[cycle_start:] + [child]
                raise ValueError(
                    f"Circular sub_agent reference detected: {' -> '.join(cycle)}"
                )
            if state[child] == UNVISITED:
                dfs(child)
        path.pop()
        state[node] = DONE

    for name in list(adj):
        if state[name] == UNVISITED:
            dfs(name)


class WorkflowConfig(BaseModel):
    nodes: list[str]
    edges: list[EdgeConfig]
    entry: str
    max_llm_calls: int = 20

    @model_validator(mode="after")
    def validate_dag(self) -> "WorkflowConfig":
        node_set = set(self.nodes)
        if self.entry not in node_set:
            raise ValueError(f"entry '{self.entry}' not found in nodes list")
        for edge in self.edges:
            if edge.from_ not in node_set:
                raise ValueError(
                    f"edge 'from: {edge.from_}' references unknown node"
                )
            if edge.to not in node_set:
                raise ValueError(
                    f"edge 'to: {edge.to}' references unknown node"
                )

        # Edge-coherence checks grouped by source.
        by_src: dict[str, list] = defaultdict(list)
        for edge in self.edges:
            by_src[edge.from_].append(edge)
        for src, src_edges in by_src.items():
            defaults = [e for e in src_edges if e.condition == "__DEFAULT__"]
            if len(defaults) > 1:
                raise ValueError(
                    f"source '{src}' has multiple default edges"
                )
            has_conditional = any(
                e.condition is not None for e in src_edges
            )
            has_unconditional = any(
                e.condition is None for e in src_edges
            )
            if has_conditional and has_unconditional:
                raise ValueError(
                    f"source '{src}' mixes unconditional and conditional edges"
                    " — use only one type per source"
                )

        return self


class RootConfig(BaseModel):
    name: str
    description: str = ""
    models: dict[str, ModelConfig]
    tools: dict[str, ToolConfig] = {}
    agents: dict[str, NodeEntry]
    workflow: WorkflowConfig

    @model_validator(mode="before")
    @classmethod
    def inject_agent_type(cls, data: Any) -> Any:
        """Fill type='agent' for entries that don't declare a type."""
        if isinstance(data, dict) and "agents" in data:
            agents = data["agents"]
            if isinstance(agents, dict):
                for name, val in agents.items():
                    if isinstance(val, dict) and "type" not in val:
                        val["type"] = "agent"
        return data

    @model_validator(mode="after")
    def validate_references(self) -> "RootConfig":
        node_set = set(self.agents.keys())
        for node_name in self.workflow.nodes:
            if node_name not in node_set:
                raise ValueError(
                    f"workflow.nodes references '{node_name}' "
                    f"which is not defined in agents"
                )
        for cfg in self.agents.values():
            if isinstance(cfg, AgentConfig):
                if cfg.model not in self.models:
                    raise ValueError(
                        f"agent references model '{cfg.model}' "
                        f"which is not defined in models"
                    )
                for tool_name in cfg.tools:
                    if tool_name not in self.tools:
                        raise ValueError(
                            f"agent references tool '{tool_name}' "
                            f"which is not defined in tools"
                        )

        # Validate sub_agent references.
        all_sub_agent_names: set[str] = set()
        for agent_name, cfg in self.agents.items():
            if isinstance(cfg, AgentConfig):
                for sa_name in cfg.sub_agents:
                    if sa_name not in self.agents:
                        raise ValueError(
                            f"agent '{agent_name}' references sub_agent '{sa_name}' "
                            f"which is not defined in agents"
                        )
                    if not isinstance(self.agents[sa_name], AgentConfig):
                        raise ValueError(
                            f"agent '{agent_name}' references sub_agent '{sa_name}' "
                            f"which must be an agent (type: agent), not a node ref"
                        )
                    all_sub_agent_names.add(sa_name)

        # Sub-agents must not appear in workflow.nodes.
        workflow_node_set = set(self.workflow.nodes)
        for sa_name in all_sub_agent_names:
            if sa_name in workflow_node_set:
                raise ValueError(
                    f"'{sa_name}' is declared as a sub_agent and must not appear "
                    f"in workflow.nodes"
                )

        # Detect circular sub_agent references.
        _detect_sub_agent_cycles(self.agents)

        return self
