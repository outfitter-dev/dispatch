"""Normalize safe structured text/image input for App Server turns."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
import ssl
from collections.abc import AsyncIterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit, urlunsplit

import httpcore
import httpx
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from outfitter.dispatch.client.models import (
    ImageInput,
    LocalImageInput,
    TextInput,
    UserInput,
)
from outfitter.dispatch.config import CapturePolicy
from outfitter.dispatch.contracts.errors import ValidationError

from .capture import bound_redacted_text
from .models import LocalImageContent, MessageContent

MAX_LOCAL_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_COUNT = 16
MAX_TOTAL_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_URL_LENGTH = 8192
REMOTE_IMAGE_TIMEOUT_SECONDS = 15.0
SUPPORTED_IMAGE_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})
_CONTENT_ADAPTER = TypeAdapter(list[MessageContent])
_SIGNATURES: tuple[tuple[str, bytes, int], ...] = (
    ("image/png", b"\x89PNG\r\n\x1a\n", 0),
    ("image/jpeg", b"\xff\xd8\xff", 0),
    ("image/gif", b"GIF87a", 0),
    ("image/gif", b"GIF89a", 0),
    ("image/webp", b"WEBP", 8),
)


@dataclass(frozen=True)
class RichInput:
    text: str
    input_items: list[UserInput]
    stored_content: list[dict[str, object]]

    @property
    def has_images(self) -> bool:
        return any(item.type in {"image", "localImage"} for item in self.input_items)

    @property
    def image_count(self) -> int:
        return sum(item.type in {"image", "localImage"} for item in self.input_items)


def normalize_rich_input(
    *,
    text: str | None,
    content: list[MessageContent],
    cwd: str,
    validate_local_files: bool,
) -> RichInput:
    image_count = sum(item.type in {"image", "local_image"} for item in content)
    if image_count > MAX_IMAGE_COUNT:
        raise ValidationError(f"at most {MAX_IMAGE_COUNT} images may be sent at once")
    input_items: list[UserInput] = []
    stored: list[dict[str, object]] = []
    local_bytes = 0
    for item in content:
        if item.type == "text":
            input_items.append(TextInput(text=item.text))
            stored.append(item.model_dump(mode="python", exclude_none=True))
        elif item.type == "image":
            url = _validate_image_url(item.url)
            input_items.append(ImageInput(url=url, detail=item.detail))
            stored.append(item.model_dump(mode="python", exclude_none=True) | {"url": url})
        else:
            path, metadata = _resolve_local_image(item, cwd=cwd, validate=validate_local_files)
            input_items.append(LocalImageInput(path=path, detail=item.detail))
            stored.append(
                item.model_dump(mode="python", exclude_none=True) | {"path": path} | metadata
            )
            size = metadata.get("size")
            local_bytes += size if isinstance(size, int) else 0
            if local_bytes > MAX_TOTAL_IMAGE_BYTES:
                raise ValidationError(
                    f"local images exceed the {MAX_TOTAL_IMAGE_BYTES}-byte aggregate limit"
                )
    normalized_text = text or ""
    if not normalized_text and not input_items:
        raise ValidationError("message requires text or image content")
    return RichInput(text=normalized_text, input_items=input_items, stored_content=stored)


async def normalize_rich_input_async(
    *,
    text: str | None,
    content: list[MessageContent],
    cwd: str,
    validate_local_files: bool,
) -> RichInput:
    return await asyncio.to_thread(
        normalize_rich_input,
        text=text,
        content=content,
        cwd=cwd,
        validate_local_files=validate_local_files,
    )


def queued_content(value: list[dict[str, Any]]) -> list[MessageContent]:
    """Parse durable references while ignoring internal file metadata."""
    try:
        return _CONTENT_ADAPTER.validate_python(value)
    except PydanticValidationError as exc:
        raise ValidationError("queued image content is invalid") from exc


def attachment_audit_detail(message: RichInput) -> str:
    kinds: list[str] = []
    for item in message.input_items:
        if item.type == "image":
            parts = urlsplit(item.url)
            safe_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            kinds.append(f"url:{safe_url}")
        elif item.type == "localImage":
            kinds.append(f"file:{Path(item.path).name}")
    return f"images={message.image_count}" + (f" ({', '.join(kinds)})" if kinds else "")


def message_audit_detail(message: RichInput, policy: CapturePolicy) -> str:
    """Return bounded, redacted text and reference-only attachment metadata."""
    text_parts = [message.text] if message.text else []
    text_parts.extend(item.text for item in message.input_items if item.type == "text")
    combined = "\n".join(text_parts)
    bounded = bound_redacted_text(combined, policy) if combined else None
    images: list[dict[str, object]] = []
    for item in message.stored_content:
        image: dict[str, object] | None = None
        if item.get("type") == "image" and isinstance(item.get("url"), str):
            image = {"type": "url", "ref": _safe_remote_ref(str(item["url"]))}
        elif item.get("type") == "local_image" and isinstance(item.get("path"), str):
            image = {"type": "file", "name": Path(str(item["path"])).name}
        if image is not None:
            for key in ("detail", "media_type", "size", "sha256"):
                if item.get(key) is not None:
                    image[key] = item[key]
            images.append(image)
    return json.dumps(
        {
            "text": bounded.text if bounded is not None else None,
            "text_bytes": bounded.original_bytes if bounded is not None else 0,
            "text_truncated": bounded.truncated if bounded is not None else False,
            "images": images,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


async def materialize_remote_images(
    message: RichInput, *, transport: httpx.AsyncBaseTransport | None = None
) -> RichInput:
    """Fetch HTTPS references into ephemeral data URLs required by App Server 0.144."""
    if not any(item.type == "image" for item in message.input_items):
        return message
    materialized: list[UserInput] = []
    stored = [dict(item) for item in message.stored_content]
    remote_index = 0
    total_bytes = sum(
        size for item in message.stored_content if isinstance((size := item.get("size")), int)
    )
    try:
        async with asyncio.timeout(REMOTE_IMAGE_TIMEOUT_SECONDS):
            for item in message.input_items:
                if item.type != "image":
                    materialized.append(item)
                    continue
                payload, media_type = await _fetch_remote_image(item.url, transport=transport)
                total_bytes += len(payload)
                if total_bytes > MAX_TOTAL_IMAGE_BYTES:
                    raise ValidationError(
                        f"images exceed the {MAX_TOTAL_IMAGE_BYTES}-byte aggregate limit"
                    )
                encoded = base64.b64encode(payload).decode("ascii")
                materialized.append(
                    ImageInput(url=f"data:{media_type};base64,{encoded}", detail=item.detail)
                )
                while remote_index < len(stored) and stored[remote_index].get("type") != "image":
                    remote_index += 1
                if remote_index < len(stored):
                    stored[remote_index].update(
                        {
                            "media_type": media_type,
                            "size": len(payload),
                            "sha256": sha256(payload).hexdigest(),
                        }
                    )
                    remote_index += 1
    except TimeoutError as exc:
        raise ValidationError("remote image delivery exceeded the 15-second timeout") from exc
    return RichInput(
        text=message.text,
        input_items=materialized,
        stored_content=stored,
    )


def _validate_image_url(value: str) -> str:
    if len(value) > MAX_IMAGE_URL_LENGTH:
        raise ValidationError(f"image URL exceeds {MAX_IMAGE_URL_LENGTH} characters")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc:
        raise ValidationError("image URLs must use https with a host")
    if parts.username is not None or parts.password is not None:
        raise ValidationError("image URLs must not contain user credentials")
    return value


async def _fetch_remote_image(
    url: str, *, transport: httpx.AsyncBaseTransport | None
) -> tuple[bytes, str]:
    parts = urlsplit(url)
    host = parts.hostname
    if host is None:
        raise ValidationError("image URL requires a host")
    address = await _resolve_public_address(url)
    pinned = transport or _PinnedAsyncHTTPTransport(host=host, address=address)
    try:
        async with (
            httpx.AsyncClient(
                follow_redirects=False,
                timeout=REMOTE_IMAGE_TIMEOUT_SECONDS,
                trust_env=False,
                transport=pinned,
                headers={"User-Agent": "outfitter-dispatch/image-input"},
            ) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_LOCAL_IMAGE_BYTES:
                    raise ValidationError(f"remote image exceeds {MAX_LOCAL_IMAGE_BYTES} bytes")
    except (
        httpx.HTTPError,
        httpcore.NetworkError,
        httpcore.TimeoutException,
        httpcore.ProtocolError,
    ) as exc:
        raise ValidationError(f"could not fetch remote image: {_safe_remote_ref(url)}") from exc
    media_type = _detect_media_type(bytes(payload))
    if media_type is None:
        raise ValidationError("remote URL did not return a supported image")
    return bytes(payload), media_type


async def _resolve_public_address(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname
    if host is None:
        raise ValidationError("image URL requires a host")
    try:
        addresses = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: socket.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM),
        )
    except OSError as exc:
        raise ValidationError(f"could not resolve remote image host: {host}") from exc
    if not addresses:
        raise ValidationError(f"could not resolve remote image host: {host}")
    for info in addresses:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValidationError("remote image URLs must resolve only to public addresses")
    return str(ipaddress.ip_address(addresses[0][4][0]))


def _safe_remote_ref(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, *, host: str, address: str) -> None:
        self._host = host
        self._address = address
        self._backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        if host != self._host:
            raise httpcore.ConnectError("remote image host changed during delivery")
        return await self._backend.connect_tcp(
            self._address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Any = None
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("unix sockets are not valid remote image targets")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _ClosableAsyncStream(Protocol):
    def __aiter__(self) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


class _CoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: _ClosableAsyncStream) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class _PinnedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, host: str, address: str) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=_PinnedNetworkBackend(host=host, address=address),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._pool.handle_async_request(
            httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_CoreResponseStream(cast(_ClosableAsyncStream, response.stream)),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


def _resolve_local_image(
    item: LocalImageContent, *, cwd: str, validate: bool
) -> tuple[str, dict[str, object]]:
    path = Path(item.path).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    path = path.resolve(strict=False)
    if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        choices = ", ".join(sorted(SUPPORTED_IMAGE_SUFFIXES))
        raise ValidationError(f"unsupported image type {path.suffix or '<none>'!r}; use: {choices}")
    if not path.exists() and not validate:
        return str(path), {}
    if not path.is_file():
        raise ValidationError(f"local image not found: {path}")
    try:
        stat_size = path.stat().st_size
        if stat_size > MAX_LOCAL_IMAGE_BYTES:
            raise ValidationError(
                f"local image {path} is {stat_size} bytes; max is {MAX_LOCAL_IMAGE_BYTES}"
            )
        with path.open("rb") as stream:
            payload = stream.read(MAX_LOCAL_IMAGE_BYTES + 1)
        if len(payload) > MAX_LOCAL_IMAGE_BYTES:
            raise ValidationError(
                f"local image {path} exceeds the {MAX_LOCAL_IMAGE_BYTES}-byte limit"
            )
    except OSError as exc:
        raise ValidationError(f"cannot read local image {path}: {exc}") from exc
    media_type = _detect_media_type(payload)
    if media_type is None:
        raise ValidationError(f"unsupported or invalid image content: {path}")
    return str(path), {
        "media_type": media_type,
        "size": len(payload),
        "sha256": sha256(payload).hexdigest(),
    }


def _detect_media_type(payload: bytes) -> str | None:
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    for media_type, signature, offset in _SIGNATURES:
        if media_type == "image/webp":
            continue
        if payload[offset : offset + len(signature)] == signature:
            return media_type
    return None
