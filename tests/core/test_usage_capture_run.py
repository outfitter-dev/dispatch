from __future__ import annotations

import io
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from outfitter.dispatch.core import usage_capture_run
from outfitter.dispatch.core.claude_statusline import read_claude_statusline_snapshot
from outfitter.dispatch.core.usage_capture import (
    UsageCaptureRecord,
    write_usage_capture_record,
)
from outfitter.dispatch.core.usage_capture_run import (
    MAX_STDIN_BYTES,
    run_usage_capture,
    run_usage_capture_from_stdin,
)


def _payload(**rate_limits: object) -> bytes:
    body: dict[str, object] = {
        "session_id": "raw-session-id",
        "version": "2.1.206",
        "model": {"display_name": "Opus"},
    }
    if rate_limits:
        body["rate_limits"] = rate_limits
    return json.dumps(body).encode()


_FIVE_HOUR = {"used_percentage": 23.5, "resets_at": 1_738_425_600}
_SEVEN_DAY = {"used_percentage": 41.2, "resets_at": 1_738_857_600}


def _py(code: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def _write_record(tmp_path: Path, command: str | None) -> Path:
    record = UsageCaptureRecord.model_validate(
        {
            "provider": "claude",
            "had_statusline": command is not None,
            "original_statusline": {"type": "command", "command": command}
            if command is not None
            else None,
            "installed_command": "dispatch usage-capture run --provider claude",
            "installed_at": "2026-08-29T12:00:00+00:00",
        }
    )
    return write_usage_capture_record(record, path=tmp_path / "statusline.original.json")


def test_run_fans_stdin_out_to_capture_and_renderer_verbatim(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    record = _write_record(
        tmp_path,
        _py("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
    )
    snapshot_path = tmp_path / "snapshot.json"
    payload = _payload(five_hour=_FIVE_HOUR, seven_day=_SEVEN_DAY)

    code = run_usage_capture(payload, record_path=record, snapshot_path=snapshot_path)

    captured = capfd.readouterr()
    assert code == 0
    assert captured.out.encode() == payload
    snapshot = read_claude_statusline_snapshot(path=snapshot_path)
    assert snapshot is not None
    assert snapshot.rate_limits.five_hour is not None
    assert snapshot.rate_limits.five_hour.used_percentage == 23.5


def test_run_preserves_multiline_unicode_ansi_and_osc8_output(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    rendered = (
        "\x1b[31m█ Opus ✨\x1b[0m\nline two\n\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\"
    )
    record = _write_record(
        tmp_path,
        _py(f"import sys; sys.stdout.write({rendered!r})"),
    )

    code = run_usage_capture(
        _payload(), record_path=record, snapshot_path=tmp_path / "snapshot.json"
    )

    captured = capfd.readouterr()
    assert code == 0
    assert captured.out == rendered


def test_run_emits_zero_stdout_when_no_renderer_existed(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    record = _write_record(tmp_path, None)

    code = run_usage_capture(
        _payload(five_hour=_FIVE_HOUR),
        record_path=record,
        snapshot_path=tmp_path / "snapshot.json",
    )

    captured = capfd.readouterr()
    assert code == 0
    assert captured.out == ""
    assert read_claude_statusline_snapshot(path=tmp_path / "snapshot.json") is not None


@pytest.mark.parametrize("corrupt", [None, b"{not a record"])
def test_run_fails_visually_open_when_record_missing_or_corrupt(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], corrupt: bytes | None
) -> None:
    record = tmp_path / "statusline.original.json"
    if corrupt is not None:
        record.write_bytes(corrupt)

    code = run_usage_capture(
        _payload(five_hour=_FIVE_HOUR),
        record_path=record,
        snapshot_path=tmp_path / "snapshot.json",
    )

    captured = capfd.readouterr()
    assert code == 0
    assert captured.out == ""
    # Capture still happened even though the record was unusable.
    assert read_claude_statusline_snapshot(path=tmp_path / "snapshot.json") is not None


def test_run_capture_failure_does_not_block_renderer_or_leak_payload(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    record = _write_record(tmp_path, _py("print('rendered anyway')"))

    code = run_usage_capture(
        b"private not-json payload",
        record_path=record,
        snapshot_path=tmp_path / "snapshot.json",
    )

    captured = capfd.readouterr()
    assert code == 0
    assert captured.out == "rendered anyway\n"
    assert "private" not in captured.err
    assert captured.err == "dispatch: Claude statusline snapshot rejected\n"
    assert not (tmp_path / "snapshot.json").exists()


@pytest.mark.parametrize(
    "payload",
    [
        # Integer literal beyond CPython's ~4300-digit int-conversion limit:
        # json.loads raises a bare ValueError (not JSONDecodeError).
        b'{"resets_at": ' + b"9" * 4400 + b"}",
        # In-limit integer that overflows float(): raises OverflowError.
        b'{"rate_limits": {"five_hour": {"used_percentage": ' + b"9" * 400 + b"}}}",
        # Escaped lone surrogate parses fine but raises UnicodeEncodeError
        # post-parse when the session fingerprint UTF-8 encodes it.
        b'{"session_id": "\\ud800"}',
        # Lone surrogate in another captured string field: pydantic rejects it
        # with ValidationError; the capture must still fail visually open.
        b'{"model": {"display_name": "x\\ud800y"}}',
    ],
)
def test_run_parser_escape_exceptions_stay_fail_open(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], payload: bytes
) -> None:
    """Hostile capture failures beyond JSONDecodeError — including post-parse
    normalization errors — must not suppress the renderer."""
    record = _write_record(
        tmp_path,
        _py("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
    )

    code = run_usage_capture(
        payload,
        record_path=record,
        snapshot_path=tmp_path / "snapshot.json",
    )

    captured = capfd.readouterr()
    assert code == 0
    # The renderer still ran and received every byte Claude Code sent.
    assert captured.out.encode() == payload
    assert captured.err == "dispatch: Claude statusline snapshot rejected\n"
    assert not (tmp_path / "snapshot.json").exists()


def test_run_never_erases_windows_when_later_payload_omits_rate_limits(tmp_path: Path) -> None:
    record = _write_record(tmp_path, None)
    snapshot_path = tmp_path / "snapshot.json"

    run_usage_capture(
        _payload(five_hour=_FIVE_HOUR, seven_day=_SEVEN_DAY),
        record_path=record,
        snapshot_path=snapshot_path,
    )
    run_usage_capture(_payload(), record_path=record, snapshot_path=snapshot_path)

    snapshot = read_claude_statusline_snapshot(path=snapshot_path)
    assert snapshot is not None
    assert snapshot.rate_limits.five_hour is not None
    assert snapshot.rate_limits.seven_day is not None
    assert snapshot.rate_limits_available is False


def test_run_preserves_cwd_and_env_including_columns_and_lines(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "session-cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("COLUMNS", "123")
    monkeypatch.setenv("LINES", "45")
    record = _write_record(
        tmp_path,
        _py("import os; print(os.getcwd()); print(os.environ['COLUMNS'], os.environ['LINES'])"),
    )

    code = run_usage_capture(
        _payload(), record_path=record, snapshot_path=tmp_path / "snapshot.json"
    )

    captured = capfd.readouterr()
    assert code == 0
    lines = captured.out.splitlines()
    assert Path(lines[0]).resolve() == workdir.resolve()
    assert lines[1] == "123 45"


def test_run_forwards_sigterm_to_renderer(tmp_path: Path) -> None:
    ready = tmp_path / "renderer-ready"
    renderer = (
        "import pathlib, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(9))\n"
        # Draining stdin gates readiness on the parent: run_usage_capture only
        # writes/closes the payload AFTER installing its forwarding handlers,
        # so `ready` proves both ends of the signal path are armed.
        "sys.stdin.buffer.read()\n"
        f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(30)\n"
    )
    # `exec` makes the renderer replace the intermediate `/bin/sh -c` process,
    # so the forwarded SIGTERM reaches the renderer's handler on every shell
    # (dash on Linux CI does not always exec a lone command the way bash does;
    # without this the shell dies with 143 and the renderer never sees it).
    record = _write_record(tmp_path, f"exec {_py(renderer)}")

    def _terminate_when_renderer_is_ready() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.01)
        if ready.exists():
            os.kill(os.getpid(), signal.SIGTERM)

    handler_before = signal.getsignal(signal.SIGTERM)
    killer = threading.Thread(target=_terminate_when_renderer_is_ready)
    killer.start()
    try:
        code = run_usage_capture(
            _payload(), record_path=record, snapshot_path=tmp_path / "snapshot.json"
        )
    finally:
        killer.join()

    assert code == 9
    # The forwarding handler was removed again after the renderer exited.
    assert signal.getsignal(signal.SIGTERM) is handler_before


def test_run_forwards_signal_delivered_during_spawn_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancellation landing mid-spawn is forwarded, never orphans the renderer.

    The forwarding handlers are armed BEFORE ``Popen``; a signal delivered while
    the process slot is still None is recorded as pending and forwarded once the
    child exists. Simulated deterministically: a wrapped ``Popen`` invokes the
    already-installed handler (as real delivery mid-spawn would) before creating
    the real child. The renderer never traps SIGTERM, so surviving 30 seconds
    would mean the signal was lost; instead it dies to the forwarded SIGTERM and
    the wrapper exits 128+SIGTERM.
    """
    renderer = "import sys, time\nsys.stdin.buffer.read()\ntime.sleep(30)\n"
    record = _write_record(tmp_path, f"exec {_py(renderer)}")

    real_popen = subprocess.Popen
    handler_before = signal.getsignal(signal.SIGTERM)

    def _popen_with_midspawn_signal(command: str, **_kwargs: object) -> subprocess.Popen[bytes]:
        # The forwarding handler must already be armed at spawn time.
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        assert handler is not handler_before
        # A real SIGTERM delivered here would run this handler with no child
        # spawned yet; invoke it directly to hit that window deterministically.
        handler(signal.SIGTERM, None)
        return real_popen(command, shell=True, stdin=subprocess.PIPE, start_new_session=True)

    monkeypatch.setattr(subprocess, "Popen", _popen_with_midspawn_signal)
    started = time.monotonic()
    code = run_usage_capture(
        _payload(), record_path=record, snapshot_path=tmp_path / "snapshot.json"
    )

    assert code == 128 + signal.SIGTERM
    # Died to the forwarded SIGTERM promptly — not to the renderer's 30s sleep
    # running out or the SIGKILL escalation timer.
    assert time.monotonic() - started < usage_capture_run._SIGKILL_ESCALATION_SECONDS
    assert signal.getsignal(signal.SIGTERM) is handler_before


def test_run_escalates_to_sigkill_when_renderer_ignores_forwarded_sigterm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A renderer that traps and ignores SIGTERM cannot hang the wrapper.

    After forwarding the signal, the wrapper bounds the remaining wait: on
    timeout it SIGKILLs the renderer's process group and exits with the
    conventional code for the signal it forwarded (not the SIGKILL cleanup).
    """
    monkeypatch.setattr(usage_capture_run, "_SIGKILL_ESCALATION_SECONDS", 0.5)
    ready = tmp_path / "renderer-ready"
    renderer = (
        "import os, pathlib, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: None)\n"
        "sys.stdin.buffer.read()\n"
        f"pathlib.Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    # `exec` makes the renderer the process-group leader, so its recorded pid
    # identifies the group the escalation must tear down.
    record = _write_record(tmp_path, f"exec {_py(renderer)}")

    def _terminate_when_renderer_is_ready() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.01)
        if ready.exists():
            os.kill(os.getpid(), signal.SIGTERM)

    killer = threading.Thread(target=_terminate_when_renderer_is_ready)
    killer.start()
    started = time.monotonic()
    try:
        code = run_usage_capture(
            _payload(), record_path=record, snapshot_path=tmp_path / "snapshot.json"
        )
    finally:
        killer.join()

    # Prompt exit: well under the renderer's 30s sleep, so the wrapper did not
    # sit in an unbounded wait behind the signal-ignoring renderer.
    assert time.monotonic() - started < 10
    assert code == 128 + signal.SIGTERM
    # The escalation killed the whole process group, not just the direct child.
    renderer_pgid = int(ready.read_text())
    with pytest.raises(ProcessLookupError):
        os.killpg(renderer_pgid, 0)


def test_run_keeps_escalation_armed_when_shell_dies_but_pipeline_member_ignores_sigterm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A signal-ignoring member of a pipeline cannot outlive the wrapper.

    With a pipeline the interposed ``/bin/sh`` stays the direct child and dies
    to the forwarded SIGTERM, so ``wait()`` returns while the signal-ignoring
    renderer survives in the group. The wrapper must keep the SIGKILL
    escalation armed until the whole group is gone instead of cancelling it
    when the shell's exit is reaped — otherwise the renderer lingers forever.
    """
    monkeypatch.setattr(usage_capture_run, "_SIGKILL_ESCALATION_SECONDS", 0.5)
    ready = tmp_path / "renderer-ready"
    renderer = (
        "import os, pathlib, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: None)\n"
        "sys.stdin.buffer.read()\n"
        f"pathlib.Path({str(ready)!r}).write_text(str(os.getpgid(0)))\n"
        "time.sleep(30)\n"
    )
    # The pipeline keeps `/bin/sh -c` interposed as the group leader; `cat`
    # and the shell die to the group SIGTERM while the renderer ignores it.
    record = _write_record(tmp_path, f"{_py(renderer)} | cat")

    def _terminate_when_renderer_is_ready() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.01)
        if ready.exists():
            os.kill(os.getpid(), signal.SIGTERM)

    killer = threading.Thread(target=_terminate_when_renderer_is_ready)
    killer.start()
    started = time.monotonic()
    try:
        code = run_usage_capture(
            _payload(), record_path=record, snapshot_path=tmp_path / "snapshot.json"
        )
    finally:
        killer.join()

    # Prompt exit: the wrapper waited out only the bounded escalation, not the
    # renderer's 30s sleep.
    assert time.monotonic() - started < 10
    assert code == 128 + signal.SIGTERM
    # The escalation still fired after the shell was reaped: the renderer's
    # whole process group is gone by the time the wrapper returns.
    renderer_pgid = int(ready.read_text())
    with pytest.raises(ProcessLookupError):
        os.killpg(renderer_pgid, 0)


def test_run_from_stdin_forwards_every_byte_of_an_over_cap_payload(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture stays bounded (oversize rejected), but the renderer sees all bytes."""
    record = _write_record(
        tmp_path,
        _py("import sys; print(len(sys.stdin.buffer.read()))"),
    )
    payload = b'{"pad": "' + b"x" * MAX_STDIN_BYTES + b'"}'
    assert len(payload) > MAX_STDIN_BYTES
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))

    code = run_usage_capture_from_stdin(
        record_path=record, snapshot_path=tmp_path / "snapshot.json"
    )

    captured = capfd.readouterr()
    assert code == 0
    assert captured.out == f"{len(payload)}\n"
    assert captured.err == "dispatch: Claude statusline snapshot rejected\n"
    assert not (tmp_path / "snapshot.json").exists()


def test_run_forwards_sigterm_through_interposed_shell_to_renderer(tmp_path: Path) -> None:
    ready = tmp_path / "renderer-ready"
    signaled = tmp_path / "renderer-signaled"
    renderer = (
        "import pathlib, signal, sys, time\n"
        "def _handle(*_):\n"
        f"    pathlib.Path({str(signaled)!r}).write_text('signaled')\n"
        "    sys.exit(9)\n"
        "signal.signal(signal.SIGTERM, _handle)\n"
        "sys.stdin.buffer.read()\n"
        f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(30)\n"
    )
    # The trailing `; :` keeps `/bin/sh -c` interposed (it cannot exec a
    # compound command), so only process-group signaling can reach the actual
    # renderer — `send_signal` on the shell alone would leave it running.
    record = _write_record(tmp_path, f"{_py(renderer)}; :")

    def _terminate_when_renderer_is_ready() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.01)
        if ready.exists():
            os.kill(os.getpid(), signal.SIGTERM)

    killer = threading.Thread(target=_terminate_when_renderer_is_ready)
    killer.start()
    try:
        code = run_usage_capture(
            _payload(), record_path=record, snapshot_path=tmp_path / "snapshot.json"
        )
    finally:
        killer.join()

    # The forwarded SIGTERM reached the renderer itself, through the shell.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not signaled.exists():
        time.sleep(0.01)
    assert signaled.exists() and signaled.read_text() == "signaled"
    # The interposed shell (the direct child) died to the same group signal.
    assert code == 128 + signal.SIGTERM


def test_run_is_safe_under_concurrent_invocations(tmp_path: Path) -> None:
    record = tmp_path / "statusline.original.json"  # intentionally absent: capture-only
    snapshot_path = tmp_path / "snapshot.json"
    payloads = [
        _payload(five_hour={"used_percentage": float(n), "resets_at": 1_738_425_600})
        for n in range(16)
    ]

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(
            pool.map(
                lambda payload: run_usage_capture(
                    payload, record_path=record, snapshot_path=snapshot_path
                ),
                payloads,
            )
        )

    assert codes == [0] * len(payloads)
    snapshot = read_claude_statusline_snapshot(path=snapshot_path)
    assert snapshot is not None
    assert snapshot.rate_limits.five_hour is not None
    assert not list(snapshot_path.parent.glob("*.tmp"))
