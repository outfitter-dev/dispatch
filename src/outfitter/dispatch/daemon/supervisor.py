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
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from outfitter.dispatch.client.errors import AppServerError, ClientError
from outfitter.dispatch.contracts.context import Ctx, LaneClient
from outfitter.dispatch.contracts.errors import CapabilityUnavailableError, DispatchError
from outfitter.dispatch.core.permission_profiles import resolve_permission_profile
from outfitter.dispatch.core.providers import (
    ALL_CODEX_ACTIONS,
    CodexLaneAdapter,
    ProviderAction,
    ProviderAvailability,
    ProviderRouter,
    route_lane,
)
from outfitter.dispatch.core.queue import drain_idle_queues
from outfitter.dispatch.registry.store import DEFAULT_CODEX_BINDING_ID

from .provider_manager import ProviderManager, SharedCoreFailure

_T = TypeVar("_T")


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
        manager: ProviderManager | None = None,
    ) -> None:
        self._ctx = ctx
        self._make_client = make_client
        self._run_reactor = run_reactor
        self._backoff = backoff
        self._manager = manager
        self._stopped = False
        self._client: SupervisedClient | None = None
        self._client_generation: str | None = None

    async def supervise(self, initial: SupervisedClient | None = None) -> None:
        """Run the recover loop, including startup connection failures."""
        client = initial
        while not self._is_stopped():
            if client is None:
                client = await self._respawn()
                if client is None:
                    break
            if self._is_stopped():
                await client.close()
                break
            self._ctx.client = client
            self._ctx.connection_generation = uuid4().hex
            self._client = client
            self._client_generation = self._ctx.connection_generation
            generation = self._ctx.connection_generation
            self._mark_ready(
                CodexLaneAdapter(
                    client,
                    availability=ProviderAvailability(ready=True, generation=generation),
                )
            )
            try:
                await self._run_generation(client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, SharedCoreFailure):
                    self._mark_unavailable(str(exc), generation=generation)
                    raise
                reason = str(exc) or type(exc).__name__
                if not self._is_stopped():
                    self._mark_unavailable(reason, generation=generation)
                    self._ctx.log.exception(
                        "app_server.connection_failed_restarting", backoff=self._backoff
                    )
            else:
                if not self._is_stopped():
                    self._mark_unavailable(
                        "Codex App Server connection closed; reconnecting",
                        generation=generation,
                    )
                    self._ctx.log.warning("app_server.died_restarting", backoff=self._backoff)
            finally:
                await self._close_generation(client, generation)
            if self._is_stopped():
                break
            if self._backoff:
                await asyncio.sleep(self._backoff)
            client = None

    async def _run_generation(self, client: SupervisedClient) -> None:
        """Run recovery and observe both the connection and its reader tasks."""
        reactor_task = asyncio.create_task(self._run_reactor())
        closed_task = asyncio.create_task(client.wait_closed())
        try:
            await self._restore_shared_state(client)
            done, _ = await asyncio.wait(
                (reactor_task, closed_task), return_when=asyncio.FIRST_COMPLETED
            )
            if reactor_task in done:
                error = reactor_task.exception()
                if error is not None:
                    raise error
                if closed_task not in done:
                    raise ClientError("provider reactor exited unexpectedly")
            await closed_task
        finally:
            reactor_task.cancel()
            closed_task.cancel()
            await asyncio.gather(reactor_task, closed_task, return_exceptions=True)

    async def _close_generation(self, client: SupervisedClient, generation: str) -> None:
        """Close only the connection still registered for this exact generation."""
        if self._client is not client or self._client_generation != generation:
            return
        with contextlib.suppress(Exception):
            await client.close()
        self._client = None
        self._client_generation = None
        self._ctx.client = None

    async def _respawn(self) -> SupervisedClient | None:
        """Spawn a replacement app-server, retrying with backoff so a transient
        spawn failure becomes a logged retry rather than a silent task death."""
        while not self._is_stopped():
            try:
                client = await self._make_client()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_unavailable(str(exc), generation=self._ctx.connection_generation or None)
                self._ctx.log.exception("app_server.spawn_failed", backoff=self._backoff)
                await asyncio.sleep(self._backoff)
                continue
            if self._is_stopped():
                with contextlib.suppress(Exception):
                    await client.close()
                return None
            return client
        return None

    def _is_stopped(self) -> bool:
        """Read the flag without assuming another task cannot change it at an await."""
        return self._stopped

    async def _restore_shared_state(self, client: SupervisedClient) -> None:
        """Restore provider observations while preserving the fatal core boundary."""
        await self._registry_call("delivery recovery", self._ctx.registry.recover_deliveries())
        # Broadcaster subscriptions register eagerly once the reactor task runs.
        # Start them before resume/queue restoration can emit a server request.
        await asyncio.sleep(0)
        await self._restore_lanes(client)

    async def _registry_call(self, operation: str, call: Awaitable[_T]) -> _T:
        try:
            return await call
        except asyncio.CancelledError:
            raise
        except SharedCoreFailure:
            raise
        except Exception as exc:
            raise SharedCoreFailure(f"shared registry {operation} failed") from exc

    def _mark_ready(self, adapter: CodexLaneAdapter) -> None:
        if self._manager is not None:
            self._manager.mark_ready("codex", DEFAULT_CODEX_BINDING_ID, adapter)
            return
        router = self._ctx.providers
        if router is None:
            router = ProviderRouter(())
            self._ctx.providers = router
        router.register_adapter(adapter)

    def _mark_unavailable(self, reason: str, *, generation: str | None) -> None:
        if self._manager is not None:
            self._manager.mark_unavailable(
                "codex", DEFAULT_CODEX_BINDING_ID, reason, generation=generation
            )
            return
        router = self._ctx.providers
        if router is None:
            router = ProviderRouter(())
            self._ctx.providers = router
        router.register_unavailable(
            provider="codex",
            binding_id=DEFAULT_CODEX_BINDING_ID,
            supported_actions=ALL_CODEX_ACTIONS,
            reason=reason,
            generation=generation,
        )

    async def _restore_lanes(self, client: SupervisedClient) -> None:
        """Restore persisted lane observation on the (re)connected app-server.

        Owned lanes are resumed so their app-server event stream is reattached.
        Attached lanes stay metadata-only unless an explicit sync previously
        established live observation; plain registration never becomes a resume.
        """
        validated_profiles: dict[tuple[str, str], str] = {}
        lanes = await self._registry_call("lane listing", self._ctx.registry.list_lanes())
        for lane in lanes:
            try:
                route = route_lane(self._ctx, lane, ProviderAction.SYNC)
            except CapabilityUnavailableError:
                self._ctx.log.info(
                    "lane.restore_unsupported_binding",
                    lane=lane.id,
                    provider=lane.provider,
                    binding_id=lane.binding_id,
                )
                continue
            try:
                sync = await self._registry_call(
                    "lane sync read", self._ctx.registry.get_lane_sync(lane.id)
                )
                observed = sync is not None and sync.observation_enabled
                if lane.source == "own" or observed:
                    runtime = await self._registry_call(
                        "runtime settings read",
                        self._ctx.registry.get_lane_runtime_settings(lane.id),
                    )
                    permission_profile = runtime.permission_profile if runtime is not None else None
                    if permission_profile is not None:
                        cwd = lane.cwd or "."
                        key = (cwd, permission_profile)
                        if key not in validated_profiles:
                            validated = await resolve_permission_profile(
                                self._ctx, permission_profile, cwd=cwd
                            )
                            assert validated is not None
                            validated_profiles[key] = validated
                        permission_profile = validated_profiles[key]
                    try:
                        route.recheck()
                        await route.adapter.resume(
                            route.target,
                            permission_profile=permission_profile,
                            exclude_turns=True,
                        )
                    except AppServerError as exc:
                        if exc.code != -32602:
                            raise
                        route.recheck()
                        await route.adapter.resume(
                            route.target, permission_profile=permission_profile
                        )
                    self._ctx.log.info("lane.resumed", lane=lane.id, source=lane.source)
                else:
                    route.recheck()
                    await route.adapter.read(route.target, include_turns=False)
                    self._ctx.log.info("lane.metadata_read", lane=lane.id, source=lane.source)
            except (ClientError, DispatchError) as exc:
                await self._registry_call(
                    "lane status update",
                    self._ctx.registry.update_lane_status(lane.id, "error"),
                )
                self._ctx.log.warning("lane.restore_failed", lane=lane.id, error=str(exc))
        from outfitter.dispatch.core.delivery_reconciliation import (
            reconcile_accepted_after_reconnect,
        )

        await self._registry_call(
            "delivery reconciliation", reconcile_accepted_after_reconnect(self._ctx)
        )
        drained = await self._registry_call("queue recovery", drain_idle_queues(self._ctx))
        if drained:
            self._ctx.log.info("queue.drained_on_resume", count=drained)

    async def stop(self, expected_generation: str | None = None) -> None:
        self._stopped = True
        if expected_generation is not None and expected_generation != self._client_generation:
            return
        if self._client is not None:
            await self._client.close()  # triggers wait_closed() → the loop exits
