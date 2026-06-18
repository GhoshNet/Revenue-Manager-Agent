"""Local sync helper: one fresh scrape -> load BOTH local and Neon -> proofs.

Because the data site regenerates daily, the deployed DB, the committed proofs,
and the live /verify only agree on the same calendar day. The primary, automated
path is the `daily-sync` GitHub Action (it runs the ETL into Neon on a schedule
and on demand). This script is the local equivalent when you want to refresh both
your local Postgres and Neon in one pass:

    python scripts/sync_dbs.py

It scrapes once, loads the same rows into local Postgres (DATABASE_URL) and Neon
(HOSTED_DATABASE_URL), writes etl/SCRAPE_MANIFEST.json + etl/LOAD_PROOF.json from
Neon (what /health serves), and reconciles against /verify. Then commit & push
the two updated JSON proofs so Render redeploys with matching /health.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(Path(__file__).resolve().parents[1] / ".env"))

from etl import load as load_mod  # noqa: E402
from etl import run_etl  # noqa: E402
from etl import scrape as scrape_mod  # noqa: E402
from etl import transform as transform_mod  # noqa: E402


def main() -> int:
    local = os.environ.get("DATABASE_URL") or load_mod.DEFAULT_DATABASE_URL
    neon = os.environ.get("HOSTED_DATABASE_URL")
    if not neon:
        raise SystemExit("HOSTED_DATABASE_URL not set in .env")

    raw = scrape_mod.scrape_all()
    transformed = transform_mod.transform(raw)
    print(f"Scraped anchor={raw['anchor_date']} rows={len(transformed['fact_rows'])} "
          f"fp={transformed['fingerprint'][:16]}…")

    for label, url in [("local", local), ("neon", neon)]:
        summary = load_mod.load(transformed, url)
        print(f"loaded {label}: {summary['row_counts']['reservations_hackathon']} rows")

    # Proofs reflect Neon (the deployed DB that /health reads about).
    run_etl.write_scrape_manifest(raw, transformed)
    proof = run_etl.generate_load_proof(neon)
    ok = run_etl.reconcile(proof, raw.get("verify", {}))
    print("\nNext: commit etl/SCRAPE_MANIFEST.json + etl/LOAD_PROOF.json and push "
          "(Render redeploys; /health will match live /verify for today).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
