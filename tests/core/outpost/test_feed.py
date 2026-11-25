from __future__ import annotations

import json

import httpx
import pytest

from outfitter.dispatch.core.outpost_feed import Binding, OutpostFeed


@pytest.mark.asyncio
async def test_binds_only_when_mcp_actor_matches_api_generation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions"):
            return httpx.Response(200, json=[{"name": "claude-1234", "startedAt": "generation-1"}])
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(200, json={"result": {}}, headers={"mcp-session-id": "binding-1"})
        assert request.headers["mcp-session-id"] == "binding-1"
        return httpx.Response(
            200,
            json={
                "result": {
                    "content": [
                        {"text": json.dumps({"agent": "claude-1234"})},
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        binding = await OutpostFeed(
            client, "https://test/api/v1", "https://test/mcp", "token"
        ).bind()
    assert binding.agent == "claude-1234"
    assert binding.started_at == "generation-1"


@pytest.mark.asyncio
async def test_refuses_wake_for_another_generation_before_provider_input() -> None:
    binding = Binding(agent="claude-1234", startedAt="generation-1", mcp_session="binding-1")
    payload = [
        {
            "id": "response-1",
            "agent": binding.agent,
            "startedAt": "generation-2",
            "thread": "ask-1",
            "message": "reply-1",
            "channel": "test",
        }
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "active", "wakes": payload}),
        )
    ) as client:
        feed = OutpostFeed(client, "https://test/api/v1", "https://test/mcp", "token")
        with pytest.raises(ValueError, match="stale or mismatched"):
            await feed.pending(binding)


@pytest.mark.asyncio
async def test_requires_api_authorization_before_provider_launch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions"):
            return httpx.Response(403)
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(200, json={"result": {}}, headers={"mcp-session-id": "binding-1"})
        return httpx.Response(
            200,
            json={
                "result": {
                    "content": [
                        {"text": json.dumps({"agent": "claude-1234"})},
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        feed = OutpostFeed(client, "https://test/api/v1", "https://test/mcp", "token")
        with pytest.raises(httpx.HTTPStatusError):
            await feed.bind()


@pytest.mark.asyncio
async def test_name_reused_during_api_read_invalidates_preflight_binding() -> None:
    replaced = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal replaced
        if request.url.path.endswith("/sessions"):
            replaced = True
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "claude-1234",
                        "startedAt": "replacement-generation",
                    }
                ],
            )
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(200, json={"result": {}}, headers={"mcp-session-id": "binding-1"})
        if replaced:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "result": {
                    "content": [
                        {"text": json.dumps({"agent": "claude-1234"})},
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        feed = OutpostFeed(client, "https://test/api/v1", "https://test/mcp", "token")
        with pytest.raises(httpx.HTTPStatusError):
            await feed.bind()
