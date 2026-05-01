"""CLI entry point: `modular-agent-designer run <yaml> --input '<data>'`."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from importlib.metadata import PackageNotFoundError, version
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

_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
_INTERNAL_STATE_PREFIXES = (_STATE_PREFIX, "_loop_", "_error_", "_dispatch_")


def _package_version() -> str:
    try:
        return version("modular-agent-designer")
    except PackageNotFoundError:
        return "0+unknown"


def _is_public_state_key(key: str) -> bool:
    return not (
        key.startswith(_INTERNAL_STATE_PREFIXES)
        or key.endswith("__thinking")
    )


def _printable_event_chunks(event: Any) -> list[str]:
    chunks: list[str] = []
    seen: set[str] = set()

    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if parts:
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                _append_printable_chunk(chunks, seen, text)

    output = getattr(event, "output", None)
    if output is not None:
        _append_printable_chunk(chunks, seen, _format_event_output(output))

    return chunks


def _append_printable_chunk(chunks: list[str], seen: set[str], value: str) -> None:
    value = value.strip()
    if value and value not in seen:
        seen.add(value)
        chunks.append(value)


def _format_event_output(output: Any) -> str:
    if isinstance(output, (dict, list)):
        return json.dumps(output, indent=2, default=str)
    return str(output)


def _parse_workflow_input(raw_input: str) -> Any:
    """Parse JSON input when possible, otherwise treat the input as plain text."""
    try:
        return json.loads(raw_input)
    except json.JSONDecodeError:
        return raw_input


@click.group()
@click.version_option(version=_package_version(), prog_name="modular-agent-designer")
def main() -> None:
    """A modular framework for designing and orchestrating complex agentic workflows with ease."""


@main.command()
@click.argument("yaml_path")
@click.option(
    "--input",
    "input_json",
    default=None,
    help=(
        "JSON value or plain string passed as the workflow input "
        "(available as state.user_input)."
    ),
)
@click.option(
    "--input-file",
    "input_file",
    default=None,
    metavar="PATH",
    help=(
        "Path to a JSON or text file to use as workflow input. "
        "Use '-' to read from stdin."
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
@click.option(
    "--log-level",
    "log_level",
    default=None,
    type=click.Choice(_LOG_LEVELS, case_sensitive=False),
    help="Set logging level (DEBUG, INFO, WARNING, ERROR).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Build the workflow and print the execution plan without running it.",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable INFO logging and print workflow details during dry runs.",
)
def run(
    yaml_path: str,
    input_json: str | None,
    input_file: str | None,
    mlflow_experiment_id: str | None,
    log_level: str | None,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Run a workflow defined in YAML_PATH with --input DATA or --input-file PATH."""
    if log_level is not None or verbose:
        effective_level = log_level.upper() if log_level is not None else "INFO"
        logging.basicConfig(
            level=getattr(logging, effective_level),
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )

    # Exactly one of --input or --input-file must be provided, except dry-run
    # mode where the workflow is built but not executed.
    if input_json is not None and input_file is not None:
        click.echo("Error: --input and --input-file are mutually exclusive.", err=True)
        sys.exit(1)
    if not dry_run and input_json is None and input_file is None:
        click.echo("Error: one of --input or --input-file is required.", err=True)
        sys.exit(1)

    raw_input: str | None = None
    if input_file is not None:
        if input_file == "-":
            raw_input = sys.stdin.read()
        else:
            p = Path(input_file)
            if not p.exists():
                click.echo(f"Error: --input-file not found: {p}", err=True)
                sys.exit(1)
            raw_input = p.read_text(encoding="utf-8")
    elif input_json is not None:
        raw_input = input_json  # type: ignore[assignment]

    # Inject the CWD and the YAML file's directory into sys.path so that local
    # tool packages (e.g. a tools/ folder next to the workflow) are importable
    # without requiring a pip install.
    for extra in (os.getcwd(), str(Path(yaml_path).resolve().parent)):
        if extra not in sys.path:
            sys.path.insert(0, extra)

    if mlflow_experiment_id is not None:
        from .telemetry import setup_tracing

        setup_tracing(mlflow_experiment_id)

    input_data: Any = {}
    if raw_input is not None:
        input_data = _parse_workflow_input(raw_input)

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

    if dry_run:
        click.echo(f"Dry run OK: workflow '{cfg.name}' builds successfully.")
        _echo_workflow_details(cfg)
        return

    final_state = asyncio.run(_run_workflow(workflow, input_data, cfg.workflow.max_llm_calls))
    click.echo(json.dumps(final_state, indent=2, default=str))


