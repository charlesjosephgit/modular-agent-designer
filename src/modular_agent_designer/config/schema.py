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


class SkillConfig(BaseModel):
    """A skill entry: dotted ref to a function returning list[Skill]."""
    model_config = ConfigDict(extra="forbid")
    ref: str


class RetryConfig(BaseModel):
    """Per-agent retry configuration for error recovery."""
    model_config = ConfigDict(extra="forbid")
    max_retries: int = Field(default=3, ge=1, le=10)
    backoff: Literal["fixed", "exponential"] = "fixed"
    delay_seconds: float = Field(default=1.0, ge=0)


class SafetySettingConfig(BaseModel):
    """Per-category safety setting for generate_content_config."""
    model_config = ConfigDict(extra="forbid")
    category: str
    threshold: str


class AgentGenerateContentConfig(BaseModel):
    """Per-agent generation parameter overrides (google.genai.types.GenerateContentConfig).

    These override the model-level temperature/max_tokens at the per-agent
    generation level. ADK forbids 'tools', 'system_instruction', and
    'response_schema' here; set them via agent fields instead.
    """
    model_config = ConfigDict(extra="forbid")
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    top_k: Optional[int] = Field(default=None, ge=1)
    max_output_tokens: Optional[int] = Field(default=None, ge=1)
    candidate_count: Optional[int] = Field(default=None, ge=1)
    stop_sequences: Optional[list[str]] = None
    seed: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    safety_settings: Optional[list[SafetySettingConfig]] = None
    cached_content: Optional[str] = None
    response_mime_type: Optional[str] = None


class AgentThinkingConfig(BaseModel):
    """Per-agent thinking config — compiled to a BuiltInPlanner at build time.

    Use this for Gemini models that support thinking_budget. This takes
    precedence over any thinking config on the model-level ModelConfig.
    """
    model_config = ConfigDict(extra="forbid")
    include_thoughts: Optional[bool] = None
    thinking_budget: Optional[int] = Field(
        default=None,
        description="Token budget for thinking. 0=disabled, -1=auto, >0=explicit budget.",
    )


class AgentConfig(BaseModel):
    type: Literal["agent"] = "agent"
    model: str
    description: Optional[str] = None
    instruction: Optional[str] = None
    instruction_file: Optional[str] = Field(
        default=None,
        description=(
            "Dotted ref to a prompt file resolved from cwd, the YAML directory, "
            "or the YAML directory's parent, e.g. "
            "'prompts.my_workflow__my_agent' → "
            "prompts/my_workflow__my_agent.md"
        ),
    )
    static_instruction: Optional[str] = None
    static_instruction_file: Optional[str] = Field(
        default=None,
        description=(
            "Dotted ref to a static prompt file (same format as instruction_file). "
            "Content is cached by the model and never varies with state."
        ),
    )
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    input_schema: Optional[str] = None
    output_schema: Optional[str] = None
    output_key: Optional[str] = None
    sub_agents: list[str] = Field(default_factory=list)
    mode: Optional[Literal["chat", "task", "single_turn"]] = None
    include_contents: Literal["default", "none"] = "default"
    disallow_transfer_to_parent: bool = False
    disallow_transfer_to_peers: bool = False
    parallel_worker: Optional[bool] = None
    generate_content_config: Optional[AgentGenerateContentConfig] = None
    thinking: Optional[AgentThinkingConfig] = None
    retry: Optional[RetryConfig] = None
    timeout_seconds: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_instruction(self) -> "AgentConfig":
        has_inline = self.instruction is not None
        has_file = self.instruction_file is not None
        if has_inline and has_file:
            raise ValueError(
                "Specify either 'instruction' or 'instruction_file', not both"
            )

        has_static_inline = self.static_instruction is not None
        has_static_file = self.static_instruction_file is not None
        if has_static_inline and has_static_file:
            raise ValueError(
                "Specify either 'static_instruction' or 'static_instruction_file', not both"
            )
        return self


