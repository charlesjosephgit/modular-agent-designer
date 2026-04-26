---
name: mad-routing
description: Guide to conditional edge routing, branching, eval expressions, default fallback, self-loops, and validation rules.
---

# Conditional Routing Reference

## How It Works Internally

For each node that has conditional outgoing edges, the framework injects an invisible router node after it. The router evaluates all conditions against the source node's output (coerced to a stripped string) and emits an `Event(route=...)` to trigger the matching destination edge. This is transparent to YAML authors — you only write edges.

---

## Condition Type 1: No Condition (Sequential / Unconditional)

```yaml
edges:
  - from: researcher
    to: writer          # always follows — no condition field
```

Use for linear pipelines. **Cannot be mixed** with conditional edges from the same source node.

---

## Condition Type 2: Exact String Match

```yaml
edges:
  - from: classifier
    to: tech_expert
    condition: "tech"     # matches if output (stripped) == "tech"

  - from: classifier
    to: creative_expert
    condition: "creative"
```

The source node's output is stripped of whitespace and compared exactly. The agent's instruction **must enforce clean single-word output**:

```yaml
agents:
  classifier:
    model: fast
    instruction: |
      Classify the request into one of: tech, creative, billing.
      Output ONLY the single word. No punctuation, no explanation.
```

---

## Condition Type 3: List (OR Logic)

```yaml
edges:
  - from: classifier
    to: business_expert
    condition: ["billing", "sales", "invoice"]   # matches any value in the list
```

Useful for consolidating multiple classifier outputs to one handler.

---

## Condition Type 4: Eval Expression

```yaml
edges:
  - from: classifier
    to: vip_handler
    condition:
      eval: "state.get('user_input', {}).get('is_vip') == True"

  - from: classifier
    to: large_order_handler
    condition:
      eval: "len(state.get('items', [])) > 10"

  - from: classifier
    to: urgent_handler
    condition:
      eval: "bool(re.search(r'urgent|asap', input, re.IGNORECASE))"
```

**Variables available inside `eval`:**

| Variable | Value |
|---|---|
| `state` | Full session state dict (`ctx.state.to_dict()`) |
| `input` | Source node output coerced to a stripped string |
| `raw_input` | Raw output value (dict, list, Pydantic model, etc.) |
| `re` | Python `re` module for regex |

**Safe builtins available:** `len`, `int`, `float`, `str`, `bool`, `abs`, `min`, `max`, `any`, `all`, `isinstance`, `sorted`, `sum`, `range`, `list`, `dict`, `set`, `tuple`, `enumerate`, `zip`, `reversed`, `round`.

**Error handling:**
- `KeyError`, `AttributeError`, `IndexError`, `TypeError` → treated as `False`, WARNING logged. Always use `state.get('key', default)` instead of `state['key']`.
- `NameError`, `SyntaxError` → propagate immediately (fail loudly — broken expressions don't silently skip).

---

## Condition Type 5: Default (Catch-All)

```yaml
edges:
  - from: classifier
    to: general_help
    condition: default   # fires if no other condition matched
```

- At most **one** `default` edge allowed per source node — Pydantic enforces this.
- Always evaluated last regardless of declaration order in the YAML.

---

## Complete Branching Example

```yaml
name: complex_routing

models:
  fast:
    provider: ollama
    model: ollama/llama3.2

agents:
  classifier:
    model: fast
    instruction: |
      Classify the user request into one of: tech, creative, billing, sales, or other.
      Output ONLY the single word.

  vip_handler:
    model: fast
    instruction: "Premium concierge for VIP: {{state.user_input.text}}"

  tech_expert:
    model: fast
    instruction: "Technical support for: {{state.user_input.text}}"

  creative_expert:
    model: fast
    instruction: "Creative writing help for: {{state.user_input.text}}"

  business_expert:
    model: fast
    instruction: "Business/sales support for: {{state.user_input.text}}"

  general_help:
    model: fast
    instruction: "General assistance for: {{state.user_input.text}}"

workflow:
  nodes: [classifier, vip_handler, tech_expert, creative_expert, business_expert, general_help]
  entry: classifier
  edges:
    # Eval condition — checked first; bypasses classifier output for VIP users
    - from: classifier
      to: vip_handler
      condition:
        eval: "state.get('user_input', {}).get('is_vip') == True"

    # Exact string match
    - from: classifier
      to: tech_expert
      condition: "tech"

    - from: classifier
      to: creative_expert
      condition: "creative"

    # List OR
    - from: classifier
      to: business_expert
      condition: ["billing", "sales"]

    # Default fallback
    - from: classifier
      to: general_help
      condition: default
```

Run with a VIP user:
```bash
uv run modular-agent-designer run workflows/routing.yaml \
  --input '{"text": "I need help", "is_vip": true}'
```

---

## Self-Loops (Retry Pattern)

A node can route back to itself if its output doesn't meet a quality bar:

```yaml
agents:
  validator:
    model: smart
    instruction: |
      Validate the user's JSON input: {{state.user_input.data}}
      If valid, output "ok". If invalid, output "retry" with a brief reason.

workflow:
  nodes: [validator, processor]
  entry: validator
  edges:
    - from: validator
      to: validator
      condition: "retry"    # loops back to itself

    - from: validator
      to: processor
      condition: "ok"
```

`max_llm_calls: 20` (default) is the global circuit breaker — the workflow stops if it's exceeded.

---

## Validation Rules

Enforced at YAML load time (not at runtime):

1. **At most one `default` edge per source node.** Multiple defaults → Pydantic error.
2. **Cannot mix unconditional and conditional edges from the same source.** Choose one type per source.

```yaml
# INVALID — mixing unconditional + conditional from the same source
edges:
  - from: agent_a
    to: agent_b             # unconditional
  - from: agent_a
    to: agent_c
    condition: "tech"       # conditional — Pydantic rejects this combination
```

---

## Common Mistakes

| Mistake | What happens | Fix |
|---|---|---|
| Agent instruction outputs extra text (`"tech: here's why..."`) | String match fails; hits `default` or hangs | Instruct the agent to output ONLY the routing word |
| `condition: "True"` thinking it always fires | Matches only if output is literally the string `"True"` | Use `condition: {eval: "True"}` or remove the condition |
| `state["key"]` in eval with a missing key | `KeyError` → silently treated as `False` + WARNING | Use `state.get('key', {})` |
| Assuming `default` is checked in YAML order | It is always last regardless of order | This is correct — `default` is always the final fallback |
| Two `default` edges from the same source | Pydantic error at load time | Keep exactly one `default` per source |
