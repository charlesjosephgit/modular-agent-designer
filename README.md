# Modular Agent Designer

A modular framework for designing and orchestrating complex agentic workflows with ease.

Define entire workflows — graph topology, nodes, edges, agents, tools, and model configuration — with ease. **No Python changes required** to add a new agent, tool, model, or workflow edge.

---

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13+.

```bash
uv sync --prerelease=allow          # install runtime
uv sync --extra telemetry --prerelease=allow # install with MLflow tracing support
uv pip install -e ".[dev]" --prerelease=allow   # add pytest + dev tools
```

Verify:

```bash
uv run python -c "from google.adk import Agent, Workflow; from google.adk.models.lite_llm import LiteLlm; print('OK')"
```

---

## Quickstart

### Scaffold a new agent (recommended for first-time use)

```bash
# Create a new agent project folder pre-wired to a local Ollama model:
uv run modular-agent-designer create my_agent

# Start Ollama and pull the default model (first time only):
ollama serve &
ollama pull gemma:e4b

# Run via CLI:
uv run modular-agent-designer run my_agent/my_agent.yaml --input '{"message": "hello"}'

# Or launch the interactive ADK web UI:
adk web my_agent
```

The `create` command generates `my_agent/` containing:
- `my_agent.yaml` — a minimal single-agent Ollama workflow you can edit
- `agent.py` — entry point for `adk web` (exposes `root_agent`)
- `__init__.py` — makes the folder a Python package (required by `adk web`)
- `README.md` — per-agent quickstart

```
modular-agent-designer create <agent-name> [--dir <parent>] [--force]
```

### Run an existing example

```bash
# Ollama must be running with at least one model pulled:
ollama serve &
ollama pull gemma:e4b

uv run modular-agent-designer run workflows/hello_world.yaml --input '{"topic":"tide pools"}'
```

Output: a JSON object containing each agent's output under its YAML name key.

---

## YAML Schema

A workflow file is a single YAML document with five top-level sections:

```yaml
name: my_workflow
description: Optional description.

models:
  <alias>:
    provider: ollama | anthropic | google | openai   # required
    model: <provider_prefix>/<model_name>            # required; see Model Providers table
    temperature: 0.7                                 # optional
    max_tokens: 1024                                 # optional

tools:
  # Bundled native tool — resolved by short name from the framework's built-in registry.
  # See "Bundled Tools" below for available names.
  <alias>:
    type: builtin
    name: fetch_url          # short name; no import path needed

  # Alternatively, reference any bundled tool by its full dotted path:
  <alias>:
    type: builtin
    ref: modular_agent_designer.tools.fetch_url

  # Arbitrary Python callable — imported by dotted ref. Points to any function
  # importable in the current environment: your own packages, third-party libs, etc.
  <alias>:
    type: python
    ref: dotted.module.path.to_callable

  # MCP server — stdio subprocess (e.g. npx, python3)
  <alias>:
    type: mcp_stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    env: {}                        # optional; ${VAR} placeholders are expanded from env
    tool_filter: [read_file]       # optional; restrict which MCP tools are exposed
    tool_name_prefix: fs           # optional; prefix to avoid name collisions

  # MCP server — SSE transport
  <alias>:
    type: mcp_sse
    url: http://localhost:8080/sse
    headers: {}                    # optional; ${VAR} placeholders expanded from env

  # MCP server — Streamable HTTP transport
  <alias>:
    type: mcp_http
    url: https://api.example.com/mcp/
    headers:
      Authorization: "Bearer ${MY_TOKEN}"   # ${VAR} expanded at load time; fails fast if unset

agents:
  <agent_name>:
    model: <model_alias>          # reference to models section
    description: "What this agent does"  # optional; used by parent LLM to choose sub-agents
    instruction: |               # inline prompt (use for short, simple prompts)
      Template text with {{state.key}} or {{state.nested.key}} refs.
    # -- OR --
    instruction_file: prompts.my_agent  # dotted ref → <cwd>/prompts/my_agent.md
    static_instruction: |        # optional; never changes — eligible for prompt caching
      You are a helpful assistant.
    # -- OR --
    static_instruction_file: prompts.my_agent_system  # same dotted-ref format
    tools: [tool_alias, ...]      # optional; references to tools section
    input_schema: pkg.module.MyModel   # optional; Pydantic BaseModel for agent-as-tool input
    output_schema: pkg.Module.Class    # optional; Pydantic v2 class for structured output
    output_key: custom_result     # optional; state key to write output (default: agent name)
    generate_content_config:      # optional; per-agent generation overrides
      temperature: 0.2            # 0.0–2.0
      top_p: 0.9                  # 0.0–1.0
      top_k: 40
      max_output_tokens: 1024
      candidate_count: 1
      stop_sequences: ["---"]
      seed: 42
      presence_penalty: 0.0
      frequency_penalty: 0.0
      response_mime_type: "application/json"  # or "text/plain"
      safety_settings:
        - category: HARM_CATEGORY_HARASSMENT
          threshold: BLOCK_NONE
    thinking:                     # optional; enables BuiltInPlanner (Gemini only)
      thinking_budget: 2048       # tokens: 0=disabled, -1=auto, >0=explicit budget
      include_thoughts: false     # include reasoning tokens in response
    retry:                        # optional; retry on error
      max_retries: 3              # 1–10, default 3
      backoff: fixed              # fixed | exponential
      delay_seconds: 1.0          # seconds between retries

  <custom_node_name>:
    type: node                    # escape hatch for non-LLM logic
    ref: pkg.module.MyBaseNode    # importable BaseNode subclass or @node function

workflow:
  nodes: [agent_a, agent_b, agent_c]   # all nodes that participate
  edges:
    - from: agent_a
      to: agent_b
      condition: "success"             # optional: only follow if agent_a yields "success"
    - from: agent_a
      to: agent_c
      condition: default               # optional: fallback if no other condition matches
    - from: agent_a
      to: [agent_b, agent_c]
      parallel: true                   # optional: fan-out to multiple nodes concurrently
      join: agent_d                    # optional: barrier node — wait for all targets
    - from: agent_a
      to: agent_b
      condition: "retry"
      loop:                            # optional: intentional cycle with a safety limit
        max_iterations: 3
        on_exhausted: agent_c          # optional: route here when limit reached
    - from: agent_a
      to: error_handler
      on_error: true                   # optional: fire only if agent_a fails all retries
    - from: agent_a
      to: timeout_handler
      on_error: true
      error_type: TimeoutError         # optional: match a specific exception class name
    - from: agent_a
      to: fallback_handler
      on_error: true
      error_match: "rate.?limit"       # optional: regex match on error message
      condition: default               # optional: default fallback among error edges
    - from: router_agent
      to: "{{state.router_agent}}"     # dynamic destination — resolved from state at runtime
      allowed_targets: [node_a, node_b] # optional: restrict candidate nodes
    - from: classifier
      switch: "{{state.classifier}}"   # switch/case sugar — expands to N eval-condition edges
      cases:
        urgent: handle_urgent
        normal: handle_normal
      default: handle_other
  entry: agent_a                       # first node to run
  max_llm_calls: 20                    # optional: circuit breaker; default 20
```

