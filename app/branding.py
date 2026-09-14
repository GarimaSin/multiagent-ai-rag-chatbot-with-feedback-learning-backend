"""Project identity shared by API metadata, prompts, logging and metrics.

Deployment manifests and package metadata also carry the filesystem-safe slug.
"""

APP_NAME = 'Multi-Agent AI RAG Chatbot with Feedback Learning'
APP_SLUG = 'multi-agent-ai-rag-chatbot-with-feedback-learning'
APP_NAMESPACE = APP_SLUG.replace("-", "_")
DEFAULT_DATABASE_URL = f"sqlite:///./data/{APP_NAMESPACE}.db"
