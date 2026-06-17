---
name: booking-pace-pickup
description: Judge booking pace / pickup and detect deceleration for future stays, then recommend a tactical demand action. Use for "what changed in the last 7 days?", pickup, or pace questions. Calls get_pickup_delta.
---

# Booking pace / pickup deceleration

## When to use
A GM asks what was booked recently, whether pace is holding, or "what changed in
the last N days for future stays?" Call `get_pickup_delta(booking_window_days,
future_stay_from)` — it measures business **booked** in the window by
`create_datetime`, not by stay date.

## How to judge (thresholds)
Compare a short window against the recent run-rate:

1. Get 7-day pickup: `get_pickup_delta(7, <future_from>)`.
2. Get 30-day pickup: `get_pickup_delta(30, <future_from>)` and divide
   `new_room_nights` by 30 to get the daily run-rate.
3. Expected 7-day pickup ≈ daily run-rate × 7.

- 7-day pickup **< 60%** of expected → **pace decelerating** — demand is softening
  for the covered stay dates.
- 7-day pickup **> 140%** of expected → **accelerating** — consider holding or
  raising rate on the strong dates.

Always read `by_segment` to see *which* segment moved — a corporate slowdown and
an OTA slowdown call for different responses.

## Recommended actions (when decelerating)
- Open a tactical promotional rate (PROM) or a limited OTA flash on the soft dates
  only; do not discount dates that are already pacing ahead.
- If the soft segment is corporate, chase known accounts and group leads rather
  than cutting BAR.
- Re-forecast the month and tell the GM the revenue at risk if pace does not
  recover.

## Trap to avoid
Pickup is defined on `create_datetime` (booking date, UTC) — never on `stay_date`.
A smaller window can only return fewer new reservations.