---

## Branching & Loops

The Modular Agent Designer supports complex graph topologies including conditional branching, retry loops, parallel fan-out, and error routing directly in YAML.

### Conditional Edges
Nodes (especially Agents) can yield a value that determines which edge to follow. 

```yaml
edges:
  - from: classifier
    to: tech_support
    condition: "tech"                  # Exact match

  - from: classifier
    to: billing_support
    condition: ["billing", "invoice"]  # List-based OR logic

  - from: classifier
    to: human_handoff
    condition: default                 # Catch-all fallback
```

#### Python expression conditions (`eval`)

For conditions that can't be expressed as exact strings or lists, use an `eval` block to write a Python expression:

```yaml
edges:
  - from: classifier
    to: vip_handler
    condition:
      eval: "state.get('user', {}).get('is_vip') == True"

  - from: classifier
    to: large_request_handler
    condition:
      eval: "len(state.get('items', [])) > 10"

  - from: classifier
    to: urgent_handler
    condition:
      eval: "bool(re.search(r'urgent|asap', input, re.IGNORECASE))"

  - from: classifier
    to: fallback
    condition: default
```

**Variables available inside `eval`:**

| Name | Value |
|---|---|
| `state` | Full session state dict (`ctx.state.to_dict()`) |
| `input` | Upstream node's output, coerced to a stripped string |
| `raw_input` | Upstream node's raw output value (dict, list, etc.) |
| `re` | Python `re` module (regex) |

**Safe builtins available:** `len`, `int`, `float`, `str`, `bool`, `abs`, `min`, `max`, `any`, `all`, `isinstance`, `sorted`, `sum`, `range`, `list`, `dict`, `set`, `tuple`, `enumerate`, `zip`, `reversed`, `round`.

**Error handling:** if the expression raises `KeyError`, `AttributeError`, `IndexError`, or `TypeError` (e.g. a missing state key), it is treated as `False` and a `WARNING` is logged. `NameError` and `SyntaxError` propagate immediately so broken expressions fail loudly at run time.

#### Validation rules

The loader enforces these rules at parse time (before any workflow runs):
- A source node may not have more than one `default` edge.
- A source node may not mix unconditional edges (no `condition`) with conditional edges — choose one type per source.
- Edges that form a cycle **must** have a `loop:` config — accidental cycles without `loop:` are rejected at load time with a clear error message.

### Switch/Case Sugar

For branching on a single state value, the `switch:` form is more concise than writing N separate `condition: {eval: ...}` edges:

```yaml
edges:
  - from: classifier
    switch: "{{state.classifier}}"       # state template: {{state.x.y.z}}
    cases:
      urgent: handle_urgent
      normal: handle_normal
      low: handle_low
    default: handle_other                # optional catch-all
```

This expands at load time into standard `condition: {eval: ...}` edges — the builder sees plain edges. The `switch:` value can be a `{{state.x.y}}` template or an `{eval: "expr"}` block:

