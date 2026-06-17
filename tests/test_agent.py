"""Agent wiring tests (Phase 3) — graph introspection + module constants + a
recorded trace fixture. No live LLM calls.

Covers tests/AGENT_TEST_SCENARIOS.md.

Subagent-vs-skill choice (Scenario 3): we route segment/mix and block work to a
dedicated **subagent** ('segment-analyst') that holds ONLY get_segment_mix and
get_block_vs_transient_mix — see test_segment_work_isolated_via_subagent.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("deepagents")

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "get_otb_summary", "get_segment_mix", "get_pickup_delta",
    "get_as_of_otb", "get_block_vs_transient_mix",
}


@pytest.fixture(autouse=True)
def _dummy_key(monkeypatch):
    # Model construction needs a key present, but no API call is made.
    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "sk-ant-test-dummy"))


@pytest.fixture(scope="module")
def graph():
    from agent.agent import build_agent
    return build_agent()


# --- Scenario 1 — tool surface is exactly the five required tools ----------
def test_tool_surface_is_exactly_five():
    from agent.agent import ALL_TOOLS  # importable without starting a server
    names = {fn.__name__ for fn in ALL_TOOLS}
    assert names == REQUIRED
    assert "run_sql" not in names and not any("sql" in n for n in names)


# --- Scenario 2 — get_as_of_otb is human-gated -----------------------------
def test_get_as_of_otb_is_human_gated(graph):
    from agent.agent import INTERRUPT_ON
    assert INTERRUPT_ON.get("get_as_of_otb") is True
    # Only the expensive point-in-time tool is gated.
    assert set(INTERRUPT_ON) == {"get_as_of_otb"}
    # The HITL middleware is actually wired into the compiled graph.
    nodes = set(graph.get_graph().nodes)
    assert any("HumanInTheLoop" in n for n in nodes), nodes


# --- Scenario 3 — segment work isolated via subagent -----------------------
def test_segment_work_isolated_via_subagent():
    from agent.agent import SEGMENT_SUBAGENT
    assert SEGMENT_SUBAGENT["name"] == "segment-analyst"
    sub_tools = {fn.__name__ for fn in SEGMENT_SUBAGENT["tools"]}
    assert sub_tools == {"get_segment_mix", "get_block_vs_transient_mix"}
    # It must NOT carry the OTB/pickup/as-of tools — isolation.
    assert "get_pickup_delta" not in sub_tools and "get_as_of_otb" not in sub_tools


# --- Scenario 4 — multi-tool decomposition (recorded trace fixture) --------
def test_composite_question_uses_multiple_tools():
    trace = json.loads((ROOT / "tests" / "fixtures" / "composite_trace.json").read_text())
    tools_used = {c["name"] for c in trace["tool_calls"]}
    assert len(tools_used) >= 2
    assert tools_used <= REQUIRED
    assert "get_otb_summary" in tools_used  # headline before drivers/pace


# --- Scenario 5 — skills load on-demand (not a monolithic prompt) ----------
def test_skills_loaded_on_demand(graph):
    from agent.agent import SKILLS_SOURCES, SYSTEM_PROMPT
    assert "/skills/" in SKILLS_SOURCES
    assert len(list((ROOT / "skills").glob("*/SKILL.md"))) >= 6
    # The skills middleware is wired in.
    nodes = set(graph.get_graph().nodes)
    assert any("Skills" in n for n in nodes), nodes
    # Heuristics live in skills, not inlined into the system prompt.
    assert "35%" not in SYSTEM_PROMPT and "share_of_revenue >" not in SYSTEM_PROMPT


# --- Scenario 6 — memory / filesystem configured ---------------------------
def test_memory_and_filesystem_configured(graph):
    assert graph.checkpointer is not None  # multi-turn thread memory
    assert graph.store is not None         # long-term cross-thread store
    from agent.agent import make_backend
    from deepagents.backends import FilesystemBackend
    assert isinstance(make_backend(), FilesystemBackend)


# --- Scenario 7 (bonus) — guardrail against a bad instruction --------------
def test_guardrail_skill_forbids_silent_bad_filter():
    body = (ROOT / "skills" / "cancellation-provisional-guardrails" / "SKILL.md").read_text().lower()
    assert "no caveat" in body or "silently" in body
    assert "exclude" in body and "provisional" in body and "cancelled" in body
