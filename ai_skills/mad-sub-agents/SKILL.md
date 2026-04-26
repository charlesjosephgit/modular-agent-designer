---
name: mad-sub-agents
description: Guide to coordinator/specialist sub-agents, skills usage, output schemas, and custom BaseNode escape hatch.
---

# Sub-Agents, Skills, and Advanced Patterns

## Two Delegation Paradigms

| | Graph edges | `sub_agents` |
|---|---|---|
| **Who decides routing** | YAML topology (you, at design time) | Parent LLM (at runtime) |
| **Best for** | Deterministic pipelines, known sequence | Dynamic delegation, "pick the right specialist" |
| **Defined in** | `workflow.edges` | `agents.<parent>.sub_agents` |
| **Appear in `workflow.nodes`** | Yes | **No** |

---

## How Sub-Agents Work

Sub-agents are built as ADK `Agent` instances and passed to the parent via `Agent(sub_agents=[...])`. ADK automatically wires them as callable tools the parent LLM can invoke. The parent decides at runtime which specialist(s) to call.

```yaml
agents:
  # Specialists — NOT in workflow.nodes
  search_specialist:
    model: fast
    instruction: "Search for factual information on the given topic. Return key facts."
    mode: single_turn      # wrapped as a callable tool the coordinator can invoke

  analysis_specialist:
    model: fast
    instruction: "Identify the three most important themes from the provided findings."
    mode: single_turn

  # Coordinator — the only workflow node
  coordinator:
    model: smart
    mode: task
    instruction: |
      Research coordinator for: {{state.user_input.topic}}

      You have two specialists:
        - search_specialist: finds factual information
        - analysis_specialist: identifies themes

      1. Delegate search to search_specialist
      2. Pass findings to analysis_specialist
      3. Write a 200-word final brief synthesizing both outputs
    sub_agents:
      - search_specialist
      - analysis_specialist

workflow:
  nodes: [coordinator]   # only the parent is a workflow node
  edges: []
  entry: coordinator
```

---

## The `mode` Field

| Mode | Behavior | Best for |
|---|---|---|
| `single_turn` | Wrapped as a callable tool; invoked once, returns immediately | Specialist tasks — most common for sub-agents |
| `chat` | Reachable via `transfer_to_agent`; supports back-and-forth | Dialogue, complex multi-turn interaction |
| `task` | Background task semantics | Long-running or async work |
| `null` (omitted) | Parent has no explicit exposure mode set | Default for top-level workflow nodes |

---

## Critical: Template Limitation in Sub-Agents

**Sub-agent instructions do NOT support `{{state.x}}` templates.** Only workflow node instructions are resolved by the framework at execution time.

```yaml
# WRONG — this template is NOT resolved; the LLM sees the literal string
search_specialist:
  instruction: "Search for {{state.user_input.topic}}"   # {{...}} passed as-is to the LLM

# CORRECT — put the template on the parent coordinator instead
coordinator:
  instruction: |
    Research coordinator for: {{state.user_input.topic}}
    Delegate to search_specialist. Give it the topic above.
```

---

## Constraints and Rules

- Sub-agents **must not** appear in `workflow.nodes` — Pydantic rejects it at load time.
- Sub-agent names must reference agents defined in the `agents:` dict.
- Circular references (A → B → A) are rejected at load time.
- Nested sub-agents are supported: a sub-agent can itself have sub-agents. Build order is resolved automatically.
- `disallow_transfer_to_parent: true` — prevents the sub-agent from transferring control back to the parent.
- `disallow_transfer_to_peers: true` — prevents the sub-agent from transferring to sibling agents.

---

## Skills Usage

Skills are ADK `SkillToolset` instances that use progressive disclosure: the agent sees the skill's name and description at startup, and only loads the full instructions when it calls `load_skill`.

### Define in YAML

```yaml
skills:
  # Builtin skill shipped with the framework
  summarizer:
    ref: modular_agent_designer.skills.summarize-text

  # Local skill (in project's skills/ directory)
  my_skill:
    ref: skills.my-custom-skill
```

The `ref` format is `<python_package_path>.<skill-directory-name>`. The framework splits on the last `.`, imports the package, and loads `<package_dir>/<skill-dir>/SKILL.md`.

### Reference in an Agent

```yaml
agents:
  researcher:
    model: smart
    mode: task
    skills: [summarizer]
    instruction: |
      You have access to skills. Use them:
      1. Call `list_skills` to see available skills.
      2. Call `load_skill` with the skill name to get instructions.
      3. Follow the skill's instructions for your task.

      Topic: {{state.user_input.topic}}
```

### Create a Local Skill

```
skills/
  __init__.py
  my-custom-skill/
    SKILL.md
```

```markdown
---
name: my-custom-skill
description: One-line description shown to the agent at startup.
---

Full instructional content here. The agent loads this on demand.
```

---

## Output Schema

Use `output_schema` to enforce structured JSON output from an agent:

```yaml
agents:
  extractor:
    model: smart
    instruction: |
      Extract the product name, price, and category from:
      {{state.user_input.text}}
    output_schema: mypackage.models.Product
```

```python
# mypackage/models.py  (or schemas/product.py inside a scaffolded agent folder)
from pydantic import BaseModel

class Product(BaseModel):
    name: str
    price: float
    category: str
```

The `schemas/` folder generated by `modular-agent-designer create` is the recommended home for these classes. Its `__init__.py` contains a worked example. Wire via dotted path:

```yaml
output_schema: my_agent.schemas.product.Product
```

- ADK enforces the schema on the agent's output.
- The result is written to `state[agent_name]` as a JSON string.
- Downstream agents receive it stringified: `{{state.extractor}}` gives the JSON.

---

## Custom BaseNode Escape Hatch

For non-LLM logic (deterministic routing, data transformation, side effects), use `type: node`:

```yaml
agents:
  my_router:
    type: node
    ref: mypackage.nodes.RouterNode   # BaseNode subclass or @node function
```

```python
# mypackage/nodes.py
from google.adk.workflow import BaseNode
from google.adk import Context, Event

class RouterNode(BaseNode):
    async def run(self, ctx: Context, node_input):
        data = ctx.state.to_dict()
        if "keyword" in str(node_input):
            yield Event(route="path_a")
        else:
            yield Event(route="path_b")
```

- Must be a `BaseNode` subclass or `@node`-decorated async generator.
- Manages its own state writes via `ctx.state`.
- No `output_key` is provided automatically — write to state explicitly if downstream agents need it.
- Good for: deterministic routers, external API side effects, pure computation without an LLM.

---

## Common Mistakes

| Mistake | What happens | Fix |
|---|---|---|
| Sub-agent listed in `workflow.nodes` | Pydantic error: sub-agents must not be workflow nodes | Remove it from `nodes:` |
| `{{state.x}}` in a sub-agent instruction | Template not resolved; LLM sees literal `{{state.x}}` | Put the template on the parent's instruction |
| Sub-agent missing from `sub_agents:` on parent | Specialists are built but parent can't invoke them | Add the alias to parent's `sub_agents:` list |
| `mode: single_turn` omitted on specialist | Parent LLM may not invoke it as a tool correctly | Set `mode: single_turn` on specialists |
| `output_schema` class not Pydantic v2 | ADK schema enforcement fails at runtime | Use `pydantic.BaseModel` subclass |
