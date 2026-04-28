---
name: mad-overview
description: Complete reference for the modular-agent-designer YAML DSL, execution pipeline, and CLI.
---

# modular-agent-designer: Complete Reference

`modular-agent-designer` is a declarative YAML-to-ADK workflow compiler. You describe agents, models, tools, and graph topology in a single YAML file; the framework compiles it into an executable Google ADK `Workflow` and runs it. No Python code is needed to build or modify agent pipelines.

---

## Execution Pipeline

```
YAML file
  → load_workflow(path)       # config/loader.py — parse + Pydantic validate → RootConfig
  → build_workflow(cfg)       # workflow/builder.py — compile to ADK Workflow
  → run_workflow_async(wf, input_data)   # __init__.py — execute + return state dict
```

---

## Full YAML Structure

A workflow file has six top-level keys: `name`, `description` (optional), `models`, `tools`, `skills`, `agents`, `workflow`.

```yaml
name: my_workflow
description: Optional human-readable description.

models:
  fast:
    provider: anthropic
    model: anthropic/claude-haiku-4-5-20251001
    temperature: 0.7
    max_tokens: 1024

  smart:
    provider: anthropic
    model: anthropic/claude-sonnet-4-6
    thinking:
      type: enabled         # Anthropic extended-thinking
      budget_tokens: 2048

tools:
  fetch:
    type: builtin
    name: fetch_url

  my_fn:
    type: python
    ref: mypackage.module.my_function

  fs:
    type: mcp_stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    env:
      SOME_VAR: ${MY_ENV_VAR}     # expanded at load time; fails immediately if unset
    tool_filter: [read_file, write_file]
    tool_name_prefix: fs

skills:
  summarizer:
    ref: modular_agent_designer.skills.summarize-text

agents:
  researcher:
    model: fast
    instruction: |
      Research this topic: {{state.user_input.topic}}
      Use fetch to gather information.
    tools: [fetch]
    skills: [summarizer]
    # -- OR -- use a dotted ref to a prompt file instead of inline instruction:
    # instruction_file: prompts.my_workflow__researcher

  writer:
    model: smart
    description: "Writes polished articles from research findings."
    instruction: |
      Based on this research: {{state.researcher}}
      Write a polished 300-word article.
    output_schema: mypackage.models.Article   # optional Pydantic v2 class
    output_key: article                       # state["article"] instead of state["writer"]
    generate_content_config:
      temperature: 0.8
      max_output_tokens: 1024

  coordinator:
    model: smart
    mode: task
    static_instruction: "You are a research coordinator. Be decisive."
    instruction: |
      Coordinate research on: {{state.user_input.topic}}
      Delegate to your sub-agents.
    thinking:
      thinking_budget: 1024
    sub_agents:
      - researcher

  router:
    type: node                                # custom BaseNode escape hatch
    ref: mypackage.nodes.RouterNode

workflow:
  nodes: [researcher, writer]
  entry: researcher
  max_llm_calls: 20                           # default: 20
  edges:
    - from: researcher
      to: writer
```

---

## Models

| Provider | Required model prefix | Required env var |
|---|---|---|
| Ollama | `ollama/` or `ollama_chat/` | `OLLAMA_API_BASE` (default: `http://localhost:11434`) |
| Anthropic | `anthropic/` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/` | `GOOGLE_API_KEY` |
| OpenAI | `openai/` | `OPENAI_API_KEY` |

API keys are validated at **build time** (not at inference time) — missing keys fail before any LLM call.

**Thinking/reasoning config (provider-specific):**

```yaml
# Anthropic extended-thinking
thinking:
  type: enabled
  budget_tokens: 2048

# OpenAI o-series
thinking:
  reasoning_effort: medium   # low | medium | high

# Gemini 2.5
thinking:
  include_thoughts: true
  thinking_budget: 2048
