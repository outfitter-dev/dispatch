"""Report durable outcomes from a stopped owner without launching another process."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from outfitter.dispatch.core.outpost_feed import OutpostFeed, validate_endpoint
from outfitter.dispatch.core.outpost_models import Binding, OutpostRunnerError
from outfitter.dispatch.core.outpost_receipts import RunLedger, run_lock


class SavedRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str
    startedAt: str
    claudeSession: str
    apiUrl: str


def read_run(directory: Path, api_url: str) -> tuple[SavedRun, RunLedger]:
    marker = directory / ".dispatch-outpost-owner"
    if marker.is_symlink() or marker.read_text() != "1\n":
        raise OutpostRunnerError("directory is not an Outpost runner directory")
    binding = directory / "binding.json"
    if binding.is_symlink() or (directory / "receipts").is_symlink():
        raise OutpostRunnerError("runner state must not be a symlink")
    saved = SavedRun.model_validate(json.loads(binding.read_text()))
    if validate_endpoint(api_url).rstrip("/") != saved.apiUrl:
        raise OutpostRunnerError("recovery endpoint does not match the original Outpost run")
    return saved, RunLedger(directory / "receipts")


async def reconcile_run(directory: Path, api_url: str, token: str) -> int:
    directory = directory.absolute()
    saved, ledger = await asyncio.to_thread(read_run, directory, api_url)
    with run_lock(directory):
        binding = Binding(agent=saved.agent, startedAt=saved.startedAt, mcp_session="recovery")
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            feed = OutpostFeed(client, saved.apiUrl, saved.apiUrl, token)
            receipts = await asyncio.to_thread(ledger.receipts)
            failed = 0
            count = 0
            for receipt in receipts:
                if receipt.reported:
                    continue
                if (receipt.wake.agent, receipt.wake.started_at) != (
                    binding.agent,
                    binding.started_at,
                ):
                    raise OutpostRunnerError("delivery receipt does not match the original owner")
                outcome = {
                    "prepared": "not-submitted",
                    "indeterminate": "uncertain",
                    "completed": "completed",
                }[receipt.state]
                try:
                    await feed.delivery(binding, receipt.wake, receipt.attempt, outcome)
                except httpx.HTTPError:
                    failed += 1
                else:
                    await asyncio.to_thread(ledger.reported, receipt.id)
                    count += 1
            if failed:
                raise OutpostRunnerError(
                    f"{failed} delivery outcomes could not be recorded; saved receipts retained"
                )
            return count
