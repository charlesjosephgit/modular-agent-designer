---
name: mad-create-workflow
description: Step-by-step guide for building a new agent workflow from scratch with modular-agent-designer.
---

# Building a New Workflow from Scratch

## Step 0 — Scaffold a New Agent (recommended starting point)

The fastest way to start is to run the `create` command. It generates a ready-to-run project folder with a sample YAML, a Python entry point, and a `tools/` package:

```bash
modular-agent-designer create <agent_name>
```

This creates `<agent_name>/` with:

```
<agent_name>/
  <agent_name>.yaml          # single-agent Ollama workflow — edit this
  agent.py                   # Python entry point that loads and builds the workflow
  __init__.py                # makes the folder a Python package
  tools/
    __init__.py              # add custom tool functions here (see comments)
  prompts/
    __init__.py              # explains the prompts/ convention
    <agent_name>__responder.md   # starter prompt for the responder agent
  schemas/
    __init__.py              # add Pydantic output schema classes here (see comments)
  README.md                  # per-agent quickstart
```

Run the scaffold immediately to verify your setup:

```bash
ollama serve && ollama pull gemma:e4b
uv run modular-agent-designer run <agent_name>/<agent_name>.yaml \
  --input '{"message": "hello"}'
```

Then open `<agent_name>.yaml` and continue from Step 1 below.

---

## Decision Checklist

Before writing YAML, answer these four questions:

1. **How many agents?** What does each one do? (e.g., classify → route → respond)
2. **Routing type?** Deterministic (use graph edges in YAML) or LLM-driven (use `sub_agents`)?
3. **Which model provider?** Ollama runs locally; Anthropic, Google, OpenAI require API keys.
4. **Tools needed?** HTTP fetch, custom Python function, or external MCP server?

---

## Step 1 — Define Models

Pick a provider, write the alias, and use the correct prefix:

```yaml
models:
  # Ollama (local, no API key needed)
  local:
    provider: ollama
    model: ollama_chat/llama3.2   # use ollama_chat/ when using tools or reasoning

  # Anthropic
  claude:
    provider: anthropic
    model: anthropic/claude-sonnet-4-6

  # Google Gemini
  gemini:
    provider: google
    model: gemini/gemini-2.0-flash

  # OpenAI
  gpt:
    provider: openai
    model: openai/gpt-4o
```

Export the required key before running:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # Anthropic
export GOOGLE_API_KEY=AIza...         # Google
export OPENAI_API_KEY=sk-...          # OpenAI
# Ollama: optional OLLAMA_API_BASE (default: http://localhost:11434)
```

Keys are validated at **build time** — missing keys fail before any LLM call.

---

## Step 2 — Define Tools (if needed)

The quickest tool is the builtin `fetch_url`:

```yaml
tools:
  fetch:
    type: builtin
    name: fetch_url
```

For full coverage of Python functions, MCP stdio/SSE/HTTP, and collision avoidance, load the `mad-tools` skill.

---

## Step 3 — Write Agent Instructions

Instructions are Jinja-like templates resolved at node execution time. Two forms:

**Inline** (short prompts, quick iteration):

```yaml
agents:
  researcher:
    model: claude
    instruction: |
      Research this topic: {{state.user_input.topic}}

      Use the fetch tool to retrieve relevant pages.
      Output a structured summary with key facts.
    tools: [fetch]
```

**Prompt file** (longer prompts; keeps YAML clean):

```yaml
agents:
  researcher:
    model: claude
    instruction_file: prompts.my_workflow__researcher
    tools: [fetch]
