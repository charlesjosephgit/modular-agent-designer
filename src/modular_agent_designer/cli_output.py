"""Console rendering for streamed workflow events and final run output."""
from __future__ import annotations

import json
import re
import sys
from typing import Any, Iterable

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

_DEFAULT_MAX_LINE_CHARS = 500
_MIN_DIVIDER_WIDTH = 40


class EventPrinter:
    """Render ADK events grouped by workflow-node sections."""

    def __init__(
        self,
        *,
        color: bool | None = None,
        max_line_chars: int = _DEFAULT_MAX_LINE_CHARS,
        agent_names: Iterable[str] | None = None,
        workflow_node_names: Iterable[str] | None = None,
    ) -> None:
        self._max_line_chars = max_line_chars
        self._agent_names: frozenset[str] = (
            frozenset(agent_names) if agent_names is not None else frozenset()
        )
        self._workflow_node_names: frozenset[str] = (
            frozenset(workflow_node_names)
            if workflow_node_names is not None
            else frozenset()
        )
        self._console = _make_console(color)
        self.last_output: Any = None
        self.last_output_author: str | None = None
        self.last_rendered_output: Any = None
        self.last_workflow_node: str | None = None
        self._current_section_name: str | None = None
        self._current_author: str | None = None
        self._partial_text: dict[tuple[str, str, str], str] = {}
        self._partial_generation: dict[tuple[str, str, str], int] = {}
        self._partial_labels: dict[tuple[str, str, str], tuple[str, str, str | None]] = {}
        self._partial_truncated: set[tuple[str, str, str]] = set()
        self._open_partial_key: tuple[str, str, str] | None = None
        self._stream_generation = 0

    def handle(self, event: Any) -> None:
        author = _resolve_event_author(event, self._agent_names)
        section_name = _resolve_event_section(
            event,
            author,
            self._workflow_node_names,
            self._current_section_name,
        )
        is_partial = bool(getattr(event, "partial", False))
        calls = [] if is_partial else _get_function_calls(event)
        responses = [] if is_partial else _get_function_responses(event)
        raw_thought_chunks = [text for text in _iter_thought_parts(event) if text.strip()]
        thought_chunks = [text.strip() for text in raw_thought_chunks]
        raw_text_chunks = [text for text in _iter_text_parts(event) if text.strip()]
        text_chunks = [text.strip() for text in raw_text_chunks]
        output = getattr(event, "output", None)
        has_renderable_content = bool(
            calls
            or responses
            or thought_chunks
            or text_chunks
            or (not is_partial and output is not None)
        )

        if has_renderable_content:
            self._ensure_section(section_name)
            self._ensure_author_spacing(author)

        thought_key = (section_name, author, "thinking")
        joined_thought = "".join(
            _normalize_stream_chunk(text.strip()) for text in raw_thought_chunks
        ).strip()
        skip_final_thought = (
            not is_partial
            and bool(joined_thought)
            and _same_streamed_text(
                self._partial_text.get((section_name, author, "thinking"), ""),
                joined_thought,
            )
        )
        seen_thoughts: set[str] = set()
        for thought in thought_chunks:
            if skip_final_thought:
                continue
            if thought in seen_thoughts:
                continue
            seen_thoughts.add(thought)
            if is_partial:
                self._emit_partial_stream_text(
                    thought_key,
                    f"[thinking: {author}]",
                    _normalize_stream_chunk(thought),
                    "italic bright_yellow",
                    body_style="dim",
                )
            else:
                self._close_partial_stream()
                self._emit_thinking_text(author, thought)
        for call in calls:
            self._close_partial_stream()
            self._start_new_stream_segment()
            raw_args = _format_call_args(call.args)
            args = self._truncate(raw_args)
            details = args if args else "(no args)"
            self._append_row(
                f"[tool: {call.name}]",
                f"-> {details}",
                "bold magenta",
            )

        for resp in responses:
            self._close_partial_stream()
            self._start_new_stream_segment()
            response_output = _response_fallback_output(resp.response)
            payload = self._truncate(_format_event_output(resp.response))
            self._append_row(
                f"[tool: {resp.name}]",
                f"<- {payload}",
                "bold yellow",
            )
            self.last_rendered_output = response_output

        seen: set[str] = set()
        text_key = (section_name, author, "text")
        joined_text = "".join(
            _normalize_stream_chunk(text.strip()) for text in raw_text_chunks
        ).strip()
        skip_final_text = (
            not is_partial
            and bool(joined_text)
            and _same_streamed_text(self._partial_text.get(text_key, ""), joined_text)
        )
        for stripped in text_chunks:
            if skip_final_text:
                continue
            if stripped in seen:
                continue
            seen.add(stripped)
            if is_partial:
                label, style = self._actor_label(author)
                self._emit_partial_stream_text(
                    text_key,
                    label,
                    _normalize_stream_chunk(stripped),
                    style,
                )
            else:
                self._close_partial_stream()
                self._emit_agent_text(author, stripped)
            self.last_rendered_output = stripped
        if not is_partial and output is not None:
            self._close_partial_stream()
            self._start_new_stream_segment()
            formatted = _format_event_output(output).strip()
            if formatted and formatted not in seen:
                seen.add(formatted)
                self._emit_agent_text(author, formatted)
                self.last_rendered_output = output

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

        if (
            has_renderable_content
            and section_name in self._workflow_node_names
            and author not in self._workflow_node_names
        ):
            self.last_workflow_node = section_name

    def close(self) -> None:
        """Finalize the current workflow-node section."""
        self._close_partial_stream()
        self._current_section_name = None
        self._current_author = None
        self._partial_generation = {}
        self._partial_labels = {}
        self._partial_truncated = set()
        self._open_partial_key = None

    def _ensure_section(self, name: str) -> None:
        if name == self._current_section_name:
            return
        self.close()
        self._current_section_name = name
        self._current_author = None
        self._print_section_header(name)

    def _ensure_author_spacing(self, author: str) -> None:
        if self._current_author is None:
            self._current_author = author
            return
        if author != self._current_author:
            self._close_partial_stream()
            self._console.print()
            self._current_author = author

    def _emit_agent_text(self, author: str, text: str) -> None:
        label, style = self._actor_label(author)
        self._append_row(label, self._truncate(text), style)

    def _emit_thinking_text(self, author: str, text: str) -> None:
        self._append_row(
            f"[thinking: {author}]",
            self._truncate(text),
            "italic bright_yellow",
            body_style="dim",
        )

    def _emit_partial_stream_text(
        self,
        key: tuple[str, str, str],
        label: str,
        chunk: str,
        style: str,
        *,
        body_style: str | None = None,
    ) -> None:
        generation = self._partial_generation.get(key)
        if generation != self._stream_generation:
            self._close_partial_stream()
            self._partial_text[key] = ""
            self._partial_truncated.discard(key)
            self._partial_generation[key] = self._stream_generation
        self._partial_labels[key] = (label, style, body_style)
        previous = self._partial_text.get(key, "")
        current = self._append_stream_text(previous, chunk)
        self._partial_text[key] = current

        delta = current[len(previous) :] if current.startswith(previous) else chunk
        if (
            self._max_line_chars > 0
            and key not in self._partial_truncated
            and len(current) > self._max_line_chars
        ):
            self._partial_truncated.add(key)
            hidden = len(current) - self._max_line_chars
            delta = delta[: max(0, len(delta) - hidden)].rstrip()
            delta = f"{delta}\n... truncated"
        elif self._max_line_chars > 0 and key in self._partial_truncated:
            return
        if self._open_partial_key != key:
            self._close_partial_stream()
            self._console.print(Text(label + " ", style=style), end="")
            self._open_partial_key = key
        self._console.print(Text(delta, style=body_style), end="")

    def _append_stream_text(self, current: str, chunk: str) -> str:
        if not current:
            return _clean_stream_text(chunk.strip())
        if not chunk:
            return current
        if current[-1].isspace() or chunk[0].isspace() or not chunk[0].isalnum():
            return _clean_stream_text(current + chunk)
        return _clean_stream_text(f"{current} {chunk}")

    def _start_new_stream_segment(self) -> None:
        self._stream_generation += 1

    def _close_partial_stream(self) -> None:
        if self._open_partial_key is not None:
            self._console.print()
            self._open_partial_key = None
            return

    def _append_row(
        self,
        label: str,
        body: str,
        style: str,
        *,
        body_style: str | None = None,
    ) -> None:
        self._console.print(self._make_row(label, body, style, body_style=body_style))

    def _make_row(
        self,
        label: str,
        body: str,
        style: str,
        *,
        body_style: str | None = None,
    ) -> Text:
        row = Text(overflow="fold")
        row.append(label, style=style)
        if body:
            row.append(" ")
            row.append(body, style=body_style)
        return row

    def _actor_label(self, author: str) -> tuple[str, str]:
        if not self._workflow_node_names:
            return f"[{author}]", "bold cyan"
        if author in self._workflow_node_names:
            return f"[{author}]", "bold cyan"
        if author in self._agent_names:
            return f"[sub-agent: {author}]", "bold blue"
        return f"[node: {author}]", "bright_cyan"

    def _print_section_header(self, name: str) -> None:
        if self._current_section_name is not None:
            self._console.print()
        self._console.print(Text(f"Workflow Node: {name}", style="bold cyan"))
        width = max(_MIN_DIVIDER_WIDTH, self._console.size.width)
        self._console.print(Text("-" * width, style="cyan"))

    def _truncate(self, text: str) -> str:
        limit = self._max_line_chars
        if limit <= 0 or len(text) <= limit:
            return text
        truncated = text[:limit].rstrip()
        if "\n" not in truncated:
            last_space = truncated.rfind(" ")
            if last_space > max(0, limit // 2):
                truncated = truncated[:last_space].rstrip()
        return f"{truncated}\n... truncated"


def print_final_output(
    value: Any,
    author: str | None = None,
    *,
    color: bool | None = None,
) -> None:
    """Emit the workflow's final answer in a Rich panel."""
    console = _make_console(color)
    title = f"Final Output ({author})" if author else "Final Output"
    body = (
        Text("(no output)", style="dim")
        if value is None
        else Text(_format_event_output(value))
    )
    console.print(Panel(body, title=title, title_align="left", border_style="green"))


def print_final_state(state: dict, *, color: bool | None = None) -> None:
    """Emit the final session state dict in a Rich panel."""
    console = _make_console(color)
    body = Text(json.dumps(state, indent=2, default=str, ensure_ascii=False))
    console.print(
        Panel(body, title="Final State", title_align="left", border_style="green")
    )


def _make_console(color: bool | None) -> Console:
    return Console(
        file=sys.stdout,
        force_terminal=color if color is not None else None,
        no_color=color is False,
        highlight=False,
    )


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
    name - but `event.node_info.path` is overwritten by the wrapper's
    NodeRunner to the parent's path, so `node_info.name` shows the parent.
    For routers, the wrapper's ctx.event_author is never overridden by an
    inner agent, so `event.author` is the workflow name while
    `node_info.name` is the synthetic node's name.

    Rule: prefer `event.author` when it's a user-declared agent (handles
    both workflow agents and sub-agents); otherwise fall back to
    `node_info.name` (handles routers, joins, dispatches). Empty path -> use
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


