"""Real HTTP end-to-end smoke test against running API + worker.

Set USER_API_KEY, REVIEWER_API_KEY, and optionally BASE_URL (default API :8000).
BASE_URL may point at the frontend :5173 to test the reverse proxy as well.
Run in demo mode against a disposable workspace. Test-created data is removed.
"""
import json
import os
import time
import uuid

import httpx

base = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
user_headers = {"Authorization": "Bearer " + os.environ["USER_API_KEY"]}
reviewer_headers = {"Authorization": "Bearer " + os.environ["REVIEWER_API_KEY"]}


def require(response):
    response.raise_for_status()
    return response.json() if response.content else None


def wait_ready(client, document_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        docs = require(client.get("/api/documents", headers=user_headers))
        doc = next((d for d in docs if d["id"] == document_id), None)
        if doc and doc["status"] == "ready":
            return
        if doc and doc["status"] == "failed":
            raise AssertionError(doc)
        time.sleep(.3)
    raise AssertionError("Worker did not index the document")


with httpx.Client(base_url=base, timeout=60) as client:
    document_id = conversation_id = None
    checks = []
    try:
        assert require(client.get("/api/me", headers=user_headers))["provider"] == "demo"
        checks.append("Authentication through real HTTP")
        doc = require(client.post("/api/documents", headers=user_headers, json={"title": "HTTP smoke policy " + uuid.uuid4().hex[:8], "text": "Refunds are available within 30 days of purchase. A receipt is required for all refunds."}))
        document_id = doc["id"]
        wait_ready(client, document_id)
        checks.append("Durable worker ingestion")
        conv = require(client.post("/api/conversations", headers=user_headers, json={}))
        conversation_id = conv["id"]
        payload = {"conversation_id": conversation_id, "request_id": str(uuid.uuid4()), "message": "What is the refund policy?", "mode": "knowledge", "quality": "fast"}
        seen, result, started, first_token = [], None, time.perf_counter(), None
        with client.stream("POST", "/api/chat/stream", headers=user_headers, json=payload) as response:
            response.raise_for_status()
            event = None
            for line in response.iter_lines():
                if line.startswith("event: "): event = line[7:]
                if line.startswith("data: "):
                    data = json.loads(line[6:]); seen.append(event)
                    if event == "delta" and first_token is None: first_token = time.perf_counter()-started
                    if event == "done": result = data["message"]
                    if event == "error": raise AssertionError(data)
        assert result and "30 days" in result["content"] and "delta" in seen
        checks.append(f"Streaming answer and citations (offline demo first token: {first_token*1000:.1f} ms)")
        replay = client.post("/api/chat/stream", headers=user_headers, json=payload)
        assert '"replayed":true' in replay.text
        checks.append("Idempotent replay")
        assert client.get(f"/api/conversations/{conversation_id}/messages", headers=reviewer_headers).status_code == 404
        checks.append("Private conversation isolation")
        source = next(s for s in result["details"]["sources"] if s["document_id"] == document_id)
        feedback = require(client.post("/api/feedback", headers=user_headers, json={"message_id": result["id"], "rating": -1, "correction": "Refunds are available within 30 days of purchase.", "consent": True, "evidence_chunk_id": source["chunk_id"], "evidence_quote": source["quote"]}))
        approved = require(client.post(f"/api/feedback/{feedback['id']}/review", headers=reviewer_headers, json={"action": "approve", "note": "Checked the exact sentence against primary evidence."}))
        assert approved["status"] == "approved"
        learned = next(d for d in require(client.get("/api/documents", headers=user_headers)) if d["learned_from"] == feedback["id"])
        wait_ready(client, learned["id"])
        checks.append("Consented correction, independent review and private reindexing")
        require(client.delete(f"/api/documents/{document_id}", headers=user_headers)); document_id = None
        assert not any(d["id"] == learned["id"] for d in require(client.get("/api/documents", headers=user_headers)))
        checks.append("Primary-source deletion withdraws learned derivative")
        require(client.delete(f"/api/conversations/{conversation_id}", headers=user_headers)); conversation_id = None
        checks.append("Conversation deletion")
        for check in checks: print("PASS:", check)
        print(f"\n{len(checks)} real-HTTP checks passed.")
    finally:
        if document_id: client.delete(f"/api/documents/{document_id}", headers=user_headers)
        if conversation_id: client.delete(f"/api/conversations/{conversation_id}", headers=user_headers)
