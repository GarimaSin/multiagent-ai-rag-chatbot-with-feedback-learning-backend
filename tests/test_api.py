import asyncio
import uuid

import pytest
from app.errors import ProviderError
from tests.conftest import USER_KEY, ingest
from tests.helpers import chat, done, events


def test_health_and_me(env):
    assert env.client.get("/health/live").json()["status"] == "ok"
    assert env.client.get("/health/ready").status_code == 200
    me = env.client.get("/api/me", headers=env.auth)
    assert me.json()["provider"] == "demo"
    assert me.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("auth", [None, "Bearer wrong", "Basic abc", "Bearer " + "x"*48])
def test_auth_required(env, auth):
    headers = {"Authorization": auth} if auth else {}
    assert env.client.get("/api/conversations", headers=headers).status_code == 401


def test_revoked_key(env):
    env.repo.revoke_key(USER_KEY)
    assert env.client.get("/api/me", headers=env.auth).status_code == 401


def test_streaming_chat_citations_and_persistence(env):
    ingest(env)
    response, payload, parsed = chat(env)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    result = done(parsed)
    assert "30 days" in result["content"]
    assert "[S1]" in result["content"]
    assert result["details"]["verification"]["level"] == "citations_checked"
    assert result["details"]["sources"][0]["quote"]
    saved = env.client.get(f"/api/conversations/{payload['conversation_id']}/messages", headers=env.auth).json()
    assert len(saved) == 2
    assert saved[1]["id"] == result["id"]
    assert [kind for kind, _ in parsed].index("delta") < [kind for kind, _ in parsed].index("done")


def test_verified_mode_waits_for_verifier(env):
    ingest(env)
    _, _, parsed = chat(env, quality="verified")
    verifier_index = next(i for i, (kind, data) in enumerate(parsed) if kind == "status" and data["agent"] == "VerifierAgent")
    first_delta = next(i for i, (kind, _) in enumerate(parsed) if kind == "delta")
    assert first_delta > verifier_index
    assert done(parsed)["details"]["verification"]["level"] == "model_checked"


def test_no_evidence_abstains(env):
    _, _, parsed = chat(env)
    assert done(parsed)["details"]["verification"]["level"] == "abstained"
    assert "not find enough" in done(parsed)["content"]


def test_irrelevant_evidence_abstains(env):
    ingest(env)
    _, _, parsed = chat(env, "Quantum neutrino oscillation wavelength")
    assert done(parsed)["details"]["verification"]["level"] == "abstained"


def test_general_demo_is_explicit(env):
    _, _, parsed = chat(env, "Explain gravity", mode="general")
    assert "offline demo" in done(parsed)["content"]
    assert done(parsed)["details"]["verification"]["level"] == "general_unverified"


def test_greeting_fast_path(env):
    _, _, parsed = chat(env, "hello")
    assert done(parsed)["details"]["verification"]["level"] == "local_response"


def test_idempotent_completed_request(env):
    ingest(env)
    response, payload, parsed = chat(env)
    second = env.client.post("/api/chat/stream", headers=env.auth, json=payload)
    result = events(second)
    assert done(parsed)["id"] == done(result)["id"]
    assert next(data for kind, data in result if kind == "done")["replayed"] is True
    assert len(env.repo.messages(env.user.id, payload["conversation_id"])) == 2


def test_idempotency_conflict(env):
    _, payload, _ = chat(env)
    payload["message"] = "Different question"
    assert env.client.post("/api/chat/stream", headers=env.auth, json=payload).status_code == 409


def test_tenant_isolation(env):
    ingest(env)
    _, payload, _ = chat(env)
    assert env.client.get(f"/api/conversations/{payload['conversation_id']}/messages", headers=env.other_auth).status_code == 404
    assert env.client.delete(f"/api/conversations/{payload['conversation_id']}", headers=env.other_auth).status_code == 404
    assert env.client.get("/api/documents", headers=env.other_auth).json() == []
    _, _, parsed = chat(env, auth=env.other_auth)
    assert done(parsed)["details"]["sources"] == []


