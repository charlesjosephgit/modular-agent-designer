---
name: mad-routing
description: Use when a coding agent should implement or debug modular-agent-designer routing, including conditions, switch/case, dynamic destinations, loops, retries, error routes, and parallel fan-out.
---

# Routing in MAD Workflows

Routing determines which workflow node runs next. Use graph edges for deterministic topology and `sub_agents` only when a parent LLM should choose specialists dynamically.

## Use This When

- The workflow needs branches, classifier routing, workflow-level default routes, switch/case, or dynamic destinations.
- The workflow needs a review loop, retry policy, typed error fallback, or parallel fan-out.
- A graph validation error mentions mixed edge types, unknown nodes, default routes, or cycles.

Load `mad-create-workflow` for initial workflow design and `mad-sub-agents` for coordinator/specialist delegation.

## Agent Workflow

1. Inspect the current `workflow.nodes`, `workflow.entry`, and all edges from the node you plan to change.
2. Decide whether the route should be deterministic YAML topology or LLM-driven delegation.
3. Keep each source node to one routing style: unconditional, conditional, error routing, switch, dynamic, or parallel.
4. Add strict output instructions to classifier/router agents.
5. Validate with `mad list`, render with `mad diagram`, then build with `mad run --dry-run`.

## Routing Decision Table

| Need | Use |
|---|---|
| Always go to the next node | Unconditional `to:` |
| Match a small set of exact labels | `condition: "label"` plus `condition: default` |
| Match several labels to one node | `condition: [a, b, c]` |
| Check state or regex | `condition: {eval: "..."}` |
| Apply one fallback condition to many nodes | `workflow.default_routes` |
| Route on one state value | `switch:` with `cases:` |
| Let a router output the next node name | Dynamic `to: "{{state.router}}"` with `allowed_targets` |
| Review/revision cycle | Back edge with `loop:` |
| Recover from node failure | `on_error: true` edges |
| Run independent branches together | `parallel: true` with `join:` |

## Unconditional Edges

```yaml
edges:
  - from: researcher
    to: writer
```

Use for linear pipelines. Do not mix unconditional and conditional edges from the same source node.

## Exact Conditions

```yaml
agents:
  classifier:
    model: fast
    instruction: |
      Classify the request as exactly one of: technical, billing, general.
      Output only the single label. No punctuation.

workflow:
  nodes: [classifier, technical_handler, billing_handler, general_handler]
  entry: classifier
  edges:
    - from: classifier
      to: technical_handler
      condition: "technical"
    - from: classifier
      to: billing_handler
      condition: "billing"
    - from: classifier
      to: general_handler
      condition: default
```

The source output is stripped and compared exactly.

## List OR Conditions

```yaml
edges:
  - from: classifier
    to: sales_handler
    condition: ["sales", "quote", "pricing"]
  - from: classifier
    to: general_handler
    condition: default
```

## Eval Conditions

```yaml
edges:
  - from: scorer
    to: vip_handler
    condition:
      eval: "state.get('user_input', {}).get('is_vip') == True"

  - from: classifier
    to: urgent_handler
    condition:
      eval: "bool(re.search(r'urgent|asap', input, re.IGNORECASE))"

  - from: tool_caller
    to: failure_handler
    condition:
      eval: "output.agent_status == 'fail'"
```

Available eval names:

| Name | Value |
|---|---|
| `state` | Full session state dict; supports dot access and `.get(...)` |
| `input` | Source output coerced to stripped string |
| `output` | Raw source output, including structured output fields |
| `raw_input` | Raw source output, retained for compatibility |
| `re` | Python regex module |

Use `state.get(...)` rather than `state[...]` when missing keys should evaluate
cleanly to `False`. Use `output.<field>` when routing immediately on the source
node's structured output.

## Default Route

```yaml
edges:
  - from: classifier
    to: fallback
    condition: default
```

Rules:

- Only one default edge per source.
- It is evaluated after all non-default conditions.
- Do not use it as the only edge from a source unless you really mean "always route here"; an unconditional edge is clearer.

## Workflow-Level Default Routes

Use `workflow.default_routes` when many source nodes should share one
conditional fallback, such as routing any structured tool-call failure to a
single handler.

