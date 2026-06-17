# Architecture

Revenue Manager Agent: **ETL → semantic views → typed tool layer → Deep Agent
(skills + subagent + HITL + memory) → streaming UI.**

## 1. ETL boundary

- **Extract** (`etl/scrape.py`): Playwright drives the client-rendered Next.js
  data site and discovers its React **server actions** at runtime, then calls
  them for clean typed JSON. The list action is paged `[page, 100]` for the full
  ID set; the detail action returns each reservation + its per-night stay rows.
  This deliberately bypasses the rendered table, which silently drops 4 edge-case
  reservations (`RES-EDGE-001..003`, `RES-ZEPHYR-7F3A`); action ids are
  rediscovered each run so the scraper survives redeploys.
- **Transform** (`etl/transform.py`): flattens to the fact grain **one row per
  `reservation_id × stay_date`**, types every field, and computes the
  `reservation_stay_status` SHA-256.
- **Load** (`etl/load.py`): idempotent truncate-and-reload in one transaction;
  appends a `load_manifest` row each run. The `rate_plan_code` FK is dropped
  (see below).
- **Verify** (`etl/run_etl.py` + `scripts/compute_load_fingerprint.py`):
  reconciles row counts, sha256, cancelled/provisional/property-date counts, and
  `dataset_revision` against the live `/verify` page for the recorded
  `anchor_date`. `etl/SCRAPE_MANIFEST.json` + `etl/LOAD_PROOF.json` are committed.

**Rate-plan FK decision.** Reservations carry ~16 granular commercial rate codes
while `rate_plan_lookup` has 8 canonical families and no published mapping;
`schema.sql`'s FK cannot hold against the shipped data. We keep the real code and
drop **only** that FK at load time. The `/verify` fingerprint is computed over
`reservation_id|stay_date|financial_status`, so this does not affect
reconciliation. Inventing a mapping would fabricate data.

## 2. Database and views

Hosted Postgres 16 (Neon for deploy; Homebrew/Docker locally). Tools read **only**
from semantic views, never `reservations_hackathon`:

- `vw_stay_night_base` — default OTB: non-cancelled **and** Posted.
- `vw_posted_stay_night` — Posted incl. cancelled (cancellation-aware tools only).
- `vw_segment_stay_night` — base grain + **stay-date-effective** macro group
  (via `market_macro_group_history`) + `market_name`.

## 3. Tool layer (`tools/revenue_tools.py`)

| Tool | View(s) | Notes |
|------|---------|-------|
| `get_otb_summary` | base / posted | row_count = stay rows (≠ reservations); `exclude_cancelled=False` uses posted view |
| `get_segment_mix` | segment | effective macro group; shares share one denominator, sum to 1.0 |
| `get_pickup_delta` | segment | window on `create_datetime` (Europe/London midnight → UTC); stays `>= future_stay_from` |
| `get_as_of_otb` | posted | point-in-time; cancellation-as-of logic; **human-gated** |
| `get_block_vs_transient_mix` | base | `is_block` split; top-3 company concentration |

No tool accepts a SQL string. Cancellation/provisional defaults live in the views.
Grain definitions: `tools/METRIC_DEFINITIONS.md`.

## 4. Deep Agents wiring (`agent/agent.py`)

| Building block | Use |
|----------------|-----|
| Tools | the five named tools — no `run_sql` |
| Skills | `skills/<name>/SKILL.md` loaded via `SkillsMiddleware` (progressive disclosure) |
| Subagents | **`segment-analyst`** holds only `get_segment_mix` + `get_block_vs_transient_mix` |
| Planning | built-in `write_todos` / todo middleware |
| Memory / filesystem | thread `checkpointer` (multi-turn) + long-term `store` + `FilesystemBackend` (virtual writes) |
| Human-in-the-loop | `interrupt_on={"get_as_of_otb": True}` |
| Model & prompt | `claude-sonnet-4-6` (configurable) + sharp RM persona / brief-§12 answer style |

## 5. Skill → tool routing matrix

| Skill | Primary tool(s) | Judgment? |
|-------|-----------------|-----------|
| `otb-month-briefing` | get_otb_summary, get_segment_mix | partial |
| `segment-mix-shift` | get_segment_mix | **Y** (concentration thresholds + action) |
| `ota-channel-concentration` | get_segment_mix | **Y** (OTA rev-share > 35/45% + shift-to-direct) |
| `booking-pace-pickup` | get_pickup_delta | **Y** (7d vs run-rate < 60% + tactical action) |
| `group-block-concentration` | get_block_vs_transient_mix | **Y** (block > 50% / top3 > 30% + protect base) |
| `as-of-point-in-time` | get_as_of_otb (HITL) | procedure |
| `cancellation-provisional-guardrails` | all (guardrail) | guardrail |
| `CHALLENGE_SKILL.md` | router/manifest (`otel-rm-v2`) | manifest |

4 skills encode judgment (≥3 required); OTA and block skills carry explicit
numeric thresholds + recommended actions.

## 6. Tests

- `tests/test_etl.py` (11) — grain, lookup counts, manifest/proof reconciliation.
- `tests/test_tools.py` (14) — scenarios 1–6, 8–12 against the loaded DB.
- `tests/test_skills.py` (10) — pack version, ≥6 skills, ≥3 judgment, routing,
  guardrail, concentration (no LLM).
- `tests/test_agent.py` (7) — exact 5-tool surface, HITL on `get_as_of_otb`
  (graph node + config), subagent isolation, multi-tool trace, on-demand skills,
  memory/filesystem (graph introspection + recorded fixture; no LLM).

## 7. Deployment topology

- **DB**: hosted Postgres (Neon), loaded by this ETL.
- **Agent backend**: LangGraph app (`agent/agent.py`) served via the deepagents
  graph; `GET /health` returns `db_fingerprint`, `dataset_revision`, `row_hash`,
  `financial_status_posted_only_rows` from `LOAD_PROOF.json`.
- **UI**: an Agent Chat UI that streams tool/skill calls (loading a skill is a
  `read_file` tool call, so tool streaming surfaces skills). Basic auth on the
  public URL; `ANTHROPIC_API_KEY` via deployment env, never committed.

## 8. Out of scope

MCP servers (optional bonus); a custom-built UI (using a ready streaming UI); a
daily ETL cron (run once per build per the brief); mapping granular rate codes to
families (no authoritative source).
