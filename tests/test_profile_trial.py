from __future__ import annotations

import struct

import pytest

from test_alcatel_legacy_ssh import ssh_server
from netops_suite.modules.inspector import InspectorService

with InspectorService()._runtime_import_path():
    from core.ssh_diagnostics import (
        SSHProbeError,
        action_hint,
        evaluate,
        parse_kexinit,
        probe_ssh,
        with_action_hint,
    )


@pytest.fixture(autouse=True)
def trust_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("NETOPS_SUITE_INSPECTOR_DATA_DIR", str(tmp_path / "trust"))


def _kexinit(kex, keys, ciphers, macs) -> bytes:
    def name_list(items):
        raw = ",".join(items).encode()
        return struct.pack(">I", len(raw)) + raw

    lists = [kex, keys, ciphers, ciphers, macs, macs, ["none"], ["none"], [], []]
    return bytes([20]) + b"\0" * 16 + b"".join(name_list(item) for item in lists)


MODERN = {
    "kex": ("curve25519-sha256@libssh.org", "diffie-hellman-group14-sha256"),
    "host_keys": ("ssh-ed25519", "rsa-sha2-256"),
    "ciphers": ("aes128-ctr", "aes128-cbc"),
    "macs": ("hmac-sha2-256", "hmac-sha1"),
}


def test_kexinit_parsing_and_verdicts():
    algorithms = parse_kexinit(
        _kexinit(["diffie-hellman-group1-sha1"], ["ssh-dss"], ["3des-cbc"], ["hmac-md5"])
    )
    assert algorithms == {
        "kex": ["diffie-hellman-group1-sha1"],
        "host_keys": ["ssh-dss"],
        "ciphers": ["3des-cbc"],
        "macs": ["hmac-md5"],
    }
    assert evaluate("SSH-2.0-x", algorithms, MODERN).mode == "unsupported"

    old = {"kex": ["diffie-hellman-group14-sha1"], "host_keys": ["ssh-rsa"],
           "ciphers": ["aes128-cbc"], "macs": ["hmac-sha1"]}
    result = evaluate("SSH-2.0-x", old, MODERN)
    assert result.mode == "legacy"
    assert result.modern_missing == ["kex", "host_keys"]
    assert "자동 전환" in result.summary()
    assert "키 교환: diffie-hellman-group14-sha1" in result.details()

    new = {"kex": ["curve25519-sha256@libssh.org"], "host_keys": ["ssh-ed25519"],
           "ciphers": ["aes128-ctr"], "macs": ["hmac-sha2-256"]}
    assert evaluate("SSH-2.0-x", new, MODERN).mode == "modern"


def test_parse_rejects_non_kexinit():
    with pytest.raises(SSHProbeError):
        parse_kexinit(bytes([21]) + b"\0" * 40)


@pytest.mark.parametrize("kind,mode", [("rsa", "modern"), ("dss", "legacy"), ("kex-sha1", "legacy")])
def test_probe_reads_real_server_offer_without_authenticating(tmp_path, kind, mode):
    with ssh_server(tmp_path, kind) as (info, marker):
        result = probe_ssh("127.0.0.1", info["port"], timeout=5)
        assert result.banner.startswith("SSH-2.0-")
        assert result.mode == mode
        assert not marker.exists()


def test_probe_reports_closed_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(SSHProbeError, match="TCP"):
        probe_ssh("127.0.0.1", port, timeout=2)


@pytest.mark.parametrize("message,expected", [
    ("TCP 연결 테스트 실패", "방화벽"),
    ("Error reading SSH protocol banner", "동시 접속"),
    ("SSH 호환성 오류 (자동 재시도 중단): Incompatible ssh peer (no acceptable host key)", "SSH 진단"),
    ("레거시 SSH 인증 실패: 계정 정보를 확인하세요.", "비밀번호"),
    ("SSH host key 지문이 일치하지 않아 인증 전에 중단했습니다.", "ssh_host_key_sha256"),
    ("Pattern not detected: 'switch#' in output.", "device_type"),
])
def test_action_hints_name_the_next_step(message, expected):
    assert expected in action_hint(message)
    assert with_action_hint(message).startswith(message + " / 조치: ")


def test_cancelled_message_has_no_hint():
    assert with_action_hint("작업이 취소되었습니다.") == "작업이 취소되었습니다."


