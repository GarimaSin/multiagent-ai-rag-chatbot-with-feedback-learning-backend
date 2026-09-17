# HTTP and streaming contract

All `/api/*` endpoints require `Authorization: Bearer <application-api-key>`. The provider API key is never a browser credential. Input objects reject unexpected fields. IDs are UUID strings. The owner is derived from authentication, never accepted as a writable request-body field.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health/live` | Process liveness; public |
| GET | `/health/ready` | Database/schema readiness; public |
| GET | `/api/me` | Principal, role, provider and ingestion limit |
| GET / POST | `/api/conversations` | List/create the principal's conversations |
| GET | `/api/conversations/{id}/messages` | Up to the latest 200 messages, chronologically ordered |
| DELETE | `/api/conversations/{id}` | Delete an idle conversation and its associated learning |
| GET / POST | `/api/documents` | List documents / enqueue `{title,text}` |
| DELETE | `/api/documents/{id}` | Delete primary or learned document and applicable derivatives |
| POST | `/api/documents/{id}/reindex` | Remove old chunks and queue reindex |
| POST | `/api/chat/stream` | Stream a request described below |
| GET / POST | `/api/feedback` | Role-scoped queue / submit rating or correction |
| POST | `/api/feedback/{id}/review` | Reviewer/admin decision with note |
| GET | `/metrics` | Reviewer/admin Prometheus metrics |

See `openapi.json` for exact input shapes and validation constraints. List endpoints do not implement cursor pagination in this version; conversations/messages/review views have bounded results.

## Chat request

```json
{
  "conversation_id": "a UUID returned by conversation creation",
  "request_id": "a fresh client-generated UUID",
  "message": "What is the refund policy?",
  "mode": "knowledge",
  "quality": "fast"
}
```

`mode` accepts `knowledge` or `general`; `quality` accepts `fast` or `verified`. General replies remain unverified even if Verified is selected. A completed identical request-ID/payload pair returns the saved result; an ID reused with a changed payload conflicts. An unfinished failed/expired request is not automatically rerun with the old ID.

## SSE framing and lifecycle

Use `fetch` with POST and read `response.body`; native `EventSource` cannot provide this POST body and authorization flow. Every event is UTF-8:

```text
event: delta
data: {"text":"Refunds are available "}

```

Events are:

- `meta`: request metadata, before ordinary work.
- `status`: agent name and a short user-facing activity label. Not hidden reasoning.
- `sources`: provisional source objects with IDs, document/chunk identity, title and exact quote.
- `delta`: append provisional answer text. These tokens alone are not a saved answer.
- `done`: `{message: <persisted assistant message>, replayed: <boolean>}`. Replace the entire draft and its sources with this object.
- `error`: sanitized code/message and request ID. Do not treat an unfinished draft as successfully persisted.

An HTTP error before streaming uses its appropriate non-200 status. An error after headers is an SSE `error` event in an already opened response. A stream ending without `done` or a terminal error is truncated and must not be treated as success. The parser supports split UTF-8 bytes, multiline data, comments and CRLF. It discards an incomplete trailing frame instead of guessing its contents.

Fast mode can display a provisional claim that a later check rejects; the final replacement removes it. Verified mode holds generated content until the evidence check. Both modes persist the final answer atomically before `done`. The client must replace, not append, the final text or it could keep an unsafe draft or duplicate content.

## Feedback shape

A rating is `message_id` plus `rating` (`1` or `-1`). A correction additionally supplies `correction`, `consent: true`, `evidence_chunk_id` and an exact `evidence_quote`. Evidence must belong to a ready primary document owned by the author. The UI selects from the answer's sources. Approval is `{action: "approve", note: "..."}`; rejection uses `reject`. The note is required. Reviewer identity must differ from the feedback author's.

The workflow's stored statuses and details are the audit record, not a public training dataset. Consent is scoped to review and private knowledge improvement; it is not permission to export chats for model training.
