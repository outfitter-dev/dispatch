from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from outfitter.dispatch.config import CapturePolicy, capture_policy, config_path, runtime_policy


def test_runtime_policy_reads_local_config(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    config_path().write_text(
        "[policy]\nallow_attached_writes = true\nallow_workspace_setup = true\n"
        "workspace_setup_timeout_seconds = 30\n"
    )

    assert runtime_policy().allow_attached_writes is True
    assert runtime_policy().allow_workspace_setup is True
    assert runtime_policy().workspace_setup_timeout_seconds == 30


def test_runtime_policy_env_overrides_local_config(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    monkeypatch.setenv("DISPATCH_ALLOW_ATTACHED_WRITES", "0")
    config_path().write_text(
        "[policy]\nallow_attached_writes = true\nallow_workspace_setup = true\n"
        "workspace_setup_timeout_seconds = 45\n"
    )

    policy = runtime_policy()
    assert policy.allow_attached_writes is False
    assert policy.allow_workspace_setup is True
    assert policy.workspace_setup_timeout_seconds == 45


def _isolate_capture_env(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    for name in (
        "DISPATCH_CONFIG",
        "DISPATCH_CAPTURE",
        "DISPATCH_RAW_PAYLOAD_RETENTION",
        "DISPATCH_CAPTURE_MAX_TEXT_BYTES",
        "DISPATCH_CAPTURE_MAX_PAYLOAD_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_capture_policy_defaults_to_standard(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _isolate_capture_env(monkeypatch, tmp_path)

    policy = capture_policy()

    assert policy == CapturePolicy()
    assert policy.raw_payloads_enabled is False
    assert policy.retains_any_raw_payloads is False


def test_capture_policy_reads_local_config(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    config_path().write_text(
        "[history]\n"
        'capture = "debug"\n'
        'raw_payload_retention = "all"\n'
        "max_text_bytes = 1024\n"
        "max_payload_bytes = 2048\n"
    )

    policy = capture_policy()

    assert policy.mode == "debug"
    assert policy.raw_payload_retention == "all"
    assert policy.raw_payloads_enabled is True
    assert policy.retains_any_raw_payloads is True
    assert policy.should_retain_raw_payload() is True
    assert policy.max_text_bytes == 1024
    assert policy.max_payload_bytes == 2048


def test_capture_policy_env_overrides_local_config(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    monkeypatch.setenv("DISPATCH_CAPTURE", "minimal")
    monkeypatch.setenv("DISPATCH_RAW_PAYLOAD_RETENTION", "off")
    monkeypatch.setenv("DISPATCH_CAPTURE_MAX_TEXT_BYTES", "512")
    monkeypatch.setenv("DISPATCH_CAPTURE_MAX_PAYLOAD_BYTES", "1024")
    config_path().write_text(
        "[history]\n"
        'capture = "debug"\n'
        'raw_payload_retention = "all"\n'
        "max_text_bytes = 4096\n"
        "max_payload_bytes = 8192\n"
    )

    policy = capture_policy()

    assert policy.mode == "minimal"
    assert policy.raw_payload_retention == "off"
    assert policy.raw_payloads_enabled is False
    assert policy.retains_any_raw_payloads is False
    assert policy.max_text_bytes == 512
    assert policy.max_payload_bytes == 1024


def test_capture_policy_rejects_invalid_mode(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    config_path().write_text('[history]\ncapture = "chatty"\n')

    try:
        capture_policy()
    except ValueError as exc:
        assert "history.capture" in str(exc)
    else:
        raise AssertionError("expected invalid capture mode to fail")


def test_capture_policy_rejects_invalid_byte_caps(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPATCH_HOME", str(tmp_path))
    config_path().write_text("[history]\nmax_payload_bytes = 0\n")

    try:
        capture_policy()
    except ValueError as exc:
        assert "history.max_payload_bytes" in str(exc)
    else:
        raise AssertionError("expected invalid capture byte cap to fail")


def test_capture_policy_error_retention_is_visible() -> None:
    policy = CapturePolicy(raw_payload_retention="errors")

    assert policy.raw_payloads_enabled is True
    assert policy.retains_any_raw_payloads is True
    assert policy.should_retain_raw_payload() is False
    assert policy.should_retain_raw_payload(is_error=True) is True
