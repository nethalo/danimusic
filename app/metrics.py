"""Prometheus metrics. Registered on the default registry, which the /metrics
ASGI app exposes. The three the spec explicitly requires are the http_* ones."""

from prometheus_client import Counter, Gauge, Histogram

# --- Required: total requests, error requests, response latency ---
REQUESTS_TOTAL = Counter(
    "http_requests_total", "Total HTTP requests",
    ["method", "endpoint", "status"],
)
REQUESTS_ERRORS = Counter(
    "http_requests_errors_total", "HTTP responses with status >= 500",
    ["method", "endpoint"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency",
    ["method", "endpoint"],
)

# --- Vendor (MusicBrainz) ---
VENDOR_LATENCY = Histogram(
    "vendor_request_duration_seconds", "MusicBrainz request latency",
    ["outcome"],
)
VENDOR_ERRORS = Counter(
    "vendor_request_errors_total", "MusicBrainz request failures",
    ["kind"],
)

# --- Cache / DB / source ---
CACHE_EVENTS = Counter("cache_events_total", "Cache lookups", ["result"])  # hit|miss
RESPONSE_SOURCE = Counter("response_source_total", "Where a response was served from", ["source"])
DB_QUERY_LATENCY = Histogram("db_query_duration_seconds", "DB query latency", ["operation"])
DB_POOL_INUSE = Gauge("db_pool_connections_in_use", "Checked-out DB pool connections")
