"""Private delivery evidence and exclusive ownership for an Outpost run."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from outfitter.dispatch.core.outpost_models import OutpostRunnerError, Wake


async def durable_io[T, **P](operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Finish started filesystem work before cancellation releases run ownership."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_private(path: Path, text: str) -> None:
    with path.open("x") as output:
        os.chmod(path, 0o600)
        output.write(text)
        output.flush()
        os.fsync(output.fileno())


@asynccontextmanager
async def owned_run(directory: Path) -> AsyncIterator[None]:
    ownership = run_lock(directory)
    try:
        await durable_io(ownership.__enter__)
    except BaseException:
        await durable_io(ownership.__exit__, None, None, None)
        raise
    try:
        yield
    finally:
        await durable_io(ownership.__exit__, None, None, None)


class DeliveryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    state: Literal["prepared", "indeterminate", "completed"]
    attempt: str
    wake: Wake
    reported: bool = False


@contextmanager
def run_lock(directory: Path) -> Iterator[None]:
    import fcntl

    path = directory / "owner.lock"
    if path.is_symlink():
        raise OutpostRunnerError("runner ownership file must not be a symlink")
    with path.open("a") as lock:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OutpostRunnerError("Outpost run still has an active owner") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class RunLedger:
    """Retain the exact attempt and wake before any possible provider input."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, identifier: str) -> Path:
        return self.directory / (hashlib.sha256(identifier.encode()).hexdigest() + ".json")

    def state(self, identifier: str) -> str | None:
        path = self._path(identifier)
        if not path.exists():
            return None
        record: object = json.loads(path.read_text())
        if not isinstance(record, dict) or record.get("id") != identifier:
            raise OutpostRunnerError("invalid Outpost runner receipt")
        status = record.get("state")
        if status not in {"prepared", "indeterminate", "completed"}:
            raise OutpostRunnerError("invalid Outpost runner receipt state")
        return str(status)

    def receipt(self, identifier: str) -> DeliveryReceipt:
        path = self._path(identifier)
        if path.is_symlink():
            raise OutpostRunnerError("runner receipt must not be a symlink")
        receipt = DeliveryReceipt.model_validate_json(path.read_text())
        if receipt.id != identifier or receipt.wake.id != identifier:
            raise OutpostRunnerError("runner receipt wake does not match its identifier")
        return receipt

    def receipts(self) -> list[DeliveryReceipt]:
        result = []
        for path in sorted(self.directory.glob("*.json")):
            if path.is_symlink():
                raise OutpostRunnerError("runner receipt must not be a symlink")
            receipt = DeliveryReceipt.model_validate_json(path.read_text())
            if self._path(receipt.id) != path or receipt.wake.id != receipt.id:
                raise OutpostRunnerError("invalid Outpost runner receipt")
            result.append(receipt)
        return result

    def reserve(self, wake: Wake) -> None:
        receipt = DeliveryReceipt(id=wake.id, state="prepared", attempt=str(uuid4()), wake=wake)
        target = self._path(wake.id)
        temporary = target.with_suffix(f".{uuid4().hex}.tmp")
        try:
            write_private(temporary, receipt.model_dump_json(by_alias=True))
            # Hard-link publication is atomic and refuses to replace an existing attempt.
            os.link(temporary, target)
            sync_directory(self.directory)
        except FileExistsError as exc:
            raise OutpostRunnerError("Outpost wake already recorded") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def submitted(self, identifier: str) -> None:
        self._transition(identifier, "prepared", "indeterminate")

    def complete(self, identifier: str) -> None:
        self._transition(identifier, "indeterminate", "completed")

    def reported(self, evidence: DeliveryReceipt) -> None:
        receipt = self.receipt(evidence.id)
        if (receipt.attempt, receipt.state, receipt.wake) != (
            evidence.attempt,
            evidence.state,
            evidence.wake,
        ):
            raise OutpostRunnerError("delivery evidence changed before its report was recorded")
        self._write(receipt.model_copy(update={"reported": True}))

    def _transition(self, identifier: str, previous: str, state: str) -> None:
        receipt = self.receipt(identifier)
        if receipt.state != previous:
            raise OutpostRunnerError("Outpost wake has no expected delivery state")
        self._write(receipt.model_copy(update={"state": state}))

    def _write(self, updated: DeliveryReceipt) -> None:
        target = self._path(updated.id)
        temporary = target.with_suffix(f".{uuid4().hex}.tmp")
        with temporary.open("x") as output:
            os.chmod(output.name, 0o600)
            output.write(updated.model_dump_json(by_alias=True))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
        sync_directory(self.directory)
