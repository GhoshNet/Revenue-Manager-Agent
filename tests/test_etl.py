"""ETL property tests (Phase 1) — run against the loaded Postgres.

Covers tests/ETL_TEST_SCENARIOS.md:
  Scenario 1 — lookup row counts
  Scenario 2 — fact-table grain uniqueness
  Scenario 3 — manifest / LOAD_PROOF reconciliation
  Scenario 4 — stay-row expansion (multi-night reservation)

Run:  DATABASE_URL=... pytest tests/test_etl.py
Tests skip (not fail) if the database is unreachable or empty.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[1]
DB_URL = os.environ.get("DATABASE_URL", "postgresql://hackathon:hackathon@localhost:5432/hotel_hackathon")


@pytest.fixture(scope="module")
def conn():
    try:
        c = psycopg.connect(DB_URL, connect_timeout=5)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable ({exc})")
    with c.cursor() as cur:
        cur.execute("select count(*) from public.reservations_hackathon")
        if cur.fetchone()[0] == 0:
            pytest.skip("reservations_hackathon empty — run `python -m etl.run_etl` first")
    yield c
    c.close()


def _scalar(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


# --- Scenario 1 — lookup row counts ----------------------------------------
@pytest.mark.parametrize(
    "table,expected",
    [
        ("room_type_lookup", 3),
        ("rate_plan_lookup", 8),
        ("market_code_lookup", 10),
        ("market_macro_group_history", 11),
        ("channel_code_lookup", 4),
    ],
)
def test_lookup_row_counts(conn, table, expected):
    assert _scalar(conn, f"select count(*) from public.{table}") == expected


# --- Scenario 2 — fact-table grain uniqueness ------------------------------
def test_fact_grain_unique(conn):
    dupes = _scalar(
        conn,
        """select count(*) from (
             select reservation_id, stay_date
             from public.reservations_hackathon
             group by reservation_id, stay_date
             having count(*) > 1
           ) d""",
    )
    assert dupes == 0


def test_row_count_exceeds_reservation_count(conn):
    rows = _scalar(conn, "select count(*) from public.reservations_hackathon")
    res = _scalar(conn, "select count(distinct reservation_id) from public.reservations_hackathon")
    assert rows >= res
    assert res > 0


# --- Scenario 3 — manifest / LOAD_PROOF reconciliation ---------------------
def test_manifest_reconciles_with_db(conn):
    manifest = json.loads((ROOT / "etl" / "SCRAPE_MANIFEST.json").read_text())
    db_ids = _scalar(conn, "select count(distinct reservation_id) from public.reservations_hackathon")
    assert manifest["reservation_ids_count"] == db_ids
    db_rows = _scalar(conn, "select count(*) from public.reservations_hackathon")
    assert manifest["total_stay_rows"] == db_rows


def test_load_proof_matches_db(conn):
    proof = json.loads((ROOT / "etl" / "LOAD_PROOF.json").read_text())
    db_rows = _scalar(conn, "select count(*) from public.reservations_hackathon")
    assert proof["row_counts"]["reservations_hackathon"] == db_rows
    # dataset_revision in LOAD_PROOF must match the latest load_manifest row
    db_rev = _scalar(conn, "select dataset_revision from public.load_manifest order by load_id desc limit 1")
    assert proof["dataset_revision"] == db_rev


def test_fingerprint_matches_manifest_row_hash(conn):
    proof = json.loads((ROOT / "etl" / "LOAD_PROOF.json").read_text())
    db_hash = _scalar(conn, "select row_hash from public.load_manifest order by load_id desc limit 1")
    assert proof["reservation_stay_status_sha256"] == db_hash


# --- Scenario 4 — stay-row expansion ---------------------------------------
def test_stay_row_expansion(conn):
    # A normal multi-night reservation has exactly `nights` distinct stay rows.
    with conn.cursor() as cur:
        cur.execute(
            """select reservation_id, nights, count(*) as rows
                 from public.reservations_hackathon
                 where reservation_id like 'R0%'
                 group by reservation_id, nights
                 having nights > 1
                 order by reservation_id
                 limit 1"""
        )
        rid, nights, rows = cur.fetchone()
    assert rows == nights, f"{rid}: {rows} stay rows != nights {nights}"
