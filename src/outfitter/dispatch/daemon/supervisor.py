"""App-server supervision: detect a crash (stdout EOF) and recover — restart the
app-server, restore lane observations, and restart the reactor subscription.

The supervisor swaps ``ctx.client`` in place so the control server, scheduler, and
handlers transparently use the new connection after a restart (recoverable daemon
— the v0 Definition of Done).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Protocol
from uuid import uuid4

from outfitter.dispatch.client.errors import ClientError
from outfitter.dispatch.contracts.context import Ctx, LaneClient
from outfitter.dispatch.core.queue import drain_idle_queues


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
            self._ctx.provider_session_id = uuid4().hex
            self._client = client
            reactor_task = asyncio.create_task(self._run_reactor())
            # Broadcaster subscriptions register eagerly once the reactor task runs.
            # Start them before resume/queue restoration can emit a server request.
            await asyncio.sleep(0)
            await self._restore_lanes(client)
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

    async def _restore_lanes(self, client: SupervisedClient) -> None:
        """Restore persisted lane observation on the (re)connected app-server.

        Owned lanes are resumed so their app-server event stream is reattached.
        Attached lanes stay metadata-only per ADR-0017; restarting the daemon must
        not turn registration into an implicit resume.
        """
        for lane in await self._ctx.registry.list_lanes():
            try:
                if lane.source == "own":
                    await client.thread_resume(lane.id)
                    self._ctx.log.info("lane.resumed", lane=lane.id, source=lane.source)
                else:
                    await client.thread_read(lane.id, include_turns=False)
                    self._ctx.log.info("lane.metadata_read", lane=lane.id, source=lane.source)
            except ClientError as exc:
                self._ctx.log.warning("lane.restore_failed", lane=lane.id, error=str(exc))
        drained = await drain_idle_queues(self._ctx)
        if drained:
            self._ctx.log.info("queue.drained_on_resume", count=drained)

    async def stop(self) -> None:
        self._stopped = True
        if self._client is not None:
            await self._client.close()  # triggers wait_closed() → the loop exits
