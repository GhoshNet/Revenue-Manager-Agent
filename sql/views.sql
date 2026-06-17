-- Semantic views — the single place the default OTB universe is defined.
-- Required agent-facing tools read from these views, never from
-- reservations_hackathon directly. See tools/METRIC_DEFINITIONS.md.

-- Default on-the-books (OTB) grain: non-cancelled, Posted (excludes Provisional).
-- financial_status is constrained to ('Posted','Provisional') in schema.sql, so
-- "= 'Posted'" is exactly "exclude Provisional".
create or replace view public.vw_stay_night_base as
select
  r.*
from public.reservations_hackathon r
where r.reservation_status <> 'Cancelled'
  and r.financial_status = 'Posted';

-- Posted stay nights INCLUDING cancelled reservations. Used only by the tools
-- that must reason about cancellation as of a point in time
-- (get_otb_summary(exclude_cancelled=False) and get_as_of_otb). Still excludes
-- Provisional, so it never leaks tentative business into committed metrics.
create or replace view public.vw_posted_stay_night as
select
  r.*
from public.reservations_hackathon r
where r.financial_status = 'Posted';

-- Stay-night grain enriched with the stay-date-effective macro group.
-- effective_macro_group uses market_macro_group_history (effective-dated) when a
-- history row covers the stay_date, otherwise falls back to the static lookup.
create or replace view public.vw_segment_stay_night as
select
  b.*,
  coalesce(h.macro_group, m.macro_group) as effective_macro_group,
  m.market_name
from public.vw_stay_night_base b
join public.market_code_lookup m on m.market_code = b.market_code
left join lateral (
  select h.macro_group
  from public.market_macro_group_history h
  where h.market_code = b.market_code
    and b.stay_date >= h.valid_from
    and (h.valid_to is null or b.stay_date < h.valid_to)
  order by h.valid_from desc
  limit 1
) h on true;
