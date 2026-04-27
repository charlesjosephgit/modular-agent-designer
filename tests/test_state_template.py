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


# ---------- Conditional blocks ----------


def test_conditional_block_included_when_key_present():
    state = {"reviewer": "Needs more detail."}
    text = "Base.{{#if state.reviewer}} Revision: {{state.reviewer}}{{/if}}"
    assert resolve(text, state) == "Base. Revision: Needs more detail."


def test_conditional_block_stripped_when_key_missing():
    state: dict = {}
    text = "Base.{{#if state.reviewer}} Revision: {{state.reviewer}}{{/if}}"
    assert resolve(text, state) == "Base."


def test_conditional_block_stripped_when_key_falsy_empty_string():
    state = {"reviewer": ""}
    text = "Base.{{#if state.reviewer}} Revision: {{state.reviewer}}{{/if}}"
    assert resolve(text, state) == "Base."


def test_conditional_block_multiline():
    state = {"reviewer": "Add examples."}
    text = (
        "Write about topic.\n"
        "{{#if state.reviewer}}\n"
        "Improve on the previous draft:\n"
        "{{state.reviewer}}\n"
        "{{/if}}"
    )
    result = resolve(text, state)
    assert "Improve on the previous draft:" in result
    assert "Add examples." in result


def test_conditional_block_multiline_stripped():
    state: dict = {}
    text = (
        "Write about topic.\n"
        "{{#if state.reviewer}}\n"
        "Improve on the previous draft:\n"
        "{{state.reviewer}}\n"
        "{{/if}}"
    )
    result = resolve(text, state)
    assert "Improve" not in result
    assert result == "Write about topic.\n"


def test_multiple_conditional_blocks():
    state = {"a": "yes", "b": ""}
    text = "{{#if state.a}}A{{/if}}-{{#if state.b}}B{{/if}}"
    assert resolve(text, state) == "A-"


def test_conditional_with_dotted_path():
    state = {"user_input": {"extra": "info"}}
    text = "{{#if state.user_input.extra}}Extra: {{state.user_input.extra}}{{/if}}"
    assert resolve(text, state) == "Extra: info"
