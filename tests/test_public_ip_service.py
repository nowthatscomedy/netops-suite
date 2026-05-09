from __future__ import annotations

import logging
from urllib import error

from app.services.public_ip_service import PublicIpEndpoint, PublicIpService


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


def test_public_ip_service_returns_valid_public_ip(monkeypatch):
    captured_timeout: list[int] = []

    def fake_urlopen(request, timeout):
        captured_timeout.append(timeout)
        return FakeResponse(b"203.0.113.10\n")

    monkeypatch.setattr("app.services.public_ip_service.request.urlopen", fake_urlopen)
    service = PublicIpService(
        logging.getLogger("test-public-ip"),
        endpoints=(PublicIpEndpoint("test", "https://example.test/ip"),),
    )

    result = service.check_public_ip(timeout_seconds=7)

    assert result.success
    assert result.payload["ip"] == "203.0.113.10"
    assert result.payload["version"] == 4
    assert captured_timeout == [7]
    assert "공인 IP: 203.0.113.10" in result.details


def test_public_ip_service_falls_back_to_next_endpoint(monkeypatch):
    calls: list[str] = []

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise error.URLError("offline")
        return FakeResponse(b"2001:db8::1")

    monkeypatch.setattr("app.services.public_ip_service.request.urlopen", fake_urlopen)
    service = PublicIpService(
        logging.getLogger("test-public-ip"),
        endpoints=(
            PublicIpEndpoint("first", "https://first.test/ip"),
            PublicIpEndpoint("second", "https://second.test/ip"),
        ),
    )

    result = service.check_public_ip()

    assert result.success
    assert result.payload["ip"] == "2001:db8::1"
    assert result.payload["version"] == 6
    assert calls == ["https://first.test/ip", "https://second.test/ip"]


def test_public_ip_service_reports_all_failures(monkeypatch):
    def fake_urlopen(req, timeout):
        raise error.URLError("blocked")

    monkeypatch.setattr("app.services.public_ip_service.request.urlopen", fake_urlopen)
    service = PublicIpService(
        logging.getLogger("test-public-ip"),
        endpoints=(PublicIpEndpoint("blocked", "https://blocked.test/ip"),),
    )

    result = service.check_public_ip()

    assert not result.success
    assert "인터넷 연결 안 됨" in result.details
    assert "blocked" in result.details
