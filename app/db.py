"""MySQL access layer: append-only response log that doubles as the durable
read tier, plus SQLAlchemy engine instrumentation feeding Prometheus.

Sync SQLAlchemy + PyMySQL is used on purpose (pure-Python, handles MySQL 8.4
caching_sha2_password via `cryptography`, no async-driver auth pitfalls). The
async service offloads these calls with starlette.run_in_threadpool so the event
loop is never blocked."""

import logging
import time
from datetime import datetime

from sqlalchemy import BigInteger, Index, String, create_engine, desc, event, func, select
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings
from .metrics import DB_POOL_INUSE, DB_QUERY_LATENCY

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class ResponseLog(Base):
    __tablename__ = "responses"

    # Surrogate PK (your call) — avoids (genre, timestamp) collision under retries.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    genre: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (Index("ix_responses_genre_created", "genre", "created_at"),)


engine = create_engine(
    settings.db_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


# --- instrumentation: per-query latency + live pool gauge ---
@event.listens_for(engine, "before_cursor_execute")
def _before_cursor(conn, cursor, statement, params, context, executemany):
    context._q_start = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _after_cursor(conn, cursor, statement, params, context, executemany):
    dur = time.perf_counter() - getattr(context, "_q_start", time.perf_counter())
    op = statement.lstrip().split(" ", 1)[0].lower() or "unknown"
    DB_QUERY_LATENCY.labels(operation=op).observe(dur)


@event.listens_for(engine, "checkout")
def _on_checkout(dbapi_conn, conn_record, conn_proxy):
    DB_POOL_INUSE.set(engine.pool.checkedout())


@event.listens_for(engine, "checkin")
def _on_checkin(dbapi_conn, conn_record):
    DB_POOL_INUSE.set(engine.pool.checkedout())


def init_db() -> None:
    Base.metadata.create_all(engine)


def insert_response(genre: str, payload: dict, created_at: datetime) -> None:
    with SessionLocal() as s:
        s.add(ResponseLog(genre=genre, payload=payload, created_at=created_at))
        s.commit()


def latest_for_genre(genre: str) -> dict | None:
    with SessionLocal() as s:
        row = s.execute(
            select(ResponseLog)
            .where(ResponseLog.genre == genre)
            .order_by(desc(ResponseLog.created_at))
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return {"payload": row.payload, "created_at": row.created_at}


def latest_all() -> list[dict]:
    """Latest row per genre — used once at startup to warm the cache."""
    with SessionLocal() as s:
        newest = (
            select(ResponseLog.genre, func.max(ResponseLog.created_at).label("mx"))
            .group_by(ResponseLog.genre)
            .subquery()
        )
        rows = s.execute(
            select(ResponseLog).join(
                newest,
                (ResponseLog.genre == newest.c.genre)
                & (ResponseLog.created_at == newest.c.mx),
            )
        ).scalars().all()
        return [{"genre": r.genre, "payload": r.payload, "created_at": r.created_at} for r in rows]


def ping() -> None:
    with engine.connect() as c:
        c.exec_driver_sql("SELECT 1")
