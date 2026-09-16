# Multi-Agent AI RAG Chatbot with Feedback Learning — Backend

A runnable, bounded multi-agent chatbot with private document retrieval, streamed answers, persistent conversations, and evidence-backed, human-reviewed knowledge improvement. The matching browser application is in the separate `multi-agent-ai-rag-chatbot-with-feedback-learning-frontend` ZIP.

**What this is:** working application code, automated tests, a no-key local demo, an OpenAI HTTP adapter, a separate ingestion worker, and deployment configuration. **What it is not:** a guarantee of factual correctness, a measured production latency SLA, autonomous weight training, or a security-certified public service.

## Start here

Extract both archives into the same parent directory:

```text
workspace/
  multi-agent-ai-rag-chatbot-with-feedback-learning-backend/
  multi-agent-ai-rag-chatbot-with-feedback-learning-frontend/
```

The demo runs without a paid API key. It uses deterministic hashed embeddings and extracts sentences from your documents; it is intentionally **not a generative language model**. Enable the live provider for natural-language generation.

### Local setup: Python 3.11+ and Node.js 20+

Run commands from the directory indicated. On macOS/Linux:

**Terminal 1 — API**

```bash
cd multi-agent-ai-rag-chatbot-with-feedback-learning-backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python scripts/setup.py
python -m app.manage init
python -m app.manage seed-demo
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

`setup.py` creates a private `.env` with four independently generated credentials. It never overwrites an existing file. On Windows, activate the virtual environment with `.venv\Scripts\activate` instead.

**Terminal 2 — document worker**

```bash
cd multi-agent-ai-rag-chatbot-with-feedback-learning-backend
source .venv/bin/activate
python -m app.worker
```

The worker is required: documents remain queued until it runs. SQLite uses a local file under `data/`; no database server is needed for this route.

**Terminal 3 — frontend**

```bash
cd multi-agent-ai-rag-chatbot-with-feedback-learning-frontend
npm start
```

Open `http://127.0.0.1:5173`. Open the backend `.env` in a local editor and copy **USER_API_KEY** into the sign-in form. Never put **OPENAI_API_KEY** into the browser. Ask “What is the refund policy?” once the sample document is Ready. The sample policy is fictional.

Use **REVIEWER_API_KEY** in a second browser session or after signing out to review consented corrections. The author cannot approve their own correction, even when their role is admin.

The API schema is available at `http://127.0.0.1:8000/docs`; a generated copy is included as `docs/openapi.json`. The UI is the easiest way to exercise authenticated endpoints. The custom bearer dependency is not represented as an interactive Swagger Authorize button.

### Enable the real AI provider

Stop API and worker, then edit these backend `.env` values:

```dotenv
PROVIDER=openai
OPENAI_API_KEY=your_server_side_key
CHAT_MODEL=gpt-4.1-mini
VERIFIER_MODEL=gpt-4.1-mini
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
```

Restart both processes. **Reindex existing documents in the Knowledge tab** after changing the provider or embedding model, including learned documents. Documents indexed under a different embedding signature are deliberately excluded from retrieval. The code fixes vector width at 1536; switching to another width requires a schema migration and complete reindex, not just an environment change.

Model names are configurable examples, not a claim that they are the latest, cheapest, or available to every account. Real calls incur your provider's charges. No live paid-provider call was made while validating this package. A separate verifier call checks a candidate answer in Verified knowledge mode; it can still be wrong, especially when both calls use the same model family.

## How the agents work

```text
Browser --POST + streaming events--> FastAPI
                                      |
                                 Supervisor
                                      |
                              PlannerAgent (local)
                                      |
                RetrievalAgent (lexical + vector, parallel)
                                      |
                              AnswerAgent (model)
                                      |
                VerifierAgent (citations / optional model)
                                      |
                     Atomic message commit --> final event

Document upload --> durable DB job --> worker --> chunks + embeddings
User correction --> consent + exact evidence --> different human reviewer
                                      |
                              LearningAgent check
                                      |
                     private learned FAQ --> worker --> retrieval
```

