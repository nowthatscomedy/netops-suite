from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.models.network_models import (
    NearbyAccessPoint,
    NetworkAdapterInfo,
    OuiRecord,
    PublicIperfServer,
    WirelessInfo,
)
from app.models.result_models import OperationResult, PingResult, TcpCheckResult
from app.utils.parser import parse_port_list, parse_target_entries


class ControlledThreadPool:
    """Queues Qt workers so a test can inspect the busy state before completion."""

    def __init__(self) -> None:
        self.pending: list[object] = []

    def start(self, worker) -> None:
        self.pending.append(worker)

    def release_next(self) -> object:
        if not self.pending:
            raise AssertionError("완료할 대기 작업이 없습니다.")
        worker = self.pending.pop(0)
        worker.run()
        return worker

    def release_all(self) -> None:
        while self.pending:
            self.release_next()

    def waitForDone(self, msecs: int = -1) -> bool:  # noqa: N802 - Qt API
        del msecs
        self.release_all()
        return True


class DeterministicNetworkInterfaceService:
    def __init__(self) -> None:
        self.adapters = [
            NetworkAdapterInfo(
                name="Ethernet QA",
                interface_description="Deterministic Ethernet Adapter",
                mac_address="00-11-22-33-44-55",
                status="Up",
                link_speed="1 Gbps",
                interface_index=7,
                ipv4="192.168.10.42",
                prefix_length=24,
                gateway="192.168.10.1",
                dns_servers=["1.1.1.1", "8.8.8.8"],
                dhcp_enabled=True,
                interface_type="Ethernet",
            )
        ]

    def list_adapters(self) -> list[NetworkAdapterInfo]:
        return list(self.adapters)

    def format_adapter_snapshot(self, adapters) -> str:
        return "\n".join(
            f"{item.name}: {item.status}, {item.ipv4}/{item.prefix_length}"
            for item in adapters
        )

    def set_dhcp(self, interface_name: str) -> OperationResult:
        return OperationResult(True, f"{interface_name}: DHCP 적용 완료")

    def set_static(self, *args, **kwargs) -> OperationResult:
        return OperationResult(True, "수동 IP 적용 완료")

    def apply_profile(self, *args, **kwargs) -> OperationResult:
        return OperationResult(True, "프로파일 적용 완료")


class DeterministicPingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def run_multi_ping(
        self,
        raw_targets: str,
        count: int,
        timeout_ms: int,
        max_workers: int | None = None,
        continuous: bool = False,
        progress_callback=None,
        cancel_event=None,
    ) -> list[PingResult]:
        del max_workers, continuous
        self.calls.append((raw_targets, count, timeout_ms))
        results: list[PingResult] = []
        for index, (name, target) in enumerate(parse_target_entries(raw_targets)):
            result = PingResult(
                name=name,
                target=target,
                success=True,
                status="정상",
                packet_loss=0.0,
                sent=count,
                received=count,
                min_rtt=2.0 + index,
                avg_rtt=3.0 + index,
                max_rtt=4.0 + index,
                last_seen="12:34:56",
            )
            results.append(result)
            if progress_callback is not None:
                progress_callback.emit(
                    {
                        "type": "ping",
                        "result": result,
                        "line": f"[12:34:56] {target}: QA 응답",
                    }
                )
        return results


class DeterministicTcpService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, int]] = []

    def run_multi_check(
        self,
        raw_targets: str,
        raw_ports: str,
        count: int,
        timeout_ms: int,
        max_workers: int | None = None,
        continuous: bool = False,
        progress_callback=None,
        cancel_event=None,
    ) -> list[TcpCheckResult]:
        del max_workers, continuous, cancel_event
        self.calls.append((raw_targets, raw_ports, count, timeout_ms))
        results: list[TcpCheckResult] = []
        for target_index, (name, target) in enumerate(
            parse_target_entries(raw_targets)
        ):
            for port_index, port in enumerate(parse_port_list(raw_ports)):
                elapsed = 5.0 + target_index + port_index
                result = TcpCheckResult(
                    name=name,
                    target=target,
                    port=port,
                    status="열림",
                    sent=count,
                    successful=count,
                    failed=0,
                    packet_loss=0.0,
                    min_response_ms=elapsed,
                    response_ms=elapsed + 0.5,
                    max_response_ms=elapsed + 1.0,
                    last_seen="12:34:57",
                )
                results.append(result)
                if progress_callback is not None:
                    progress_callback.emit(
                        {
                            "type": "tcp",
                            "result": result,
                            "line": (
                                f"[12:34:57] {target}:{port} QA 연결 성공 "
                                f"({elapsed:.2f} ms)"
                            ),
                        }
                    )
        return results


