import asyncio
import contextlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agents import LearningAgent
from app.branding import APP_NAME, APP_NAMESPACE
from app.config import Settings
from app.db import Database
from app.errors import AppError, ProviderError
from app.metrics import Metrics
from app.provider import make_provider
from app.repository import Principal, Repository
from app.schemas import ChatInput, ConversationInput, DocumentInput, FeedbackInput, ReviewInput
from app.supervisor import Supervisor

log = logging.getLogger(f"{APP_NAMESPACE}.api")


def event_bytes(kind: str, data: dict) -> bytes:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


class RequestLimits:
    """ASGI body-size enforcement for both Content-Length and chunked bodies."""
    def __init__(self, app, maximum):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"error": {"code": "bad_length", "message": "Invalid Content-Length"}}, 400)(scope, receive, send)
        if length > self.maximum:
            return await JSONResponse({"error": {"code": "body_too_large", "message": "Request is too large"}}, 413)(scope, receive, send)
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.maximum:
                    raise StarletteHTTPException(413, "Request is too large")
            return message
        await self.app(scope, limited_receive, send)


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        request_id = str(uuid.uuid4())

        async def wrapped_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-request-id", request_id.encode()),
                    (b"cache-control", b"no-store"),
                ])
            await send(message)
        await self.app(scope, receive, wrapped_send)


