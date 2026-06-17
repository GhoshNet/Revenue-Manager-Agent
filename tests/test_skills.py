"""Skill-pack structural / judgment tests (Phase 3) — no LLM calls.

Covers tests/SKILL_TEST_SCENARIOS.md. Skills live as skills/<name>/SKILL.md
(loaded by deepagents) plus the brief-required skills/CHALLENGE_SKILL.md pack
manifest (pinned to otel-rm-v2).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"
REQUIRED_TOOLS = {
    "get_otb_summary", "get_segment_mix", "get_pickup_delta",
    "get_as_of_otb", "get_block_vs_transient_mix",
}
ACTION_WORDS = (
    "recommend", "shift", "review", "protect", "diversify", "chase", "hold",
    "open a", "cap ", "pull back", "tighten", "confirm",
)
THRESHOLD_RE = re.compile(r"(\d+\s*%|[<>]=?\s*\d|\b0\.\d|\bbelow\s+\d)")


def _parse(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert m, f"{path} missing YAML frontmatter"
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


def _nested_skills() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


# --- Scenario 1 — pack version pin -----------------------------------------
def test_challenge_skill_pins_pack_version():
    fm, body = _parse(SKILLS_DIR / "CHALLENGE_SKILL.md")
    assert "otel-rm-v2" in fm.get("description", "")
    assert body.strip(), "CHALLENGE_SKILL.md has empty body"


# --- Scenario 2 — minimum skill count --------------------------------------
def test_minimum_six_skills_with_frontmatter():
    skills = _nested_skills()
    assert len(skills) >= 6, f"only {len(skills)} skills"
    for p in skills:
        fm, _ = _parse(p)
        assert fm.get("name") and fm.get("description"), p


def test_skill_name_matches_directory():
    # deepagents requires frontmatter name == parent directory name.
    for p in _nested_skills():
        fm, _ = _parse(p)
        assert fm["name"] == p.parent.name, f"{p}: name {fm['name']} != dir {p.parent.name}"


# --- Scenario 3 — judgment skills (threshold + action, >=80 words) ----------
def test_at_least_three_judgment_skills():
    judgment = []
    for p in _nested_skills():
        _, body = _parse(p)
        words = len(body.split())
        has_threshold = bool(THRESHOLD_RE.search(body))
        has_action = any(w in body.lower() for w in ACTION_WORDS)
        if has_threshold and has_action and words >= 80:
            judgment.append(p.parent.name)
    assert len(judgment) >= 3, f"only {len(judgment)} judgment skills: {judgment}"


# --- Scenario 4 — tool routing declared, no raw SQL ------------------------
def test_every_skill_names_a_required_tool():
    for p in _nested_skills():
        fm, body = _parse(p)
        blob = (fm.get("description", "") + " " + body)
        assert any(t in blob for t in REQUIRED_TOOLS), f"{p} names no required tool"


def test_no_skill_instructs_raw_sql():
    for p in _nested_skills() + [SKILLS_DIR / "CHALLENGE_SKILL.md"]:
        _, body = _parse(p)
        assert "```sql" not in body.lower()
        assert "run_sql" not in body.lower()


# --- Scenario 5 — distinct routing (no clones) -----------------------------
def test_distinct_names_and_descriptions():
    names, descs = [], []
    for p in _nested_skills():
        fm, _ = _parse(p)
        names.append(fm["name"])
        descs.append(re.sub(r"\s+", " ", fm["description"]).strip())
    assert len(names) == len(set(names))
    assert len(descs) == len(set(descs))


def test_covers_pickup_mix_and_otb():
    blobs = {p.parent.name: _parse(p)[1].lower() for p in _nested_skills()}
    assert any("pickup" in b or "pace" in b for b in blobs.values())
    assert any("segment" in b or "mix" in b for b in blobs.values())
    assert any("get_otb_summary" in b or "on the books" in b for b in blobs.values())


# --- Scenario 6 — adversarial guardrail ------------------------------------
def test_has_adversarial_guardrail():
    traps = ("cancelled", "provisional", "property_date", "row_count")
    found = False
    for p in _nested_skills():
        _, body = _parse(p)
        low = body.lower()
        if sum(t in low for t in traps) >= 2:
            found = True
    assert found, "no skill warns against the known OTB traps"


# --- Scenario 7 (bonus) — Tier D readiness (concentration) -----------------
def test_concentration_skill_references_revenue_share():
    hits = []
    for p in _nested_skills():
        _, body = _parse(p)
        low = body.lower()
        if ("ota" in low or "block" in low) and (
            "share_of_revenue" in low or "block_share_of_revenue" in low
        ):
            hits.append(p.parent.name)
    assert hits, "no OTA/block concentration skill references *_share_of_revenue"
