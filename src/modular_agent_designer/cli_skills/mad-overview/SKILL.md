---
name: mad-overview
description: Use when a coding agent needs the modular-agent-designer YAML DSL, CLI commands, state model, validation flow, or a map to the narrower MAD skills.
---

# modular-agent-designer Overview

`modular-agent-designer` is a YAML-to-Google-ADK workflow compiler. A workflow file declares models, tools, runtime skills, agents, and graph edges; the CLI validates it, builds an ADK workflow, and runs it.

## Use This When

- The user asks how MAD works or which YAML fields are available.
- You need to inspect an unfamiliar MAD project before changing it.
- You need a compact reference before loading a narrower skill.

For implementation tasks, prefer the owner skill:

| Task | Load |
|---|---|
| Build a workflow from scratch | `mad-create-workflow` |
| Add builtin, Python, or MCP tools | `mad-tools` |
| Add conditions, loops, retries, errors, or parallel fan-out | `mad-routing` |
| Add sub-agents, runtime skills, schemas, A2A, or custom nodes | `mad-sub-agents` |

## Agent Workflow

1. Inspect existing `*.yaml`, `prompts/`, `tools/`, `schemas/`, `skills/`, and relevant `examples/workflows/*.yaml`.
2. Identify whether the task changes workflow topology, agent behavior, tools, schemas, or runtime services.
3. Reuse existing aliases and project structure where possible.
4. Validate with:

```bash
mad list path/to/workflow.yaml
mad diagram path/to/workflow.yaml
mad run path/to/workflow.yaml --dry-run
```

Use `uv run modular-agent-designer ...` when working from a source checkout that has not installed the console script.

## Execution Pipeline

```text
YAML file
  -> load_workflow(path)          # parse YAML and validate with Pydantic
  -> build_workflow(cfg)          # compile into ADK workflow nodes
  -> run_workflow_async(wf, input_data)
  -> final session state dict
```

## Top-Level YAML Shape

```yaml
name: my_workflow
description: Optional human-readable description.

models:
  local:
    provider: ollama
    model: ollama_chat/llama3.2

tools:
  fetch:
    type: builtin
    name: fetch_url

skills:
  summarizer:
    ref: modular_agent_designer.skills.summarize-text

agents:
  researcher:
    model: local
    instruction: |
      Research: {{state.user_input.topic}}
    tools: [fetch]
    skills: [summarizer]

workflow:
  nodes: [researcher]
  entry: researcher
  edges: []
```

Required top-level keys: `name`, `models`, `agents`, `workflow`.

Optional top-level keys: `description`, `tools`, `skills`.

## Models

| Provider | Model prefix | Env var |
|---|---|---|
| Ollama | `ollama/` or `ollama_chat/` | `OLLAMA_API_BASE` defaults to `http://localhost:11434` |
| Anthropic | `anthropic/` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/` | `GOOGLE_API_KEY` |
| OpenAI | `openai/` | `OPENAI_API_KEY` |

Hosted provider keys are validated at build time. Missing keys fail before the first model call.

Provider-specific thinking examples:

```yaml
models:
  claude:
    provider: anthropic
    model: anthropic/claude-sonnet-4-6
    thinking:
      type: enabled
      budget_tokens: 2048

  gpt:
    provider: openai
    model: openai/o3-mini
    thinking:
      reasoning_effort: medium

  gemini:
    provider: google
    model: gemini/gemini-2.5-pro
    thinking:
      include_thoughts: true
      thinking_budget: 2048
