import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.provider import DemoProvider
from app.repository import Repository
from app.worker import IngestionWorker

USER_KEY = "u" * 48
OTHER_KEY = "o" * 48
REVIEWER_KEY = "r" * 48
ADMIN_KEY = "a" * 48


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, app_env="test", database_url=f"sqlite:///{tmp_path}/test.db", requests_per_minute=10000)
    db = Database(settings.database_url)
    db.migrate()
    repo = Repository(db)
    user = repo.seed_user("Alice", "user", USER_KEY)
    other = repo.seed_user("Bob", "user", OTHER_KEY)
    reviewer = repo.seed_user("Reviewer", "reviewer", REVIEWER_KEY)
    admin = repo.seed_user("Admin", "admin", ADMIN_KEY)
    provider = DemoProvider()
    worker = IngestionWorker(repo, provider, settings)
    application = create_app(settings, provider=provider, database=db)
    with TestClient(application) as client:
        yield SimpleNamespace(settings=settings, db=db, repo=repo, user=user, other=other, reviewer=reviewer, admin=admin, provider=provider, worker=worker, app=application, client=client,
            auth={"Authorization": "Bearer " + USER_KEY}, other_auth={"Authorization": "Bearer " + OTHER_KEY}, reviewer_auth={"Authorization": "Bearer " + REVIEWER_KEY}, admin_auth={"Authorization": "Bearer " + ADMIN_KEY})


def ingest(env, body="Refunds are available within 30 days of purchase. A receipt is required for all refunds.", user=None, title="Policy"):
    doc = env.repo.add_document((user or env.user).id, title, body)
    asyncio.run(env.worker.run_once())
    return doc
