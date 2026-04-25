"""Tests for state/template.py."""
from __future__ import annotations

import pytest
from modular_agent_designer.state.template import StateReferenceError, resolve



def test_simple_resolve():
    assert resolve("hello {{state.name}}", {"name": "world"}) == "hello world"


def test_dotted_path():
    state = {"user_input": {"topic": "tide pools"}}
    result = resolve("Topic: {{state.user_input.topic}}", state)
    assert result == "Topic: tide pools"


def test_multiple_references():
    state = {"a": "AAA", "b": "BBB"}
    result = resolve("{{state.a}} and {{state.b}}", state)
    assert result == "AAA and BBB"


def test_no_templates_unchanged():
    text = "No templates here. Just {curly} braces."
    assert resolve(text, {}) == text


def test_missing_top_level_key_raises():
    with pytest.raises(StateReferenceError) as exc_info:
        resolve("{{state.missing}}", {})
    assert "missing" in str(exc_info.value)
    assert "state" in str(exc_info.value)


def test_missing_nested_key_raises():
    state = {"user_input": {"topic": "tide pools"}}
    with pytest.raises(StateReferenceError) as exc_info:
        resolve("{{state.user_input.nonexistent}}", state)
    msg = str(exc_info.value)
    assert "nonexistent" in msg
    assert "user_input" in msg


def test_error_message_lists_available_keys():
    state = {"user_input": {"topic": "x", "other": "y"}}
    with pytest.raises(StateReferenceError) as exc_info:
        resolve("{{state.user_input.missing}}", state)
    msg = str(exc_info.value)
    assert "topic" in msg or "other" in msg


def test_dict_value_serialized_as_json():
    state = {"data": {"key": "val"}}
    result = resolve("{{state.data}}", state)
    assert '"key"' in result
    assert '"val"' in result


def test_int_value_stringified():
    state = {"count": 42}
    assert resolve("{{state.count}}", state) == "42"


def test_whitespace_in_template():
    state = {"x": "hello"}
    assert resolve("{{ state.x }}", state) == "hello"
