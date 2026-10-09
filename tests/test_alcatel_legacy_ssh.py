from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock

import paramiko
import pandas as pd
import pytest

from netops_suite.modules.inspector import InspectorService

with InspectorService()._runtime_import_path():
    from core.legacy_ssh import (
        LegacySSHClient, LegacySSHError, legacy_python, legacy_requested, validate_legacy_device,
    )
    from core.validator import validate_device_info
    import core.inspector as inspector_module
    from vendors.alcatel_lucent import AlcatelLucentHandler
    import core.ssh_compat as compat_module


def device(**overrides):
    return dict({"ip": "127.0.0.1", "port": 22, "vendor": "alcatel-lucent", "os": "aos6",
                 "connection_type": "ssh", "username": "test", "password": "test-password",
                 "legacy_ssh": True, "ssh_host_key_sha256": "SHA256:" + "A" * 43}, **overrides)


@pytest.fixture(autouse=True)
def isolated_trust_store(tmp_path, monkeypatch):
    monkeypatch.setenv("NETOPS_SUITE_INSPECTOR_DATA_DIR", str(tmp_path / "trust"))


@pytest.mark.parametrize("value", ["", False, 0, None, float("nan"), "FALSE"])
def test_legacy_is_off_by_default(value):
    assert not legacy_requested({"legacy_ssh": value})


@pytest.mark.parametrize("overrides", [
    {"connection_type": "telnet"},
    {"legacy_ssh": "maybe"}, {"ssh_host_key_sha256": "invalid"},
])
def test_legacy_scope_and_fingerprint_are_required(overrides):
    with pytest.raises(LegacySSHError):
        validate_legacy_device(device(**overrides))


def test_preflight_reports_missing_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("NETOPS_LEGACY_SSH_PYTHON", str(tmp_path / "missing.exe"))
    valid, error = validate_device_info(device())
    assert not valid and "런타임" in error


