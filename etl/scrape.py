"""Extract step: scrape the Grand Harbour Hotel data site.

The site is a Next.js app whose data is delivered through **React Server
Actions** (POST endpoints discovered at runtime), not a public JSON API and not
static HTML — plain HTTP fetches return an empty shell. We drive a real browser
(Playwright) to (a) render the app and (b) discover the current server-action
ids, then call those actions directly to pull clean, typed JSON.

Why actions instead of parsing the rendered table:
  The reservation **list UI silently renders only 50 of page 3's 54 rows** — it
  drops four edge-case reservations with non-sequential ids (RES-EDGE-001..003,
  RES-ZEPHYR-7F3A) that carry the property_date-mismatch audit rows. The list
  *action* returns all 254. Trusting the DOM would lose them; calling the action
  the page itself uses is both faithful and complete. Action ids are rediscovered
  every run, so the scraper survives site redeploys.

No database access here — extraction is cleanly separated from transform/load.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, sync_playwright

BASE_URL = os.environ.get("DATA_SITE_URL", "https://otel-hackathon-data-site.vercel.app")
RAW_DIR = Path(__file__).resolve().parent / "raw"
_ACTION_HEADERS_CT = "text/plain;charset=UTF-8"


# ---------------------------------------------------------------------------
# Server-action plumbing
# ---------------------------------------------------------------------------
def _rsc_value(text: str, require_keys: tuple[str, ...]) -> Any:
    """Parse a Next.js server-action response (lines like `1:{...}`) and return
    the first decoded value that is a dict containing all require_keys."""
    for line in text.splitlines():
        if ":" not in line:
            continue
        payload = line.split(":", 1)[1]
        try:
            val = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(val, dict) and all(k in val for k in require_keys):
            return val
    return None


def _call_action(ctx: BrowserContext, url: str, action_id: str, args: list) -> Any:
    resp = ctx.request.post(
        url,
        headers={"Next-Action": action_id, "Content-Type": _ACTION_HEADERS_CT},
        data=json.dumps(args),
    )
    if resp.status != 200:
        raise RuntimeError(f"action {action_id} on {url} -> HTTP {resp.status}")
    return resp.text()


def _discover_actions(page: Page) -> tuple[str, str]:
    """Render the list + a detail page and capture their server-action ids."""
    captured: dict[str, str] = {}

    def on_request(req):
        aid = req.headers.get("next-action")
        if req.method == "POST" and aid:
            key = "detail" if "/reservations/" in req.url else "list"
            captured.setdefault(key, aid)

    page.on("request", on_request)
    page.goto(f"{BASE_URL}/reservations", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(1500)
    page.goto(f"{BASE_URL}/reservations/R0001", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(1500)
    page.remove_listener("request", on_request)

    if "list" not in captured or "detail" not in captured:
        raise RuntimeError(f"Could not discover server actions: {captured}")
    return captured["list"], captured["detail"]


# ---------------------------------------------------------------------------
# /verify and /reference
# ---------------------------------------------------------------------------
def scrape_verify(page: Page) -> dict[str, Any]:
    """Return the /verify raw-JSON object (anchor_date, revision, checksums…)."""
    page.goto(f"{BASE_URL}/verify", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(1500)
    for sel in ("text=Raw JSON", "summary:has-text('Raw JSON')", "button:has-text('Raw JSON')"):
        try:
            page.click(sel, timeout=2000)
            break
        except Exception:
            continue
    page.wait_for_timeout(600)
    body = page.inner_text("body")
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError("Could not locate raw JSON on /verify")
    return json.loads(body[start : end + 1])


def _scrape_table(page: Page) -> list[dict[str, str]]:
    page.wait_for_selector("table", timeout=15000)
    return page.eval_on_selector(
        "table",
        """tbl => {
            const headers = [...tbl.querySelectorAll('thead th')].map(th => th.innerText.trim());
            return [...tbl.querySelectorAll('tbody tr')].map(tr => {
                const cells = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
                const o = {};
                headers.forEach((h, i) => o[h] = cells[i] ?? null);
                return o;
            });
        }""",
    )


def scrape_reference(page: Page) -> dict[str, list[dict[str, str]]]:
    """Scrape all five reference tabs into raw row dicts (keyed by table name)."""
    page.goto(f"{BASE_URL}/reference", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(1200)
    tabs = {
        "room_type_lookup": "Room types",
        "market_code_lookup": "Markets",
        "channel_code_lookup": "Channels",
        "rate_plan_lookup": "Rate plans",
        "market_macro_group_history": "Macro history",
    }
    out: dict[str, list[dict[str, str]]] = {}
    for key, label in tabs.items():
        page.click(f"button:has-text('{label}')", timeout=10000)
        page.wait_for_timeout(500)
        out[key] = _scrape_table(page)
    return out


# ---------------------------------------------------------------------------
# Reservations (list action for ids, detail action for full records)
# ---------------------------------------------------------------------------
def fetch_reservation_ids(ctx: BrowserContext, list_action: str) -> tuple[list[str], int]:
    """Page through the list action to collect every reservation_id.

    Uses the action's own totalItems/totalPages metadata (the source of truth),
    not the rendered DOM, so the four edge-case reservations are included.
    """
    url = f"{BASE_URL}/reservations"
    first = _rsc_value(_call_action(ctx, url, list_action, [1, 100]), ("items", "totalItems"))
    if first is None:
        raise RuntimeError("list action returned no items payload")
    total_items = int(first["totalItems"])
    total_pages = int(first.get("totalPages") or 1)

    ids: list[str] = [it["reservation_id"] for it in first["items"]]
    for pg in range(2, total_pages + 1):
        payload = _rsc_value(_call_action(ctx, url, list_action, [pg, 100]), ("items",))
        ids.extend(it["reservation_id"] for it in payload["items"])

    ids = sorted(set(ids))
    if len(ids) != total_items:
        print(
            f"  NOTE: collected {len(ids)} distinct ids vs totalItems={total_items} "
            f"(site metadata). Proceeding with all reachable reservations; the "
            f"load fingerprint is reconciled against /verify after load.",
            flush=True,
        )
    return ids, total_items


def fetch_detail(ctx: BrowserContext, detail_action: str, rid: str) -> dict[str, Any] | None:
    url = f"{BASE_URL}/reservations/{rid}"
    rec = _rsc_value(_call_action(ctx, url, detail_action, [rid]), ("reservation_id", "stay_rows"))
    return rec


def scrape_all(*, persist: bool = True, limit: int | None = None) -> dict[str, Any]:
    """Full extract via server actions. limit = first N reservations (dev only)."""
    t0 = time.time()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()

        print("→ discovering server actions …", flush=True)
        list_action, detail_action = _discover_actions(page)
        print(f"  list={list_action[:12]}… detail={detail_action[:12]}…", flush=True)

        print("→ /verify …", flush=True)
        verify = scrape_verify(page)
        anchor_date = verify["anchor_date"]
        print(f"  anchor_date={anchor_date} revision={verify['dataset_revision']} "
              f"total_reservations={verify['total_reservations']} "
              f"total_stay_rows={verify['total_stay_rows']}", flush=True)

        print("→ /reference …", flush=True)
        reference = scrape_reference(page)
        print(f"  {dict((k, len(v)) for k, v in reference.items())}", flush=True)

        print("→ reservation ids (list action) …", flush=True)
        ids, total_items = fetch_reservation_ids(ctx, list_action)
        print(f"  {len(ids)} reservations (totalItems={total_items})", flush=True)

        if limit:
            ids = ids[:limit]
        print("→ detail records (detail action) …", flush=True)
        reservations, missing = [], []
        for i, rid in enumerate(ids, 1):
            rec = fetch_detail(ctx, detail_action, rid)
            if rec is None:
                missing.append(rid)
            else:
                reservations.append(rec)
            if i % 50 == 0 or i == len(ids):
                print(f"  detail {i}/{len(ids)}", flush=True)
        if missing:
            print(f"  WARNING: {len(missing)} ids had no detail record: {missing}", flush=True)

        browser.close()

    result = {
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_url": BASE_URL,
        "anchor_date": anchor_date,
        "dataset_revision": verify["dataset_revision"],
        "verify": verify,
        "reference": reference,
        "reservations": reservations,
        "reservation_ids_listed": ids,
        "missing_detail_ids": missing,
    }
    if persist:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        out = RAW_DIR / f"scrape_{anchor_date}.json"
        out.write_text(json.dumps(result, indent=2))
        print(f"→ wrote {out} ({time.time() - t0:.0f}s, {len(reservations)} reservations)", flush=True)
    return result


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    scrape_all(limit=lim)