```yaml
  - from: scorer
    switch:
      eval: "state.get('scorer', {}).get('score', 0) > 0.8"
    cases:
      "True": high_quality
      "False": low_quality
```

See [`workflows/switch_example.yaml`](workflows/switch_example.yaml) for a runnable example.

### Dynamic Destination

When a router agent's output determines the next node by name, use a template in `to:` instead of writing one exact-match edge per candidate:

```yaml
agents:
  router:
    model: llm
    instruction: |
      Pick a specialist: analyst, writer, or researcher.
      Reply with exactly one word.

workflow:
  nodes: [router, analyst, writer, researcher]
  entry: router
  edges:
    - from: router
      to: "{{state.router}}"               # resolved from state at runtime
      allowed_targets: [analyst, writer, researcher]   # documents + constrains candidates
```

- `to:` accepts any `{{state.x.y}}` template string. Node-set validation is skipped at load time; the resolved name is validated at runtime.
- `allowed_targets` is optional — when omitted, all workflow nodes are candidates. When provided, only those nodes are wired as route targets and unknown names fail validation at load time.
- If the template resolves to a name that isn't among the candidates, the workflow terminates with a logged error.

See [`workflows/dynamic_router.yaml`](workflows/dynamic_router.yaml) for a runnable example.

### Loop Config (Controlled Cycles)

For review/revision loops where a node routes back to a prior node, use `loop:` to set a safety limit and an optional escape route:

```yaml
edges:
  # reviewer → writer loop (max 3 revisions)
  - from: reviewer
    to: writer
    condition: "revise"
    loop:
      max_iterations: 3           # 1–100; required
      on_exhausted: finalizer     # optional: route here when limit reached

  # reviewer → finalizer (when approved)
  - from: reviewer
    to: finalizer
    condition: "approved"
```

| Field | Type | Default | Description |
|---|---|---|---|
| `max_iterations` | int | 3 | Maximum number of loop iterations (1–100) |
| `on_exhausted` | string | `null` | Node to route to when the limit is reached. If `null`, the branch terminates with a log warning. |

The framework tracks iteration counts in state automatically (key: `_loop_<from>_<to>_iter`). On exhaustion the counter is reset to 0.

See [`workflows/loop_workflow.yaml`](workflows/loop_workflow.yaml) for a runnable writer→reviewer→finalizer loop.

### Agent Retry Config

For transient errors (API timeouts, rate limits), agents can retry automatically before the workflow fails:

```yaml
agents:
  researcher:
    model: smart
    instruction: "Research this topic: {{state.user_input.topic}}"
    retry:
      max_retries: 3              # 1–10 additional attempts (default: 3)
      backoff: exponential        # fixed | exponential
      delay_seconds: 1.0          # base delay between retries
```

| Field | Type | Default | Description |
|---|---|---|---|
| `max_retries` | int | 3 | Number of retry attempts after the first failure (1–10) |
| `backoff` | string | `fixed` | `fixed` — constant delay; `exponential` — delay doubles each attempt |
| `delay_seconds` | float | 1.0 | Base delay in seconds (≥ 0) |

If all retries are exhausted, the error info is written to `state._error_<agent_name>` and the workflow can route via `on_error` edges.

### Error Routing

Edges with `on_error: true` fire **only** when the source node fails (after exhausting all retries). When a node has both normal and `on_error` edges, the framework injects a unified error router — only one path fires (success or error, never both).

**Basic error routing:**
```yaml
edges:
  - from: researcher
    to: writer            # success path

  - from: researcher
    to: error_handler
    on_error: true        # fires on any error
```

**Typed error routing** — match on exception class name (`error_type`) and/or a regex on the error message (`error_match`). Edges are evaluated in declaration order; `condition: default` forces last:

```yaml
edges:
  - from: api_caller
    to: success_handler

  - from: api_caller
    to: timeout_handler
    on_error: true
    error_type: TimeoutError         # exact match on exception class name

  - from: api_caller
    to: rate_limit_handler
    on_error: true
    error_match: "rate.?limit"       # regex match on error message

  - from: api_caller
    to: generic_error_handler
    on_error: true
    condition: default               # catch-all fallback among error edges
```

| Field | Type | Default | Description |
|---|---|---|---|
| `on_error` | bool | `false` | Route only on failure (after all retries) |
| `error_type` | string | `null` | Exact match on exception class name (e.g. `TimeoutError`) |
| `error_match` | string | `null` | Regex pattern matched against the error message |
| `condition: default` | — | — | Catch-all fallback; evaluated last regardless of declaration order |

When both `error_type` and `error_match` are set on one edge, **both** must match. An edge with neither is a wildcard that catches any error (original behavior — backwards-compatible). If no typed edge matches and there is no `condition: default`, the workflow terminates with a logged warning.

The error info in state (`state._error_<agent_name>`):
```json
{
  "error_type": "TimeoutError",
  "error_message": "Request timed out after 30s",
  "attempts": 4
}
```

See [`workflows/typed_errors.yaml`](workflows/typed_errors.yaml) for a runnable example.

### Parallel / Fan-Out Edges

Send work to multiple nodes concurrently using `to: [list]` with `parallel: true`:

