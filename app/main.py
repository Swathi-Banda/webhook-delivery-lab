import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse

from app.config import load_settings
from app.delivery import DeliveryConfigurationError, validate_target
from app.schemas import DeliveryStatus, WebhookAccepted, WebhookCreate
from app.store import IdempotencyConflict, WebhookStore
from app.worker import run_worker

settings = load_settings()
store = WebhookStore(settings.database_path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(store.initialize)
    worker = asyncio.create_task(run_worker(store, settings))
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Reliable Webhook Delivery",
    description="A small durable webhook queue with bounded retries.",
    version="1.0.0",
    lifespan=lifespan,
)

dashboard_path = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(dashboard_path)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/webhooks",
    response_model=WebhookAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_webhook(
    event: WebhookCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> WebhookAccepted:
    if idempotency_key is not None and (
        not idempotency_key.strip() or len(idempotency_key) > 200
    ):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain 1 to 200 non-whitespace characters",
        )
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
    if not settings.signing_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook delivery is disabled until WEBHOOK_SIGNING_SECRET is configured",
        )
    if not settings.allowed_webhook_hosts:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook delivery is disabled until WEBHOOK_ALLOWED_HOSTS is configured",
        )

    target_url = str(event.target_url)
    try:
        validate_target(target_url, settings.allowed_webhook_hosts)
    except DeliveryConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        delivery_id, deduplicated = await asyncio.to_thread(
            store.enqueue_with_result,
            target_url,
            event.event_type,
            event.payload,
            idempotency_key,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record = await asyncio.to_thread(store.get, delivery_id)
    return WebhookAccepted(
        id=delivery_id,
        status=record["status"],
        deduplicated=deduplicated,
        replay_of=record["replay_of"],
    )


@app.post(
    "/api/v1/webhooks/{delivery_id}/replay",
    response_model=WebhookAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replay_webhook(delivery_id: str) -> WebhookAccepted:
    if not settings.signing_secret or not settings.allowed_webhook_hosts:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook delivery requires a signing secret and configured host allowlist",
        )

    original = await asyncio.to_thread(store.get, delivery_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Webhook delivery not found")
    try:
        validate_target(original["target_url"], settings.allowed_webhook_hosts)
        replay_id = await asyncio.to_thread(store.replay, delivery_id)
    except DeliveryConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return WebhookAccepted(
        id=replay_id,
        status="pending",
        replay_of=delivery_id,
    )


@app.get(
    "/api/v1/webhooks/{delivery_id}",
    response_model=DeliveryStatus,
)
async def get_webhook_status(
    delivery_id: str,
) -> DeliveryStatus:
    record = await asyncio.to_thread(store.get, delivery_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Webhook delivery not found")
    return DeliveryStatus(**record)
