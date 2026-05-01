# CLI Coding Assistant Skills

These are instruction skills for AI coding tools — **not** ADK agent skills used at workflow runtime. Load them into Claude Code, Gemini CLI, or ChatGPT CLI to get expert assistance building `modular-agent-designer` workflows.

> The `modular_agent_designer/skills/` package contains ADK runtime skills for agents. This `cli_skills/` package is separate and contains instructional guides for developers using AI coding assistants.

---

## Which Skill to Load

| Task | Skill to load |
|---|---|
| First time using the library / full reference | `mad-overview` |
| Building a new workflow from scratch | `mad-create-workflow` |
| Adding tools (builtin, python, MCP stdio/SSE/HTTP) | `mad-tools` |
| Conditional routing, loops, error routing, parallel edges | `mad-routing` |
| Using sub-agents, skills, output schemas, or custom nodes | `mad-sub-agents` |

---

## Loading Skills

### Codex / Agents CLI

Project-level skills can be placed in `.agents/skills/`. From your project root:

```bash
mad cli-skills setup
```

That installs the bundled skills into `.agents/skills/`. To replace existing copies:

```bash
mad cli-skills setup --force
```

### Claude Code

Skills must be placed in `.claude/skills/` for auto-discovery. Run once from the project root:

```bash
mad cli-skills setup --dir .claude/skills
```

Then start a session with `claude`. Skills are loaded automatically, or invoke manually:

```
/mad-overview
/mad-create-workflow
/mad-tools
/mad-routing
/mad-sub-agents
```

To make skills available across all your projects (user-level):

```bash
mad cli-skills setup --dir ~/.claude/skills
```

### Gemini CLI

Skills must be placed in `.gemini/skills/` for auto-discovery. Run once from the project root:

```bash
mad cli-skills setup --dir .gemini/skills
```

Then start a session with `gemini`. The model activates skills automatically based on your prompt, or invoke manually:

```
/mad-overview
/mad-create-workflow
```

To make skills available across all your projects (user-level):

```bash
mad cli-skills setup --dir ~/.gemini/skills
```

### ChatGPT CLI

Attach the relevant SKILL.md as a file or paste its contents as context before asking for help.

---

## Complementary Context Files

The `CLAUDE.md` and `GEMINI.md` files at the project root give AI tools codebase context (architecture, commands, gotchas). The skills in this directory give task-specific instructional behavior. Both complement each other — load the context file first, then the relevant skill.