def test_document_delete_invalidates_cache(env):
    doc = ingest(env)
    chat(env)
    _, _, cached = chat(env)
    assert done(cached)["details"]["retrieval_cache_hit"] is True
    assert env.client.delete(f"/api/documents/{doc['id']}", headers=env.auth).status_code == 204
    _, _, parsed = chat(env)
    assert done(parsed)["details"]["sources"] == []


def test_document_scope_and_reindex(env):
    doc = ingest(env)
    assert env.client.delete(f"/api/documents/{doc['id']}", headers=env.other_auth).status_code == 404
    assert env.client.post(f"/api/documents/{doc['id']}/reindex", headers=env.other_auth).status_code == 404
    response = env.client.post(f"/api/documents/{doc['id']}/reindex", headers=env.auth)
    assert response.json()["status"] == "queued"
    _, _, parsed = chat(env)
    assert done(parsed)["details"]["sources"] == []


def test_bad_document_and_invalid_input(env):
    assert env.client.post("/api/documents", headers=env.auth, json={"title": "X", "text": "short"}).status_code == 422
    assert env.client.post("/api/documents", headers=env.auth, json={"title": "X", "text": "a"*200001}).status_code == 413
    assert env.client.post("/api/conversations", headers=env.auth, json={"title": "x", "user_id": env.other.id}).status_code == 422


def test_provider_failure_does_not_save_partial_answer(env, monkeypatch):
    ingest(env)
    async def broken(*args, **kwargs):
        yield "Unfinished text"
        raise ProviderError()
    monkeypatch.setattr(env.provider, "stream", broken)
    _, payload, parsed = chat(env)
    assert any(kind == "error" for kind, _ in parsed)
    assert not any(kind == "done" for kind, _ in parsed)
    assert env.repo.messages(env.user.id, payload["conversation_id"]) == []
    # Same conversation can take a new attempt after failure releases its lease.
    _, _, retry = chat(env, "hello", conversation_id=payload["conversation_id"])
    assert done(retry)


def test_generation_deadline(env, monkeypatch):
    ingest(env)
    env.settings.request_deadline_seconds = .02
    async def slow(*args, **kwargs):
        await asyncio.sleep(.2)
        yield "too late"
    monkeypatch.setattr(env.provider, "stream", slow)
    _, payload, parsed = chat(env)
    assert next(data["code"] for kind, data in parsed if kind == "error") == "deadline_exceeded"
    assert env.repo.messages(env.user.id, payload["conversation_id"]) == []


def test_bad_citations_replace_provisional_draft(env, monkeypatch):
    ingest(env)
    async def bad(*args, **kwargs):
        yield "Refunds last 900 days. [S99]"
    monkeypatch.setattr(env.provider, "stream", bad)
    _, _, parsed = chat(env)
    assert "900" in "".join(data["text"] for kind, data in parsed if kind == "delta")
    final = done(parsed)
    assert "900" not in final["content"]
    assert final["details"]["verification"]["replaced_draft"] is True


def test_metrics_restricted(env):
    assert env.client.get("/metrics", headers=env.auth).status_code == 403
    response = env.client.get("/metrics", headers=env.reviewer_auth)
    assert response.status_code == 200
    assert "multi_agent_ai_rag_chatbot_with_feedback_learning_chat_seconds" in response.text


def test_rate_limit(env):
    env.settings.requests_per_minute = 1
    assert env.client.get("/api/me", headers=env.auth).status_code == 200
    response = env.client.get("/api/me", headers=env.auth)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_delete_conversation(env):
    _, payload, _ = chat(env, "hello")
    assert env.client.delete(f"/api/conversations/{payload['conversation_id']}", headers=env.auth).status_code == 204
    assert env.client.get("/api/conversations", headers=env.auth).json() == []