```

## Agents

Common agent fields:

| Field | Notes |
|---|---|
| `model` | Required for LLM agents; references a key in `models:` |
| `instruction` | Inline prompt with `{{state.x.y}}` templates |
| `instruction_file` | Dotted prompt ref such as `prompts.my_workflow__writer`; mutually exclusive with `instruction` |
| `static_instruction` / `static_instruction_file` | Cacheable system content that does not use state templates |
| `tools` | List of aliases from `tools:` |
| `skills` | List of aliases from `skills:` runtime skills |
| `output_schema` | Dotted Pydantic v2 model path |
| `output_key` | State key override; default is the agent name |
| `sub_agents` | Specialist agent names; those specialists must not be in `workflow.nodes` |
| `mode` | `chat`, `task`, or `single_turn` |
| `retry` | `{max_retries, backoff, delay_seconds}` |
| `include_contents` | `default` or `none` |
| `type: node` | Custom ADK `BaseNode` escape hatch |
| `type: a2a` | Remote Agent2Agent protocol agent |

Prompt templates are resolved only for workflow nodes at execution time:

```yaml
instruction: |
  Topic: {{state.user_input.topic}}
  Previous result: {{state.researcher}}

  {{#if state.reviewer}}
  Reviewer feedback: {{state.reviewer}}
  {{/if}}
```

Missing required state references raise `StateReferenceError` with the missing path and available keys.

## Tools, Skills, and Sub-Agents

- `tools:` declares callable capabilities: builtin tools, Python callables, or MCP toolsets. Load `mad-tools` for details.
- `skills:` declares runtime ADK skills that agents can load via `SkillToolset`. Load `mad-sub-agents` for details.
- `sub_agents:` gives a parent LLM dynamic delegation power. Sub-agents are not graph nodes. Load `mad-sub-agents` for details.

## Workflow Graph

```yaml
workflow:
  nodes: [classifier, specialist, fallback]
  entry: classifier
  max_llm_calls: 20
  edges:
    - from: classifier
      to: specialist
      condition: "specialist"
    - from: classifier
      to: fallback
      condition: default
```

`edges` support:

- Unconditional sequential edges.
- Exact-match and list conditions.
- `condition: {eval: "..."}` expressions.
- `condition: default` fallback.
- `switch:` / `cases:` sugar.
- Dynamic `to: "{{state.router}}"` with `allowed_targets`.
- Controlled loops with `loop:`.
- Error routes with `on_error: true`.
- Parallel fan-out with `to: [...]`, `parallel: true`, and `join:`.

Load `mad-routing` before editing non-trivial graph behavior.

## CLI Commands

| Command | Purpose |
|---|---|
| `mad create <agent_name>` | Scaffold a runnable workflow project |
| `mad validate <workflow.yaml>` | Validate YAML schema |
| `mad list <workflow.yaml>` | Print models, tools, agents, and graph details |
| `mad diagram <workflow.yaml>` | Render a Mermaid graph |
| `mad run <workflow.yaml> --input '{"key":"value"}'` | Execute a workflow |
| `mad run <workflow.yaml> --input-file input.json` | Execute using JSON from a file |
| `mad run <workflow.yaml> --input '{"key":"value"}' --verbose` | Execute and stream workflow-node, agent, sub-agent, and tool events |
| `mad run <workflow.yaml> --dry-run` | Load and build without model execution |
| `mad run <workflow.yaml> --log-level INFO --input '{"key":"value"}'` | Execute with Python/library logging enabled |
| `mad cli-skills setup` | Install assistant skills into `.agents/skills` |

`mad run` prints final output and final state by default. Add `--verbose` only
when you need the intermediate event stream; use `--log-level` separately for
library logs.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Agent references a missing model/tool/skill alias | Add the alias or correct the spelling |
| Sub-agent also appears in `workflow.nodes` | Remove sub-agents from graph nodes |
| `instruction` and `instruction_file` are both set | Keep exactly one |
| Old path-style prompt refs like `../prompts/foo.md` | Use dotted refs like `prompts.my_workflow__agent` |
| Conditional and unconditional edges from the same source | Use one routing style per source node |
| Unbounded cycle in graph edges | Add a `loop:` config with `max_iterations` |
| Hosted model key missing | Export the provider env var before `mad run --dry-run` |