```

---

## Agents Fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `model` | string | Yes | Must reference a key in `models:` |
| `description` | string | No | Shown to the parent LLM to decide delegation — strongly recommended for sub-agents |
| `instruction` | string | No | Inline prompt; supports `{{state.x.y}}` templates, resolved at execution time. Mutually exclusive with `instruction_file`. |
| `instruction_file` | string | No | Dotted ref to a `.md` file resolved from cwd, e.g. `prompts.my_workflow__researcher`. Mutually exclusive with `instruction`. |
| `static_instruction` | string | No | Cacheable static system content; never changes — ADK sends it to a cache-eligible position |
| `static_instruction_file` | string | No | Dotted ref to a `.md` file containing the static instruction |
| `tools` | list[string] | No | References to keys in `tools:` |
| `skills` | list[string] | No | References to keys in `skills:` |
| `input_schema` | string | No | Dotted path to a Pydantic `BaseModel` class — constrains input when agent is invoked as a tool |
| `output_schema` | string | No | Dotted path to a Pydantic v2 class for structured output |
| `output_key` | string | No | State key to write output to; default is the agent name |
| `sub_agents` | list[string] | No | Names of sub-agent agents; must NOT appear in `workflow.nodes` |
| `mode` | string | No | `chat`, `task`, or `single_turn` |
| `parallel_worker` | bool | No | Sub-agents only — allow parent to invoke concurrently with siblings |
| `generate_content_config` | object | No | Per-agent generation overrides; see below |
| `thinking` | object | No | `{thinking_budget, include_thoughts}` — builds a `BuiltInPlanner` (Gemini 2.5+ only) |
| `retry` | object | No | Retry config: `{max_retries: 3, backoff: fixed\|exponential, delay_seconds: 1.0}` |
| `disallow_transfer_to_parent` | bool | No | Default: false |
| `disallow_transfer_to_peers` | bool | No | Default: false |
| `include_contents` | string | No | `default` or `none`; `none` strips conversation history from the context |
| `type` | string | No | `"node"` for custom BaseNode escape hatch |
| `ref` | string | No | Dotted path to BaseNode subclass (when `type: node`) |

### `generate_content_config` fields

| Field | Type | Notes |
|---|---|---|
| `temperature` | float | 0.0–2.0 |
| `top_p` | float | 0.0–1.0 |
| `top_k` | int | ≥ 1 |
| `max_output_tokens` | int | ≥ 1 |
| `candidate_count` | int | ≥ 1 |
| `stop_sequences` | list[string] | Stop tokens |
| `seed` | int | For reproducible outputs |
| `presence_penalty` | float | |
| `frequency_penalty` | float | |
| `response_mime_type` | string | e.g. `"application/json"` or `"text/plain"` |
| `cached_content` | string | Explicit cache resource name (advanced) |
| `safety_settings` | list | `[{category: ..., threshold: ...}]` |

---

## Workflow Fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `nodes` | list[string] | Yes | All agents that participate in the graph; sub-agents must NOT be here |
| `entry` | string | Yes | First node to execute |
| `edges` | list | Yes | Can be empty `[]` for single-node workflows |
| `max_llm_calls` | int | No | Circuit breaker; default 20 |

### Edge Fields

| Field | Type | Default | Notes |
|---|---|---|---|
| `from` | string | — | Source node (required) |
| `to` | `string \| list[string]` | — | Target node(s); list enables fan-out; `"{{state.x}}"` template enables dynamic destination (required) |
| `condition` | string/int/bool/list/eval | `null` | Routing condition (exact match, list OR, eval expression, or `default`) |
| `switch` | string/eval | `null` | Sugar: `"{{state.x}}"` template or `{eval: expr}`; matched against `cases` keys; expands to N eval edges at load time |
| `cases` | map | `null` | Required with `switch:` — map of value → target node |
| `default` | string | `null` | Fallback target when no `switch:` case matches (equivalent to `condition: default`) |
| `allowed_targets` | list[string] | `null` | Constrains a dynamic `to:` template; unknown names rejected at load time |
| `loop` | object | `null` | `{max_iterations, on_exhausted}` — controlled cycle; required for any edge forming a loop |
| `on_error` | bool | `false` | Fire only when source node fails (after all retries); mutually exclusive with `condition` |
| `error_type` | string | `null` | Exact match on exception class name; requires `on_error: true` |
| `error_match` | string | `null` | Python `re.search` pattern on error message; requires `on_error: true` |
| `parallel` | bool | `false` | Fan-out; requires `to: [list]` |
| `join` | string | `null` | Barrier node — wait for all fan-out targets; requires `to: [list]` |

---

## State System

Every node's output is automatically written to `state[agent_name]`. The `--input` JSON is available as `state.user_input`.

```yaml
instruction: |
  The topic is: {{state.user_input.topic}}
  Researcher found: {{state.researcher}}
