# Modular Agent Designer

Modular Agent Designer is a YAML-first toolkit for designing automated agent
workflows from modular pieces: models, tools, agents, routing, retries, loops,
and handoffs.

It is built for people who want to automate agent workflows without writing
orchestration code. Most workflows live entirely in YAML. Python is only needed
when you add custom tools, Pydantic schemas, or custom node logic.

```bash
mad create my_agent
mad run my_agent/my_agent.yaml --input '{"message": "hello"}'
```

`mad` is the short alias for `modular-agent-designer`.

If you are running from a cloned checkout without activating the virtual
environment, prefix commands with `uv run`, for example `uv run mad run ...`.

---

## What You Build

A Modular Agent Designer workflow describes:

| Part | What it controls |
|---|---|
| `models` | Which LLM providers and model aliases your agents use |
| `tools` | Builtin tools, Python functions, or MCP tools agents can call |
| `agents` | Agent prompts, model choices, tools, schemas, retries, and behavior |
| `workflow` | The graph: entry node, edges, routing, loops, fan-out, and handoffs |

You can start with a single agent and grow into multi-step workflows:

```text
user input -> researcher -> analyst -> writer -> final state
```

Or design more advanced automations:

```text
classifier
  -> billing agent
  -> technical agent
  -> human handoff
```

---

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --prerelease=allow
```

For development tools:

```bash
uv pip install -e ".[dev]" --prerelease=allow
```

For optional MLflow / OTLP tracing support:

```bash
uv sync --extra telemetry --prerelease=allow
```

If you use the default scaffolded local workflow, install and start Ollama:

```bash
ollama serve
ollama pull gemma:e4b
```

---

## Quickstart

Create a new agent project:

```bash
mad create my_agent
```

This creates:

| File | Purpose |
|---|---|
| `my_agent.yaml` | The workflow definition you edit |
| `agent.py` | ADK web entry point exposing `root_agent` |
| `__init__.py` | Makes the folder importable |
| `README.md` | Per-agent quickstart notes |

Run the workflow:

```bash
mad run my_agent/my_agent.yaml --input '{"message": "hello"}'
```

Validate it without running:

```bash
mad validate my_agent/my_agent.yaml --skip-build
```

List the workflow structure:

```bash
mad list my_agent/my_agent.yaml
```

Generate a Mermaid diagram:

```bash
mad diagram my_agent/my_agent.yaml
```

You can also use the full command name:

```bash
modular-agent-designer run my_agent/my_agent.yaml --input '{"message": "hello"}'
```

---

## Minimal Workflow

This is a complete single-agent workflow:

```yaml
name: hello_world
description: Single-agent sanity check using a local Ollama model.

models:
  local_fast:
    provider: ollama
    model: ollama/gemma4:e4b

tools: {}

agents:
  greeter:
    model: local_fast
    instruction: |
      The user gave you this topic: {{state.user_input.topic}}
      Write a single friendly sentence greeting about that topic.

workflow:
  nodes:
    - greeter
  edges: []
  entry: greeter
```

Run it:

```bash
mad run examples/workflows/hello_world.yaml --input '{"topic": "tide pools"}'
```

Workflow input is stored in state as `state.user_input`, so prompts can refer to
values like `{{state.user_input.topic}}`.

---

## YAML Concepts

### Models

Define provider-specific models once, then reference them by alias from agents.

```yaml
models:
  local:
    provider: ollama
    model: ollama/gemma4:e4b
    thinking:
      reasoning_effort: high

  writer_model:
    provider: openai
    model: openai/gpt-4o
```

Supported provider prefixes:

| Provider | Model prefix | Environment |
|---|---|---|
| Ollama | `ollama/` or `ollama_chat/` | Optional `OLLAMA_API_BASE` |
| Anthropic | `anthropic/` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/` | `GOOGLE_API_KEY` |
| OpenAI | `openai/` | `OPENAI_API_KEY` |

The optional `thinking` field passes provider-specific reasoning settings to
the model. Use `reasoning_effort` for providers that support effort levels, or
Gemini-style fields such as `thinking_budget` and `include_thoughts` when
needed.

### Tools

Tools can be builtin, Python functions, or MCP servers.

```yaml
tools:
  fetch:
    type: builtin
    name: fetch_url

  custom_lookup:
    type: python
    ref: examples.tools.text_tools.keyword_count

  filesystem:
    type: mcp_stdio
    command: docker
    args: ["mcp", "gateway", "run", "--servers=filesystem"]
    tool_name_prefix: fs
```

