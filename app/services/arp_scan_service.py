from __future__ import annotations

import ipaddress
import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.models.network_models import ArpScanEntry, NetworkAdapterInfo
from app.models.result_models import OperationResult
from app.services.oui_service import OuiService
from app.utils.parser import parse_arp_table
from app.utils.process_utils import no_window_creationflags, windows_console_encoding


class ArpScanService:
    REPLY_PATTERN = re.compile(r"(?:time|시간)\s*[=<]?\s*(\d+)\s*ms", re.IGNORECASE)

    def __init__(self, oui_service: OuiService, logger: logging.Logger) -> None:
        self.oui_service = oui_service
        self.logger = logger

    def list_candidate_subnets(self, adapters: list[NetworkAdapterInfo]) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        for adapter in adapters:
            if not adapter.ipv4 or not adapter.prefix_length:
                continue
            if adapter.ipv4.startswith("127.") or adapter.ipv4.startswith("169.254."):
                continue
            try:
                network = ipaddress.ip_network(f"{adapter.ipv4}/{adapter.prefix_length}", strict=False)
            except ValueError:
                continue
            description = (adapter.interface_description or "").strip()
            if description and description.lower() != adapter.name.strip().lower():
                interface_text = f"{adapter.name} ({description})"
            else:
                interface_text = adapter.name
            label = f"{interface_text} - {network.with_prefixlen}"
            candidates.append((label, network.with_prefixlen))
        return candidates

    def run_scan(
        self,
        subnet_text: str,
        timeout_ms: int = 800,
        max_workers: int = 64,
        progress_callback=None,
        cancel_event=None,
    ) -> OperationResult:
        subnet = self._parse_subnet(subnet_text)
        hosts = [str(host) for host in subnet.hosts()]
        if len(hosts) > 1024:
            raise ValueError("ARP 스캔은 한 번에 1024호스트 이하 대역만 허용합니다. 더 작은 서브넷으로 나눠 주세요.")

        replies: dict[str, float | None] = {}
        worker_count = max(1, min(max_workers, len(hosts) or 1))
        if progress_callback is not None:
            progress_callback.emit(f"[ARP] {subnet.with_prefixlen} 스캔 시작 ({len(hosts)} hosts)")

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(self._ping_once, host, timeout_ms, cancel_event): host
                for host in hosts
            }
            for future in as_completed(future_map):
                if cancel_event is not None and cancel_event.is_set():
                    return OperationResult(False, "ARP 스캔이 취소되었습니다.")

                host = future_map[future]
                reachable, response_ms = future.result()
                if reachable:
                    replies[host] = response_ms
                    if progress_callback is not None:
                        rtt_text = f"{response_ms:.0f} ms" if response_ms is not None else "응답"
                        progress_callback.emit(f"[ARP] {host} 응답 ({rtt_text})")

        arp_entries = self._read_arp_entries(subnet)
        merged_entries = self._merge_entries(replies, arp_entries)
        summary = f"{subnet.with_prefixlen}에서 {len(merged_entries)}개 장비를 찾았습니다."
        details = "\n".join(
            f"{entry.ip_address}\t{entry.mac_address or '-'}\t{entry.vendor or '-'}\t{entry.status_text}"
            for entry in merged_entries
        )
        return OperationResult(True, summary, details, merged_entries)

    def _parse_subnet(self, subnet_text: str) -> ipaddress.IPv4Network:
        text = subnet_text.strip()
        if not text:
            raise ValueError("스캔할 서브넷을 입력해 주세요. 예: 192.168.0.0/24")
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise ValueError("유효한 IPv4 CIDR 형식으로 입력해 주세요. 예: 192.168.0.0/24") from exc
        if network.version != 4:
            raise ValueError("현재 버전에서는 IPv4 서브넷만 지원합니다.")
        return network

    def _ping_once(self, host: str, timeout_ms: int, cancel_event) -> tuple[bool, float | None]:
        if cancel_event is not None and cancel_event.is_set():
            return False, None

        completed = subprocess.run(
            ["ping", host, "-n", "1", "-w", str(timeout_ms)],
            capture_output=True,
            text=True,
            encoding=windows_console_encoding(),
            errors="replace",
            creationflags=no_window_creationflags(),
        )
        output = completed.stdout or completed.stderr
        if completed.returncode != 0 and "TTL=" not in output.upper():
            return False, None

        match = self.REPLY_PATTERN.search(output)
        return True, float(match.group(1)) if match else None

    def _read_arp_entries(self, subnet: ipaddress.IPv4Network) -> list[dict[str, str]]:
        completed = subprocess.run(
            ["arp", "-a"],
            capture_output=True,
            text=True,
            encoding=windows_console_encoding(),
            errors="replace",
            creationflags=no_window_creationflags(),
        )
        raw_output = completed.stdout or completed.stderr
        parsed = parse_arp_table(raw_output)
        results: list[dict[str, str]] = []
        for item in parsed:
            try:
                ip_obj = ipaddress.ip_address(item["ip_address"])
            except ValueError:
                continue
            if ip_obj in subnet:
                results.append(item)
        return results

    def _merge_entries(self, replies: dict[str, float | None], arp_entries: list[dict[str, str]]) -> list[ArpScanEntry]:
        merged: dict[str, ArpScanEntry] = {}

        for ip_address, response_ms in replies.items():
            merged[ip_address] = ArpScanEntry(
                ip_address=ip_address,
                reachable=True,
                response_ms=response_ms,
            )

        for item in arp_entries:
            ip_address = item["ip_address"]
            entry = merged.get(ip_address)
            if entry is None:
                entry = ArpScanEntry(ip_address=ip_address)
                merged[ip_address] = entry
            entry.interface_name = item.get("interface_ip", "")
            entry.mac_address = item.get("mac_address", "")
            entry.arp_type = item.get("arp_type", "")
            entry.vendor = self.oui_service.lookup_vendor(entry.mac_address)

        return sorted(
            merged.values(),
            key=lambda item: tuple(int(part) for part in item.ip_address.split(".")),
        )
