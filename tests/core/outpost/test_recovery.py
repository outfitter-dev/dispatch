from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from outfitter.dispatch.core import outpost_recovery
from outfitter.dispatch.core.outpost_models import OutpostRunnerError, Wake
from outfitter.dispatch.core.outpost_receipts import RunLedger, run_lock
from outfitter.dispatch.core.outpost_runner import prepare_run


def saved_run(path: Path) -> RunLedger:
    ledger = prepare_run(path)
    (path / "binding.json").write_text(
        json.dumps(
            {
                "agent": "claude-1234",
                "startedAt": "original-generation",
                "claudeSession": "original-process",
                "apiUrl": "https://outpost.test/api/v1",
            }
        )
    )
    return ledger


def wake(identifier: str) -> Wake:
    return Wake(
        id=identifier,
        agent="claude-1234",
        startedAt="original-generation",
        thread="ask-1",
        message="answer-1",
        channel="fixture",
    )


@pytest.mark.asyncio
async def test_stopped_owner_reports_exact_evidence_and_marks_success_durably(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "run"
    ledger = saved_run(run)
    for identifier in ["never-written", "possible-write", "completed"]:
        ledger.reserve(wake(identifier))
    ledger.submitted("possible-write")
    ledger.submitted("completed")
    ledger.complete("completed")
    # A crash may leave an unfinished write beside the authoritative receipt.
    next((run / "receipts").glob("*.json")).with_suffix(".tmp").write_text("unfinished")
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer original-parent"
        assert "outpost-act-as" not in request.headers
        body = json.loads(request.content)
        assert body["startedAt"] == "original-generation"
        identifier = request.url.path.split("/")[-2]
        assert body["attempt"] == ledger.receipt(identifier).attempt
        requests.append((identifier, body["outcome"]))
        return httpx.Response(200, json={"recorded": True})

    client_class = httpx.AsyncClient
    client = client_class(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    assert (
        await outpost_recovery.reconcile_run(run, "https://outpost.test/api/v1", "original-parent")
        == 3
    )
    assert sorted(requests) == [
        ("completed", "completed"),
        ("never-written", "not-submitted"),
        ("possible-write", "uncertain"),
    ]
    assert all(receipt.reported for receipt in RunLedger(run / "receipts").receipts())
    requests.clear()
    replacement = client_class(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: replacement)
    assert (
        await outpost_recovery.reconcile_run(run, "https://outpost.test/api/v1", "original-parent")
        == 0
    )
    assert requests == []


@pytest.mark.asyncio
async def test_recovery_continues_after_a_conflicting_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "run"
    ledger = saved_run(run)
    for identifier in ["conflict", "completed"]:
        ledger.reserve(wake(identifier))
        ledger.submitted(identifier)
        ledger.complete(identifier)
    reported = []

    def handler(request: httpx.Request) -> httpx.Response:
        identifier = request.url.path.split("/")[-2]
        reported.append(identifier)
        return httpx.Response(409 if identifier == "conflict" else 200)

    client_class = httpx.AsyncClient
    client = client_class(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(OutpostRunnerError, match="1 delivery outcomes"):
        await outpost_recovery.reconcile_run(run, "https://outpost.test/api/v1", "parent")
    assert sorted(reported) == ["completed", "conflict"]
    assert ledger.receipt("completed").reported
    assert not ledger.receipt("conflict").reported


@pytest.mark.asyncio
async def test_recovery_refuses_an_active_owner_and_redirected_endpoint(tmp_path: Path) -> None:
    run = tmp_path / "run"
    saved_run(run)
    with run_lock(run), pytest.raises(OutpostRunnerError, match="active owner"):
        await outpost_recovery.reconcile_run(run, "https://outpost.test/api/v1", "parent")
    with pytest.raises(OutpostRunnerError, match="original Outpost run"):
        await outpost_recovery.reconcile_run(run, "https://other.test/api/v1", "parent")


@pytest.mark.asyncio
async def test_cancelled_completion_keeps_ownership_until_writer_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import threading

    from outfitter.dispatch.core.outpost_receipts import durable_io, owned_run

    run = tmp_path / "run"
    ledger = saved_run(run)
    ledger.reserve(wake("completed"))
    ledger.submitted("completed")
    entered = threading.Event()
    release = threading.Event()
    original = ledger._write

    def delayed_write(receipt: object) -> None:
        entered.set()
        assert release.wait(timeout=5)
        original(receipt)  # type: ignore[arg-type]

    monkeypatch.setattr(ledger, "_write", delayed_write)

    async def finish() -> None:
        async with owned_run(run):
            await durable_io(ledger.complete, "completed")

    task = asyncio.create_task(finish())
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0.02)
    with pytest.raises(OutpostRunnerError, match="active owner"), run_lock(run):
        pass
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger.receipt("completed").state == "completed"
    with run_lock(run):
        pass


def test_prepared_receipt_is_invisible_until_atomic_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from outfitter.dispatch.core import outpost_receipts

    ledger = RunLedger(tmp_path)
    final_files: list[Path] = []

    def interrupted(path: Path, text: str) -> None:
        path.write_text("partial")
        final_files.extend(tmp_path.glob("*.json"))
        raise OSError("synthetic interrupted preparation")

    monkeypatch.setattr(outpost_receipts, "write_private", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        ledger.reserve(wake("prepared"))
    assert final_files == []
    assert ledger.receipts() == []


def test_report_marker_cannot_settle_a_newer_outcome(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path)
    ledger.reserve(wake("completed"))
    ledger.submitted("completed")
    uncertain = ledger.receipt("completed")
    ledger.complete("completed")
    with pytest.raises(OutpostRunnerError, match="evidence changed"):
        ledger.reported(uncertain)
    assert not ledger.receipt("completed").reported