class A2aAgentConfig(BaseModel):
    """Remote A2A agent declared in YAML."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["a2a"]
    agent_card: str = Field(
        description=(
            "URL to a remote agent card or local path to an agent card "
            "JSON file."
        )
    )
    description: str = ""
    output_key: Optional[str] = None
    timeout_seconds: float = Field(default=600.0, gt=0)
    full_history_when_stateless: bool = False
    use_legacy: bool = True

    @model_validator(mode="after")
    def _expand(self) -> "A2aAgentConfig":
        self.agent_card = _expand_env(
            self.agent_card,
            context="a2a agent_card",
        )
        return self


class NodeRefConfig(BaseModel):
    type: Literal["node"]
    ref: str
    config: dict[str, Any] = Field(default_factory=dict)


NodeEntry = Annotated[
    Union[AgentConfig, A2aAgentConfig, NodeRefConfig],
    Field(discriminator="type"),
]


class EvalCondition(BaseModel):
    """A condition that evaluates a Python expression against state and input."""
    eval: str


class LoopConfig(BaseModel):
    """Configuration for an edge that forms an intentional cycle."""
    model_config = ConfigDict(extra="forbid")
    max_iterations: int = Field(default=3, ge=1, le=100)
    on_exhausted: Optional[str] = Field(
        default=None,
        description="Node to route to when max_iterations is reached. "
        "If None, the branch terminates with a log warning.",
    )


_DYNAMIC_TO_RE = re.compile(r"^\{\{.*\}\}$")


def _is_dynamic_to(val: Any) -> bool:
    """Return True if *val* is a template string like ``{{state.x.y}}``."""
    return isinstance(val, str) and bool(_DYNAMIC_TO_RE.match(val.strip()))


class EdgeConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    to: Union[str, list[str]]
    condition: Optional[
        Union[EvalCondition, str, int, bool, list[Union[str, int, bool]]]
    ] = None
    loop: Optional[LoopConfig] = None
    on_error: bool = False
    parallel: bool = False
    join: Optional[str] = None
    # Typed error routing: match on exception class name (exact) and/or message (regex).
    error_type: Optional[str] = None
    error_match: Optional[str] = None
    # Dynamic destination: constrain which nodes the dispatcher may route to.
    # When None and to: is a template, all workflow nodes are candidates.
    allowed_targets: Optional[list[str]] = None

    @model_validator(mode="after")
    def _validate_edge(self) -> "EdgeConfig":
        # Normalise 'default' → '__DEFAULT__'
        if self.condition == "default":
            self.condition = "__DEFAULT__"

        to_is_list = isinstance(self.to, list)
        to_is_dynamic = not to_is_list and _is_dynamic_to(self.to)

        # parallel / join require to: [list]
        if self.parallel and not to_is_list:
            raise ValueError(
                "'parallel: true' requires 'to' to be a list of nodes"
            )
        if self.join is not None and not to_is_list:
            raise ValueError(
                "'join' requires 'to' to be a list of nodes"
            )

        # loop is not compatible with list-to, dynamic-to, or on_error
        if self.loop is not None:
            if to_is_list:
                raise ValueError(
                    "'loop' is not compatible with fan-out edges (to: [list])"
                )
            if to_is_dynamic:
                raise ValueError(
                    "'loop' is not compatible with dynamic destination (to: template)"
                )
            if self.on_error:
                raise ValueError(
                    "'loop' and 'on_error' cannot be used on the same edge"
                )

        # on_error edges may only carry condition: default (for explicit fallback ordering)
        if self.on_error and self.condition is not None and self.condition != "__DEFAULT__":
            raise ValueError(
                "'on_error: true' edges only accept 'condition: default',"
                " not other condition types"
            )

        # error_type / error_match only valid on on_error edges
        if not self.on_error and (self.error_type is not None or self.error_match is not None):
            raise ValueError(
                "'error_type' and 'error_match' are only valid on 'on_error: true' edges"
            )

        # allowed_targets only valid with a dynamic to:
        if self.allowed_targets is not None and not to_is_dynamic:
            raise ValueError(
                "'allowed_targets' is only valid when 'to' is a template (dynamic destination)"
            )

        return self


class DefaultRouteConfig(BaseModel):
    """Workflow-level fallback route applied to multiple source nodes."""

    model_config = ConfigDict(populate_by_name=True)
    to: str
    condition: Union[
        EvalCondition, str, int, bool, list[Union[str, int, bool]]
    ]
    from_: Optional[list[str]] = Field(default=None, alias="from")
    exclude: list[str] = Field(default_factory=list)


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


def _detect_workflow_cycles(
    node_set: set[str],
    edges: list["EdgeConfig"],
    loop_edges: set[tuple[str, str]],
) -> None:
    """Raise ValueError if workflow edges form a cycle not covered by a loop config.

    Edges in *loop_edges* (from, to) are intentional cycles and are excluded
    from cycle detection. Any other cycle is an accidental infinite loop.
    """
    adj: dict[str, list[str]] = {name: [] for name in node_set}
    for edge in edges:
        to_targets = edge.to if isinstance(edge.to, list) else [edge.to]
        for t in to_targets:
            # Skip dynamic destinations and intentional loop edges.
            if _is_dynamic_to(t) or (edge.from_, t) in loop_edges:
                continue
            adj[edge.from_].append(t)

    UNVISITED, IN_PROGRESS, DONE = 0, 1, 2
    state: dict[str, int] = {name: UNVISITED for name in node_set}
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
                    f"Accidental cycle detected in workflow edges: "
                    f"{' -> '.join(cycle)}. "
                    f"Add a 'loop:' config to the edge to make this intentional."
                )
            if state[child] == UNVISITED:
                dfs(child)
        path.pop()
        state[node] = DONE

    for name in list(node_set):
        if state[name] == UNVISITED:
            dfs(name)


class WorkflowConfig(BaseModel):
    nodes: list[str]
    edges: list[EdgeConfig]
    entry: str
    max_llm_calls: int = 20
    default_routes: list[DefaultRouteConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_workflow(self) -> "WorkflowConfig":
        node_set = set(self.nodes)
        if self.entry not in node_set:
            raise ValueError(f"entry '{self.entry}' not found in nodes list")

        for route in self.default_routes:
            if route.to not in node_set:
                raise ValueError(
                    f"default_routes target '{route.to}' references unknown node"
                )
            if route.from_ is not None:
                for src in route.from_:
                    if src not in node_set:
                        raise ValueError(
                            f"default_routes from '{src}' references unknown node"
                        )
            for src in route.exclude:
                if src not in node_set:
                    raise ValueError(
                        f"default_routes exclude '{src}' references unknown node"
                    )

        # Collect edges that have loop config for cycle-tolerance later.
        loop_edges: set[tuple[str, str]] = set()

        for edge in self.edges:
            if edge.from_ not in node_set:
                raise ValueError(
                    f"edge 'from: {edge.from_}' references unknown node"
                )

            # Validate 'to' targets (str or list[str]).
            # Dynamic (template) destinations are validated at runtime.
            to_targets = edge.to if isinstance(edge.to, list) else [edge.to]
            for t in to_targets:
                if not _is_dynamic_to(t) and t not in node_set:
                    raise ValueError(
                        f"edge 'to: {t}' references unknown node"
                    )

            # Validate allowed_targets entries.
            if edge.allowed_targets is not None:
                for t in edge.allowed_targets:
                    if t not in node_set:
                        raise ValueError(
                            f"edge 'allowed_targets: {t}' references unknown node"
                        )

            # Validate join target.
            if edge.join is not None and edge.join not in node_set:
                raise ValueError(
                    f"edge 'join: {edge.join}' references unknown node"
                )

            # Validate loop on_exhausted target.
            if (
                edge.loop is not None
                and edge.loop.on_exhausted is not None
                and edge.loop.on_exhausted not in node_set
            ):
                raise ValueError(
                    f"loop on_exhausted '{edge.loop.on_exhausted}' "
                    f"references unknown node"
                )

            # Track loop edges.
            if edge.loop is not None:
                assert isinstance(edge.to, str)
                loop_edges.add((edge.from_, edge.to))

        # Edge-coherence checks grouped by source.
        # Fan-out edges (to: [list]) are excluded from mix-checking since
        # they are always unconditional.
        by_src: dict[str, list] = defaultdict(list)
        for edge in self.edges:
            # Skip fan-out edges and on_error edges from coherence checks.
            if isinstance(edge.to, list) or edge.on_error:
                continue
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

        # Detect accidental cycles (cycles NOT covered by a loop config).
        _detect_workflow_cycles(node_set, self.edges, loop_edges)

        return self


class RootConfig(BaseModel):
    schema_version: int = 1
    name: str
    description: str = ""
    models: dict[str, ModelConfig]
    tools: dict[str, ToolConfig] = {}
    skills: dict[str, SkillConfig] = {}
    agents: dict[str, NodeEntry]
    workflow: WorkflowConfig

    @model_validator(mode="after")
    def validate_schema_version(self) -> "RootConfig":
        if self.schema_version != 1:
            raise ValueError(
                f"Unsupported schema_version {self.schema_version}; only 1 is supported"
            )
        return self

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
                for skill_name in cfg.skills:
                    if skill_name not in self.skills:
                        raise ValueError(
                            f"agent references skill '{skill_name}' "
                            f"which is not defined in skills"
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
                    if not isinstance(
                        self.agents[sa_name],
                        (AgentConfig, A2aAgentConfig),
                    ):
                        raise ValueError(
                            f"agent '{agent_name}' references sub_agent '{sa_name}' "
                            "which must be an agent (type: agent or "
                            "type: a2a), not a node ref"
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

        # parallel_worker is only valid on sub-agents.
        for agent_name, cfg in self.agents.items():
            if isinstance(cfg, AgentConfig) and cfg.parallel_worker is not None:
                if agent_name not in all_sub_agent_names:
                    raise ValueError(
                        f"agent '{agent_name}': 'parallel_worker' is only valid on "
                        f"sub-agents (agents listed under another agent's sub_agents)"
                    )

        # Detect circular sub_agent references.
        _detect_sub_agent_cycles(self.agents)

        return self
