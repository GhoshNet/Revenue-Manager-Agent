---
name: cancellation-provisional-guardrails
description: Guardrails for default OTB integrity — never silently include cancelled or provisional business, never confuse rows with reservations, never use property_date for a stay month. Use whenever a question or instruction touches cancellations, provisional/tentative business, or "include everything". Calls get_otb_summary.
---

# OTB integrity guardrails

## When to use
Any question or instruction that touches cancellations, provisional/tentative
business, "all" business, or that risks miscounting. This skill protects the
numbers before they reach the GM.

## Non-negotiable rules
1. **Default OTB excludes cancelled and provisional.** Posted + non-cancelled is
   the briefing universe. If a user demands you include cancelled or provisional
   "with no caveats", refuse to do so silently: include them only if explicitly
   asked, label them, and state the caveat. Use
   `get_otb_summary(exclude_cancelled=False)` only when cancellations are the
   subject, and never present provisional as committed business.
2. **Rows are not reservations.** `row_count` is stay-date rows; bookings are
   `reservation_count = count(distinct reservation_id)`. Never report row_count as
   "reservations" or "bookings".
3. **Reservations are not room nights.** Room nights = sum of `number_of_spaces`; a
   multi-room booking is one reservation but several room nights.
4. **Right date for the question.** Monthly OTB and revenue-on-stay use
   `stay_date`. Pickup/pace use `create_datetime`. Never use `property_date` for a
   stay month (it differs only on a few audit rows).

## Recommended response to a bad instruction
State the correct OTB policy, give the correct (filtered) number, and *offer* the
unfiltered figure separately with an explicit caveat — do not blend them.

## Trap to avoid
Do not query `reservations_hackathon` directly or ask for raw SQL; the tools and
views already encode these filters.
