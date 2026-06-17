"""Revenue Manager Deep Agent.

A single create_deep_agent() assembled from the framework's building blocks:

  - Tools        : the five required data tools (no run_sql)
  - Skills       : SKILL.md files under skills/, loaded on demand (progressive
                   disclosure) via the FilesystemBackend
  - Subagent     : `segment-analyst`, given only the segment/block tools, so
                   mix reasoning is isolated (brief: segment work routed to a subagent)
  - Planning     : deepagents' built-in write_todos / todo planning
  - Memory       : thread checkpointer (multi-turn) + long-term store + virtual FS
  - HITL         : interrupt_on get_as_of_otb (expensive point-in-time rebuild)
  - Model/prompt : a sharp revenue-manager persona holding the brief's answer style

Importing this module does not call the LLM or touch the database.
"""
from __future__ import annotations

import os
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

from agent.models import resolve_spec
from tools.revenue_tools import (
    ALL_TOOLS,
    get_block_vs_transient_mix,
    get_segment_mix,
)

REPO = Path(__file__).resolve().parents[1]

# Default model spec (a registry id or raw provider:model). The UI/build_agent
# can override per session — see agent/models.py for switchable options.
AGENT_MODEL = resolve_spec(os.environ.get("AGENT_MODEL_ID"))

# Skills live on disk under skills/<name>/SKILL.md and are read through the
# backend; nothing is inlined into the system prompt (progressive disclosure).
SKILLS_SOURCES = ["/skills/"]

# Human-in-the-loop: the only data tool gated behind approval is the expensive
# point-in-time rebuild. Approving forces the analyst to confirm the snapshot.
INTERRUPT_ON: dict[str, bool] = {"get_as_of_otb": True}

SYSTEM_PROMPT = """\
You are the Revenue Manager Agent for the General Manager of the Grand Harbour
Hotel. You turn reservation data into commercial judgment: what is changing in
future business, why it matters, and what to do next.

How you work:
- Answer from the five data tools only — get_otb_summary, get_segment_mix,
  get_pickup_delta, get_as_of_otb, get_block_vs_transient_mix. Never ask for or
  write raw SQL.
- Before answering a multi-part question, plan the ordered tool calls (use the
  todo/planning tool), then execute them.
- Load the relevant SKILL for the question type; the skills carry the thresholds
  and recommended actions. Do not hard-code heuristics you have not loaded.
- For segment-mix, OTA/channel, and group/block questions, delegate to the
  `segment-analyst` subagent via the task tool so mix reasoning stays isolated.
- For as-of / point-in-time questions, CALL get_as_of_otb directly with the
  parsed stay_month and as_of timestamp. The framework automatically pauses for
  human approval before it runs — do NOT ask the user to confirm in plain text;
  just call the tool and let the approval gate handle it.

Grain and filters you never get wrong:
- reservation_count = distinct reservations; row_count = stay rows (never report
  row_count as bookings); room_nights = sum(number_of_spaces).
- Default on-the-books excludes cancelled and provisional business and filters on
  stay_date. Include cancelled/provisional only when explicitly asked, and label
  it clearly with a caveat — never silently.
- Pickup/pace uses booking date (create_datetime); monthly/stay analysis uses
  stay_date; never property_date for a stay month. Use the effective macro group.

Answer style — a sharp morning briefing, not a dashboard:
- Lead with the headline numbers (reservations, room nights, total revenue, ADR).
- Explain the two or three drivers and quantify them.
- Name one risk or opportunity and recommend one concrete action.
- State assumptions when a question is ambiguous.
"""

# Subagent: isolates segment / block mix work with only the relevant tools.
SEGMENT_SUBAGENT: dict = {
    "name": "segment-analyst",
    "description": (
        "Specialist for segment mix, OTA/channel concentration, and group/block "
        "concentration questions. Delegate any mix or concentration analysis here."
    ),
    "system_prompt": (
        "You are a segment-mix specialist for a hotel revenue manager. Use "
        "get_segment_mix for market/macro-group mix and OTA concentration, and "
        "get_block_vs_transient_mix for group vs transient and company "
        "concentration. Load the ota-channel-concentration, segment-mix-shift, or "
        "group-block-concentration skill as appropriate. Report shares (0-1) with "
        "the threshold judgment and one recommended action. Use the effective "
        "macro group; never use raw SQL."
    ),
    "tools": [get_segment_mix, get_block_vs_transient_mix],
    "skills": SKILLS_SOURCES,
}


def make_backend() -> FilesystemBackend:
    # Skills are read from disk under REPO/skills/; virtual_mode keeps any agent
    # file writes in state so the repo is never modified at runtime.
    return FilesystemBackend(root_dir=str(REPO), virtual_mode=True)


def build_agent(checkpointer=None, store=None, model_id: str | None = None):
    """Construct the compiled Deep Agent graph.

    model_id selects the chatbot (see agent/models.py — Claude, Groq, Ollama…);
    None uses the default. checkpointer enables multi-turn memory per conversation
    thread; store provides long-term cross-thread memory. Defaults are in-memory
    (fine for dev/tests); pass durable backends for deployment.
    """
    model = resolve_spec(model_id) if model_id else AGENT_MODEL
    if checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()
    if store is None:
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()

    return create_deep_agent(
        model=model,
        tools=list(ALL_TOOLS),
        system_prompt=SYSTEM_PROMPT,
        skills=SKILLS_SOURCES,
        subagents=[SEGMENT_SUBAGENT],
        interrupt_on=INTERRUPT_ON,
        backend=make_backend(),
        checkpointer=checkpointer,
        store=store,
        name="revenue-manager-agent",
    )
