from __future__ import annotations

import json

import httpx
import pytest

from outfitter.dispatch.core.outpost_feed import OutpostFeed
from outfitter.dispatch.core.outpost_models import Binding, OutpostRunnerError


@pytest.mark.asyncio
async def test_binds_only_when_mcp_actor_matches_api_generation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions"):
            return httpx.Response(200, json=[{"name": "claude-1234", "startedAt": "generation-1"}])
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(
                200, json={"id": 1, "result": {}}, headers={"mcp-session-id": "binding-1"}
            )
        assert request.headers["mcp-session-id"] == "binding-1"
        return httpx.Response(
            200,
            json={
                "id": 2,
                "result": {
                    "content": [
                        {"text": json.dumps({"agent": "claude-1234"})},
                    ]
                },
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
        with pytest.raises(OutpostRunnerError, match="stale or mismatched"):
            await feed.pending(binding)


@pytest.mark.asyncio
async def test_requires_api_authorization_before_provider_launch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions"):
            return httpx.Response(403)
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(
                200, json={"id": 1, "result": {}}, headers={"mcp-session-id": "binding-1"}
            )
        return httpx.Response(
            200,
            json={
                "id": 2,
                "result": {
                    "content": [
                        {"text": json.dumps({"agent": "claude-1234"})},
                    ]
                },
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
            return httpx.Response(
                200, json={"id": 1, "result": {}}, headers={"mcp-session-id": "binding-1"}
            )
        if replaced:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "id": 2,
                "result": {
                    "content": [
                        {"text": json.dumps({"agent": "claude-1234"})},
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        feed = OutpostFeed(client, "https://test/api/v1", "https://test/mcp", "token")
        with pytest.raises(httpx.HTTPStatusError):
            await feed.bind()


def test_sse_notification_before_result_does_not_hide_rpc_response() -> None:
    from outfitter.dispatch.core.outpost_feed import rpc_body

    response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://test/mcp"),
        text=(
            'data: {"jsonrpc":"2.0","method":"notifications/message","params":{}}\n\n'
            'data: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n'
        ),
    )
    assert rpc_body(response, 2) == {"content": []}


@pytest.mark.asyncio
async def test_complete_handshake_preserves_exact_mcp_url_and_cleanup_protocol() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            assert request.url.path == "/mcp/"
            assert request.headers["mcp-protocol-version"] == "2025-06-18"
            return httpx.Response(204)
        if request.url.path.endswith("/sessions"):
            return httpx.Response(200, json=[{"name": "claude-1234", "startedAt": "generation-1"}])
        assert request.url.path == "/mcp/"
        body = json.loads(request.content)
        methods.append(body["method"])
        if body["method"] == "initialize":
            return httpx.Response(
                200, json={"id": 1, "result": {}}, headers={"mcp-session-id": "binding-1"}
            )
        if body["method"] == "notifications/initialized":
            assert request.headers["mcp-session-id"] == "binding-1"
            return httpx.Response(202)
        assert methods[:2] == ["initialize", "notifications/initialized"]
        return httpx.Response(
            200,
            json={
                "id": 2,
                "result": {
                    "content": [
                        {"text": json.dumps({"agent": "claude-1234"})},
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        feed = OutpostFeed(client, "https://test/api/v1/", "https://test/mcp/", "token")
        assert (await feed.bind()).agent == "claude-1234"
        await feed.close()
