"""Shared SSH negotiation and isolated compatibility fallback."""
from __future__ import annotations

import base64
import hashlib
import logging
import time

import paramiko

from core.legacy_ssh import (
    LegacySSHClient, LegacySSHError, automatic_legacy_allowed,
    optional_fingerprint, validate_legacy_device,
)

logger = logging.getLogger(__name__)


class OptionalHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, device):
        self.expected = optional_fingerprint(device)

    def missing_host_key(self, client, hostname, key):
        if not self.expected:
            return
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if self.expected != fingerprint:
            raise LegacySSHError("SSH host key 지문이 일치하지 않아 인증 전에 중단했습니다.")


class NoneAuthStrategy:
    def __init__(self, username):
        self.username = username

    def authenticate(self, transport):
        transport.auth_none(self.username)


class CompatibleSSHClient:
    """Keep driver shell handling intact; change only the SSH connection backend."""

    def __init__(self, device, session_log_file=None, native_factory=None, auth_mode="password", record=None):
        self.device = device
        self.session_log_file = session_log_file
        self.native_factory = native_factory or paramiko.SSHClient
        self.client = None
        self.auth_mode = auth_mode
        self.record = record

    @property
    def is_legacy(self):
        return isinstance(self.client, LegacySSHClient)

    def __getattr__(self, name):
        return getattr(self.client, name)

    def _record(self, message):
        logger.info("%s: %s", self.device["ip"], message)
        if self.record:
            self.record(message)
        if self.session_log_file:
            with open(self.session_log_file, "a", encoding="utf-8") as stream:
                stream.write(message + "\n")

    def connect(self, **kwargs):
        kwargs.setdefault("auth_mode", self.auth_mode)
        explicit = validate_legacy_device(self.device)
        allowed = automatic_legacy_allowed(self.device)
        try:
            if explicit:
                self.client = LegacySSHClient(self.device, automatic=True)
                self.client.connect(**kwargs)
            else:
                self.client = self.native_factory()
                self.client.set_missing_host_key_policy(OptionalHostKeyPolicy(self.device))
                native_args = dict(kwargs)
                if native_args.pop("auth_mode", "password") == "none":
                    native_args["auth_strategy"] = NoneAuthStrategy(native_args["username"])
                try:
                    self.client.connect(**native_args)
                except paramiko.ssh_exception.IncompatiblePeer as exc:
                    algorithm_failure = any(message in str(exc).lower() for message in (
                        "no acceptable host key", "no acceptable kex algorithm",
                        "no acceptable ciphers", "no acceptable macs",
                    ))
                    if not allowed or not algorithm_failure:
                        raise
                    self.client.close()
                    time.sleep(1)
                    self._record("SSH 알고리즘 협상 실패: 격리된 레거시 SSH로 한 번 전환")
                    self.client = LegacySSHClient(self.device, automatic=True)
                    self.client.connect(**kwargs)
            if self.is_legacy:
                self._record("레거시 SSH 연결: " + self.client.host_key_type)
        except Exception:
            self.close()
            raise

    def invoke_shell(self, **kwargs):
        return self.client.invoke_shell(**kwargs)

    def get_transport(self):
        return self.client.get_transport()

    def close(self):
        if self.client is not None:
            self.client.close()


def compatible_connect_handler(*, compat_device=None, **params):
    """Keep the selected Netmiko driver and its command/session preparation logic."""
    from netmiko.ssh_dispatcher import ssh_dispatcher

    driver = ssh_dispatcher(params["device_type"])
    device = compat_device or {
        "ip": params.get("host") or params.get("ip"), "port": params.get("port", 22),
        "connection_type": "ssh",
    }

    class CompatibleDriver(driver):
        def _build_ssh_client(self):
            from netmiko.ssh_auth import SSHClient_noauth
            native = super()._build_ssh_client()
            mode = "none" if isinstance(native, SSHClient_noauth) else "password"
            record = (lambda message: self.session_log.write(message + "\n")) if self.session_log else None
            return CompatibleSSHClient(device, native_factory=lambda: native, auth_mode=mode, record=record)

        def establish_connection(self, *args, **kwargs):
            # Netmiko asserts the concrete Transport type when enabling keepalive.
            # Apply it through the connection adapter so isolated transports work too.
            keepalive = self.keepalive
            self.keepalive = 0
            try:
                super().establish_connection(*args, **kwargs)
                if keepalive and self.protocol == "ssh":
                    self.remote_conn_pre.get_transport().set_keepalive(keepalive)
            finally:
                self.keepalive = keepalive

        def is_alive(self):
            client = getattr(self, "remote_conn_pre", None)
            if self.protocol == "ssh" and isinstance(client, CompatibleSSHClient) and client.is_legacy:
                try:
                    self.write_channel(chr(0))
                    return client.get_transport().is_active()
                except (OSError, EOFError, LegacySSHError):
                    return False
            return super().is_alive()

    return CompatibleDriver(**params)