def create_app(settings: Settings | None = None, provider=None, database=None) -> FastAPI:
    settings = settings or Settings()
    db = database or Database(settings.database_url)
    repo = Repository(db)
    provider = provider or make_provider(settings)
    metrics = Metrics()
    semaphore = asyncio.Semaphore(settings.max_concurrent_chats)
    supervisor = Supervisor(repo, provider, settings)
    learning = LearningAgent(provider)

    @asynccontextmanager
    async def lifespan(app):
        if not await asyncio.to_thread(db.ready):
            raise RuntimeError("Database is not initialized. Run python -m app.manage init")
        yield
        await provider.close()
        db.close()

    app = FastAPI(title=f"{APP_NAME} API", version="1.0.0", lifespan=lifespan)
    app.state.repo, app.state.provider, app.state.settings = repo, provider, settings
    app.state.supervisor, app.state.metrics = supervisor, metrics
    app.add_middleware(RequestLimits, maximum=settings.max_request_bytes)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type"], allow_credentials=False)
    app.add_middleware(SecurityHeaders)

    @app.exception_handler(AppError)
    async def app_error(request, error):
        headers = {"Retry-After": "60"} if error.status == 429 else None
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status, headers=headers)

    @app.exception_handler(ProviderError)
    async def provider_error(request, error):
        return JSONResponse({"error": {"code": error.code, "message": "The AI provider is unavailable. Please retry."}}, status_code=503)

    async def authenticate(request: Request) -> Principal:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise AppError(401, "unauthorized", "A bearer API key is required")
        principal = await asyncio.to_thread(repo.authenticate, auth[7:])
        if not principal:
            raise AppError(401, "unauthorized", "Invalid or revoked API key")
        return principal

    async def principal(request: Request) -> Principal:
        p = await authenticate(request)
        if not await asyncio.to_thread(repo.rate_limit, p.id, settings.requests_per_minute):
            raise AppError(429, "rate_limit", "Request limit reached; wait for the next minute")
        return p

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        try:
            ok = await asyncio.to_thread(db.ready)
        except Exception:
            ok = False
        return JSONResponse({"status": "ready" if ok else "not_ready"}, status_code=200 if ok else 503)

    @app.get("/api/me")
    async def me(p: Principal = Depends(principal)):
        return {"id": p.id, "name": p.name, "role": p.role, "provider": settings.provider, "embedding_signature": settings.embedding_signature, "max_document_chars": settings.max_document_chars}

    @app.get("/metrics")
    async def get_metrics(p: Principal = Depends(authenticate)):
        if p.role not in {"reviewer", "admin"}:
            raise AppError(403, "forbidden", "Reviewer or admin role required")
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/conversations")
    async def conversations(p: Principal = Depends(principal)):
        return await asyncio.to_thread(repo.list_conversations, p.id)

    @app.post("/api/conversations", status_code=201)
    async def create_conversation(body: ConversationInput, p: Principal = Depends(principal)):
        return await asyncio.to_thread(repo.create_conversation, p.id, body.title)

    @app.get("/api/conversations/{conversation_id}/messages")
    async def messages(conversation_id: UUID, p: Principal = Depends(principal)):
        return await asyncio.to_thread(repo.messages, p.id, str(conversation_id))

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: UUID, p: Principal = Depends(principal)):
        await asyncio.to_thread(repo.delete_conversation, p.id, str(conversation_id))
        return Response(status_code=204)

    @app.get("/api/documents")
    async def documents(p: Principal = Depends(principal)):
        return await asyncio.to_thread(repo.list_documents, p.id)

    @app.post("/api/documents", status_code=202)
    async def add_document(body: DocumentInput, p: Principal = Depends(principal)):
        if len(body.text) > settings.max_document_chars:
            raise AppError(413, "document_too_large", f"Documents must contain at most {settings.max_document_chars} characters")
        return await asyncio.to_thread(repo.add_document, p.id, body.title, body.text, settings.max_documents_per_user)

    @app.delete("/api/documents/{document_id}", status_code=204)
    async def delete_document(document_id: UUID, p: Principal = Depends(principal)):
        await asyncio.to_thread(repo.delete_document, p.id, str(document_id))
        return Response(status_code=204)

    @app.post("/api/documents/{document_id}/reindex")
    async def reindex_document(document_id: UUID, p: Principal = Depends(principal)):
        return await asyncio.to_thread(repo.reindex_document, p.id, str(document_id))

    @app.post("/api/feedback", status_code=201)
    async def feedback(body: FeedbackInput, p: Principal = Depends(principal)):
        return await asyncio.to_thread(repo.submit_feedback, p.id, body)

    @app.get("/api/feedback")
    async def feedback_list(p: Principal = Depends(principal)):
        return await asyncio.to_thread(repo.list_feedback, p)

    @app.post("/api/feedback/{feedback_id}/review")
    async def review(feedback_id: UUID, body: ReviewInput, p: Principal = Depends(principal)):
        candidate = await asyncio.to_thread(repo.review_candidate, p, str(feedback_id), body.action == "approve")
        try:
            async with asyncio.timeout(settings.request_deadline_seconds):
                verdict = await learning.evaluate(candidate) if body.action == "approve" else {"supported": False, "reason": "Rejected by reviewer"}
        except TimeoutError as error:
            raise AppError(504, "review_timeout", "Evidence verification timed out; no approval was saved") from error
        return await asyncio.to_thread(repo.commit_review, p, str(feedback_id), body.action, body.note, verdict, settings.max_documents_per_user)

    @app.post("/api/chat/stream")
    async def chat(body: ChatInput, request: Request, p: Principal = Depends(principal)):
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=.1)
        except TimeoutError:
            raise AppError(503, "busy", "The server is busy. Retry shortly")
        try:
            prepared = await asyncio.to_thread(repo.begin_chat, p.id, body.model_dump(mode="json"), settings.request_deadline_seconds + 20)
        except BaseException:
            semaphore.release()
            raise

        async def stream():
            started = time.perf_counter()
            first_token = False
            committed = False
            token = prepared.get("token")
            metrics.active.inc()
            try:
                yield event_bytes("meta", {"request_id": str(body.request_id), "conversation_id": str(body.conversation_id), "quality": body.quality, "provider": settings.provider, "provisional": body.quality == "fast"})
                if "replay" in prepared:
                    committed = True
                    yield event_bytes("done", {"message": prepared["replay"], "replayed": True})
                    return
                final_data = None
                async with asyncio.timeout(settings.request_deadline_seconds):
                    pipeline = supervisor.run(p, body, prepared["history"])
                    try:
                        async for kind, data in pipeline:
                            if await request.is_disconnected():
                                raise asyncio.CancelledError()
                            if kind == "delta" and not first_token:
                                first_token = True
                                metrics.first_token.observe(time.perf_counter()-started)
                            if kind == "result":
                                final_data = data
                            else:
                                yield event_bytes(kind, data)
                    finally:
                        await pipeline.aclose()
                if final_data is None:
                    raise ValueError("No final result from supervisor")
                # Database commits cannot be cancelled halfway through. If a
                # disconnect happens here, await the atomic outcome; the client
                # can recover a completed answer using the same request ID.
                commit_task = asyncio.create_task(asyncio.to_thread(repo.finish_chat, p.id, str(body.request_id), token, body.message, final_data["content"], final_data["details"]))
                try:
                    result = await asyncio.shield(commit_task)
                except asyncio.CancelledError:
                    result = await commit_task
                    committed = True
                    raise
                committed = True
                metrics.chats.labels("completed").inc()
                yield event_bytes("done", {"message": result, "replayed": False})
            except asyncio.CancelledError:
                metrics.chats.labels("cancelled").inc()
                raise
            except (Exception,) as error:
                code = "deadline_exceeded" if isinstance(error, TimeoutError) else getattr(error, "code", "reply_failed")
                metrics.chats.labels("failed").inc()
                log.warning("chat_failed request_id=%s error_type=%s", body.request_id, type(error).__name__)
                yield event_bytes("error", {"code": code, "message": "The reply could not be completed. The unfinished draft was not saved. Please retry.", "request_id": str(body.request_id)})
            finally:
                if not committed and token:
                    with contextlib.suppress(Exception):
                        await asyncio.shield(asyncio.to_thread(repo.fail_chat, p.id, str(body.request_id), token))
                metrics.duration.observe(time.perf_counter()-started)
                metrics.active.dec()
                semaphore.release()

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform"})

    return app
