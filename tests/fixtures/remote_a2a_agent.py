"""Tiny remote A2A agent for local integration testing.

Run manually:

    python tests/fixtures/remote_a2a_agent.py --port 9999

Then point YAML at:

    http://127.0.0.1:9999/.well-known/agent-card.json
"""
from __future__ import annotations

import argparse
import uuid

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Role,
    UnsupportedOperationError,
)
from starlette.applications import Starlette


class EchoAgentExecutor(AgentExecutor):
    """A deterministic A2A executor that echoes the user's text."""

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        user_text = context.get_user_input()
        response = f"MAD test A2A echo: {user_text}"
        message = Message(
            message_id=str(uuid.uuid4()),
            context_id=context.context_id,
            task_id=context.task_id,
            role=Role.ROLE_AGENT,
        )
        message.parts.add(text=response)
        await event_queue.enqueue_event(message)

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        raise UnsupportedOperationError()


def build_agent_card(base_url: str) -> AgentCard:
    return AgentCard(
        name="MAD Test Echo A2A Agent",
        description="Local test-only A2A agent for modular-agent-designer.",
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=base_url),
        ],
        version="0.1.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="echo",
                name="Echo text",
                description="Echoes incoming text with a deterministic prefix.",
                tags=["test", "echo"],
                examples=["hello from modular-agent-designer"],
            ),
        ],
    )


def build_app(base_url: str):
    agent_card = build_agent_card(base_url)
    request_handler = DefaultRequestHandler(
        agent_executor=EchoAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    routes = []
    routes.extend(create_agent_card_routes(agent_card))
    routes.extend(create_jsonrpc_routes(request_handler, "/"))
    return Starlette(routes=routes)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()

    import uvicorn

    base_url = f"http://{args.host}:{args.port}"
    uvicorn.run(build_app(base_url), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
