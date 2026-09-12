"""Owned Hermes stdio transport and one-generation provider worker.

The worker launches only the configured interpreter and never discovers,
attaches to, resumes, or stops any other Hermes runtime.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import os
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from outfitter.dispatch.client.hermes import (
    HermesClient,
    HermesGatewayCapabilities,
    HermesProtocolError,
    HermesTransport,
    HermesTransportError,
)
from outfitter.dispatch.config import HERMES_GATEWAY_MODULE as HERMES_GATEWAY_MODULE
from outfitter.dispatch.core.providers import (
    ProviderAction,
    ProviderBindingAdapter,
    ProviderDurability,
)

from .provider_manager import ProviderWorker

DEFAULT_HERMES_STDIO_LIMIT = 8 * 1024 * 1024
DEFAULT_HERMES_STDERR_LINES = 50
DEFAULT_HERMES_STDERR_LINE_CHARS = 2_000


class HermesBinding(Protocol):
    """Structural view of the fixed global Hermes binding configuration."""

    @property
    def hermes_home(self) -> Path: ...

    @property
    def source_root(self) -> Path: ...

    @property
    def interpreter(self) -> Path: ...

    @property
    def binding_id(self) -> str: ...

    @property
    def profile(self) -> str: ...


class OwnedHermesTransport(HermesTransport, Protocol):
    generation: str

    async def start(self) -> None: ...

    async def close(self, expected_generation: str | None = None) -> None: ...


class WorkerHermesClient(Protocol):
    async def start(self) -> None: ...

    async def negotiate(self) -> HermesGatewayCapabilities: ...

    async def wait_closed(self) -> None: ...

    async def close(self) -> None: ...


class WorkerHermesAdapter(ProviderBindingAdapter, Protocol):
    async def close_observers(self) -> None: ...


type HermesAdapterFactory = Callable[
    [HermesClient, str, HermesGatewayCapabilities], WorkerHermesAdapter
]
type HermesReadyCallback = Callable[[ProviderBindingAdapter], None]
type HermesTransportFactory = Callable[[HermesBinding], OwnedHermesTransport]
type HermesClientFactory = Callable[[HermesTransport], WorkerHermesClient]


class HermesStdioTransport:
    """One bounded JSONL channel to one owned Hermes gateway child."""

    def __init__(
        self,
        binding: HermesBinding,
        *,
        base_env: Mapping[str, str] | None = None,
        read_limit: int = DEFAULT_HERMES_STDIO_LIMIT,
        stderr_lines: int = DEFAULT_HERMES_STDERR_LINES,
        stderr_line_chars: int = DEFAULT_HERMES_STDERR_LINE_CHARS,
    ) -> None:
        if read_limit < 1 or stderr_lines < 1 or stderr_line_chars < 1:
            raise ValueError("Hermes stdio limits must be positive")
        self._binding = binding
        self._base_env = dict(base_env) if base_env is not None else dict(os.environ)
        self._read_limit = read_limit
        self._stderr_line_chars = stderr_line_chars
        self._stderr_tail: deque[str] = deque(maxlen=stderr_lines)
        self._stderr_task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._stopping = False
        self.generation = uuid4().hex

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc is not None else None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    async def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("Hermes stdio transport already started")
        env = self._environment()
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self._binding.interpreter),
                "-m",
                HERMES_GATEWAY_MODULE,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._binding.source_root),
                env=env,
                limit=self._read_limit,
            )
        except OSError as exc:
            raise HermesTransportError(
                f"failed to spawn configured Hermes interpreter: {exc}"
            ) from exc
        self._proc = proc
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name=f"hermes-stderr:{self.generation}"
        )
        if self._stopping:
            await self._stop_process()

    async def send(self, message: Mapping[str, object]) -> None:
        proc = self._require_process()
        if proc.stdin is None or proc.stdin.is_closing():
            raise HermesTransportError("Hermes gateway stdin is closed")
        encoded = (json.dumps(dict(message), separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > self._read_limit:
            raise HermesProtocolError(
                f"Hermes JSONL request exceeded the {self._read_limit}-byte limit"
            )
        proc.stdin.write(encoded)
        try:
            await proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError) as exc:
            raise HermesTransportError(f"Hermes gateway stdin write failed: {exc}") from exc

    async def receive(self) -> dict[str, object] | None:
        proc = self._require_process()
        if proc.stdout is None:
            raise HermesTransportError("Hermes gateway stdout is unavailable")
        while True:
            try:
                raw = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError) as exc:
                raise HermesProtocolError(
                    f"Hermes JSONL response exceeded the {self._read_limit}-byte limit"
                ) from exc
            if raw == b"":
                return None
            try:
                text = raw.decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise HermesProtocolError("Hermes gateway emitted invalid UTF-8") from exc
            if not text:
                continue
            try:
                parsed: object = json.loads(text)
            except json.JSONDecodeError as exc:
                raise HermesProtocolError(
                    f"Hermes gateway emitted malformed JSON: {text[:200]!r}"
                ) from exc
            if not isinstance(parsed, dict):
                raise HermesProtocolError(
                    f"Hermes gateway message must be an object, got {type(parsed).__name__}"
                )
            return parsed

    async def close(self, expected_generation: str | None = None) -> None:
        if expected_generation is not None and expected_generation != self.generation:
            return
        self._stopping = True
        await self._stop_process()

    def _environment(self) -> dict[str, str]:
        env = dict(self._base_env)
        env.pop("HERMES_TUI_GATEWAY_URL", None)
        env.pop("HERMES_TUI_SIDECAR_URL", None)
        source_root = str(self._binding.source_root)
        inherited_pythonpath = env.get("PYTHONPATH", "").strip()
        env["PYTHONPATH"] = (
            f"{source_root}{os.pathsep}{inherited_pythonpath}"
            if inherited_pythonpath
            else source_root
        )
        env["HERMES_HOME"] = str(self._binding.hermes_home)
        env["HERMES_PYTHON_SRC_ROOT"] = source_root
        env["HERMES_TUI_WS_ORPHAN_REAP_GRACE_S"] = "0"
        return env

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._proc is None:
            raise HermesTransportError("Hermes stdio transport is not started")
        return self._proc

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        current: list[str] = []
        current_length = 0
        while raw := await proc.stderr.read(4096):
            for char in decoder.decode(raw):
                if char == "\n":
                    line = "".join(current).rstrip("\r")
                    if line:
                        self._stderr_tail.append(line)
                    current.clear()
                    current_length = 0
                elif current_length < self._stderr_line_chars:
                    current.append(char)
                    current_length += 1
        for char in decoder.decode(b"", final=True):
            if current_length < self._stderr_line_chars:
                current.append(char)
                current_length += 1
        line = "".join(current).rstrip("\r")
        if line:
            self._stderr_tail.append(line)

    async def _stop_process(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task


class HermesWorkerSupervisor:
    """Start, negotiate, publish, and stop one owned gateway generation."""

    def __init__(
        self,
        binding: HermesBinding,
        *,
        adapter_factory: HermesAdapterFactory,
        mark_ready: HermesReadyCallback,
        transport_factory: HermesTransportFactory | None = None,
        client_factory: HermesClientFactory | None = None,
    ) -> None:
        self.binding = binding
        self._adapter_factory = adapter_factory
        self._mark_ready = mark_ready
        self._transport_factory = transport_factory or HermesStdioTransport
        self._client_factory = client_factory or HermesClient
        self._transport: OwnedHermesTransport | None = None
        self._client: WorkerHermesClient | None = None
        self._generation: str | None = None
        self._stopping = asyncio.Event()

    @property
    def generation(self) -> str | None:
        return self._generation

    async def run(self) -> None:
        if self._transport is not None:
            raise RuntimeError("Hermes worker is already running")
        if self._stopping.is_set():
            return
        transport = self._transport_factory(self.binding)
        self._transport = transport
        client: WorkerHermesClient | None = None
        adapter: WorkerHermesAdapter | None = None
        try:
            await transport.start()
            generation = transport.generation
            self._generation = generation
            if self._stopping.is_set():
                return
            client = self._client_factory(transport)
            self._client = client
            await client.start()
            capabilities = await client.negotiate()
            if self._stopping.is_set():
                return
            adapter = self._adapter_factory(cast(HermesClient, client), generation, capabilities)
            self._mark_ready(adapter)
            await client.wait_closed()
            if not self._stopping.is_set():
                raise HermesTransportError(
                    f"owned Hermes gateway generation {generation} closed unexpectedly"
                )
        finally:
            if client is not None:
                await client.close()
                if adapter is not None:
                    await adapter.close_observers()
            else:
                await transport.close(self._generation)
            if self._transport is transport:
                self._transport = None
                self._client = None

    async def close(self, expected_generation: str | None = None) -> None:
        if expected_generation is not None and expected_generation != self._generation:
            return
        self._stopping.set()
        client = self._client
        transport = self._transport
        if client is not None:
            await client.close()
        elif transport is not None:
            await transport.close(expected_generation)

    def provider_worker(
        self,
        *,
        supported_actions: frozenset[ProviderAction],
        durability: ProviderDurability,
    ) -> ProviderWorker:
        """Project this supervisor into the existing ProviderManager contract."""

        return ProviderWorker(
            provider="hermes",
            binding_id=self.binding.binding_id,
            supported_actions=supported_actions,
            durability=durability,
            owns_process=True,
            run=self.run,
            close=self.close,
            quarantine_on_generation_change=True,
        )
