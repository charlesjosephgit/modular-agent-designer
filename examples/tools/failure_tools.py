"""Failure tools used by example workflows."""
from __future__ import annotations


def explode(reason: str = "intentional tool failure") -> dict:
    """Raise an exception so MAD's tool safety wrapper can catch it."""
    raise RuntimeError(reason)
