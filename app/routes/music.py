from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, Request

from ..config import settings
from ..models import MusicResponse
from ..ratelimit import limiter
from ..service import ServiceUnavailable, get_music

router = APIRouter()

# Pydantic validation: string, 1..N chars, restricted charset (blocks Lucene
# injection before it reaches the query builder).
_GENRE_PATTERN = r"^[A-Za-z0-9 &+/'-]{1,%d}$" % settings.genre_max_length


@router.get("/music/{genre}", response_model=MusicResponse)
@limiter.limit(settings.inbound_rate_limit)
async def music(
    request: Request,
    background_tasks: BackgroundTasks,
    genre: str = Path(..., min_length=1, max_length=settings.genre_max_length, pattern=_GENRE_PATTERN),
):
    try:
        return await get_music(genre, background_tasks)
    except ServiceUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
