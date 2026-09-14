from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from app.branding import DEFAULT_DATABASE_URL
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["development", "production", "test"] = "development"
    database_url: str = DEFAULT_DATABASE_URL
    provider: Literal["demo", "openai"] = "demo"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    chat_model: str = "gpt-4.1-mini"
    verifier_model: str = "gpt-4.1-mini"
    fallback_model: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: Literal[1536] = 1536
    request_deadline_seconds: float = Field(40, ge=1, le=180)
    provider_timeout_seconds: float = Field(15, ge=.1, le=120)
    max_output_tokens: int = Field(800, ge=64, le=4096)
    max_concurrent_chats: int = Field(16, ge=1, le=256)
    requests_per_minute: int = Field(60, ge=1, le=10000)
    max_document_chars: int = Field(200_000, ge=1000, le=500_000)
    max_request_bytes: int = Field(3_000_000, ge=5000, le=5_000_000)
    max_documents_per_user: int = Field(200, ge=1, le=10000)
    top_k: int = Field(5, ge=1, le=12)
    min_similarity: float = Field(.23, ge=0, le=1)
    cache_ttl_seconds: int = Field(300, ge=0, le=3600)
    cache_max_entries: int = Field(256, ge=0, le=10000)
    worker_poll_seconds: float = Field(1, ge=.05, le=60)
    worker_lease_seconds: int = Field(180, ge=10, le=3600)
    ingestion_deadline_seconds: int = Field(120, ge=5, le=1800)
    user_api_key: str = ""
    reviewer_api_key: str = ""
    admin_api_key: str = ""
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def embedding_signature(self) -> str:
        model = "hash-v1" if self.provider == "demo" else self.embedding_model
        return f"{self.provider}:{model}:{self.embedding_dimensions}"

    @model_validator(mode="after")
    def validate_runtime(self):
        if self.provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for PROVIDER=openai")
        if self.app_env == "production" and self.database_url.startswith("sqlite"):
            raise ValueError("Production requires PostgreSQL; SQLite is for local development")
        parsed = urlparse(self.openai_base_url)
        if parsed.scheme != "https" and not (
            self.app_env != "production" and parsed.hostname in {"localhost", "127.0.0.1"}
        ):
            raise ValueError("Provider URL must use HTTPS (localhost allowed in development)")
        if self.database_url.startswith("sqlite:///") and self.database_url != "sqlite:///:memory:":
            Path(self.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return self
