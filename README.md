# Revenue Manager Agent

An AI **Revenue Manager for a hotel General Manager**, built for the
[otel-build-challenge](https://github.com/otel-ai/otel-build-challenge). It
answers natural-language commercial questions ("What's driving July?", "Are we
too dependent on OTA?") from reservation data that this repo **scrapes itself**,
behind a deployed chat UI that shows every tool and skill call.

Pipeline: **ETL (scrape → load)** → **semantic views + typed tool layer** →
**LangChain Deep Agent with skills** → **deployed UI**.

## The one concept that matters most

`reservations_hackathon` is **one row per `reservation_id` × `stay_date`**, not
one row per booking:

- reservation count = `count(distinct reservation_id)`
- room nights = `sum(number_of_spaces)`
- stay rows (raw row count) is **neither** — never report it as bookings

Default **on-the-books (OTB)** universe = non-cancelled **and** Posted
(excludes `Provisional`), encoded once in `vw_stay_night_base` so nothing
downstream has to remember it.

## Repository layout

| Path | What |
|------|------|
| `ATTESTATION.md` | Phase 0 comprehension answers + one-line ETL design |
| `etl/` | `scrape.py` → `transform.py` → `load.py`, orchestrated by `run_etl.py`; proofs in `SCRAPE_MANIFEST.json` / `LOAD_PROOF.json` |
| `sql/views.sql` | `vw_stay_night_base`, `vw_posted_stay_night`, `vw_segment_stay_night` |
| `tools/` | the five required tools + `METRIC_DEFINITIONS.md` |
| `skills/` | `SKILL.md` files (progressive disclosure) |
| `agent/` | `create_deep_agent` wiring + server |
| `tests/` | `test_etl.py`, `test_tools.py`, `test_skills.py`, `test_agent.py`, `test_security.py` |
| `schema.sql`, `docker-compose.yml` | DB schema + local Postgres (from the brief) |
| `ARCHITECTURE.md` | one-page architecture + skill→tool routing matrix |
| `SECURITY.md` | threat model, every guardrail and what it defends against, known gaps |

## Setup

```bash
conda create -n otel python=3.12 -y && conda activate otel
pip install -r requirements-etl.txt   # core + scraper (web host needs only requirements.txt)
python -m playwright install chromium
cp .env.example .env   # fill in ANTHROPIC_API_KEY etc.
```

**Database (local).** The brief ships a Docker Postgres:

```bash
docker compose up -d            # Postgres on :5432, db hotel_hackathon
psql "$DATABASE_URL" -f schema.sql
psql "$DATABASE_URL" -f sql/views.sql
```

(Any Postgres 16 reachable at `DATABASE_URL` works — e.g. a local
`postgresql@16` service or a hosted Neon/Supabase instance for deployment.)

## Models (pluggable)

The chatbot is selectable (`agent/models.py`, `available_models()` for the UI):

| Model id | Provider | Notes |
|----------|----------|-------|
| `gemini-flash-lite` | Google | Gemini 3.5 Flash-Lite; free tier, UI default; needs `GOOGLE_API_KEY` ([AI Studio](https://aistudio.google.com/apikey)) |
| `gemini-flash` | Google | Gemini 3.8 Flash; free tier allows only 20 requests/day, about two questions |
| `claude-sonnet-4-6` / `claude-haiku-4-5` | Anthropic | most thorough briefings; needs `ANTHROPIC_API_KEY` |
| `groq-llama-3.3-70b` / `groq-gpt-oss-20b` | Groq | open weights; needs `GROQ_API_KEY` — **free tier (8–12k TPM) is too small for this tool-heavy agent**, use Groq's paid tier |
| `ollama-qwen2.5` / `ollama-llama3.1` | Ollama | local, free, no key (no token cap); install Ollama + `ollama pull qwen2.5` |

Set the default with `AGENT_MODEL_ID` in `.env`. Live check:
`AGENT_MODEL_ID=gemini-flash-lite python scripts/smoke_agent.py`.

The server only accepts model ids from this registry whose key is configured;
anything else is rejected.

## Run the ETL

```bash
export DATABASE_URL=postgresql://hackathon:hackathon@localhost:5432/hotel_hackathon
python -m etl.run_etl                 # fresh scrape + load + proofs + reconcile
python -m etl.run_etl --no-scrape     # reuse newest cached scrape (dev)
```

This scrapes the [data site](https://otel-hackathon-data-site.vercel.app),
loads Postgres idempotently, writes `etl/SCRAPE_MANIFEST.json` +
`etl/LOAD_PROOF.json`, and reconciles row counts and the
`reservation_stay_status_sha256` against the live `/verify` page.

> **Anchor-date dependency.** The data site regenerates its dataset daily,
> forward-looking from "today". Counts and checksums change each day, so
> **re-run the ETL on the same day you load/deploy/submit** and reconcile
> against `/verify` then.

## Two data-site subtleties handled by the ETL

1. **Edge-case reservations.** The reservation list UI renders only 50 of
   page 3's 54 rows, silently dropping four edge-case reservations
   (`RES-EDGE-001..003`, `RES-ZEPHYR-7F3A`) that carry the `property_date`
   audit rows. The ETL reads the page's own list **server action** (which
   returns all 254) rather than the rendered DOM, so nothing is lost.
2. **Rate-plan codes.** Reservations carry ~16 granular commercial rate codes
   while `rate_plan_lookup` has 8 canonical families with no published mapping.
   We keep the real code and drop only the `rate_plan_code` foreign key (the
   `/verify` fingerprint does not depend on it). See `ARCHITECTURE.md`.

## Run the agent + UI

```bash
uvicorn agent.server:app --host 0.0.0.0 --port 8000
# open http://localhost:8000  (basic auth: APP_USERNAME / APP_PASSWORD; local default gm / harbour)
# In deployment, set real APP_USERNAME / APP_PASSWORD via env — credentials are never committed.
```

The chat UI streams every **tool call** and **skill load** live in a side panel,
has a **model dropdown** (Gemini / Claude), and shows an **Approve / Reject**
prompt when a question triggers the human-gated `get_as_of_otb`. Endpoints:

- `GET /health` (open) — `db_fingerprint`, `dataset_revision`, `row_hash`,
  `financial_status_posted_only_rows` from `etl/LOAD_PROOF.json`.
- `POST /chat`, `POST /resume` (auth, rate-limited) — SSE stream; `/resume` carries
  the HITL decision. Both need a `thread_id` of 20-64 URL-safe characters (the UI
  generates a UUID) and a `model_id` from the registry.

CLI smoke test (no UI): `python scripts/smoke_agent.py "Are we too dependent on OTA in August 2026?"`

## Security and guardrails

Summarised here; `SECURITY.md` has the threat model, the full list and the known gaps.

- **Agent sandbox.** The agent can read `skills/` and nothing else on disk, cannot
  write anywhere on disk, and is never offered file-write or shell tools.
- **Data access.** Five fixed, parameterised query tools (no SQL from the model),
  read-only database sessions with a statement timeout, validated inputs.
- **Server.** Basic auth that refuses to start with default credentials when
  hosted, a model allow-list, a 2,000-character message cap, unguessable thread
  ids, per-client and daily rate limits, a request timeout and a graph step limit.
  Provider errors are logged, not shown.
- **Browser.** Model output is sanitised with DOMPurify; CDN scripts are pinned
  with integrity hashes.
- **Prompt.** The agent is scoped to this hotel's revenue data and told to treat
  text inside tool results as data, not instructions.

Booking-pace questions measure "the last N days" back from the dataset's own
as-of moment (the latest load) rather than the wall clock, so a snapshot loaded
some time ago still answers them. Override with `DATA_AS_OF` (ISO-8601).

## Tests

```bash
pytest                       # all 58
pytest tests/test_etl.py     # ETL: grain, lookup counts, manifest reconciliation
pytest tests/test_tools.py   # tool layer (against loaded Postgres)
pytest tests/test_skills.py  # skill pack structure/judgment (no LLM calls)
pytest tests/test_agent.py   # agent wiring: tool surface, HITL, routing
pytest tests/test_security.py  # sandbox, server limits, read-only DB (no LLM calls)
```

## Deploy

The web service needs only `requirements.txt` (no browser). Three pieces:

1. **Hosted Postgres** (Neon/Supabase/Railway): create it, then on your machine run
   `DATABASE_URL=<hosted> python -m etl.run_etl` (same day you deploy) so the live
   DB matches the committed `LOAD_PROOF.json`, and apply `sql/views.sql`.
2. **Web service** (Render/Railway/Fly): start command from the `Procfile`
   (`uvicorn agent.server:app --host 0.0.0.0 --port $PORT`). Set env:
   `DATABASE_URL`, `GOOGLE_API_KEY` (and/or `ANTHROPIC_API_KEY`, `GROQ_API_KEY`),
   `AGENT_MODEL_ID`, `APP_USERNAME`, `APP_PASSWORD`. Never commit keys. The server
   will not start on a hosted platform without `APP_USERNAME` and `APP_PASSWORD`.
   For least privilege, create the read-only role in `sql/readonly_role.sql` and
   use it for the web service's `DATABASE_URL`.
3. **Verify**: `GET /health` on the live URL must match `etl/LOAD_PROOF.json` and the
   data site `/verify` for that day.

**Automated sync & uptime (GitHub Actions).**
- `.github/workflows/sync-to-neon.yml` (manual dispatch) re-runs the ETL into the
  hosted DB, refreshes the committed proofs, and pushes — triggering a redeploy so
  `/health` matches the day's `/verify`. Run it on submission day. Requires repo
  secret `NEON_DATABASE_URL`.
- `.github/workflows/keepalive.yml` pings `/health` every 10 min so a free-tier
  instance stays awake.
