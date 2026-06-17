"""Thin Postgres access for the tool layer.

Tools never accept SQL from the model — every query lives in revenue_tools.py and
reads from the semantic views (vw_stay_night_base / vw_posted_stay_night /
vw_segment_stay_night), never from reservations_hackathon directly.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

DEFAULT_DATABASE_URL = "postgresql://hackathon:hackathon@localhost:5432/hotel_hackathon"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


@contextmanager
def get_conn():
    conn = psycopg.connect(get_database_url(), row_factory=dict_row)
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
    year, month = (int(x) for x in stay_month.split("-"))
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


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
