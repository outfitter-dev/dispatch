"""Independent lifecycle boundary for configured provider workers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import structlog

from outfitter.dispatch.core.providers import (
    ProviderAction,
    ProviderBindingAdapter,
    ProviderDurability,
    ProviderRouter,
)

ProviderWorkerState = Literal[
    "configured", "starting", "ready", "unavailable", "stopped", "quarantined"
]


class SharedCoreFailure(RuntimeError):
    """A registry/control failure that must terminate the daemon."""


@dataclass(frozen=True)
class ProviderWorker:
    """One configured binding with connection and exact-lifecycle callbacks."""

    provider: str
    binding_id: str
    supported_actions: frozenset[ProviderAction]
    durability: ProviderDurability
    owns_process: bool
    run: Callable[[], Awaitable[None]]
    close: Callable[[str | None], Awaitable[None]]
    quarantine_on_generation_change: bool = False


@dataclass(frozen=True)
class ProviderWorkerSnapshot:
    provider: str
    binding_id: str
    state: ProviderWorkerState
    reason: str | None
    last_error: str | None
    observed_at: str
    connection_generation: str | None
    owns_process: bool


class ProviderManager:
    """Own provider worker tasks without coupling their failure domains."""

    def __init__(
        self,
        router: ProviderRouter,
        log: structlog.stdlib.BoundLogger,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._router = router
        self._log = log
        self._now = now or (lambda: datetime.now(UTC))
        self._workers: dict[tuple[str, str], ProviderWorker] = {}
        self._snapshots: dict[tuple[str, str], ProviderWorkerSnapshot] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._late_close_tasks: set[asyncio.Future[None]] = set()
        self._stopping = False
        self._fatal = asyncio.Event()
        self._fatal_error: SharedCoreFailure | None = None

    def add(self, worker: ProviderWorker) -> None:
        key = (worker.provider, worker.binding_id)
        if key in self._workers:
            raise ValueError(
                f"provider worker {worker.provider}:{worker.binding_id} already exists"
            )
        self._workers[key] = worker
        self._set_state(worker, "configured", reason="provider worker is configured")

    def start(self) -> None:
        if self._tasks:
            raise RuntimeError("provider workers already started")
        self._stopping = False
        self._fatal.clear()
        self._fatal_error = None
        for key, worker in self._workers.items():
            self._tasks[key] = asyncio.create_task(
                self._run(worker),
                name=f"provider:{worker.provider}:{worker.binding_id}",
            )

    async def _run(self, worker: ProviderWorker) -> None:
        self._set_state(worker, "starting", reason="provider connection is starting")
        try:
            await worker.run()
        except asyncio.CancelledError:
            raise
        except SharedCoreFailure as exc:
            reason = str(exc) or type(exc).__name__
            self.mark_unavailable(worker.provider, worker.binding_id, reason)
            if self._fatal_error is None:
                self._fatal_error = exc
                self._fatal.set()
            self._log.exception(
                "provider.worker_shared_core_failed",
                provider=worker.provider,
                binding_id=worker.binding_id,
            )
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            self.mark_unavailable(worker.provider, worker.binding_id, reason)
            self._log.exception(
                "provider.worker_failed",
                provider=worker.provider,
                binding_id=worker.binding_id,
            )
        else:
            if not self._stopping:
                self.mark_unavailable(
                    worker.provider,
                    worker.binding_id,
                    "provider worker exited unexpectedly",
                )

    async def wait_fatal(self) -> None:
        """Raise when a worker reports a shared registry/control failure."""
        await self._fatal.wait()
        assert self._fatal_error is not None
        raise self._fatal_error

    def mark_ready(self, provider: str, binding_id: str, adapter: ProviderBindingAdapter) -> None:
        worker = self._worker(provider, binding_id)
        if (adapter.facts.provider, adapter.facts.binding_id) != (provider, binding_id):
            raise ValueError("provider adapter identity does not match its configured worker")
        generation = adapter.facts.availability.generation
        if not adapter.facts.availability.ready or generation is None:
            raise ValueError("ready provider adapter requires a connection generation")
        if self._stopping:
            self._set_state(
                worker,
                "stopped",
                reason="provider became ready after shutdown began",
                generation=generation,
            )
            close_task: asyncio.Future[None] = asyncio.ensure_future(worker.close(generation))
            self._late_close_tasks.add(close_task)
            return
        previous = self._snapshots[(provider, binding_id)]
        if (
            worker.quarantine_on_generation_change
            and previous.connection_generation is not None
            and previous.connection_generation != generation
        ):
            self._set_state(
                worker,
                "quarantined",
                reason=(
                    "provider process generation changed; existing sessions require "
                    "explicit safe resumption"
                ),
                generation=generation,
            )
            return
        self._router.register_adapter(adapter)
        self._set_state(worker, "ready", generation=generation)

    def mark_unavailable(
        self,
        provider: str,
        binding_id: str,
        reason: str,
        *,
        generation: str | None = None,
        quarantined: bool = False,
    ) -> None:
        worker = self._worker(provider, binding_id)
        previous = self._snapshots[(provider, binding_id)]
        self._set_state(
            worker,
            "quarantined" if quarantined else "unavailable",
            reason=reason,
            last_error=reason,
            generation=(generation if generation is not None else previous.connection_generation),
        )

    def snapshot(self, provider: str, binding_id: str) -> ProviderWorkerSnapshot:
        return self._snapshots[(provider, binding_id)]

    def snapshots(self) -> tuple[ProviderWorkerSnapshot, ...]:
        return tuple(self._snapshots[key] for key in sorted(self._snapshots))

    async def stop(self) -> None:
        self._stopping = True
        results = await asyncio.gather(
            *(
                worker.close(
                    self.snapshot(worker.provider, worker.binding_id).connection_generation
                )
                for worker in self._workers.values()
            ),
            return_exceptions=True,
        )
        for worker, result in zip(self._workers.values(), results, strict=True):
            if isinstance(result, BaseException):
                self._log.error(
                    "provider.close_failed",
                    provider=worker.provider,
                    binding_id=worker.binding_id,
                    error=str(result),
                )
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        if self._late_close_tasks:
            late_results = await asyncio.gather(*self._late_close_tasks, return_exceptions=True)
            for result in late_results:
                if isinstance(result, BaseException):
                    self._log.error("provider.late_close_failed", error=str(result))
        for worker in self._workers.values():
            previous = self.snapshot(worker.provider, worker.binding_id)
            self._set_state(
                worker,
                "stopped",
                reason="provider worker stopped",
                generation=previous.connection_generation,
            )

    def _worker(self, provider: str, binding_id: str) -> ProviderWorker:
        try:
            return self._workers[(provider, binding_id)]
        except KeyError as exc:
            raise KeyError(f"unknown provider worker {provider}:{binding_id}") from exc

    def _set_state(
        self,
        worker: ProviderWorker,
        state: ProviderWorkerState,
        *,
        reason: str | None = None,
        last_error: str | None = None,
        generation: str | None = None,
    ) -> None:
        self._snapshots[(worker.provider, worker.binding_id)] = ProviderWorkerSnapshot(
            provider=worker.provider,
            binding_id=worker.binding_id,
            state=state,
            reason=reason,
            last_error=last_error,
            observed_at=self._now().isoformat(),
            connection_generation=generation,
            owns_process=worker.owns_process,
        )
        if state != "ready":
            self._router.register_unavailable(
                provider=worker.provider,
                binding_id=worker.binding_id,
                supported_actions=worker.supported_actions,
                reason=(
                    reason or f"provider binding {worker.provider}:{worker.binding_id} is {state}"
                ),
                generation=generation,
                durability=worker.durability,
            )
