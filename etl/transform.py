"""Transform step: raw scrape dict -> typed records matching schema.sql.

Enforces the fact-table grain (one row per reservation_id x stay_date), parses
types, and computes the reservation_stay_status fingerprint that must match the
data site's /verify reservation_stay_status_sha256.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any


def _d(s: str | None) -> date | None:
    if not s or s == "—":
        return None
    return date.fromisoformat(s)


def _ts(s: str | None) -> datetime | None:
    if not s or s == "—":
        return None
    # Action JSON uses ...Z; fromisoformat handles 'Z' on Python >= 3.11.
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _int(v: Any) -> int:
    return int(str(v).strip())


def _num(v: Any) -> float:
    return float(str(v).strip().replace(",", ""))


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() == "true"


# ---- lookups (from /reference DOM rows, uppercase headers) -----------------
def _room_types(rows):
    return [
        {
            "space_type": r["SPACE_TYPE"],
            "room_class": r["ROOM_CLASS"],
            "display_name": r["DISPLAY_NAME"],
            "number_of_rooms": _int(r["NUMBER_OF_ROOMS"]),
        }
        for r in rows
    ]


def _markets(rows):
    return [
        {
            "market_code": r["MARKET_CODE"],
            "market_name": r["MARKET_NAME"],
            "macro_group": r["MACRO_GROUP"],
            "description": r.get("DESCRIPTION"),
        }
        for r in rows
    ]


def _channels(rows):
    return [
        {
            "channel_code": r["CHANNEL_CODE"],
            "channel_name": r["CHANNEL_NAME"],
            "channel_group": r["CHANNEL_GROUP"],
        }
        for r in rows
    ]


def _rate_plans(rows):
    return [
        {
            "rate_plan_code": r["RATE_PLAN_CODE"],
            "plan_family": r["PLAN_FAMILY"],
            "is_commissionable": _bool(r["IS_COMMISSIONABLE"]),
        }
        for r in rows
    ]


def _macro_history(rows):
    return [
        {
            "market_code": r["MARKET_CODE"],
            "valid_from": _d(r["VALID_FROM"]),
            "valid_to": _d(r["VALID_TO"]),
            "macro_group": r["MACRO_GROUP"],
        }
        for r in rows
    ]


# ---- fact rows -------------------------------------------------------------
def _fact_rows(reservations: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in reservations:
        for s in r["stay_rows"]:
            out.append(
                {
                    "reservation_id": r["reservation_id"],
                    "arrival_date": _d(r["arrival_date"]),
                    "departure_date": _d(r["departure_date"]),
                    "stay_date": _d(s["stay_date"]),
                    "property_date": _d(s["property_date"]),
                    "reservation_status": r["reservation_status"],
                    "financial_status": s["financial_status"],
                    "create_datetime": _ts(r["create_datetime"]),
                    "cancellation_datetime": _ts(r.get("cancellation_datetime")),
                    "guest_country": r.get("guest_country"),
                    "is_block": _bool(r["is_block"]),
                    "is_walk_in": _bool(r["is_walk_in"]),
                    "number_of_spaces": _int(r["number_of_spaces"]),
                    "space_type": r["space_type"],
                    "market_code": r["market_code"],
                    "channel_code": r["channel_code"],
                    "source_name": r["source_name"],
                    "rate_plan_code": r["rate_plan_code"],
                    "daily_room_revenue_before_tax": _num(s["daily_room_revenue_before_tax"]),
                    "daily_total_revenue_before_tax": _num(s["daily_total_revenue_before_tax"]),
                    "nights": _int(r["nights"]),
                    "adr_room": _num(r["adr_room"]),
                    "lead_time": _int(r["lead_time"]),
                    "company_name": r.get("company_name"),
                    "travel_agent_name": r.get("travel_agent_name"),
                }
            )
    return out


def fingerprint(fact_rows: list[dict]) -> str:
    """SHA-256 over sorted 'reservation_id|stay_date|financial_status' lines —
    matches the data site /verify reservation_stay_status_sha256 and the brief's
    compute_load_fingerprint.py."""
    lines = sorted(
        f"{r['reservation_id']}|{r['stay_date'].isoformat()}|{r['financial_status']}"
        for r in fact_rows
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def reservation_ids_sha256(fact_rows: list[dict]) -> tuple[int, str]:
    """Count + SHA-256 of sorted distinct reservation_id lines (SCRAPE_MANIFEST)."""
    ids = sorted({r["reservation_id"] for r in fact_rows})
    payload = "\n".join(ids).encode("utf-8")
    return len(ids), hashlib.sha256(payload).hexdigest()


def transform(raw: dict[str, Any]) -> dict[str, Any]:
    ref = raw["reference"]
    lookups = {
        "room_type_lookup": _room_types(ref["room_type_lookup"]),
        "rate_plan_lookup": _rate_plans(ref["rate_plan_lookup"]),
        "market_code_lookup": _markets(ref["market_code_lookup"]),
        "channel_code_lookup": _channels(ref["channel_code_lookup"]),
        "market_macro_group_history": _macro_history(ref["market_macro_group_history"]),
    }
    facts = _fact_rows(raw["reservations"])
    return {
        "anchor_date": raw["anchor_date"],
        "dataset_revision": raw["dataset_revision"],
        "scraped_at": raw["scraped_at"],
        "source_url": raw["source_url"],
        "lookups": lookups,
        "fact_rows": facts,
        "fingerprint": fingerprint(facts),
        "verify": raw.get("verify", {}),
    }
