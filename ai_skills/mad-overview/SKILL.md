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
    instruction: |
      Based on this research: {{state.researcher}}
      Write a polished 300-word article.
    output_schema: mypackage.models.Article   # optional Pydantic v2 class

  coordinator:
    model: smart
    mode: task
    instruction: |
      Coordinate research on: {{state.user_input.topic}}
      Delegate to your sub-agents.
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
| `instruction` | string | One of | Inline prompt; supports `{{state.x.y}}` templates, resolved at execution time |
| `instruction_file` | string | One of | Dotted ref to a `.md` file resolved from cwd, e.g. `prompts.my_workflow__researcher` → `<cwd>/prompts/my_workflow__researcher.md`; `{{state.x.y}}` templates work inside the file |
| `tools` | list[string] | No | References to keys in `tools:` |
| `skills` | list[string] | No | References to keys in `skills:` |
| `output_schema` | string | No | Dotted path to a Pydantic v2 class |
| `sub_agents` | list[string] | No | Names of sub-agent agents; must NOT appear in `workflow.nodes` |
| `mode` | string | No | `chat`, `task`, or `single_turn` |
| `disallow_transfer_to_parent` | bool | No | Default: false |
| `disallow_transfer_to_peers` | bool | No | Default: false |
| `type` | string | No | `"node"` for custom BaseNode escape hatch |
| `ref` | string | No | Dotted path to BaseNode subclass (when `type: node`) |

---

## Workflow Fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `nodes` | list[string] | Yes | All agents that participate in the graph; sub-agents must NOT be here |
| `entry` | string | Yes | First node to execute |
| `edges` | list | Yes | Can be empty `[]` for single-node workflows |
| `max_llm_calls` | int | No | Circuit breaker; default 20 |

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

---

## CLI

```bash
# Install (--prerelease=allow is mandatory)
uv sync --prerelease=allow

# Run a workflow
uv run modular-agent-designer run <yaml_path> --input '<json>'

# With MLflow tracing
uv run modular-agent-designer run <yaml_path> --input '<json>' --mlflow <experiment_id>

# Example
uv run modular-agent-designer run workflows/hello_world.yaml --input '{"topic": "AI agents"}'

# Visualize a workflow as a Mermaid flowchart (no API keys required)
uv run modular-agent-designer diagram <yaml_path>
uv run modular-agent-designer diagram <yaml_path> --output diagram.mmd
```

`run` output: final session state as pretty-printed JSON.

`diagram` output: Mermaid `flowchart TD` text. Paste it into [mermaid.live](https://mermaid.live) or any GitHub/Markdown renderer for an instant visual of the workflow graph. No LLM calls or API keys are needed — it reads only the YAML config. Nodes are rectangles (LLM agents) or hexagons (custom BaseNode). Edges are solid (unconditional) or dashed with a label (string/list/eval/default conditions). Sub-agents appear as a named subgraph cluster.

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
