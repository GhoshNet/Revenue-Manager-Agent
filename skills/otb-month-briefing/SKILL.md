---
name: otb-month-briefing
description: Structure a GM morning-briefing answer for a stay month — on-the-books position plus drivers and one recommendation. Use for "what revenue is on the books for July?", "what's our position for August?". Calls get_otb_summary then get_segment_mix.
---

# Monthly OTB briefing

## When to use
A GM asks for the on-the-books position of a month, or "what revenue do we have
for <month>?" This skill plans a short, decision-ready briefing.

## Plan (ordered tool calls)
1. `get_otb_summary(stay_month)` — the headline: `reservation_count`,
   `room_nights`, `room_revenue`, `total_revenue`. Lead with these.
2. `get_segment_mix(stay_month)` — name the two or three segments driving the
   month (revenue and share) so the number has a "why".
3. If the GM asks "what changed" or pace, hand off to the pickup skill.

## Answer style (brief 12)
Write like a sharp revenue manager's morning briefing, not a dashboard dump:
- One headline sentence with the key numbers (reservations, room nights, total
  revenue, implied ADR = room_revenue / room_nights).
- Two or three sentences on **drivers** (which segments, which direction).
- One **risk or opportunity** and one **recommended action**.
- Quantify everything; state assumptions when the question is ambiguous.

## Grain and filters
`reservation_count` = distinct reservations; `row_count` = stay rows (never report
row_count as bookings); `room_nights` = sum of spaces. Default OTB excludes
cancelled and provisional and filters on **stay_date**. Use `total_revenue` for
"revenue" questions and `room_revenue` for room-only/ADR questions.
