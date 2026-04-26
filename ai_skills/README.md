# AI Coding Assistant Skills

These are instruction skills for AI coding tools — **not** ADK agent skills used at workflow runtime. Load them into Claude Code, Gemini CLI, or ChatGPT CLI to get expert assistance building `modular-agent-designer` workflows.

> The `skills/` directory at the project root contains ADK runtime skills for agents. This `ai_skills/` directory is separate and contains instructional guides for human developers using AI coding assistants.

---

## Which Skill to Load

| Task | Skill to load |
|---|---|
| First time using the library / full reference | `mad-overview` |
| Building a new workflow from scratch | `mad-create-workflow` |
| Adding tools (builtin, python, MCP stdio/SSE/HTTP) | `mad-tools` |
| Setting up conditional routing or branching | `mad-routing` |
| Using sub-agents, skills, output schemas, or custom nodes | `mad-sub-agents` |

---

## Loading Skills

### Claude Code

Skills must be placed in `.claude/skills/` for auto-discovery. Run once from the project root:

```bash
mkdir -p .claude/skills
for skill in ai_skills/mad-*/; do cp -r "$skill" .claude/skills/; done
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
mkdir -p ~/.claude/skills
for skill in ai_skills/mad-*/; do cp -r "$skill" ~/.claude/skills/; done
```

### Gemini CLI

Skills must be placed in `.gemini/skills/` for auto-discovery. Run once from the project root:

```bash
mkdir -p .gemini/skills
for skill in ai_skills/mad-*/; do cp -r "$skill" .gemini/skills/; done
```

Then start a session with `gemini`. The model activates skills automatically based on your prompt, or invoke manually:

```
/mad-overview
/mad-create-workflow
```

To make skills available across all your projects (user-level):

```bash
mkdir -p ~/.gemini/skills
for skill in ai_skills/mad-*/; do cp -r "$skill" ~/.gemini/skills/; done
```

### ChatGPT CLI

Attach the relevant SKILL.md as a file or paste its contents as context before asking for help.

---

## Complementary Context Files

The `CLAUDE.md` and `GEMINI.md` files at the project root give AI tools codebase context (architecture, commands, gotchas). The skills in this directory give task-specific instructional behavior. Both complement each other — load the context file first, then the relevant skill.