def _resolve_event_section(
    event: Any,
    author: str,
    workflow_node_names: frozenset[str],
    current_section: str | None = None,
) -> str:
    """Return the top-level workflow node that should group this event."""
    if not workflow_node_names:
        return author

    if author in workflow_node_names:
        return author

    node_info = getattr(event, "node_info", None)
    if node_info is not None:
        for node_name in _iter_node_path_names(getattr(node_info, "path", "")):
            if node_name in workflow_node_names:
                return node_name

    if current_section in workflow_node_names:
        return current_section

    return author


def _iter_node_path_names(path: str):
    for part in path.split("/"):
        name = part.split("@", 1)[0]
        if name:
            yield name


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


def _iter_thought_parts(event: Any):
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return
    for part in parts:
        if not getattr(part, "thought", False):
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
        return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))
    return repr(value) if isinstance(value, str) else str(value)


def _format_event_output(output: Any) -> str:
    if isinstance(output, (dict, list)):
        return json.dumps(output, indent=2, default=str, ensure_ascii=False)
    return str(output)


def _response_fallback_output(response: Any) -> Any:
    if isinstance(response, dict) and set(response) == {"result"}:
        return response["result"]
    return response


def _same_streamed_text(left: str, right: str) -> bool:
    return "".join(left.split()) == "".join(right.split())


def _normalize_stream_chunk(chunk: str) -> str:
    if not chunk:
        return chunk
    if chunk[0].isalnum():
        return f" {chunk}"
    return chunk


def _clean_stream_text(text: str) -> str:
    text = re.sub(r"\s+([.,;:!?%)\]\}])", r"\1", text)
    text = re.sub(r"([\(\[\{])\s+", r"\1", text)
    text = re.sub(r"\s+([`*_])", r"\1", text)
    text = re.sub(r"([`*_])\s+", r"\1", text)
    text = re.sub(r"([A-Za-z0-9])\s+_\s+([A-Za-z0-9])", r"\1_\2", text)
    text = re.sub(r"([A-Za-z0-9])_\s+([A-Za-z0-9])", r"\1_\2", text)
    text = re.sub(r"(special)\s+(ist)\b", r"\1\2", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+-\s*", "-", text)
    text = re.sub(r"\s*-\s+", "-", text)
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    return text
