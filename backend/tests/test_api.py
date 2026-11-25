"""API tests: auth, user isolation, conversation storage. Uses a fake model."""
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import agent
from app.main import app


def _reply(text):
    return NS(choices=[NS(message=NS(content=text, tool_calls=None))])


def _tool(call_id, name, args):
    call = NS(id=call_id, function=NS(name=name, arguments=json.dumps(args)))
    return NS(choices=[NS(message=NS(content="", tool_calls=[call]))])


@pytest.fixture
def fake_model(monkeypatch):
    """Each test sets `queue` to the responses the fake model should return in order."""
    queue = []
    monkeypatch.setattr(agent, "_call_model", lambda messages, allow_tools=True: queue.pop(0))
    return queue


def _client(db, username, password="password123"):
    client = TestClient(app)
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 201, r.text
    return client


def test_register_login_logout(db):
    with TestClient(app) as c:
        assert c.post("/api/auth/register", json={"username": "Raed", "password": "password123"}).status_code == 201
        assert c.get("/api/me").json()["user"]["username"] == "raed"
        c.post("/api/auth/logout")
        assert c.get("/api/me").status_code == 401
        assert c.post("/api/auth/login", json={"username": "raed", "password": "wrong-password"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "raed", "password": "password123"}).status_code == 200
        assert c.get("/api/me").status_code == 200


def test_duplicate_username_and_bad_input(db):
    _client(db, "raed")
    with TestClient(app) as c:
        assert c.post("/api/auth/register", json={"username": "RAED", "password": "password123"}).status_code == 409
        assert c.post("/api/auth/register", json={"username": "a b", "password": "password123"}).status_code == 422
        assert c.post("/api/auth/register", json={"username": "sara", "password": "short"}).status_code == 422


def test_chat_requires_login(db):
    with TestClient(app) as c:
        assert c.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_forged_token_is_rejected(db):
    with TestClient(app) as c:
        c.cookies.set("sufra_token", "not.a.real.token")
        assert c.get("/api/me").status_code == 401


def test_chat_stores_conversation_and_continues_it(db, fake_model):
    alice = _client(db, "alice")
    fake_model += [
        _tool("c1", "save_preference", {"kind": "allergy", "value": "peanuts"}),
        _reply("Noted! Try the hummus."),
        _reply("Still no peanuts for you."),
    ]
    r = alice.post("/api/chat", json={"message": "I'm allergic to peanuts"}).json()
    conv_id = r["conversation_id"]
    assert r["reply"] == "Noted! Try the hummus."
    # The safety net saves the allergy before the model runs; the model's save is then a no-op.
    assert [t["name"] for t in r["tool_calls"]] == ["safety_net", "save_preference"]
    assert r["safety_events"] == ["Safety net saved allergy: peanuts"]
    assert r["preferences"][0]["value"] == "peanuts"

    r2 = alice.post("/api/chat", json={"message": "and now?", "conversation_id": conv_id}).json()
    assert r2["conversation_id"] == conv_id

    # UI only sees user messages and final replies, not tool plumbing.
    msgs = alice.get(f"/api/conversations/{conv_id}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert len(alice.get("/api/conversations").json()) == 1


def test_users_cannot_see_each_others_data(db, fake_model):
    alice = _client(db, "alice")
    bob = _client(db, "bob")
    fake_model += [_tool("c1", "save_preference", {"kind": "diet", "value": "vegan"}), _reply("ok")]
    conv_id = alice.post("/api/chat", json={"message": "I'm vegan"}).json()["conversation_id"]
    pref_id = alice.get("/api/me").json()["preferences"][0]["id"]

    # Bob can't read, continue, or delete Alice's things, even with the right ids.
    assert bob.get(f"/api/conversations/{conv_id}/messages").status_code == 404
    assert bob.post("/api/chat", json={"message": "hi", "conversation_id": conv_id}).status_code == 404
    assert bob.delete(f"/api/me/preferences/{pref_id}").status_code == 404
    assert bob.get("/api/me").json()["preferences"] == []
    assert bob.get("/api/conversations").json() == []
    assert len(alice.get("/api/me").json()["preferences"]) == 1


def test_agent_failure_returns_502_without_details(db, monkeypatch):
    alice = _client(db, "alice")

    def boom(messages, allow_tools=True):
        raise agent.AgentError("Groq error: secret provider details")

    monkeypatch.setattr(agent, "_call_model", boom)
    r = alice.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 502
    assert "secret" not in r.text
