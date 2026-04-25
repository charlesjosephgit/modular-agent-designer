"""Tests for the shared import_dotted_ref utility."""
import json
import pytest

from modular_agent_designer.utils.imports import import_dotted_ref


def test_happy_path_returns_attribute():
    result = import_dotted_ref("json.loads", context="test")
    assert result is json.loads


def test_non_dotted_ref_raises_value_error():
    with pytest.raises(ValueError, match="test context"):
        import_dotted_ref("nodots", context="test context")


def test_non_dotted_ref_error_includes_ref():
    with pytest.raises(ValueError, match="nodots"):
        import_dotted_ref("nodots", context="x")


def test_missing_module_raises_import_error():
    with pytest.raises(ImportError, match="my_ctx"):
        import_dotted_ref("nonexistent_pkg_xyz.attr", context="my_ctx")


def test_missing_module_error_includes_ref():
    with pytest.raises(ImportError, match="nonexistent_pkg_xyz.attr"):
        import_dotted_ref("nonexistent_pkg_xyz.attr", context="x")


def test_missing_attribute_raises_attribute_error():
    with pytest.raises(AttributeError, match="my_ctx"):
        import_dotted_ref("json.no_such_function_xyz", context="my_ctx")


def test_missing_attribute_error_includes_ref():
    with pytest.raises(AttributeError, match="json.no_such_function_xyz"):
        import_dotted_ref("json.no_such_function_xyz", context="x")


def test_import_error_chains_original_exception():
    with pytest.raises(ImportError) as exc_info:
        import_dotted_ref("nonexistent_pkg_xyz.attr", context="x")
    assert exc_info.value.__cause__ is not None
