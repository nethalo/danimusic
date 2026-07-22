from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunables live here. Every field is overridable via an env var of the
    same name (case-insensitive), e.g. DB_URL, FRESHNESS_MINUTES, VENDOR_TIMEOUT_SECONDS."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- Vendor (MusicBrainz) ---
    mb_base_url: str = "https://musicbrainz.org/ws/2"
    mb_entity: str = "release-group"
    mb_user_agent: str = "DaniMusic/1.0 ( nethalo@gmail.com )"
    mb_result_limit: int = 25
    vendor_timeout_seconds: float = 5.0          # total budget T for a vendor fetch
    vendor_max_attempts: int = 2                 # 1 try + 1 retry, all inside T
    vendor_backoff_base_seconds: float = 0.3
    outbound_min_interval_seconds: float = 1.0   # ~1 req/s throttle to respect MB

    # --- Freshness / cache ---
    freshness_minutes: float = 10.0              # single knob X
    cache_max_size: int = 1024

    # --- Inbound rate limit (protects our own API) ---
    inbound_rate_limit: str = "30/minute"

    # --- Validation ---
    genre_max_length: int = 20

    # --- Database ---
    db_url: str = "mysql+pymysql://music:music@localhost:3306/musicdb"
    db_pool_size: int = 5
    db_max_overflow: int = 5

    @property
    def freshness_seconds(self) -> float:
        return self.freshness_minutes * 60


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
