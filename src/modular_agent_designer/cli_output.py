"""Console rendering for streamed workflow events, the final answer, and
final session state.

Used by the `mad run` CLI to attribute every chunk of streamed output to the
agent or tool that produced it, and to set the final answer (the last agent's
output) and final state dict apart from intermediate stream lines with
colored banners.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

import click

_DEFAULT_MAX_LINE_CHARS = 500


class EventPrinter:
    """Render ADK events to the console with per-actor labels and colors.

    `color=None` defers to Click's TTY detection (color is auto-suppressed when
    stdout is piped or redirected).
    """

    def __init__(
        self,
        *,
        color: bool | None = None,
        max_line_chars: int = _DEFAULT_MAX_LINE_CHARS,
        agent_names: Iterable[str] | None = None,
        workflow_node_names: Iterable[str] | None = None,
    ) -> None:
        self._color = color
        self._max_line_chars = max_line_chars
        self._agent_names: frozenset[str] = (
            frozenset(agent_names) if agent_names is not None else frozenset()
        )
        self._workflow_node_names: frozenset[str] = (
            frozenset(workflow_node_names)
            if workflow_node_names is not None
            else frozenset()
        )
        self.last_output: Any = None
        self.last_output_author: str | None = None
        self.last_workflow_node: str | None = None

    def handle(self, event: Any) -> None:
        author = _resolve_event_author(event, self._agent_names)

        for call in _get_function_calls(event):
            args_str = self._truncate(_format_call_args(call.args))
            line = click.style(f"→ {call.name}({args_str})", fg="yellow")
            self._echo_line(line)

        for resp in _get_function_responses(event):
            payload = _format_event_output(resp.response)
            line = click.style(
                f"← {resp.name} → {self._truncate(payload)}",
                fg="yellow",
                dim=True,
            )
            self._echo_line(line)

        seen: set[str] = set()
        for text in _iter_text_parts(event):
            stripped = text.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            self._emit_agent_text(author, stripped)

        output = getattr(event, "output", None)
        if output is not None:
            formatted = _format_event_output(output).strip()
            if formatted and formatted not in seen:
                seen.add(formatted)
                self._emit_agent_text(author, formatted)

        # Track only top-level workflow nodes so sub-agent events don't
        # masquerade as the workflow's final answer. When no workflow-node
        # set was configured, every agent-final event counts (legacy mode).
        is_workflow_node = (
            not self._workflow_node_names
            or author in self._workflow_node_names
        )
        if is_workflow_node:
            self.last_workflow_node = author
            if _is_agent_final_answer(event):
                self.last_output = (
                    output if output is not None else _joined_text(event)
                )
                self.last_output_author = author

    def _emit_agent_text(self, author: str, text: str) -> None:
        header = click.style(f"[{author}]", fg="cyan", bold=True)
        body = self._truncate(text)
        self._echo_line(f"{header} {body}")

    def _echo_line(self, line: str) -> None:
        click.echo(line, color=self._color)

    def _truncate(self, text: str) -> str:
        limit = self._max_line_chars
        if limit <= 0 or len(text) <= limit:
            return text
        remaining = len(text) - limit
        return f"{text[:limit]}… (truncated, {remaining} more chars)"


def print_final_output(
    value: Any,
    author: str | None = None,
    *,
    color: bool | None = None,
) -> None:
    """Emit the workflow's final answer wrapped in a colored banner.

    `value` is typically the most recent event's `output` — the value the
    last agent produced. `author` is the name of the agent that produced
    it; when supplied it appears in the banner.
    """
    label = f"── Final Output ({author}) ──" if author else "── Final Output ──"
    banner = click.style(label, fg="green", bold=True)
    closing = click.style("─" * len(label), fg="green", bold=True)
    click.echo(banner, color=color)
    if value is None:
        click.echo(click.style("(no output)", dim=True), color=color)
    else:
        click.echo(_format_event_output(value), color=color)
    click.echo(closing, color=color)


def print_final_state(state: dict, *, color: bool | None = None) -> None:
    """Emit the final session state dict wrapped in a `Final State` banner."""
    label = "── Final State ──"
    banner = click.style(label, fg="green", bold=True)
    closing = click.style("─" * len(label), fg="green", bold=True)
    click.echo(banner, color=color)
    click.echo(json.dumps(state, indent=2, default=str), color=color)
    click.echo(closing, color=color)


def _is_agent_final_answer(event: Any) -> bool:
    """Whether this event represents an agent's final response to the user.

    ADK sets `event.node_info.message_as_output = True` on the non-partial,
    non-tool-call model-role event that carries an LlmAgent's final response
    (see google.adk.workflow._llm_agent_wrapper.process_llm_agent_output).
    A2A nodes set the same flag explicitly. Synthetic router/join/dispatch
    nodes injected by the workflow compiler do NOT set it, so they're
    correctly excluded from "the workflow's final answer".
    """
    node_info = getattr(event, "node_info", None)
    if node_info is None:
        return False
    return bool(getattr(node_info, "message_as_output", False))


def _joined_text(event: Any) -> str:
    return "".join(_iter_text_parts(event)).strip()


def _resolve_event_author(event: Any, agent_names: frozenset[str]) -> str:
    """Identify which agent or node produced this event.

    Three cases collide here:

    | Case                     | event.author      | node_info.name      |
    |--------------------------|-------------------|---------------------|
    | Workflow node (writer)   | writer            | writer              |
    | Sub-agent (search_spec.) | search_specialist | coordinator (parent)|
    | Synthetic router         | <workflow name>   | validator_router    |

    For sub-agents, ADK propagates the inner author via `ctx.event_author`
    in `base_agent._run_impl`, so `event.author` carries the sub-agent's
    name — but `event.node_info.path` is overwritten by the wrapper's
    NodeRunner to the parent's path, so `node_info.name` shows the parent.
    For routers, the wrapper's ctx.event_author is never overridden by an
    inner agent, so `event.author` is the workflow name while
    `node_info.name` is the synthetic node's name.

    Rule: prefer `event.author` when it's a user-declared agent (handles
    both workflow agents and sub-agents); otherwise fall back to
    `node_info.name` (handles routers, joins, dispatches). Empty path → use
    author as a last resort.
    """
    author = getattr(event, "author", None) or ""
    if author and author in agent_names:
        return author

    node_info = getattr(event, "node_info", None)
    if node_info is not None:
        name = getattr(node_info, "name", None)
        if name:
            return name

    return author or "?"


def _iter_text_parts(event: Any):
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return
    for part in parts:
        if getattr(part, "thought", False):
            continue
        text = getattr(part, "text", None)
        if text:
            yield text


def _get_function_calls(event: Any) -> list[Any]:
    getter = getattr(event, "get_function_calls", None)
    if callable(getter):
        try:
            return list(getter() or [])
        except Exception:
            return []
    return []


def _get_function_responses(event: Any) -> list[Any]:
    getter = getattr(event, "get_function_responses", None)
    if callable(getter):
        try:
            return list(getter() or [])
        except Exception:
            return []
    return []


def _format_call_args(args: Any) -> str:
    if not args:
        return ""
    if isinstance(args, dict):
        return ", ".join(f"{k}={_compact(v)}" for k, v in args.items())
    return _compact(args)


def _compact(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, separators=(",", ":"))
    return repr(value) if isinstance(value, str) else str(value)


def _format_event_output(output: Any) -> str:
    if isinstance(output, (dict, list)):
        return json.dumps(output, indent=2, default=str)
    return str(output)