@contextmanager
def ssh_server(tmp_path, kind, attempts=1, auth_mode="password"):
    try:
        python = legacy_python()
    except LegacySSHError:
        if os.environ.get("CI"):
            raise
        pytest.skip("Run scripts/setup_legacy_ssh.py for real SSH handshake regression tests")
    marker = tmp_path / (kind + "-auth.txt")
    script = Path(__file__).parent / "support" / "legacy_ssh_server.py"
    process = subprocess.Popen(
        [str(python), "-I", "-u", str(script), kind, str(marker), str(attempts), auth_mode],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(process.stdout.readline)
            try:
                info = json.loads(future.result(timeout=15))
            except Exception:
                process.kill()
                raise
        yield info, marker
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()


def connect_client(client, port):
    client.connect(hostname="127.0.0.1", port=port, username="test", password="test-password",
                   timeout=5, allow_agent=False, look_for_keys=False)


@pytest.mark.parametrize("kind,legacy", [("dss", True), ("rsa", False), ("both", True)])
def test_real_handshake_and_alcatel_commands(tmp_path, kind, legacy):
    with ssh_server(tmp_path, kind) as (info, marker):
        handler = AlcatelLucentHandler(device(
            port=info["port"], legacy_ssh=legacy, ssh_host_key_sha256=info["fingerprint"]), timeout=5)
        try:
            assert handler.connect()
            assert "TEST OUTPUT" in handler.send_command("show system", timeout=0.2)
            assert marker.exists()
            if legacy:
                assert handler.ssh.host_key_type == ("ssh-dss" if kind == "dss" else "ssh-rsa")
                child = handler.ssh.process
            else:
                assert isinstance(handler.ssh.client, paramiko.SSHClient)
        finally:
            handler.disconnect()
        if legacy:
            assert child.poll() is not None
    assert int(paramiko.__version__.split(".")[0]) >= 5
    assert not hasattr(paramiko, "DSSKey")


def test_native_paramiko_does_not_silently_enable_dss(tmp_path):
    with ssh_server(tmp_path, "dss") as (info, marker):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            with pytest.raises(paramiko.ssh_exception.IncompatiblePeer, match="host key"):
                connect_client(client, info["port"])
            assert not marker.exists()
        finally:
            client.close()


def test_wrong_fingerprint_stops_before_password_authentication(tmp_path):
    with ssh_server(tmp_path, "dss") as (info, marker):
        client = LegacySSHClient(device())
        with pytest.raises(LegacySSHError, match="지문"):
            connect_client(client, info["port"])
        assert not marker.exists()
        assert client.process is None


def test_legacy_worker_exit_is_bounded_and_closed(tmp_path):
    with ssh_server(tmp_path, "dss") as (info, _):
        client = LegacySSHClient(device(ssh_host_key_sha256=info["fingerprint"]))
        connect_client(client, info["port"])
        process = client.process
        process.kill()
        process.wait(timeout=5)
        started = time.monotonic()
        try:
            with pytest.raises(LegacySSHError):
                client.invoke_shell(width=80, height=24)
        finally:
            client.close()
        assert time.monotonic() - started < 7


def test_wrong_runtime_version_is_rejected(monkeypatch):
    monkeypatch.setenv("NETOPS_LEGACY_SSH_PYTHON", sys.executable)
    client = LegacySSHClient(device())
    with pytest.raises(LegacySSHError, match="3.5.1"):
        connect_client(client, 22)
    assert client.process is None


@pytest.mark.parametrize("error", [LegacySSHError("pinned host key mismatch"),
                                 paramiko.ssh_exception.IncompatiblePeer("no acceptable host key")])
def test_deterministic_ssh_failure_is_not_retried(tmp_path, monkeypatch, error):
    monkeypatch.chdir(tmp_path)
    inspector = inspector_module.NetworkInspector("test.xlsx", inspection_only=True, max_retries=3)
    monkeypatch.setattr(inspector, "_test_tcping", lambda *_: True)
    handler = Mock()
    handler.connect.side_effect = error
    factory = Mock(return_value=handler)
    monkeypatch.setattr(inspector_module, "get_custom_handler", factory)
    _, result = inspector._connect_to_device(device(), inspection_mode=True, backup_mode=False)
    assert "error" in result
    assert "자동 재시도 중단" in result["error"]
    assert handler.connect.call_count == 1


def test_modern_and_legacy_connections_can_run_concurrently(tmp_path):
    with ssh_server(tmp_path, "dss") as (old, _), ssh_server(tmp_path, "rsa") as (modern, _):
        def run(info, legacy):
            handler = AlcatelLucentHandler(device(port=info["port"], legacy_ssh=legacy,
                                                  ssh_host_key_sha256=info["fingerprint"]), timeout=5)
            try:
                assert handler.connect()
                return handler.send_command("show system", timeout=0.2)
            finally:
                handler.disconnect()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, old, True), pool.submit(run, modern, False)]
            assert all("TEST OUTPUT" in f.result(timeout=15) for f in futures)


def test_authentication_failure_does_not_leak_password(tmp_path):
    with ssh_server(tmp_path, "dss") as (info, marker):
        client = LegacySSHClient(device(ssh_host_key_sha256=info["fingerprint"]))
        with pytest.raises(LegacySSHError, match="인증 실패") as exc:
            client.connect(hostname="127.0.0.1", port=info["port"], username="test", password="private-wrong-password",
                           timeout=5, allow_agent=False, look_for_keys=False)
        assert "private-wrong-password" not in str(exc.value)
        assert marker.exists()
        assert client.process is None