```yaml
edges:
  - from: planner
    to: [researcher_a, researcher_b, researcher_c]
    parallel: true
    join: synthesizer              # wait for all three, then proceed
```

| Field | Type | Default | Description |
|---|---|---|---|
| `to` | `string \| list[string]` | — | Single target or list of fan-out targets |
| `parallel` | bool | `false` | Must be `true` when `to` is a list |
| `join` | string | `null` | Barrier node — the workflow proceeds to this node only after all fan-out targets have written their output to state |

The framework auto-generates an invisible join node that polls `ctx.state` for all source outputs before routing to the `join` target.

**Rules:**
- `parallel: true` requires `to` to be a list.
- `join` requires `to` to be a list.
- `loop` is not compatible with fan-out edges (`to: [list]`).
- Fan-out edges are always unconditional (no `condition:` allowed).

---

## Sub-Agents

Sub-agents let a parent agent delegate work to specialist agents at runtime — the parent LLM decides which specialist to call, rather than the workflow graph. This is complementary to workflow edges: use edges for deterministic pipelines, use sub-agents for LLM-driven delegation.

### How it works

Sub-agents are declared under the parent in the `agents` block and are **not** listed in `workflow.nodes`. They are built as plain ADK `Agent` instances and passed to the parent via ADK's native `sub_agents` parameter. ADK automatically wires them as tools the parent LLM can invoke.

### YAML fields

| Field | Type | Default | Description |
|---|---|---|---|
| `sub_agents` | `list[str]` | `[]` | Names of agents to register as sub-agents of this agent |
| `mode` | `chat \| task \| single_turn \| null` | `null` | How the sub-agent is exposed to the parent LLM |
| `description` | `str \| null` | `null` | Shown to the parent LLM to decide delegation — **strongly recommended for sub-agents** |
| `disallow_transfer_to_parent` | `bool` | `false` | Prevent the sub-agent from transferring back to parent |
| `disallow_transfer_to_peers` | `bool` | `false` | Prevent the sub-agent from transferring to sibling agents |
| `parallel_worker` | `bool \| null` | `null` | Sub-agents only — allows parent to invoke multiple specialists concurrently |

**Modes:**
- `single_turn` — sub-agent is wrapped as a callable tool; invoked once and returns a result immediately. Best for specialist tasks.
- `chat` — sub-agent is reachable via `transfer_to_agent`; the LLM can have a back-and-forth conversation with it.
- `task` — sub-agent runs as a background task.

### Example

```yaml
agents:
  search_specialist:
    model: smart_model
    description: "Retrieves factual information about a topic from the web."
    instruction: "Search for factual information on the given topic."
    mode: single_turn           # exposed as a callable tool to the parent
    parallel_worker: true       # may run concurrently with sibling specialists

  analysis_specialist:
    model: smart_model
    description: "Identifies themes and insights from a body of text."
    instruction: "Identify the three most important themes from the findings."
    mode: single_turn
    parallel_worker: true

  coordinator:
    model: smart_model
    static_instruction: "You are a research coordinator. Be decisive and concise."
    instruction: |
      You coordinate research about: {{state.user_input.topic}}.
      Delegate to search_specialist for facts, then analysis_specialist for themes.
      Synthesize a final 200-word brief.
    output_key: final_brief     # write result to state["final_brief"]
    sub_agents:
      - search_specialist
      - analysis_specialist

workflow:
  nodes: [coordinator]          # only the parent is a workflow node
  edges: []
  entry: coordinator
```

### Rules and constraints

