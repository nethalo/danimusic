from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class MusicResponse(BaseModel):
    source: Literal["cache", "db", "vendor"] = Field(description="Which tier served this")
    stale: bool = Field(description="True when served as a timeout/failure fallback")
    data_age_seconds: float = Field(description="How old the served data is")
    fetched_at: datetime
    data: dict[str, Any]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["up", "down"]
