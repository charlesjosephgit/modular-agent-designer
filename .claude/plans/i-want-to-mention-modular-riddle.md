# Switch `instruction_file` to a dotted Python-module-style ref

## Context

`instruction_file` was just added (working-tree changes in [config/schema.py](src/modular_agent_designer/config/schema.py), [config/loader.py](src/modular_agent_designer/config/loader.py), 6 workflow YAMLs, plus 13 untracked files in [prompts/](prompts/)). It currently takes a filesystem path (`../prompts/research_assistant__researcher.txt`) resolved relative to the YAML file's directory.

We want the field to instead take a **dotted Python-module-style reference**, mirroring how `output_schema: pkg.Module.Class` works elsewhere in the schema. The path is resolved from the **project / cwd root**, with dots translated to `/` and a `.txt` extension appended.

```yaml
agents:
  researcher:
    model: local
    instruction_file: prompts.research_assistant__researcher  # → <cwd>/prompts/research_assistant__researcher.txt
```

## Files to modify

### 1. [src/modular_agent_designer/config/loader.py](src/modular_agent_designer/config/loader.py:12-38)
Rewrite `_resolve_instruction_files`:
- Drop the `base_dir` argument (or keep but ignore — see call site at L62).
- Validate the value is a non-empty dotted identifier: each segment must match `[A-Za-z_][\w-]*` (allow `_` and `-`; `__` already used in filenames). Reject leading/trailing/consecutive dots and slashes/backslashes — fail-fast with a clear error.
- Build the path: `Path.cwd() / Path(*value.split("."))` then suffix `.txt`. Resolve and read.
- Keep the existing both-set check and the `FileNotFoundError` / `OSError` error wrapping; include the dotted ref *and* the resolved path in the error messages.
- Update L62 call site: `_resolve_instruction_files(raw)` (drop `p.parent`).

### 2. [src/modular_agent_designer/config/schema.py](src/modular_agent_designer/config/schema.py:167)
Update the `instruction_file` field description to document the dotted-ref format and that it's resolved from cwd. No validator change needed — the loader rewrites the field to `instruction` before pydantic validates.

### 3. Workflow YAMLs (6 files, all currently use `../prompts/<name>.txt`)
Convert each `instruction_file` value to dotted form:
- [workflows/local_tools_example.yaml](workflows/local_tools_example.yaml)
- [workflows/output_schema_routing.yaml](workflows/output_schema_routing.yaml)
- [workflows/research_assistant.yaml](workflows/research_assistant.yaml)
- [workflows/skills_example.yaml](workflows/skills_example.yaml)
- [workflows/sub_agent_example.yaml](workflows/sub_agent_example.yaml)
- [workflows/summarize_article.yaml](workflows/summarize_article.yaml)

Example: `instruction_file: ../prompts/research_assistant__researcher.txt` → `instruction_file: prompts.research_assistant__researcher`.

### 4. [tests/test_loader.py](tests/test_loader.py:122-202)
Update the four new tests to use dotted refs. Use `monkeypatch.chdir(tmp_path)` so cwd-based resolution works inside the test sandbox; create `tmp_path/prompts/<name>.txt` and reference it as `prompts.<name>`. Add one negative test for malformed dotted refs (e.g. `prompts..foo`, `prompts/foo`, empty string).

### 5. [README.md](README.md) (External Prompt Files section, ~L291-308)
Replace the path example with the dotted form. State explicitly:
- Dots are folder separators; `.txt` extension is implied.
- Resolution is from the project root (cwd of the CLI), **not** the YAML file's directory.
- The recommended layout is `<repo>/prompts/<workflow>__<agent>.txt`, referenced as `prompts.<workflow>__<agent>`.
- `{{state.x.y}}` template syntax still works inside the file.

## Reuse / patterns

- Existing dotted-ref consumer: `output_schema` in [config/schema.py](src/modular_agent_designer/config/schema.py). Skim its validator for a regex precedent before writing the new one.
- State templating with `{{state.a.b}}` is unrelated and unchanged — see [state/template.py](src/modular_agent_designer/state/template.py:8). It still runs at node-execution time on the loaded instruction text.
- The both-set / neither-set validator on `AgentConfig` ([schema.py:177-189](src/modular_agent_designer/config/schema.py#L177-L189)) stays as-is; the loader still strips `instruction_file` before validation.

## Verification

1. `uv run pytest tests/test_loader.py -v` — all 4 updated `instruction_file` tests plus the new malformed-ref test pass.
2. `uv run pytest -k "not ollama"` — full suite stays green.
3. `flake8 src/` — clean.
4. End-to-end smoke from repo root:
   `uv run modular-agent-designer run workflows/research_assistant.yaml --input '{"topic": "AI"}'`
   should resolve `prompts.research_assistant__researcher` → `prompts/research_assistant__researcher.txt` and run without errors.
5. Negative check: run from a different cwd (e.g. `cd /tmp && uv run …`) and confirm it fails with a clear "instruction_file not found: /tmp/prompts/…" error — documenting the cwd-relative behavior.
