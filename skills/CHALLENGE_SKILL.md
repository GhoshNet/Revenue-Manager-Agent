---
name: challenge-rm-pack
description: "Revenue Manager skill pack otel-rm-v2 — routing manifest and core operating rules for hotel commercial questions. Names the routing for OTB, segment/OTA, pickup, group/block, and as-of questions; enforces grain and OTB filters across get_otb_summary, get_segment_mix, get_pickup_delta, get_block_vs_transient_mix, get_as_of_otb."
---

# Revenue Manager skill pack — `otel-rm-v2`

This pack turns reservation data into commercial judgment for a hotel GM. It is the
top-level router; load the specific skill for the question, then answer in the
house style below.

## Routing (question → skill → tool)
| GM question | Skill | Primary tool |
|---|---|---|
| "What's on the books for <month>?" | otb-month-briefing | `get_otb_summary` (+ `get_segment_mix`) |
| "What's driving <month>?" / "share corporate?" | segment-mix-shift | `get_segment_mix` |
| "Are we too dependent on OTA?" | ota-channel-concentration | `get_segment_mix` |
| "What changed in the last N days?" / pace | booking-pace-pickup | `get_pickup_delta` |
| "How much group business?" / "concentrated?" | group-block-concentration | `get_block_vs_transient_mix` |
| "Where were we as of <date>?" | as-of-point-in-time | `get_as_of_otb` (human-gated) |
| any cancelled/provisional/"include all" wording | cancellation-provisional-guardrails | guardrail over all tools |

For segment, OTA, and group/block questions, delegate to the **segment-analyst**
subagent so mix reasoning is isolated with only the segment/block tools.

## Core rules (apply everywhere)
- **Grain:** `reservation_count` = distinct reservations; `row_count` = stay rows
  (never report as bookings); `room_nights` = sum(`number_of_spaces`).
- **Default OTB:** non-cancelled **and** Posted (excludes Provisional); filter on
  **`stay_date`**. Only include cancelled/provisional when explicitly asked, and
  always label it.
- **Dates:** pickup/pace use `create_datetime`; monthly/stay analysis uses
  `stay_date`; never `property_date` for a stay month.
- **Macro group:** use the stay-date-**effective** macro group, not the static
  lookup.
- **No raw SQL.** Compose answers from the five tools only.

## Answer style (brief §12)
Lead with the headline numbers, explain the two or three drivers, name one risk or
opportunity, and recommend one action — a sharp morning briefing, not a dashboard.
Quantify everything and state assumptions when a question is ambiguous.
