"""FastAPI server for the Revenue Manager agent.

Endpoints:
  GET  /health   - DB fingerprint from LOAD_PROOF.json (open; graders call it)
  GET  /models   - switchable chatbots (auth)
  GET  /         - the streaming chat UI (auth)
  POST /chat     - SSE stream of tool/skill calls + answer (auth)
  POST /resume   - approve/reject a human-gated get_as_of_otb call (auth)

Tool/skill visibility: in Deep Agents, loading a skill is a `read_file` tool call
on a SKILL.md, so streaming tool calls surfaces skills automatically. HITL: a
get_as_of_otb call pauses and the UI shows Approve / Reject.
"""
from __future__ import annotations

import json
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from pydantic import BaseModel

load_dotenv()

from agent.agent import build_agent  # noqa: E402
from agent.models import DEFAULT_MODEL_ID, available_models  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
LOAD_PROOF = REPO / "etl" / "LOAD_PROOF.json"
STATIC = Path(__file__).resolve().parent / "static" / "index.html"

# Shared persistence so multi-turn threads survive across requests (single instance).
_CHECKPOINTER = InMemorySaver()
_STORE = InMemoryStore()
_AGENTS: dict[str, Any] = {}


def get_agent(model_id: str):
    if model_id not in _AGENTS:
        _AGENTS[model_id] = build_agent(
            checkpointer=_CHECKPOINTER, store=_STORE, model_id=model_id
        )
    return _AGENTS[model_id]


# ---- auth -----------------------------------------------------------------
security = HTTPBasic()
APP_USERNAME = os.environ.get("APP_USERNAME") or "gm"
APP_PASSWORD = os.environ.get("APP_PASSWORD") or "harbour"


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    ok = secrets.compare_digest(credentials.username, APP_USERNAME) and secrets.compare_digest(
        credentials.password, APP_PASSWORD
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


app = FastAPI(title="Revenue Manager Agent")


# ---- health (open) --------------------------------------------------------
@app.get("/health")
def health() -> dict[str, Any]:
    if not LOAD_PROOF.exists():
        raise HTTPException(503, "LOAD_PROOF.json missing — run the ETL")
    proof = json.loads(LOAD_PROOF.read_text())
    return {
        "status": "ok",
        "db_fingerprint": proof["reservation_stay_status_sha256"],
        "dataset_revision": proof["dataset_revision"],
        "row_hash": proof["load_manifest_row_hash"],
        "financial_status_posted_only_rows": proof["aggregates"]["posted_stay_rows"],
    }


@app.get("/models")
def models(_: str = Depends(require_auth)) -> dict[str, Any]:
    return {"default": DEFAULT_MODEL_ID, "models": available_models()}


@app.get("/")
def index(_: str = Depends(require_auth)) -> FileResponse:
    return FileResponse(str(STATIC))


# ---- chat -----------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    thread_id: str = "default"
    model_id: str = DEFAULT_MODEL_ID


class ResumeRequest(BaseModel):
    thread_id: str = "default"
    model_id: str = DEFAULT_MODEL_ID
    decision: str = "approve"  # "approve" | "reject"


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _classify_tool_call(tc: dict) -> dict:
    name, args = tc.get("name", "?"), tc.get("args", {}) or {}
    if name == "read_file" and "SKILL.md" in str(args.get("file_path", "")):
        skill = str(args["file_path"]).split("/skills/")[-1].split("/")[0]
        return {"type": "skill_loaded", "skill": skill}
    if name == "task":
        return {"type": "subagent", "subagent": args.get("subagent_type", "subagent"),
                "detail": str(args.get("description", ""))[:160]}
    return {"type": "tool_call", "tool": name, "args": args}


async def _run(agent, payload, config) -> AsyncIterator[str]:
    """Stream state updates as SSE events; surface tool/skill calls + final text + HITL."""
    seen = 0
    try:
        async for chunk in agent.astream(payload, config=config, stream_mode="values"):
            msgs = chunk.get("messages", []) if isinstance(chunk, dict) else []
            for m in msgs[seen:]:
                mtype = getattr(m, "type", "")
                for tc in getattr(m, "tool_calls", None) or []:
                    yield _sse(_classify_tool_call(tc))
                if mtype == "tool":
                    yield _sse({"type": "tool_result", "tool": getattr(m, "name", "tool"),
                                "preview": str(getattr(m, "content", ""))[:240]})
                if mtype == "ai" and getattr(m, "content", None) and not (getattr(m, "tool_calls", None)):
                    content = m.content
                    if isinstance(content, list):
                        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                    yield _sse({"type": "message", "text": content})
            seen = len(msgs)

        # Pending human approval? (get_as_of_otb gate)
        snap = await agent.aget_state(config)
        interrupts = getattr(snap, "interrupts", None) or []
        if interrupts:
            val = interrupts[0].value
            reqs = (val or {}).get("action_requests", []) if isinstance(val, dict) else []
            ar = reqs[0] if reqs else {}
            yield _sse({"type": "approval_required", "tool": ar.get("name", "get_as_of_otb"),
                        "args": ar.get("args", {})})
        else:
            yield _sse({"type": "done"})
    except Exception as exc:  # noqa: BLE001
        yield _sse({"type": "error", "error": str(exc)})


@app.post("/chat")
async def chat(req: ChatRequest, _: str = Depends(require_auth)) -> StreamingResponse:
    agent = get_agent(req.model_id)
    config = {"configurable": {"thread_id": req.thread_id}}
    payload = {"messages": [{"role": "user", "content": req.message}]}
    return StreamingResponse(_run(agent, payload, config), media_type="text/event-stream")


@app.post("/resume")
async def resume(req: ResumeRequest, _: str = Depends(require_auth)) -> StreamingResponse:
    agent = get_agent(req.model_id)
    config = {"configurable": {"thread_id": req.thread_id}}
    decision = "approve" if req.decision == "approve" else "reject"
    command = Command(resume={"decisions": [{"type": decision}]})
    return StreamingResponse(_run(agent, command, config), media_type="text/event-stream")
