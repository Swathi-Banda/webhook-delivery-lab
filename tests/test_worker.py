import asyncio
import sqlite3
import time

import httpx

from app.config import Settings
from app.store import WebhookStore
from app.worker import run_worker


def test_worker_retries_transient_failure_and_delivers(tmp_path):
    database_path = str(tmp_path / "worker.db")
    store = WebhookStore(database_path)
    store.initialize()
    delivery_id = store.enqueue(
        "https://hooks.example.test/events",
        "demo.created",
        {"message": "retry test"},
    )
    settings = Settings(
        database_path=database_path,
        allowed_webhook_hosts=frozenset({"hooks.example.test"}),
        signing_secret="unit-test-secret",
        max_attempts=3,
        poll_interval_seconds=0.01,
        request_timeout_seconds=1,
    )
    calls = 0

    async def flaky_sender(
        target_url,
        event_type,
        event_id,
        payload,
        signing_secret,
        timeout_seconds,
    ):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("simulated temporary failure")
        return 200

    async def wait_for_record(predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            record = await asyncio.to_thread(store.get, delivery_id)
            if predicate(record):
                return record
            await asyncio.sleep(0.01)
        raise AssertionError("delivery did not reach the expected state in time")

    async def exercise_worker():
        worker = asyncio.create_task(run_worker(store, settings, flaky_sender))
        try:
            failed_attempt = await wait_for_record(
                lambda record: record["status"] == "pending"
                and record["attempts"] == 1
            )
            assert failed_attempt["last_error"] == "simulated temporary failure"

            # Make the scheduled retry due now so the test stays fast.
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "UPDATE webhook_deliveries SET next_attempt_at = ? WHERE id = ?",
                    (time.time(), delivery_id),
                )

            delivered = await wait_for_record(
                lambda record: record["status"] == "delivered"
            )
            return delivered
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    result = asyncio.run(exercise_worker())

    assert calls == 2
    assert result["attempts"] == 2
    assert result["last_error"] is None
    assert result["delivered_at"] is not None
    assert [attempt["status"] for attempt in result["attempt_history"]] == [
        "failed",
        "delivered",
    ]
    assert [attempt["attempt"] for attempt in result["attempt_history"]] == [1, 2]
