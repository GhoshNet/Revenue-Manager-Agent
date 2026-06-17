"""ETL orchestrator: scrape -> transform -> load -> proofs -> reconcile.

Usage:
  python -m etl.run_etl                 # fresh scrape + load + proofs
  python -m etl.run_etl --no-scrape     # reuse newest etl/raw/scrape_*.json
  python -m etl.run_etl --limit 20      # dev: only first 20 reservations
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

from etl import load as load_mod
from etl import scrape as scrape_mod
from etl import transform as transform_mod

ETL_DIR = Path(__file__).resolve().parent
ROOT = ETL_DIR.parent
RAW_DIR = ETL_DIR / "raw"
MANIFEST_PATH = ETL_DIR / "SCRAPE_MANIFEST.json"
LOAD_PROOF_PATH = ETL_DIR / "LOAD_PROOF.json"


def _newest_raw() -> dict:
    files = sorted(glob.glob(str(RAW_DIR / "scrape_*.json")))
    if not files:
        raise SystemExit("No etl/raw/scrape_*.json found — run without --no-scrape first.")
    print(f"Using cached scrape: {files[-1]}")
    return json.loads(Path(files[-1]).read_text())


def write_scrape_manifest(raw: dict, transformed: dict) -> dict:
    facts = transformed["fact_rows"]
    n_ids, ids_sha = transform_mod.reservation_ids_sha256(facts)
    verify = raw.get("verify", {})
    manifest = {
        "anchor_date": raw["anchor_date"],
        "dataset_revision": raw["dataset_revision"],
        "scraped_at": raw["scraped_at"],
        "source_url": raw["source_url"],
        "pages_scraped": (int(verify.get("total_reservations", n_ids)) + 99) // 100,
        "reservation_ids_count": n_ids,
        "reservation_ids_sha256": ids_sha,
        "total_stay_rows": len(facts),
        "reservation_stay_status_sha256": transformed["fingerprint"],
        "reservations_reached": len(raw["reservations"]),
        "verify_total_reservations": verify.get("total_reservations"),
        "notes": (
            "reservation_ids_count must match count(distinct reservation_id) in DB "
            "and total_reservations on /verify. Reservations include the four "
            "edge-case ids (RES-EDGE-001..003, RES-ZEPHYR-7F3A) that the list UI "
            "does not render but the list server action returns."
        ),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {MANIFEST_PATH}")
    return manifest


def generate_load_proof(database_url: str) -> dict:
    env = dict(os.environ, DATABASE_URL=database_url)
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "compute_load_fingerprint.py"),
        "--manifest", str(MANIFEST_PATH),
        "--output", str(LOAD_PROOF_PATH),
    ]
    subprocess.run(cmd, check=True, env=env)
    return json.loads(LOAD_PROOF_PATH.read_text())


def reconcile(proof: dict, verify: dict) -> bool:
    agg = proof["aggregates"]
    rc = proof["row_counts"]
    checks = [
        ("total_stay_rows", rc["reservations_hackathon"], verify.get("total_stay_rows")),
        ("reservation_stay_status_sha256",
         proof["reservation_stay_status_sha256"], verify.get("reservation_stay_status_sha256")),
        ("dataset_revision", proof["dataset_revision"], verify.get("dataset_revision")),
        ("cancelled_reservations", agg["cancelled_reservation_count"], verify.get("cancelled_reservations")),
        ("provisional_row_count", agg["provisional_row_count"], verify.get("provisional_row_count")),
        ("property_date_mismatch_count", agg["property_date_mismatch_count"], verify.get("property_date_mismatch_count")),
    ]
    print("\n=== Reconciliation vs /verify ===")
    ok = True
    for name, got, exp in checks:
        match = str(got) == str(exp)
        ok = ok and match
        flag = "OK " if match else "XX "
        g = str(got)[:18] + "…" if len(str(got)) > 19 else got
        e = str(exp)[:18] + "…" if len(str(exp)) > 19 else exp
        print(f"  {flag}{name}: db={g} verify={e}")
    print("=== RECONCILED ===" if ok else "=== MISMATCH (see above) ===")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-scrape", action="store_true", help="reuse newest cached scrape")
    ap.add_argument("--limit", type=int, default=None, help="dev: first N reservations")
    args = ap.parse_args()

    raw = _newest_raw() if args.no_scrape else scrape_mod.scrape_all(limit=args.limit)
    transformed = transform_mod.transform(raw)
    print(f"Transformed: {len(transformed['fact_rows'])} fact rows, "
          f"fingerprint={transformed['fingerprint'][:16]}…")

    db_url = load_mod.get_database_url()
    summary = load_mod.load(transformed, db_url)
    print(f"Loaded: {summary['row_counts']}")

    write_scrape_manifest(raw, transformed)
    proof = generate_load_proof(db_url)
    ok = reconcile(proof, raw.get("verify", {}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
