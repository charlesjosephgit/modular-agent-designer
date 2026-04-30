# Test Fixtures

## Remote A2A Agent

`remote_a2a_agent.py` is a tiny local A2A server used for manual and optional
integration testing.

Run it from the repository root:

```bash
python tests/fixtures/remote_a2a_agent.py --port 9999
```

Then use `tests/fixtures/a2a_test_workflow.yaml`, which points to:

```text
http://127.0.0.1:9999/.well-known/agent-card.json
```