@pytest.mark.parametrize("kind", ["kex-sha1", "dss"])
def test_unsaved_profile_trial_runs_commands_on_old_firmware(tmp_path, kind):
    service = InspectorService(work_dir=tmp_path / "results", user_data_dir=tmp_path / "inspector")
    events = []
    # Probe, TCP check and one direct legacy login: the modern attempt is skipped.
    with ssh_server(tmp_path, kind, attempts=3) as (info, marker):
        result = service.run_profile_trial(
            {
                "ip": "127.0.0.1", "port": info["port"], "vendor": "QA-Korea", "os": "OldOS",
                "username": "test", "password": "test-password", "connection_type": "ssh",
            },
            commands=["show version", "show system"],
            timeout=5,
            progress_callback=events.append,
        )
    assert result.success, result.error
    assert result.probe_mode == "legacy"
    assert [item["command"] for item in result.outputs] == ["show version", "show system"]
    assert all("TEST OUTPUT" in item["output"] for item in result.outputs)
    assert result.session_log_dir and "results" in result.session_log_dir
    assert any("SSH 방식 확인" in event["message"] for event in events)
    assert marker.exists()


def test_profile_trial_failure_carries_hint(tmp_path):
    import socket

    service = InspectorService(work_dir=tmp_path / "results", user_data_dir=tmp_path / "inspector")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    result = service.run_profile_trial(
        {"ip": "127.0.0.1", "port": port, "vendor": "v", "os": "o", "connection_type": "ssh"},
        commands=["show version"],
        timeout=2,
    )
    assert not result.success
    assert "조치:" in result.error
    assert "TCP" in result.probe_summary


def test_profile_trial_requires_device_and_commands(tmp_path):
    service = InspectorService(work_dir=tmp_path / "results", user_data_dir=tmp_path / "inspector")
    with pytest.raises(ValueError, match="명령"):
        service.run_profile_trial({"ip": "10.0.0.1", "vendor": "v", "os": "o"}, commands=[" "])
    with pytest.raises(ValueError, match="IP"):
        service.run_profile_trial({"vendor": "v", "os": "o"}, commands=["show version"])


def test_profile_dialog_trial_fills_samples_and_shows_column_values(qapp, tmp_path, monkeypatch):
    from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog
    from netops_suite.modules.inspector.service import ProfileTrialResult

    service = InspectorService(work_dir=tmp_path / "results", user_data_dir=tmp_path / "inspector")
    calls = []

    def fake_trial(device, **kwargs):
        calls.append((device, kwargs))
        return ProfileTrialResult(
            connection_type="ssh",
            probe_mode="legacy",
            probe_summary="오래된 SSH 방식만 지원하는 장비입니다.",
            outputs=[
                {"command": "show version",
                 "output": "Cisco IOS XE Software, Version 17.12.01\nC9300\nProcessor board ID FOC9999"},
                {"command": "show inventory", "output": "NAME: chassis"},
                {"command": "show running-config", "output": "hostname qa"},
            ],
            session_log_dir=str(tmp_path),
        )

    monkeypatch.setattr(service, "run_profile_trial", fake_trial)
    monkeypatch.setattr(
        "app.ui.dialogs.inspector_profile_dialog.confirm_risky_action", lambda *a, **k: True
    )
    dialog = InspectorProfileDialog(service)
    try:
        monkeypatch.setattr(
            dialog._trial_runner, "start",
            lambda fn, *args, on_result=None, on_finished=None, on_error=None,
            on_progress=None, **kwargs: (on_result(fn(*args, **kwargs)), on_finished()),
        )
        dialog.vendor_edit.setText("Cisco")
        dialog.os_edit.setText("IOS-XE")
        dialog.backup_enabled_check.setChecked(True)
        dialog.backup_command_edit.setText("show running-config")
        dialog.trial_ip_edit.setText("10.0.0.5")
        dialog.trial_username_edit.setText("admin")
        dialog.trial_password_edit.setText("secret")
        dialog.trial_run_button.click()

        device, kwargs = calls[0]
        assert device["ip"] == "10.0.0.5" and device["password"] == "secret"
        assert kwargs["commands"] == ["show version", "show inventory", "show running-config"]
        assert dialog.state["commands"][0]["sample"].startswith("Cisco IOS XE Software")
        result_text = dialog.trial_result_view.toPlainText()
        assert "- OS버전: 17.12.01" in result_text
        assert "- 시리얼번호: FOC9999" in result_text
        assert "오래된 SSH" in result_text
        assert dialog.trial_log_button.isEnabled()
        assert dialog.trial_run_button.isEnabled() and not dialog._trial_running
        assert "명령 3개" in dialog.trial_status_label.text()
    finally:
        dialog._dirty = False
        dialog.close()


def test_profile_dialog_trial_reports_failure_hint(qapp, tmp_path):
    from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog
    from netops_suite.modules.inspector.service import ProfileTrialResult

    dialog = InspectorProfileDialog(InspectorService(user_data_dir=tmp_path / "inspector"))
    try:
        before = dialog.state["commands"][0]["sample"]
        dialog._show_trial_result(ProfileTrialResult(
            connection_type="ssh", error="TCP 연결 테스트 실패 / 조치: 방화벽을 확인하세요."))
        assert "조치:" in dialog.trial_status_label.text()
        assert dialog.state["commands"][0]["sample"] == before
    finally:
        dialog._dirty = False
        dialog.close()
