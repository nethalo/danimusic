import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from . import cache, db, vendor
from .logging_setup import setup_logging
from .middleware import ContextMiddleware
from .ratelimit import limiter
from .routes import health, music

setup_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    await vendor.startup()
    try:
        items = db.latest_all()
        cache.warm(items)  # the one time DB populates the cache
        log.info("cache warmed from db", extra={"entries": len(items)})
    except Exception as e:  # a cold/empty DB must not block startup
        log.warning("cache warm skipped: %s", e)
    yield
    await vendor.shutdown()
    db.engine.dispose()


app = FastAPI(
    title="DaniMusic Backend",
    version="1.0.0",
    description="MusicBrainz-backed service: CACHE -> DB -> vendor read-through, "
    "graceful degradation, Prometheus metrics.",
    lifespan=lifespan,
)

# inbound rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# request-id + access log + HTTP metrics
app.add_middleware(ContextMiddleware)

app.include_router(music.router)
app.include_router(health.router)


# Prometheus exposition (default registry) — direct route, no mount/redirect
@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
