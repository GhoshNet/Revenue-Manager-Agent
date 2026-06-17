"""The five required Revenue-Manager tools (Phase 2).

Design principle (brief 0.6): correctness lives in *our* code, not in model-written
SQL. None of these tools accept a SQL string. Each reads from a semantic view so
the default OTB filters (non-cancelled, Posted) and the stay-date-effective macro
group are applied in exactly one place.

Grain vocabulary used in every docstring:
  - stay rows / row_count : rows at reservation_id x stay_date grain (raw rows)
  - reservation_count     : count(distinct reservation_id)
  - room_nights           : sum(number_of_spaces) at stay-date grain
Room revenue  = daily_room_revenue_before_tax (room only).
Total revenue = daily_total_revenue_before_tax (room + extras).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tools.db import f, month_bounds, query, query_one, ratio

LONDON = ZoneInfo("Europe/London")


# ---------------------------------------------------------------------------
# 1. get_otb_summary
# ---------------------------------------------------------------------------
def get_otb_summary(stay_month: str, exclude_cancelled: bool = True) -> dict:
    """On-the-books summary for one calendar month of stay dates (stay_month = 'YYYY-MM').

    Default universe is vw_stay_night_base (non-cancelled, Posted). Set
    exclude_cancelled=False to also include cancelled-but-Posted rows
    (vw_posted_stay_night); Provisional rows are excluded either way.

    Returns (all at stay-date grain for the month):
      - stay_month
      - row_count          : number of stay rows  (NOT a reservation count)
      - reservation_count  : count(distinct reservation_id)
      - room_nights        : sum(number_of_spaces)
      - room_revenue       : sum(daily_room_revenue_before_tax)
      - total_revenue      : sum(daily_total_revenue_before_tax)
      - exclude_cancelled  : echo of the input
    """
    start, end = month_bounds(stay_month)
    view = "vw_stay_night_base" if exclude_cancelled else "vw_posted_stay_night"
    row = query_one(
        f"""
        select
          count(*)                                  as row_count,
          count(distinct reservation_id)            as reservation_count,
          coalesce(sum(number_of_spaces), 0)        as room_nights,
          coalesce(sum(daily_room_revenue_before_tax), 0)  as room_revenue,
          coalesce(sum(daily_total_revenue_before_tax), 0) as total_revenue
        from public.{view}
        where stay_date >= %(start)s and stay_date < %(end)s
        """,
        {"start": start, "end": end},
    )
    return {
        "stay_month": stay_month,
        "row_count": int(row["row_count"]),
        "reservation_count": int(row["reservation_count"]),
        "room_nights": int(row["room_nights"]),
        "room_revenue": f(row["room_revenue"]),
        "total_revenue": f(row["total_revenue"]),
        "exclude_cancelled": exclude_cancelled,
    }


# ---------------------------------------------------------------------------
# 2. get_segment_mix
# ---------------------------------------------------------------------------
def get_segment_mix(stay_month: str, macro_group: str | None = None) -> dict:
    """Segment mix for a stay month from vw_segment_stay_night (non-cancelled, Posted).

    Uses the stay-date-effective macro group (market_macro_group_history), not the
    static market_code_lookup value. If macro_group is given, the result is
    filtered to that effective macro group and shares are taken over that filtered
    population only.

    Shares use a single denominator (stated in the payload) so they sum to 1.0.
    All sums are at stay-date grain (room_nights = sum(number_of_spaces)).

    Returns:
      - stay_month, macro_group (filter echo)
      - denominator: {room_nights, total_revenue} for all segments in scope
      - segments: list of
          {market_code, market_name, macro_group (effective),
           room_nights, total_revenue,
           share_of_room_nights (0-1), share_of_revenue (0-1)}
    """
    start, end = month_bounds(stay_month)
    params = {"start": start, "end": end}
    macro_filter = ""
    if macro_group is not None:
        macro_filter = "and effective_macro_group = %(macro_group)s"
        params["macro_group"] = macro_group

    rows = query(
        f"""
        select
          market_code,
          max(market_name)                   as market_name,
          effective_macro_group,
          coalesce(sum(number_of_spaces), 0) as room_nights,
          coalesce(sum(daily_total_revenue_before_tax), 0) as total_revenue
        from public.vw_segment_stay_night
        where stay_date >= %(start)s and stay_date < %(end)s
        {macro_filter}
        group by market_code, effective_macro_group
        order by total_revenue desc
        """,
        params,
    )
    denom_nights = sum(f(r["room_nights"]) for r in rows)
    denom_rev = sum(f(r["total_revenue"]) for r in rows)
    segments = [
        {
            "market_code": r["market_code"],
            "market_name": r["market_name"],
            "macro_group": r["effective_macro_group"],
            "room_nights": int(r["room_nights"]),
            "total_revenue": f(r["total_revenue"]),
            "share_of_room_nights": ratio(r["room_nights"], denom_nights),
            "share_of_revenue": ratio(r["total_revenue"], denom_rev),
        }
        for r in rows
    ]
    return {
        "stay_month": stay_month,
        "macro_group": macro_group,
        "denominator": {"room_nights": int(denom_nights), "total_revenue": denom_rev},
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# 3. get_pickup_delta
# ---------------------------------------------------------------------------
def get_pickup_delta(booking_window_days: int, future_stay_from: str) -> dict:
    """Booking pace / pickup: net new business *booked* recently for future stays.

    The window is defined on create_datetime (booking date), NOT stay_date:
      [ start_of_day Europe/London (now - booking_window_days) , now ]
    converted to UTC for comparison against create_datetime (stored in UTC).
    Only stay rows with stay_date >= future_stay_from are counted. Universe is
    vw_stay_night_base (non-cancelled, Posted) — i.e. business still on the books.
    Counts are at stay-date grain; new_reservations is count(distinct reservation_id).

    Returns:
      - booking_window_days, future_stay_from
      - window_start_utc, window_end_utc (echo of the resolved boundaries)
      - new_reservations : count(distinct reservation_id) created in the window
      - new_room_nights  : sum(number_of_spaces)
      - new_total_revenue: sum(daily_total_revenue_before_tax)
      - by_segment: per market_code {market_code, market_name, room_nights,
                    total_revenue}, ordered by total_revenue desc
    """
    now_utc = datetime.now(timezone.utc)
    start_london = (now_utc.astimezone(LONDON) - timedelta(days=booking_window_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start_utc = start_london.astimezone(timezone.utc)
    params = {"start_utc": start_utc, "end_utc": now_utc, "future": future_stay_from}
    where = (
        "where create_datetime >= %(start_utc)s and create_datetime <= %(end_utc)s "
        "and stay_date >= %(future)s"
    )
    totals = query_one(
        f"""
        select
          count(distinct reservation_id)            as new_reservations,
          coalesce(sum(number_of_spaces), 0)        as new_room_nights,
          coalesce(sum(daily_total_revenue_before_tax), 0) as new_total_revenue
        from public.vw_segment_stay_night
        {where}
        """,
        params,
    )
    by_segment = query(
        f"""
        select market_code, max(market_name) as market_name,
               coalesce(sum(number_of_spaces),0) as room_nights,
               coalesce(sum(daily_total_revenue_before_tax),0) as total_revenue
        from public.vw_segment_stay_night
        {where}
        group by market_code
        order by total_revenue desc
        """,
        params,
    )
    return {
        "booking_window_days": booking_window_days,
        "future_stay_from": future_stay_from,
        "window_start_utc": start_utc.isoformat(),
        "window_end_utc": now_utc.isoformat(),
        "new_reservations": int(totals["new_reservations"]),
        "new_room_nights": int(totals["new_room_nights"]),
        "new_total_revenue": f(totals["new_total_revenue"]),
        "by_segment": [
            {
                "market_code": r["market_code"],
                "market_name": r["market_name"],
                "room_nights": int(r["room_nights"]),
                "total_revenue": f(r["total_revenue"]),
            }
            for r in by_segment
        ],
    }


# ---------------------------------------------------------------------------
# 4. get_as_of_otb  (HUMAN-IN-THE-LOOP gated in the agent)
# ---------------------------------------------------------------------------
def get_as_of_otb(stay_month: str, as_of_utc: str) -> dict:
    """Point-in-time on-the-books for a stay month, as it was known at as_of_utc.

    Rebuilds the book from history: a stay row is included when
      - create_datetime <= as_of_utc, AND
      - (reservation_status <> 'Cancelled' OR cancellation_datetime > as_of_utc), AND
      - financial_status = 'Posted'
    Reads vw_posted_stay_night (Posted, cancellation-aware). Same return shape as
    get_otb_summary plus an as_of_utc echo, at stay-date grain (row_count = stay
    rows, reservation_count = distinct reservations). Moving as_of_utc earlier
    reinstates bookings that were later cancelled.

    This is an expensive historical rebuild and is gated behind human approval in
    the agent so the analyst confirms the exact snapshot timestamp.
    """
    start, end = month_bounds(stay_month)
    params = {"start": start, "end": end, "as_of": as_of_utc}
    row = query_one(
        """
        select
          count(*)                                  as row_count,
          count(distinct reservation_id)            as reservation_count,
          coalesce(sum(number_of_spaces), 0)        as room_nights,
          coalesce(sum(daily_room_revenue_before_tax), 0)  as room_revenue,
          coalesce(sum(daily_total_revenue_before_tax), 0) as total_revenue
        from public.vw_posted_stay_night
        where stay_date >= %(start)s and stay_date < %(end)s
          and create_datetime <= %(as_of)s
          and (reservation_status <> 'Cancelled' or cancellation_datetime > %(as_of)s)
        """,
        params,
    )
    return {
        "stay_month": stay_month,
        "as_of_utc": as_of_utc,
        "row_count": int(row["row_count"]),
        "reservation_count": int(row["reservation_count"]),
        "room_nights": int(row["room_nights"]),
        "room_revenue": f(row["room_revenue"]),
        "total_revenue": f(row["total_revenue"]),
        "exclude_cancelled": True,
    }


# ---------------------------------------------------------------------------
# 5. get_block_vs_transient_mix
# ---------------------------------------------------------------------------
def get_block_vs_transient_mix(stay_month: str) -> dict:
    """Block (group) vs transient mix for a stay month from vw_stay_night_base.

    Splits the non-cancelled Posted stay-night population on is_block. All counts
    are at stay-date grain: room_nights = sum(number_of_spaces),
    revenue = sum(daily_total_revenue_before_tax).

    Returns:
      - stay_month
      - block_room_nights, transient_room_nights
      - block_total_revenue, transient_total_revenue
      - block_share_of_room_nights, block_share_of_revenue (0-1)
      - top_companies: up to 3 {company_name, total_revenue} by revenue desc
                       (null company_name -> 'Transient')
      - top3_company_revenue_share: their combined share of month total revenue (0-1)
    """
    start, end = month_bounds(stay_month)
    params = {"start": start, "end": end}
    split = query(
        """
        select is_block,
               coalesce(sum(number_of_spaces),0) as room_nights,
               coalesce(sum(daily_total_revenue_before_tax),0) as total_revenue
        from public.vw_stay_night_base
        where stay_date >= %(start)s and stay_date < %(end)s
        group by is_block
        """,
        params,
    )
    block = next((r for r in split if r["is_block"]), {"room_nights": 0, "total_revenue": 0})
    trans = next((r for r in split if not r["is_block"]), {"room_nights": 0, "total_revenue": 0})
    tot_nights = f(block["room_nights"]) + f(trans["room_nights"])
    tot_rev = f(block["total_revenue"]) + f(trans["total_revenue"])

    companies = query(
        """
        select coalesce(company_name, 'Transient') as company_name,
               coalesce(sum(daily_total_revenue_before_tax),0) as total_revenue
        from public.vw_stay_night_base
        where stay_date >= %(start)s and stay_date < %(end)s
        group by coalesce(company_name, 'Transient')
        order by total_revenue desc
        limit 3
        """,
        params,
    )
    top_companies = [
        {"company_name": r["company_name"], "total_revenue": f(r["total_revenue"])}
        for r in companies
    ]
    top3_rev = sum(c["total_revenue"] for c in top_companies)
    return {
        "stay_month": stay_month,
        "block_room_nights": int(f(block["room_nights"])),
        "transient_room_nights": int(f(trans["room_nights"])),
        "block_total_revenue": f(block["total_revenue"]),
        "transient_total_revenue": f(trans["total_revenue"]),
        "block_share_of_room_nights": ratio(block["room_nights"], tot_nights),
        "block_share_of_revenue": ratio(block["total_revenue"], tot_rev),
        "top_companies": top_companies,
        "top3_company_revenue_share": ratio(top3_rev, tot_rev),
    }


# Convenience: the exact five-tool surface (used by the agent + tests).
ALL_TOOLS = [
    get_otb_summary,
    get_segment_mix,
    get_pickup_delta,
    get_as_of_otb,
    get_block_vs_transient_mix,
]
