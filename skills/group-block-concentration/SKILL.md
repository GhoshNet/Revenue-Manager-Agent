---
name: group-block-concentration
description: Judge group/block vs transient mix and top-company concentration risk for a stay month, then recommend a protect-the-base action. Use for "how much group business?", "is revenue concentrated in a few bookings?", company-dependency questions. Calls get_block_vs_transient_mix.
---

# Group / block concentration risk

## When to use
A GM asks how much group business there is, whether revenue is concentrated in a
few large bookings, or which companies contribute most. Call
`get_block_vs_transient_mix(stay_month)` and read `block_share_of_revenue`,
`top3_company_revenue_share`, and `top_companies`.

## How to judge (thresholds)
- **`block_share_of_revenue > 50%`** → the month leans on group business; group
  attrition or cancellation is now the dominant risk.
- **`top3_company_revenue_share > 30%`** → **concentration risk**: a few accounts
  drive the month, so one cancellation materially dents revenue and ADR.
- Combine them: high block share *and* high top-3 share is the danger zone — the
  month is effectively a handful of contracts.

## Recommended actions (when concentrated)
1. Protect the base: confirm deposits, signed contracts, and attrition/cancellation
   clauses on the top blocks; verify cut-off dates.
2. Run a displacement check — are blocks crowding out higher-rated transient on
   peak dates? If so, cap block pickup and hold transient inventory.
3. Diversify forward: pursue additional mid-size accounts so the month is not one
   or two contracts.
4. Tell the GM the explicit revenue at risk if the single largest company cancels.

## Trap to avoid
Group vs transient is the `is_block` split, at stay-date grain on the OTB universe
(non-cancelled, Posted). `top_companies` maps null `company_name` to "Transient" —
that row is not a single account, so do not treat it as concentration.