These are five separately testable roles under a finite supervisor, not five model calls on every turn. There is no autonomous tool loop, browsing, shell access, recursive agent debate, or arbitrary code execution. That keeps the default path bounded and avoids spending extra model calls on simple routing.

### Answer modes

| Choice | Behavior | Important limitation |
|---|---|---|
| Knowledge + Fast | Hybrid retrieval, one answer-generation call, streamed draft, local citation checks | Citation validity is not semantic fact checking. A provisional draft may be replaced at the end. |
| Knowledge + Verified | Retrieval, buffered generation, separate model evidence check, then display | Slower; a model verdict is not proof of truth. |
| General | No document retrieval; normal model response | Not document-verified and has no live web access. |
| Demo provider | Extractive local answer and deterministic checks | Useful for testing the workflow, not evaluating real model quality. |

A knowledge question without sufficiently relevant evidence receives an explicit abstention. Invalid citations or an unsupported verification verdict also replace the draft with an abstention. The final `done` event, not a stream token or a source event, is the authority for the saved answer.

### What “self-learning” means here

A user rates an answer or submits a correction. Ratings alone do not alter the knowledge base. A correction must include explicit consent, an exact quote from the user's own ready primary document, and its chunk ID. A different reviewer checks the proposed correction and requests approval. The LearningAgent verifies support; the database then revalidates ownership, consent, evidence and review state before creating a private learned FAQ for indexing.

Approved knowledge improves what later turns can retrieve. It does **not** update model weights, learn from every chat automatically, promise monotonically better accuracy, or share knowledge across users. Deleting the primary source withdraws its learned derivatives. Deleting a conversation removes its associated feedback and learned derivatives. Reindexing a source may invalidate the old chunk references of pending corrections; submit a new correction against the current evidence.

## Included capabilities

- Private users, conversations, documents and retrieval, with hashed application API keys and role-based review.
- Persistent history, atomic user/assistant turn commits, request-ID replay and one active generation per conversation.
- SSE over authenticated POST, cancellation, surfaced failures and explicit draft-to-final replacement.
- Background chunking and batched embeddings with durable leases, retries, backoff and stale-lease recovery.
- SQLite FTS5 plus exact vector search locally; PostgreSQL full text plus pgvector/HNSW for deployment.
- Tenant- and knowledge-version-scoped retrieval caching; no cross-user answer cache.
- Parallel retrieval, bounded context/output, connection reuse, provider timeouts and circuit breakers.
- Shared database-backed per-user request limits, a per-process chat concurrency limit, health endpoints and protected Prometheus metrics.
- Conservative prompt/data separation, small outbound redaction rules, safe browser text rendering and source excerpts.
- Docker images, a two-archive Compose stack, CI workflows, optional PostgreSQL integration tests and a Kubernetes deployment sketch.

## Docker Compose: both applications

This is an alternative to the three local terminals; stop local services first to avoid port conflicts. Docker must be installed and running. Extract the ZIPs into sibling folders, then:

```bash
cd multi-agent-ai-rag-chatbot-with-feedback-learning-backend
python3 scripts/setup.py  # Skip if .env already exists.
docker compose -f compose.yaml -f compose.full.yaml up --build -d
```

This starts PostgreSQL/pgvector, a one-off schema initializer, the API, worker and frontend. The configuration defaults to the demo provider; edit `.env` before starting for real AI. Visit `http://127.0.0.1:5173` and sign in using `USER_API_KEY` from `.env`. Upload a `.txt`/`.md` document, or seed the sample:

```bash
docker compose exec api python -m app.manage seed-demo
docker compose logs -f api worker
```

Stop the stack with both Compose files:

```bash
docker compose -f compose.yaml -f compose.full.yaml down
```

The database volume remains. Adding `-v` deliberately deletes it. Docker images and deployment paths were supplied and syntax-checked, but Docker and PostgreSQL were unavailable for execution in the build environment. See the test report before relying on this configuration in a deployment.

## Tests

```bash
python -m pytest -q
python -m pytest --cov=app --cov-report=term-missing
```

