# Operations and latency

## Configuration

`app/config.py` is the authoritative validated settings schema. Environment variables are case-insensitive. `.env.example` lists the common settings; additional supported values can be set in the environment or `.env`.

| Variable | Default | Meaning |
|---|---:|---|
| `PROVIDER` | `demo` | Local extractive demo or `openai` live HTTP adapter |
| `DATABASE_URL` | SQLite file | Use `postgresql+psycopg://...` for PostgreSQL |
| `CHAT_MODEL` / `VERIFIER_MODEL` | `gpt-4.1-mini` | Configurable generation and verification model IDs |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Live embedding model; requires reindex when changed |
| `EMBEDDING_DIMENSIONS` | `1536` | Fixed schema width in this version |
| `FALLBACK_MODEL` | empty | Optional pre-first-token generation fallback |
| `REQUEST_DEADLINE_SECONDS` | `40` | Inference/retrieval budget, not end-to-end SLA |
| `PROVIDER_TIMEOUT_SECONDS` | `15` | HTTP provider timeout settings |
| `MAX_OUTPUT_TOKENS` | `800` | Generation token bound |
| `MAX_CONCURRENT_CHATS` | `16` | Per-process active chat admission bound |
| `REQUESTS_PER_MINUTE` | `60` | Shared per-user fixed-minute API request limit |
| `MAX_DOCUMENT_CHARS` | `200000` | Per-document text limit |
| `MAX_REQUEST_BYTES` | `3000000` | Entire incoming request-body limit |
| `MAX_DOCUMENTS_PER_USER` | `200` | Includes learned FAQ documents |
| `TOP_K` | `5` | Final retrieval context size |
| `MIN_SIMILARITY` | `0.23` | Vector threshold; corpus-dependent, not confidence |
| `CACHE_TTL_SECONDS` | `300` | Retrieval entry lifetime; zero disables reuse |
| `CACHE_MAX_ENTRIES` | `256` | Per-process LRU entry bound |
| `WORKER_POLL_SECONDS` | `1` | Document job poll interval |
| `WORKER_LEASE_SECONDS` | `180` | Job lease extended by heartbeat |
| `INGESTION_DEADLINE_SECONDS` | `120` | One processing attempt deadline |
| `CORS_ORIGINS` | local port 5173 | JSON array when set as an environment value |

Changing provider or embedding model changes the retrieval signature. Restart API and worker together and reindex all documents. Keep both processes on the same settings and database. Changing only the database URL moves to a different workspace; it does not migrate existing records.

## Latency choices

Default routing is local rather than another model call. Lexical retrieval runs alongside embedding computation. The retrieval cache avoids repeated search/embedding work for identical owner/version/query combinations. HTTP connections are reused. Context, output and queue work are bounded. Ingestion happens outside the chat request. Fast mode shows provisional answer tokens while the final check/commit is still pending.

Verified knowledge mode adds a separate model round trip and buffers until the verdict, so expect a longer wait. General mode does not retrieve documents and does not provide document-grounding checks. Turning off retrieval, verification or isolation solely to improve a benchmark changes the product's correctness boundary.

No live model latency or load benchmark was run during packaging. The included report records a single local extractive-demo first-token observation; it is not representative of a network model. Measure first **answer** token, complete-answer duration, throughput, errors and tail latency on realistic data before choosing deployment limits. Status events must not be counted as model tokens.

## Scaling and availability

API and worker are separate processes and may be replicated with a shared PostgreSQL database. Keep one API process per container so metric scraping and local admission limits are predictable. Total possible chat concurrency is roughly the per-process limit multiplied by replicas; it must fit your provider quota and database connection budget. The rate limit, conversation lease, document lease and completed request state are shared in SQL. Caches and circuit breakers are not shared.

SQLite is for local development and functional tests, not a supported production mode. It uses an exact vector scan, not an ANN index. PostgreSQL HNSW/FTS paths and indexes are implemented, but real deployment testing remains necessary. There is no claim of demonstrated million-document retrieval, horizontal throughput, autoscaling or high availability.

Provision database replicas/backups and failover, ingress connection draining, provider fallback policies, network controls and deployment rollback separately. `deploy/kubernetes.yaml` contains placeholders for your registry, ConfigMap and Secret and assumes an external PostgreSQL database. It does not provide a cluster, certificates, Ingress, policies or migration Job.

## Health, monitoring and schema

`/health/live` checks that the HTTP process is running. `/health/ready` checks initialized database connectivity/schema, not provider availability, worker progress or corpus quality. `/metrics` requires a reviewer/admin bearer token and exposes per-process chat outcome counts, active chats, duration and first-answer-token histograms. Protect scrape credentials. Add queue/worker-liveness and provider-budget alerts appropriate to your deployment.

Run `python -m app.manage init` once per release before API startup; initialization is idempotent and uses a PostgreSQL advisory migration lock. Version 1 is the only migration implemented. Add an explicit migration history before changing production schema. Use a separate, appropriately privileged migrator rather than retaining extension/schema privileges in the runtime account.

## Failure behavior and recovery

- **Missing initialization:** API startup rejects an absent/unrecognized schema; initialize the configured database.
- **Documents stay queued:** verify that the worker is running with the same database/provider configuration. Check worker logs, provider quota and index signatures.
- **Failed ingestion:** after three attempts the job is failed. Fix the issue and use Reindex to reset it.
- **Stream drops:** the UI keeps it unfinished. Retry first tries to recover a completed request using the same ID; explicitly failed/expired turns need a new ID. Provider billing may still have occurred.
- **409 busy conversation:** let the active turn finish or wait for its lease to expire after a crashed process. Do not create concurrent turns against the same history.
- **Changed knowledge during generation:** final output asks the user to retry rather than commit a new answer from a stale index snapshot.
- **429 rate limit:** wait for the next fixed minute. Document polling and navigation count toward the same user limit.
- **503 busy/provider error:** back off and inspect process concurrency/provider health. The app does not queue unlimited chat requests.
- **Correction rejected:** inspect the exact evidence and verifier reason. A reviewer cannot override an unsupported automated verdict through this interface; fix the correction/source and resubmit.

Logs intentionally omit source text and provider body details. Do not turn on broad request-body logging in a proxy to troubleshoot sensitive data. Back up the database with your database-native tools and periodically test restoration. Container image/dependency pins are supplied for reproducibility, not a claim of an ongoing vulnerability audit.
