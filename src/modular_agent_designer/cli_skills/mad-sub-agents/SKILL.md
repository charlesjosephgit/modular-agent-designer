---
name: mad-sub-agents
description: Use when a coding agent should add or debug MAD coordinator sub-agents, runtime skills, structured output schemas, A2A agents, or custom BaseNode implementations.
---

# Sub-Agents, Runtime Skills, Schemas, A2A, and Custom Nodes

This skill covers advanced agent composition beyond a simple graph pipeline.

## Use This When

- A parent agent should choose or call specialists at runtime.
- The workflow needs runtime ADK skills loaded through the YAML `skills:` block.
- An agent should produce structured output with a Pydantic schema.
- The workflow needs a remote Agent2Agent protocol agent.
- Deterministic non-LLM logic needs a custom `BaseNode`.

Load `mad-routing` when the question is graph topology, and `mad-tools` when the question is callable tools.

## Agent Workflow

1. Inspect current `agents:`, `workflow.nodes`, schemas, runtime `skills/`, and any custom node modules.
2. Decide whether the behavior belongs in graph edges, sub-agent delegation, a runtime skill, a tool, a schema, an A2A agent, or a custom node.
3. Keep graph nodes and sub-agents distinct: sub-agents are declared under `agents:` but must not appear in `workflow.nodes`.
4. Add clear `description` values for specialists so the parent LLM knows when to call them.
5. Validate with `mad list`, `mad diagram`, and `mad run --dry-run`.

## Pattern Decision Table

| Need | Use |
|---|---|
| Fixed sequence or deterministic branch | Graph edges; load `mad-routing` |
| Parent LLM chooses specialists | `sub_agents` |
| Reusable instructions loaded on demand | Runtime `skills:` |
| Deterministic callable capability | Tool; load `mad-tools` |
| Structured JSON-like model output | `output_schema` |
| Remote A2A-compatible agent | `type: a2a` |
| Non-LLM stateful workflow logic | `type: node` custom `BaseNode` |

## Sub-Agents

Sub-agents are ADK `Agent` instances passed to a parent agent. The parent chooses when to invoke them.

```yaml
agents:
  search_specialist:
    model: fast
    description: "Finds factual information and source URLs."
    mode: single_turn
    instruction: |
      Search for factual information. Return concise findings with sources.

  analysis_specialist:
    model: fast
    description: "Extracts themes and implications from research findings."
    mode: single_turn
    instruction: |
      Identify the three most important themes from the provided findings.

  coordinator:
    model: smart
    mode: task
    instruction: |
      Research coordinator for: {{state.user_input.topic}}

      Use search_specialist for source gathering.
      Use analysis_specialist for theme extraction.
      Return a concise final brief.
    sub_agents:
      - search_specialist
      - analysis_specialist

workflow:
  nodes: [coordinator]
  entry: coordinator
  edges: []
```

Rules:

- Specialists are declared in `agents:` but omitted from `workflow.nodes`.
- Use `mode: single_turn` for callable specialist tasks.
- Add `description` to specialists; parent models use it to choose delegation.
- Circular sub-agent references are rejected.
- Nested sub-agents are supported when needed.

## Template Limitation

Sub-agent instructions are not resolved through MAD's state template engine. A sub-agent sees `{{state.x}}` literally.

Wrong:

```yaml
search_specialist:
  instruction: "Research {{state.user_input.topic}}"
```

Right:

```yaml
coordinator:
  instruction: |
    Topic: {{state.user_input.topic}}
    Ask search_specialist to research the topic above.
```

## Transfer Controls

```yaml
agents:
  specialist:
    model: fast
    mode: single_turn
    disallow_transfer_to_parent: true
    disallow_transfer_to_peers: true
```

Use these flags when a specialist should only complete its assigned call and not transfer control.

## Runtime Skills

Runtime skills are ADK `SkillToolset` entries. They are different from this `cli_skills/` package.

```yaml
skills:
  summarizer:
    ref: modular_agent_designer.skills.summarize-text

  local_policy:
    ref: skills.policy-review

agents:
  reviewer:
    model: smart
    skills: [summarizer, local_policy]
    instruction: |
      You have runtime skills available.
      Call list_skills, load the relevant skill, and follow its instructions.

      Review: {{state.user_input.text}}
```

