"""Opt-in integration tests. Point only to a DISPOSABLE pgvector database.

TEST_POSTGRES_URL must be set. Not part of default SQLite unit tests.
These exercise SQL specific to the production backend, not a mock dialect.
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete

from app.config import Settings
from app.db import Database
from app.models import User
from app.provider import DemoProvider
from app.repository import Repository
from app.text import hash_embedding
from app.worker import IngestionWorker

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="No disposable PostgreSQL database configured")


def test_postgres_hybrid_index_and_tenant_isolation():
    settings = Settings(_env_file=None, app_env="test", database_url=os.environ["TEST_POSTGRES_URL"], provider="demo")
    db = Database(settings.database_url)
    db.migrate()
    repo = Repository(db)
    key = uuid.uuid4().hex + uuid.uuid4().hex
    user = repo.seed_user("pg-test", "user", key)
    other = repo.seed_user("pg-other", "user", key[::-1])
    try:
        doc = repo.add_document(user.id, "Refund policy", "Refunds are available within 30 days of purchase. A receipt is required.")
        # Claim this isolated database's queue before beginning shared tests.
        asyncio.run(IngestionWorker(repo, DemoProvider(), settings).run_once())
        lexical = repo.lexical_search(user.id, "refund", settings.embedding_signature)
        dense = repo.vector_search(user.id, hash_embedding("refund purchase"), settings.embedding_signature, minimum=0)
        assert lexical and dense
        assert lexical[0]["document_id"] == doc["id"]
        assert repo.lexical_search(other.id, "refund", settings.embedding_signature) == []
        assert repo.vector_search(other.id, hash_embedding("refund"), settings.embedding_signature, minimum=0) == []
        repo.delete_document(user.id, doc["id"])
        assert repo.lexical_search(user.id, "refund", settings.embedding_signature) == []
    finally:
        with db.session.begin() as s:
            s.execute(delete(User).where(User.id.in_([user.id, other.id])))
        db.close()
