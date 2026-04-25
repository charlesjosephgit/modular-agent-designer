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

```bash
# Ollama must be running with at least one model pulled:
ollama serve &
ollama pull mistral:7b

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
    instruction: |
      Template text with {{state.key}} or {{state.nested.key}} refs.
    tools: [tool_alias, ...]      # optional; references to tools section
    output_schema: pkg.Module.Class  # optional; Pydantic v2 class

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
  entry: agent_a                       # first node to run
```

---

## Branching & Loops

The Modular Agent Designer supports complex graph topologies including conditional branching and retry loops directly in YAML.

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

The loader enforces two rules at parse time (before any workflow runs):
- A source node may not have more than one `default` edge.
- A source node may not mix unconditional edges (no `condition`) with conditional edges — choose one type per source.

### Self-Loops & Retries
To retry a node (e.g., if a model's output is invalid), route the node back to itself:

```yaml
edges:
  - from: researcher
    to: researcher
    condition: "retry"                 # If 'researcher' returns "retry", it runs again
```

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
| `disallow_transfer_to_parent` | `bool` | `false` | Prevent the sub-agent from transferring back to parent |
| `disallow_transfer_to_peers` | `bool` | `false` | Prevent the sub-agent from transferring to sibling agents |

**Modes:**
- `single_turn` — sub-agent is wrapped as a callable tool; invoked once and returns a result immediately. Best for specialist tasks.
- `chat` — sub-agent is reachable via `transfer_to_agent`; the LLM can have a back-and-forth conversation with it.
- `task` — sub-agent runs as a background task.

### Example

```yaml
agents:
  search_specialist:
    model: smart_model
    instruction: "Search for factual information on the given topic."
    mode: single_turn           # exposed as a callable tool to the parent

  analysis_specialist:
    model: smart_model
    instruction: "Identify the three most important themes from the findings."
    mode: single_turn

  coordinator:
    model: smart_model
    instruction: |
      You coordinate research about: {{state.user_input.topic}}.
      Delegate to search_specialist for facts, then analysis_specialist for themes.
      Synthesize a final 200-word brief.
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

**Rules:**
- Double-brace syntax `{{state.x}}` — resolved by Modular Agent Designer at node execution time.
- Single-brace `{key}` — passed through to ADK's native state injection.
- Missing key → `StateReferenceError` naming the exact missing path and available keys. No silent empty strings.

---

## Model Providers

All providers route through `LiteLlm`. API keys are read from environment variables only — never from YAML.

| Provider | model prefix | Env var required |
|---|---|---|
| Ollama | `ollama/` | `OLLAMA_API_BASE` (default: `http://localhost:11434`) |
| Anthropic | `anthropic/` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/` | `GOOGLE_API_KEY` |
| OpenAI | `openai/` | `OPENAI_API_KEY` |

Provider validation: the model string must start with the expected prefix (e.g., `gemini/gemini-2.5-flash` for Google). This is checked at load time.

---

## Bundled Tools

The following tools ship with the framework and can be referenced by short name using `type: builtin` + `name:`:

| Name | Description |
|---|---|
| `fetch_url` | Fetch a URL and return the response body as text (async, follows redirects, 30 s timeout). |

```yaml
tools:
  fetch:
    type: builtin
    name: fetch_url   # resolves to modular_agent_designer.tools.fetch_url

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
```

```python
# my_package/nodes.py
from google.adk.workflow import BaseNode, node
from google.adk import Context, Event

class RouterNode(BaseNode):
    async def run(self, ctx: Context, node_input):
        # Read/write ctx.state, call ctx.run_node(), yield Events
        if "keyword" in node_input:
            yield Event(route="path_a")
        else:
            yield Event(route="path_b")
```

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

```
modular-agent-designer run <yaml_path> --input '<json>'
```

- `yaml_path` — path to the workflow YAML file
- `--input` — JSON object available as `state.user_input` in templates
- `--mlflow` — Enable MLflow tracing via OTLP (takes experiment ID as argument)

Output: final session state as pretty-printed JSON.

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
- **Template timing**: `{{state.x.y}}` is resolved at node execution time (not workflow construction time) using `ctx.state.to_dict()`
- **AgentNode wrapper**: Each YAML agent becomes a `@node(rerun_on_resume=True)` async generator — required by ADK when calling `ctx.run_node()`
- **Branching**: Supports literal matching, list-based OR logic, and `default` catch-alls.
- **Loops**: Graph cycles (like self-retries) are natively supported via edge definitions.vely supported via edge definitions.
