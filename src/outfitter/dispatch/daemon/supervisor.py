"""App-server supervision: detect a crash (stdout EOF) and recover — restart the
app-server, re-resume persisted lanes, and restart the reactor subscription.

The supervisor swaps ``ctx.client`` in place so the control server, scheduler, and
handlers transparently use the new connection after a restart (recoverable daemon
— the v0 Definition of Done).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Protocol

from outfitter.dispatch.client.errors import ClientError
from outfitter.dispatch.contracts.context import Ctx, LaneClient


class SupervisedClient(LaneClient, Protocol):
    """A LaneClient whose lifecycle the supervisor manages."""

    async def wait_closed(self) -> None: ...

    async def close(self) -> None: ...


class Supervisor:
    def __init__(
        self,
        ctx: Ctx,
        make_client: Callable[[], Awaitable[SupervisedClient]],
        run_reactor: Callable[[], Coroutine[Any, Any, None]],
        *,
        backoff: float = 0.5,
    ) -> None:
        self._ctx = ctx
        self._make_client = make_client
        self._run_reactor = run_reactor
        self._backoff = backoff
        self._stopped = False
        self._client: SupervisedClient | None = None

    async def supervise(self, initial: SupervisedClient) -> None:
        """Run the recover loop, starting from an already-connected client."""
        client = initial
        while True:  # not `while not self._stopped` — stop() flips it during the await below
            self._ctx.client = client
            self._client = client
            await self._resume_lanes(client)
            reactor_task = asyncio.create_task(self._run_reactor())
            await client.wait_closed()  # blocks until app-server dies or we stop
            reactor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reactor_task
            if self._stopped:
                break
            self._ctx.log.warning("app_server.died_restarting", backoff=self._backoff)
            respawned = await self._respawn()
            if respawned is None:  # stopped while trying to respawn
                break
            client = respawned

    async def _respawn(self) -> SupervisedClient | None:
        """Spawn a replacement app-server, retrying with backoff so a transient
        spawn failure becomes a logged retry rather than a silent task death."""
        while not self._stopped:
            await asyncio.sleep(self._backoff)
            try:
                return await self._make_client()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._ctx.log.exception("app_server.spawn_failed", backoff=self._backoff)
        return None

    async def _resume_lanes(self, client: SupervisedClient) -> None:
        """Re-resume persisted lanes on the (re)connected app-server so the daemon
        observes them again after a restart."""
        for lane in await self._ctx.registry.list_lanes():
            try:
                await client.thread_resume(lane.id)
                self._ctx.log.info("lane.resumed", lane=lane.id, source=lane.source)
            except ClientError as exc:
                self._ctx.log.warning("lane.resume_failed", lane=lane.id, error=str(exc))

    async def stop(self) -> None:
        self._stopped = True
        if self._client is not None:
            await self._client.close()  # triggers wait_closed() → the loop exits
