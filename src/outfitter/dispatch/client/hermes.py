"""Typed Hermes gateway JSON-RPC primitives over an injected transport.

This module owns no process lifecycle.  A daemon worker supplies the transport;
unit tests use an in-memory transport.  The client deliberately exposes only the
small native contract needed by the first Hermes provider slice.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, Self


class HermesTransport(Protocol):
    """One already-connected, newline-framed Hermes JSON-RPC transport."""

    async def send(self, message: Mapping[str, object]) -> None: ...

    async def receive(self) -> dict[str, object] | None: ...

    async def close(self) -> None: ...


class HermesClientError(Exception):
    """Base error for the standalone Hermes wire client."""


class HermesTransportError(HermesClientError):
    """The injected transport failed or closed."""


class HermesProtocolError(HermesClientError):
    """Hermes returned a malformed or unsupported wire shape."""


class HermesCapabilityError(HermesClientError):
    """The connected gateway does not advertise the required contract."""


class HermesRpcError(HermesClientError):
    """Hermes returned a JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: object | None = None) -> None:
        super().__init__(f"Hermes gateway error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True)
class HermesGatewayCapabilities:
    replay_epoch: str
    prompt_submit_if_idle_v1: Literal[True] = True
    prompt_turn_correlation_v1: Literal[True] = True


@dataclass(frozen=True)
class HermesSessionCreated:
    runtime_session_id: str
    stored_session_id: str
    effective_cwd: str
    status: Literal["created"] = "created"


@dataclass(frozen=True)
class HermesSessionCreationUnknown:
    error: str
    status: Literal["unknown"] = "unknown"


HermesSessionCreationResult = HermesSessionCreated | HermesSessionCreationUnknown


@dataclass(frozen=True)
class HermesEvent:
    """One request-correlated native event from the current gateway generation."""

    type: str
    runtime_session_id: str
    turn_id: str
    payload: dict[str, object]


HermesAttentionCategory = Literal[
    "human_or_sensitive_input", "native_client_capability_unavailable"
]


@dataclass(frozen=True)
class HermesAttentionEvent:
    """One source-proven native blocking request or matching expiry event."""

    type: str
    family: str
    runtime_session_id: str
    request_id: str | None
    category: HermesAttentionCategory
    expired: bool


type HermesAttentionHandler = Callable[[HermesAttentionEvent], None]
type HermesActivityHandler = Callable[[HermesEvent], None]

_HUMAN_ATTENTION_FAMILIES = frozenset(
    {
        "clarify",
        "approval",
        "mcp.setup",
        "sudo",
        "secret",
        "vault.unlock",
        "vault.save_login",
        "vault.code",
    }
)
_NATIVE_CAPABILITY_ATTENTION_FAMILIES = frozenset(
    {"terminal.read", "preview.read", "preview.act", "window.read", "tour"}
)
_EXPIRING_ATTENTION_FAMILIES = (
    _HUMAN_ATTENTION_FAMILIES | _NATIVE_CAPABILITY_ATTENTION_FAMILIES
) - {"approval"}


class HermesTurnStream:
    """Buffered pre-ACK events followed by live events for one native turn."""

    def __init__(
        self,
        buffered: tuple[HermesEvent, ...],
        *,
        partial: bool,
        max_live_events: int,
        on_close: Callable[[], None],
    ) -> None:
        self.buffered = buffered
        self.partial = partial
        self.uncertainty_reason = (
            "Hermes events exceeded the bounded pre-ACK buffer" if partial else None
        )
        self._queue: asyncio.Queue[HermesEvent | None] = asyncio.Queue(max_live_events)
        self._closed = False
        self._on_close = on_close

    def publish(self, event: HermesEvent) -> None:
        if self._closed:
            return
        if self._queue.full():
            self.partial = True
            self.uncertainty_reason = "Hermes events exceeded the bounded live-event buffer"
            self._queue.get_nowait()
        self._queue.put_nowait(event)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._queue.full():
            self._queue.get_nowait()
            self.partial = True
            self.uncertainty_reason = "Hermes events exceeded the bounded live-event buffer"
        self._queue.put_nowait(None)
        self._on_close()

    async def aclose(self) -> None:
        self.close()

    def __aiter__(self) -> AsyncIterator[HermesEvent]:
        return self

    async def __anext__(self) -> HermesEvent:
        event = await self._queue.get()
        if event is None:
            raise StopAsyncIteration
        return event


@dataclass(frozen=True)
class HermesSubmissionAccepted:
    turn_id: str
    stream: HermesTurnStream
    status: Literal["accepted"] = "accepted"

    @property
    def partial(self) -> bool:
        return self.stream.partial

    @property
    def uncertainty_reason(self) -> str | None:
        return self.stream.uncertainty_reason


@dataclass(frozen=True)
class HermesSubmissionRejected:
    error: str
    code: int = 4091
    status: Literal["rejected"] = "rejected"


@dataclass(frozen=True)
class HermesSubmissionUnknown:
    error: str
    status: Literal["unknown"] = "unknown"


HermesSubmissionResult = (
    HermesSubmissionAccepted | HermesSubmissionRejected | HermesSubmissionUnknown
)


type _EventKey = tuple[str, str]


class _PreAckEventBuffer:
    """Globally bounded unmatched events with conservative overflow tombstones."""

    def __init__(self, *, max_events: int, max_bytes: int) -> None:
        if max_events < 1 or max_bytes < 1:
            raise ValueError("Hermes event buffer limits must be positive")
        self._max_events = max_events
        self._max_bytes = max_bytes
        self._events: deque[tuple[_EventKey, HermesEvent, int]] = deque()
        self._bytes = 0
        self._partial_keys: set[_EventKey] = set()
        self._global_partial = False

    def add(self, event: HermesEvent) -> None:
        key = (event.runtime_session_id, event.turn_id)
        size = len(json.dumps(event.payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        self._events.append((key, event, size))
        self._bytes += size
        while len(self._events) > self._max_events or self._bytes > self._max_bytes:
            dropped_key, _, dropped_size = self._events.popleft()
            self._bytes -= dropped_size
            self._mark_partial(dropped_key)

    def claim(self, key: _EventKey) -> tuple[tuple[HermesEvent, ...], bool]:
        matched: list[HermesEvent] = []
        retained: deque[tuple[_EventKey, HermesEvent, int]] = deque()
        retained_bytes = 0
        while self._events:
            event_key, event, size = self._events.popleft()
            if event_key == key:
                matched.append(event)
            else:
                retained.append((event_key, event, size))
                retained_bytes += size
        self._events = retained
        self._bytes = retained_bytes
        partial = self._global_partial or key in self._partial_keys
        self._partial_keys.discard(key)
        return tuple(matched), partial

    def _mark_partial(self, key: _EventKey) -> None:
        # Tombstones are bounded too. If their exact identity no longer fits,
        # conservatively mark every later claim partial for this connection.
        if len(self._partial_keys) >= self._max_events:
            self._partial_keys.clear()
            self._global_partial = True
            return
        self._partial_keys.add(key)


class HermesClient:
    """Small request/correlation client for one Hermes gateway connection."""

    def __init__(
        self,
        transport: HermesTransport,
        *,
        request_timeout: float = 15,
        max_pre_ack_events: int = 64,
        max_pre_ack_bytes: int = 256 * 1024,
        max_live_events: int = 256,
        attention_handler: HermesAttentionHandler | None = None,
        activity_handler: HermesActivityHandler | None = None,
    ) -> None:
        if request_timeout <= 0 or max_live_events < 1:
            raise ValueError("Hermes client limits must be positive")
        self._transport = transport
        self._request_timeout = request_timeout
        self._max_live_events = max_live_events
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, object]]] = {}
        self._pre_ack = _PreAckEventBuffer(
            max_events=max_pre_ack_events,
            max_bytes=max_pre_ack_bytes,
        )
        self._turn_streams: dict[_EventKey, HermesTurnStream] = {}
        self._ready = asyncio.Event()
        self._replay_epoch: str | None = None
        self._capabilities: HermesGatewayCapabilities | None = None
        self._reader: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self._attention_handler = attention_handler
        self._activity_handler = activity_handler

    def set_attention_handler(self, handler: HermesAttentionHandler) -> None:
        """Install the observation-only sink used by the owning lane adapter."""

        self._attention_handler = handler

    def set_activity_handler(self, handler: HermesActivityHandler) -> None:
        """Install the observation-only sink for owned-session busy/idle state."""

        self._activity_handler = handler

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._reader is not None:
            raise RuntimeError("Hermes client already started")
        self._reader = asyncio.create_task(self._read_loop(), name="hermes-jsonrpc-reader")

    async def negotiate(self) -> HermesGatewayCapabilities:
        """Wait for ready and require both admission/correlation capabilities."""

        try:
            async with asyncio.timeout(self._request_timeout):
                await self._ready.wait()
        except TimeoutError as exc:
            raise HermesTransportError("timed out waiting for gateway.ready") from exc
        result = await self._request("gateway.capabilities", {})
        missing = [
            name
            for name in ("prompt_submit_if_idle_v1", "prompt_turn_correlation_v1")
            if result.get(name) is not True
        ]
        if missing:
            raise HermesCapabilityError(
                "Hermes gateway lacks required capabilities: " + ", ".join(missing)
            )
        assert self._replay_epoch is not None
        self._capabilities = HermesGatewayCapabilities(replay_epoch=self._replay_epoch)
        return self._capabilities

    async def create_session(
        self,
        *,
        profile: str,
        cwd: str,
        title: str,
    ) -> HermesSessionCreationResult:
        """Create once; every error after call entry leaves creation unknown."""

        self._require_capabilities()
        try:
            result = await self._request(
                "session.create",
                {"profile": profile, "cwd": cwd, "title": title},
            )
            runtime_id = result.get("session_id")
            stored_id = result.get("stored_session_id")
            info = result.get("info")
            effective_cwd = info.get("cwd") if isinstance(info, dict) else None
            if (
                not isinstance(runtime_id, str)
                or not runtime_id
                or not isinstance(stored_id, str)
                or not stored_id
                or not isinstance(effective_cwd, str)
                or not effective_cwd
            ):
                raise HermesProtocolError(
                    "session.create omitted runtime, stored, or effective cwd identity"
                )
            return HermesSessionCreated(
                runtime_session_id=runtime_id,
                stored_session_id=stored_id,
                effective_cwd=effective_cwd,
            )
        except asyncio.CancelledError:
            raise
        except (HermesClientError, TimeoutError) as exc:
            return HermesSessionCreationUnknown(error=str(exc))

    async def submit_prompt(self, *, runtime_session_id: str, text: str) -> HermesSubmissionResult:
        """Submit only through strict idle admission and bind native turn evidence."""

        self._require_capabilities()
        if not runtime_session_id or not text:
            raise ValueError("Hermes prompt submission requires a runtime session id and text")
        try:
            result = await self._request(
                "prompt.submit",
                {"session_id": runtime_session_id, "text": text, "if_idle": True},
            )
            turn_id = result.get("turn_id")
            if result.get("status") != "streaming" or not isinstance(turn_id, str) or not turn_id:
                raise HermesProtocolError(
                    "prompt.submit omitted streaming status or native turn correlation"
                )
            key = (runtime_session_id, turn_id)
            buffered, partial = self._pre_ack.claim(key)

            def release_stream() -> None:
                self._turn_streams.pop(key, None)

            stream = HermesTurnStream(
                buffered,
                partial=partial,
                max_live_events=self._max_live_events,
                on_close=release_stream,
            )
            self._turn_streams[key] = stream
            return HermesSubmissionAccepted(turn_id=turn_id, stream=stream)
        except asyncio.CancelledError:
            raise
        except HermesRpcError as exc:
            if exc.code == 4091 and isinstance(exc.data, dict) and exc.data.get("reason") == "busy":
                return HermesSubmissionRejected(error=str(exc))
            return HermesSubmissionUnknown(error=str(exc))
        except (HermesClientError, TimeoutError) as exc:
            return HermesSubmissionUnknown(error=str(exc))

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        await self._transport.close()
        if self._reader is not None and not self._reader.done():
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        self._fail_all(HermesTransportError("Hermes gateway connection closed"))
        self._close_turn_streams()
        self._closed.set()

    def _require_capabilities(self) -> None:
        if self._capabilities is None:
            raise HermesCapabilityError("Hermes gateway capabilities have not been negotiated")

    async def _request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            try:
                await self._transport.send(
                    {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise HermesTransportError(f"Hermes transport send failed: {exc}") from exc
            async with asyncio.timeout(self._request_timeout):
                return await future
        finally:
            self._pending.pop(request_id, None)

    async def _read_loop(self) -> None:
        try:
            while True:
                try:
                    message = await self._transport.receive()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise HermesTransportError(f"Hermes transport receive failed: {exc}") from exc
                if message is None:
                    raise HermesTransportError("Hermes gateway stream closed")
                if not isinstance(message, dict):
                    raise HermesProtocolError("Hermes gateway message must be a JSON object")
                self._handle(message)
        except asyncio.CancelledError:
            raise
        except HermesClientError as exc:
            self._fail_all(exc)
            self._close_turn_streams()
        finally:
            self._closed.set()

    def _handle(self, message: dict[str, object]) -> None:
        request_id = message.get("id")
        if type(request_id) is int and ("result" in message or "error" in message):
            self._resolve(request_id, message)
            return
        if message.get("method") != "event":
            return
        params = message.get("params")
        if not isinstance(params, dict):
            return
        event_type = params.get("type")
        payload = params.get("payload")
        if event_type == "gateway.ready" and isinstance(payload, dict):
            epoch = payload.get("replay_epoch")
            if isinstance(epoch, str) and epoch:
                self._replay_epoch = epoch
                self._ready.set()
            return
        runtime_id = params.get("session_id")
        if not isinstance(event_type, str) or not isinstance(runtime_id, str):
            return
        event_payload = payload if isinstance(payload, dict) else {}
        attention = _attention_event(event_type, runtime_id, event_payload)
        if attention is not None:
            if self._attention_handler is not None:
                self._attention_handler(attention)
            return
        turn_id = event_payload.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            return
        event = HermesEvent(
            type=event_type,
            runtime_session_id=runtime_id,
            turn_id=turn_id,
            payload=dict(event_payload),
        )
        if event.type in {"message.start", "message.complete"} and self._activity_handler:
            self._activity_handler(event)
        key = (runtime_id, turn_id)
        stream = self._turn_streams.get(key)
        if stream is None:
            self._pre_ack.add(event)
        else:
            stream.publish(event)

    def _resolve(self, request_id: int, message: dict[str, object]) -> None:
        future = self._pending.get(request_id)
        if future is None or future.done():
            return
        if "error" in message:
            raw_error = message.get("error")
            if not isinstance(raw_error, dict):
                future.set_exception(HermesProtocolError("malformed Hermes error response"))
                return
            code = raw_error.get("code")
            text = raw_error.get("message")
            future.set_exception(
                HermesRpcError(
                    code if type(code) is int else -1,
                    text if isinstance(text, str) else "unknown error",
                    raw_error.get("data"),
                )
            )
            return
        result = message.get("result")
        if not isinstance(result, dict):
            future.set_exception(HermesProtocolError("Hermes result must be a JSON object"))
            return
        future.set_result(result)

    def _fail_all(self, error: HermesClientError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    def _close_turn_streams(self) -> None:
        for stream in tuple(self._turn_streams.values()):
            stream.close()
        self._turn_streams.clear()


def _attention_event(
    event_type: str, runtime_session_id: str, payload: dict[str, object]
) -> HermesAttentionEvent | None:
    suffix: Literal["request", "expire"] | None = None
    if event_type.endswith(".request"):
        suffix = "request"
    elif event_type.endswith(".expire"):
        suffix = "expire"
    if suffix is None:
        return None
    family = event_type[: -(len(suffix) + 1)]
    if family in _HUMAN_ATTENTION_FAMILIES:
        category: HermesAttentionCategory = "human_or_sensitive_input"
    elif family in _NATIVE_CAPABILITY_ATTENTION_FAMILIES:
        category = "native_client_capability_unavailable"
    else:
        return None
    if suffix == "expire" and family not in _EXPIRING_ATTENTION_FAMILIES:
        return None
    raw_request_id = payload.get("request_id")
    request_id = (
        raw_request_id if isinstance(raw_request_id, str) and raw_request_id.strip() else None
    )
    if suffix == "expire" and request_id is None:
        return None
    return HermesAttentionEvent(
        type=event_type,
        family=family,
        runtime_session_id=runtime_session_id,
        request_id=request_id,
        category=category,
        expired=suffix == "expire",
    )
