from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base

SCHEMA_VERSION = 1


class Database:
    """One short-lived synchronous session per repository operation.

    API callers dispatch these operations to threads; sessions never span awaits.
    """
    def __init__(self, url: str):
        self.is_postgres = url.startswith("postgresql")
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
        else:
            kwargs.update(pool_size=10, max_overflow=10)
        self.engine = create_engine(url, **kwargs)
        self.session = sessionmaker(self.engine, expire_on_commit=False)
        if not self.is_postgres:
            @event.listens_for(self.engine, "connect")
            def set_pragmas(conn, _):
                cursor = conn.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=15000")
                cursor.close()

    def migrate(self):
        """Initial versioned migration. Run once before API/worker startup.

        Later schema changes must be new migrations, not edits to version 1.
        """
        with self.engine.begin() as conn:
            if self.is_postgres:
                conn.execute(text("SELECT pg_advisory_xact_lock(71923811)"))
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            Base.metadata.create_all(conn)
            versions = list(conn.execute(text("SELECT version FROM schema_version")).scalars())
            if versions and versions != [SCHEMA_VERSION]:
                raise RuntimeError("Unsupported schema version; run the matching migrations")
            if not versions:
                if self.is_postgres:
                    conn.execute(text("ALTER TABLE chunks ADD COLUMN embedding_vec vector(1536)"))
                    conn.execute(text("CREATE INDEX chunks_vector_hnsw ON chunks USING hnsw (embedding_vec vector_cosine_ops)"))
                    conn.execute(text("CREATE INDEX chunks_search ON chunks USING gin (to_tsvector('english', body))"))
                else:
                    conn.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, body, tokenize='porter unicode61')"))
                conn.execute(text("INSERT INTO schema_version(version) VALUES (:v)"), {"v": SCHEMA_VERSION})

    def ready(self) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT version FROM schema_version")).scalar() == SCHEMA_VERSION

    def close(self):
        self.engine.dispose()
