# Architecture and correctness boundaries

## Request path

Authentication resolves a SHA-256 API-key digest to a principal. Every ordinary authenticated endpoint consumes a shared SQL rate-limit bucket. A local semaphore bounds simultaneously active chat handlers in one API process. Database calls run off the event loop through `asyncio.to_thread` and use short SQLAlchemy sessions.

Before opening a chat stream, `begin_chat` checks conversation ownership, the request UUID and canonical payload hash. A conditional conversation lease prevents concurrent turns from using inconsistent history. Completed requests replay the persisted answer. Pending duplicates conflict; failed or expired requests require a new request ID. This provides application-level idempotent persistence, not exactly-once billing at a remote provider.

The last eight messages, each bounded in length, provide conversational context. They are data, not a source of independently verified facts. The local planner handles greetings and chooses retrieval without spending a model call. A simple follow-up heuristic includes the previous user question in the retrieval query; it is not a general coreference model.

## Retrieval

Lexical search and query embedding run concurrently. Embedding failure falls back to lexical results and marks retrieval degraded. Reciprocal-rank fusion combines the rankings. The candidate limit, minimum vector similarity and final top-k bound the amount of evidence passed to the model. Scores are retrieval heuristics, not calibrated confidence probabilities.

SQLite FTS5 uses the Porter tokenizer; local vectors are stored as JSON and ranked by exact cosine similarity. PostgreSQL stores a parallel `vector(1536)` column, builds HNSW and full-text GIN indexes, and enables iterative HNSW scan within the query transaction. Owner filters and embedding-signature filters apply to both paths. The PostgreSQL path must be validated against your actual server/extension versions and corpus.

The process-local cache stores retrieval results only. Its key includes owner, knowledge revision, embedding signature, retrieval policy and query; entries expire and the cache is bounded. It is neither a distributed cache nor a response cache. Every owner-scoped knowledge mutation increments the revision so stale cache entries stop being reusable. In-flight turns recheck the revision after generation and again under the owner lock at final commit. A changed revision replaces the answer with a request to ask again. Historical saved answers remain historical snapshots until their conversation is deleted; deleting a document does not rewrite past messages.

## Answer and verification

Evidence is packaged as JSON data with stable per-answer source IDs. No retrieved instruction is intentionally promoted to a system instruction. Heuristic injection detection can discard obvious malicious chunks but cannot establish that all remaining content is safe.

Fast mode streams draft tokens. Its final check verifies reference existence and citation coverage; it does not prove that a cited passage entails the answer. Verified knowledge mode buffers the answer, asks a separate model call for a strict structured support verdict, and only then emits answer text. The default verifier model is configurable and may be the same family as the answering model; errors can be correlated. Neither mode establishes source truthfulness.

The provider adapter allows at most two attempts before any visible token, with small jitter and an optional fallback model. A stream interrupted after a delta is not restarted and spliced into the existing answer. Completion requires the provider's explicit completed event. Empty, incomplete, refused, invalid and truncated responses do not silently become final successful messages. Generation and embedding have separate process-local circuit breakers.

The inference/retrieval deadline excludes final database commit and transport buffering. There is no promised end-to-end maximum latency. Final commit is shielded so cancellation does not leave ambiguous half-persisted turns. If a connection disappears after commit, the client can recover the completed message using the same request ID. `done` is sent only after that atomic commit.

## Durable ingestion

Uploaded text becomes a queued document. Workers claim jobs with a random lease token; PostgreSQL additionally supports `FOR UPDATE SKIP LOCKED`. A heartbeat extends the lease while the worker processes bounded chunks and embedding batches. Publishing is a conditional, fenced transaction: a stale worker cannot overwrite a later claimant. Failed work retries with backoff up to three attempts. Expired leases become reclaimable. Reindexing removes existing chunks and queues a new ingestion.

The document limit and chunk limit bound individual inputs, but there is no global storage quota, account billing quota, broker, object storage layer or queue-depth autoscaler. The SQL queue was chosen to keep this application deployable without extra infrastructure. It is not a substitute for measuring queue behavior at scale.

## Reviewed learning

A correction references an assistant message owned by its author and an exact quote from a ready primary document owned by that author. Learned documents cannot be primary evidence for another correction; this limits recursive self-citation. Consent is required before material enters the reviewer-visible queue. Reviewers cannot access the author's full private conversation through the ordinary chat endpoints.

A different reviewer submits an approve/reject decision and a note. Approval additionally requires the LearningAgent's evidence-support verdict. The final transaction rechecks consent, pending state, ownership and evidence freshness and creates a private FAQ document once. Its parent points to the primary source, and its origin points to the feedback item. It becomes searchable only after the worker publishes its chunks. Rejecting a stale correction is allowed, but approving stale evidence is not.

Deleting a primary source removes its derived learned documents and records withdrawn feedback. Deleting a conversation removes associated learning derivatives as well. Feedback ratings without corrections are retained as feedback, not automatically converted into training labels or model updates.

## Deployment boundaries

Use one API process per container and scale container replicas. Local semaphores, caches, breakers and metrics remain per process. SQL ownership, rate buckets, idempotency and leases are shared. PostgreSQL availability, connection budgets, ingress streaming behavior, cross-replica operational monitoring and graceful rollout must be engineered in the deployment.

`db.migrate()` contains the initial schema version only and refuses unknown schema versions. It is not a full migration history system. Add explicit reviewed migrations before modifying an existing deployed schema. The Kubernetes file is a customization sketch, not a turnkey cluster installation.
