import asyncio
import pytest
from app.schemas import FeedbackInput
from tests.conftest import ingest
from tests.helpers import chat, done


def candidate(env):
    doc = ingest(env)
    _, _, parsed = chat(env)
    msg = done(parsed)
    source = msg["details"]["sources"][0]
    body = {"message_id": msg["id"], "rating": -1, "correction": "Refunds are available within 30 days of purchase.", "consent": True, "evidence_chunk_id": source["chunk_id"], "evidence_quote": source["quote"]}
    return doc, msg, body


def test_feedback_to_reviewed_knowledge(env):
    doc, msg, body = candidate(env)
    response = env.client.post("/api/feedback", headers=env.auth, json=body)
    assert response.status_code == 201
    feedback = response.json()
    assert feedback["status"] == "pending"
    assert len(env.repo.list_documents(env.user.id)) == 1
    assert env.client.post(f"/api/feedback/{feedback['id']}/review", headers=env.auth, json={"action": "approve", "note": "Matches the policy."}).status_code == 403
    approved = env.client.post(f"/api/feedback/{feedback['id']}/review", headers=env.reviewer_auth, json={"action": "approve", "note": "Checked the original refund policy."})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    learned = next(d for d in env.repo.list_documents(env.user.id) if d["learned_from"])
    assert learned["status"] == "queued"
    asyncio.run(env.worker.run_once())
    learned = next(d for d in env.repo.list_documents(env.user.id) if d["learned_from"])
    assert learned["status"] == "ready"
    assert env.repo.list_documents(env.reviewer.id) == []
    # Duplicate approval cannot create a second learned document.
    assert env.client.post(f"/api/feedback/{feedback['id']}/review", headers=env.reviewer_auth, json={"action": "approve", "note": "Checked again."}).status_code == 409
    assert len(env.repo.list_documents(env.user.id)) == 2
    # Removing primary evidence also withdraws the derivative answer.
    env.repo.delete_document(env.user.id, doc["id"])
    assert env.repo.list_documents(env.user.id) == []
    assert env.repo.list_feedback(env.user)[0]["status"] == "withdrawn"


def test_unconsented_correction_rejected(env):
    _, _, body = candidate(env)
    body["consent"] = False
    assert env.client.post("/api/feedback", headers=env.auth, json=body).status_code == 422


def test_false_quote_rejected(env):
    _, _, body = candidate(env)
    body["evidence_quote"] = "This is a quote that does not exist in the document."
    assert env.client.post("/api/feedback", headers=env.auth, json=body).status_code == 400


def test_feedback_is_tenant_scoped(env):
    _, _, body = candidate(env)
    assert env.client.post("/api/feedback", headers=env.other_auth, json=body).status_code == 404


def test_unsupported_correction_cannot_be_approved(env):
    _, _, body = candidate(env)
    body["correction"] = "Refunds are available within 900 days of purchase."
    feedback = env.client.post("/api/feedback", headers=env.auth, json=body).json()
    response = env.client.post(f"/api/feedback/{feedback['id']}/review", headers=env.reviewer_auth, json={"action": "approve", "note": "Please approve this."})
    assert response.status_code == 422
    assert len(env.repo.list_documents(env.user.id)) == 1
    assert env.repo.list_feedback(env.user)[0]["status"] == "pending"


def test_reject_feedback(env):
    _, _, body = candidate(env)
    feedback = env.client.post("/api/feedback", headers=env.auth, json=body).json()
    response = env.client.post(f"/api/feedback/{feedback['id']}/review", headers=env.reviewer_auth, json={"action": "reject", "note": "Insufficient context."})
    assert response.json()["status"] == "rejected"
    assert len(env.repo.list_documents(env.user.id)) == 1


def test_rating_alone_never_publishes_knowledge(env):
    _, msg, _ = candidate(env)
    response = env.client.post("/api/feedback", headers=env.auth, json={"message_id": msg["id"], "rating": 1})
    assert response.json()["status"] == "recorded"
    assert env.client.get("/api/feedback", headers=env.reviewer_auth).json() == []
    assert len(env.repo.list_documents(env.user.id)) == 1


def test_duplicate_feedback_rejected(env):
    _, _, body = candidate(env)
    env.client.post("/api/feedback", headers=env.auth, json=body)
    assert env.client.post("/api/feedback", headers=env.auth, json=body).status_code == 409


def test_self_review_blocked_even_for_reviewer(env):
    ingest(env, user=env.reviewer)
    _, _, parsed = chat(env, auth=env.reviewer_auth)
    msg = done(parsed)
    source = msg["details"]["sources"][0]
    body = {"message_id": msg["id"], "rating": -1, "correction": "Refunds are available within 30 days of purchase.", "consent": True, "evidence_chunk_id": source["chunk_id"], "evidence_quote": source["quote"]}
    f = env.client.post("/api/feedback", headers=env.reviewer_auth, json=body).json()
    assert env.client.post(f"/api/feedback/{f['id']}/review", headers=env.reviewer_auth, json={"action": "approve", "note": "I reviewed my own work."}).status_code == 403
