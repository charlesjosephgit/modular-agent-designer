"""Tests for models/registry.py."""
from __future__ import annotations

import os

import pytest

from modular_agent_designer.config.schema import ModelConfig
from modular_agent_designer.models.registry import build_model


def _cfg(provider: str, model: str, **kw) -> ModelConfig:
    return ModelConfig(provider=provider, model=model, **kw)


def test_ollama_builds_without_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
    m = build_model(_cfg("ollama", "ollama/gemma4:e4b"))
    assert m.model == "ollama/gemma4:e4b"


def test_anthropic_raises_without_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
        build_model(_cfg("anthropic", "anthropic/claude-sonnet-4-5"))


def test_anthropic_builds_with_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    m = build_model(_cfg("anthropic", "anthropic/claude-sonnet-4-5", temperature=0.3))
    assert m.model == "anthropic/claude-sonnet-4-5"


def test_google_raises_without_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="GOOGLE_API_KEY"):
        build_model(_cfg("google", "gemini/gemini-2.5-flash"))


def test_google_builds_with_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
    m = build_model(_cfg("google", "gemini/gemini-2.5-flash"))
    assert m.model == "gemini/gemini-2.5-flash"


def test_openai_raises_without_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="OPENAI_API_KEY"):
        build_model(_cfg("openai", "openai/gpt-4.1"))


def test_openai_builds_with_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    m = build_model(_cfg("openai", "openai/gpt-4.1"))
    assert m.model == "openai/gpt-4.1"


def test_temperature_passed_through(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    m = build_model(_cfg("anthropic", "anthropic/claude-sonnet-4-5", temperature=0.7))
    assert m.model == "anthropic/claude-sonnet-4-5"
