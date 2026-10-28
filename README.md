# Reliable Webhook Delivery

A small, independently written portfolio project demonstrating a durable webhook outbox, bounded retries, delivery history, HMAC signatures, and explicit outbound-host controls.

## What it does

- Accepts webhook events through `POST /api/v1/webhooks`.
- Persists accepted events in SQLite before acknowledging them.
- Uses a background worker to deliver events and retries failures with exponential backoff.
- Stops retrying after a configurable attempt limit.
- Records each delivery attempt, including timestamps, HTTP status, failure details, and retry delay.
- Signs each exact JSON request body with HMAC-SHA256.
- Supports `Idempotency-Key` on event submissions to avoid duplicate jobs when clients retry requests.
- Allows failed deliveries to be replayed as new, linked deliveries.
- Includes a browser dashboard at `/` for submitting and observing events.
- Restricts outbound delivery to administrator-configured HTTPS hostnames.

The service sends the event JSON body and includes `X-Webhook-Event`, `X-Webhook-Id`, and `X-Webhook-Signature` headers. The signature format is `sha256=<hex digest>` and is calculated over the exact request body. Receivers should verify it against the raw body using the same secret, then parse the JSON. The event ID can be used for receiver-side idempotency.

The service does not claim exactly-once delivery: a process can fail after the receiver accepts a request but before the sender records success. Receivers should therefore handle duplicate events idempotently.

## Idempotent requests and replay

Send an `Idempotency-Key` header with `POST /api/v1/webhooks`. Repeating the
same key with the same target, event type, and payload returns the original
delivery ID. Reusing the key for a different request returns HTTP `409`.
Keys are unique across this demo database and are not scoped to authenticated
tenants because this project does not implement authentication.

Failed deliveries can be replayed with
`POST /api/v1/webhooks/{delivery_id}/replay`. Replay creates a new delivery ID,
resets its attempt count, and records the original ID in `replay_of`; the
original attempt history remains unchanged. Only terminal `failed` deliveries
can be manually replayed.

## Run locally

Requires Python 3.10 or later.

```powershell
py -m venv .venv
$python = ".\.venv\Scripts\python.exe"
& $python -m pip install -r requirements.txt
$env:WEBHOOK_ALLOWED_HOSTS = "hooks.example.com"
$env:WEBHOOK_SIGNING_SECRET = (& $python -c "import secrets; print(secrets.token_hex(32))")
& $python -m uvicorn app.main:app --reload
```

Only configure hostnames you control or explicitly trust. The endpoint remains disabled when the allowlist is empty. In production, enforce outbound network policy as well; hostname allowlisting is not a substitute for egress controls or protection against DNS changes.

OpenAPI docs are available at `http://127.0.0.1:8000/docs`; the dashboard is at `http://127.0.0.1:8000/`. Enter an HTTPS URL whose hostname is in the allowlist. Use a webhook test inbox and dummy data only. The dashboard generates an idempotency key for each page load; reuse it to demonstrate duplicate-submit protection.

## Example

```powershell
$body = @{
  target_url = "https://hooks.example.com/events"
  event_type = "invoice.created"
  payload = @{ invoice_id = "inv_demo_123" }
} | ConvertTo-Json

$accepted = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/webhooks `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/webhooks/$($accepted.id)"
```

## Configuration

| Variable | Default | Description |
|---|---:|---|
| `DATABASE_PATH` | `webhooks.db` | SQLite database file |
| `WEBHOOK_ALLOWED_HOSTS` | empty | Comma-separated exact hostnames permitted as delivery targets |
| `WEBHOOK_SIGNING_SECRET` | empty | Shared HMAC secret; delivery API stays disabled when unset |
| `MAX_ATTEMPTS` | `5` | Maximum total delivery attempts |
| `POLL_INTERVAL_SECONDS` | `1` | Delay when no due event is available |
| `REQUEST_TIMEOUT_SECONDS` | `5` | HTTP timeout per delivery attempt |

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The worker retry test uses a simulated network failure on attempt one, verifies
that the delivery is rescheduled, then makes the retry due immediately and
verifies that attempt two succeeds. Signature tests check successful
verification and reject modified payloads or incorrect secrets. Store tests
cover idempotent submissions, key/payload conflicts, and replaying failed
deliveries. These tests do not send a failing request to a third-party service.
GitHub Actions runs the test suite on pushes and pull requests.

## Receiver signature verification

The receiver must verify the **raw** request bytes before parsing JSON:

```python
from app.signing import verify_signature

raw_body = await request.body()
signature = request.headers.get("X-Webhook-Signature", "")
if not verify_signature(shared_secret, raw_body, signature):
    raise HTTPException(status_code=401, detail="Invalid webhook signature")
payload = json.loads(raw_body)
```

In a separate receiver project, copy the small `verify_signature` implementation
from `app/signing.py` rather than importing this sender application.

## Limitations and next steps

This is a learning project, not a production-ready hosted service. It uses a single polling worker and SQLite. A production system would need authenticated tenant-aware APIs, secret rotation and per-endpoint signing secrets, rate limits, delivery concurrency controls, metrics, retention policies, managed secrets, and carefully designed network egress. Host allowlisting alone does not protect against DNS rebinding; enforce outbound network policy as well.
