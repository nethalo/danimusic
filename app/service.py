"""Read-through logic: CACHE -> DB -> VENDOR, with graceful degradation.

Fresh path (age < X): serve first tier that has fresh data.
Vendor path: on miss, fetch; on success cache + async-persist; on failure serve
the freshest thing we have (any age) marked stale; if nothing, ServiceUnavailable."""

import logging
from datetime import datetime

from fastapi import BackgroundTasks
from starlette.concurrency import run_in_threadpool

from . import cache, db, vendor
from .config import settings
from .metrics import CACHE_EVENTS, RESPONSE_SOURCE
from .models import MusicResponse

log = logging.getLogger(__name__)


class ServiceUnavailable(Exception):
    """No fresh source and nothing to fall back on."""


def _age_seconds(created_at: datetime) -> float:
    return (datetime.utcnow() - created_at).total_seconds()


def _build(entry: dict, source: str, stale: bool) -> MusicResponse:
    RESPONSE_SOURCE.labels(source=source).inc()
    return MusicResponse(
        source=source,
        stale=stale,
        data_age_seconds=round(_age_seconds(entry["created_at"]), 3),
        fetched_at=entry["created_at"],
        data=entry["payload"],
    )


def _freshest(*candidates: tuple[dict | None, str]) -> tuple[dict, str] | None:
    present = [(e, s) for e, s in candidates if e is not None]
    if not present:
        return None
    return max(present, key=lambda c: c[0]["created_at"])


async def get_music(genre: str, background: BackgroundTasks) -> MusicResponse:
    key = genre.strip().lower()
    fresh = settings.freshness_seconds

    # 1) CACHE
    cached = cache.get(key)
    if cached and _age_seconds(cached["created_at"]) < fresh:
        CACHE_EVENTS.labels(result="hit").inc()
        return _build(cached, "cache", stale=False)
    CACHE_EVENTS.labels(result="miss").inc()

    # 2) DB (durable read tier)
    row = await run_in_threadpool(db.latest_for_genre, key)
    if row and _age_seconds(row["created_at"]) < fresh:
        cache.set(key, row)
        return _build(row, "db", stale=False)

    # 3) VENDOR
    try:
        payload, created = await vendor.fetch(key)
        entry = {"payload": payload, "created_at": created}
        cache.set(key, entry)
        background.add_task(db.insert_response, key, payload, created)  # non-blocking persist
        return _build(entry, "vendor", stale=False)
    except vendor.VendorError as e:
        log.warning("vendor failed genre=%s: %s", key, e)
        fallback = _freshest((cache.get(key), "cache"), (row, "db"))
        if fallback is not None:
            entry, source = fallback
            return _build(entry, source, stale=True)
        raise ServiceUnavailable("no cached data available") from e
