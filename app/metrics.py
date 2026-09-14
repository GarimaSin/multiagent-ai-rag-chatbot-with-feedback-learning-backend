from app.branding import APP_NAMESPACE
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class Metrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.chats = Counter(f"{APP_NAMESPACE}_chats_total", "Completed or failed chat requests", ["outcome"], registry=self.registry)
        self.active = Gauge(f"{APP_NAMESPACE}_active_chats", "Active chats in this API process", registry=self.registry)
        self.duration = Histogram(f"{APP_NAMESPACE}_chat_seconds", "End-to-end chat duration", buckets=(.1, .25, .5, 1, 2, 5, 10, 20, 40, 90), registry=self.registry)
        self.first_token = Histogram(f"{APP_NAMESPACE}_first_token_seconds", "Time until first answer token, excluding status events", buckets=(.1, .25, .5, 1, 2, 5, 10, 20, 40), registry=self.registry)