class DeterministicDnsService:
    def lookup(self, query: str, record_type: str, server: str = ""):
        server_text = server or "시스템 기본 DNS"
        return OperationResult(
            True,
            f"{query} {record_type} 조회 완료",
            f"서버: {server_text}\n이름: {query}\nAddress: 203.0.113.10",
        )

    def flush_dns_cache(self):
        return OperationResult(True, "DNS 캐시를 비웠습니다.")


class DeterministicTraceService:
    def run_tracert(self, *args, **kwargs):
        return OperationResult(True, "tracert 완료", "1 192.168.10.1\n2 203.0.113.1")

    def run_pathping(self, *args, **kwargs):
        return OperationResult(True, "pathping 완료", "홉 1 손실 0%")

    def run_ipconfig_all(self):
        return OperationResult(
            True,
            "ipconfig /all 완료",
            "Ethernet QA\nIPv4 Address: 192.168.10.42",
        )

    def run_route_print(self):
        return OperationResult(
            True,
            "route print 완료",
            "0.0.0.0 0.0.0.0 192.168.10.1",
        )

    def run_arp_table(self):
        return OperationResult(
            True,
            "arp -a 완료",
            "192.168.10.1 00-11-22-33-44-55 dynamic",
        )


class DeterministicPublicIpService:
    def check_public_ip(self):
        return OperationResult(
            True,
            "공인 IP 확인 완료",
            "IPv4: 203.0.113.25\n제공자: QA fixture",
        )


class DeterministicOuiService:
    def __init__(self) -> None:
        self.updated = False

    def cache_summary(self) -> str:
        return "QA OUI 캐시 2건"

    def cache_status(self) -> dict[str, object]:
        return {
            "available": True,
            "record_count": 2,
            "updated_at": (
                "2026-07-18T12:00:00+09:00"
                if self.updated
                else "2026-06-01T12:00:00+09:00"
            ),
            "age_days": 0 if self.updated else 47,
            "stale": not self.updated,
            "dataset_version": "qa-oui-version-2" if self.updated else "qa-oui-version-1",
            "version_label": "SHA-256 qaoui0000002" if self.updated else "SHA-256 qaoui0000001",
            "source_name": "IEEE Registration Authority",
            "source_url": "https://standards-oui.ieee.org/",
            "source_updated_at": "Sat, 18 Jul 2026 00:00:00 GMT",
            "sources": {},
            "last_checked_at": "",
        }

    def check_for_updates(self, *args, **kwargs):
        return OperationResult(
            True,
            (
                "OUI 데이터가 최신 상태입니다."
                if self.updated
                else "최신 IEEE OUI 데이터가 있습니다."
            ),
            "QA IEEE 원본 4개 비교 완료",
            {
                "update_available": not self.updated,
                "is_latest": self.updated,
            },
        )

    def split_label_and_mac(self, value: str):
        if "," in value:
            name, mac = value.split(",", 1)
            return name.strip(), mac.strip()
        return value.strip(), value.strip()

    def normalize_mac(self, mac_address: str) -> str:
        return "".join(
            character
            for character in mac_address.upper()
            if character in "0123456789ABCDEF"
        )

    def lookup(self, mac_address: str):
        normalized = self.normalize_mac(mac_address)
        if len(normalized) < 12:
            return None
        organization = (
            "QA Network Devices"
            if normalized.startswith("001122")
            else "QA Wireless Labs"
        )
        return OuiRecord(
            prefix=normalized[:6],
            prefix_bits=24,
            organization=organization,
            registry="MA-L",
        )

    def refresh_cache(self, *args, **kwargs):
        self.updated = True
        return OperationResult(
            True,
            "OUI 데이터를 최신 상태로 업데이트했습니다.",
            "QA OUI 2건",
        )


class DeterministicArpScanService:
    def list_candidate_subnets(self, adapters):
        return [
            (
                f"{adapter.name} - 192.168.10.0/24",
                "192.168.10.0/24",
            )
            for adapter in adapters
        ]

    def run_scan(self, *args, **kwargs):
        return OperationResult(True, "ARP 스캔 완료", payload=[])


