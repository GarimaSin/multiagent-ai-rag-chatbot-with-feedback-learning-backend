# Security, privacy and responsible learning

## Implemented controls

Application API tokens are randomly generated for local bootstrap and stored as SHA-256 digests in the database. No real credentials ship in the archive. Each repository read/write checks principal ownership; another user cannot retrieve private conversation IDs or documents by guessing an identifier. Rate limits are shared through SQL. Reviewers receive only deliberately consented correction material and cannot approve their own corrections.

The provider secret stays in the API/worker environment. The frontend holds its application bearer token in memory only; logout and page reload clear it. It does not write keys or conversations to localStorage/sessionStorage. UI content is rendered with DOM text nodes rather than injected HTML. HTML/Markdown instructions inside a document are displayed as text. Same-origin proxying avoids putting the provider endpoint or credential into the client. Static-file whitelisting prevents serving the backend `.env` or arbitrary project files.

Request size, document size, output size, ingestion attempts, chat concurrency and provider timeouts are bounded. Responses include conservative headers. The frontend CSP disallows arbitrary remote scripts. The provider client does not follow redirects. Logs intentionally record error types and request IDs instead of prompts, quotes or provider response bodies. Database state changes use transactions, leases and fresh authorization checks.

## Not a security guarantee

The prompt-injection detector is a small heuristic. Prompt/data separation and strict output parsing reduce some failure modes but cannot eliminate model-level manipulation. The model is not granted browsing, shell execution, network tools or a filesystem tool, so a malicious source cannot directly invoke those capabilities through this application.

Outbound redaction masks a few email/API-key patterns. It is **not** complete PII detection, anonymization or data-loss prevention. Document bodies, chat content and saved source quotes are stored unencrypted by application code. Even redacted outbound content may remain identifying. Only send material to a live model that your organization authorizes for that provider. The code sends `store: false` for Responses requests, but this alone does not establish provider-wide zero retention or legal compliance; review your account's applicable data terms and controls.

Deleting a document removes its current retrieval index and learned derivatives, but saved historical assistant messages can still contain its quoted excerpt. Delete the affected conversations for application-level removal of those copies. Backups and logs outside the application need their own retention/deletion procedures. This is not a certified erasure workflow.

Bootstrapped API keys do not expire automatically and there is no user password flow, SSO, MFA, OAuth/OIDC, account recovery or organization membership model. A key authenticates its holder until revoked. Application role configuration is not PostgreSQL row-level security. Database operators and administrators with infrastructure access may read all stored material.

## Before public or sensitive deployment

1. Put the services behind TLS and a properly configured identity/access gateway; replace or integrate the bootstrap identity model with managed OIDC and key rotation.
2. Use managed PostgreSQL with least-privilege runtime access, a separate migration credential, encryption, network restrictions and restore-tested backups. The development Compose database role is not a hardened least-privilege design.
3. Adopt data classification, consent, redaction, retention, legal review and a provider-use policy. Isolate especially sensitive workloads.
4. Test prompt attacks, malicious source content, huge or adversarial documents, multi-user isolation, quotas and dependency/image vulnerabilities. Add ingestion scanning/connectors only with explicit security boundaries.
5. Configure ingress limits, connection timeouts, centralized alerts, audit collection and incident response. Never expose the demo stack directly to the public internet without review.

## Key management

`python scripts/setup.py` writes `.env` with mode 0600 on supporting systems. Keep it out of version control. `python -m app.manage create-user --name NAME --role user` creates another principal and prints its key once. The reviewer and admin roles are available through the same command.

`python -m app.manage revoke-key` reads the key from standard input. Avoid placing tokens into shell history, command-line arguments, screenshots or shared terminals. Clear or replace a revoked bootstrap token in `.env` as part of rotation; initialization can reseed configured tokens. Use a secret manager for deployed containers rather than relying on an exported developer environment.

Tests contain obvious synthetic fixture tokens only. CI includes a disposable database password for its isolated test service; it is not a deployment credential.
