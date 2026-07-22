from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from .. import db
from ..models import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness always 200 (process is up); `database` reflects readiness."""
    try:
        await run_in_threadpool(db.ping)
        return HealthResponse(status="ok", database="up")
    except Exception:
        return HealthResponse(status="degraded", database="down")
