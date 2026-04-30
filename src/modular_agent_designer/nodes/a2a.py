"""Build YAML-declared remote A2A agents."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator
from urllib.parse import urlparse

from google.adk import Context, Event
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.workflow import node as adk_node
from google.genai import types
from pydantic import BaseModel

from ..config.schema import A2aAgentConfig


def build_remote_a2a_agent(agent_name: str, cfg: A2aAgentConfig) -> Any:
    """Build an ADK-compatible remote A2A agent from YAML config."""
    _ensure_a2a_sdk()
    return RemoteA2aAgentAdapter(
        name=agent_name,
        agent_card=cfg.agent_card,
        description=cfg.description,
        timeout_seconds=cfg.timeout_seconds,
    )


def build_a2a_agent_node(agent_name: str, cfg: A2aAgentConfig) -> Any:
    """Return an ADK-compatible workflow node for a remote A2A agent."""
    agent = build_remote_a2a_agent(agent_name, cfg)
    output_key = cfg.output_key or agent_name

    async def _wrapper(ctx: Context, node_input: Any) -> AsyncGenerator:
        _append_node_input(ctx, node_input)
        ic = ctx.get_invocation_context().model_copy(update={"agent": agent})
        async for event in agent.run_async(ic):
            _set_output(agent_name, output_key, ctx, event)
            yield event

    _wrapper.__name__ = agent_name
    _wrapper.__qualname__ = agent_name

    return adk_node(rerun_on_resume=True)(_wrapper)


class RemoteA2aAgentAdapter(BaseAgent):
    """Small ADK BaseAgent adapter over the installed a2a-sdk client."""

    agent_card: str
    timeout_seconds: float = 600.0

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        request_text = _last_session_text(ctx) or ""
        response_text = await _send_a2a_message(
            self.agent_card,
            request_text,
            timeout_seconds=self.timeout_seconds,
        )
        content = types.Content(
            role="model",
            parts=[types.Part(text=response_text)],
        )
        yield Event(
            author=self.name,
            content=content,
            output=response_text,
            invocation_id=ctx.invocation_id,
            branch=ctx.branch,
        )

    async def _run_live_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        raise NotImplementedError("A2A live mode is not supported.")
        yield


def _ensure_a2a_sdk() -> None:
    try:
        import a2a.client  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name == "a2a":
            raise RuntimeError(
                "A2A agents require the A2A SDK. Install it with "
                "`pip install 'a2a-sdk[http-server]'` or install "
                "`modular-agent-designer[a2a]`."
            ) from exc
        raise


async def _send_a2a_message(
    agent_card: str,
    text: str,
    timeout_seconds: float,
) -> str:
    import httpx
    from a2a.client.client import ClientConfig, ClientCallContext
    from a2a.client.client_factory import ClientFactory
    from a2a.client.card_resolver import parse_agent_card
    from a2a.types import SendMessageRequest

    async with httpx.AsyncClient(timeout=timeout_seconds) as httpx_client:
        factory = ClientFactory(ClientConfig(httpx_client=httpx_client))
        parsed = urlparse(agent_card)
        if parsed.scheme in {"http", "https"}:
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            relative_path = parsed.path or None
            client = await factory.create_from_url(
                base_url,
                relative_card_path=relative_path,
            )
        else:
            with Path(agent_card).open("r", encoding="utf-8") as f:
                card = parse_agent_card(json.load(f))
            client = factory.create(card)

        request = SendMessageRequest(message=_build_user_message(text))

        chunks: list[str] = []
        async for item in client.send_message(
            request,
            context=ClientCallContext(timeout=timeout_seconds),
        ):
            chunks.extend(_stream_response_text_parts(item))
        return "\n".join(part for part in chunks if part)


def _stream_response_text_parts(stream_response: Any) -> list[str]:
    if stream_response.HasField("message"):
        return _message_text_parts(stream_response.message)
    if stream_response.HasField("task"):
        parts: list[str] = []
        task = stream_response.task
        if task.status and task.status.HasField("message"):
            parts.extend(_message_text_parts(task.status.message))
        for artifact in task.artifacts:
            for part in artifact.parts:
                if part.text:
                    parts.append(part.text)
        return parts
    if stream_response.HasField("status_update"):
        update = stream_response.status_update
        if update.status and update.status.HasField("message"):
            return _message_text_parts(update.status.message)
    if stream_response.HasField("artifact_update"):
        return [
            part.text
            for part in stream_response.artifact_update.artifact.parts
            if part.text
        ]
    return []


def _build_user_message(text: str) -> Any:
    from a2a.types import Message, Role

    message = Message(
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_USER,
    )
    message.parts.add(text=text)
    return message


def _message_text_parts(message: Any) -> list[str]:
    return [part.text for part in message.parts if part.text]


def _last_session_text(ctx: InvocationContext) -> str | None:
    for event in reversed(ctx.session.events):
        content = getattr(event, "content", None)
        if not content or not content.parts:
            continue
        text = "\n".join(
            part.text
            for part in content.parts
            if getattr(part, "text", None)
        )
        if text:
            return text
    return None


def _append_node_input(ctx: Context, node_input: Any) -> None:
    if node_input is None:
        return
    content = _node_input_to_content(node_input)
    event = Event(author="user", message=content)
    if event.content is not None:
        event.content.role = "user"
    event.branch = ctx._invocation_context.branch
    ctx.session.events.append(event)


def _node_input_to_content(node_input: Any) -> types.Content:
    if isinstance(node_input, types.Content):
        return types.Content(role="user", parts=node_input.parts)
    if isinstance(node_input, str):
        text = node_input
    elif isinstance(node_input, BaseModel):
        text = node_input.model_dump_json()
    elif isinstance(node_input, (dict, list)):
        text = json.dumps(node_input)
    else:
        text = str(node_input)
    return types.Content(role="user", parts=[types.Part(text=text)])


def _set_output(
    agent_name: str,
    output_key: str,
    ctx: Context,
    event: Event,
) -> None:
    if event.partial or event.author != agent_name or not event.content:
        return
    if event.get_function_calls():
        return
    text = (
        "".join(
            part.text
            for part in event.content.parts
            if part.text and not part.thought
        )
        if event.content.parts
        else ""
    )
    if not text:
        return
    event.output = text
    event.node_info.message_as_output = True
    ctx.actions.state_delta[output_key] = text