- Sub-agents must **not** appear in `workflow.nodes` — they live inside their parent, not the graph.
- Sub-agent names must reference agents defined in the `agents` dict.
- Sub-agent instructions are **static** — `{{state.x}}` templates are not resolved (only the parent's instruction supports templating).
- Circular sub-agent references (A→B→A) are rejected at load time.
- Nested sub-agents are supported: a sub-agent can itself have sub-agents. Build order is resolved automatically.
- `parallel_worker` is only valid on sub-agents; setting it on a workflow node raises a validation error.

---

## ADK 2.0 Agent Efficiency & Flexibility Params

These params expose additional ADK 2.0 `LlmAgent` knobs directly in YAML.

### `static_instruction` — prompt caching

Content that never changes across turns. ADK moves this to a cacheable position in the request, reducing latency and cost on repeated invocations.

```yaml
agents:
  analyst:
    model: my_model
    static_instruction: |
      You are a senior data analyst. Follow these rules strictly:
      1. Always cite sources.
      2. Use bullet points for findings.
    instruction: "Analyse the data in {{state.analyst_input}}."
    # -- OR load from a file --
    # static_instruction_file: prompts.analyst_system
```

### `generate_content_config` — per-agent generation control

Override temperature, token limits, safety settings, and more on a per-agent basis. These take effect at the model-call level and override any model-level `temperature`/`max_tokens` set on the shared `ModelConfig`.

```yaml
agents:
  deterministic_agent:
    model: my_model
    instruction: "Extract structured data."
    generate_content_config:
      temperature: 0.0          # fully deterministic
      max_output_tokens: 512
      seed: 42
      response_mime_type: "application/json"
      stop_sequences: ["---END---"]
      safety_settings:
        - category: HARM_CATEGORY_DANGEROUS_CONTENT
          threshold: BLOCK_NONE

  creative_agent:
    model: my_model
    instruction: "Write a short story."
    generate_content_config:
      temperature: 1.2
      top_p: 0.95
      presence_penalty: 0.3
```

Valid `category` values (from `google.genai.types.HarmCategory`): `HARM_CATEGORY_HARASSMENT`, `HARM_CATEGORY_HATE_SPEECH`, `HARM_CATEGORY_SEXUALLY_EXPLICIT`, `HARM_CATEGORY_DANGEROUS_CONTENT`, etc.

Valid `threshold` values: `BLOCK_NONE`, `BLOCK_ONLY_HIGH`, `BLOCK_MEDIUM_AND_ABOVE`, `BLOCK_LOW_AND_ABOVE`, `OFF`.

### `thinking` — per-agent BuiltInPlanner (Gemini 2.5+)

Enables ADK's `BuiltInPlanner` with a Gemini thinking budget. This controls how many tokens Gemini spends on internal reasoning before producing its response.

```yaml
agents:
  reasoning_agent:
    model: gemini_model
    instruction: "Solve this multi-step problem: {{state.problem}}"
    thinking:
      thinking_budget: 2048     # 0=disabled, -1=auto, >0=explicit token budget
      include_thoughts: false   # true to surface reasoning in the response
```

> **Note:** `thinking` applies only to Gemini models that support thinking (e.g. `gemini-2.5-flash`). For Anthropic extended-thinking or OpenAI reasoning_effort, use the model-level `thinking:` config on `ModelConfig`.

### `input_schema` — typed agent-as-tool input

When a sub-agent is invoked as a tool by its parent, `input_schema` constrains what the parent LLM can pass in.

```yaml
agents:
  typed_specialist:
    model: my_model
    instruction: "Process the query."
    mode: single_turn
    input_schema: mypackage.schemas.SearchInput  # dotted import path to a Pydantic BaseModel
```

### `output_key` — custom state key

By default, a workflow agent's output is written to `state[agent_name]`. Override with `output_key` to use a different key:

```yaml
agents:
  summariser:
    model: my_model
    instruction: "Summarise {{state.raw_text}}."
    output_key: summary         # downstream agents read state["summary"]
```

### Full example

See [`workflows/agent_overrides.yaml`](workflows/agent_overrides.yaml) for a complete workflow demonstrating all new params together.

---

## External Prompt Files

For longer prompts, store the text in a separate file and reference it with `instruction_file`:

```yaml
agents:
  researcher:
    model: local
    instruction_file: prompts.research_assistant__researcher
```

- Dots are folder separators; `.md` is appended automatically. The ref above resolves to `<cwd>/prompts/research_assistant__researcher.md`.
- Resolution is from the **project root (cwd where the CLI runs)**, not the YAML file's directory.
- The recommended layout is a top-level `prompts/` directory at the repo root, with files named `<workflow>__<agent>.md`.
- `{{state.x}}` and `{{state.x.y.z}}` template syntax works identically inside prompt files — resolved at node-execution time, not load time.
- `instruction` and `instruction_file` are mutually exclusive. Both are optional — omit them when the agent relies entirely on `static_instruction` or receives its prompt via tool/sub-agent delegation.

---

## State Templating

Every node's output is written to `ctx.state[<agent_name>]` automatically (via ADK's `output_key`).

Reference prior outputs in any instruction using `{{state.<dotted.path>}}`:

```yaml
instruction: |
  The topic is: {{state.user_input.topic}}
  Based on this research: {{state.researcher}}
  Write a summary.
```

`user_input` is always available from the `--input` JSON argument.

### Conditional Blocks

Use `{{#if state.key}}…{{/if}}` to include a section only when the key exists in state and is truthy. This is especially useful in loops where a node's output may not exist on the first iteration:

```yaml
instruction: |
  Write a short paragraph about: {{state.user_input.topic}}
  {{#if state.reviewer}}
  The reviewer provided this feedback — incorporate it:
  {{state.reviewer}}
  {{/if}}
```

- The inner content (including any `{{state.x}}` templates) is included only when the condition key exists and is truthy.
- If the key is missing or falsy (empty string, `null`, `0`, `false`), the entire block is removed.
- Conditional blocks are resolved **before** value templates — so `{{state.x}}` references inside the block are safe.
- Nesting conditional blocks is not supported.

**Rules:**
- Double-brace syntax `{{state.x}}` — resolved by Modular Agent Designer at node execution time.
- Single-brace `{key}` — passed through to ADK's native state injection.
- Missing key in a `{{state.x}}` reference (outside a conditional block) → `StateReferenceError` naming the exact missing path and available keys. No silent empty strings.

---

## Model Providers

All providers route through `LiteLlm`. API keys are read from environment variables only — never from YAML.

