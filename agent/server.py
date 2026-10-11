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

Request guardrails (see SECURITY.md): model ids are allow-listed, messages are
length-capped, thread ids must be unguessable, requests are rate-limited and
time-boxed, the graph has a step limit, and provider errors are not echoed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from pydantic import BaseModel, Field, field_validator

load_dotenv()

from agent.agent import build_agent  # noqa: E402
from agent.models import DEFAULT_MODEL_ID, MODEL_REGISTRY, available_models, is_available  # noqa: E402

log = logging.getLogger("revenue_manager.server")

# ---- limits (all overridable via env) -------------------------------------
MAX_MESSAGE_CHARS = int(os.environ.get("MAX_MESSAGE_CHARS", "2000"))
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "10"))    # per client
DAILY_REQUEST_CAP = int(os.environ.get("DAILY_REQUEST_CAP", "300"))     # whole app
REQUEST_TIMEOUT_S = int(os.environ.get("REQUEST_TIMEOUT_S", "240"))
RECURSION_LIMIT = int(os.environ.get("AGENT_RECURSION_LIMIT", "150"))   # graph steps

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
# Hosted deployments must set real credentials; the gm/harbour fallback exists
# only for local development and the server refuses to start with it elsewhere.
IS_HOSTED = bool(os.environ.get("RENDER")) or os.environ.get("APP_ENV") == "production"
if IS_HOSTED and not (os.environ.get("APP_USERNAME") and os.environ.get("APP_PASSWORD")):
    raise RuntimeError("APP_USERNAME and APP_PASSWORD must be set in a hosted deployment.")
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


# ---- rate limiting (in-memory; single instance) ---------------------------
_HITS: dict[str, deque[float]] = defaultdict(deque)
_DAY: dict[str, Any] = {"date": "", "count": 0}


def _client_id(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def rate_limit(request: Request, _: str = Depends(require_auth)) -> None:
    # Runs after auth, so unauthenticated traffic cannot use up the daily cap.
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    if _DAY["date"] != today:
        _DAY.update(date=today, count=0)
        _HITS.clear()
    if _DAY["count"] >= DAILY_REQUEST_CAP:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Daily request limit reached.")
    hits = _HITS[_client_id(request)]
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests; slow down.",
                            headers={"Retry-After": "60"})
    hits.append(now)
    _DAY["count"] += 1


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
class _ThreadRequest(BaseModel):
    # The thread id is the only handle on a conversation, so it must be an
    # unguessable client-generated value (the UI uses crypto.randomUUID()).
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{20,64}$")
    model_id: str = DEFAULT_MODEL_ID

    @field_validator("model_id")
    @classmethod
    def _known_model(cls, v: str) -> str:
        entry = MODEL_REGISTRY.get(v)
        if entry is None or not is_available(entry):
            raise ValueError("unknown or unavailable model_id")
        return v


class ChatRequest(_ThreadRequest):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ResumeRequest(_ThreadRequest):
    decision: str = Field(default="approve", pattern=r"^(approve|reject)$")


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


def _text_of(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content or ""


async def _run(agent, payload, config) -> AsyncIterator[str]:
    """Stream graph *updates* (deltas only — no history replay) as SSE events:
    tool/skill calls, tool results, the final answer, and the HITL gate."""
    emitted: set[str] = set()
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_S):
            async for chunk in agent.astream(payload, config=config, stream_mode="updates"):
                if not isinstance(chunk, dict):
                    continue
                for node, update in chunk.items():
                    if node == "__interrupt__" or not isinstance(update, dict):
                        continue
                    for m in update.get("messages", []) or []:
                        mid = getattr(m, "id", None)
                        if mid and mid in emitted:
                            continue
                        if mid:
                            emitted.add(mid)
                        for tc in getattr(m, "tool_calls", None) or []:
                            yield _sse(_classify_tool_call(tc))
                        mtype = getattr(m, "type", "")
                        if mtype == "tool":
                            yield _sse({"type": "tool_result", "tool": getattr(m, "name", "tool"),
                                        "preview": str(getattr(m, "content", ""))[:240]})
                        elif mtype == "ai" and not (getattr(m, "tool_calls", None)):
                            text = _text_of(m)
                            if text.strip():
                                yield _sse({"type": "message", "text": text})

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
    except TimeoutError:
        yield _sse({"type": "error", "error": f"The request took longer than {REQUEST_TIMEOUT_S}s and was stopped."})
    except Exception as exc:  # noqa: BLE001
        yield _sse({"type": "error", "error": _public_error(exc)})


def _public_error(exc: Exception) -> str:
    """Log the real failure; tell the client only what it can act on."""
    ref = uuid.uuid4().hex[:8]
    log.exception("agent run failed (ref %s)", ref)
    text = str(exc).lower()
    if "recursion" in text:
        return "The agent hit its step limit on this question. Try a narrower question."
    if any(k in text for k in ("429", "resource_exhausted", "rate limit", "quota")):
        return "This model's usage quota is exhausted. Pick another model or try again later."
    if any(k in text for k in ("401", "403", "api key", "credit balance", "billing")):
        return "This model is not available right now. Pick another model."
    return f"Something went wrong while answering (ref {ref})."


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}


@app.post("/chat", dependencies=[Depends(rate_limit)])
async def chat(req: ChatRequest, _: str = Depends(require_auth)) -> StreamingResponse:
    agent = get_agent(req.model_id)
    config = _config(req.thread_id)
    payload = {"messages": [{"role": "user", "content": req.message}]}
    return StreamingResponse(_run(agent, payload, config), media_type="text/event-stream")


@app.post("/resume", dependencies=[Depends(rate_limit)])
async def resume(req: ResumeRequest, _: str = Depends(require_auth)) -> StreamingResponse:
    agent = get_agent(req.model_id)
    config = _config(req.thread_id)
    command = Command(resume={"decisions": [{"type": req.decision}]})
    return StreamingResponse(_run(agent, command, config), media_type="text/event-stream")
