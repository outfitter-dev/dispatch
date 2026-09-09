"""Receipt projection across the real daemon control socket."""

from pathlib import Path

from outfitter.dispatch.config import RuntimePolicy
from outfitter.dispatch.core.ops import REGISTRY
from outfitter.dispatch.daemon.control import ControlServer
from outfitter.dispatch.registry.store import Registry
from tests.daemon.test_control import _call, _result
from tests.fakes import make_ctx


async def test_native_receipt_and_lookup_cross_socket(socket_dir: Path) -> None:
    store = await Registry.open()
    ctx = make_ctx(store, policy=RuntimePolicy(allow_attached_writes=True))
    await store.add_lane(id="target", handle="@target", source="attached", status="busy")
    server = ControlServer(REGISTRY, ctx)
    path = socket_dir / "dispatchd.sock"
    await server.serve(path)
    try:
        sent = _result(
            await _call(
                path,
                "send",
                {
                    "lane": "target",
                    "text": "queued",
                    "mode": "queue",
                    "idempotency_key": "key",
                },
            )
        )
        receipt = sent["delivery"]
        assert isinstance(receipt, dict)
        assert receipt["transport"] == "native_queue"
        assert receipt["submission_id"] == "submission-1" and receipt["turn_id"] is None
        assert receipt["status"] == "accepted"
        assert _result(await _call(path, "delivery-get", {"receipt_id": receipt["id"]})) == receipt
    finally:
        await server.close()
        await store.close()
