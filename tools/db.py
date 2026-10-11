"""Thin Postgres access for the tool layer.

Tools never accept SQL from the model — every query lives in revenue_tools.py and
reads from the semantic views (vw_stay_night_base / vw_posted_stay_night /
vw_segment_stay_night), never from reservations_hackathon directly.

Every tool connection is opened read-only with a statement timeout, so the tool
layer cannot modify data even if the configured role is allowed to.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

DEFAULT_DATABASE_URL = "postgresql://hackathon:hackathon@localhost:5432/hotel_hackathon"
STATEMENT_TIMEOUT_MS = int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "10000"))
_STAY_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def get_database_url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip() or DEFAULT_DATABASE_URL


@contextmanager
def get_conn():
    conn = psycopg.connect(get_database_url(), row_factory=dict_row, connect_timeout=15)
    conn.read_only = True
    conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
    try:
        yield conn
    finally:
        conn.close()


def query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def query_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = query(sql, params)
    return rows[0] if rows else {}


def month_bounds(stay_month: str) -> tuple[date, date]:
    """'YYYY-MM' -> (first day of month, first day of next month)."""
    if not isinstance(stay_month, str) or not _STAY_MONTH.match(stay_month):
        raise ValueError("stay_month must be 'YYYY-MM', e.g. '2026-08'")
    year, month = (int(x) for x in stay_month.split("-"))
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def data_as_of() -> datetime:
    """The moment the loaded dataset describes ("now" for the data).

    Booking-pace windows are measured back from this, not from the wall clock, so
    a snapshot loaded some time ago still answers "what was booked in the last 7
    days" meaningfully. It is the latest load's scrape time; DATA_AS_OF (ISO-8601)
    overrides it, and the wall clock is the fallback when no load is recorded.
    """
    override = (os.environ.get("DATA_AS_OF") or "").strip()
    if override:
        parsed = datetime.fromisoformat(override.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    row = query_one("select max(scraped_at) as as_of from public.load_manifest")
    return row.get("as_of") or datetime.now(timezone.utc)


def f(value: Any) -> float:
    """Normalise Decimal/None numerics to float for JSON-friendly tool output."""
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def ratio(numerator: Any, denominator: Any) -> float:
    n, d = f(numerator), f(denominator)
    return n / d if d else 0.0
