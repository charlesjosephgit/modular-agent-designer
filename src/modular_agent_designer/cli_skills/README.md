# CLI Coding Assistant Skills

These bundled skills are for coding agents and assistant CLIs that help developers build `modular-agent-designer` workflows. They are not ADK runtime skills.

Runtime skills live under `modular_agent_designer/skills/` and are loaded by workflow YAML through the `skills:` block. The `cli_skills/` package is different: it gives Codex, Agents CLI, Claude Code, Gemini CLI, or another coding assistant task-specific instructions while editing a project.

## Codex / Agents CLI Quickstart

From the project root, install the bundled skills into the default discovery directory:

```bash
mad cli-skills setup
```

This copies all bundled skills into:

```text
.agents/skills/
```

Replace older installed copies with:

```bash
mad cli-skills setup --force
```

After installation, a coding agent can auto-select the right skill from `.agents/skills` based on the task, or you can name one directly in the prompt.

## Which Skill Should an Agent Load?

| User task | Load this skill |
|---|---|
| Understand the DSL, CLI, state model, or available YAML fields | `mad-overview` |
| Create a new workflow or turn an idea into runnable YAML | `mad-create-workflow` |
| Add builtin, Python, MCP stdio, MCP SSE, MCP HTTP tools, or debug tool failures | `mad-tools` |
| Add branches, default routes, switch/case, dynamic destinations, loops, retries, error routes, or parallel fan-out | `mad-routing` |
| Add coordinators, sub-agents, runtime skills, structured outputs, A2A agents, or custom nodes | `mad-sub-agents` |

Agent rule of thumb: load `mad-overview` for orientation, then switch to the narrow skill that owns the change.

## Agent Usage Pattern

When using these skills, a coding agent should:

1. Inspect the existing workflow YAML, prompt files, tools, schemas, and nearby examples before editing.
2. Preserve local project conventions such as `workflows/`, `prompts/`, `tools/`, `schemas/`, and `skills/`.
3. Make the smallest workflow change that satisfies the user request.
4. Validate with `mad list`, `mad diagram`, and `mad run --dry-run` when the workflow can be built without secrets or services.
5. Run a real `mad run ... --input ...` only when model credentials and local services are available.
6. Add `--verbose` to `mad run` only when you need the intermediate workflow-node, agent, sub-agent, and tool event stream. Final output and final state print by default.

Use `mad-routing` for `workflow.default_routes` and eval route conditions such
as `output.agent_status == 'fail'`. Use `mad-tools` for Python tool exceptions,
MCP discovery failures, and unavailable tool-call behavior.

## Other Assistant CLIs

### Claude Code

Install into Claude's project-level discovery directory:

```bash
mad cli-skills setup --dir .claude/skills
```

User-level install:

```bash
mad cli-skills setup --dir ~/.claude/skills
```

Claude can auto-discover the skills or you can invoke them by name, for example `/mad-create-workflow`.

### Gemini CLI

Install into Gemini's project-level discovery directory:

```bash
mad cli-skills setup --dir .gemini/skills
```

User-level install:

```bash
mad cli-skills setup --dir ~/.gemini/skills
```

### ChatGPT CLI

Attach the relevant `SKILL.md` file as context, or paste the selected skill into the conversation before asking for workflow help.

## Complementary Context

Project context files such as `CLAUDE.md`, `GEMINI.md`, or repository README files explain the local codebase. These skills explain how an assistant should perform a specific MAD workflow task. Use both when available.