| Provider | model prefix | Env var required |
|---|---|---|
| Ollama | `ollama/` (completion) · `ollama_chat/` (tools/structured output) | `OLLAMA_API_BASE` (default: `http://localhost:11434`) |
| Anthropic | `anthropic/` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/` | `GOOGLE_API_KEY` |
| OpenAI | `openai/` | `OPENAI_API_KEY` |

Provider validation: the model string must start with the expected prefix (e.g., `gemini/gemini-2.5-flash` for Google). This is checked at load time.

---

## Bundled Tools

The following tools ship with the framework and can be referenced by short name using `type: builtin` + `name:`:

| Name | Description |
|---|---|
| `fetch_url` | Async HTTP GET — returns response body as text. On HTTP error returns `ERROR: …` string (never raises). |
| `http_get_json` | Async HTTP GET — parses response as JSON and returns a `dict`. On error returns `{"error": "…"}`. |
| `read_text_file` | Read a UTF-8 text file at a path relative to CWD. Rejects absolute paths and `..` traversal. Returns file contents or an `ERROR: …` string. |

```yaml
tools:
  fetch:
    type: builtin
    name: fetch_url   # resolves to modular_agent_designer.tools.fetch_url

  fetch_json:
    type: builtin
    name: http_get_json

  read_file:
    type: builtin
    name: read_text_file

agents:
  researcher:
    model: my_model
    instruction: "Fetch https://example.com and summarize it."
    tools: [fetch]
```

You can also reference bundled tools by dotted path if you prefer:

```yaml
tools:
  fetch:
    type: builtin
    ref: modular_agent_designer.tools.fetch_url
```

---

## MCP Tools

Any tool with `type: mcp_stdio`, `mcp_sse`, or `mcp_http` is wired as a [`McpToolset`](https://google.github.io/adk-docs/) — the full ADK MCP integration. Agents reference MCP tools by their YAML alias name exactly like any other tool:

```yaml
tools:
  fs:
    type: mcp_stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/sandbox"]
    tool_name_prefix: fs

agents:
  writer:
    model: my_model
    instruction: "List the files and write a summary."
    tools: [fs]
```

**Secrets**: header/env values containing `${VAR}` are replaced with the corresponding environment variable at load time. If the variable is unset the load fails immediately with a clear error — credentials are never stored in YAML.

**Lifecycle**: MCP connections are opened lazily on first use and closed automatically by the ADK Runner. No teardown code is required.

See [`workflows/mcp_example.yaml`](workflows/mcp_example.yaml) for a runnable reference showing all three transports together.

---

## Using Tools from a Local Directory (No Install Required)

Drop a `tools/` package in your project root and reference callables directly from YAML — no `pip install` needed.

**Layout:**

```
your-project/
  tools/
    __init__.py          # empty — makes tools/ a Python package
    text_tools.py        # your custom functions
  workflows/
    my_workflow.yaml
```

**YAML reference:**

```yaml
tools:
  word_count:
    type: python
    ref: tools.text_tools.word_count
```

**How it works:** `modular-agent-designer` automatically prepends the current working directory (CWD) and the YAML file's directory to `sys.path` at startup. Run the CLI from your project root and any local package is importable.

```bash
# Run from the project root — tools/ is on sys.path automatically
uv run modular-agent-designer run workflows/my_workflow.yaml --input '{"text": "hello"}'
```

See [`tools/text_tools.py`](tools/text_tools.py) and [`workflows/local_tools_example.yaml`](workflows/local_tools_example.yaml) for a working reference.

---

## Using Tools from External Packages

Any Python callable in an **installed package** can be used as a tool — no changes to `modular_agent_designer` are needed:

```yaml
tools:
  forecast:
    type: python
    ref: mycompany_tools.weather.get_forecast   # any pip-installed package
```

**Requirements:**
- The package must be installed in the same Python environment that runs `modular-agent-designer` (`uv pip install -e ./your_pkg --prerelease=allow` for a local editable install).
- The ref must resolve to a **callable** (function, async function, or instance with `__call__`). Pointing at a module, a dict, or a non-callable attribute will raise a `TypeError` at load time naming the alias and the resolved type.

See [`workflows/external_tool_example.yaml`](workflows/external_tool_example.yaml) for a runnable example with setup instructions.

---

## Adding a New Agent

Edit only the YAML — no Python changes:

1. Add a model alias (if needed) to `models:`.
2. Add the agent under `agents:` with `model:`, `instruction:`, and optional `tools:`.
3. Add the agent name to `workflow.nodes`.
4. Add edges connecting it to the chain under `workflow.edges`.

---

## Adding a Custom BaseNode

For logic that isn't a plain LLM call (branching, loops, side effects):

```yaml
agents:
  my_router:
    type: node
    ref: my_package.nodes.RouterNode   # BaseNode subclass or @node-decorated function
    config:                            # optional; forwarded as kwargs to the constructor
      threshold: 0.8
      label: primary
```

```python
# my_package/nodes.py
from google.adk.workflow import BaseNode, node
from google.adk import Context, Event

class RouterNode(BaseNode):
    def __init__(self, name: str, threshold: float = 0.5, label: str = "default"):
        super().__init__(name=name)
        self.threshold = threshold
        self.label = label

    async def run(self, ctx: Context, node_input):
        # Read/write ctx.state, call ctx.run_node(), yield Events
        if "keyword" in node_input:
            yield Event(route="path_a")
        else:
            yield Event(route="path_b")
