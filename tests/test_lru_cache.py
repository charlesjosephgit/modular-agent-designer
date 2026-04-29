"""Tests for the _LRUCache used in agent_node.py."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from modular_agent_designer.nodes.agent_node import _LRUCache


def test_lru_cache_stores_and_retrieves() -> None:
    cache = _LRUCache(maxsize=3)
    cache.set("a", 1)
    assert cache.get("a") == 1


def test_lru_cache_returns_none_for_missing() -> None:
    cache = _LRUCache(maxsize=3)
    assert cache.get("x") is None


def test_lru_cache_evicts_least_recently_used() -> None:
    cache = _LRUCache(maxsize=2)
    cache.set("a", 1)
    cache.set("b", 2)
    # Access "a" so "b" becomes the LRU
    cache.get("a")
    # Adding "c" should evict "b" (the least recently used)
    cache.set("c", 3)
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert cache.get("b") is None


def test_lru_cache_len() -> None:
    cache = _LRUCache(maxsize=5)
    for i in range(3):
        cache.set(str(i), i)
    assert len(cache) == 3


def test_lru_cache_does_not_exceed_maxsize() -> None:
    cache = _LRUCache(maxsize=3)
    for i in range(10):
        cache.set(str(i), i)
    assert len(cache) <= 3


def test_lru_cache_update_refreshes_order() -> None:
    cache = _LRUCache(maxsize=2)
    cache.set("a", 1)
    cache.set("b", 2)
    # Update "a" to refresh its position
    cache.set("a", 99)
    # Now adding "c" should evict "b"
    cache.set("c", 3)
    assert cache.get("a") == 99
    assert cache.get("c") == 3
    assert cache.get("b") is None


def test_lru_cache_thread_safe_under_concurrent_access() -> None:
    cache = _LRUCache(maxsize=8)

    def worker(offset: int) -> None:
        for i in range(200):
            key = str((offset + i) % 16)
            cache.set(key, i)
            cache.get(key)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(worker, range(8)))

    assert len(cache) <= 8