Use YAML for wiring. Use Python only when the tool itself is custom logic.
Tool invocation exceptions are returned to the agent as structured tool results
so the agent can inspect the failure and route or respond accordingly. If an MCP
server cannot be reached during tool discovery, MAD exposes an
`*_mcp_unavailable` fallback tool. If a model calls a tool name that is not
available, the agent receives a tool-visible "not available" response instead
of an opaque crash.

### Agents

Agents combine a model, instructions, optional tools, and optional output
contracts.

```yaml
agents:
  researcher:
    model: local
    tools: [fetch]
    instruction: |
      Research this topic: {{state.user_input.topic}}
      Return concise findings.

  writer:
    model: local
    instruction: |
      Write a short article from these findings:
      {{state.researcher}}
```

Agent outputs are written back into state under the agent name by default. In
the example above, `writer` can read `{{state.researcher}}`.

### Workflow Graph

The workflow graph chooses which agents run and in what order.

```yaml
workflow:
  nodes: [researcher, writer]
  edges:
    - from: researcher
      to: writer
  entry: researcher
```

See [`examples/workflows/research_assistant.yaml`](examples/workflows/research_assistant.yaml)
for a three-stage workflow.

---

## When Python Is Needed

Most workflow changes should be YAML-only. Reach for Python when you need:

| Need | Use |
|---|---|
| Custom tool behavior | A Python callable referenced by `type: python` |
| Structured agent inputs or outputs | Pydantic models referenced by dotted path |
| Non-LLM workflow logic | A custom ADK `BaseNode` or node function |

Examples:

- [`examples/tools/text_tools.py`](examples/tools/text_tools.py)
- [`examples/schemas/research.py`](examples/schemas/research.py)
- [`examples/workflows/local_tools_example.yaml`](examples/workflows/local_tools_example.yaml)
- [`examples/workflows/output_schema_routing.yaml`](examples/workflows/output_schema_routing.yaml)

---

## Schemas and Runtime Skills

### Structured Schemas

Use Pydantic schemas when an agent should return structured data that later
workflow steps can inspect.

```python
from typing import Literal

from pydantic import BaseModel, Field


class ValidationResult(BaseModel):
    validation_result: Literal["success", "fail"]
    reason: str = Field(description="Why validation passed or failed")
```

Reference the schema from YAML with a dotted path:

```yaml
agents:
  validator:
    model: fast
    output_schema: examples.schemas.validation.ValidationResult
    instruction_file: examples.prompts.output_schema_routing__validator

workflow:
  edges:
    - from: validator
      to: process_node
      condition:
        eval: "state.get('validator', {}).get('validation_result') == 'success'"
    - from: validator
      to: reject_node
      condition: default
```

Example: [`examples/workflows/output_schema_routing.yaml`](examples/workflows/output_schema_routing.yaml)

Route conditions can also inspect the source node's raw structured output with
`output`, which is useful immediately after an `output_schema` agent:

```yaml
workflow:
  default_routes:
    - to: expected_failure_reporter
      condition:
        eval: "output.agent_status == 'fail'"
```

Eval conditions can use:

| Name | Value |
|---|---|
| `state` | Full workflow state; supports `state.user_input.topic` and `state.get(...)` |
| `input` | Source output coerced to stripped text |
| `output` | Raw source output, including structured output fields |
| `raw_input` | Raw source output, retained for compatibility |
| `re` | Python regex module |

### Runtime Skills

Runtime skills are reusable instruction packages that agents can load through
ADK's `SkillToolset`. They are different from the coding-assistant skills in
`src/modular_agent_designer/cli_skills`.

Define skills once at the workflow root, then attach them to agents by alias:

```yaml
skills:
  summarizer:
    ref: modular_agent_designer.skills.summarize-text
  local_summary:
    ref: examples.skills.summarize-text

agents:
  researcher:
    model: local
    instruction_file: prompts.skills_example__researcher
    skills: [summarizer]
```

Each skill ref points to a directory containing `SKILL.md`, such as
[`examples/skills/summarize-text/SKILL.md`](examples/skills/summarize-text/SKILL.md).

Example: [`examples/workflows/skills_example.yaml`](examples/workflows/skills_example.yaml)

---

## Common Workflow Patterns

### Sequential Pipeline

Run agents in a fixed order.

