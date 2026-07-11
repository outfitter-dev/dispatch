import socket
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest

from outfitter.dispatch.contracts.errors import ValidationError
from outfitter.dispatch.core import rich_input
from outfitter.dispatch.core.models import ImageUrlContent, LocalImageContent
from outfitter.dispatch.core.rich_input import materialize_remote_images, normalize_rich_input


def test_normalize_mixed_images_uses_absolute_path_and_safe_metadata(tmp_path: Path) -> None:
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")

    rich = normalize_rich_input(
        text="inspect",
        content=[
            LocalImageContent(path="sample.png", detail="high"),
            ImageUrlContent(url="https://example.com/a.png?token=secret"),
        ],
        cwd=str(tmp_path),
        validate_local_files=True,
    )

    assert rich.image_count == 2
    assert rich.input_items[0].model_dump(exclude_none=True) == {
        "type": "localImage",
        "path": str(image),
        "detail": "high",
    }
    assert rich.stored_content[0]["media_type"] == "image/png"
    assert rich.stored_content[0]["size"] == len(image.read_bytes())
    assert len(str(rich.stored_content[0]["sha256"])) == 64


def test_normalize_rejects_bad_scheme_and_spoofed_extension(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="must use https"):
        normalize_rich_input(
            text=None,
            content=[ImageUrlContent(url="http://example.com/a.png")],
            cwd=str(tmp_path),
            validate_local_files=True,
        )

    fake = tmp_path / "fake.png"
    fake.write_text("not an image")
    with pytest.raises(ValidationError, match="invalid image content"):
        normalize_rich_input(
            text=None,
            content=[LocalImageContent(path=str(fake))],
            cwd=str(tmp_path),
            validate_local_files=True,
        )


def test_queue_normalization_allows_a_missing_local_reference(tmp_path: Path) -> None:
    rich = normalize_rich_input(
        text=None,
        content=[LocalImageContent(path="later.png")],
        cwd=str(tmp_path),
        validate_local_files=False,
    )

    assert rich.stored_content == [{"type": "local_image", "path": str(tmp_path / "later.png")}]


async def test_remote_image_is_ephemerally_materialized_as_a_data_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=png))
    monkeypatch.setattr(
        "outfitter.dispatch.core.rich_input._resolve_public_address",
        AsyncMock(return_value="93.184.216.34"),
    )
    rich = normalize_rich_input(
        text="inspect",
        content=[ImageUrlContent(url="https://example.com/a.png?token=secret")],
        cwd=".",
        validate_local_files=True,
    )

    wire = await materialize_remote_images(rich, transport=transport)

    assert wire.input_items[0].type == "image"
    assert wire.input_items[0].url.startswith("data:image/png;base64,")
    assert rich.stored_content == [
        {"type": "image", "url": "https://example.com/a.png?token=secret"}
    ]


async def test_remote_fetch_error_omits_signed_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(404))
    monkeypatch.setattr(
        "outfitter.dispatch.core.rich_input._resolve_public_address",
        AsyncMock(return_value="93.184.216.34"),
    )
    rich = normalize_rich_input(
        text=None,
        content=[ImageUrlContent(url="https://example.com/a.png?token=secret")],
        cwd=".",
        validate_local_files=True,
    )

    with pytest.raises(ValidationError) as caught:
        await materialize_remote_images(rich, transport=transport)

    assert "https://example.com/a.png" in str(caught.value)
    assert "secret" not in str(caught.value)


async def test_remote_resolution_rejects_any_private_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ],
    )

    with pytest.raises(ValidationError, match="public addresses"):
        await rich_input._resolve_public_address("https://example.com/a.png")


async def test_pinned_backend_connects_to_the_validated_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect = AsyncMock(return_value=cast(Any, object()))
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    backend = rich_input._PinnedNetworkBackend(host="example.com", address="93.184.216.34")

    await backend.connect_tcp("example.com", 443)

    assert connect.await_args is not None
    assert connect.await_args.args[:2] == ("93.184.216.34", 443)
