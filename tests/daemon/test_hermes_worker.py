"""Owned Hermes stdio lifecycle without launching a native Hermes process."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from outfitter.dispatch.client.hermes import (
    HermesClient,
    HermesGatewayCapabilities,
    HermesProtocolError,
    HermesTransport,
)
from outfitter.dispatch.core.providers import (
    ProviderAction,
    ProviderAvailability,
    ProviderBindingAdapter,
    ProviderBindingFacts,
    ProviderDurability,
)
from outfitter.dispatch.daemon.hermes_worker import (
    HERMES_GATEWAY_MODULE,
    HermesStdioTransport,
    HermesWorkerSupervisor,
    OwnedHermesTransport,
    WorkerHermesClient,
)


@dataclass(frozen=True)
class _Binding:
    hermes_home: Path
    source_root: Path
    interpreter: Path
    binding_id: str = "hermes-default"
    profile: str = "default"


class _Stdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False

    def is_closing(self) -> bool:
        return self.closed

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None


class _Process:
    def __init__(self, *, read_limit: int = 64 * 1024) -> None:
        self.stdin = _Stdin()
        self.stdout = asyncio.StreamReader(limit=read_limit)
        self.stderr = asyncio.StreamReader()
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode


def _binding(tmp_path: Path) -> _Binding:
    source = tmp_path / "hermes-source"
    home = tmp_path / "hermes-home"
    interpreter = tmp_path / "venv" / "bin" / "python"
    source.mkdir()
    home.mkdir()
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("synthetic")
    return _Binding(home, source, interpreter)


async def test_stdio_spawn_uses_fixed_gateway_and_source_first_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _binding(tmp_path)
    process = _Process()
    captured: dict[str, Any] = {}

    async def fake_spawn(*argv: str, **kwargs: Any) -> _Process:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    transport = HermesStdioTransport(
        binding,
        base_env={
            "PYTHONPATH": "/existing",
            "KEEP": "yes",
            "HERMES_TUI_GATEWAY_URL": "ws://existing",
            "HERMES_TUI_SIDECAR_URL": "http://existing",
        },
        read_limit=1234,
    )
    await transport.start()
    try:
        assert captured["argv"] == (str(binding.interpreter), "-m", HERMES_GATEWAY_MODULE)
        kwargs = captured["kwargs"]
        assert kwargs["cwd"] == str(binding.source_root)
        assert kwargs["limit"] == 1234
        assert kwargs["stdin"] is asyncio.subprocess.PIPE
        assert kwargs["stdout"] is asyncio.subprocess.PIPE
        assert kwargs["stderr"] is asyncio.subprocess.PIPE
        env = kwargs["env"]
        assert env["PYTHONPATH"] == f"{binding.source_root}:/existing"
        assert env["HERMES_HOME"] == str(binding.hermes_home)
        assert env["HERMES_PYTHON_SRC_ROOT"] == str(binding.source_root)
        assert env["HERMES_TUI_WS_ORPHAN_REAP_GRACE_S"] == "0"
        assert env["KEEP"] == "yes"
        assert "HERMES_TUI_GATEWAY_URL" not in env
        assert "HERMES_TUI_SIDECAR_URL" not in env
    finally:
        await transport.close()

    assert process.terminated is True


async def test_stdio_round_trip_and_bounded_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _binding(tmp_path)
    process = _Process()

    async def fake_spawn(*_argv: str, **_kwargs: Any) -> _Process:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    transport = HermesStdioTransport(binding, stderr_lines=2, stderr_line_chars=5)
    await transport.start()
    try:
        await transport.send({"id": 1, "method": "ping", "params": {}})
        assert process.stdin.writes == [b'{"id":1,"method":"ping","params":{}}\n']

        process.stdout.feed_data(b'{"id":1,"result":{"ok":true}}\n')
        assert await transport.receive() == {"id": 1, "result": {"ok": True}}

        process.stderr.feed_data(b"x" * 100_000 + b"\nsecond-long\nthird-long\n")
        process.stderr.feed_eof()
        await asyncio.sleep(0)
        assert transport.stderr_tail() == "secon\nthird"
    finally:
        await transport.close()


async def test_stdio_enforces_request_and_response_line_bounds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _binding(tmp_path)
    process = _Process(read_limit=32)

    async def fake_spawn(*_argv: str, **_kwargs: Any) -> _Process:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    transport = HermesStdioTransport(binding, read_limit=32)
    await transport.start()
    try:
        with pytest.raises(HermesProtocolError, match="request exceeded"):
            await transport.send({"text": "x" * 100})
        process.stdout.feed_data(b'{"result":{"text":"' + b"x" * 100 + b'"}}\n')
        with pytest.raises(HermesProtocolError, match="response exceeded"):
            await transport.receive()
    finally:
        await transport.close()


async def test_transport_close_is_fenced_to_exact_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _Process()

    async def fake_spawn(*_argv: str, **_kwargs: Any) -> _Process:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    transport = HermesStdioTransport(_binding(tmp_path))
    await transport.start()

    await transport.close("stale-generation")
    assert process.terminated is False
    await transport.close(transport.generation)
    assert process.terminated is True


class _FakeOwnedTransport:
    def __init__(self, _binding: object) -> None:
        self.generation = "generation-1"
        self.started = False
        self.closed: list[str | None] = []

    async def start(self) -> None:
        self.started = True

    async def send(self, _message: Mapping[str, object]) -> None:
        return None

    async def receive(self) -> dict[str, object] | None:
        await asyncio.Event().wait()
        return None

    async def close(self, expected_generation: str | None = None) -> None:
        self.closed.append(expected_generation)


class _FakeClient:
    def __init__(self, _transport: object) -> None:
        self.started = False
        self.closed = asyncio.Event()

    async def start(self) -> None:
        self.started = True

    async def negotiate(self) -> HermesGatewayCapabilities:
        return HermesGatewayCapabilities(replay_epoch="epoch-1")

    async def wait_closed(self) -> None:
        await self.closed.wait()

    async def close(self) -> None:
        self.closed.set()


class _Adapter:
    def __init__(self, generation: str) -> None:
        self.observers_closed = asyncio.Event()
        self.facts = ProviderBindingFacts(
            provider="hermes",
            binding_id="hermes-default",
            supported_actions=frozenset({ProviderAction.LAUNCH, ProviderAction.SEND}),
            availability=ProviderAvailability(ready=True, generation=generation),
            durability=ProviderDurability(local_reservation=True, native_evidence=True),
        )

    async def close_observers(self) -> None:
        self.observers_closed.set()


async def test_worker_negotiates_before_ready_and_projects_provider_worker(tmp_path: Path) -> None:
    transport = _FakeOwnedTransport(object())
    client = _FakeClient(object())
    ready: list[ProviderBindingAdapter] = []
    adapter = _Adapter("generation-1")

    def transport_factory(_binding: object) -> OwnedHermesTransport:
        return transport

    def client_factory(_transport: object) -> WorkerHermesClient:
        return client

    def adapter_factory(
        concrete: HermesClient,
        generation: str,
        capabilities: HermesGatewayCapabilities,
    ) -> _Adapter:
        # The production factory receives the concrete client. This synthetic
        # worker client exercises lifecycle without provider I/O.
        assert cast(object, concrete) is client
        assert capabilities.replay_epoch == "epoch-1"
        assert generation == "generation-1"
        return adapter

    supervisor = HermesWorkerSupervisor(
        _binding(tmp_path),
        adapter_factory=adapter_factory,
        mark_ready=ready.append,
        transport_factory=cast(Any, transport_factory),
        client_factory=cast(Any, client_factory),
    )
    worker = supervisor.provider_worker(
        supported_actions=frozenset({ProviderAction.LAUNCH, ProviderAction.SEND}),
        durability=ProviderDurability(local_reservation=True, native_evidence=True),
    )
    run = asyncio.create_task(supervisor.run())
    async with asyncio.timeout(1):
        while not ready:
            await asyncio.sleep(0)

    assert transport.started is True
    assert client.started is True
    assert ready[0].facts.availability.generation == "generation-1"
    assert worker.provider == "hermes"
    assert worker.binding_id == "hermes-default"
    assert worker.owns_process is True
    assert worker.quarantine_on_generation_change is True

    await supervisor.close("stale-generation")
    assert client.closed.is_set() is False
    await supervisor.close("generation-1")
    await run
    assert adapter.observers_closed.is_set()


async def test_worker_closes_started_transport_when_negotiation_fails(tmp_path: Path) -> None:
    transport = _FakeOwnedTransport(object())

    class FailedClient(_FakeClient):
        async def negotiate(self) -> HermesGatewayCapabilities:
            raise HermesProtocolError("missing capabilities")

    client = FailedClient(object())
    supervisor = HermesWorkerSupervisor(
        _binding(tmp_path),
        adapter_factory=lambda *_args: _Adapter("never"),
        mark_ready=lambda _adapter: pytest.fail("worker must not become ready"),
        transport_factory=cast(Any, lambda _binding: transport),
        client_factory=cast(Any, lambda _transport: client),
    )

    with pytest.raises(HermesProtocolError, match="missing capabilities"):
        await supervisor.run()
    assert client.closed.is_set()


async def test_worker_stopped_during_spawn_never_constructs_or_publishes_client(
    tmp_path: Path,
) -> None:
    class BlockedTransport(_FakeOwnedTransport):
        def __init__(self) -> None:
            super().__init__(object())
            self.starting = asyncio.Event()
            self.release = asyncio.Event()

        async def start(self) -> None:
            self.starting.set()
            await self.release.wait()
            self.started = True

    transport = BlockedTransport()
    ready: list[ProviderBindingAdapter] = []

    def fail_client_factory(_transport: HermesTransport) -> WorkerHermesClient:
        pytest.fail("a stopped worker must not construct its Hermes client")

    supervisor = HermesWorkerSupervisor(
        _binding(tmp_path),
        adapter_factory=lambda *_args: _Adapter("never"),
        mark_ready=ready.append,
        transport_factory=cast(Any, lambda _binding: transport),
        client_factory=fail_client_factory,
    )
    run = asyncio.create_task(supervisor.run())
    await transport.starting.wait()
    await supervisor.close()
    transport.release.set()
    await run

    assert ready == []
    assert transport.closed == [None, "generation-1"]