```

The `config:` mapping is passed as keyword arguments to the `BaseNode` subclass constructor (alongside `name`). This lets you parameterise a node in YAML without writing a new subclass per configuration. `@node`-decorated plain functions ignore `config:`.

Custom nodes handle their own state writes.

---

## Running Tests

```bash
uv run pytest                              # all tests (e2e skipped if Ollama unreachable)
uv run pytest -k "not ollama"             # unit tests only
uv run pytest tests/test_end_to_end_ollama.py -v   # e2e (requires Ollama)
```

---

## Using as a Library

You can integrate Modular Agent Designer into your own Python applications.

```python
import asyncio
import json
from modular_agent_designer import load_workflow, build_workflow, run_workflow_async

async def main():
    # 1. Load the workflow configuration
    cfg = load_workflow("workflows/hello_world.yaml")
    
    # 2. Build the workflow graph
    workflow = build_workflow(cfg)
    
    # 3. Define the input data
    input_data = {"topic": "AI Agents"}
    
    # 4. Run the workflow asynchronously
    # This handles session management and state injection
    final_state = await run_workflow_async(workflow, input_data)
    
    print(json.dumps(final_state, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
```

---

## CLI Reference

### `create` — scaffold a new agent project

```
modular-agent-designer create <agent_name> [--dir <parent>] [--force]
```

- `agent_name` — name of the agent folder to create; must be a valid Python identifier
- `--dir` — parent directory to create the folder in (defaults to CWD)
- `--force` — overwrite existing files in the target folder

Creates `<agent_name>/` containing a ready-to-run YAML workflow, a Python entry point, a `tools/` package, and a per-agent README.

### `run` — execute a workflow

```
modular-agent-designer run <yaml_path> (--input '<json>' | --input-file <path>) [options]
```

- `yaml_path` — path to the workflow YAML file
- `--input '<json>'` — JSON object available as `state.user_input` in templates (mutually exclusive with `--input-file`)
- `--input-file PATH` — read input JSON from a file; use `-` to read from stdin
- `--mlflow EXPERIMENT_ID` — Enable MLflow tracing via OTLP
- `--log-level LEVEL` — set logging verbosity: `DEBUG`, `INFO`, `WARNING`, or `ERROR`

Exactly one of `--input` or `--input-file` is required. Output: final session state as pretty-printed JSON.

```bash
# Inline JSON
uv run modular-agent-designer run workflows/hello_world.yaml --input '{"topic":"AI"}'

# From a file
uv run modular-agent-designer run workflows/hello_world.yaml --input-file input.json

# From stdin
echo '{"topic":"AI"}' | uv run modular-agent-designer run workflows/hello_world.yaml --input-file -

# With debug logging
uv run modular-agent-designer run workflows/hello_world.yaml --input '{"topic":"AI"}' --log-level DEBUG
```

### `validate` — check a workflow without running it

```
modular-agent-designer validate <yaml_path> [--skip-build]
```

- `yaml_path` — path to the workflow YAML file
- `--skip-build` — only validate the YAML schema; skip the build step (avoids API-key checks — useful in CI without secrets)

Exits `0` on success, `1` on any error. Useful for CI pipelines and pre-commit hooks.

```bash
# Full validation (schema + build — requires API keys)
uv run modular-agent-designer validate workflows/research_assistant.yaml

# Schema-only (no API keys required)
uv run modular-agent-designer validate workflows/research_assistant.yaml --skip-build
```

### `list` — inspect a workflow's structure

```
modular-agent-designer list <yaml_path>
```

Loads the YAML and prints a human-readable summary of models, tools, skills, agents, and the workflow graph (entry point, nodes, edges with conditions). No LLM calls or API keys required.

### `diagram` — visualize a workflow as a Mermaid flowchart

```
modular-agent-designer diagram <yaml_path> [--output PATH]
```

- `yaml_path` — path to the workflow YAML file
- `--output PATH` — write the diagram to a file instead of stdout

Loads the workflow config (no LLM calls, no API keys required) and emits a [Mermaid](https://mermaid.js.org/) `flowchart TD` to stdout. Paste the output into [mermaid.live](https://mermaid.live) or any Markdown renderer (GitHub, Notion, Obsidian) to get an instant visual of your node/edge graph.

```bash
# Print to terminal — pipe into a .mmd file or paste into mermaid.live
uv run modular-agent-designer diagram workflows/conditional_workflow.yaml

# Write directly to a file
uv run modular-agent-designer diagram workflows/complex_conditions.yaml --output diagram.mmd
```

Example output for `conditional_workflow.yaml`:

```
flowchart TD
    START((start))
    classifier["classifier<br/>(local_fast)"]
    tech_expert["tech_expert<br/>(local_fast) · chat"]
    creative_expert["creative_expert<br/>(local_fast)"]
    START --> classifier
    classifier -. "technical" .-> tech_expert
    classifier -. "creative" .-> creative_expert
```

**What gets rendered:**

| Element | Mermaid representation |
|---|---|
| Workflow entry | Virtual `START` node with solid arrow to the entry node |
| LLM agent | Rectangle with `name (model_alias)` label; appends `· chat` for chat mode |
| Custom `BaseNode` | Hexagon with `name (ref)` label |
| Unconditional edge | Solid arrow `-->` |
| String / integer condition | Dashed arrow with the value as label |
| List condition (OR logic) | Dashed arrow with values joined by `\|` |
| `eval` condition | Dashed arrow with `eval: <expression>` label (truncated to 40 chars) |
| `default` fallback | Dashed arrow with `default` label |
| Sub-agents | Subgraph cluster under the parent node, with dotted edges |

---

## AI Coding Assistant Skills

The [`ai_skills/`](ai_skills/) directory contains five task-specific skills for Claude Code and Gemini CLI. Both tools use the same [ADK SKILL.md format](https://geminicli.com/docs/cli/skills/) and auto-discover skills from a `.claude/skills/` or `.gemini/skills/` directory in your project root.

| Task | Skill |
|---|---|
| Full reference / first time using the library | `mad-overview` |
| Building a new workflow from scratch | `mad-create-workflow` |
| Adding tools (builtin, python, MCP stdio/SSE/HTTP) | `mad-tools` |
| Conditional routing, loops, error routing, parallel edges | `mad-routing` |
| Sub-agents, skills, output schemas, custom nodes | `mad-sub-agents` |

### Claude Code

**1. Copy skills into the Claude Code discovery directory:**

```bash
mkdir -p .claude/skills
for skill in ai_skills/mad-*/; do cp -r "$skill" .claude/skills/; done
```

This creates `.claude/skills/mad-overview/`, `.claude/skills/mad-create-workflow/`, etc. Commit these to version control so every contributor gets them automatically.

**2. Start a Claude Code session — skills are auto-loaded:**

```bash
claude
```

Claude Code reads each skill's `name` and `description` at session start and activates the relevant skill based on your request.

**3. Invoke a skill manually:**

```
/mad-overview
/mad-create-workflow
/mad-tools
/mad-routing
/mad-sub-agents
```

---

### Gemini CLI

**1. Copy skills into the Gemini CLI discovery directory:**

```bash
mkdir -p .gemini/skills
for skill in ai_skills/mad-*/; do cp -r "$skill" .gemini/skills/; done
```

Commit `.gemini/skills/` to version control to share skills across your team.

**2. Start a Gemini CLI session — skills are auto-discovered:**

```bash
gemini
```

The model reads each skill's name and description at startup. When your prompt matches a skill, it calls `activate_skill` to pull in the full instructions automatically. Only the metadata is loaded upfront, saving context tokens.

**3. Invoke a skill manually:**

```
/mad-overview
/mad-routing
```

---

### User-level install (available in all projects)

To make the skills available globally — not just in this project — copy them to your home directory:

```bash
# Claude Code
mkdir -p ~/.claude/skills
for skill in ai_skills/mad-*/; do cp -r "$skill" ~/.claude/skills/; done

# Gemini CLI
mkdir -p ~/.gemini/skills
for skill in ai_skills/mad-*/; do cp -r "$skill" ~/.gemini/skills/; done
```

See [`ai_skills/README.md`](ai_skills/README.md) for the full skill reference.

---

## Required Env Vars Summary

```bash
# Ollama (optional — defaults to http://localhost:11434)
export OLLAMA_API_BASE=http://localhost:11434

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Google Gemini
export GOOGLE_API_KEY=AIza...

# OpenAI
export OPENAI_API_KEY=sk-...
```

---

## Architecture Notes

- **ADK version**: `google-adk[extensions]==2.0.0a3` (alpha — breaking changes expected)
- **State injection**: Initial state from `--input` is set via `InMemorySessionService.create_session(state={"user_input": ...})` before the workflow runs
- **Template timing**: `{{state.x.y}}` is resolved at node execution time (not workflow construction time) using `ctx.state.to_dict()`. Conditional blocks `{{#if state.key}}…{{/if}}` are resolved first, then value templates.
- **AgentNode wrapper**: Each YAML agent becomes a `@node(rerun_on_resume=True)` async generator — required by ADK when calling `ctx.run_node()`. Optionally wrapped in a retry loop when `retry:` config is set.
- **Branching**: Supports literal matching, list-based OR logic, `eval` expressions, `default` catch-alls, `switch/case` sugar (expanded at load time), and dynamic destinations (`to: "{{state.x}}"` resolved at runtime).
- **Loops**: Controlled via `loop:` config on edges. Iteration counters are tracked in state (`_loop_<from>_<to>_iter`). Accidental cycles (without `loop:`) are rejected at load time.
- **Error routing**: `on_error: true` edges fire only when a node fails (after all retries). Supports typed matching via `error_type` (exact exception class) and `error_match` (regex on message). A unified error router ensures exactly one path fires (success or error).
- **Parallel / Fan-out**: `to: [list]` with `parallel: true` dispatches to multiple nodes. An auto-generated join node (when `join:` is set) waits for all fan-out targets before continuing.
