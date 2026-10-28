import time

import pytest

from app.store import IdempotencyConflict, WebhookStore


def make_store(tmp_path):
    store = WebhookStore(str(tmp_path / "test.db"))
    store.initialize()
    return store


def test_enqueue_and_claim_due_delivery(tmp_path):
    store = make_store(tmp_path)
    delivery_id = store.enqueue(
        "https://hooks.example.test/events",
        "invoice.created",
        {"invoice_id": "inv_123"},
    )

    claimed = store.claim_due()

    assert claimed is not None
    assert claimed["id"] == delivery_id
    assert claimed["payload"] == {"invoice_id": "inv_123"}
    assert claimed["attempts"] == 1
    assert store.get(delivery_id)["status"] == "delivering"


def test_failed_delivery_is_scheduled_for_retry(tmp_path):
    store = make_store(tmp_path)
    delivery_id = store.enqueue(
        "https://hooks.example.test/events",
        "invoice.created",
        {},
    )
    claimed = store.claim_due()

    store.mark_failed(delivery_id, "temporary failure", claimed["attempts"], 3)

    record = store.get(delivery_id)
    assert record["status"] == "pending"
    assert record["next_attempt_at"] > time.time()
    assert record["last_error"] == "temporary failure"
    assert record["attempt_history"][0]["status"] == "failed"
    assert record["attempt_history"][0]["retry_delay_seconds"] == 1


def test_retry_limit_marks_delivery_failed(tmp_path):
    store = make_store(tmp_path)
    delivery_id = store.enqueue(
        "https://hooks.example.test/events",
        "invoice.created",
        {},
    )
    claimed = store.claim_due()

    store.mark_failed(delivery_id, "permanent failure", claimed["attempts"], 1)

    record = store.get(delivery_id)
    assert record["status"] == "failed"
    assert record["next_attempt_at"] is None
    assert record["attempt_history"][0]["status"] == "failed"
    assert record["attempt_history"][0]["retry_delay_seconds"] is None


def test_idempotency_key_returns_existing_delivery_for_same_request(tmp_path):
    store = make_store(tmp_path)
    request = (
        "https://hooks.example.test/events",
        "invoice.created",
        {"invoice_id": "inv_123"},
    )

    original_id, original_deduplicated = store.enqueue_with_result(
        *request,
        idempotency_key="request-123",
    )
    duplicate_id, deduplicated = store.enqueue_with_result(
        *request,
        idempotency_key="request-123",
    )

    assert original_id == duplicate_id
    assert original_deduplicated is False
    assert deduplicated is True


def test_idempotency_key_rejects_different_request_payload(tmp_path):
    store = make_store(tmp_path)
    store.enqueue_with_result(
        "https://hooks.example.test/events",
        "invoice.created",
        {"invoice_id": "inv_123"},
        idempotency_key="request-123",
    )

    with pytest.raises(IdempotencyConflict):
        store.enqueue_with_result(
            "https://hooks.example.test/events",
            "invoice.created",
            {"invoice_id": "inv_456"},
            idempotency_key="request-123",
        )


def test_failed_delivery_can_be_replayed_as_new_linked_delivery(tmp_path):
    store = make_store(tmp_path)
    original_id = store.enqueue(
        "https://hooks.example.test/events",
        "invoice.created",
        {"invoice_id": "inv_123"},
    )
    claimed = store.claim_due()
    store.mark_failed(
        original_id,
        "permanent failure",
        claimed["attempts"],
        1,
    )

    replay_id = store.replay(original_id)
    replay = store.get(replay_id)

    assert replay_id != original_id
    assert replay["status"] == "pending"
    assert replay["attempts"] == 0
    assert replay["replay_of"] == original_id
    assert store.get(original_id)["status"] == "failed"


def test_only_failed_deliveries_can_be_replayed(tmp_path):
    store = make_store(tmp_path)
    delivery_id = store.enqueue(
        "https://hooks.example.test/events",
        "invoice.created",
        {},
    )

    with pytest.raises(ValueError, match="Only failed"):
        store.replay(delivery_id)
