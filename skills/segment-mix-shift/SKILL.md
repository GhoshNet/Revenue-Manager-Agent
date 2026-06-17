---
name: segment-mix-shift
description: Judge segment / macro-group mix and detect concentration or unhealthy shifts for a stay month, then recommend a steering action. Use for "what's driving July?", "what share is corporate?", segment-mix questions. Calls get_segment_mix.
---

# Segment / macro-group mix health

## When to use
A GM asks what is driving a month, which segments are growing, or what share is
corporate / leisure / MICE. Call `get_segment_mix(stay_month)` and read each
segment's `share_of_revenue`, `share_of_room_nights`, and effective `macro_group`.

## How to judge (thresholds)
- **Single-segment concentration:** any one `market_code` with
  `share_of_revenue > 40%` → over-reliant on one segment; a single account or OTA
  swing puts the month at risk.
- **Macro-group balance:** a healthy month usually keeps Retail roughly 30–55% of
  revenue with Corporate + MICE providing a base. If **Retail > 60%**, the month
  is exposed to short-lead volatility; if **Corporate < 15%**, the negotiated base
  is thin.
- **ADR signal:** if a segment's `share_of_room_nights` is well above its
  `share_of_revenue`, it is low-rate volume — diluting ADR.

## Recommended actions
- Over-concentrated in one segment → diversify: chase corporate/group leads for
  the month, or open promotional retail only if Retail is the *weak* side.
- Retail-heavy and volatile → protect high-demand dates for higher-rated
  corporate/direct and tighten low-rate retail availability.
- Always name the **two or three segments doing the most work** with their
  revenue and shares — that is the "driver" answer a GM wants.

## Trap to avoid
Use the **effective** macro group (stay-date-effective via history), not the static
lookup — e.g. PROM is Leisure Group after its reclassification, not Retail.
Shares share one denominator and sum to 1.0.
