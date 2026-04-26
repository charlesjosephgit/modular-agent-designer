# demo_agent

Scaffolded by `modular-agent-designer create`. Powered by a local Ollama model — no cloud API keys needed.

## Prerequisites

```bash
ollama serve           # start the Ollama daemon
ollama pull mistral:7b # pull the default model (first time only)
```

## Run

```bash
uv run modular-agent-designer run demo_agent/demo_agent.yaml --input '{"message": "hello"}'
```

## Customise

Edit `demo_agent.yaml` to change the model, add tools, or build a multi-agent graph.
See the [full docs](https://github.com/your-org/modular-agent-designer) for the complete YAML schema.
