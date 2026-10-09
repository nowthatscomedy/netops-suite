"""Protocol regressions covering custom, Netmiko and auth-none SSH paths."""
from __future__ import annotations

import time
from unittest.mock import Mock

import paramiko
import pytest

from test_alcatel_legacy_ssh import ssh_server, default_device
from netops_suite.modules.inspector import InspectorService

with InspectorService()._runtime_import_path():
    from core.legacy_ssh import LegacySSHError
    from core.ssh_compat import CompatibleSSHClient, compatible_connect_handler
    import core.ssh_compat as compat_module
    from vendors.nexg import VForceSSHHandler


@pytest.fixture(autouse=True)
def trust_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("NETOPS_SUITE_INSPECTOR_DATA_DIR", str(tmp_path / "trust"))


@pytest.mark.parametrize("vendor,os_name", [
    ("cisco", "ios"), ("juniper", "junos"), ("axgate", "axgate"),
    ("alcatel-lucent", "aos8"), ("handreamnet", "subgate"),
])
def test_vendor_independent_automatic_dss_fallback(tmp_path, vendor, os_name):
    with ssh_server(tmp_path, "dss", attempts=2) as (info, marker):
        target = dict(default_device(info["port"]), vendor=vendor, os=os_name)
        client = CompatibleSSHClient(target)
        try:
            client.connect(hostname=target["ip"], port=target["port"], username="test",
                           password="test-password", timeout=5, allow_agent=False, look_for_keys=False)
            assert client.is_legacy
            assert not (tmp_path / "trust").exists()
            assert marker.exists()
        finally:
            client.close()


@pytest.mark.parametrize("kind", ["rsa", "dss"])
def test_netmiko_uses_real_channels_keepalive_and_selected_driver(tmp_path, kind):
    with ssh_server(tmp_path, kind, attempts=2 if kind == "dss" else 1) as (info, marker):
        target = dict(default_device(info["port"]), vendor="cisco", os="ios")
        with compatible_connect_handler(
            compat_device=target, device_type="terminal_server", host=target["ip"], port=target["port"],
            username="test", password="test-password", conn_timeout=5, keepalive=5,
        ) as connection:
            assert connection.device_type == "terminal_server"
            assert connection.remote_conn_pre.is_legacy == (kind == "dss")
            connection.write_channel("show system\n")
            output = ""
            deadline = time.monotonic() + 5
            while "TEST OUTPUT" not in output and time.monotonic() < deadline:
                output += connection.read_channel()
                time.sleep(0.05)
            assert "TEST OUTPUT" in output
            assert connection.is_alive()
            assert marker.exists()


@pytest.mark.parametrize("kind", ["rsa", "dss"])
def test_nexg_keeps_auth_none_and_interactive_password_login(tmp_path, kind):
    with ssh_server(tmp_path, kind, attempts=2 if kind == "dss" else 1, auth_mode="none") as (info, marker):
        target = dict(default_device(info["port"]), vendor="nexg", os="vforce")
        handler = VForceSSHHandler(target, timeout=5)
        try:
            assert handler.connect()
            assert marker.read_text() == "none"
            assert handler.ssh.is_legacy == (kind == "dss")
        finally:
            handler.disconnect()


def test_explicit_modern_fingerprint_mismatch_blocks_before_authentication(tmp_path):
    with ssh_server(tmp_path, "rsa") as (info, marker):
        target = default_device(info["port"])
        target["ssh_host_key_sha256"] = "SHA256:" + "A" * 43
        client = CompatibleSSHClient(target)
        with pytest.raises(LegacySSHError, match="지문"):
            client.connect(hostname=target["ip"], port=target["port"], username="test",
                           password="test-password", timeout=5, allow_agent=False, look_for_keys=False)
        assert not marker.exists()
        assert not (tmp_path / "trust").exists()


def test_kex_negotiation_failure_attempts_compatibility_once(monkeypatch):
    native = Mock()
    native.connect.side_effect = paramiko.ssh_exception.IncompatiblePeer("no acceptable kex algorithm")
    legacy = Mock()
    legacy.connect.side_effect = LegacySSHError("legacy also incompatible")
    factory = Mock(return_value=legacy)
    monkeypatch.setattr(compat_module, "LegacySSHClient", factory)
    monkeypatch.setattr(compat_module.time, "sleep", lambda _: None)
    client = CompatibleSSHClient(default_device(22), native_factory=lambda: native)
    with pytest.raises(LegacySSHError, match="also incompatible"):
        client.connect(hostname="127.0.0.1", port=22, username="test", password="test", timeout=5)
    assert native.connect.call_count == 1
    assert legacy.connect.call_count == 1
    assert factory.call_count == 1


@pytest.mark.parametrize("device_type", ["cisco_ios", "aruba_os"])
def test_real_netmiko_vendor_session_preparation_and_commands(tmp_path, device_type):
    with ssh_server(tmp_path, "dss", attempts=2) as (info, _):
        target = default_device(info["port"])
        with compatible_connect_handler(
            compat_device=target, device_type=device_type, host=target["ip"], port=target["port"],
            username="test", password="test-password", conn_timeout=5, fast_cli=True,
            session_log=str(tmp_path / "session.log"),
        ) as connection:
            assert connection.remote_conn_pre.is_legacy
            assert connection.check_enable_mode()
            assert "TEST OUTPUT" in connection.send_command("show system", read_timeout=5)


def test_netmiko_explicit_pin_mismatch_is_not_wrapped_as_retryable_timeout(tmp_path):
    with ssh_server(tmp_path, "rsa") as (info, marker):
        target = default_device(info["port"])
        target["ssh_host_key_sha256"] = "SHA256:" + "A" * 43
        with pytest.raises(LegacySSHError, match="지문"):
            compatible_connect_handler(
                compat_device=target, device_type="terminal_server", host=target["ip"], port=target["port"],
                username="test", password="test-password", conn_timeout=5,
            )
        assert not marker.exists()


@pytest.mark.parametrize("kind", ["rsa", "dss"])
def test_old_saved_key_is_ignored_and_never_updated(tmp_path, kind):
    import hashlib

    with ssh_server(tmp_path, kind, attempts=2 if kind == "dss" else 1) as (info, marker):
        target = default_device(info["port"])
        directory = tmp_path / "trust" / "ssh_host_keys"
        directory.mkdir(parents=True)
        digest = hashlib.sha256(f"{target['ip']}:{target['port']}".encode()).hexdigest()
        old_record = directory / (digest + ".json")
        # Even a corrupt old record must not be read or prevent a replacement device connecting.
        old_record.write_text("obsolete key record", encoding="utf-8")
        before = old_record.stat().st_mtime_ns
        client = CompatibleSSHClient(target)
        try:
            client.connect(hostname=target["ip"], port=target["port"], username="test",
                           password="test-password", timeout=5, allow_agent=False, look_for_keys=False)
            assert marker.exists()
            assert client.is_legacy == (kind == "dss")
            assert old_record.read_text(encoding="utf-8") == "obsolete key record"
            assert old_record.stat().st_mtime_ns == before
            assert list(directory.iterdir()) == [old_record]
        finally:
            client.close()


def test_replacement_keys_accepted_without_persistence(tmp_path):
    from core.ssh_compat import OptionalHostKeyPolicy

    policy = OptionalHostKeyPolicy(default_device(22))
    client = paramiko.SSHClient()
    try:
        for _ in range(2):
            policy.missing_host_key(client, "127.0.0.1", paramiko.RSAKey.generate(1024))
        assert not client.get_host_keys()
        assert not (tmp_path / "trust").exists()
    finally:
        client.close()