The unit/API suite uses temporary SQLite databases and a deterministic or mocked provider; it does not charge a model account. For the real HTTP workflow, run the local demo API and worker, export the user and reviewer keys from your local `.env`, then:

```bash
python scripts/smoke.py
# To exercise the frontend reverse proxy as well:
BASE_URL=http://127.0.0.1:5173 python scripts/smoke.py
```

The smoke script requires `USER_API_KEY` and `REVIEWER_API_KEY` in the process environment; dotenv parsing does not happen inside that standalone script. Use a disposable workspace. It creates its own records and attempts cleanup.

For an actual PostgreSQL test, install `.[test,postgres]`, start a disposable PostgreSQL server with pgvector, set `TEST_POSTGRES_URL=postgresql+psycopg://...`, and run:

```bash
python -m pytest integration/test_postgres.py -q
```

The PostgreSQL test can create schemas/extensions and data. Never point it at a production database. The included CI workflow is configured to run it, but the workflow itself has not run as part of packaging.

## Project map

```text
app/
  main.py          Authenticated API, streaming and lifecycle
  supervisor.py    Finite answer workflow
  agents.py        Planner, retrieval, answer, verifier and learning roles
  provider.py      Demo provider and live Responses/Embeddings adapter
  repository.py    Ownership, transactions, leases, search and review
  models.py        Persistent entities
  db.py            SQLite/PostgreSQL setup and initial migration
  worker.py        Durable ingestion worker
  config.py        Validated configuration
  text.py          Chunking, local embeddings and basic sanitization
  cache.py         Bounded retrieval cache
  schemas.py       Strict API input models
  metrics.py       Per-process Prometheus metrics
  manage.py        Initialize, seed, create users and revoke keys
scripts/           Setup and real-HTTP smoke test
sample-data/       Fictional example knowledge
integration/       Opt-in real PostgreSQL test
tests/            Unit/API/provider-wire-contract tests
docs/             Architecture, security, operations, API and test report
```

## Limits and deployment work still required

Only UTF-8 plain text and Markdown ingestion is implemented. There is no PDF parser, OCR, URL crawler, document connector, attachment antivirus, fine-tuning pipeline, reranker, team-shared knowledge, SSO, billing system, or automated model-quality benchmark. The default limits are 200 documents per user and 200,000 characters per document, with at most 800 chunks per ingestion. SQLite vector search scans the owner's matching chunks and is intended for small local corpora; use PostgreSQL and measure real workloads for scale.

Bootstrap API keys are a development/internal-service authentication path, not an enterprise identity system. Before public exposure, add an identity provider, operational key rotation, TLS, network restrictions, restore-tested backups, encryption/retention policies, central monitoring, realistic load tests and a security review. The API/worker split supports independent scaling; it does not itself provide high availability. Review queues intentionally disclose consented correction material to reviewers.

See [ARCHITECTURE](docs/ARCHITECTURE.md), [SECURITY](docs/SECURITY.md), [OPERATIONS](docs/OPERATIONS.md), [API](docs/API.md) and [TEST_REPORT](docs/TEST_REPORT.md).

## Official implementation references

The provider wire contract and PostgreSQL search configuration were checked against primary documentation during implementation:

- OpenAI streaming responses: https://developers.openai.com/api/docs/guides/streaming-responses
- OpenAI structured outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- pgvector indexes and filtered search: https://github.com/pgvector/pgvector
- FastAPI streaming responses: https://fastapi.tiangolo.com/advanced/custom-response/

These references explain the external APIs. They do not certify this implementation or replace the deployment tests in the report.

## License

MIT; see `LICENSE`. External packages and container images retain their respective licenses.

## Project name and existing installations

The display name is **Multi-Agent AI RAG Chatbot with Feedback Learning**. The lowercase filesystem/package slug is
`multi-agent-ai-rag-chatbot-with-feedback-learning`. The runtime name is centralized in
`app/branding.py`.

See [renaming notes](docs/RENAMING.md) before replacing an existing installation.
Keep existing secrets and database connections; this source-code rename does not
migrate, delete, or rename any existing database or Docker volume.