```yaml
edges:
  - from: researcher
    to: analyst
  - from: analyst
    to: writer
```

Example: [`examples/workflows/research_assistant.yaml`](examples/workflows/research_assistant.yaml)

### Conditional Routing

Route based on a node output.

```yaml
edges:
  - from: classifier
    to: technical_support
    condition: tech
  - from: classifier
    to: billing_support
    condition: billing
  - from: classifier
    to: fallback
    condition: default
```

Examples:

- [`examples/workflows/conditional_workflow.yaml`](examples/workflows/conditional_workflow.yaml)
- [`examples/workflows/switch_example.yaml`](examples/workflows/switch_example.yaml)
- [`examples/workflows/dynamic_router.yaml`](examples/workflows/dynamic_router.yaml)

### Workflow-Level Default Routes

Use `workflow.default_routes` when many nodes should share the same conditional
fallback. A default route injects conditional fallback edges at build time and
is shown by `mad list` and `mad diagram`.

```yaml
workflow:
  nodes: [tool_caller, final_reporter, expected_failure_reporter]
  entry: tool_caller
  default_routes:
    - to: expected_failure_reporter
      condition:
        eval: "output.agent_status == 'fail'"
      exclude: [final_reporter]
  edges:
    - from: tool_caller
      to: final_reporter
      condition:
        eval: "state.tool_caller.agent_status == 'success'"
```

Fields:

| Field | Meaning |
|---|---|
| `to` | Fallback target node |
| `condition` | Same condition forms as normal edges, including `eval` |
| `from` | Optional list of source nodes to apply this route to |
| `exclude` | Optional list of source nodes to skip |

Default routes skip self-routes to their target. They are not injected for a
source that already has an unconditional edge or an explicit
`condition: default` edge.

Example: [`examples/workflows/tool_exception_test.yaml`](examples/workflows/tool_exception_test.yaml)

### Switch/Case Routing

Use `switch` when one state value should choose from several named routes. This
keeps classifier-style workflows easier to scan than repeating one conditional
edge per case.

```yaml
edges:
  - from: classifier
    switch: "{{state.classifier}}"
    cases:
      urgent: handle_urgent
      normal: handle_normal
      low: handle_low
    default: handle_normal
```

The `switch` value can read from state with `{{state.key}}`. The optional
`default` target handles values that do not match any case.

Example: [`examples/workflows/switch_example.yaml`](examples/workflows/switch_example.yaml)

### Loops and Retries

Use loops for intentional workflow cycles, such as draft-review-revise flows.
Use retries for transient node failures.

```yaml
agents:
  writer:
    model: local
    instruction: "Write a draft."
    retry:
      max_retries: 3
      backoff: exponential
      delay_seconds: 1

workflow:
  edges:
    - from: reviewer
      to: writer
      condition: revise
      loop:
        max_iterations: 3
```

Examples:

- [`examples/workflows/loop_workflow.yaml`](examples/workflows/loop_workflow.yaml)
- [`examples/workflows/retry_workflow.yaml`](examples/workflows/retry_workflow.yaml)

### Error Handling

Route failures to recovery agents after retries are exhausted. If an agent fails
and there is no matching `on_error` route, MAD stops the workflow and surfaces a
final failure message instead of continuing along normal edges.

```yaml
edges:
  - from: api_caller
    to: success_handler

  - from: api_caller
    to: timeout_handler
    on_error: true
    error_type: TimeoutError

  - from: api_caller
    to: generic_error_handler
    on_error: true
    condition: default
```

Examples:

- [`examples/workflows/typed_errors.yaml`](examples/workflows/typed_errors.yaml)
- [`examples/workflows/agent_failure_stop.yaml`](examples/workflows/agent_failure_stop.yaml)

### Parallel Fan-Out

Send work to multiple agents at the same time and join the results.

```yaml
edges:
  - from: planner
    to: [researcher_a, researcher_b, researcher_c]
    parallel: true
    join: synthesizer
```

Example: [`examples/workflows/parallel_workflow.yaml`](examples/workflows/parallel_workflow.yaml)

### Sub-Agents

Use sub-agents when a parent agent should choose which specialist to call at
runtime.

```yaml
agents:
  coordinator:
    model: local
    instruction: "Delegate to the right specialist."
    sub_agents: [search_specialist, analysis_specialist]

  search_specialist:
    model: local
    description: "Finds relevant source material."
    mode: single_turn

  analysis_specialist:
    model: local
    description: "Turns source material into findings."
    mode: single_turn
```