class DeterministicWirelessService:
    def get_wireless_info(self) -> WirelessInfo:
        return WirelessInfo(
            interface_name="Wi-Fi QA",
            description="Deterministic Wi-Fi Adapter",
            state="연결됨",
            ssid="QA-Lab-5G",
            bssid="00:11:22:33:44:55",
            radio_type="802.11ax",
            channel="36",
            band="5 GHz",
            signal_percent=86,
            rssi="-48",
            receive_rate_mbps="1200",
            transmit_rate_mbps="1200",
        )

    def scan_nearby_access_points(self, include_oui: bool = True):
        del include_oui
        return [
            NearbyAccessPoint(
                interface_name="Wi-Fi QA",
                ssid="QA-Lab-5G",
                bssid="00:11:22:33:44:55",
                vendor="QA Network Devices",
                authentication="WPA2-Personal",
                encryption="CCMP",
                radio_standard="802.11ax",
                band="5 GHz",
                channel="36",
                signal_percent=86,
                connected_stations=4,
                channel_utilization_percent=18,
            ),
            NearbyAccessPoint(
                interface_name="Wi-Fi QA",
                ssid="QA-Guest",
                bssid="66:77:88:99:AA:BB",
                vendor="QA Wireless Labs",
                authentication="Open",
                encryption="None",
                radio_standard="802.11n",
                band="2.4 GHz",
                channel="6",
                signal_percent=61,
                connected_stations=2,
                channel_utilization_percent=44,
            ),
            NearbyAccessPoint(
                interface_name="Wi-Fi QA",
                ssid="Other-Network",
                bssid="CC:DD:EE:FF:00:11",
                vendor="Other Vendor",
                authentication="WPA3-Personal",
                encryption="CCMP",
                radio_standard="802.11ax",
                band="6 GHz",
                channel="5",
                signal_percent=42,
                connected_stations=1,
                channel_utilization_percent=8,
            ),
        ]


class DeterministicIperfService:
    def executable_details(self):
        return (None, "")

    def managed_install_state(self):
        return {
            "available": False,
            "installed": False,
            "update_available": False,
            "button_enabled": False,
            "action_label": "설정에서 관리",
            "package_id": "qa.iperf3",
            "package_url": "https://example.invalid/iperf3",
        }

    def executable_version(self, executable_path=None):
        del executable_path
        return None

    def managed_package_page(self):
        return "https://example.invalid/iperf3"

    def install_or_update_managed(self, *args, **kwargs):
        return OperationResult(True, "iperf3 QA 설치 완료")

    def run_test(self, *args, **kwargs):
        return OperationResult(True, "iperf3 QA 측정 완료")


class DeterministicPublicIperfService:
    def __init__(self) -> None:
        self.server = PublicIperfServer(
            name="QA Seoul",
            host="iperf.qa.invalid",
            port_spec="5201",
            default_port=5201,
            region="asia",
            site="Seoul",
            country_code="KR",
        )

    def _result(self, from_cache: bool):
        return OperationResult(
            True,
            "QA 서버 목록",
            payload={
                "servers": [self.server],
                "fetched_at": "2026-07-18T00:00:00Z",
                "from_cache": from_cache,
                "stale": False,
            },
        )

    def load_cached_servers(self):
        return self._result(True)

    def fetch_public_servers(self, force_refresh: bool = False):
        del force_refresh
        return self._result(False)


class DeterministicTransferService:
    def runtime_support_status(self, protocol: str = ""):
        suffix = f" {protocol}" if protocol else ""
        return OperationResult(True, f"QA{suffix} 지원")


def install_deterministic_services(state) -> None:
    state.network_interface_service = DeterministicNetworkInterfaceService()
    state.ping_service = DeterministicPingService()
    state.tcp_check_service = DeterministicTcpService()
    state.dns_service = DeterministicDnsService()
    state.trace_service = DeterministicTraceService()
    state.public_ip_service = DeterministicPublicIpService()
    state.oui_service = DeterministicOuiService()
    state.arp_scan_service = DeterministicArpScanService()
    state.wireless_service = DeterministicWirelessService()
    state.iperf_service = DeterministicIperfService()
    state.public_iperf_service = DeterministicPublicIperfService()
    state.ftp_client_service = DeterministicTransferService()
    state.ftp_server_service = DeterministicTransferService()
    state.scp_client_service = DeterministicTransferService()
    state.scp_server_service = DeterministicTransferService()
    state.tftp_service = DeterministicTransferService()


def qa_timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def ensure_test_upload_file(root: Path) -> Path:
    path = root / "qa-upload.txt"
    path.write_text("NetOps Suite offscreen QA\n", encoding="utf-8")
    return path