```

- `instruction_file` takes a dotted ref: dots → path separators, `.md` appended automatically.
- Resolved from the project root (cwd), so `prompts.my_workflow__researcher` → `<cwd>/prompts/my_workflow__researcher.md`.
- The scaffolder creates `prompts/` with an `__init__.py` and a sample `.md` to start from.
- `instruction` and `instruction_file` are mutually exclusive. Both are optional — omit when the agent uses `static_instruction` alone or receives its prompt via delegation.

**Template syntax (both forms):**

- `{{state.user_input.key}}` — reads from the `--input` JSON argument.
- `{{state.agent_name}}` — reads the output of a prior node.
- Nested refs work: `{{state.user_input.config.mode}}`.
- Missing key → `StateReferenceError` with the exact path and available keys listed. Use this to debug.

**Conditional blocks (for loops):**

Use `{{#if state.key}}…{{/if}}` to include content only when a state key exists and is truthy. Essential for loops where a node re-runs and its previous output may not exist on the first pass:

```yaml
instruction: |
  Write about: {{state.user_input.topic}}
  {{#if state.reviewer}}
  Reviewer feedback: {{state.reviewer}}
  {{/if}}
```

---

## Step 4 — Wire the Workflow Graph

**Linear pipeline** (sequential, unconditional):

```yaml
workflow:
  nodes: [researcher, analyst, writer]
  entry: researcher
  edges:
    - from: researcher
      to: analyst
    - from: analyst
      to: writer
```

**Conditional branch** (classifier routes to specialist):

```yaml
workflow:
  nodes: [classifier, tech_expert, general_help]
  entry: classifier
  edges:
    - from: classifier
      to: tech_expert
      condition: "tech"
    - from: classifier
      to: general_help
      condition: default
```

For full routing coverage (eval expressions, list OR, self-loops), load the `mad-routing` skill.

**Switch/case** (route on a single state value — more concise than N separate condition edges):

```yaml
edges:
  - from: classifier
    switch: "{{state.classifier}}"
    cases:
      urgent: handle_urgent
      normal: handle_normal
    default: handle_other
```

See [`examples/workflows/switch_example.yaml`](../../examples/workflows/switch_example.yaml) for a runnable example.

**Dynamic destination** (an LLM router picks the next node by name at runtime):

```yaml
edges:
  - from: router
    to: "{{state.router}}"
    allowed_targets: [analyst, writer, researcher]
```

See [`examples/workflows/dynamic_router.yaml`](../../examples/workflows/dynamic_router.yaml) for a runnable example.

**Parallel fan-out + join** (dispatch to multiple nodes concurrently, then synthesize):

```yaml
edges:
  - from: dispatcher
    to: [researcher_a, researcher_b, researcher_c]
    parallel: true
    join: synthesizer
```

See [`examples/workflows/parallel_workflow.yaml`](../../examples/workflows/parallel_workflow.yaml) for a runnable example.

**Loop workflow** (writer → reviewer → revise cycle):

```yaml
workflow:
  nodes: [writer, reviewer, finalizer]
  entry: writer
  edges:
    - from: writer
      to: reviewer
    - from: reviewer
      to: writer
      condition: "revise"
      loop:
        max_iterations: 3
        on_exhausted: finalizer
    - from: reviewer
      to: finalizer
      condition: "approved"
```

**Retry + typed error edges** (transient failures with typed fallback routing):

```yaml
agents:
  api_caller:
    retry:
      max_retries: 3
      backoff: exponential
      delay_seconds: 1.0

edges:
  - from: api_caller
    to: success_handler
  - from: api_caller
    to: timeout_handler
    on_error: true
    error_type: TimeoutError
  - from: api_caller
    to: generic_error
    on_error: true
    condition: default
```

See [`examples/workflows/retry_workflow.yaml`](../../examples/workflows/retry_workflow.yaml) and [`examples/workflows/typed_errors.yaml`](../../examples/workflows/typed_errors.yaml).

**Structured-output routing** (Pydantic schema drives edge conditions):

```yaml
agents:
  validator:
    output_schema: examples.schemas.validation.ValidationResult  # Pydantic v2 class

edges:
  - from: validator
    to: accept_handler
    condition:
      eval: "state.get('validator', {}).get('is_valid') == True"
  - from: validator
    to: reject_handler
    condition: default
```

See [`examples/workflows/output_schema_routing.yaml`](../../examples/workflows/output_schema_routing.yaml) + [`examples/schemas/validation.py`](../../examples/schemas/validation.py).

**ADK 2.0 agent overrides** (`generate_content_config`, `parallel_worker`, `output_key`, `static_instruction`):

See [`examples/workflows/agent_overrides.yaml`](../../examples/workflows/agent_overrides.yaml) for all supported fields.

For full loop, error routing, and parallel edge coverage, load the `mad-routing` skill.

---

## Step 5 — Verify Visually

Before running, render the workflow as a Mermaid diagram to catch topology mistakes:

```bash
uv run modular-agent-designer diagram workflows/my_workflow.yaml
```

Paste the output into [mermaid.live](https://mermaid.live) or any GitHub/Markdown renderer. Nodes are rectangles (LLM agents) or hexagons (custom BaseNode). Retry count, mode, and conditional labels are shown on the graph.

---

## Step 7 — Run the Workflow

```bash
uv run modular-agent-designer run workflows/my_workflow.yaml --input '{"topic": "climate change"}'
```

Output is the final session state as pretty-printed JSON:

```json
{
  "user_input": {"topic": "climate change"},
  "researcher": "Key findings: ...",
  "analyst": "Three themes: ...",
  "writer": "Climate change refers to..."
}
```

---

## Complete Example: 3-Node Research Pipeline

```yaml
name: research_pipeline
description: Researcher → Analyst → Writer pipeline.

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
      Research the following topic: {{state.user_input.topic}}
      Use the fetch tool to retrieve relevant information from the web.
      Output a comprehensive summary with key facts, dates, and sources.
    tools: [fetch]

  analyst:
    model: claude
    instruction: |
      You received this research: {{state.researcher}}

      Identify the three most important insights and explain their significance.
      Be concise and factual.

  writer:
    model: claude
    instruction: |
      You have these insights: {{state.analyst}}

      Write a polished, engaging 300-word article for a general audience
      about {{state.user_input.topic}}.

workflow:
  nodes: [researcher, analyst, writer]
  entry: researcher
  edges:
    - from: researcher
      to: analyst
    - from: analyst
      to: writer
```

Run it:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run modular-agent-designer run workflows/research_pipeline.yaml \
  --input '{"topic": "quantum computing"}'
```

---

## Minimal Example: 1-Node Hello World

Use this to verify your model config and env vars before building a larger pipeline:

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
      Write a single friendly sentence about: {{state.user_input.topic}}

workflow:
  nodes: [greeter]
  edges: []
  entry: greeter
```

```bash
uv run modular-agent-designer run examples/workflows/hello_world.yaml --input '{"topic": "the ocean"}'
```

---

## Common Mistakes

| Mistake | What happens | Fix |
|---|---|---|
| Agent not in `workflow.nodes` | Pydantic error at load time: "references unknown node" | Add it to `nodes:` list |
| Model alias doesn't exist in `models:` | Pydantic error at load time | Check the alias spelling |
| `{{state.user_input}}` when input has nested keys | `StateReferenceError` at runtime | Use `{{state.user_input.topic}}` etc. |
| Mixing unconditional + conditional edges from same source | Pydantic error at load time | Use only one type per source node |
| `condition: default` with no other edges from that source | Works but unnecessary | Remove it for a clean unconditional edge |
| Sub-agent listed in `workflow.nodes` | Pydantic error: sub-agents must not be workflow nodes | Remove it from `nodes:` |
| `instruction_file: ../prompts/file.md` (old path style) | `ValueError: not a valid dotted ref` at load time | Use dotted syntax: `instruction_file: prompts.my_workflow__agent` |
| `instruction_file` path not found | `ValueError: instruction_file not found: <path>` | Run CLI from project root; file must be at `<cwd>/prompts/…` |
| Both `instruction:` and `instruction_file:` set | `ValueError: not both` at load time | Remove one; they are mutually exclusive |
