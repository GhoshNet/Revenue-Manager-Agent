# ATTESTATION.md (Phase 0)

## Candidate

- Name: Tanmay Ghosh
- Repository URL: https://github.com/GhoshNet/Revenue-Manager-Agent
- Date: 2026-06-16

---

## Comprehension prompts

### 1. Fact-table grain

`reservations_hackathon` is **one row per `reservation_id` × `stay_date`** (one
stay-night per reservation), **not** one row per booking.

### 2. Revenue columns

- `daily_room_revenue_before_tax` — room-only revenue for that stay night; use it
  for room-revenue and ADR questions (ADR = room revenue / room nights).
- `daily_total_revenue_before_tax` — room **plus** extras (breakfast/package
  effects); use it for broader "total revenue" questions.

### 3. Row vs reservation

"How many reservations/bookings do we have for July?" Counting rows overcounts,
because a 3-night booking is 3 rows. The correct measure is
`count(distinct reservation_id)`.

### 4. Schema fields

**No.** There is no `otel_challenge_token` column anywhere in `schema.sql`
(`reservations_hackathon` or any lookup). It does not exist and is used for
nothing — this is a trap; loading or referencing it would be inventing data.

### 5. Default OTB filters

Default on-the-books excludes `reservation_status = 'Cancelled'` **and**
`financial_status = 'Provisional'` (i.e. keep non-cancelled **Posted** rows).
`financial_status` is constrained to `('Posted','Provisional')`, so "= Posted"
is exactly "exclude Provisional".

### 6. Stay date vs property date

`property_date` is the hotel business date attributed to a stay row; it usually
equals `stay_date` but can differ on night-boundary / audit rows (Appendix B).
**Monthly OTB is driven by `stay_date`**, not `property_date`.

### 7. Point-in-time OTB

In `get_as_of_otb`, a row is included only if `create_datetime <= as_of_utc` and
it was still live at that instant: a cancelled reservation is **kept** if its
`cancellation_datetime > as_of_utc` (it had not yet been cancelled as of that
time) and **dropped** if `cancellation_datetime <= as_of_utc`. So moving
`as_of_utc` earlier reinstates bookings that were later cancelled.

### 8. Block vs transient

A "group vs transient mix" question splits the stay-night population on the
`is_block` flag: `is_block = true` is group/block business, `false` is transient.
Aggregate room nights / revenue grouped by `is_block`, then derive shares.

### 9. List pagination

**100 reservations per list page.**

### 10. Pagination completeness

Read `total_reservations` from `/verify`, then page until the list yields no new
IDs / the next control is disabled. Proof: the count of distinct scraped
`reservation_id`s must equal `/verify` `total_reservations`, and
`SCRAPE_MANIFEST.reservation_ids_sha256` (SHA-256 of sorted IDs) must equal
`count(distinct reservation_id)` loaded in the DB.

### 11. Tool grain

For `get_otb_summary`, `row_count` is the number of **stay-date rows** in scope,
while `reservation_count` is `count(distinct reservation_id)`. Because multi-night
stays span several rows, `row_count >= reservation_count` (and they are different
metrics — never report row_count as "bookings").

### 12. Human-in-the-loop

`get_as_of_otb` is an expensive point-in-time rebuild: it re-derives the entire
book as known at an arbitrary timestamp using full create/cancellation history.
Gating it behind approval (a) forces the human to confirm the exact `as_of_utc`
snapshot they intend (a wrong timestamp silently produces a plausible-but-wrong
historical answer), and (b) prevents the agent from triggering a costly recompute
on its own. Without the gate, the agent can answer heavy historical queries
unconfirmed and burn compute on a misread date.

### 13. Skill vs tool

"Are we too dependent on OTA?" should load the channel/segment-concentration
**skill** (which carries the threshold + recommended action) but answer by
calling **`get_segment_mix`** — never raw SQL.

---

## ETL design (one line)

Playwright paginates `/reservations` at 100/page until exhausted (asserting the
distinct-ID count equals `/verify` `total_reservations`), drills into each
`/reservations/<id>` for detail-only fields (`financial_status`, `property_date`,
per-night stay rows), transforms into the `reservation_id × stay_date` grain, and
loads idempotently via truncate-and-reload inside one transaction keyed on
`unique(reservation_id, stay_date)` — all against **today's `anchor_date`**, which
must match `/verify` on load day.
