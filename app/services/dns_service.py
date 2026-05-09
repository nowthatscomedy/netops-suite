from __future__ import annotations

import logging

from app.models.result_models import OperationResult
from app.services.powershell_service import PowerShellService


class DnsService:
    TYPE_LABELS = {
        1: "A",
        5: "CNAME",
        15: "MX",
        28: "AAAA",
        12: "PTR",
    }
    SECTION_LABELS = {
        1: "Answer",
        2: "Authority",
        3: "Additional",
    }

    def __init__(self, powershell: PowerShellService, logger: logging.Logger) -> None:
        self.powershell = powershell
        self.logger = logger

    def lookup(self, query: str, record_type: str = "A", server: str = "") -> OperationResult:
        if not query.strip():
            raise ValueError("조회할 도메인 또는 IP를 입력해 주세요.")

        query_value = self.powershell.quote(query.strip())
        record_type_value = self.powershell.quote(record_type.strip().upper())
        server_clause = f"-Server {self.powershell.quote(server.strip())}" if server.strip() else ""
        script = f"""
Resolve-DnsName -Name {query_value} -Type {record_type_value} {server_clause} -ErrorAction Stop |
  Select-Object Name, Type, TTL, IPAddress, NameHost, Section, Strings, NameExchange, Preference, PtrDomainName |
  ConvertTo-Json -Depth 5 -Compress
"""
        try:
            data = self.powershell.run_json(script, timeout=20)
        except Exception as exc:
            fallback_command = f"nslookup -type={record_type.strip().upper()} {query.strip()} {server.strip()}".strip()
            fallback = self.powershell.run(fallback_command, timeout=20)
            if fallback.success:
                return OperationResult(True, "DNS 조회가 완료되었습니다.", fallback.stdout)
            raise RuntimeError(f"DNS 조회에 실패했습니다: {exc}") from exc

        if not data:
            return OperationResult(True, "조회 결과가 없습니다.", "응답 레코드가 없습니다.", payload=[])

        records = [data] if isinstance(data, dict) else list(data)
        return OperationResult(
            True,
            "DNS 조회가 완료되었습니다.",
            self._format_records(query.strip(), record_type.strip().upper(), records, server.strip()),
            payload=records,
        )

    def flush_dns_cache(self) -> OperationResult:
        result = self.powershell.run("Clear-DnsClientCache", timeout=15)
        if result.success:
            return OperationResult(True, "DNS 캐시를 비웠습니다.", result.stdout)
        return OperationResult(False, "DNS 캐시 비우기에 실패했습니다.", result.stderr)

    def _format_records(self, query: str, record_type: str, records: list[dict], server: str) -> str:
        lines = [
            f"조회 대상: {query}",
            f"레코드 타입: {record_type}",
            f"사용 DNS 서버: {server or '기본 DNS'}",
            "",
        ]
        for index, record in enumerate(records, start=1):
            record_type_label = self.TYPE_LABELS.get(record.get("Type"), record.get("Type", "-"))
            section_label = self.SECTION_LABELS.get(record.get("Section"), record.get("Section", "-"))
            answer = (
                record.get("IPAddress")
                or record.get("NameHost")
                or record.get("PtrDomainName")
                or record.get("NameExchange")
                or ", ".join(record.get("Strings", []) if isinstance(record.get("Strings"), list) else [])
                or "-"
            )
            preference = record.get("Preference")
            if preference is not None:
                answer = f"{answer} (Preference {preference})"
            lines.extend(
                [
                    f"[{index}] {record_type_label}",
                    f"이름: {record.get('Name', '-')}",
                    f"값 : {answer}",
                    f"TTL: {record.get('TTL', '-')}",
                    f"구간: {section_label}",
                    "",
                ]
            )
        return "\n".join(lines).strip()