```

- Templates are resolved at **node execution time**, not compile time.
- Missing key → `StateReferenceError` naming the exact missing path and available keys.
- Pydantic model outputs are JSON-stringified before being stored.

### Conditional Blocks

Use `{{#if state.key}}…{{/if}}` to conditionally include instruction content:

```yaml
instruction: |
  Write about: {{state.user_input.topic}}
  {{#if state.reviewer}}
  Reviewer feedback to incorporate: {{state.reviewer}}
  {{/if}}
```

- Block included only when key exists and is truthy.
- Resolved **before** value templates — `{{state.x}}` refs inside are safe.
- Essential for loops where a node's output doesn't exist on the first iteration.

---

## CLI

```bash
# Install (--prerelease=allow is mandatory)
uv sync --prerelease=allow

# Run a workflow
uv run modular-agent-designer run <yaml_path> --input '<json>'
uv run modular-agent-designer run <yaml_path> --input-file input.json   # read input from file
uv run modular-agent-designer run <yaml_path> --input-file -            # read from stdin
uv run modular-agent-designer run <yaml_path> --input '<json>' --log-level DEBUG

# With MLflow tracing
uv run modular-agent-designer run <yaml_path> --input '<json>' --mlflow <experiment_id>

# Validate without running (no API keys required with --skip-build)
uv run modular-agent-designer validate <yaml_path>
uv run modular-agent-designer validate <yaml_path> --skip-build   # schema-only, CI-safe

# Inspect a workflow's structure (no API keys required)
uv run modular-agent-designer list <yaml_path>

# Visualize a workflow as a Mermaid flowchart (no API keys required)
uv run modular-agent-designer diagram <yaml_path>
uv run modular-agent-designer diagram <yaml_path> --output diagram.mmd
```

**`run`** — executes the workflow and prints the final session state as pretty-printed JSON. `--input` and `--input-file` are mutually exclusive; exactly one is required. `--log-level` accepts `DEBUG`, `INFO`, `WARNING`, `ERROR`.

**`validate`** — validates schema and (by default) also builds the workflow to check API keys and tool refs. `--skip-build` skips the build step so CI can run without secrets. Exits `0` on success, `1` on error.

**`list`** — prints a human-readable summary of models, tools, skills, agents (including which are workflow nodes vs. sub-agents), and edges with their conditions.

**`diagram`** — emits a Mermaid `flowchart TD`. Paste into [mermaid.live](https://mermaid.live) or any GitHub/Markdown renderer. Nodes are rectangles (LLM agents) or hexagons (custom BaseNode). Edges are solid (unconditional) or dashed with a label (string/list/eval/default). Sub-agents appear as a named subgraph cluster.

---

## Library API

```python
import asyncio
from modular_agent_designer import load_workflow, build_workflow, run_workflow_async

async def main():
    cfg = load_workflow("workflows/my_workflow.yaml")
    workflow = build_workflow(cfg)
    final_state = await run_workflow_async(workflow, {"topic": "AI"})
    print(final_state)  # JSON-serializable dict

asyncio.run(main())
```

---

## Key Gotchas

- **`--prerelease=allow` is mandatory** for all `uv` commands — omitting it breaks dependency resolution.
- **Model IDs require provider prefix** — `anthropic/claude-sonnet-4-6`, not `claude-sonnet-4-6`. Pydantic rejects bare names at load time.
- **Sub-agents must NOT appear in `workflow.nodes`** — they live inside their parent agent, not the graph.
- **`ollama_chat/` prefix required for tool calling and reasoning** — `ollama/` works for plain completion only.
- **`{{state.x}}` templates are NOT resolved in sub-agent instructions** — only workflow node instructions support templating.
- **MCP connections are lazy** — opened on first tool use, auto-closed by the ADK Runner. Don't manage their lifecycle manually.
- **`${VAR}` in `env`/`headers` fails immediately at load time** if the env var is unset — by design.
- **API keys checked at build time** — set them before calling `build_workflow()` or running the CLI.
- **Cycles require `loop:` config** — any edge forming a cycle without `loop:` is rejected at load time to prevent accidental infinite loops.
- **Use `{{#if state.x}}` in loops** — referencing `{{state.x}}` for a node that hasn't run yet raises `StateReferenceError`; wrap in a conditional block.
- **`on_error` and `condition` are mutually exclusive** — error edges cannot have conditions.
