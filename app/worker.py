import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.config import Settings
from app.delivery import DeliveryConfigurationError, send_webhook, validate_target
from app.store import WebhookStore

logger = logging.getLogger(__name__)
Sender = Callable[[str, str, str, dict[str, Any], str, float], Awaitable[int]]


async def run_worker(
    store: WebhookStore,
    settings: Settings,
    sender: Sender = send_webhook,
) -> None:
    while True:
        delivery = await asyncio.to_thread(store.claim_due)
        if delivery is None:
            await asyncio.sleep(settings.poll_interval_seconds)
            continue

        try:
            validate_target(
                delivery["target_url"],
                settings.allowed_webhook_hosts,
            )
            http_status = await sender(
                delivery["target_url"],
                delivery["event_type"],
                delivery["id"],
                delivery["payload"],
                settings.signing_secret,
                settings.request_timeout_seconds,
            )
        except (DeliveryConfigurationError, httpx.HTTPError) as exc:
            http_status = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            logger.warning(
                "Webhook delivery %s failed on attempt %s: %s",
                delivery["id"],
                delivery["attempts"],
                exc,
            )
            await asyncio.to_thread(
                store.mark_failed,
                delivery["id"],
                str(exc),
                delivery["attempts"],
                settings.max_attempts,
                http_status,
            )
        else:
            await asyncio.to_thread(
                store.mark_delivered,
                delivery["id"],
                http_status,
            )
