import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


class IdempotencyConflict(ValueError):
    pass


class WebhookStore:
    def __init__(self, database_path: str):
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS webhook_deliveries (
                    id TEXT PRIMARY KEY,
                    target_url TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    delivered_at REAL,
                    idempotency_key TEXT,
                    replay_of TEXT REFERENCES webhook_deliveries(id)
                )
                """
            )
            delivery_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(webhook_deliveries)"
                ).fetchall()
            }
            if "idempotency_key" not in delivery_columns:
                connection.execute(
                    "ALTER TABLE webhook_deliveries ADD COLUMN idempotency_key TEXT"
                )
            if "replay_of" not in delivery_columns:
                connection.execute(
                    "ALTER TABLE webhook_deliveries ADD COLUMN replay_of TEXT"
                )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_webhook_idempotency
                ON webhook_deliveries(idempotency_key)
                WHERE idempotency_key IS NOT NULL
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_webhook_due
                ON webhook_deliveries(status, next_attempt_at, created_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS webhook_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    delivery_id TEXT NOT NULL REFERENCES webhook_deliveries(id),
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    completed_at REAL,
                    http_status INTEGER,
                    error TEXT,
                    retry_delay_seconds REAL,
                    UNIQUE(delivery_id, attempt)
                )
                """
            )
            connection.execute(
                """
                UPDATE webhook_attempts
                SET status = 'failed', completed_at = ?, error = 'Worker interrupted'
                WHERE status = 'delivering'
                """,
                (time.time(),),
            )
            connection.execute(
                """
                UPDATE webhook_deliveries
                SET status = 'pending', next_attempt_at = ?
                WHERE status = 'delivering'
                """,
                (time.time(),),
            )

    def enqueue(
        self,
        target_url: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> str:
        return self.enqueue_with_result(target_url, event_type, payload)[0]

    def enqueue_with_result(
        self,
        target_url: str,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[str, bool]:
        serialized_payload = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        )
        delivery_id = str(uuid.uuid4())
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = connection.execute(
                    """
                    SELECT id, target_url, event_type, payload
                    FROM webhook_deliveries
                    WHERE idempotency_key = ?
                    """,
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    same_request = (
                        existing["target_url"] == target_url
                        and existing["event_type"] == event_type
                        and existing["payload"] == serialized_payload
                    )
                    if not same_request:
                        raise IdempotencyConflict(
                            "Idempotency key was already used for a different request"
                        )
                    return existing["id"], True

            connection.execute(
                """
                INSERT INTO webhook_deliveries (
                    id, target_url, event_type, payload, status, next_attempt_at, created_at,
                    idempotency_key
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    delivery_id,
                    target_url,
                    event_type,
                    serialized_payload,
                    now,
                    now,
                    idempotency_key,
                ),
            )
        return delivery_id, False

    def replay(self, delivery_id: str) -> str:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            original = connection.execute(
                """
                SELECT target_url, event_type, payload, status
                FROM webhook_deliveries
                WHERE id = ?
                """,
                (delivery_id,),
            ).fetchone()
            if original is None:
                raise KeyError("Webhook delivery not found")
            if original["status"] != "failed":
                raise ValueError("Only failed deliveries can be replayed")

            replay_id = str(uuid.uuid4())
            now = time.time()
            connection.execute(
                """
                INSERT INTO webhook_deliveries (
                    id, target_url, event_type, payload, status, next_attempt_at,
                    created_at, replay_of
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    replay_id,
                    original["target_url"],
                    original["event_type"],
                    original["payload"],
                    now,
                    now,
                    delivery_id,
                ),
            )
        return replay_id

    def claim_due(self) -> dict[str, Any] | None:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id, target_url, event_type, payload, attempts
                FROM webhook_deliveries
                WHERE status = 'pending' AND next_attempt_at <= ?
                ORDER BY created_at
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None

            updated = connection.execute(
                """
                UPDATE webhook_deliveries
                SET status = 'delivering', attempts = attempts + 1
                WHERE id = ? AND status = 'pending'
                """,
                (row["id"],),
            )
            if updated.rowcount != 1:
                return None

            result = dict(row)
            result["payload"] = json.loads(result["payload"])
            result["attempts"] += 1
            connection.execute(
                """
                INSERT INTO webhook_attempts (delivery_id, attempt, status, started_at)
                VALUES (?, ?, 'delivering', ?)
                """,
                (result["id"], result["attempts"], now),
            )
            return result

    def mark_delivered(self, delivery_id: str, http_status: int) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE webhook_deliveries
                SET status = 'delivered', delivered_at = ?, next_attempt_at = NULL,
                    last_error = NULL
                WHERE id = ? AND status = 'delivering'
                """,
                (now, delivery_id),
            )
            connection.execute(
                """
                UPDATE webhook_attempts
                SET status = 'delivered', completed_at = ?, http_status = ?
                WHERE delivery_id = ? AND status = 'delivering'
                """,
                (now, http_status, delivery_id),
            )

    def mark_failed(
        self,
        delivery_id: str,
        error: str,
        attempts: int,
        max_attempts: int,
        http_status: int | None = None,
    ) -> None:
        terminal = attempts >= max_attempts
        retry_delay_seconds = None if terminal else min(2 ** (attempts - 1), 300)
        now = time.time()
        next_attempt_at = (
            None if terminal else now + retry_delay_seconds
        )
        status = "failed" if terminal else "pending"
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE webhook_deliveries
                SET status = ?, next_attempt_at = ?, last_error = ?
                WHERE id = ? AND status = 'delivering'
                """,
                (status, next_attempt_at, error[:500], delivery_id),
            )
            connection.execute(
                """
                UPDATE webhook_attempts
                SET status = 'failed', completed_at = ?, http_status = ?, error = ?,
                    retry_delay_seconds = ?
                WHERE delivery_id = ? AND status = 'delivering'
                """,
                (now, http_status, error[:500], retry_delay_seconds, delivery_id),
            )

    def get(self, delivery_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, target_url, event_type, status, attempts, next_attempt_at,
                       last_error, created_at, delivered_at, replay_of
                FROM webhook_deliveries
                WHERE id = ?
                """,
                (delivery_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            attempts = connection.execute(
                """
                SELECT attempt, status, started_at, completed_at, http_status, error,
                       retry_delay_seconds
                FROM webhook_attempts
                WHERE delivery_id = ?
                ORDER BY attempt
                """,
                (delivery_id,),
            ).fetchall()
            result["attempt_history"] = [dict(attempt) for attempt in attempts]
        return result
