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
    instruction_file: {name}.prompts.{name}__responder

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

_PROMPTS_INIT_PY = """\
# prompts/__init__.py
#
# Store agent prompt files in this directory as .md files.
# Each file corresponds to one agent's instruction.
#
# Naming convention: <workflow>__<agent>.md
# Example: {name}__responder.md
#
# Reference a prompt file in {name}.yaml using a dotted ref:
#
#   agents:
#     responder:
#       instruction_file: {name}.prompts.{name}__responder
#
# Dots are folder separators; .md is appended automatically.
# The path is resolved from the project root (cwd where the CLI runs).
#
# {{{{state.x}}}} and {{{{state.x.y.z}}}} template syntax works inside prompt
# files — resolved at node-execution time, not load time.
"""

_PROMPTS_SAMPLE_TXT = """\
The user said: {{state.user_input.message}}
Reply in one short, friendly sentence.
"""

_SCHEMAS_INIT_PY = """\
# schemas/__init__.py
#
# Define Pydantic v2 output schemas for agents in this workflow.
# Each class becomes the value of `output_schema:` in the YAML.
#
# Example schema (add to a new file, e.g. schemas/response.py):
#
#   from pydantic import BaseModel
#
#   class Response(BaseModel):
#       answer: str
#       confidence: float
#
# Wire it in {name}.yaml:
#
#   agents:
#     responder:
#       model: local
#       output_schema: {name}.schemas.response.Response
#
# ADK validates the agent's output against the schema and stores it as a JSON
# string in state[agent_name]. Downstream agents receive it via
# {{{{state.responder}}}}.
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

_SKILLS_INIT_PY = """\
# skills/__init__.py
#
# Place custom skill packages for this agent in this directory.
#
# A skill is a folder that contains a SKILL.md file. Use skills for reusable
# instructions, workflows, or domain-specific guidance that agents can load by
# reference from the YAML.
#
# Example layout:
#
#   skills/
#     support_triage/
#       SKILL.md
#
# Then wire it in {name}.yaml:
#
#   skills:
#     support_triage:
#       ref: {name}.skills.support_triage
#
#   agents:
#     responder:
#       model: local
#       skills: [support_triage]
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

Edit `{name}.yaml` to change the model, add tools, add skills, or build a multi-agent graph.
See the [full docs](https://github.com/charlesjosephgit/modular-agent-designer) for the complete YAML schema.
"""


def render(name: str) -> dict[str, str]:
    """Return a mapping of relative path → content for the scaffolded agent folder."""
    return {
        "agent.py": _AGENT_PY.format(name=name),
        f"{name}.yaml": _WORKFLOW_YAML.format(name=name),
        "__init__.py": _INIT_PY,
        "README.md": _README.format(name=name),
        "tools/__init__.py": _TOOLS_INIT_PY.format(name=name),
        "skills/__init__.py": _SKILLS_INIT_PY.format(name=name),
        "prompts/__init__.py": _PROMPTS_INIT_PY.format(name=name),
        f"prompts/{name}__responder.md": _PROMPTS_SAMPLE_TXT,
        "schemas/__init__.py": _SCHEMAS_INIT_PY.format(name=name),
    }
