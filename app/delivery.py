from urllib.parse import urlsplit

import httpx

from app.signing import create_signature

class DeliveryConfigurationError(ValueError):
    pass


def validate_target(target_url: str, allowed_hosts: frozenset[str]) -> None:
    parsed = urlsplit(target_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")

    if parsed.scheme != "https":
        raise DeliveryConfigurationError("Webhook targets must use HTTPS")
    if not hostname or parsed.username or parsed.password or parsed.fragment:
        raise DeliveryConfigurationError("Webhook target URL is invalid")
    if parsed.port not in (None, 443):
        raise DeliveryConfigurationError("Webhook targets must use the default HTTPS port")
    if hostname not in allowed_hosts:
        raise DeliveryConfigurationError("Webhook target host is not allowlisted")


async def send_webhook(
    target_url: str,
    event_type: str,
    delivery_id: str,
    payload: dict,
    signing_secret: str,
    timeout_seconds: float,
) -> int:
    import json

    body = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=False,
    ) as client:
        response = await client.post(
            target_url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Event": event_type,
                "X-Webhook-Id": delivery_id,
                "X-Webhook-Signature": create_signature(signing_secret, body),
            },
        )
        response.raise_for_status()
        return response.status_code
