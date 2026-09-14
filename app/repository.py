"""Tenant-scoped persistence and transactional state changes.

Every public read/write requires a principal or explicit owner ID. No caller
can choose another user's owner ID through the HTTP API.
"""
import hashlib
import json
import math
import time
from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError

from app.db import Database
from app.errors import AppError
from app.models import ApiKey, ChatRequest, Chunk, Conversation, Document, Feedback, Message, RateBucket, User, new_id
from app.text import cosine, tokens


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    role: str


def digest_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def document_dict(doc: Document) -> dict:
    return {k: getattr(doc, k) for k in ("id", "title", "status", "error", "attempts", "signature", "learned_from", "created_at")}


def message_dict(msg: Message) -> dict:
    return {"id": msg.id, "role": msg.role, "content": msg.content, "details": msg.details, "created_at": msg.created_at}


def conversation_dict(conv: Conversation) -> dict:
    return {k: getattr(conv, k) for k in ("id", "title", "created_at", "updated_at")}


def feedback_dict(f: Feedback) -> dict:
    return {k: getattr(f, k) for k in ("id", "user_id", "message_id", "rating", "question", "correction", "evidence_chunk_id", "evidence_quote", "status", "reviewer_id", "review_note", "verification", "created_at")}


class Repository:
    def __init__(self, db: Database):
        self.db = db

    def seed_user(self, name: str, role: str, token: str) -> Principal:
        if len(token) < 32:
            raise ValueError("API keys must contain at least 32 characters")
        if role not in {"user", "reviewer", "admin"}:
            raise ValueError("Invalid role")
        with self.db.session.begin() as s:
            key = s.get(ApiKey, digest_key(token))
            if key:
                user = s.get(User, key.user_id)
                if user.name != name or user.role != role or not key.active:
                    raise ValueError("A seed key is already assigned to a different or revoked identity")
                return Principal(user.id, user.name, user.role)
            user = User(name=name, role=role)
            s.add(user)
            s.flush()
            s.add(ApiKey(digest=digest_key(token), user_id=user.id))
            return Principal(user.id, user.name, user.role)

    def authenticate(self, token: str) -> Principal | None:
        if not 32 <= len(token) <= 256:
            return None
        with self.db.session() as s:
            user = s.execute(select(User).join(ApiKey, ApiKey.user_id == User.id).where(ApiKey.digest == digest_key(token), ApiKey.active.is_(True))).scalar_one_or_none()
            return Principal(user.id, user.name, user.role) if user else None

    def revoke_key(self, token: str):
        with self.db.session.begin() as s:
            s.execute(update(ApiKey).where(ApiKey.digest == digest_key(token)).values(active=False))

    def rate_limit(self, identity: str, limit: int) -> bool:
        now = time.time()
        key = f"{identity}:{int(now // 60)}"
        with self.db.session.begin() as s:
            count = s.execute(text("INSERT INTO rate_buckets(key, count, expires_at) VALUES (:key, 1, :expiry) ON CONFLICT(key) DO UPDATE SET count=rate_buckets.count+1 RETURNING count"), {"key": key, "expiry": now + 120}).scalar_one()
            return count <= limit

    def clean_rate_buckets(self):
        with self.db.session.begin() as s:
            s.execute(delete(RateBucket).where(RateBucket.expires_at < time.time()))

    def kb_version(self, user_id: str) -> int:
        with self.db.session() as s:
            return s.get(User, user_id).kb_version

    def _owned_conversation(self, s, user_id, conversation_id):
        conv = s.execute(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)).scalar_one_or_none()
        if not conv:
            raise AppError(404, "not_found", "Conversation not found")
        return conv

    def create_conversation(self, user_id, title):
        with self.db.session.begin() as s:
            conv = Conversation(user_id=user_id, title=title)
            s.add(conv)
            s.flush()
            return conversation_dict(conv)

    def list_conversations(self, user_id):
        with self.db.session() as s:
            return [conversation_dict(c) for c in s.scalars(select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc()).limit(200))]

    def messages(self, user_id, conversation_id):
        with self.db.session() as s:
            self._owned_conversation(s, user_id, conversation_id)
            rows = list(s.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.desc(), Message.id.desc()).limit(200)))
            return [message_dict(m) for m in reversed(rows)]

    def delete_conversation(self, user_id, conversation_id):
        with self.db.session.begin() as s:
            conv = self._owned_conversation(s, user_id, conversation_id)
            if conv.lease_until > time.time():
                raise AppError(409, "conversation_busy", "Stop the active reply before deleting this conversation")
            feedback_ids = list(s.scalars(select(Feedback.id).join(Message, Feedback.message_id == Message.id).where(Message.conversation_id == conversation_id)))
            if feedback_ids:
                docs = list(s.scalars(select(Document).where(Document.user_id == user_id, Document.learned_from.in_(feedback_ids))))
                for doc in docs:
                    self._remove_document(s, doc)
                if docs:
                    self._bump(s, user_id)
            s.delete(conv)

    def begin_chat(self, user_id, payload: dict, lease_seconds: float):
        request_id, conversation_id = payload["request_id"], payload["conversation_id"]
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        try:
            with self.db.session.begin() as s:
                conv = self._owned_conversation(s, user_id, conversation_id)
                existing = s.get(ChatRequest, request_id)
                if existing:
                    if existing.user_id != user_id:
                        raise AppError(409, "request_conflict", "Request ID is already in use")
                    if existing.payload_hash != payload_hash:
                        raise AppError(409, "idempotency_conflict", "The same request ID cannot be reused with a different message")
                    if existing.status == "completed":
                        return {"replay": existing.result}
                    if existing.status == "running" and existing.lease_until > time.time():
                        raise AppError(409, "request_running", "This request is already running")
                    raise AppError(409, "request_finished", "This attempt did not complete. Retry with a new request ID")
                token, now = new_id(), time.time()
                count = s.execute(update(Conversation).where(Conversation.id == conv.id, Conversation.user_id == user_id, Conversation.lease_until <= now).values(lease_token=token, lease_until=now + lease_seconds)).rowcount
                if count != 1:
                    raise AppError(409, "conversation_busy", "Another reply is running in this conversation")
                s.add(ChatRequest(id=request_id, user_id=user_id, conversation_id=conv.id, payload_hash=payload_hash, lease_token=token, lease_until=now + lease_seconds))
                history = list(s.scalars(select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at.desc(), Message.id.desc()).limit(8)))
                return {"token": token, "history": [{"role": m.role, "content": m.content[:2500]} for m in reversed(history)]}
        except IntegrityError as e:
            raise AppError(409, "request_conflict", "Request ID or conversation is already in use") from e

    def finish_chat(self, user_id, request_id, token, question, content, details):
        with self.db.session.begin() as s:
            # Fence KB deletion/publication against final answer persistence.
            s.execute(update(User).where(User.id == user_id).values(kb_version=User.kb_version))
            if details.get("mode") == "knowledge" and "knowledge_version" in details:
                current_version = s.scalar(select(User.kb_version).where(User.id == user_id))
                if current_version != details["knowledge_version"]:
                    content = "Your documents changed while I was answering. Please ask again so I can use the current sources."
                    details = {**details, "sources": [], "verification": {"level": "knowledge_changed", "replaced_draft": True}}
            job = s.get(ChatRequest, request_id)
            if not job or job.user_id != user_id or job.lease_token != token or job.status != "running":
                raise AppError(409, "lease_lost", "This reply no longer owns the request")
            changed = s.execute(update(Conversation).where(Conversation.id == job.conversation_id, Conversation.lease_token == token, Conversation.lease_until > time.time()).values(lease_token=None, lease_until=0, updated_at=time.time())).rowcount
            if changed != 1:
                raise AppError(409, "lease_lost", "The conversation lease expired")
            now = time.time()
            s.add(Message(conversation_id=job.conversation_id, role="user", content=question, created_at=now))
            msg = Message(conversation_id=job.conversation_id, role="assistant", content=content, details=details, created_at=now + .0001)
            s.add(msg)
            s.flush()
            result = message_dict(msg)
            job.status, job.result = "completed", result
            conv = s.get(Conversation, job.conversation_id)
            if conv.title == "New conversation":
                conv.title = question[:80]
            return result

    def fail_chat(self, user_id, request_id, token):
        with self.db.session.begin() as s:
            job = s.get(ChatRequest, request_id)
            if job and job.user_id == user_id and job.status == "running" and job.lease_token == token:
                job.status = "failed"
                s.execute(update(Conversation).where(Conversation.id == job.conversation_id, Conversation.lease_token == token).values(lease_until=0, lease_token=None))

    def _bump(self, s, user_id):
        s.execute(update(User).where(User.id == user_id).values(kb_version=User.kb_version + 1))

    def add_document(self, user_id, title, body, limit=200):
        with self.db.session.begin() as s:
            # Serialize the quota check per owner (also acquires a SQLite write lock).
            s.execute(update(User).where(User.id == user_id).values(kb_version=User.kb_version))
            count = s.scalar(select(func.count()).select_from(Document).where(Document.user_id == user_id))
            if count >= limit:
                raise AppError(409, "document_limit", "Document limit reached. Delete unused documents first")
            doc = Document(user_id=user_id, title=title, body=body)
            s.add(doc)
            s.flush()
            return document_dict(doc)

    def list_documents(self, user_id):
        with self.db.session() as s:
            return [document_dict(d) for d in s.scalars(select(Document).where(Document.user_id == user_id).order_by(Document.created_at.desc()))]

    def _remove_chunks(self, s, document_id):
        if not self.db.is_postgres:
            s.execute(text("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=:id)"), {"id": document_id})
        s.execute(delete(Chunk).where(Chunk.document_id == document_id))

    def _remove_document(self, s, doc):
        self._remove_chunks(s, doc.id)
        s.delete(doc)

    def delete_document(self, user_id, document_id):
        with self.db.session.begin() as s:
            # Owner lock fences publication/deletion/learning races.
            s.execute(update(User).where(User.id == user_id).values(kb_version=User.kb_version))
            doc = s.execute(select(Document).where(Document.id == document_id, Document.user_id == user_id)).scalar_one_or_none()
            if not doc:
                raise AppError(404, "not_found", "Document not found")
            for child in s.scalars(select(Document).where(Document.user_id == user_id, Document.parent_document_id == doc.id)):
                self._remove_document(s, child)
            s.execute(update(Feedback).where(Feedback.user_id == user_id, Feedback.evidence_document_id == doc.id).values(status="withdrawn", review_note="Source document deleted"))
            self._remove_document(s, doc)
            self._bump(s, user_id)

    def reindex_document(self, user_id, document_id):
        with self.db.session.begin() as s:
            s.execute(update(User).where(User.id == user_id).values(kb_version=User.kb_version))
            doc = s.execute(select(Document).where(Document.id == document_id, Document.user_id == user_id)).scalar_one_or_none()
            if not doc:
                raise AppError(404, "not_found", "Document not found")
            doc.status, doc.attempts, doc.error, doc.lease_token, doc.lease_until, doc.available_at = "queued", 0, None, None, 0, 0
            self._remove_chunks(s, doc.id)
            self._bump(s, user_id)
            return document_dict(doc)

    def claim_document(self, lease_seconds):
        now = time.time()
        with self.db.session.begin() as s:
            eligible = ((Document.status == "queued") & (Document.available_at <= now)) | ((Document.status == "processing") & (Document.lease_until < now))
            # Exhausted crash retries become terminal instead of staying in processing forever.
            s.execute(update(Document).where(eligible, Document.attempts >= 3).values(status="failed", error="Ingestion attempts exhausted", lease_token=None, lease_until=0))
            stmt = select(Document).where(eligible, Document.attempts < 3).order_by(Document.created_at).limit(1)
            if self.db.is_postgres:
                stmt = stmt.with_for_update(skip_locked=True)
            doc = s.scalar(stmt)
            if not doc:
                return None
            token = new_id()
            changed = s.execute(update(Document).where(Document.id == doc.id, eligible, Document.attempts < 3).values(status="processing", lease_token=token, lease_until=now + lease_seconds, attempts=Document.attempts + 1)).rowcount
            if not changed:
                return None
            return {"id": doc.id, "user_id": doc.user_id, "title": doc.title, "body": doc.body, "token": token}

    def renew_document(self, doc_id, token, lease_seconds):
        with self.db.session.begin() as s:
            return s.execute(update(Document).where(Document.id == doc_id, Document.lease_token == token, Document.status == "processing").values(lease_until=time.time() + lease_seconds)).rowcount == 1

    def publish_document(self, job, content_chunks, vectors, signature):
        if len(content_chunks) != len(vectors) or not content_chunks:
            raise ValueError("Embedding count does not match chunks")
        if any(len(v) != 1536 or any(not math.isfinite(x) for x in v) for v in vectors):
            raise ValueError("Invalid embedding dimension or value")
        with self.db.session.begin() as s:
            s.execute(update(User).where(User.id == job["user_id"]).values(kb_version=User.kb_version))
            changed = s.execute(update(Document).where(Document.id == job["id"], Document.lease_token == job["token"], Document.status == "processing", Document.lease_until > time.time()).values(status="ready", signature=signature, error=None, lease_token=None, lease_until=0)).rowcount
            if not changed:
                return False
            self._remove_chunks(s, job["id"])
            for i, (body, embedding) in enumerate(zip(content_chunks, vectors)):
                chunk = Chunk(document_id=job["id"], ordinal=i, body=body, embedding=embedding)
                s.add(chunk)
                s.flush()
                if self.db.is_postgres:
                    s.execute(text("UPDATE chunks SET embedding_vec=CAST(:v AS vector) WHERE id=:id"), {"id": chunk.id, "v": json.dumps(embedding)})
                else:
                    s.execute(text("INSERT INTO chunks_fts(chunk_id, body) VALUES (:id, :body)"), {"id": chunk.id, "body": body})
            self._bump(s, job["user_id"])
            return True

    def fail_document(self, job, terminal=False):
        with self.db.session.begin() as s:
            doc = s.execute(select(Document).where(Document.id == job["id"], Document.lease_token == job["token"])).scalar_one_or_none()
            if doc:
                doc.status = "failed" if terminal or doc.attempts >= 3 else "queued"
                doc.error = "Document rejected by ingestion checks" if terminal else "Ingestion provider unavailable; retry or check worker logs"
                doc.lease_token, doc.lease_until, doc.available_at = None, 0, time.time() + 2 ** doc.attempts

    def lexical_search(self, user_id, query, signature, limit=15):
        words = list(dict.fromkeys(tokens(query)))[:40]
        if not words:
            return []
        with self.db.session() as s:
            if self.db.is_postgres:
                sql = """SELECT c.id, c.document_id, c.body, d.title,
                    ts_rank_cd(to_tsvector('english', c.body), plainto_tsquery('english', :q)) AS score
                    FROM chunks c JOIN documents d ON d.id=c.document_id
                    WHERE d.user_id=:u AND d.status='ready' AND d.signature=:sig
                    AND to_tsvector('english', c.body) @@ plainto_tsquery('english', :q)
                    ORDER BY score DESC LIMIT :lim"""
                q = " ".join(words)
            else:
                sql = """SELECT c.id, c.document_id, c.body, d.title, -bm25(chunks_fts) AS score
                    FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.chunk_id
                    JOIN documents d ON d.id=c.document_id
                    WHERE chunks_fts MATCH :q AND d.user_id=:u AND d.status='ready' AND d.signature=:sig
                    ORDER BY bm25(chunks_fts) LIMIT :lim"""
                q = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
            return [dict(row) for row in s.execute(text(sql), {"q": q, "u": user_id, "sig": signature, "lim": limit}).mappings()]

    def vector_search(self, user_id, vector, signature, limit=15, minimum=.23):
        with self.db.session() as s:
            if self.db.is_postgres:
                # pgvector >= 0.8; iterative scans reduce filtering-related underfill.
                s.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
                sql = """SELECT c.id, c.document_id, c.body, d.title,
                    1-(c.embedding_vec <=> CAST(:v AS vector)) AS score
                    FROM chunks c JOIN documents d ON d.id=c.document_id
                    WHERE d.user_id=:u AND d.status='ready' AND d.signature=:sig
                    ORDER BY c.embedding_vec <=> CAST(:v AS vector) LIMIT :lim"""
                rows = [dict(row) for row in s.execute(text(sql), {"v": json.dumps(vector), "u": user_id, "sig": signature, "lim": limit}).mappings()]
            else:
                # Local-only exact scan, deliberately not advertised as a large-corpus index.
                rows = [{"id": c.id, "document_id": d.id, "title": d.title, "body": c.body, "score": cosine(vector, c.embedding)} for c, d in s.execute(select(Chunk, Document).join(Document, Chunk.document_id == Document.id).where(Document.user_id == user_id, Document.status == "ready", Document.signature == signature))]
                rows.sort(key=lambda r: r["score"], reverse=True)
            return [row for row in rows if row["score"] >= minimum][:limit]

    def _evidence(self, s, user_id, chunk_id, quote):
        row = s.execute(select(Chunk, Document).join(Document, Chunk.document_id == Document.id).where(Chunk.id == chunk_id, Document.user_id == user_id, Document.status == "ready")).first()
        if not row or quote not in row[0].body:
            raise AppError(400, "invalid_evidence", "The exact evidence quote must appear in one of your ready source chunks")
        if row[1].learned_from:
            raise AppError(400, "circular_evidence", "Reviewed answers cannot serve as primary evidence for another correction")
        return row

    def submit_feedback(self, user_id, body):
        try:
            with self.db.session.begin() as s:
                row = s.execute(select(Message, Conversation).join(Conversation, Message.conversation_id == Conversation.id).where(Message.id == str(body.message_id), Conversation.user_id == user_id, Message.role == "assistant")).first()
                if not row:
                    raise AppError(404, "not_found", "Assistant message not found")
                msg, conv = row
                previous = s.scalar(select(Message).where(Message.conversation_id == conv.id, Message.role == "user", Message.created_at < msg.created_at).order_by(Message.created_at.desc()).limit(1))
                doc_id = None
                if body.correction:
                    _, doc = self._evidence(s, user_id, str(body.evidence_chunk_id), body.evidence_quote)
                    doc_id = doc.id
                feedback = Feedback(user_id=user_id, message_id=msg.id, rating=body.rating, question=previous.content if previous else "", correction=body.correction, consent=body.consent, evidence_chunk_id=str(body.evidence_chunk_id) if body.evidence_chunk_id else None, evidence_document_id=doc_id, evidence_quote=body.evidence_quote, status="pending" if body.correction else "recorded")
                s.add(feedback)
                s.flush()
                return feedback_dict(feedback)
        except IntegrityError as e:
            raise AppError(409, "feedback_exists", "Feedback already exists for this reply") from e

    def list_feedback(self, principal: Principal):
        with self.db.session() as s:
            stmt = select(Feedback).order_by(Feedback.created_at.desc()).limit(200)
            if principal.role not in {"reviewer", "admin"}:
                stmt = stmt.where(Feedback.user_id == principal.id)
            else:
                stmt = stmt.where(Feedback.consent.is_(True), Feedback.correction != "")
            return [feedback_dict(f) for f in s.scalars(stmt)]

    def review_candidate(self, principal: Principal, feedback_id, require_evidence=True):
        if principal.role not in {"reviewer", "admin"}:
            raise AppError(403, "forbidden", "A reviewer account is required")
        with self.db.session() as s:
            f = s.get(Feedback, feedback_id)
            if not f or not f.consent:
                raise AppError(404, "not_found", "Review item not found")
            if f.user_id == principal.id:
                raise AppError(403, "self_review", "A different person must review this correction")
            if f.status != "pending":
                raise AppError(409, "already_reviewed", "This correction is no longer pending")
            if require_evidence:
                self._evidence(s, f.user_id, f.evidence_chunk_id, f.evidence_quote)
            return feedback_dict(f)

    def commit_review(self, principal: Principal, feedback_id, action, note, verdict, max_documents=200):
        with self.db.session.begin() as s:
            f = s.get(Feedback, feedback_id)
            if not f or not f.consent:
                raise AppError(404, "not_found", "Review item not found")
            if principal.role not in {"reviewer", "admin"} or principal.id == f.user_id:
                raise AppError(403, "forbidden", "Independent reviewer required")
            s.execute(update(User).where(User.id == f.user_id).values(kb_version=User.kb_version))
            # Reload after owner lock so concurrent reviews/deletion cannot publish twice.
            s.refresh(f)
            if f.status != "pending":
                raise AppError(409, "already_reviewed", "This correction is no longer pending")
            if action == "approve":
                if not verdict.get("supported"):
                    raise AppError(422, "unsupported_correction", "The correction is not supported by the supplied evidence")
                _, source = self._evidence(s, f.user_id, f.evidence_chunk_id, f.evidence_quote)
                count = s.scalar(select(func.count()).select_from(Document).where(Document.user_id == f.user_id))
                if count >= max_documents:
                    raise AppError(409, "document_limit", "The author's document quota is full")
                doc = Document(user_id=f.user_id, title="Reviewed answer: " + f.question[:120], body=f"Question: {f.question}\nApproved answer: {f.correction}\nPrimary evidence: {f.evidence_quote}\nSource: {source.title}", learned_from=f.id, parent_document_id=source.id)
                s.add(doc)
                f.status = "approved"
            else:
                f.status = "rejected"
            f.reviewer_id, f.review_note, f.verification = principal.id, note, verdict
            s.flush()
            return feedback_dict(f)
