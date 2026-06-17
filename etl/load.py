"""Load step: idempotent truncate-and-reload of the typed records into Postgres.

Idempotency: the whole load runs in one transaction that TRUNCATEs every data
table and reinserts, so re-running yields an identical database (no duplicates).

Rate-plan FK: the shipped data uses ~16 granular commercial rate codes
(GOORO, BARCBB, EXPBARH, …) while rate_plan_lookup is exactly 8 canonical
families with no published mapping. schema.sql's FK cannot hold against the real
data, and the /verify fingerprint is computed over
reservation_id|stay_date|financial_status (not rate_plan_code), so we keep the
real granular value and drop ONLY that FK (documented in ARCHITECTURE.md).
"""
from __future__ import annotations

import os
from typing import Any

import psycopg

DEFAULT_DATABASE_URL = "postgresql://hackathon:hackathon@localhost:5432/hotel_hackathon"

# Insert column order per table (excludes generated identity columns).
COLUMNS: dict[str, list[str]] = {
    "room_type_lookup": ["space_type", "room_class", "display_name", "number_of_rooms"],
    "rate_plan_lookup": ["rate_plan_code", "plan_family", "is_commissionable"],
    "market_code_lookup": ["market_code", "market_name", "macro_group", "description"],
    "channel_code_lookup": ["channel_code", "channel_name", "channel_group"],
    "market_macro_group_history": ["market_code", "valid_from", "valid_to", "macro_group"],
    "reservations_hackathon": [
        "reservation_id", "arrival_date", "departure_date", "stay_date",
        "property_date", "reservation_status", "financial_status",
        "create_datetime", "cancellation_datetime", "guest_country", "is_block",
        "is_walk_in", "number_of_spaces", "space_type", "market_code",
        "channel_code", "source_name", "rate_plan_code",
        "daily_room_revenue_before_tax", "daily_total_revenue_before_tax",
        "nights", "adr_room", "lead_time", "company_name", "travel_agent_name",
    ],
}

# Insert order respects FK dependencies (lookups before fact, market before history).
TRUNCATE_ORDER = [
    "reservations_hackathon",
    "market_macro_group_history",
    "room_type_lookup",
    "rate_plan_lookup",
    "market_code_lookup",
    "channel_code_lookup",
    "load_manifest",
]
INSERT_ORDER = [
    "room_type_lookup",
    "rate_plan_lookup",
    "market_code_lookup",
    "channel_code_lookup",
    "market_macro_group_history",
    "reservations_hackathon",
]


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def _insert_many(cur, table: str, rows: list[dict[str, Any]]) -> int:
    cols = COLUMNS[table]
    if not rows:
        return 0
    placeholders = ", ".join(["%s"] * len(cols))
    sql = f"insert into public.{table} ({', '.join(cols)}) values ({placeholders})"
    cur.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    return len(rows)


def load(transformed: dict[str, Any], database_url: str | None = None) -> dict[str, Any]:
    database_url = database_url or get_database_url()
    lookups = transformed["lookups"]
    facts = transformed["fact_rows"]
    counts: dict[str, int] = {}

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            # Drop the impossible-to-satisfy rate_plan FK (documented).
            cur.execute(
                "alter table public.reservations_hackathon "
                "drop constraint if exists reservations_hackathon_rate_plan_code_fkey"
            )
            # Clean slate (idempotent).
            cur.execute(
                "truncate "
                + ", ".join(f"public.{t}" for t in TRUNCATE_ORDER)
                + " restart identity cascade"
            )
            # Insert lookups then fact rows.
            for table in INSERT_ORDER:
                data = facts if table == "reservations_hackathon" else lookups[table]
                counts[table] = _insert_many(cur, table, data)
            # One manifest row for this run.
            cur.execute(
                "insert into public.load_manifest "
                "(dataset_revision, scraped_at, source_url, row_hash) "
                "values (%s, %s, %s, %s)",
                (
                    transformed["dataset_revision"],
                    transformed["scraped_at"],
                    transformed["source_url"],
                    transformed["fingerprint"],
                ),
            )
            counts["load_manifest"] = 1
        conn.commit()

    return {"row_counts": counts, "fingerprint": transformed["fingerprint"]}
