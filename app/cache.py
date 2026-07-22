"""In-process TTL cache. Entries are {"payload": dict, "created_at": datetime}.

Freshness is judged by created_at at read time (see service.py), NOT by the
TTLCache clock — so a DB row warmed at startup that is already older than X is
never served as fresh. The TTL here is only a memory-eviction backstop."""

import threading

from cachetools import TTLCache

from .config import settings

_lock = threading.Lock()
_cache: TTLCache = TTLCache(
    maxsize=settings.cache_max_size,
    ttl=max(settings.freshness_seconds * 2, 60),
)


def get(key: str) -> dict | None:
    with _lock:
        return _cache.get(key)


def set(key: str, value: dict) -> None:
    with _lock:
        _cache[key] = value


def warm(items: list[dict]) -> None:
    """Seed cache from DB at startup (the only time DB populates the cache)."""
    with _lock:
        for it in items:
            _cache[it["genre"]] = {"payload": it["payload"], "created_at": it["created_at"]}
