"""Outpost session binding and response feed for the supervised Claude runner."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx
from pydantic import TypeAdapter, ValidationError

from outfitter.dispatch.core.outpost_models import (
    AgentIdentity,
    Binding,
    FeedPage,
    OutpostRunnerError,
    RpcEnvelope,
    SessionIdentity,
    ToolResult,
    Wake,
)


def validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OutpostRunnerError("Outpost endpoint must not contain credentials, query or fragment")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise OutpostRunnerError("Outpost endpoint requires HTTPS or a loopback HTTP address")
    return value


def rpc_body(response: httpx.Response, expected_id: int) -> dict[str, object]:
    response.raise_for_status()
    raw = response.text.strip()
    payloads = (
        [raw]
        if raw.startswith("{")
        else [
            "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
            for block in raw.replace("\r\n", "\n").split("\n\n")
        ]
    )
    for payload in filter(None, payloads):
        body = RpcEnvelope.model_validate_json(payload)
        if body.id == expected_id:
            if body.error is not None or body.result is None:
                raise OutpostRunnerError("Outpost MCP request failed")
            return body.result
    raise OutpostRunnerError("Outpost MCP stream returned no matching result")


class OutpostFeed:
    def __init__(self, client: httpx.AsyncClient, api_url: str, mcp_url: str, token: str) -> None:
        self.client = client
        self.api_url = validate_endpoint(api_url).rstrip("/")
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
        rpc_body(initialized, 1)
        session = initialized.headers.get("mcp-session-id")
        if not session:
            raise OutpostRunnerError("Outpost connection must enable temporary session agents")
        self.binding = Binding(agent="unresolved", startedAt="unresolved", mcp_session=session)
        headers["mcp-session-id"] = session
        notification = await self.client.post(
            self.mcp_url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )
        notification.raise_for_status()
        agent = await self._agent(headers)
        response = await self.client.get(f"{self.api_url}/sessions", headers=self.headers)
        response.raise_for_status()
        sessions = TypeAdapter(list[SessionIdentity]).validate_python(response.json())
        matches = [item for item in sessions if item.name == agent]
        if len(matches) != 1:
            raise OutpostRunnerError(
                "Outpost API could not verify the negotiated session generation"
            )
        # Every bound MCP call verifies its immutable token-store generation. A
        # second read rejects a name reused while the API session list was read.
        if await self._agent(headers) != agent:
            raise OutpostRunnerError("Outpost changed the negotiated session identity")
        self.binding = Binding(agent=agent, startedAt=matches[0].startedAt, mcp_session=session)
        return self.binding

    async def _agent(self, headers: dict[str, str]) -> str:
        response = await self.client.post(
            self.mcp_url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "outpost_whoami", "arguments": {}},
            },
        )
        result = ToolResult.model_validate(rpc_body(response, 2))
        if result.isError:
            raise OutpostRunnerError("Outpost whoami returned an error")
        return AgentIdentity.model_validate_json(result.content[0].text).agent

    async def pending(self, binding: Binding) -> list[Wake]:
        response = await self.client.get(
            f"{self.api_url}/board/response-wakes/{binding.agent}",
            headers={**self.headers, "outpost-act-as": binding.agent},
            params={"startedAt": binding.started_at, "deliveryProtocol": "1"},
        )
        response.raise_for_status()
        try:
            page = FeedPage.model_validate(response.json())
        except ValidationError as exc:
            raise OutpostRunnerError("Outpost response feed contains malformed data") from exc
        if page.status == "ended":
            raise OutpostRunnerError("Outpost recipient has ended")
        if page.status == "paused":
            if page.wakes:
                raise OutpostRunnerError("Outpost exposed wakes for a paused session")
            return []
        wakes = page.wakes
        if any(
            (item.agent, item.started_at) != (binding.agent, binding.started_at) for item in wakes
        ):
            raise OutpostRunnerError(
                "Outpost response wake has a stale or mismatched session binding"
            )
        return wakes

    async def reserve(self, binding: Binding, wake: Wake, attempt: str) -> None:
        response = await self.client.post(
            f"{self.api_url}/board/response-wakes/{binding.agent}/{wake.id}/reserve",
            headers=self.headers,
            json={"startedAt": binding.started_at, "attempt": attempt},
        )
        response.raise_for_status()

    async def delivery(self, binding: Binding, wake: Wake, attempt: str, outcome: str) -> None:
        response = await self.client.post(
            f"{self.api_url}/board/response-wakes/{binding.agent}/{wake.id}/delivery",
            headers=self.headers,
            json={"startedAt": binding.started_at, "attempt": attempt, "outcome": outcome},
        )
        response.raise_for_status()

    async def close(self) -> None:
        if self.binding:
            response = await self.client.delete(
                self.mcp_url,
                headers={
                    **self.headers,
                    "mcp-session-id": self.binding.mcp_session,
                    "mcp-protocol-version": "2025-06-18",
                    "accept": "application/json, text/event-stream",
                },
            )
            if response.status_code not in {200, 204, 404}:
                response.raise_for_status()
