"""CLI entry point: `modular-agent-designer run <yaml> --input '<data>'`."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
from importlib import resources
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

import click
from google.adk import Runner
from google.adk.agents.run_config import RunConfig
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .cli_output import EventPrinter, print_final_output, print_final_state
from .config.loader import load_workflow
from .plugins.dedup import DeduplicateToolCallsPlugin, _STATE_PREFIX
from .scaffolding.templates import render as _render_scaffold
from .workflow.builder import build_workflow

_APP_NAME = "modular_agent_designer"
_USER_ID = "cli-user"
_SESSION_ID = "cli-session"

_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
_INTERNAL_STATE_PREFIXES = (_STATE_PREFIX, "_loop_", "_error_", "_dispatch_")
_CLI_SKILLS_PACKAGE = "modular_agent_designer.cli_skills"


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


def _parse_workflow_input(raw_input: str) -> Any:
    """Parse JSON input when possible, otherwise treat the input as plain text."""
    try:
        return json.loads(raw_input)
    except json.JSONDecodeError:
        return raw_input


def _copy_cli_skills(target_dir: Path, force: bool = False) -> list[Path]:
    """Copy bundled assistant CLI skills into a discovery directory."""
    skills_root = resources.files(_CLI_SKILLS_PACKAGE)

    with resources.as_file(skills_root) as root:
        skill_dirs = sorted(
            path
            for path in root.iterdir()
            if path.is_dir() and path.name.startswith("mad-")
        )

        existing = [
            target_dir / path.name
            for path in skill_dirs
            if (target_dir / path.name).exists()
            or (target_dir / path.name).is_symlink()
        ]
        if existing and not force:
            names = ", ".join(str(path) for path in existing)
            raise FileExistsError(
                f"Skill folder(s) already exist: {names}. "
                "Use --force to replace them."
            )

        target_dir.mkdir(parents=True, exist_ok=True)
        installed: list[Path] = []
        for skill_dir in skill_dirs:
            destination = target_dir / skill_dir.name
            if force:
                _remove_existing_path(destination)
            shutil.copytree(skill_dir, destination)
            installed.append(destination)

    return installed


def _remove_existing_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


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
    help=(
        "Stream agent/tool events. "
        "Dry runs also print workflow details."
    ),
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
    if log_level is not None:
        effective_level = log_level.upper()
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

    printer = (
        EventPrinter(
            agent_names=cfg.agents.keys(),
            workflow_node_names=getattr(cfg.workflow, "nodes", ()),
        )
        if verbose
        else None
    )
    final_state = asyncio.run(
        _run_workflow(
            workflow,
            input_data,
            cfg.workflow.max_llm_calls,
            event_handler=printer.handle if printer is not None else None,
        )
    )
    final_output_author = _resolve_final_output_author(
        final_state,
        cfg,
        printer.last_output_author if printer is not None else None,
        printer.last_workflow_node if printer is not None else None,
    )
    final_output = _resolve_final_output(
        final_state,
        cfg,
        final_output_author,
        printer.last_output if printer is not None else None,
    )
    if final_output is None:
        final_output_author = None
    if printer is not None:
        printer.close()
    print_final_output(
        final_output,
        final_output_author,
    )
    print_final_state(final_state)


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


def _resolve_final_output(
    final_state: dict[str, Any],
    cfg: Any,
    author: str | None,
    fallback: Any,
) -> Any:
    """Return the final workflow node's committed state value.

    ADK marks sub-agent final responses with `message_as_output=True`, but
    only top-level workflow nodes write the durable workflow result. Prefer the
    final state value so the CLI banner matches downstream workflow state.
    """
    if author is None:
        return fallback

    agent_cfg = cfg.agents.get(author)
    output_key = getattr(agent_cfg, "output_key", None) or author
    return final_state.get(output_key, fallback)


def _resolve_final_output_author(
    final_state: dict[str, Any],
    cfg: Any,
    event_author: str | None,
    last_workflow_node: str | None,
) -> str | None:
    """Find the workflow node whose committed state should be printed."""
    for author in (event_author, last_workflow_node):
        if author is not None and _state_has_agent_output(final_state, cfg, author):
            return author

    for author in reversed(getattr(cfg.workflow, "nodes", ())):
        if _state_has_agent_output(final_state, cfg, author):
            return author

    return event_author or last_workflow_node


def _state_has_agent_output(final_state: dict[str, Any], cfg: Any, author: str) -> bool:
    agent_cfg = cfg.agents.get(author)
    output_key = getattr(agent_cfg, "output_key", None) or author
    return output_key in final_state


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


@main.group(name="cli-skills")
def cli_skills() -> None:
    """Manage bundled assistant CLI skills."""


@cli_skills.command(name="setup")
@click.option(
    "--dir",
    "target_dir",
    default=".agents/skills",
    metavar="DIR",
    help="Directory to install skills into (defaults to .agents/skills).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Replace existing skill folders in the target directory.",
)
def setup_cli_skills(target_dir: str, force: bool) -> None:
    """Install bundled assistant CLI skills into TARGET_DIR.

    Defaults to .agents/skills for Codex-style project discovery.
    """
    target = Path(target_dir).expanduser()
    try:
        installed = _copy_cli_skills(target, force=force)
    except FileExistsError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Installed {len(installed)} CLI skill(s) into {target}/")
    for path in installed:
        click.echo(f"  {path.name}")


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
    workflow,
    input_data: Any,
    max_llm_calls: int = 20,
    *,
    event_handler: Callable[[Any], None] | None = None,
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
        if event_handler is not None:
            event_handler(event)

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
