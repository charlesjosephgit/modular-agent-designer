"""Tests for new and updated builtin tools."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modular_agent_designer.tools.native.files import read_text_file
from modular_agent_designer.tools.native.http import fetch_url, http_get_json
from modular_agent_designer.tools import BUILTIN_TOOLS


# ---------------------------------------------------------------------------
# read_text_file
# ---------------------------------------------------------------------------


def test_read_text_file_reads_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello.txt").write_text("world", encoding="utf-8")
    result = read_text_file("hello.txt")
    assert result == "world"


def test_read_text_file_rejects_absolute_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = read_text_file("/etc/passwd")
    assert result.startswith("ERROR:")
    assert "absolute" in result


def test_read_text_file_rejects_path_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = read_text_file("../../etc/passwd")
    assert result.startswith("ERROR:")


def test_read_text_file_missing_returns_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = read_text_file("nonexistent.txt")
    assert result.startswith("ERROR:")
    assert "not found" in result


def test_read_text_file_subdirectory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "note.txt").write_text("ok", encoding="utf-8")
    result = read_text_file("sub/note.txt")
    assert result == "ok"


def test_read_text_file_registered_as_builtin() -> None:
    assert "read_text_file" in BUILTIN_TOOLS
    assert callable(BUILTIN_TOOLS["read_text_file"])


# ---------------------------------------------------------------------------
# fetch_url — hardened error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_url_returns_error_on_http_error() -> None:
    import httpx

    with patch("modular_agent_designer.tools.native.http.httpx.AsyncClient") as mock_cls:
        client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        client.get.side_effect = httpx.ConnectError("refused")

        result = await fetch_url("http://nowhere.example")
        assert result.startswith("ERROR ")
        assert "refused" in result


@pytest.mark.asyncio
async def test_fetch_url_success() -> None:
    with patch("modular_agent_designer.tools.native.http.httpx.AsyncClient") as mock_cls:
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.text = "<html>ok</html>"
        client = AsyncMock()
        client.get = AsyncMock(return_value=response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await fetch_url("http://example.com")
        assert result == "<html>ok</html>"


# ---------------------------------------------------------------------------
# http_get_json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_get_json_parses_dict() -> None:
    with patch("modular_agent_designer.tools.native.http.fetch_url", new=AsyncMock(return_value='{"key": "value"}')):
        result = await http_get_json("http://example.com/api")
        assert result == {"key": "value"}


@pytest.mark.asyncio
async def test_http_get_json_returns_error_on_fetch_failure() -> None:
    with patch("modular_agent_designer.tools.native.http.fetch_url", new=AsyncMock(return_value="ERROR fetching http://x: refused")):
        result = await http_get_json("http://x")
        assert "error" in result


@pytest.mark.asyncio
async def test_http_get_json_returns_error_on_invalid_json() -> None:
    with patch("modular_agent_designer.tools.native.http.fetch_url", new=AsyncMock(return_value="not json")):
        result = await http_get_json("http://example.com/bad")
        assert "error" in result
        assert "parse" in result["error"].lower() or "JSON" in result["error"]


@pytest.mark.asyncio
async def test_http_get_json_wraps_non_dict_response() -> None:
    with patch("modular_agent_designer.tools.native.http.fetch_url", new=AsyncMock(return_value="[1, 2, 3]")):
        result = await http_get_json("http://example.com/list")
        assert "error" in result
        assert "data" in result


def test_http_get_json_registered_as_builtin() -> None:
    assert "http_get_json" in BUILTIN_TOOLS
    assert callable(BUILTIN_TOOLS["http_get_json"])
