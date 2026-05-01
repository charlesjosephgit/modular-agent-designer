---
name: mad-create-workflow
description: Use when a coding agent should create or reshape a runnable modular-agent-designer workflow, starting from scaffolded conventions and validating the YAML.
---

# Build a New MAD Workflow

Use this skill when the user wants a new workflow, a scaffolded agent project, or a clear path from idea to runnable YAML.

## Use This When

- The user asks to create a MAD workflow or agent project from scratch.
- Existing YAML is too small or too wrong to extend safely.
- You need to choose the initial agents, models, prompts, and graph shape.

Load another skill when the main work is specialized:

| Need | Load |
|---|---|
| Builtin, Python, or MCP tools | `mad-tools` |
| Branches, switch/case, loops, retries, errors, or parallel fan-out | `mad-routing` |
| Sub-agents, runtime skills, schemas, A2A, or custom nodes | `mad-sub-agents` |
| Full field reference | `mad-overview` |

## Agent Workflow

1. Inspect the repository first: existing `examples/workflows/*.yaml`, prompt conventions, tool packages, schemas, and README commands.
2. Prefer `mad create <agent_name>` for a new standalone project because it creates matching YAML, prompt, tools, schemas, and README files.
3. If editing an existing project, follow its current layout instead of introducing a new one.
4. Keep prompts in `prompts/` when they are long or likely to be reused; use inline `instruction` for short prompts.
5. Validate with `mad list`, `mad diagram`, and `mad run --dry-run` before attempting a live run.

## Step 0: Scaffold First

```bash
mad create <agent_name>
```

The scaffold creates:

```text
<agent_name>/
  <agent_name>.yaml
  agent.py
  __init__.py
  prompts/
    __init__.py
    <agent_name>__responder.md
  tools/
    __init__.py
  schemas/
    __init__.py
  skills/
    __init__.py
  README.md
```

From a source checkout, use:

```bash
uv run modular-agent-designer create <agent_name>
```

Run the scaffolded workflow after the local model is available:

```bash
ollama serve
ollama pull gemma:e4b
mad run <agent_name>/<agent_name>.yaml --input '{"message": "hello"}'
```

## Design Checklist

Before writing YAML, decide:

1. What is the user input shape? Example: `{"topic": "..."}` or `{"message": "..."}`.
2. How many graph nodes are needed? Start with the fewest agents that preserve clear responsibility.
3. Is routing deterministic? Use `workflow.edges` for known paths; use `sub_agents` only when a parent LLM should choose specialists at runtime.
4. Which provider is appropriate? Ollama for local examples; hosted providers require env vars.
5. Which capabilities need tools, schemas, or runtime skills?

## Minimal Runnable Workflow

```yaml
name: hello_world

models:
  local:
    provider: ollama
    model: ollama/llama3.2

agents:
  greeter:
    model: local
    instruction: |
      Write one concise response to: {{state.user_input.message}}

workflow:
  nodes: [greeter]
  entry: greeter
  edges: []
```

Run:

```bash
mad run hello_world.yaml --input '{"message": "hello"}'
```

## Model Block

```yaml
models:
  local:
    provider: ollama
    model: ollama_chat/llama3.2

  claude:
    provider: anthropic
    model: anthropic/claude-sonnet-4-6

  gemini:
    provider: google
    model: gemini/gemini-2.0-flash

  gpt:
    provider: openai
    model: openai/gpt-4o
```

Use `ollama_chat/` when the agent needs tool calling or chat-specific behavior. Hosted providers require their env vars before build:

```bash
export ANTHROPIC_API_KEY=...
export GOOGLE_API_KEY=...
export OPENAI_API_KEY=...
```

## Agent Instructions

Use inline instructions for short prompts:

```yaml
agents:
  researcher:
    model: claude
    instruction: |
      Research this topic: {{state.user_input.topic}}
      Return concise findings with sources.
```

Use prompt files for long prompts:

```yaml
agents:
  researcher:
    model: claude
    instruction_file: prompts.research_pipeline__researcher
```

Prompt file rules:

- `prompts.research_pipeline__researcher` resolves to `prompts/research_pipeline__researcher.md`.
- The resolver checks the current directory, the YAML directory, and the YAML directory's parent.
- `instruction` and `instruction_file` are mutually exclusive.

Template syntax:

```yaml
instruction: |
  Topic: {{state.user_input.topic}}
  Research result: {{state.researcher}}

  {{#if state.reviewer}}
  Reviewer feedback: {{state.reviewer}}
  {{/if}}
```

## Linear Pipeline Example

```yaml
name: research_pipeline

models:
  claude:
    provider: anthropic
    model: anthropic/claude-sonnet-4-6

tools:
  fetch:
    type: builtin
    name: fetch_url

agents:
  researcher:
    model: claude
    instruction: |
      Research: {{state.user_input.topic}}
      Use available tools when useful.
      Return facts, dates, and source URLs.
    tools: [fetch]

  analyst:
    model: claude
    instruction: |
      Analyze the research:
      {{state.researcher}}

      Return the three most important insights.

  writer:
    model: claude
    instruction: |
      Use these insights:
      {{state.analyst}}

      Write a polished 300-word brief for a general audience.

workflow:
  nodes: [researcher, analyst, writer]
  entry: researcher
  edges:
    - from: researcher
      to: analyst
    - from: analyst
      to: writer
```

## Common Graph Patterns

Sequential:

```yaml
edges:
  - from: researcher
    to: writer
```

Classifier branch:

```yaml
edges:
  - from: classifier
    to: technical_handler
    condition: "technical"
  - from: classifier
    to: general_handler
    condition: default
```

Switch/case:

```yaml
edges:
  - from: classifier
    switch: "{{state.classifier}}"
    cases:
      urgent: urgent_handler
      normal: normal_handler
    default: fallback_handler
```

Parallel fan-out:

```yaml
edges:
  - from: dispatcher
    to: [researcher_a, researcher_b]
    parallel: true
    join: synthesizer
```

For graph behavior beyond a simple pipeline, load `mad-routing`.

## Validation

Run these before handing work back:

```bash
mad list workflows/my_workflow.yaml
mad diagram workflows/my_workflow.yaml
mad run workflows/my_workflow.yaml --dry-run
```

Then run with sample input if credentials and services are available:

```bash
mad run workflows/my_workflow.yaml --input '{"topic": "quantum computing"}'
```

## Common Mistakes

| Mistake | Fix |
|---|---|
| Too many agents for a simple task | Start with one or two nodes, then split only when responsibilities diverge |
| Missing `workflow.entry` | Set it to the first node in `workflow.nodes` |
| Agent not listed in `workflow.nodes` | Add top-level graph agents to `nodes`; do not add sub-agents |
| Long prompt embedded in YAML | Move it to `prompts/` and use `instruction_file` |
| `{{state.user_input.topic}}` but input uses `message` | Align templates with the documented input JSON |
| Conditional branch without strict classifier output | Instruct classifier to output only the route token |
