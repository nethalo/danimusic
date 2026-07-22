"""One cross-cutting middleware: request-id, structured access log, and the
three required HTTP metrics. Uses the matched route template (not the raw path)
as the endpoint label to keep cardinality bounded."""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .metrics import REQUEST_LATENCY, REQUESTS_ERRORS, REQUESTS_TOTAL

access_log = logging.getLogger("access")


class ContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = rid
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            self._record(request, 500, time.perf_counter() - start, rid)
            raise

        self._record(request, response.status_code, time.perf_counter() - start, rid)
        response.headers["X-Request-ID"] = rid
        return response

    @staticmethod
    def _record(request: Request, status: int, duration: float, rid: str) -> None:
        route = request.scope.get("route")
        endpoint = getattr(route, "path", request.url.path)
        method = request.method
        REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status=status).inc()
        REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(duration)
        if status >= 500:
            REQUESTS_ERRORS.labels(method=method, endpoint=endpoint).inc()
        access_log.info(
            "request",
            extra={
                "request_id": rid,
                "method": method,
                "endpoint": endpoint,
                "status": status,
                "duration_ms": round(duration * 1000, 2),
            },
        )
