from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import dataclass
from urllib import error, request

from app.models.result_models import OperationResult


@dataclass(frozen=True, slots=True)
class PublicIpEndpoint:
    name: str
    url: str


class PublicIpService:
    DEFAULT_ENDPOINTS = (
        PublicIpEndpoint("ipify", "https://api.ipify.org"),
        PublicIpEndpoint("AWS checkip", "https://checkip.amazonaws.com"),
        PublicIpEndpoint("ifconfig.me", "https://ifconfig.me/ip"),
    )

    def __init__(
        self,
        logger: logging.Logger,
        endpoints: tuple[PublicIpEndpoint, ...] | None = None,
    ) -> None:
        self.logger = logger
        self.endpoints = endpoints or self.DEFAULT_ENDPOINTS

    def check_public_ip(self, timeout_seconds: int = 5) -> OperationResult:
        timeout = max(1, min(int(timeout_seconds or 5), 30))
        errors: list[str] = []

        for endpoint in self.endpoints:
            started = time.perf_counter()
            try:
                ip_text = self._request_endpoint(endpoint, timeout)
                address = ipaddress.ip_address(ip_text)
                elapsed_ms = (time.perf_counter() - started) * 1000
                details = "\n".join(
                    [
                        f"공인 IP: {address}",
                        f"IP 버전: IPv{address.version}",
                        f"확인 서비스: {endpoint.name}",
                        f"응답 시간: {elapsed_ms:.0f} ms",
                        "",
                        "외부 HTTPS 확인 서비스에 요청하여 확인한 값입니다.",
                    ]
                )
                self.logger.info("Public IP detected via %s: %s", endpoint.name, address)
                return OperationResult(
                    True,
                    f"공인 IP 확인 완료: {address}",
                    details,
                    {
                        "ip": str(address),
                        "version": address.version,
                        "endpoint": endpoint.name,
                        "elapsed_ms": elapsed_ms,
                    },
                )
            except (ValueError, OSError, error.URLError, TimeoutError) as exc:
                error_text = f"{endpoint.name}: {exc}"
                errors.append(error_text)
                self.logger.warning("Public IP endpoint failed: %s", error_text)

        details = "\n".join(
            [
                "공인 IP를 확인하지 못했습니다.",
                "",
                "가능한 원인:",
                "- 인터넷 연결 안 됨",
                "- 프록시 또는 방화벽에서 외부 확인 서비스 차단",
                "- DNS 또는 HTTPS 연결 문제",
                "",
                "[시도한 서비스]",
                *errors,
            ]
        )
        return OperationResult(False, "공인 IP 확인에 실패했습니다.", details)

    def _request_endpoint(self, endpoint: PublicIpEndpoint, timeout: int) -> str:
        req = request.Request(
            endpoint.url,
            headers={
                "User-Agent": "NetOpsSuite/PublicIP",
                "Accept": "text/plain",
            },
        )
        with request.urlopen(req, timeout=timeout) as response:
            raw_body = response.read(256)
        return raw_body.decode("utf-8", errors="replace").strip()