@main.command()
@click.argument("yaml_path")
@click.option(
    "--skip-build",
    is_flag=True,
    default=False,
    help=(
        "Only validate the YAML schema; skip building the workflow "
        "(avoids API-key checks, useful in CI without secrets)."
    ),
)
def validate(yaml_path: str, skip_build: bool) -> None:
    """Validate the workflow YAML at YAML_PATH without running it.

    Exits 0 on success, 1 on any error.
    """
    for extra in (os.getcwd(), str(Path(yaml_path).resolve().parent)):
        if extra not in sys.path:
            sys.path.insert(0, extra)

    try:
        cfg = load_workflow(yaml_path)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if not skip_build:
        try:
            build_workflow(cfg)
        except (ValueError, ImportError, AttributeError, EnvironmentError) as exc:
            click.echo(f"Error building workflow: {exc}", err=True)
            sys.exit(1)

    n_agents = len(cfg.agents)
    n_tools = len(cfg.tools)
    n_models = len(cfg.models)
    phase = "schema" if skip_build else "build"
    click.echo(
        f"OK ({phase}): '{cfg.name}' — "
        f"{n_agents} agent(s), {n_tools} tool(s), {n_models} model(s)"
    )


def _echo_workflow_details(cfg: Any) -> None:
    from .config.schema import A2aAgentConfig, AgentConfig, NodeRefConfig

    click.echo(f"\nWorkflow: {cfg.name}")
    if cfg.description:
        click.echo(f"  {cfg.description}")

    click.echo(f"\nModels ({len(cfg.models)}):")
    for alias, m in cfg.models.items():
        extras = []
        if m.temperature is not None:
            extras.append(f"temp={m.temperature}")
        if m.max_tokens is not None:
            extras.append(f"max_tokens={m.max_tokens}")
        if m.thinking:
            extras.append("thinking")
        suffix = f"  [{', '.join(extras)}]" if extras else ""
        click.echo(f"  {alias}: {m.model}{suffix}")

    click.echo(f"\nTools ({len(cfg.tools)}):")
    for alias, t in cfg.tools.items():
        click.echo(f"  {alias}: type={t.type}")

    if cfg.skills:
        click.echo(f"\nSkills ({len(cfg.skills)}):")
        for alias, s in cfg.skills.items():
            click.echo(f"  {alias}: {s.ref}")

    click.echo(f"\nAgents ({len(cfg.agents)}):")
    wf_nodes = set(cfg.workflow.nodes)
    all_sub: set[str] = set()
    for a in cfg.agents.values():
        if isinstance(a, AgentConfig):
            all_sub.update(a.sub_agents)

    for agent_name, a in cfg.agents.items():
        tags: list[str] = []
        if agent_name in wf_nodes:
            tags.append("node")
        if agent_name in all_sub:
            tags.append("sub-agent")
        tag_str = f" [{', '.join(tags)}]" if tags else ""

        if isinstance(a, AgentConfig):
            tool_str = f"  tools={a.tools}" if a.tools else ""
            sub_str = f"  sub_agents={a.sub_agents}" if a.sub_agents else ""
            mode_str = f"  mode={a.mode}" if a.mode else ""
            retry_str = ""
            if a.retry is not None:
                retry_str = (
                    f"  retry(max={a.retry.max_retries}, "
                    f"backoff={a.retry.backoff}, "
                    f"delay={a.retry.delay_seconds}s)"
                )
            click.echo(f"  {agent_name}{tag_str}: model={a.model}{mode_str}{tool_str}{sub_str}{retry_str}")
        elif isinstance(a, A2aAgentConfig):
            click.echo(
                f"  {agent_name}{tag_str}: a2a agent_card={a.agent_card}"
            )
        elif isinstance(a, NodeRefConfig):
            click.echo(f"  {agent_name}{tag_str}: custom node ref={a.ref}")

    wf = cfg.workflow
    click.echo("\nWorkflow graph:")
    click.echo(f"  entry: {wf.entry}")
    click.echo(f"  nodes: {', '.join(wf.nodes)}")
    click.echo(f"  max_llm_calls: {wf.max_llm_calls}")
    if wf.edges:
        click.echo(f"  edges ({len(wf.edges)}):")
        for edge in wf.edges:
            # Build the target display.
            if isinstance(edge.to, list):
                to_str = f"[{', '.join(edge.to)}]"
            else:
                to_str = edge.to

            # Build decorators.
            decorators: list[str] = []

            if edge.condition is not None:
                c = edge.condition
                if c == "__DEFAULT__":
                    decorators.append("default")
                elif hasattr(c, "eval"):
                    decorators.append(f"eval: {c.eval}")
                elif isinstance(c, list):
                    decorators.append(f"if: {' | '.join(str(v) for v in c)}")
                else:
                    decorators.append(f"if: {c}")

            if edge.loop is not None:
                loop_str = f"loop ≤{edge.loop.max_iterations}×"
                if edge.loop.on_exhausted:
                    loop_str += f" → {edge.loop.on_exhausted}"
                decorators.append(loop_str)

            if edge.parallel:
                decorators.append("parallel")

            if edge.join is not None:
                decorators.append(f"join: {edge.join}")

            if edge.on_error:
                decorators.append("on_error")

            dec_str = f" [{', '.join(decorators)}]" if decorators else ""
            click.echo(f"    {edge.from_} -> {to_str}{dec_str}")
    else:
        click.echo("  edges: (none)")
    click.echo("")


