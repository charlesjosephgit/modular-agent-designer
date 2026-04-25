"""Built-in HTTP tool: fetch a URL and return the response body as text."""
from __future__ import annotations

import httpx


async def fetch_url(url: str) -> str:
    """Fetch a URL and return the response body as text."""
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=30.0
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text
