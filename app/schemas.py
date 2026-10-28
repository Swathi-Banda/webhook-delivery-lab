from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints
from typing_extensions import Annotated


EventType = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_url: HttpUrl
    event_type: EventType
    payload: dict[str, Any] = Field(max_length=100)


class WebhookAccepted(BaseModel):
    id: str
    status: str
    deduplicated: bool = False
    replay_of: str | None = None


class AttemptStatus(BaseModel):
    attempt: int
    status: str
    started_at: float
    completed_at: float | None
    http_status: int | None
    error: str | None
    retry_delay_seconds: float | None


class DeliveryStatus(BaseModel):
    id: str
    event_type: str
    status: str
    attempts: int
    next_attempt_at: float | None
    last_error: str | None
    created_at: float
    delivered_at: float | None
    replay_of: str | None
    attempt_history: list[AttemptStatus]
