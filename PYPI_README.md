# Modular Agent Designer

**Declarative YAML → Google ADK workflow compiler.** Define agents, tools, models, and graph topology in a single YAML file — no Python code required.

```bash
pip install modular-agent-designer
```

> **Note:** `google-adk` is currently in beta — install with `--prerelease=allow` when using `uv`.

---

## Quickstart

```bash
# Scaffold a new agent project
uv run modular-agent-designer create my_agent

# Run it
uv run modular-agent-designer run my_agent/my_agent.yaml --input '{"message": "hello"}'
```

---

## What a workflow looks like

```yaml
name: research_assistant

models:
  sonnet:
    provider: anthropic
    model: anthropic/claude-sonnet-4-6

tools:
  web:
    type: builtin
    name: fetch_url

agents:
  researcher:
    model: sonnet
    tools: [web]
    instruction: "Research {{state.topic}} and summarize your findings."

  writer:
    model: sonnet
    instruction: "Write a short article based on: {{state.researcher}}"

workflow:
  nodes: [researcher, writer]
  edges:
    - from: researcher
      to: writer
```

```bash
uv run modular-agent-designer run research.yaml --input '{"topic": "quantum computing"}'
```

---

## Key features

| Feature | Details |
|---|---|
| **Multi-provider models** | Anthropic, Google Gemini, OpenAI, Ollama — all via LiteLLM |
| **Tools** | Builtin callables, arbitrary Python functions, MCP servers (stdio / SSE / HTTP) |
| **Routing** | Conditional edges, `default` fallback, self-loops, parallel fan-out with join barriers |
| **State templating** | `{{state.key}}` in prompts resolved at runtime |
| **Structured output** | Per-agent Pydantic `output_schema` |
| **Thinking/reasoning** | Anthropic extended-thinking, OpenAI reasoning effort, Gemini thinking budget |
| **Retries** | Per-agent fixed or exponential backoff |
| **Observability** | Optional MLflow / OTLP tracing via `--mlflow` |
| **Escape hatch** | Drop in custom `BaseNode` subclasses for non-LLM logic |

---

## Supported model providers

```yaml
model: anthropic/claude-sonnet-4-6    # Anthropic — ANTHROPIC_API_KEY
model: gemini/gemini-2.5-pro          # Google    — GOOGLE_API_KEY
model: openai/gpt-4o                  # OpenAI    — OPENAI_API_KEY
model: ollama_chat/gemma3             # Ollama    — OLLAMA_API_BASE (default: localhost:11434)
```

---

## Links

- [GitHub](https://github.com/charlesjosephgit/modular-agent-designer)
- [Full YAML reference & docs](https://github.com/charlesjosephgit/modular-agent-designer#readme)
