import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str
    allowed_webhook_hosts: frozenset[str]
    signing_secret: str
    max_attempts: int
    poll_interval_seconds: float
    request_timeout_seconds: float


def load_settings() -> Settings:
    hosts = os.getenv("WEBHOOK_ALLOWED_HOSTS", "")
    allowed_hosts = frozenset(
        host.strip().lower().rstrip(".")
        for host in hosts.split(",")
        if host.strip()
    )

    return Settings(
        database_path=os.getenv("DATABASE_PATH", "webhooks.db"),
        allowed_webhook_hosts=allowed_hosts,
        signing_secret=os.getenv("WEBHOOK_SIGNING_SECRET", ""),
        max_attempts=max(1, int(os.getenv("MAX_ATTEMPTS", "5"))),
        poll_interval_seconds=max(
            0.1, float(os.getenv("POLL_INTERVAL_SECONDS", "1"))
        ),
        request_timeout_seconds=max(
            0.1, float(os.getenv("REQUEST_TIMEOUT_SECONDS", "5"))
        ),
    )
