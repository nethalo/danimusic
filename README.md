# 🎵 DaniMusic Backend

A production-shaped FastAPI service that sits in front of the
[MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API) and serves music
metadata by genre — with a **layered read-through cache**, **graceful
degradation**, **Prometheus metrics**, and **structured logging**.

Ask for a genre, get the freshest data available from whichever tier can answer
fastest — and a truthful answer even when the upstream API is down.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Architecture](#architecture)
- [Request flow](#request-flow)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Endpoints](#endpoints)
- [Response shape](#response-shape)
- [Observability](#observability)
- [Configuration](#configuration)
- [Data model](#data-model)
- [Design decisions](#design-decisions)
- [Project layout](#project-layout)

---

## Why this exists

MusicBrainz is a great source of truth, but hammering it directly is a bad idea:
it enforces a **~1 request/second** limit, requires a descriptive `User-Agent`,
and — like any network dependency — it will occasionally be slow or unavailable.

This service turns that fragile, rate-limited dependency into a fast, resilient
internal API:

- **Fast** — most reads are served from an in-process cache in microseconds.
- **Resilient** — if the vendor times out, you still get the last known-good
  answer, clearly labelled with how old it is.
- **Observable** — every request, cache event, vendor call, and DB query is
  measured and exposed to Prometheus.
- **Honest** — a response always tells you *where* it came from and *how stale*
  it is; if there's genuinely nothing to serve, you get a clean `503`.

---

## Architecture

```
                         ┌─────────────────────────────────────────┐
   client ──HTTP──▶      │  FastAPI                                 │
                         │  ┌────────────────────────────────────┐ │
                         │  │ middleware: request-id · access log │ │
                         │  │             · HTTP metrics          │ │
                         │  └────────────────────────────────────┘ │
                         │        │                                 │
                         │   /music/{genre}   /health   /metrics    │
                         │        │                                 │
                         │        ▼                                 │
                         │   read-through service                   │
                         └────────┼───────────┬───────────┬────────┘
                                  ▼           ▼           ▼
                            ┌──────────┐ ┌─────────┐ ┌──────────────┐
                            │  CACHE   │ │  MySQL  │ │  MusicBrainz │
                            │ in-proc  │ │  8.4    │ │  (vendor)    │
                            │  TTL     │ │ durable │ │ throttled +  │
                            │          │ │  log    │ │ retry + T/O  │
                            └──────────┘ └─────────┘ └──────────────┘
                              fastest     survives      source of
                                          restart        truth
```

---

## Request flow

`GET /music/{genre}` resolves in tiers. `X` = `FRESHNESS_MINUTES`, `T` = `VENDOR_TIMEOUT_SECONDS`.

```
1. CACHE hit, age < X ............................ serve  (source=cache, stale=false)
2. else DB latest row, age < X .................. serve  (source=db,    stale=false, warms cache)
3. else call VENDOR within budget T
      ├─ success ....... persist(DB, async) + cache → serve (source=vendor, stale=false)
      └─ timeout/error → serve freshest CACHE/DB we have, ANY age
                          ├─ found → serve (stale=true, data_age_seconds=…)
                          └─ nothing → 503 "no cached data available"
```

Key properties:

- **Freshness is judged by each record's `created_at`, not by a cache clock** —
  so a stale row warmed into the cache at startup is never served as fresh.
- **Retries live inside the timeout budget `T`** — a cold-start caller still gets
  its retries, but total vendor wait can never blow past `T` before falling back.
- **Partial / malformed vendor bodies are validated and discarded** — a truncated
  stream or an HTML rate-limit page never enters the cache or the database.

---

## Requirements

The repository is **fully self-contained** — everything needed to build and run
the service ships in it. You do **not** need any prebuilt image or access to a
container registry.

**To run with Docker (recommended):**

- [Docker](https://docs.docker.com/get-docker/) 24+ with the Compose plugin
  (`docker compose version`). That's the only prerequisite.

**To run locally without Docker:**

- Python **3.12** (the version the dependencies are pinned for)
- A reachable **MySQL 8.4** instance
- The Python packages in [`requirements.txt`](requirements.txt) (pinned)

> **Where do the Docker images come from?**
> They are **not** stored in the repo or pulled from a private registry — they are
> produced on your own machine:
> - **`danimusic-api`** is *built locally* from the [`Dockerfile`](Dockerfile) the
>   first time you run `docker compose up --build` (the `--build` flag does it).
>   Because `requirements.txt` is pinned, everyone gets the same Python deps.
> - **`mysql:8.4`** is *pulled automatically* from Docker Hub by Compose.
>
> So a third party who only has the repo URL just needs Docker — no image handoff:
> ```bash
> git clone git@github.com:nethalo/danimusic.git
> cd danimusic
> docker compose up --build
> ```
> Built images live in Docker's local store (`docker images`), inside the Docker
> Desktop VM — not on your filesystem and not in git.
>
> *Reproducible, not bit-identical:* pinned pip versions guarantee the same
> dependencies, but the base images (`python:3.12-slim`, `mysql:8.4`) can change
> over time — pin them by digest if you need byte-exact builds.

---

## Quick start

### With Docker (recommended — pins every dependency and MySQL 8.4)

```bash
docker compose up --build
```

- API      → http://localhost:8000
- Swagger  → http://localhost:8000/docs
- Metrics  → http://localhost:8000/metrics

MySQL 8.4 starts first; the API waits for its healthcheck before booting.

```bash
# try it
curl -s http://localhost:8000/music/rock | jq

# tear down (add -v to also drop the MySQL volume)
docker compose down
```

### Locally, without Docker

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # point DB_URL at your own MySQL 8.4
uvicorn app.main:app --reload
```

> **Note:** dependencies are pinned for Python 3.12 (the Docker base image).
> Newer interpreters may lack prebuilt wheels for some pinned versions.

---

## Endpoints

| Method | Path             | Description                                                            |
|--------|------------------|-----------------------------------------------------------------------|
| `GET`  | `/music/{genre}` | Metadata for a genre. `genre`: string, 1–20 chars, `[A-Za-z0-9 &+/'-]`. |
| `GET`  | `/health`        | Always `200`; `database: up\|down` reflects readiness.                 |
| `GET`  | `/metrics`       | Prometheus exposition.                                                 |
| `GET`  | `/docs`          | Interactive Swagger UI.                                                |

Invalid genres (too long / bad characters) are rejected with `422` before any
work is done. The endpoint is rate-limited per client IP (`INBOUND_RATE_LIMIT`).

---

## Response shape

```jsonc
// GET /music/rock
{
  "source": "cache",            // cache | db | vendor — which tier answered
  "stale": false,               // true only when served as a failure fallback
  "data_age_seconds": 0.026,    // how old the served data actually is
  "fetched_at": "2026-07-21T23:44:32.889000",
  "data": {                     // raw MusicBrainz payload
    "count": 572034,
    "release-groups": [ /* … */ ]
  }
}
```

When the vendor is unreachable and there's nothing cached:

```jsonc
// 503 Service Unavailable
{ "detail": "no cached data available" }
```

---

## Observability

**Structured JSON logs** to stdout, one access line per request, each carrying a
`request_id` (also returned in the `X-Request-ID` response header).

**Prometheus metrics** at `/metrics`:

| Metric                              | Type      | Purpose                              |
|-------------------------------------|-----------|--------------------------------------|
| `http_requests_total`               | counter   | Requests by method / endpoint / status |
| `http_requests_errors_total`        | counter   | Responses with status ≥ 500          |
| `http_request_duration_seconds`     | histogram | End-to-end request latency           |
| `vendor_request_duration_seconds`   | histogram | MusicBrainz latency by outcome       |
| `vendor_request_errors_total`       | counter   | Vendor failures by kind              |
| `cache_events_total`                | counter   | Cache hits vs misses                 |
| `response_source_total`             | counter   | Responses served per tier            |
| `db_query_duration_seconds`         | histogram | SQL query latency (instrumented driver) |
| `db_pool_connections_in_use`        | gauge     | Live checked-out DB connections      |

Endpoint labels use the **route template** (`/music/{genre}`), not the raw path,
to keep metric cardinality bounded.

---

## Configuration

Every knob is an environment variable (see [`.env.example`](.env.example)):

| Variable                        | Default                          | Meaning                                   |
|---------------------------------|----------------------------------|-------------------------------------------|
| `DB_URL`                        | `mysql+pymysql://…/musicdb`      | SQLAlchemy connection URL                 |
| `MB_USER_AGENT`                 | `DaniMusic/1.0 ( … )`            | Required by MusicBrainz                   |
| `MB_ENTITY`                     | `release-group`                  | Which MB entity to search                 |
| `FRESHNESS_MINUTES`             | `10`                             | Freshness window `X`                      |
| `VENDOR_TIMEOUT_SECONDS`        | `5`                              | Total vendor budget `T`                   |
| `VENDOR_MAX_ATTEMPTS`           | `2`                              | Tries per fetch (backoff, inside `T`)     |
| `OUTBOUND_MIN_INTERVAL_SECONDS` | `1`                              | Throttle to respect MB's ~1 req/s         |
| `INBOUND_RATE_LIMIT`            | `30/minute`                      | Per-IP inbound limit (slowapi syntax)     |
| `GENRE_MAX_LENGTH`              | `20`                             | Max genre length accepted                 |

---

## Data model

A single append-only table doubles as an audit log **and** the durable read tier:

```sql
CREATE TABLE responses (
    id         BIGINT       NOT NULL AUTO_INCREMENT,   -- surrogate PK (collision-safe)
    genre      VARCHAR(20)  NOT NULL,
    created_at DATETIME(6)  NOT NULL,                  -- microsecond precision
    payload    JSON         NOT NULL,                  -- raw vendor response
    PRIMARY KEY (id),
    KEY ix_responses_genre_created (genre, created_at) -- "latest per genre" is a cheap scan
);
```

The table is created automatically on startup; `schema.sql` is provided for
reference. "Latest row for a genre" is
`WHERE genre = ? ORDER BY created_at DESC LIMIT 1` — served by the index prefix.

---

## Design decisions

- **One middleware, no instrumentator dependency.** Request-id, access logging,
  and the three required HTTP metrics are handled by a single `BaseHTTPMiddleware`.
- **No message queue.** In a synchronous request/response path it buys nothing;
  the DB write is a fire-and-forget `BackgroundTask` — the lightweight equivalent.
- **Sync SQLAlchemy + PyMySQL, offloaded to a threadpool.** Pure-Python, handles
  MySQL 8.4's `caching_sha2_password` cleanly, and sidesteps async-driver auth
  pitfalls — while `run_in_threadpool` keeps the event loop free.
- **Two independent rate limits.** Inbound (protect this service) and outbound
  (respect MusicBrainz).
- **Freshness by timestamp, one knob.** `FRESHNESS_MINUTES` governs the fresh-serve
  window for both cache and DB, compared against each record's real age.

---

## Project layout

```
app/
  config.py        # all tunables (pydantic-settings)
  main.py          # app wiring, lifespan (init DB, warm cache), routes
  middleware.py    # request-id + access log + HTTP metrics
  metrics.py       # Prometheus metric definitions
  logging_setup.py # JSON logging
  ratelimit.py     # slowapi limiter
  cache.py         # in-process TTL cache
  db.py            # SQLAlchemy models, queries, engine instrumentation
  vendor.py        # MusicBrainz client: throttle, retry, timeout, validation
  service.py       # the CACHE → DB → vendor read-through logic
  models.py        # Pydantic request/response schemas
  routes/
    music.py       # GET /music/{genre}
    health.py      # GET /health
Dockerfile
docker-compose.yml # api + mysql:8.4
requirements.txt   # pinned
schema.sql
.env.example
```

---

Built as a demonstration of a resilient, observable API gateway pattern over a
public, rate-limited third-party service.