The ref format is `<python_package_path>.<skill-directory-name>`. MAD imports the package path and loads `<package_dir>/<skill-directory-name>/SKILL.md`.

Local skill layout:

```text
skills/
  __init__.py
  policy-review/
    SKILL.md
```

Skill front matter:

```markdown
---
name: policy-review
description: Reviews text for policy and compliance issues.
---

Instructions for the runtime agent.
```

## Structured Output Schemas

Use `output_schema` when downstream routing or prompts need reliable fields.

```yaml
agents:
  extractor:
    model: smart
    output_schema: my_agent.schemas.product.Product
    instruction: |
      Extract product data from:
      {{state.user_input.text}}
```

Example schema:

```python
from pydantic import BaseModel

class Product(BaseModel):
    name: str
    price: float
    category: str
```

In scaffolded projects, put schemas under `schemas/` and reference them by dotted path, such as:

```yaml
output_schema: my_agent.schemas.product.Product
```

Downstream agents receive the structured result through state:

```yaml
instruction: |
  Product JSON:
  {{state.extractor}}
```

Use `output_key` to rename the state key:

```yaml
agents:
  extractor:
    model: smart
    output_key: product
    output_schema: my_agent.schemas.product.Product
```

## A2A Remote Agents

Use `type: a2a` for remote Agent2Agent protocol agents.

```yaml
agents:
  remote_researcher:
    type: a2a
    agent_card: https://remote.example.com/.well-known/agent.json
    description: "Remote research specialist."
    output_key: remote_result
    timeout_seconds: 600

workflow:
  nodes: [remote_researcher]
  entry: remote_researcher
  edges: []
```

A2A agents can also be sub-agents:

```yaml
agents:
  remote_specialist:
    type: a2a
    agent_card: ${REMOTE_AGENT_CARD}
    description: "Remote specialist for detailed analysis."

  coordinator:
    model: smart
    instruction: |
      Delegate detailed analysis when useful.
    sub_agents: [remote_specialist]
```

Install optional A2A dependencies when needed:

```bash
pip install "modular-agent-designer[a2a]"
```

## Custom BaseNode

Use `type: node` for deterministic workflow logic that should run as a node rather than as an LLM tool.

```yaml
agents:
  router_node:
    type: node
    ref: my_agent.nodes.RouterNode
    config:
      threshold: 0.8

workflow:
  nodes: [router_node, high_handler, low_handler]
  entry: router_node
  edges:
    - from: router_node
      to: high_handler
      condition: "high"
    - from: router_node
      to: low_handler
      condition: default
```

Example node:

```python
from google.adk import Context, Event
from google.adk.workflow import BaseNode

class RouterNode(BaseNode):
    def __init__(self, name: str, threshold: float = 0.5):
        super().__init__(name=name)
        self.threshold = threshold

    async def run(self, ctx: Context, node_input):
        score = ctx.state.to_dict().get("score", 0)
        if score >= self.threshold:
            yield Event(route="high")
        else:
            yield Event(route="low")
```

Notes:

- `config:` is passed as keyword arguments to the node constructor.
- Custom nodes manage their own state writes.
- Use custom nodes for deterministic workflow behavior; use tools for capabilities an LLM should call.

## Validation

```bash
mad list workflows/my_workflow.yaml
mad diagram workflows/my_workflow.yaml
mad run workflows/my_workflow.yaml --dry-run
```

`mad list` is especially useful because it marks graph nodes and sub-agents separately.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Sub-agent appears in `workflow.nodes` | Remove it from graph nodes |
| Specialist has no `description` | Add one so the parent can delegate correctly |
| Sub-agent uses `{{state...}}` templates | Put state context in the parent instruction |
| Runtime skill ref points at the skill dir directly | Use `<package>.<skill-dir>` dotted ref |
| Local `skills/` or `schemas/` lacks `__init__.py` | Add package markers |
| `output_schema` is not a Pydantic v2 `BaseModel` | Use a Pydantic model class |
| Custom node expects `output_key` behavior | Write needed state inside the node |
| A2A dependency missing | Install the `a2a` extra |
