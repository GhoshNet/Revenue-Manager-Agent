"""Live end-to-end smoke test of the Revenue Manager agent.

Requires a real model key in .env (ANTHROPIC_API_KEY or GROQ_API_KEY) and the
loaded local Postgres. Prints each tool/skill call and the final answer.

Usage:
  python scripts/smoke_agent.py
  python scripts/smoke_agent.py "How much group business do we have in September 2026?"
  AGENT_MODEL_ID=groq-llama-3.3-70b python scripts/smoke_agent.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from agent.agent import build_agent  # noqa: E402

DEFAULT_Q = (
    "Are we too dependent on OTA in August 2026? "
    "Give me the numbers and a recommendation."
)


def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_Q
    agent = build_agent()
    config = {"configurable": {"thread_id": "smoke-1"}}

    print(f"\nQ: {question}\n" + "=" * 70)
    seen = 0
    final = None
    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": question}]},
        config=config,
        stream_mode="values",
    ):
        msgs = chunk.get("messages", [])
        for m in msgs[seen:]:
            role = getattr(m, "type", "?")
            tool_calls = getattr(m, "tool_calls", None) or []
            for tc in tool_calls:
                print(f"  → tool call: {tc['name']}({tc.get('args', {})})")
            if role == "tool":
                preview = str(getattr(m, "content", ""))[:160].replace("\n", " ")
                print(f"  ← {getattr(m, 'name', 'tool')}: {preview}")
            if role == "ai" and getattr(m, "content", None) and not tool_calls:
                final = m.content
        seen = len(msgs)

    print("=" * 70)
    print("\nFINAL ANSWER:\n")
    if isinstance(final, list):  # some providers return content blocks
        final = " ".join(b.get("text", "") for b in final if isinstance(b, dict))
    print(final or "(no final text captured)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
