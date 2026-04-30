# Gemini Project Context: Modular Agent Designer

A modular framework for designing and orchestrating complex agentic workflows with ease. This project allows defining complex agentic workflows (nodes, edges, agents, tools, and model configurations) through a modular, intuitive architecture without modifying Python code.

## Project Overview

- **Core Technology**: Built on `google-adk[extensions]==2.0.0b1`.
- **Model Abstraction**: Uses `LiteLlm` to support multiple providers (Ollama, Anthropic, Google Gemini, OpenAI).
- **Workflow Engine**: Supports complex graph topologies including conditional branching and loops (cycles). Each node can be an LLM-powered agent or a custom Python node.
- **State Management**: Automatically tracks node outputs in a shared session state. Uses custom `{{state.path}}` templating for instruction injection at runtime.
- **CLI-First**: Primary interaction is via the `modular-agent-designer` CLI tool.

## Directory Structure

- `src/modular_agent_designer/`: Core package containing the framework logic.
    - `cli.py`: Entry point for the `modular-agent-designer` command and `run_workflow_async` runner.
    - `config/`: YAML loading and Pydantic-based schema validation (`schema.py`).
    - `models/`: Registry and configuration for LLM providers.
    - `nodes/`: Implementation of `AgentNode` (LLM agents) and `CustomNode` wrappers.
    - `state/`: Logic for state templating (`template.py`) and state event creation (`writer.py`).
    - `skills/`: Registry and built-in skills leveraging ADK's `SkillToolset`.
    - `tools/`: Registry for built-in and custom tools.
    - `workflow/`: Logic for building the ADK `Workflow` from YAML config (`builder.py`).
- `skills/`: Example local skill directories (importable via dotted ref).
- `ai_skills/`: Task-specific instructional skills for AI coding assistants (Gemini CLI, Claude Code).
- `tests/`: Comprehensive test suite using `pytest`.
- `workflows/`: Example YAML workflow definitions.

## Building and Running

### Prerequisites
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Python 3.11+

### Setup
```bash
# Install dependencies
uv sync --prerelease=allow
uv sync --extra telemetry --prerelease=allow

# Install in editable mode with dev dependencies
uv pip install -e ".[dev]" --prerelease=allow
```

### Execution
Run a workflow by providing a path to a YAML file and an initial JSON input:
```bash
uv run modular-agent-designer run workflows/hello_world.yaml --input '{"topic": "tide pools"}'
```

### Testing
```bash
# Run all tests
uv run pytest

# Run unit tests only (skip end-to-end Ollama tests)
uv run pytest -k "not ollama"
```

## Development Conventions

### YAML Schema
Workflows are defined with six main sections: `name`, `models`, `tools`, `skills`, `agents`, and `workflow`.
- **Agents**: Default to `type: agent`.
- **Custom Nodes**: Use `type: node` and provide a `ref` to a dotted Python path (BaseNode subclass or @node function).
- **Tools**: Supported types are `builtin`, `python`, `mcp_stdio`, `mcp_sse`, and `mcp_http`. MCP toolsets are wired via ADK's `McpToolset` and passed whole into `Agent(tools=[...])`. Connections open lazily and ADK's Runner handles teardown automatically. Header/env values may contain `${VAR}` placeholders expanded from the process environment at load time; missing variables fail fast.
- **Skills**: Defined in the root-level `skills:` section with a dotted `ref` pointing to a Python package containing a skill directory (e.g., `modular_agent_designer.skills.summarize-text`). Agents reference skills by alias name. Skills leverage ADK's native `SkillToolset` for progressive disclosure (L1 metadata → L2 instructions → L3 resources). Skills can come from:
  - **Internal**: `modular_agent_designer.skills.*` (shipped with the framework)
  - **External**: Any pip-installed package exporting skill directories
  - **Local**: A `skills/` folder next to the YAML or in CWD (auto-added to `sys.path`)

### Model Configuration
Model strings MUST be prefixed with the provider-specific string (e.g., `gemini/`, `anthropic/`, `ollama/`). This is enforced at load time via Pydantic validators in `src/modular_agent_designer/config/schema.py`.

### State Templating
- Use `{{state.key}}` for framework-level resolution at node execution time. This allows agents to "see" the outputs of any prior node in the graph.
- Use `{key}` for ADK-native state injection.
- Template resolution is handled in `src/modular_agent_designer/state/template.py`.

### Environment Variables
API keys should be set as environment variables:
- `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`.
- `OLLAMA_API_BASE` (defaults to `http://localhost:11434`).

### Adding Features
- **New Tools**: Register in `src/modular_agent_designer/tools/registry.py`.
- **New Skills**: Create a skill directory with a `SKILL.md` file, then reference it in YAML via `skills: { my_skill: { ref: my.package.skill-name } }`. See `src/modular_agent_designer/skills/summarize-text/` for an example.
- **New Node Types**: Implement in `src/modular_agent_designer/nodes/` and update `src/modular_agent_designer/config/schema.py`.
### Telemetry and Tracing
- **MLflow Traces**: Enable with `--mlflow EXPERIMENT_ID`. Spans are sent via OTLP (defaulting to localhost:4318) with the `x-mlflow-experiment-id` header set to the provided ID.
