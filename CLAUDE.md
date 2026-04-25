# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Modular Agent Designer is a declarative YAML-to-ADK workflow compiler. Users describe agents, tools, models, and graph topology in YAML; the framework compiles this into an executable Google ADK `Workflow` and runs it. No Python code changes are needed to build new agent pipelines.

## Commands

All commands use `uv`. The `--prerelease=allow` flag is **required** because `google-adk==2.0.0b1` is a beta release.

```bash
# Install
uv sync --prerelease=allow
uv pip install -e ".[dev]" --prerelease=allow      # dev extras (pytest)
uv sync --extra telemetry --prerelease=allow        # MLflow/OTLP tracing

# Run a workflow
uv run modular-agent-designer run <yaml_path> --input '<json>'
uv run modular-agent-designer run workflows/hello_world.yaml --input '{"topic": "AI"}'
uv run modular-agent-designer run <yaml_path> --input '<json>' --mlflow <experiment_id>

# Tests
uv run pytest
uv run pytest -k "not ollama"                       # skip integration tests (needs Ollama daemon)
uv run pytest tests/test_loader.py::test_name -v    # single test

# Lint
flake8 src/
```

## Architecture

**Execution pipeline:**

```
YAML file
  → load_workflow()       config/loader.py        parse + Pydantic validate
  → build_workflow()      workflow/builder.py      compile to ADK Workflow
  → run_workflow_async()  __init__.py              execute + return state dict
```

**Key modules:**
- [src/modular_agent_designer/cli.py](src/modular_agent_designer/cli.py) — Click entry point; wires the pipeline and handles `--mlflow`.
- [config/schema.py](src/modular_agent_designer/config/schema.py) — Pydantic v2 schemas with 26+ validators. All structural rules live here.
- [models/registry.py](src/modular_agent_designer/models/registry.py) — Builds `LiteLlm` instances; reads API keys here (fail-fast at compile time, not call time).
- [tools/registry.py](src/modular_agent_designer/tools/registry.py) — Resolves tools: builtin callables, arbitrary Python functions, and MCP toolsets (stdio / SSE / HTTP).
- [nodes/agent_node.py](src/modular_agent_designer/nodes/agent_node.py) — Wraps each YAML agent as an ADK `@node` async generator.
- [nodes/custom.py](src/modular_agent_designer/nodes/custom.py) — Dynamically imports user-defined `BaseNode` subclasses.
- [state/template.py](src/modular_agent_designer/state/template.py) — Resolves `{{state.dotted.path}}` in agent instructions at node-execution time (not compile time). Missing keys raise `StateReferenceError` with available keys listed.
- [plugins/](src/modular_agent_designer/plugins/) — `DeduplicateToolCallsPlugin`, `make_capture_thinking_callback` (writes reasoning to state).
- [telemetry.py](src/modular_agent_designer/telemetry.py) — Optional MLflow/OTLP tracer; only activates when `--mlflow` is passed.

**Edges:** support exact-match strings, list-based OR, and a `default` fallback. Self-loops and cycles are allowed.

## Gotchas

- **`--prerelease=allow` is mandatory** for all `uv` installs. Omitting it breaks dependency resolution.
- **Model IDs require a provider prefix** (`anthropic/…`, `openai/…`, `gemini/…`, `ollama_chat/…`). Pydantic rejects bare model names.
- **API keys are validated at build time**, not at inference time: `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`. Ollama uses `OLLAMA_API_BASE` (default: `http://localhost:11434`).
- **MCP connections are lazy** — they open on first use and ADK Runner closes them automatically. Don't manage their lifecycle manually.
- **Thinking/reasoning** is configured per-provider under `models.<alias>.thinking`: Anthropic uses `extended-thinking`, OpenAI o-series uses `reasoning_effort`, Gemini 2.5 uses `thinking_budget`.
- **`test_end_to_end_ollama.py`** requires a live Ollama daemon. Run `uv run pytest -k "not ollama"` when it isn't available.

## References

- `README.md` — full user guide: YAML schema, branching/loops, custom nodes, library API, CLI reference.
- `client_run.py` — programmatic (non-CLI) usage example.
- `workflows/` — YAML workflow examples (hello_world, conditional, research_assistant, mcp_example).
