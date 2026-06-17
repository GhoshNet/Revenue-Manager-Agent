# Published skill test scenarios (Phase 3)

Implement these as **filesystem / structure tests** in `tests/test_skills.py`.
You do not need an LLM call for most cases — test the skill pack itself.

Assume skills live under `skills/` as `SKILL.md` files (or nested `*/SKILL.md`).

---

## Scenario 1 — Pack version pin

**Target:** `skills/CHALLENGE_SKILL.md`

**Properties:**

- YAML frontmatter includes `description` containing `otel-rm-v2`
- File exists and is valid UTF-8 markdown

---

## Scenario 2 — Minimum skill count

**Properties:**

- At least **6** distinct `SKILL.md` files under `skills/`
- Each has `name` and `description` in frontmatter

---

## Scenario 3 — Judgment skills (not definitions only)

**Properties:**

- At least **3** skills (50% of minimum pack) include **both**:
  - a **numeric threshold** (e.g. `> 35%`, `>= 0.4`, `ADR below 120`)
  - a **recommended action** (e.g. "shift rate", "close OTA", "review block", "hold BAR")
- Judgment skills must be **≥ 80 words** of body text (excluding frontmatter)

---

## Scenario 4 — Tool routing declared

**Properties:**

- Every skill's body or `description` names at least one required tool
  (`get_otb_summary`, `get_segment_mix`, `get_pickup_delta`, `get_as_of_otb`,
  `get_block_vs_transient_mix`)
- No skill instructs the model to run arbitrary SQL or query
  `reservations_hackathon` directly

---

## Scenario 5 — Distinct routing (no clones)

**Properties:**

- No two skills share the same `name` frontmatter value
- No two `description` fields are identical after normalizing whitespace
- At least one skill targets **pickup / pace**, one **mix / segment**, one **OTB summary**

---

## Scenario 6 — Adversarial guardrail

**Properties:**

- At least one skill explicitly warns against a known trap, e.g.:
  - counting stay rows as reservations
  - using `property_date` for monthly OTB
  - including cancelled or provisional rows in default OTB without caveats

---

## Scenario 7 — Tier D/E readiness (bonus)

**Properties:**

- At least one skill encodes **OTA concentration** or **block concentration** judgment
  (aligns with internal Tier D questions)
- Skill text references `share_of_revenue` or `block_share_of_revenue` semantics
