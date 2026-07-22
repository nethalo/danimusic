"""Inbound rate limiting (protects our API). Separate module to avoid a
circular import between main and the routes."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import settings

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.inbound_rate_limit])
