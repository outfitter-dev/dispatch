"""Outpost session binding and response feed for the supervised Claude runner."""

from __future__ import annotations

import json
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,199}$")]


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


def validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Outpost endpoint must not contain credentials, query or fragment")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("Outpost endpoint requires HTTPS or a loopback HTTP address")
    return value.rstrip("/")


def rpc_body(response: httpx.Response) -> dict[str, object]:
    response.raise_for_status()
    raw = response.text.strip()
    if not raw.startswith("{"):
        raw = "\n".join(line[5:].lstrip() for line in raw.splitlines() if line.startswith("data:"))
    body: object = json.loads(raw)
    if not isinstance(body, dict) or "error" in body or not isinstance(body.get("result"), dict):
        raise ValueError("Outpost MCP request failed")
    return body


class OutpostFeed:
    def __init__(self, client: httpx.AsyncClient, api_url: str, mcp_url: str, token: str) -> None:
        self.client = client
        self.api_url = validate_endpoint(api_url)
        self.mcp_url = validate_endpoint(mcp_url)
        self.headers = {"authorization": f"Bearer {token}"}
        self.binding: Binding | None = None

    async def bind(self) -> Binding:
        headers = {
            **self.headers,
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "mcp-protocol-version": "2025-06-18",
        }
        initialized = await self.client.post(
            self.mcp_url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "dispatch-outpost", "version": "1"},
                },
            },
        )
        rpc_body(initialized)
        session = initialized.headers.get("mcp-session-id")
        if not session:
            raise ValueError("Outpost connection must enable temporary session agents")
        self.binding = Binding(agent="unresolved", startedAt="unresolved", mcp_session=session)
        headers["mcp-session-id"] = session
        agent = await self._agent(headers)
        response = await self.client.get(f"{self.api_url}/sessions", headers=self.headers)
        response.raise_for_status()
        sessions: object = response.json()
        matches = (
            [item for item in sessions if isinstance(item, dict) and item.get("name") == agent]
            if isinstance(sessions, list)
            else []
        )
        if len(matches) != 1 or not isinstance(matches[0].get("startedAt"), str):
            raise ValueError("Outpost API could not verify the negotiated session generation")
        # Every bound MCP call verifies its immutable token-store generation. A
        # second read rejects a name reused while the API session list was read.
        if await self._agent(headers) != agent:
            raise ValueError("Outpost changed the negotiated session identity")
        self.binding = Binding(agent=agent, startedAt=matches[0]["startedAt"], mcp_session=session)
        return self.binding

    async def _agent(self, headers: dict[str, str]) -> str:
        who = rpc_body(
            await self.client.post(
                self.mcp_url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "outpost_whoami", "arguments": {}},
                },
            )
        )
        result = who["result"]
        if not isinstance(result, dict) or not isinstance(result.get("content"), list):
            raise ValueError("Outpost whoami returned no actor")
        content = result["content"]
        if (
            not content
            or not isinstance(content[0], dict)
            or not isinstance(content[0].get("text"), str)
        ):
            raise ValueError("Outpost whoami returned no actor")
        identity: object = json.loads(content[0]["text"])
        agent: object = identity.get("agent") if isinstance(identity, dict) else None
        if not isinstance(agent, str):
            raise ValueError("Outpost whoami returned no agent")
        return agent

    async def pending(self, binding: Binding) -> list[Wake]:
        response = await self.client.get(
            f"{self.api_url}/board/response-wakes/{binding.agent}",
            headers={**self.headers, "outpost-act-as": binding.agent},
            params={"startedAt": binding.started_at},
        )
        response.raise_for_status()
        body: object = response.json()
        if not isinstance(body, dict) or body.get("status") not in {"active", "paused", "ended"}:
            raise ValueError("Outpost response feed returned no session status")
        if body["status"] == "ended":
            raise ValueError("Outpost recipient has ended")
        if not isinstance(body.get("wakes"), list):
            raise ValueError("Outpost response feed returned no wake list")
        try:
            wakes = [Wake.model_validate(item) for item in body["wakes"]]
        except ValidationError as exc:
            raise ValueError("Outpost response feed contains a malformed wake") from exc
        if body["status"] == "paused":
            if wakes:
                raise ValueError("Outpost exposed wakes for a paused session")
            return []
        if any(
            (item.agent, item.started_at) != (binding.agent, binding.started_at) for item in wakes
        ):
            raise ValueError("Outpost response wake has a stale or mismatched session binding")
        return wakes

    async def ack(self, binding: Binding, wake: Wake) -> None:
        response = await self.client.post(
            f"{self.api_url}/board/response-wakes/{binding.agent}/{wake.id}/ack",
            headers={**self.headers, "outpost-act-as": binding.agent},
            json={"startedAt": binding.started_at},
        )
        response.raise_for_status()

    async def close(self) -> None:
        if self.binding:
            response = await self.client.delete(
                self.mcp_url,
                headers={
                    **self.headers,
                    "mcp-session-id": self.binding.mcp_session,
                    "accept": "application/json, text/event-stream",
                },
            )
            if response.status_code not in {200, 204, 404}:
                response.raise_for_status()
