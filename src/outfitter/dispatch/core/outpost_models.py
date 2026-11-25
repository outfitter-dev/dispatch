"""Boundary records for the explicit Outpost response runner."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from outfitter.dispatch.contracts.errors import DispatchError

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,199}$")]


class OutpostRunnerError(DispatchError):
    """The bounded Outpost owner cannot safely continue."""


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    agent: Identifier
    started_at: str = Field(alias="startedAt", min_length=1, max_length=100)
    mcp_session: str = Field(min_length=1, max_length=200)


class Wake(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Identifier
    agent: Identifier
    started_at: str = Field(alias="startedAt", min_length=1, max_length=100)
    thread: Identifier
    message: Identifier
    channel: Identifier


class FeedPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "paused", "ended"]
    wakes: list[Wake]


class RpcEnvelope(BaseModel):
    id: str | int | None = None
    result: dict[str, object] | None = None
    error: dict[str, object] | None = None


class TextContent(BaseModel):
    text: str


class ToolResult(BaseModel):
    content: list[TextContent] = Field(min_length=1)
    isError: bool = False


class AgentIdentity(BaseModel):
    agent: Identifier


class SessionIdentity(BaseModel):
    name: Identifier
    startedAt: str


class ClaudeResult(BaseModel):
    type: Literal["result"]
    session_id: str
    is_error: bool
