---
name: ota-channel-concentration
description: Judge OTA / channel concentration risk for a stay month and recommend a shift-to-direct action. Use for "are we too dependent on OTA?", channel-mix, or distribution-cost questions. Calls get_segment_mix.
---

# OTA / channel concentration risk

## When to use
A GM asks whether the hotel is too dependent on OTAs, about distribution cost, or
about channel mix for a month. Call `get_segment_mix(stay_month)` and read the
`OTA` segment's `share_of_revenue` and `share_of_room_nights`.

## How to judge (thresholds)
Interpret OTA **`share_of_revenue`** for the month:

- **< 25%** — healthy. Direct/corporate base is carrying the month; no action.
- **25–35%** — watch. Note it; protect direct conversion.
- **> 35%** — **channel concentration risk.** OTA commission (typically 15–20%)
  is materially eroding net ADR.
- **> 45%** — **severe dependency.** A single OTA rate/ranking change can swing
  the month.

Also flag when OTA `share_of_room_nights` **exceeds** its `share_of_revenue` by
more than ~5 points: OTA is buying low-rate volume and diluting ADR.

## Recommended actions (when > 35%)
1. Shift demand to direct: push BAR/PROM and brand-web rate parity, loyalty member
   rates, and a small direct-only perk (breakfast/late checkout).
2. Review OTA mix and commission tiers; pull back the most expensive OTA on
   high-demand dates and protect those dates for direct/corporate.
3. Re-check rate parity — leaking parity trains guests to book OTA.
4. Quantify the cost: at >35% revenue share, ~6–7% of month revenue is
   commission; state that number to the GM.

## Trap to avoid
Use revenue share, not room-night share, as the headline dependency metric, and
always at **stay-date** grain from the OTB universe — do not count cancelled or
provisional business. Never read `reservations_hackathon` directly.