Example: [`examples/workflows/sub_agent_example.yaml`](examples/workflows/sub_agent_example.yaml)

---

## CLI Reference

| Command | Purpose |
|---|---|
| `mad create <agent-name>` | Scaffold a new editable agent project |
| `mad run <workflow.yaml> --input '<json-or-text>'` | Run a workflow |
| `mad run <workflow.yaml> --input-file <path>` | Run using JSON or text from a file |
| `mad run <workflow.yaml> --input '<json-or-text>' --verbose` | Stream workflow-node, agent, sub-agent, and tool events while running |
| `mad validate <workflow.yaml>` | Validate and build a workflow |
| `mad validate <workflow.yaml> --skip-build` | Validate YAML only, useful in CI without secrets |
| `mad list <workflow.yaml>` | Print models, tools, agents, and graph details |
| `mad diagram <workflow.yaml>` | Emit a Mermaid flowchart |
| `mad cli-skills setup` | Install bundled assistant skills into `.agents/skills` |

Useful run options:

```bash
mad run workflow.yaml --dry-run --verbose
mad run workflow.yaml --input '{"topic": "x"}' --verbose
mad run workflow.yaml --log-level INFO --input '{"topic": "x"}'
mad run workflow.yaml --mlflow 0 --input '{"topic": "x"}'
```

By default, `mad run` prints the final output and final state only. Add
`--verbose` to stream intermediate workflow-node, agent, sub-agent, and tool
events. Use `--log-level` separately when you want Python/library logs.

---

## Examples

Start here:

| Example | What it shows |
|---|---|
| [`hello_world.yaml`](examples/workflows/hello_world.yaml) | Smallest single-agent workflow |
| [`research_assistant.yaml`](examples/workflows/research_assistant.yaml) | Multi-stage pipeline |
| [`local_tools_example.yaml`](examples/workflows/local_tools_example.yaml) | Python tool integration |
| [`mcp_example.yaml`](examples/workflows/mcp_example.yaml) | MCP tool integration |
| [`conditional_workflow.yaml`](examples/workflows/conditional_workflow.yaml) | Conditional branches |
| [`parallel_workflow.yaml`](examples/workflows/parallel_workflow.yaml) | Parallel fan-out and join |
| [`sub_agent_example.yaml`](examples/workflows/sub_agent_example.yaml) | Parent agent with specialists |
| [`output_schema_routing.yaml`](examples/workflows/output_schema_routing.yaml) | Structured output and routing |
| [`tool_exception_test.yaml`](examples/workflows/tool_exception_test.yaml) | Tool failure as structured output plus default routes |
| [`agent_failure_stop.yaml`](examples/workflows/agent_failure_stop.yaml) | Agent failure stopping downstream execution |
| [`skills_example.yaml`](examples/workflows/skills_example.yaml) | Runtime skills attached to an agent |

All workflow examples live in [`examples/workflows`](examples/workflows).

---

## Assistant Skills

The project includes instruction skills for coding assistants. They are not
runtime agent skills; they help a coding assistant guide you through building
workflows.

Install them into a project:

```bash
mad cli-skills setup
```

Available guides:

| Skill | Use it for |
|---|---|
| `mad-overview` | Full project and YAML reference |
| `mad-create-workflow` | Building a workflow from scratch |
| `mad-tools` | Builtin, Python, MCP tools, and tool failure behavior |
| `mad-routing` | Branching, default routes, loops, errors, and parallel edges |
| `mad-sub-agents` | Sub-agents, skills, schemas, structured outputs, and custom nodes |

See [`src/modular_agent_designer/cli_skills/README.md`](src/modular_agent_designer/cli_skills/README.md)
for setup details for Codex, Claude Code, Gemini CLI, and ChatGPT CLI.

---

## Development

Install with development dependencies:

```bash
uv pip install -e ".[dev]" --prerelease=allow
```

Run tests:

```bash
uv run pytest
```

Run a lightweight validation check:

```bash
mad validate examples/workflows/hello_world.yaml --skip-build
```

---

## Project Status

Modular Agent Designer compiles declarative YAML workflows into Google ADK
agents and workflow graphs. The project supports local Ollama workflows,
hosted model providers, builtin tools, custom tools, MCP tools, sub-agents,
structured outputs, retries, routing, diagrams, and optional tracing.
