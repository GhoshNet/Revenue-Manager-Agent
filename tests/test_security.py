"""Security guardrail tests — see SECURITY.md. No live LLM calls.

Agent sandbox: the model can read skills/ and nothing else on disk, cannot write
anywhere on disk, and is never offered write/edit/shell tools.
Server: model allow-list, input limits, unguessable thread ids, rate limiting,
hosted deployments refuse default credentials, provider errors are not echoed.
Tool layer: read-only database sessions and validated inputs.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import psycopg
import pytest

pytest.importorskip("deepagents")

ROOT = Path(__file__).resolve().parents[1]
AUTH = ("gm", "harbour")
THREAD = "test-thread-0123456789abcdef"


@pytest.fixture(autouse=True)
def _dummy_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "sk-ant-test-dummy"))


# --- Agent sandbox ---------------------------------------------------------
def test_backend_reads_skills_but_refuses_writes():
    from agent.agent import make_backend
    backend = make_backend()
    assert "ota-channel-concentration" in str(backend.read("/skills/ota-channel-concentration/SKILL.md"))
    result = backend.write("/skills/injected/SKILL.md", "malicious")
    assert result.error and "read-only" in result.error
    assert not (ROOT / "skills" / "injected").exists()
    edit = backend.edit("/skills/ota-channel-concentration/SKILL.md", "OTA", "X")
    assert edit.error and "read-only" in edit.error


def test_backend_cannot_escape_skills_directory():
    from agent.agent import make_backend
    backend = make_backend()
    for path in ("/skills/../.env", "/skills/../agent/agent.py", "/skills/../../../etc/hosts"):
        try:
            leaked = str(backend.read(path))
        except ValueError:
            continue  # traversal rejected outright
        assert "ANTHROPIC_API_KEY" not in leaked and "create_deep_agent" not in leaked


def test_repo_files_outside_skills_are_not_on_the_agent_disk():
    from agent.agent import make_backend
    # Only /skills/ is routed to disk; every other path belongs to per-thread state.
    assert set(make_backend().routes) == {"/skills/"}


def _offered_tools(middleware_cls):
    from langchain_core.tools import tool

    @tool
    def write_file(x: str) -> str:
        """w"""
        return x

    @tool
    def read_file(x: str) -> str:
        """r"""
        return x

    seen = {}

    class Req:
        tools = [write_file, read_file, {"name": "execute"}, {"name": "edit_file"}]

        def override(self, tools):
            seen["tools"] = tools
            return self

    middleware_cls().wrap_model_call(Req(), lambda r: None)
    return {t["name"] if isinstance(t, dict) else t.name for t in seen["tools"]}


def test_write_and_shell_tools_are_never_offered_to_the_model():
    from agent.agent import HIDDEN_TOOLS, HiddenToolsMiddleware
    assert {"write_file", "edit_file", "execute"} <= HIDDEN_TOOLS
    assert _offered_tools(HiddenToolsMiddleware) == {"read_file"}


def test_prompt_states_scope_and_injection_boundaries():
    from agent.agent import SYSTEM_PROMPT
    text = SYSTEM_PROMPT.lower()
    assert "data, never instructions" in text
    assert "decline" in text and "credentials" in text


# --- Server ----------------------------------------------------------------
@pytest.fixture()
def client(monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    for var in ("RENDER", "APP_ENV", "APP_USERNAME", "APP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_USERNAME", "")
    monkeypatch.setenv("APP_PASSWORD", "")
    import agent.server as server
    server = importlib.reload(server)
    server._HITS.clear()
    server._DAY.update(date="", count=0)
    return TestClient(server.app), server


def test_chat_requires_auth(client):
    c, _ = client
    assert c.post("/chat", json={"message": "hi", "thread_id": THREAD}).status_code == 401


def test_unknown_model_id_is_rejected(client):
    c, _ = client
    for bad in ("anthropic:claude-opus-4-1", "openai:gpt-4.1", "not-a-model"):
        r = c.post("/chat", auth=AUTH, json={"message": "hi", "thread_id": THREAD, "model_id": bad})
        assert r.status_code == 422, bad


def test_guessable_or_missing_thread_id_is_rejected(client):
    c, _ = client
    assert c.post("/chat", auth=AUTH, json={"message": "hi"}).status_code == 422
    assert c.post("/chat", auth=AUTH, json={"message": "hi", "thread_id": "default"}).status_code == 422
    assert c.post("/resume", auth=AUTH, json={"thread_id": "default"}).status_code == 422


def test_oversized_and_empty_messages_are_rejected(client):
    c, server = client
    big = "x" * (server.MAX_MESSAGE_CHARS + 1)
    assert c.post("/chat", auth=AUTH, json={"message": big, "thread_id": THREAD}).status_code == 422
    assert c.post("/chat", auth=AUTH, json={"message": "", "thread_id": THREAD}).status_code == 422


def test_resume_decision_must_be_approve_or_reject(client):
    c, _ = client
    r = c.post("/resume", auth=AUTH, json={"thread_id": THREAD, "decision": "anything"})
    assert r.status_code == 422


def test_rate_limit_returns_429(client):
    c, server = client
    # Invalid bodies still count against the limit, so no model is ever called here.
    codes = [c.post("/chat", auth=AUTH, json={"message": ""}).status_code
             for _ in range(server.RATE_LIMIT_PER_MIN + 1)]
    assert codes[-1] == 429 and 429 not in codes[:-1]


def test_hosted_deployment_refuses_default_credentials(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_USERNAME", "")
    monkeypatch.setenv("APP_PASSWORD", "")
    import agent.server as server
    with pytest.raises(RuntimeError):
        importlib.reload(server)
    monkeypatch.delenv("APP_ENV")
    importlib.reload(server)


def test_provider_errors_are_not_echoed_to_the_client(client):
    _, server = client
    secret = "postgresql://user:hunter2@db.internal/prod request_id=req_123"
    msg = server._public_error(RuntimeError(secret))
    assert "hunter2" not in msg and "req_123" not in msg
    assert "quota" in server._public_error(RuntimeError("429 RESOURCE_EXHAUSTED")).lower()


# --- Tool layer ------------------------------------------------------------
def test_tool_connections_are_read_only():
    from tools import db
    with db.get_conn() as conn, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        conn.execute("create temporary table _should_not_exist (x int)")
        conn.execute("delete from public.load_manifest")


def test_stay_month_is_validated():
    from tools import db
    for bad in ("2026-13", "2026-8", "26-08", "2026-08-01", "2026-08'; drop table x;--"):
        with pytest.raises(ValueError):
            db.month_bounds(bad)
    assert db.month_bounds("2026-12")[1].year == 2027


def test_pickup_window_is_anchored_to_the_dataset(monkeypatch):
    from tools import db, revenue_tools as rt
    monkeypatch.delenv("DATA_AS_OF", raising=False)
    res = rt.get_pickup_delta(booking_window_days=30, future_stay_from="2026-07-01")
    assert res["window_end_utc"] == db.data_as_of().astimezone(db.timezone.utc).isoformat()
    monkeypatch.setenv("DATA_AS_OF", "2026-06-10T00:00:00Z")
    assert rt.get_pickup_delta(7, "2026-07-01")["window_end_utc"].startswith("2026-06-10")