@main.command(name="list")
@click.argument("yaml_path")
def list_workflow(yaml_path: str) -> None:
    """List models, tools, agents, and workflow graph defined in YAML_PATH."""
    try:
        cfg = load_workflow(yaml_path)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    _echo_workflow_details(cfg)


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


@main.command()
@click.argument("yaml_path")
@click.option(
    "--output",
    "output_path",
    default=None,
    metavar="PATH",
    help="Write diagram to PATH instead of stdout.",
)
def diagram(yaml_path: str, output_path: str | None) -> None:
    """Emit a Mermaid flowchart for the workflow defined in YAML_PATH."""
    try:
        cfg = load_workflow(yaml_path)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Error loading workflow: {exc}", err=True)
        sys.exit(1)

    from .visualize.mermaid import render_mermaid

    text = render_mermaid(cfg)

    if output_path:
        Path(output_path).write_text(text)
        click.echo(f"Diagram written to {output_path}")
    else:
        click.echo(text, nl=False)


async def _run_workflow(
    workflow, input_data: Any, max_llm_calls: int = 20
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
        parts=[
            types.Part(
                text=input_data
                if isinstance(input_data, str)
                else json.dumps(input_data)
            )
        ],
    )

    async for event in runner.run_async(
        user_id=_USER_ID,
        session_id=session.id,
        new_message=new_message,
        run_config=RunConfig(max_llm_calls=max_llm_calls),
    ):
        for chunk in _printable_event_chunks(event):
            click.echo(chunk)

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
            if _is_public_state_key(k)
        }
    return {}
