# Project naming and upgrade notes

Project display name: **Multi-Agent AI RAG Chatbot with Feedback Learning**.

Filesystem/package slug: `multi-agent-ai-rag-chatbot-with-feedback-learning`.

Both ZIPs extract to sibling directories named `multi-agent-ai-rag-chatbot-with-feedback-learning-backend` and
`multi-agent-ai-rag-chatbot-with-feedback-learning-frontend`. Docker Compose build paths refer to those exact names.

## What changed

The UI brand, assistant labels, browser title, favicon, system-prompt identity,
API title, CLI text, package metadata, logger/metric prefixes, policy identifier,
new-install database defaults, deployment resource names and documentation now
use the new project identity. Backend runtime values live in `app/branding.py`;
frontend runtime values live in `src/branding.js`. Static HTML, package metadata,
and deployment manifests carry matching values checked by the branding tests.

The non-root container account is called `chatbot`; a descriptive display name
is not used as an operating-system username. PostgreSQL identifiers and metric
prefixes use underscores; filesystem, image and Kubernetes names use hyphens.

## Existing installations: preserve data

This update changes source files and fresh-install defaults only. It does not
connect to or alter any running installation, stored conversation, or document.

Before upgrading, back up the existing database and configuration. Keep your
existing `.env`, API keys, and `DATABASE_URL`. A relative SQLite path must still
resolve to the existing database after moving directories, or be changed to an
absolute SQLite URL. Do not overwrite your configuration with `.env.example`.

For an existing Docker Compose deployment, preserve the previous Compose project
name (for example via `docker compose -p YOUR_EXISTING_PROJECT_NAME ...`) and its
existing database volume, PostgreSQL role, database name, password and connection
URL. The renamed Compose file contains fresh-install defaults; adapt those
settings before starting it against existing data. Never remove database volumes
as part of a branding change. This package does not include an automatic data
migration.

Kubernetes resource references also changed; coordinate an upgrade of manifests,
ConfigMaps, Secrets, image names and Services rather than applying them blindly
to an existing deployment.

The new Prometheus prefix is `multi_agent_ai_rag_chatbot_with_feedback_learning_`; update existing dashboards and alert
queries accordingly. Old stored messages and historical audit metadata are
left untouched. The updated policy identifier is stored on future operations.
