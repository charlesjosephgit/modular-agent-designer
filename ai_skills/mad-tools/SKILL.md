---
name: mad-tools
description: Guide to all tool types in modular-agent-designer: builtin, python, and MCP stdio/SSE/HTTP.
---

# Tools Reference

Tools are resolved at **build time** and passed to the ADK `Agent`. Agents reference them by their YAML alias name in the `tools:` list.

---

## `type: builtin` — Framework-Provided Tools

Reference a bundled tool by short name or full dotted path:

```yaml
tools:
  # Short name (preferred)
  fetch:
    type: builtin
    name: fetch_url

  # Full dotted path (equivalent)
  fetch2:
    type: builtin
    ref: modular_agent_designer.tools.fetch_url
```

**Available builtin tools:**

| Name | Description |
|---|---|
| `fetch_url` | Async HTTP GET; follows redirects; 30 s timeout; returns response body as text |

Use `name:` OR `ref:`, never both — Pydantic rejects it.

---

## `type: python` — Arbitrary Python Callable

Reference any importable callable from an installed package or local directory:

```yaml
tools:
  word_count:
    type: python
    ref: tools.text_tools.word_count   # dotted path to a callable

  forecast:
    type: python
    ref: mycompany_tools.weather.get_forecast
```

`ref` must point at a **callable** (function, async function, or `__call__`-bearing object) — not a module or class. Pointing at a module raises `TypeError` at build time.

### Local package (no install needed)

Drop a `tools/` directory at your project root:

```
your-project/
  tools/
    __init__.py        # empty — makes tools/ a Python package
    text_tools.py      # your functions here
  workflows/
    my_workflow.yaml
```

Run the CLI from the project root — the framework auto-adds CWD and the YAML file's directory to `sys.path`:

```bash
uv run modular-agent-designer run workflows/my_workflow.yaml --input '{"text": "hello world"}'
```

### External installed package

Install it into the same venv, then reference by dotted path:

```bash
uv pip install -e ./my_tools_pkg --prerelease=allow
```

```yaml
tools:
  my_tool:
    type: python
    ref: my_tools_pkg.module.my_function
```

---

## `type: mcp_stdio` — Subprocess MCP Server

Spawns a subprocess (e.g., `npx`, `python3`) that speaks the MCP protocol:

```yaml
tools:
  fs:
    type: mcp_stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/sandbox"]
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}    # ${VAR} expanded at load time
    tool_filter: [read_file, write_file]   # restrict exposed tools (optional)
    tool_name_prefix: fs                   # prefix to avoid collisions (optional)
```

- `command` — the executable to run.
- `args` — arguments passed to the subprocess.
- `env` — environment variables for the subprocess; `${VAR}` is expanded from the shell environment at YAML load time. **Fails immediately** if the variable is unset.
- **Lifecycle**: connection opened lazily on first tool use; ADK Runner closes it automatically.

---

## `type: mcp_sse` — SSE Transport

For MCP servers already running as a remote service:

```yaml
tools:
  remote_tools:
    type: mcp_sse
    url: http://localhost:8080/sse
    headers:
      Authorization: "Bearer ${API_TOKEN}"   # ${VAR} expanded at load time
    tool_filter: [search, summarize]
    tool_name_prefix: remote
```

Use `mcp_sse` when the MCP server is a long-running process or remote service you don't manage.

---

## `type: mcp_http` — Streamable HTTP Transport

For MCP servers that use HTTP/2 streaming or the newer streamable HTTP transport:

```yaml
tools:
  api_tools:
    type: mcp_http
    url: https://api.example.com/mcp/
    headers:
      Authorization: "Bearer ${MY_TOKEN}"
    tool_name_prefix: api
```

All three MCP types support `tool_filter` and `tool_name_prefix`.

---

## `tool_filter` and `tool_name_prefix`

**`tool_filter`** restricts which tools from the MCP server are exposed to the agent. Useful when a server provides dozens of tools but you only need a few:

```yaml
  fs:
    type: mcp_stdio
    command: docker
    args: ["mcp", "gateway", "run", "--servers=filesystem"]
    tool_filter: [read_file, list_directory]
```

**`tool_name_prefix`** renames all tools from the server by prepending a string. Prevents collisions when two MCP servers both expose a tool named `read_file`:

```yaml
tools:
  local_fs:
    type: mcp_stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    tool_name_prefix: local    # read_file → local_read_file

  remote_fs:
    type: mcp_http
    url: https://remote.example.com/mcp/
    tool_name_prefix: remote   # read_file → remote_read_file
```

---

## Complete Multi-Tool Example

```yaml
name: multi_tool_demo

models:
  local:
    provider: ollama
    model: ollama_chat/llama3.2
    thinking: { reasoning_effort: high }

tools:
  # Builtin: HTTP fetch
  fetch:
    type: builtin
    name: fetch_url

  # Python: local callable
  word_count:
    type: python
    ref: tools.text_tools.word_count

  # MCP stdio: filesystem server via Docker MCP gateway
  docker_fs:
    type: mcp_stdio
    command: docker
    args: ["mcp", "gateway", "run", "--servers=filesystem"]
    tool_name_prefix: docker

agents:
  assistant:
    model: local
    mode: task
    instruction: |
      You have three tools: fetch_url, word_count, and docker_* filesystem tools.
      Help the user with: {{state.user_input.question}}
    tools: [fetch, word_count, docker_fs]

workflow:
  nodes: [assistant]
  edges: []
  entry: assistant
```

---

## Common Mistakes

| Mistake | What happens | Fix |
|---|---|---|
| `ref` points at a module, not a callable | `TypeError` at build time | Point `ref` at the function, e.g., `tools.text_tools.word_count` |
| `${VAR}` with an unset env var | Immediate failure at load time with a clear message | Export the env var before running |
| Tool alias referenced in agent `tools:` but not defined in `tools:` | Pydantic error at load time | Add the tool definition under `tools:` |
| `name:` and `ref:` both set on a builtin | Pydantic rejects it | Use one or the other, not both |
| Forgetting `tool_name_prefix` when two MCP servers share tool names | Agent calls wrong tool or gets confused | Add a unique prefix to each MCP toolset |
