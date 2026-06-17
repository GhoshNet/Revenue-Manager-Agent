# Metric definitions

The single source of truth for how the tool layer counts things. All tools read
from the semantic views (`vw_stay_night_base`, `vw_posted_stay_night`,
`vw_segment_stay_night`) — never from `reservations_hackathon` directly — so these
rules are applied in exactly one place.

## Grain: stay rows vs reservations vs room nights

`reservations_hackathon` is **one row per `reservation_id` × `stay_date`**.

| Metric | Definition | SQL |
|--------|------------|-----|
| **stay rows** (`row_count`) | raw rows at reservation × stay-date grain | `count(*)` |
| **reservation count** | distinct bookings | `count(distinct reservation_id)` |
| **room nights** | rooms occupied across nights | `sum(number_of_spaces)` |

A 2-room, 3-night booking = **3 stay rows**, **1 reservation**, **6 room nights**.
Never report `row_count` as "bookings". For any month, `row_count ≥
reservation_count` and `room_nights ≥ reservation_count`.

**Revenue.** `room_revenue` = `sum(daily_room_revenue_before_tax)` (room only);
`total_revenue` = `sum(daily_total_revenue_before_tax)` (room + extras such as
breakfast/packages). Per row, room ≤ total, so `room_revenue ≤ total_revenue`.
ADR (room) = `room_revenue / room_nights`.

## Default OTB (on-the-books) filters

The default "committed business" universe excludes:

1. `reservation_status = 'Cancelled'`
2. `financial_status = 'Provisional'` (i.e. keep **Posted**; `financial_status`
   is constrained to `Posted`/`Provisional`, so "= Posted" *is* "exclude Provisional")

This is `vw_stay_night_base`. `get_otb_summary(exclude_cancelled=False)` and
`get_as_of_otb` instead read `vw_posted_stay_night` (Posted, **including**
cancelled) so they can reason about cancellation explicitly; Provisional stays
excluded throughout. Monthly filters always use **`stay_date`**, not
`property_date` (`property_date` only differs on 3 night-audit rows and is never
the basis for a stay month).

**Anchor date.** The dataset is forward-looking from "today" and regenerated
daily, so absolute counts shift by anchor date. Every load records the anchor in
`SCRAPE_MANIFEST.json` and reconciles against `/verify` on the same day.

## Pickup window (`get_pickup_delta`)

Pickup measures business **booked** in a recent window, using `create_datetime`
(booking date, stored in UTC) — never `stay_date`. The window is
`[ start_of_day Europe/London(now − booking_window_days) , now ]`, with the
London local-midnight boundary converted to UTC before comparing against
`create_datetime`. `future_stay_from` separately restricts to `stay_date ≥` that
date. So a smaller `booking_window_days` can only reduce `new_reservations`.

## Effective macro group vs static lookup

`market_code_lookup.macro_group` is a static label. The correct macro group is
**effective-dated** via `market_macro_group_history`, joined on
`stay_date ∈ [valid_from, valid_to)`. `vw_segment_stay_night` exposes this as
`effective_macro_group`. Example: `PROM` is `Retail` before 2025-06-01 and
`Leisure Group` on/after it, so `get_segment_mix(macro_group="Retail")` correctly
**excludes** PROM stays dated after the reclassification — using the static lookup
would misclassify them.
