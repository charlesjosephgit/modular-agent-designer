---
name: mad-routing
description: Guide to conditional edge routing, branching, eval expressions, default fallback, loop config, error routing, parallel/fan-out edges, conditional templates, and validation rules.
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

## Loop Config (Controlled Cycles)

For review/revision loops where a node routes back to a prior node, use `loop:` to set a safety limit and an escape route:

```yaml
agents:
  writer:
    model: smart
    instruction: |
      Write a short paragraph about: {{state.user_input.topic}}
      {{#if state.writer}}
      Improve on the previous draft:
      {{state.writer}}
      {{/if}}

  reviewer:
    model: smart
    instruction: |
      Review this draft and respond with ONLY the word "approved" or "revise":
      {{state.writer}}

  finalizer:
    model: smart
    instruction: |
      Polish and finalize this content for publication:
      {{state.writer}}

workflow:
  nodes: [writer, reviewer, finalizer]
  entry: writer
  edges:
    - from: writer
      to: reviewer

    # Loop back if "revise" — max 3 iterations
    - from: reviewer
      to: writer
      condition: "revise"
      loop:
        max_iterations: 3
        on_exhausted: finalizer    # route here when limit reached

    - from: reviewer
      to: finalizer
      condition: "approved"
```

| Field | Type | Default | Description |
|---|---|---|---|
| `max_iterations` | int | 3 | Maximum number of loop iterations (1–100) |
| `on_exhausted` | string | `null` | Node to route to when the limit is reached. If `null`, the branch terminates with a log warning. |

**How it works internally:**
- The framework tracks iteration counts in state (key: `_loop_<from>_<to>_iter`).
- On each loop iteration, the counter increments.
- When `max_iterations` is reached, the router routes to `on_exhausted` (if set) and resets the counter.
- Edges forming a cycle **must** have a `loop:` config — accidental cycles without one are rejected at load time.

---

## Conditional Templates in Instructions

Use `{{#if state.key}}…{{/if}}` to include instruction content only when a state key exists and is truthy. This is essential for loop patterns where a node's output may not exist on the first iteration:

```yaml
agents:
  writer:
    model: smart
    instruction: |
      Write a short paragraph about: {{state.user_input.topic}}
      {{#if state.reviewer}}
      The reviewer said: {{state.reviewer}}
      Please revise accordingly.
      {{/if}}
```

- If `state.reviewer` is missing or falsy, the entire block is removed — no `StateReferenceError`.
- Conditional blocks are resolved **before** value templates — so `{{state.x}}` refs inside are safe.
- Nesting conditional blocks is not supported.

---

## Agent Retry Config

For transient errors (API timeouts, rate limits), agents can retry before the workflow gives up:

```yaml
agents:
  researcher:
    model: smart
    instruction: "Research this topic: {{state.user_input.topic}}"
    retry:
      max_retries: 3              # 1–10 (default: 3)
      backoff: exponential        # fixed | exponential
      delay_seconds: 1.0          # base delay between retries
```

| Field | Type | Default | Description |
|---|---|---|---|
| `max_retries` | int | 3 | Additional attempts after first failure (1–10) |
| `backoff` | string | `fixed` | `fixed` — constant delay; `exponential` — doubles each attempt |
| `delay_seconds` | float | 1.0 | Base delay in seconds (≥ 0) |

If all retries are exhausted, error info is written to `state._error_<agent_name>` and the workflow can route via `on_error` edges.

---

## Error Routing

Edges with `on_error: true` fire **only** when the source node fails (after all retries). They are mutually exclusive with `condition:`:

```yaml
edges:
  # Normal success path
  - from: researcher
    to: writer

  # Error fallback path
  - from: researcher
    to: error_handler
    on_error: true
```

When a node has both normal and `on_error` edges, the framework injects a unified error router that routes to exactly one path — success or error, never both.

The error info in state (`state._error_<agent_name>`) is a dict:
```json
{
  "error_type": "TimeoutError",
  "error_message": "Request timed out after 30s",
  "attempts": 4
}
```

---

## Parallel / Fan-Out Edges

Send work to multiple nodes concurrently using `to: [list]` with `parallel: true`:

```yaml
edges:
  - from: planner
    to: [researcher_a, researcher_b, researcher_c]
    parallel: true
    join: synthesizer              # wait for all three, then proceed
```

| Field | Type | Default | Description |
|---|---|---|---|
| `to` | `string \| list[string]` | — | Single target or list of fan-out targets |
| `parallel` | bool | `false` | Must be `true` when `to` is a list |
| `join` | string | `null` | Barrier node — proceeds only after all fan-out targets have written output to state |

**Rules:**
- `parallel: true` requires `to` to be a list.
- `join` requires `to` to be a list.
- `loop` is not compatible with fan-out edges.
- Fan-out edges are always unconditional (no `condition:`).
- The join node is auto-generated — it polls state for all source outputs.

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

## Validation Rules

Enforced at YAML load time (not at runtime):

1. **At most one `default` edge per source node.** Multiple defaults → Pydantic error.
2. **Cannot mix unconditional and conditional edges from the same source.** Choose one type per source.
3. **Accidental cycles are rejected.** Any edge forming a cycle must have a `loop:` config. Without it, the loader raises a `ValueError` naming the cycle path.

```yaml
# INVALID — mixing unconditional + conditional from the same source
edges:
  - from: agent_a
    to: agent_b             # unconditional
  - from: agent_a
    to: agent_c
    condition: "tech"       # conditional — Pydantic rejects this combination
```

```yaml
# INVALID — cycle without loop config
edges:
  - from: agent_a
    to: agent_b
  - from: agent_b
    to: agent_a             # accidental cycle — add loop: to make intentional
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
| Cycle without `loop:` config | `ValueError` at load time: "Accidental cycle detected" | Add `loop: { max_iterations: N }` to the edge |
| `on_error: true` with a `condition:` | `ValueError` at load time | Error edges cannot have conditions — remove one |
| `loop:` on a fan-out edge (`to: [list]`) | `ValueError` at load time | Loop is not compatible with parallel fan-out |
| `{{state.x}}` in a loop where `x` doesn't exist on first pass | `StateReferenceError` on the first iteration | Wrap in `{{#if state.x}}…{{/if}}` |