```yaml
agents:
  tool_caller:
    model: smart
    output_schema: examples.schemas.tool_status.ToolCallStatus
    tools: [explode]
    instruction: |
      Call the tool and return agent_status as success or fail.

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

| Field | Notes |
|---|---|
| `to` | Required fallback target node |
| `condition` | Required condition using the same forms as normal edges |
| `from` | Optional source-node allowlist |
| `exclude` | Optional source-node blocklist |

Default routes are injected at build time. They skip self-routes to the target
and are not injected for sources that already have an unconditional normal edge
or an explicit `condition: default` edge. `mad list` prints the configured
`default_routes`; `mad diagram` renders their injected fallback edges as dotted
edges.

## Switch / Case

Use `switch:` when many routes depend on one value:

```yaml
edges:
  - from: classifier
    switch: "{{state.classifier}}"
    cases:
      urgent: urgent_handler
      normal: normal_handler
      low: low_priority_handler
    default: fallback
```

Eval switch:

```yaml
edges:
  - from: scorer
    switch:
      eval: "state.get('scorer', {}).get('label', '')"
    cases:
      pass: finalizer
      fail: reviser
```

`cases:` targets must be known workflow nodes.

## Dynamic Destinations

Use dynamic `to:` when an LLM router outputs a node name:

```yaml
agents:
  router:
    model: smart
    instruction: |
      Choose the next node: analyst, writer, or researcher.
      Output only the node name.

workflow:
  nodes: [router, analyst, writer, researcher]
  entry: router
  edges:
    - from: router
      to: "{{state.router}}"
      allowed_targets: [analyst, writer, researcher]
```

Always prefer `allowed_targets`; it constrains the graph and catches unknown names earlier. Dynamic destinations are not compatible with `loop:`.

## Loops

Any edge that forms a cycle must declare `loop:`.

```yaml
agents:
  writer:
    model: smart
    instruction: |
      Draft content for: {{state.user_input.topic}}
      {{#if state.reviewer}}
      Revise using this feedback: {{state.reviewer}}
      {{/if}}

  reviewer:
    model: smart
    instruction: |
      Review the draft and output only "approved" or "revise":
      {{state.writer}}

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

Use `{{#if state.key}}...{{/if}}` in looping prompts so the first iteration does not reference missing state.

## Retries

Retries belong on agents, not edges:

```yaml
agents:
  api_caller:
    model: smart
    instruction: "Call the required API for: {{state.user_input.request}}"
    retry:
      max_retries: 3
      backoff: exponential
      delay_seconds: 1.0
```

After retries are exhausted, error details are written to state and `on_error`
routes can fire. If no matching `on_error` route exists, the workflow stops and
MAD surfaces a final agent-failure message instead of continuing along normal
edges.

## Error Routing

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

Use `error_type` for exception class names and `error_match` for regex matching on error messages.

## Parallel Fan-Out and Join

```yaml
edges:
  - from: dispatcher
    to: [researcher_a, researcher_b, researcher_c]
    parallel: true
    join: synthesizer
```

The join node runs after all fan-out targets complete. Use this for independent work that can run concurrently, not for ordered dependencies.

## Structured-Output Routing

When an agent uses `output_schema`, route with eval against the stored value:

```yaml
agents:
  validator:
    model: smart
    output_schema: examples.schemas.validation.ValidationResult
    instruction: |
      Validate: {{state.user_input.payload}}

edges:
  - from: validator
    to: accept_handler
    condition:
      eval: "state.get('validator', {}).get('is_valid') == True"
  - from: validator
    to: reject_handler
    condition: default
```

For the immediate source node output, prefer `output.<field>`:

```yaml
edges:
  - from: validator
    to: reject_handler
    condition:
      eval: "output.is_valid == False"
```

Use `state.<node>` when reading an earlier node's committed result from another
source node.

## Validation

```bash
mad list workflows/my_workflow.yaml
mad diagram workflows/my_workflow.yaml
mad run workflows/my_workflow.yaml --dry-run
```

Inspect the diagram for accidental missing joins, wrong branch names, or cycles.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Conditional classifier returns prose | Prompt it to output only the exact label |
| Source has unconditional and conditional edges | Use one style for that source |
| Multiple default routes from one source | Keep one fallback |
| Shared failure route repeated on many nodes | Use `workflow.default_routes` |
| Cycle without `loop:` | Add `loop.max_iterations` and optional `on_exhausted` |
| Dynamic route without constraints | Add `allowed_targets` |
| Parallel edge uses a string target | Use `to: [node_a, node_b]` |
| Error route uses `condition` without `on_error: true` | Add `on_error: true` for failure paths |
