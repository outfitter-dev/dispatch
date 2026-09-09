"""Codex 0.153.4 experimental queue models, independent of writer ownership."""

from pydantic import Field

from .models import UserInput, WireModel


class ThreadQueueAddParams(WireModel):
    thread_id: str
    client_user_message_id: str
    input: list[UserInput]


class QueuedSubmission(WireModel):
    id: str = Field(min_length=1)
    client_user_message_id: str
    input: list[UserInput]


class ThreadQueueAddResult(WireModel):
    queued_submission: QueuedSubmission


class ThreadQueueListParams(WireModel):
    thread_id: str
    cursor: str | None = None
    limit: int | None = None


class ThreadQueuePage(WireModel):
    data: list[QueuedSubmission]
    next_cursor: str | None = None
