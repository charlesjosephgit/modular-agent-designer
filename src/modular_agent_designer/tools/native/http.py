"""Built-in HTTP tools."""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class _FetchResult:
    body: str | None = None
    error: str | None = None


async def _fetch_url_result(url: str) -> _FetchResult:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=30.0
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return _FetchResult(body=response.text)
    except httpx.HTTPError as exc:
        return _FetchResult(error=f"ERROR fetching {url}: {exc}")


async def fetch_url(url: str) -> str:
    """Fetch a URL and return the response body as text.

    Returns an error string prefixed with 'ERROR ' on failure so the
    calling LLM can react to it rather than crashing the tool call.
    """
    result = await _fetch_url_result(url)
    return result.body if result.error is None else result.error


async def http_get_json(url: str) -> dict:
    """Fetch a URL and parse the response body as JSON.

    Returns a dict on success. On HTTP or parse failure, returns
    {"error": "<description>"} so the calling LLM can react gracefully.
    """
    fetch_result = await _fetch_url_result(url)
    if fetch_result.error is not None:
        return {"error": fetch_result.error}
    raw = fetch_result.body or ""
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            return {"error": f"Expected JSON object, got {type(result).__name__}", "data": result}
        return result
    except json.JSONDecodeError as exc:
        return {"error": f"JSON parse error: {exc}", "raw": raw[:500]}
