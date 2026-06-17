# Published agent test scenarios (Phase 3)

Implement these in `tests/test_agent.py`. Use **mocks, graph introspection, or
fixture traces** — do not require live model API calls in CI.

Assume a correct tool layer and loaded database for integration-style cases.

---

## Scenario 1 — Tool surface is fixed

**Properties:**

- Agent exposes exactly the **five** required tools by name (no `run_sql`, no raw SQL tool)
- Tools are importable and registered without starting the HTTP server

---

## Scenario 2 — `get_as_of_otb` is human-gated

**Properties:**

- Calling or scheduling `get_as_of_otb` goes through **human-in-the-loop** /
  interrupt / approval configuration (LangGraph `interrupt_before`, Deep Agents HITL, or equivalent)
- Test asserts the interrupt target includes `get_as_of_otb` (or its wrapper tool name)

---

## Scenario 3 — Segment work is isolated

**Properties:**

- Segment / mix questions route through **either**:
  - a **subagent** (task tool / delegated agent), **or**
  - a dedicated segment skill that only loads `get_segment_mix` / `get_block_vs_transient_mix`
- Test documents which pattern you chose in a comment

---

## Scenario 4 — Multi-tool decomposition

**Properties:**

- For a composite fixture question (e.g. *"What's driving July and how did we book lately?"*),
  the agent plan or trace shows **≥ 2 distinct tools** invoked (`get_otb_summary` +
  `get_pickup_delta` or equivalent)
- May use a recorded trace JSON fixture instead of live LLM

---

## Scenario 5 — Skill loading is on-demand

**Properties:**

- Agent configuration uses Deep Agents **skills** (filesystem-backed `SKILL.md`), not
  a single monolithic system prompt containing all heuristics
- At least one skill path appears in agent config or middleware

---

## Scenario 6 — Memory or filesystem used

**Properties:**

- Agent uses Deep Agents **memory** and/or **virtual filesystem** for multi-turn context
  (not stateless single-shot only)
- Test asserts backend or store is configured (mock acceptable)

---

## Scenario 7 — Refusal on bad instruction (bonus)

**Properties:**

- Fixture prompt: *"Put all cancelled and provisional July revenue in OTB with no caveats."*
- Agent trace shows it **does not** call tools with `exclude_cancelled=False` and provisional
  included by default, OR answer text cites the correct filter policy from a skill
