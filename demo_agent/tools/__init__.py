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
#       """Search the web and return a summary."""
#       ...
#
# Then wire it in demo_agent.yaml:
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
