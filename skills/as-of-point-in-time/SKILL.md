---
name: as-of-point-in-time
description: Reconstruct the book as it was known at a past timestamp for pace/pickup-vs-prior comparisons, with human approval of the snapshot. Use for "where were we on this date last month?", as-of / point-in-time questions. Calls get_as_of_otb (human-gated).
---

# Point-in-time (as-of) OTB

## When to use
A GM asks where the book stood at a past moment ("what did we have on the books for
August as of May 1?") or wants a like-for-like pace comparison against a prior
snapshot.

## How it works
Parse the month and timestamp from the question and **call
`get_as_of_otb(stay_month, as_of_utc)` directly** — do not ask the user to confirm
in plain text. The tool is an expensive historical rebuild and is **gated behind a
human-approval interrupt**: the framework automatically pauses and shows the
analyst the exact `as_of_utc` for approval before the query runs, so calling the
tool *is* how you request that confirmation. A stay row counts only if it was
created on or before `as_of_utc` and was still live then (not yet cancelled as of
that instant); provisional is excluded. Moving `as_of_utc` earlier reinstates
bookings that were later cancelled, so an earlier snapshot never has more
reservations than a later one.

## How to use the result
- Compare the as-of `reservation_count` / `room_nights` / `total_revenue` to the
  current `get_otb_summary` for the same month to quantify net pickup since the
  snapshot.
- Always state the snapshot timestamp in the answer so the comparison is auditable.

## Trap to avoid
A wrong `as_of_utc` produces a plausible but wrong history — the approval gate
exists precisely so the analyst can catch a misread timestamp, so always pass the
exact UTC instant from the question. Do not approximate this with the current OTB;
the cancellation-as-of logic genuinely differs.