def test_legacy_option_preserves_selected_netmiko_driver(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inspector = inspector_module.NetworkInspector("test.xlsx", inspection_only=True)
    profile = replace(inspector._resolve_device_profile(device()),
                      handler_overrides={"handler_type": "netmiko"},
                      connection_overrides={"ssh": "alcatel_aos"})
    monkeypatch.setattr(inspector, "_resolve_device_profile", lambda _: profile)
    monkeypatch.setattr(inspector, "_test_tcping", lambda *_: True)
    factory = Mock(side_effect=LegacySSHError("expected stop"))
    forbidden = Mock(side_effect=AssertionError("legacy routing escaped"))
    monkeypatch.setattr(inspector_module, "get_custom_handler", forbidden)
    monkeypatch.setattr(inspector_module, "ConnectHandler", factory)
    _, result = inspector._connect_to_device(device())
    assert "expected stop" in result["error"]
    assert factory.call_count == 1
    assert factory.call_args.kwargs["compat_device"]["legacy_ssh"] is True
    assert factory.call_args.kwargs["device_type"] == "alcatel_aos"
    forbidden.assert_not_called()


def test_excel_inventory_preserves_per_device_legacy_options(tmp_path):
    path = tmp_path / "inventory.xlsx"
    pd.DataFrame([
        device(ip="192.0.2.1"),
        device(ip="192.0.2.2", legacy_ssh="", ssh_host_key_sha256=""),
    ]).to_excel(path, index=False)
    service = InspectorService(work_dir=tmp_path, user_data_dir=tmp_path / "data")
    devices = service.load_inventory(str(path))
    assert legacy_requested(devices[0])
    assert devices[0]["ssh_host_key_sha256"] == "SHA256:" + "A" * 43
    assert not legacy_requested(devices[1])


def default_device(port):
    result = device(port=port)
    result.pop("legacy_ssh")
    result.pop("ssh_host_key_sha256")
    return result


def test_aos665_dss_automatically_connects_without_saving_key(tmp_path):
    with ssh_server(tmp_path, "dss", attempts=4) as (info, marker):
        target = default_device(info["port"])
        for _ in range(2):
            handler = AlcatelLucentHandler(target, timeout=5)
            try:
                assert handler.connect()
                assert handler.ssh.is_legacy
                assert "TEST OUTPUT" in handler.send_command("show system", timeout=0.2)
                assert not (tmp_path / "trust").exists()
            finally:
                handler.disconnect()
        assert marker.exists()


def test_aos672_rsa_connects_without_saving_key_or_using_legacy(tmp_path):
    with ssh_server(tmp_path, "rsa") as (info, _):
        target = default_device(info["port"])
        handler = AlcatelLucentHandler(target, timeout=5)
        try:
            assert handler.connect()
            assert isinstance(handler.ssh.client, paramiko.SSHClient)
            assert not (tmp_path / "trust").exists()
        finally:
            handler.disconnect()


@pytest.mark.parametrize("overrides", [{"legacy_ssh": False}])
def test_explicit_optout_does_not_fallback(tmp_path, overrides):
    with ssh_server(tmp_path, "dss") as (info, marker):
        target = dict(default_device(info["port"]), **overrides)
        handler = AlcatelLucentHandler(target, timeout=5)
        with pytest.raises(paramiko.ssh_exception.IncompatiblePeer):
            handler.connect()
        assert not marker.exists()
        assert not (tmp_path / "trust").exists()


@pytest.mark.parametrize("error", [paramiko.AuthenticationException("authentication failed"),
                                 paramiko.SSHException("Error reading SSH protocol banner")])
def test_other_connection_errors_do_not_trigger_automatic_downgrade(monkeypatch, error):
    client = Mock()
    client.connect.side_effect = error
    monkeypatch.setattr(paramiko, "SSHClient", Mock(return_value=client))
    forbidden = Mock(side_effect=AssertionError("unexpected compatibility fallback"))
    monkeypatch.setattr(compat_module, "LegacySSHClient", forbidden)
    handler = AlcatelLucentHandler(default_device(22), timeout=5)
    with pytest.raises(type(error)):
        handler.connect()
    forbidden.assert_not_called()


