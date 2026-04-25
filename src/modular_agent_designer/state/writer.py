"""Helpers for writing node outputs to Context state."""
from __future__ import annotations

from typing import Any

from google.adk import Event


def state_event(key: str, value: Any) -> Event:
    """Return an Event that writes *value* into ctx.state[key]."""
    return Event(state={key: value})
