---
name: mad-tools
description: Use when a coding agent should add, choose, debug, or validate modular-agent-designer builtin tools, Python callables, or MCP stdio/SSE/HTTP toolsets.
---

# Tools in MAD Workflows

Tools are resolved at build time and attached to ADK agents through each agent's `tools:` list.

## Use This When

- The workflow needs HTTP fetches, local Python functions, file reads, or external MCP tools.
- An agent references a tool alias that is missing or broken.
- You need to choose between builtin tools, Python tools, and MCP transports.

Load `mad-create-workflow` for end-to-end workflow creation, `mad-routing` for graph behavior, and `mad-sub-agents` when tools are part of a coordinator/specialist design.

## Agent Workflow

1. Inspect existing `tools:` blocks, local `tools/` packages, and nearby examples such as `examples/workflows/local_tools_example.yaml` and `examples/workflows/mcp_example.yaml`.
2. Choose the smallest tool type that fits the need:
   - builtin for shipped simple utilities,
   - Python for project-local deterministic logic,
   - MCP for external tool servers or large tool suites.
3. Give every tool a clear YAML alias and attach that alias to only the agents that need it.
4. For MCP servers, prefer `tool_filter` and `tool_name_prefix` to reduce ambiguity.
5. Validate with `mad list` and `mad run --dry-run`.

## Tool Type Decision

| Need | Use |
|---|---|
| Fetch text from a URL | builtin `fetch_url` |
| Fetch JSON from a URL | builtin `http_get_json` |
| Read a UTF-8 file under CWD | builtin `read_text_file` |
| Call project code | `type: python` |
| Use a subprocess MCP server | `type: mcp_stdio` |
| Use a running SSE MCP service | `type: mcp_sse` |
| Use streamable HTTP MCP | `type: mcp_http` |

## Builtin Tools

```yaml
tools:
  fetch:
    type: builtin
    name: fetch_url

  json_api:
    type: builtin
    name: http_get_json

  reader:
    type: builtin
    name: read_text_file
```

Available builtin tools:

| Name | Behavior |
|---|---|
| `fetch_url` | Async HTTP GET; follows redirects; 30 second timeout; returns response text or `ERROR: ...` |
| `http_get_json` | Async HTTP GET and JSON parse; returns a dict or `{"error": "..."}` |
| `read_text_file` | Reads UTF-8 text relative to CWD; rejects absolute paths and `..` traversal |

For builtin tools, use either `name:` or `ref:`, not both:

```yaml
tools:
  fetch:
    type: builtin
    ref: modular_agent_designer.tools.fetch_url
```

## Python Tools

Use Python tools for deterministic project-local logic.

```yaml
tools:
  word_count:
    type: python
    ref: tools.text_tools.word_count

agents:
  analyst:
    model: local
    instruction: |
      Analyze this text: {{state.user_input.text}}
      Use word_count when exact counts are needed.
    tools: [word_count]
```

Recommended layout:

```text
your-project/
  workflows/
    my_workflow.yaml
  tools/
    __init__.py
    text_tools.py
```

The CLI adds CWD and the YAML directory to `sys.path`, so local `tools/` packages work when running from the project root.

Tool refs must point to callables, not modules:

```python
def word_count(text: str) -> int:
    return len(text.split())
```

## MCP stdio Tools

Use `mcp_stdio` when MAD should start the MCP server subprocess:

```yaml
tools:
  fs:
    type: mcp_stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/sandbox"]
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}
    tool_filter: [read_file, write_file]
    tool_name_prefix: fs
```

Notes:

- `${VAR}` values are expanded at YAML load time and fail immediately when unset.
- The MCP connection is opened lazily on first tool use.
- ADK Runner handles cleanup.

## MCP SSE Tools

Use `mcp_sse` for an already-running SSE server:

```yaml
tools:
  remote_search:
    type: mcp_sse
    url: http://localhost:8080/sse
    headers:
      Authorization: "Bearer ${API_TOKEN}"
    tool_filter: [search, summarize]
    tool_name_prefix: remote
```

## MCP Streamable HTTP Tools

Use `mcp_http` for streamable HTTP MCP servers:

```yaml
tools:
  api:
    type: mcp_http
    url: https://api.example.com/mcp/
    headers:
      Authorization: "Bearer ${API_TOKEN}"
    tool_name_prefix: api
```

## Collision Control

When an MCP server exposes many tools, restrict and prefix them:

```yaml
tools:
  local_fs:
    type: mcp_stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/local"]
    tool_filter: [read_file, list_directory]
    tool_name_prefix: local

  remote_fs:
    type: mcp_http
    url: https://remote.example.com/mcp/
    tool_filter: [read_file]
    tool_name_prefix: remote
```

Without prefixes, two servers that expose `read_file` can confuse the model or collide in the tool namespace.

## Complete Tool Example

```yaml
name: multi_tool_demo

models:
  local:
    provider: ollama
    model: ollama_chat/llama3.2

tools:
  fetch:
    type: builtin
    name: fetch_url

  word_count:
    type: python
    ref: tools.text_tools.word_count

  filesystem:
    type: mcp_stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/sandbox"]
    tool_filter: [read_file, list_directory]
    tool_name_prefix: fs

agents:
  assistant:
    model: local
    mode: task
    instruction: |
      Answer: {{state.user_input.question}}
      Use fetch for web pages, word_count for exact counts, and fs_* tools for filesystem inspection.
    tools: [fetch, word_count, filesystem]

workflow:
  nodes: [assistant]
  entry: assistant
  edges: []
```

## Validation

```bash
mad list workflows/my_workflow.yaml
mad run workflows/my_workflow.yaml --dry-run
```

For a live run, make sure required MCP servers, subprocess commands, env vars, and model credentials are available.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Agent lists a tool alias not declared under `tools:` | Add the tool or correct the alias |
| Python `ref` points to a module | Point to a callable such as `tools.text_tools.word_count` |
| Local `tools/` lacks `__init__.py` | Add it so Python can import the package |
| MCP env var is unset | Export it before load/build |
| MCP exposes too many tools | Add `tool_filter` |
| Multiple MCP servers expose the same names | Add unique `tool_name_prefix` values |
| Ollama model cannot call tools | Use an `ollama_chat/` model where tool calling is needed |
