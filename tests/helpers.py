import json
import uuid


def events(response):
    parsed = []
    for block in response.text.replace("\r\n", "\n").split("\n\n"):
        kind, data = None, []
        for line in block.splitlines():
            if line.startswith("event: "):
                kind = line[7:]
            if line.startswith("data: "):
                data.append(line[6:])
        if kind and data:
            parsed.append((kind, json.loads("\n".join(data))))
    return parsed


def chat(env, text="How long is the refund window?", quality="fast", mode="knowledge", conversation_id=None, request_id=None, auth=None):
    headers = auth or env.auth
    if not conversation_id:
        conversation_id = env.client.post("/api/conversations", headers=headers, json={}).json()["id"]
    payload = {"conversation_id": conversation_id, "request_id": request_id or str(uuid.uuid4()), "message": text, "mode": mode, "quality": quality}
    response = env.client.post("/api/chat/stream", headers=headers, json=payload)
    return response, payload, events(response) if response.status_code == 200 else []


def done(parsed):
    return next(data["message"] for kind, data in parsed if kind == "done")
