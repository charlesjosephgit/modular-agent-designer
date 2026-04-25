"""Agent-level after_model_callback: capture thinking parts into session state.

ADK's LiteLlm wrapper translates provider-specific reasoning payloads
(Anthropic `thinking_blocks`, LiteLLM `reasoning_content`, Ollama/vLLM
`reasoning`, Gemini thought parts) into `types.Part(text=..., thought=True)`
on `LlmResponse.content.parts`.

We attach this as `after_model_callback` on the Agent itself (not a Plugin).
ADK 2.0a3 constructs the plugin-level CallbackContext without event_actions,
so plugin state writes are silently discarded — see
`google/adk/agents/llm/_reasoning.py::_handle_after_model_callback`. The
agent-level callback receives a context wired to the real event, so state
writes persist.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.models.llm_response import LlmResponse


def make_capture_thinking_callback(agent_name: str) -> Callable:
    """Return an `after_model_callback` that stashes thought text in state.

    Writes to `state[f'{agent_name}__thinking']` as a list of strings — one
    entry per model turn (a tool-calling loop produces multiple).
    """
    key = f"{agent_name}__thinking"

    async def _capture(
        *,
        callback_context: "CallbackContext",
        llm_response: "LlmResponse",
    ) -> Optional["LlmResponse"]:
        content = llm_response.content
        if content is None or not content.parts:
            return None
        thoughts = [
            part.text
            for part in content.parts
            if getattr(part, "thought", False) and part.text
        ]
        if not thoughts:
            return None
        existing = callback_context.state.get(key)
        if isinstance(existing, list):
            callback_context.state[key] = existing + thoughts
        else:
            callback_context.state[key] = thoughts
        return None

    return _capture
