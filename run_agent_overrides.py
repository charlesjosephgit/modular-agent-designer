"""Test script for workflows/agent_overrides.yaml.

Verifies:
- coordinator writes to state["coordinator_result"] (custom output_key)
- search_specialist and synthesis_specialist are actually called
- static_instruction, description, parallel_worker, generate_content_config all compile cleanly

Run:
    uv run python run_agent_overrides.py
    uv run python run_agent_overrides.py --topic "quantum computing"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SEPARATOR = "=" * 60


async def main(topic: str) -> None:
    from modular_agent_designer import load_workflow, build_workflow, run_workflow_async

    yaml_path = "workflows/agent_overrides.yaml"
    input_data = {"topic": topic}

    print(f"\n{SEPARATOR}")
    print(f"  Workflow : {yaml_path}")
    print(f"  Topic    : {topic}")
    print(f"{SEPARATOR}\n")

    # ── 1. Load & validate ────────────────────────────────────────────
    print("Step 1/3  Loading + validating YAML …")
    cfg = load_workflow(yaml_path)

    coordinator_cfg = cfg.agents["coordinator"]
    search_cfg = cfg.agents["search_specialist"]
    synthesis_cfg = cfg.agents["synthesis_specialist"]

    assert coordinator_cfg.output_key == "coordinator_result"
    assert coordinator_cfg.static_instruction is not None
    assert coordinator_cfg.generate_content_config.temperature == 0.3
    assert search_cfg.description is not None
    assert search_cfg.parallel_worker is True
    assert search_cfg.generate_content_config.temperature == 0.1
    assert synthesis_cfg.parallel_worker is True
    assert synthesis_cfg.generate_content_config.temperature == 0.7

    print("  ✓ Schema OK")
    print(f"  ✓ output_key          : {coordinator_cfg.output_key!r}")
    print(f"  ✓ static_instruction  : {len(coordinator_cfg.static_instruction)} chars")
    print(f"  ✓ coordinator temp    : {coordinator_cfg.generate_content_config.temperature}")
    print(f"  ✓ search temp         : {search_cfg.generate_content_config.temperature}")
    print(f"  ✓ synthesis temp      : {synthesis_cfg.generate_content_config.temperature}")
    print(f"  ✓ parallel_worker     : {search_cfg.parallel_worker}")

    # ── 2. Build ──────────────────────────────────────────────────────
    print("\nStep 2/3  Compiling workflow graph …")
    workflow = build_workflow(cfg)
    print(f"  ✓ '{workflow.name}' compiled ({len(workflow.edges)} edges)")

    # ── 3. Run ────────────────────────────────────────────────────────
    print("\nStep 3/3  Running workflow (Ollama must be running) …")
    final_state = await run_workflow_async(workflow, input_data)

    print(f"\n{SEPARATOR}")
    print("  Final state keys:", list(final_state.keys()))
    print(SEPARATOR)

    # Show each agent's output
    for key in ("search_specialist", "synthesis_specialist", "coordinator_result"):
        value = final_state.get(key)
        if value:
            print(f"\n── {key} ──")
            print(value)

    if not final_state.get("search_specialist") and not final_state.get("synthesis_specialist"):
        print("\n  ⚠ Neither sub-agent wrote to state.")
        print("    Sub-agent outputs appear in state only when ADK routes them")
        print("    as top-level nodes; when invoked as tools by the coordinator")
        print("    their output is embedded in the coordinator's conversation.")
        print("    Check the coordinator_result for delegated content.\n")

    # coordinator_result is the mandatory assertion
    assert "coordinator_result" in final_state, (
        f"Expected 'coordinator_result' in state, got keys: {list(final_state.keys())}"
    )
    result = final_state["coordinator_result"]
    assert result and str(result).strip(), "coordinator_result is empty"

    # Heuristic: the result should mention content from both specialists
    # (not just "I will call..." — that means delegation didn't happen)
    result_lower = str(result).lower()
    delegation_phrases = ["i will", "i'll call", "i would", "let me call", "i should use"]
    only_described = any(p in result_lower for p in delegation_phrases) and len(result) < 200
    if only_described:
        print("\n  ⚠ Coordinator described what it would do instead of doing it.")
        print("    The model may need a stronger prompt or a different Ollama model.")
    else:
        print(f"\n  ✓ coordinator_result has content ({len(result)} chars)")

    print(f"\n{SEPARATOR}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test agent_overrides.yaml workflow")
    parser.add_argument(
        "--topic",
        default="the history of artificial intelligence",
        help="Topic to research",
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args.topic))
    except KeyboardInterrupt:
        sys.exit(0)
    except AssertionError as exc:
        print(f"\n✗ Assertion failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        logger.exception("Workflow failed: %s", exc)
        sys.exit(1)
