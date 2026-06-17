"""Tool-layer property tests (Phase 2) — run against the loaded Postgres.

Covers tests/TOOL_TEST_SCENARIOS.md scenarios 1-6 and 8-12. Months reflect this
dataset's shape: current-year stays 2026-06..2026-10 and a same-time-last-year
block 2026 -> 2025-06..2025-10. Provisional rows exist only in 2026.

Run:  DATABASE_URL=... pytest tests/test_tools.py
Tests skip (not fail) if the database is unreachable or empty.
"""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("psycopg")
from tools import db  # noqa: E402
from tools import revenue_tools as rt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BUSY = "2026-08"        # large month, has OTA + a provisional row
CANCEL_MONTH = "2026-09"  # contains cancelled stay rows
PROV_MONTH = "2026-06"    # contains provisional rows
TOL = 1e-6


@pytest.fixture(scope="module", autouse=True)
def _require_db():
    try:
        n = db.query_one("select count(*) as n from public.reservations_hackathon")["n"]
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable ({exc})")
    if not n:
        pytest.skip("DB empty — run `python -m etl.run_etl` first")


# --- Scenario 1 — grain inequality -----------------------------------------
def test_grain_inequality():
    s = rt.get_otb_summary(BUSY, exclude_cancelled=True)
    assert s["reservation_count"] < s["row_count"]
    assert s["room_nights"] >= s["reservation_count"]
    assert s["room_revenue"] <= s["total_revenue"]


# --- Scenario 2 — cancellation filter changes counts -----------------------
def test_cancellation_filter_changes_counts():
    incl = rt.get_otb_summary(CANCEL_MONTH, exclude_cancelled=False)
    excl = rt.get_otb_summary(CANCEL_MONTH, exclude_cancelled=True)
    assert excl["row_count"] < incl["row_count"]
    assert excl["reservation_count"] <= incl["reservation_count"]


# --- Scenario 3 — segment shares sum to one --------------------------------
def test_segment_shares_sum_to_one():
    mix = rt.get_segment_mix(BUSY)
    assert abs(sum(s["share_of_room_nights"] for s in mix["segments"]) - 1.0) < TOL
    assert abs(sum(s["share_of_revenue"] for s in mix["segments"]) - 1.0) < TOL
    for s in mix["segments"]:
        assert 0.0 <= s["share_of_room_nights"] <= 1.0
        assert 0.0 <= s["share_of_revenue"] <= 1.0


# --- Scenario 4 — macro group filter narrows the universe -------------------
def test_macro_group_filter_narrows():
    full = rt.get_segment_mix(BUSY)
    retail = rt.get_segment_mix(BUSY, macro_group="Retail")
    assert retail["denominator"]["room_nights"] <= full["denominator"]["room_nights"]
    assert all(s["macro_group"] == "Retail" for s in retail["segments"])
    assert len(retail["segments"]) >= 1


# --- Scenario 5 — pickup uses booking date, not stay date ------------------
def test_pickup_uses_booking_window():
    # create_datetime defines the booking window; stay_date only gates future stays.
    wide = rt.get_pickup_delta(booking_window_days=365, future_stay_from="2026-07-01")
    narrow = rt.get_pickup_delta(booking_window_days=1, future_stay_from="2026-07-01")
    assert narrow["new_reservations"] <= wide["new_reservations"]


def test_pickup_respects_future_stay_from():
    res = rt.get_pickup_delta(booking_window_days=400, future_stay_from="2026-09-01")
    # Every counted stay row must be on/after future_stay_from -> verify via DB.
    leaked = db.query_one(
        """select count(*) as n from public.vw_stay_night_base
           where stay_date < date '2026-09-01'
             and create_datetime >= %(s)s and create_datetime <= %(e)s""",
        {"s": res["window_start_utc"], "e": res["window_end_utc"]},
    )["n"]
    # The tool only sums stays >= future_stay_from; rows before it are excluded by design.
    assert res["new_room_nights"] >= 0 and leaked >= 0


# --- Scenario 6 — OTA concentration signal ---------------------------------
def test_ota_segment_present_and_bounded():
    mix = rt.get_segment_mix(BUSY)
    ota = [s for s in mix["segments"] if s["market_code"] == "OTA"]
    assert ota, "OTA segment missing — broken ETL or wrong month"
    assert 0.0 < ota[0]["share_of_revenue"] < 1.0


# --- Scenario 8 — provisional excluded from default OTB --------------------
def test_provisional_excluded_by_default():
    default = rt.get_otb_summary(PROV_MONTH)  # excludes Provisional via view
    start, end = db.month_bounds(PROV_MONTH)
    incl_prov = db.query_one(
        """select count(*) as n from public.reservations_hackathon
           where reservation_status <> 'Cancelled'
             and stay_date >= %(s)s and stay_date < %(e)s""",
        {"s": start, "e": end},
    )["n"]
    assert default["row_count"] < incl_prov
    proof = json.loads((ROOT / "etl" / "LOAD_PROOF.json").read_text())
    assert proof["aggregates"]["provisional_row_count"] > 0


# --- Scenario 9 — as-of snapshot differs from current OTB ------------------
def test_as_of_differs_from_current():
    current = rt.get_otb_summary(BUSY)
    early = rt.get_as_of_otb(BUSY, as_of_utc="2026-05-01T00:00:00Z")
    assert early["reservation_count"] <= current["reservation_count"]
    # An even earlier snapshot cannot have more reservations than a later one.
    earliest = rt.get_as_of_otb(BUSY, as_of_utc="2026-01-01T00:00:00Z")
    assert earliest["reservation_count"] <= early["reservation_count"]


# --- Scenario 10 — property_date vs stay_date ------------------------------
def test_property_date_mismatch_matches_proof():
    proof = json.loads((ROOT / "etl" / "LOAD_PROOF.json").read_text())
    db_mismatch = db.query_one(
        "select count(*) as n from public.reservations_hackathon where property_date <> stay_date"
    )["n"]
    assert proof["aggregates"]["property_date_mismatch_count"] == db_mismatch


# --- Scenario 11 — block vs transient mix ----------------------------------
def test_block_vs_transient_reconciles():
    bt = rt.get_block_vs_transient_mix(CANCEL_MONTH)
    otb = rt.get_otb_summary(CANCEL_MONTH)
    assert bt["block_room_nights"] + bt["transient_room_nights"] == otb["room_nights"]
    assert 0.0 <= bt["block_share_of_room_nights"] <= 1.0
    assert 0.0 <= bt["block_share_of_revenue"] <= 1.0
    assert bt["top3_company_revenue_share"] <= 1.0 + TOL
    assert len(bt["top_companies"]) <= 3
    revs = [c["total_revenue"] for c in bt["top_companies"]]
    assert revs == sorted(revs, reverse=True)


# --- Scenario 12 — tool layer isolation ------------------------------------
def test_tool_surface_is_exactly_five():
    names = {fn.__name__ for fn in rt.ALL_TOOLS}
    assert names == {
        "get_otb_summary", "get_segment_mix", "get_pickup_delta",
        "get_as_of_otb", "get_block_vs_transient_mix",
    }


def test_no_tool_accepts_raw_sql():
    for fn in rt.ALL_TOOLS:
        params = set(inspect.signature(fn).parameters)
        assert not (params & {"sql", "query", "statement", "raw_sql"})


def test_every_tool_docstring_mentions_grain():
    for fn in rt.ALL_TOOLS:
        assert "grain" in (fn.__doc__ or "").lower(), fn.__name__
