"""Shared fixtures for client unit tests: an in-memory fake transport so the
client/router can be exercised without spawning a real app-server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping

import pytest_asyncio

from outfitter.dispatch.client.client import AppServerClient

Responder = Callable[[dict[str, object]], list[dict[str, object]]]


class FakeTransport:
    """In-memory ``Transport``. ``auto`` scripts server replies to sent requests;
    ``feed``/``eof`` push notifications and end-of-stream from a test."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.auto: Responder | None = None
        self._inbound: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()

    async def send(self, message: Mapping[str, object]) -> None:
        recorded = dict(message)
        self.sent.append(recorded)
        if self.auto is not None:
            for reply in self.auto(recorded):
                self._inbound.put_nowait(reply)

    async def receive(self) -> dict[str, object] | None:
        return await self._inbound.get()

    async def close(self) -> None:
        self._inbound.put_nowait(None)

    def feed(self, message: dict[str, object]) -> None:
        self._inbound.put_nowait(message)

    def eof(self) -> None:
        self._inbound.put_nowait(None)


@pytest_asyncio.fixture
async def fake() -> AsyncIterator[FakeTransport]:
    yield FakeTransport()


@pytest_asyncio.fixture
async def client(fake: FakeTransport) -> AsyncIterator[tuple[AppServerClient, FakeTransport]]:
    c = AppServerClient(fake)
    await c.start()
    try:
        yield c, fake
    finally:
        await c.close()
