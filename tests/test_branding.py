"""Regression checks for project identity without changing chatbot behavior."""
import json
import re
import tomllib
from pathlib import Path

from prometheus_client import generate_latest

from app.branding import APP_NAME, APP_SLUG, APP_NAMESPACE, DEFAULT_DATABASE_URL
from app.config import Settings
from app.metrics import Metrics
from app.provider import ANSWER_INSTRUCTIONS

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NAME = 'Multi-Agent AI RAG Chatbot with Feedback Learning'
EXPECTED_SLUG = 'multi-agent-ai-rag-chatbot-with-feedback-learning'


def test_project_identity_and_safe_identifiers():
    assert APP_NAME == EXPECTED_NAME
    assert APP_SLUG == EXPECTED_SLUG
    assert APP_NAMESPACE == APP_SLUG.replace("-", "_")
    assert re.fullmatch(r"[a-z][a-z0-9_]*", APP_NAMESPACE)
    assert len(APP_SLUG + "-backend") <= 63


def test_api_title_uses_project_name(env):
    response = env.client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == f"{EXPECTED_NAME} API"


def test_answer_prompt_uses_project_name():
    assert ANSWER_INSTRUCTIONS.startswith(f"You are {EXPECTED_NAME},")
    assert "untrusted DATA" in ANSWER_INSTRUCTIONS
    assert "Cite every factual" in ANSWER_INSTRUCTIONS


def test_database_default_and_explicit_connection_override():
    assert DEFAULT_DATABASE_URL == f"sqlite:///./data/{APP_NAMESPACE}.db"
    assert Settings.model_fields["database_url"].default == DEFAULT_DATABASE_URL
    settings = Settings(_env_file=None, database_url="sqlite:///:memory:", provider="demo", app_env="test")
    assert settings.database_url == "sqlite:///:memory:"


def test_metrics_use_project_namespace():
    metrics = Metrics()
    metrics.chats.labels(outcome="ok").inc()
    output = generate_latest(metrics.registry).decode()
    for suffix in ("chats_total", "active_chats", "chat_seconds", "first_token_seconds"):
        assert f"{APP_NAMESPACE}_{suffix}" in output


def test_static_metadata_matches_runtime_identity():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["name"] == f"{APP_SLUG}-backend"
    schema = json.loads((ROOT / "docs/openapi.json").read_text())
    assert schema["info"]["title"] == f"{APP_NAME} API"
    assert f"DATABASE_URL={DEFAULT_DATABASE_URL}" in (ROOT / ".env.example").read_text()
    assert f"../{APP_SLUG}-frontend" in (ROOT / "compose.full.yaml").read_text()
