"""CLI entry point: `modular-agent-designer run <yaml> --input '<json>'`."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import click
from google.adk import Runner
from google.adk.agents.run_config import RunConfig
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .config.loader import load_workflow
from .plugins.dedup import DeduplicateToolCallsPlugin, _STATE_PREFIX
from .scaffolding.templates import render as _render_scaffold
from .workflow.builder import build_workflow

_APP_NAME = "modular_agent_designer"
_USER_ID = "cli-user"
_SESSION_ID = "cli-session"


@click.group()
def main() -> None:
    """A modular framework for designing and orchestrating complex agentic workflows with ease."""


@main.command()
@click.argument("yaml_path")
@click.option(
    "--input",
    "input_json",
    required=True,
    help=(
        "JSON object passed as the workflow input "
        "(available as state.user_input)."
    ),
)
@click.option(
    "--mlflow",
    "mlflow_experiment_id",
    default=None,
    metavar="EXPERIMENT_ID",
    help="Enable MLflow tracing via OTLP and send spans to the configured OTLP endpoint. "
    "EXPERIMENT_ID is set as the x-mlflow-experiment-id header (default: 0).",
)
def run(yaml_path: str, input_json: str, mlflow_experiment_id: str | None) -> None:
    """Run a workflow defined in YAML_PATH with --input JSON."""
    # Inject the CWD and the YAML file's directory into sys.path so that local
    # tool packages (e.g. a tools/ folder next to the workflow) are importable
    # without requiring a pip install.
    for extra in (os.getcwd(), str(Path(yaml_path).resolve().parent)):
        if extra not in sys.path:
            sys.path.insert(0, extra)

    if mlflow_experiment_id is not None:
        from .telemetry import setup_tracing

        setup_tracing(mlflow_experiment_id)

    try:
        _parsed = json.loads(input_json)
    except json.JSONDecodeError as exc:
        click.echo(f"Error: --input is not valid JSON: {exc}", err=True)
        sys.exit(1)
    if not isinstance(_parsed, dict):
        click.echo(
            f"Error: --input must be a JSON object, got {type(_parsed).__name__}",
            err=True,
        )
        sys.exit(1)
    input_data: dict[str, Any] = _parsed

    try:
        cfg = load_workflow(yaml_path)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error loading workflow: {exc}", err=True)
        sys.exit(1)

    try:
        workflow = build_workflow(cfg)
    except (ValueError, ImportError, AttributeError, EnvironmentError) as exc:
        click.echo(f"Error building workflow: {exc}", err=True)
        sys.exit(1)

    final_state = asyncio.run(_run_workflow(workflow, input_data, cfg.workflow.max_llm_calls))
    click.echo(json.dumps(final_state, indent=2, default=str))


@main.command()
@click.argument("agent_name")
@click.option(
    "--dir",
    "parent_dir",
    default=None,
    metavar="DIR",
    help="Parent directory to create the agent folder in (defaults to CWD).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing files in the target folder.",
)
def create(agent_name: str, parent_dir: str | None, force: bool) -> None:
    """Scaffold a new agent project folder named AGENT_NAME."""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", agent_name):
        click.echo(
            f"Error: '{agent_name}' is not a valid Python identifier. "
            "Use letters, digits, and underscores; must not start with a digit.",
            err=True,
        )
        sys.exit(1)

    base = Path(parent_dir) if parent_dir else Path.cwd()
    folder = base / agent_name

    files = _render_scaffold(agent_name)

    existing = [folder / name for name in files if (folder / name).exists()]
    if existing and not force:
        names = ", ".join(str(p.relative_to(base)) for p in existing)
        click.echo(
            f"Error: file(s) already exist: {names}\n"
            "Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)

    folder.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        target = folder / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    click.echo(f"\nCreated agent '{agent_name}' in {folder}/\n")
    click.echo("  Files:")
    for filename in files:
        click.echo(f"    {agent_name}/{filename}")
    click.echo(
        f"\nNext steps:\n"
        f"  1. Start Ollama:  ollama serve && ollama pull gemma:e4b\n"
        f"  2. Run:           uv run modular-agent-designer run "
        f"{agent_name}/{agent_name}.yaml --input '{{\"message\": \"hello\"}}'\n"
    )


async def _run_workflow(
    workflow, input_data: dict[str, Any], max_llm_calls: int = 20
) -> dict[str, Any]:
    session_service = InMemorySessionService()

    # Pre-populate state so {{state.user_input.*}} templates resolve.
    session = await session_service.create_session(
        app_name=_APP_NAME,
        user_id=_USER_ID,
        state={"user_input": input_data},
    )

    runner = Runner(
        app_name=_APP_NAME,
        agent=workflow,
        session_service=session_service,
        plugins=[DeduplicateToolCallsPlugin()],
    )

    new_message = types.Content(
        role="user",
        parts=[types.Part(text=json.dumps(input_data))],
    )

    async for _ in runner.run_async(
        user_id=_USER_ID,
        session_id=session.id,
        new_message=new_message,
        run_config=RunConfig(max_llm_calls=max_llm_calls),
    ):
        pass  # drain events; state is written into the session

    # Return the final session state.
    final_session = await session_service.get_session(
        app_name=_APP_NAME,
        user_id=_USER_ID,
        session_id=session.id,
    )
    if (
        final_session
        and hasattr(final_session, "state")
        and final_session.state
    ):
        return {
            k: v
            for k, v in dict(final_session.state).items()
            if not k.startswith(_STATE_PREFIX)
        }
    return {}
