"""Template strings for the `create` scaffold command."""
from __future__ import annotations

_AGENT_PY = """\
from pathlib import Path
from modular_agent_designer import load_workflow, build_workflow

yaml_path = Path(__file__).parent / "{name}.yaml"
cfg = load_workflow(str(yaml_path))
root_agent = build_workflow(cfg)
"""

_WORKFLOW_YAML = """\
name: {name}
description: Starter workflow scaffolded by `modular-agent-designer create`.

models:
  local:
    provider: ollama
    model: ollama_chat/gemma:e4b

tools: {{}}

agents:
  responder:
    model: local
    instruction: |
      The user said: {{{{state.user_input.message}}}}
      Reply in one short, friendly sentence.

workflow:
  entry: responder
  nodes:
    - responder
  edges: []
"""

_INIT_PY = """\
# This file makes the agent folder a Python package.
# Do not delete this file.
"""

_TOOLS_INIT_PY = """\
# tools/__init__.py
#
# Place all custom tool functions for this agent in this package.
#
# A tool is any plain Python function (sync or async). No special decorator
# or base class is required — modular-agent-designer imports and wires it
# automatically via the dotted `ref:` path in the YAML.
#
# Keeping tools here:
#   - makes them easy to test in isolation
#   - keeps the YAML workflow clean
#   - allows sharing tools across multiple agents in the same project
#
# Example tool (add to a new file, e.g. tools/search.py):
#
#   def web_search(query: str) -> str:
#       \"\"\"Search the web and return a summary.\"\"\"
#       ...
#
# Then wire it in {name}.yaml:
#
#   tools:
#     web_search:
#       type: python
#       ref: tools.search.web_search
#
#   agents:
#     responder:
#       model: local
#       tools: [web_search]
"""

_README = """\
# {name}

Scaffolded by `modular-agent-designer create`. Powered by a local Ollama model — no cloud API keys needed.

## Prerequisites

```bash
ollama serve           # start the Ollama daemon
ollama pull gemma:e4b # pull the default model (first time only)
```

## Run

```bash
uv run modular-agent-designer run {name}/{name}.yaml --input '{{"message": "hello"}}'
```

## Customise

Edit `{name}.yaml` to change the model, add tools, or build a multi-agent graph.
See the [full docs](https://github.com/your-org/modular-agent-designer) for the complete YAML schema.
"""


def render(name: str) -> dict[str, str]:
    """Return a mapping of relative path → content for the scaffolded agent folder."""
    return {
        "agent.py": _AGENT_PY.format(name=name),
        f"{name}.yaml": _WORKFLOW_YAML.format(name=name),
        "__init__.py": _INIT_PY,
        "README.md": _README.format(name=name),
        "tools/__init__.py": _TOOLS_INIT_PY.format(name=name),
    }
