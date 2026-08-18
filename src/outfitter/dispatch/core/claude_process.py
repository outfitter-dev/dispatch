"""Bounded direct execution for internal Claude CLI operations."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from outfitter.dispatch.core.claude_launch_types import (
    ClaudeLaunchOutputLimitError,
    ClaudeLaunchTimeoutError,
    ClaudeProcessResult,
)

_MAX_OUTPUT_BYTES = 1024 * 1024
_COMMAND_TIMEOUT_SECONDS = 10.0
_SHORT_ID_TOKEN = re.compile(r"(?:^|\s)([0-9a-fA-F]{8})(?=\s|$)")


def _capture_short_ids(data: bytearray, candidates: set[str]) -> None:
    text = data.decode(errors="replace")
    candidates.update(match.group(1).lower() for match in _SHORT_ID_TOKEN.finditer(text))


async def _read_bounded(
    stream: asyncio.StreamReader, *, short_id_candidates: set[str] | None = None
) -> bytes:
    data = bytearray()
    while chunk := await stream.read(64 * 1024):
        data.extend(chunk)
        if short_id_candidates is not None:
            _capture_short_ids(data, short_id_candidates)
        if len(data) > _MAX_OUTPUT_BYTES:
            raise ClaudeLaunchOutputLimitError("Claude CLI output exceeded the safe limit")
    return bytes(data)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await process.wait()


async def run_claude_process(
    argv: tuple[str, ...], cwd: Path, environment: Mapping[str, str]
) -> ClaudeProcessResult:
    """Run one Claude CLI command without a shell under fixed resource bounds."""

    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=dict(environment),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=64 * 1024,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_short_ids: set[str] = set()
    tasks = [
        asyncio.create_task(_read_bounded(process.stdout, short_id_candidates=stdout_short_ids)),
        asyncio.create_task(_read_bounded(process.stderr)),
        asyncio.create_task(process.wait()),
    ]
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=_COMMAND_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        for task in tasks:
            task.cancel()
        await asyncio.shield(_terminate(process))
        await asyncio.gather(*tasks, return_exceptions=True)
        raise ClaudeLaunchTimeoutError(
            "Claude CLI command timed out",
            short_id_candidates=tuple(sorted(stdout_short_ids)),
        ) from exc
    except ClaudeLaunchOutputLimitError as exc:
        for task in tasks:
            task.cancel()
        await asyncio.shield(_terminate(process))
        await asyncio.gather(*tasks, return_exceptions=True)
        raise ClaudeLaunchOutputLimitError(
            "Claude CLI output exceeded the safe limit",
            short_id_candidates=tuple(sorted(stdout_short_ids)),
        ) from exc
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.shield(_terminate(process))
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    stdout = cast(bytes, results[0]).decode(errors="replace")
    stderr = cast(bytes, results[1]).decode(errors="replace")
    return ClaudeProcessResult(process.returncode or 0, stdout, stderr)
