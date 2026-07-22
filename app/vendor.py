"""MusicBrainz client: throttled, bounded-retry, timeout-budgeted, and it
validates the body so partial/malformed responses are DISCARDED (never cached
or persisted) — they surface as a VendorError and the caller falls back."""

import asyncio
import logging
import random
import time
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from .config import settings
from .metrics import VENDOR_ERRORS, VENDOR_LATENCY

log = logging.getLogger(__name__)


class VendorError(Exception):
    """Base: vendor did not give us a usable answer."""


class VendorUnavailable(VendorError):
    """Transient — timeout, 5xx, connection error."""


class VendorBadResponse(VendorError):
    """Partial/malformed body — discard, do not retry."""


class _MBSearchBody(BaseModel):
    """Minimal shape proving this is a real MB search body. `count` present +
    integer is enough to reject HTML error pages / truncated JSON."""

    model_config = ConfigDict(extra="allow")
    count: int


_client: httpx.AsyncClient | None = None
_throttle_lock = asyncio.Lock()
_last_request_monotonic: float = 0.0

_LUCENE_SPECIALS = set(r'+-&|!(){}[]^"~*?:\/')


async def startup() -> None:
    global _client
    _client = httpx.AsyncClient(
        base_url=settings.mb_base_url,
        headers={"User-Agent": settings.mb_user_agent},
    )


async def shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _throttle() -> None:
    """Serialize outbound calls to ~1 req/s to respect MusicBrainz."""
    global _last_request_monotonic
    async with _throttle_lock:
        wait = settings.outbound_min_interval_seconds - (time.monotonic() - _last_request_monotonic)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_monotonic = time.monotonic()


def _escape_lucene(value: str) -> str:
    return "".join("\\" + c if c in _LUCENE_SPECIALS else c for c in value)


async def fetch(genre: str) -> tuple[dict, datetime]:
    """Return (payload, fetched_at). Raises VendorError on failure.

    All attempts share one deadline of vendor_timeout_seconds (T). A cold-start
    caller still gets its retries — they're inside T, not unbounded."""
    if _client is None:
        raise VendorUnavailable("client not initialized")

    params = {
        "query": f'genre:"{_escape_lucene(genre)}"',
        "fmt": "json",
        "limit": settings.mb_result_limit,
    }
    deadline = time.monotonic() + settings.vendor_timeout_seconds
    last_exc: VendorError | None = None

    for attempt in range(1, settings.vendor_max_attempts + 1):
        await _throttle()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        start = time.perf_counter()
        try:
            resp = await _client.get(f"/{settings.mb_entity}", params=params, timeout=remaining)
        except httpx.TimeoutException as e:
            VENDOR_LATENCY.labels(outcome="timeout").observe(time.perf_counter() - start)
            VENDOR_ERRORS.labels(kind="timeout").inc()
            last_exc = VendorUnavailable(f"timeout: {e}")
        except httpx.HTTPError as e:
            VENDOR_LATENCY.labels(outcome="error").observe(time.perf_counter() - start)
            VENDOR_ERRORS.labels(kind="connection").inc()
            last_exc = VendorUnavailable(f"connection: {e}")
        else:
            status = resp.status_code
            if status >= 500:
                VENDOR_LATENCY.labels(outcome="error").observe(time.perf_counter() - start)
                VENDOR_ERRORS.labels(kind=f"http_{status}").inc()
                last_exc = VendorUnavailable(f"http {status}")
            elif status >= 400:
                # 4xx (e.g. bad query) won't fix on retry — discard, fall back.
                VENDOR_ERRORS.labels(kind=f"http_{status}").inc()
                raise VendorBadResponse(f"http {status}")
            else:
                try:
                    data = resp.json()
                    _MBSearchBody.model_validate(data)
                except (ValueError, ValidationError) as e:
                    VENDOR_ERRORS.labels(kind="bad_response").inc()
                    raise VendorBadResponse(f"partial/invalid body: {e}") from e
                VENDOR_LATENCY.labels(outcome="success").observe(time.perf_counter() - start)
                return data, datetime.utcnow()

        # bounded backoff before next attempt, never exceeding the remaining budget
        if attempt < settings.vendor_max_attempts:
            backoff = settings.vendor_backoff_base_seconds * (2 ** (attempt - 1)) + random.uniform(0, 0.1)
            backoff = min(backoff, max(deadline - time.monotonic(), 0.0))
            if backoff > 0:
                await asyncio.sleep(backoff)

    raise last_exc or VendorUnavailable("vendor failed")
