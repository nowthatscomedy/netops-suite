from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Iterator


class SupportLevel(str, Enum):
    """How NetOps Suite can satisfy a user intent."""

    INTERNAL_EXECUTION = "internal_execution"
    UI_GUIDANCE = "ui_guidance"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"

    @property
    def public_label(self) -> str:
        return {
            self.INTERNAL_EXECUTION: "내부 실행 가능",
            self.UI_GUIDANCE: "화면 안내 가능",
            self.PARTIAL: "부분 지원",
            self.UNSUPPORTED: "미지원",
        }[self]

    @classmethod
    def from_value(cls, value: SupportLevel | str) -> SupportLevel:
        if isinstance(value, cls):
            return value
        normalized = str(value or "").strip().casefold().replace("-", "_")
        for member in cls:
            if normalized in {member.value, member.name.casefold()}:
                return member
        raise ValueError(f"Unknown support level: {value!r}")


class FeatureRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @classmethod
    def from_value(cls, value: FeatureRisk | str) -> FeatureRisk:
        if isinstance(value, cls):
            return value
        normalized = str(value or "low").strip().casefold()
        for member in cls:
            if normalized in {member.value, member.name.casefold()}:
                return member
        return cls.LOW


def _strings(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            text
            for value in values
            if (text := str(value or "").strip())
        )
    )


@dataclass(frozen=True, slots=True)
class FeatureCapability:
    """Canonical public contract shared by assistant, UI and user guides.

    Internal tool names are deliberately separate from public labels.  Callers may
    use ``tool_names`` for dispatch, but should only render ``public_context`` from
    the grounding service to users.
    """

    feature_id: str
    public_name: str
    guide_id: str
    route: str
    aliases: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    support_level: SupportLevel = SupportLevel.UI_GUIDANCE
    operations: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    risk: FeatureRisk = FeatureRisk.LOW
    tool_names: tuple[str, ...] = ()
    source_paths: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    success_checks: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("feature_id", "public_name", "guide_id", "route"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"FeatureCapability.{field_name} is required.")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "support_level", SupportLevel.from_value(self.support_level))
        object.__setattr__(self, "risk", FeatureRisk.from_value(self.risk))
        for field_name in (
            "aliases",
            "intents",
            "operations",
            "inputs",
            "constraints",
            "tool_names",
            "source_paths",
            "steps",
            "success_checks",
            "stop_conditions",
        ):
            object.__setattr__(self, field_name, _strings(getattr(self, field_name)))

    @property
    def search_terms(self) -> tuple[str, ...]:
        return _strings((self.public_name, *self.aliases, *self.intents))

    @property
    def has_internal_tool(self) -> bool:
        return bool(self.tool_names)

    @property
    def title(self) -> str:
        """Manifest-friendly alias for the public UI name."""

        return self.public_name


class FeatureCapabilityCatalog:
    """Validated, immutable lookup for public feature capabilities."""

    def __init__(self, capabilities: Iterable[FeatureCapability]) -> None:
        items = tuple(capabilities)
        by_id: dict[str, FeatureCapability] = {}
        for capability in items:
            if not isinstance(capability, FeatureCapability):
                raise TypeError("Capability catalog items must be FeatureCapability objects.")
            if capability.feature_id in by_id:
                raise ValueError(f"Duplicate feature capability: {capability.feature_id}")
            by_id[capability.feature_id] = capability
        self._items = items
        self._by_id = by_id

    def __iter__(self) -> Iterator[FeatureCapability]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def get(self, feature_id: str) -> FeatureCapability | None:
        return self._by_id.get(str(feature_id or "").strip())

    def require(self, feature_id: str) -> FeatureCapability:
        capability = self.get(feature_id)
        if capability is None:
            raise KeyError(feature_id)
        return capability

    def list(self) -> tuple[FeatureCapability, ...]:
        return self._items


_DEFAULT_SOURCE_PATHS: dict[str, tuple[str, ...]] = {
    "getting-started": ("app/main_window.py", "README.md"),
    "installation": ("installer/netops-suite.iss", "app/services/update_service.py"),
    "interface": ("app/ui/tabs/interface_tab.py", "app/services/network_interface_service.py"),
    "diagnostics": ("app/ui/tabs/diagnostics_tab.py", "app/ui/tabs/diagnostics/core.py"),
    "diagnostics.ping": ("app/ui/tabs/diagnostics/ping.py", "app/services/ping_service.py"),
    "diagnostics.tcp": ("app/ui/tabs/diagnostics/tcp.py", "app/services/tcp_check_service.py"),
    "diagnostics.dns": ("app/ui/tabs/diagnostics/dns.py", "app/services/dns_service.py"),
    "diagnostics.trace": ("app/ui/tabs/diagnostics/trace.py", "app/services/trace_service.py"),
    "diagnostics.iperf": ("app/ui/tabs/diagnostics/iperf.py", "app/services/iperf_service.py"),
    "diagnostics.arp": ("app/ui/tabs/diagnostics/tools.py", "app/services/arp_scan_service.py"),
    "diagnostics.subnet": ("app/ui/tabs/diagnostics/tools.py",),
    "diagnostics.oui": ("app/ui/tabs/diagnostics/tools.py", "app/services/oui_service.py"),
    "diagnostics.transfer": ("app/ui/tabs/diagnostics/ftp.py", "app/ui/tabs/diagnostics/scp.py", "app/ui/tabs/diagnostics/tftp.py"),
    "diagnostics.transfer.ftp.client": ("app/ui/tabs/diagnostics/ftp.py", "app/services/ftp_client_service.py"),
    "diagnostics.transfer.ftp.server": ("app/ui/tabs/diagnostics/ftp.py", "app/services/ftp_server_service.py"),
    "diagnostics.transfer.ftps.client": ("app/ui/tabs/diagnostics/ftp.py", "app/services/ftp_client_service.py"),
    "diagnostics.transfer.ftps.server": ("app/ui/tabs/diagnostics/ftp.py", "app/services/ftp_server_service.py"),
    "diagnostics.transfer.sftp.client": ("app/ui/tabs/diagnostics/ftp.py", "app/services/ftp_client_service.py"),
    "diagnostics.transfer.sftp.server": ("app/ui/tabs/diagnostics/ftp.py", "app/services/ftp_server_service.py"),
    "diagnostics.transfer.scp.client": ("app/ui/tabs/diagnostics/scp.py", "app/services/scp_client_service.py"),
    "diagnostics.transfer.scp.server": ("app/ui/tabs/diagnostics/scp.py", "app/services/scp_server_service.py"),
    "diagnostics.transfer.tftp.client": ("app/ui/tabs/diagnostics/tftp.py", "app/services/tftp_service.py"),
    "diagnostics.transfer.tftp.server": ("app/ui/tabs/diagnostics/tftp.py", "app/services/tftp_service.py"),
    "diagnostics.commands": ("app/ui/tabs/diagnostics/tools.py", "app/services/powershell_service.py"),
    "wireless": ("app/ui/tabs/wireless_tab.py", "app/services/wireless_service.py"),
    "inspector": ("app/ui/tabs/inspector_tab.py", "netops_suite/modules/inspector/service.py", "netops_suite/modules/inspector_runtime/core/command_patterns.py"),
    "config-builder": ("app/ui/tabs/config_builder_tab.py", "netops_suite/modules/config_builder/service.py"),
    "assistant": (
        "app/ui/tabs/ai_chat_tab.py",
        "app/models/ai_models.py",
        "app/services/ai_agent_service.py",
        "app/services/ai_model_catalog_service.py",
        "app/assistant/policy.py",
        "app/assistant/capabilities.py",
        "app/assistant/grounding.py",
    ),
    "settings": (
        "app/ui/tabs/settings_tab.py",
        "app/models/ai_models.py",
        "app/services/ai_agent_service.py",
        "app/app_state.py",
    ),
    "data-security": ("app/utils/file_utils.py", "app/ui/tabs/ai_chat_tab.py"),
    "troubleshooting": ("app/services/logging_service.py", "app/ui/tabs/settings_tab.py"),
}


def _feature(
    feature_id: str,
    public_name: str,
    *,
    aliases: tuple[str, ...] = (),
    intents: tuple[str, ...] = (),
    support: SupportLevel = SupportLevel.UI_GUIDANCE,
    operations: tuple[str, ...] = (),
    inputs: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
    risk: FeatureRisk = FeatureRisk.LOW,
    tools: tuple[str, ...] = (),
    source_paths: tuple[str, ...] = (),
    steps: tuple[str, ...] = (),
    success: tuple[str, ...] = (),
    stop: tuple[str, ...] = (),
    guide_id: str | None = None,
    route: str | None = None,
) -> FeatureCapability:
    resolved_guide_id = guide_id or feature_id
    return FeatureCapability(
        feature_id=feature_id,
        public_name=public_name,
        guide_id=resolved_guide_id,
        route=route or resolved_guide_id,
        aliases=aliases,
        intents=intents,
        support_level=support,
        operations=operations,
        inputs=inputs,
        constraints=constraints,
        risk=risk,
        tool_names=tools,
        source_paths=source_paths or _DEFAULT_SOURCE_PATHS.get(resolved_guide_id, ()),
        steps=steps,
        success_checks=success,
        stop_conditions=stop,
    )


# Keep this list aligned with docs/guide_manifest.json.  Operation-level entries
# may share a guide and route with their parent public screen.
DEFAULT_FEATURE_CAPABILITIES: tuple[FeatureCapability, ...] = (
    _feature(
        "getting-started",
        "NetOps Suite 빠른 시작",
        aliases=("처음 사용", "시작 방법", "메뉴 안내"),
        intents=("NetOps 사용법", "어디서 시작", "기능 둘러보기"),
        operations=("주요 화면 찾기", "권한과 결과 저장 위치 확인"),
    ),
    _feature(
        "installation",
        "설치, 실행 및 업데이트",
        aliases=("설치", "업데이트", "실행 오류"),
        intents=("프로그램 설치", "새 버전 확인", "업데이트 확인"),
        operations=("설치 및 실행", "업데이트 확인"),
        risk=FeatureRisk.MEDIUM,
        tools=("update_check",),
    ),
    _feature(
        "interface",
        "네트워크 설정",
        aliases=("네트워크 어댑터", "고정 IP", "DHCP", "DNS 설정"),
        intents=("IP 변경", "게이트웨이 설정", "DNS 변경", "어댑터 조회"),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("현재 어댑터 조회", "고정 IP 설정", "DHCP 전환", "DNS 설정"),
        inputs=("대상 네트워크 어댑터", "IPv4 주소와 프리픽스", "게이트웨이와 DNS 서버"),
        constraints=("설정 변경은 관리자 권한과 사용자 승인이 필요합니다.",),
        risk=FeatureRisk.HIGH,
        tools=("interface_snapshot", "set_static_ip", "set_dhcp", "set_dns", "ip_profiles"),
    ),
    _feature(
        "diagnostics",
        "연결 진단",
        aliases=("네트워크 진단", "빠른 진단", "연결 확인"),
        intents=("인터넷 안됨", "네트워크 문제 확인"),
        operations=("Ping, 포트, DNS, 경로와 로컬 네트워크 상태 진단"),
        risk=FeatureRisk.MEDIUM,
    ),
    _feature(
        "diagnostics.ping",
        "멀티 Ping",
        aliases=("ping", "핑", "ICMP", "손실률", "RTT"),
        intents=("통신 확인", "여러 장비 ping", "외부 연결 확인"),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("단일·다중 대상 Ping", "기본 외부 대상 Ping"),
        inputs=("호스트명 또는 IP 주소"),
        risk=FeatureRisk.MEDIUM,
        tools=("ping", "ping_batch", "external_ping"),
    ),
    _feature(
        "diagnostics.tcp",
        "포트 확인 (TCPing)",
        aliases=("tcping", "TCP 포트", "포트 열림", "방화벽 포트"),
        intents=("포트 연결 확인", "여러 장비 포트 확인"),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("단일·다중 대상 TCP 포트 연결 확인"),
        inputs=("호스트명 또는 IP 주소", "TCP 포트 번호"),
        risk=FeatureRisk.MEDIUM,
        tools=("tcp_check", "tcp_batch"),
    ),
    _feature(
        "diagnostics.dns",
        "DNS 조회 (nslookup)",
        aliases=("DNS", "nslookup", "도메인 조회", "DNS 캐시"),
        intents=("도메인 IP 확인", "레코드 조회", "DNS 캐시 초기화"),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("DNS 레코드 조회", "Windows DNS 캐시 비우기"),
        inputs=("도메인 또는 IP 주소", "레코드 유형"),
        risk=FeatureRisk.MEDIUM,
        tools=("dns_lookup", "dns_flush_cache"),
    ),
    _feature(
        "diagnostics.trace",
        "경로 추적 (tracert/pathping)",
        aliases=("tracert", "traceroute", "pathping", "경로 추적", "홉"),
        intents=("어디서 끊기는지", "네트워크 경로 확인"),
        operations=("tracert 또는 pathping 실행과 홉별 결과 확인"),
        inputs=("호스트명 또는 IP 주소"),
        risk=FeatureRisk.MEDIUM,
    ),
    _feature(
        "diagnostics.iperf",
        "대역폭 측정 (iperf3)",
        aliases=("iperf", "iperf3", "대역폭", "속도 측정", "처리량"),
        intents=("네트워크 성능 측정", "UDP 테스트", "공개 iperf 서버"),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("iperf3 준비 상태 확인", "클라이언트 측정", "공개 서버 목록 조회"),
        inputs=("iperf3 서버 주소와 포트", "TCP 또는 UDP 측정 옵션"),
        constraints=("외부 공개 서버 사용 전 운영 정책과 서버 이용 조건을 확인합니다.",),
        risk=FeatureRisk.HIGH,
        tools=("iperf_status", "iperf_client_test", "public_iperf_cached", "public_iperf_refresh"),
    ),
    _feature(
        "diagnostics.arp",
        "같은 대역 장비 찾기 (ARP 스캔)",
        aliases=("ARP 스캔", "장비 찾기", "IP 충돌", "중복 MAC"),
        intents=("같은 네트워크 장비 검색", "사용 중인 IP 확인"),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("스캔 후보 대역 확인", "선택 대역 ARP 스캔"),
        inputs=("스캔할 로컬 IPv4 대역"),
        constraints=("승인된 로컬 대역만 스캔합니다.",),
        risk=FeatureRisk.HIGH,
        tools=("arp_scan_candidates", "arp_scan"),
    ),
    _feature(
        "diagnostics.subnet",
        "서브넷 계산기",
        aliases=("서브넷", "CIDR", "프리픽스", "브로드캐스트"),
        intents=("네트워크 주소 계산", "호스트 범위 계산"),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("IPv4 네트워크, 브로드캐스트와 호스트 범위 계산"),
        inputs=("IPv4 주소와 프리픽스"),
        tools=("subnet_calculate",),
    ),
    _feature(
        "diagnostics.oui",
        "MAC 제조사 조회 (OUI)",
        aliases=("OUI", "MAC 제조사", "BSSID 제조사", "IEEE 제조사"),
        intents=("MAC 주소 제조사 확인", "OUI 데이터 갱신"),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("MAC 제조사 조회", "OUI 캐시 상태 확인과 갱신"),
        inputs=("MAC 주소 또는 OUI 접두어"),
        tools=("oui_lookup", "oui_cache_summary", "oui_cache_refresh"),
    ),
    _feature(
        "diagnostics.transfer",
        "파일 전송",
        aliases=("파일 업로드", "파일 다운로드", "임시 파일 서버"),
        intents=("장비와 파일 전송", "FTP SFTP SCP TFTP"),
        operations=("FTP·FTPS·SFTP·SCP·TFTP 클라이언트 및 임시 서버 화면 안내"),
        risk=FeatureRisk.HIGH,
    ),
    _feature(
        "diagnostics.transfer.ftp.client",
        "FTP 클라이언트",
        aliases=("FTP 접속", "FTP 업로드", "FTP 다운로드"),
        intents=("FTP 원격 파일 관리",),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("연결, 목록, 업로드, 다운로드, 폴더 생성, 이름 변경과 삭제"),
        inputs=("호스트, 포트, 계정과 원격 경로"),
        risk=FeatureRisk.HIGH,
        tools=("ftp_client_runtime", "ftp_connect", "ftp_disconnect", "ftp_list", "ftp_upload", "ftp_download", "ftp_mkdir", "ftp_rename", "ftp_delete", "ftp_profiles"),
    ),
    _feature(
        "diagnostics.transfer.ftp.server",
        "FTP 임시 서버",
        aliases=("FTP 서버", "임시 FTP", "FTP 공유"),
        intents=("FTP 임시 서버 열기",),
        operations=("FTP 서버 준비 상태와 안전한 공유 설정 안내"),
        constraints=("공유 루트와 쓰기 권한을 최소 범위로 제한합니다.",),
        risk=FeatureRisk.HIGH,
        tools=("ftp_server_runtime",),
    ),
    _feature(
        "diagnostics.transfer.ftps.client",
        "FTPS 클라이언트",
        aliases=("FTPS 접속", "TLS FTP", "FTPS 업로드"),
        intents=("인증서를 확인하며 FTP 전송",),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("FTPS 연결과 파일 관리"),
        inputs=("호스트, 계정, TLS 방식과 인증서 확인 정보"),
        risk=FeatureRisk.HIGH,
        tools=("ftp_client_runtime", "ftp_connect", "ftp_disconnect", "ftp_list", "ftp_upload", "ftp_download"),
    ),
    _feature(
        "diagnostics.transfer.ftps.server",
        "FTPS 임시 서버",
        aliases=("FTPS 서버", "TLS FTP 서버"),
        intents=("암호화된 FTP 임시 서버 열기",),
        operations=("FTPS 서버 준비 상태와 인증서 지문 확인 안내"),
        risk=FeatureRisk.HIGH,
        tools=("ftp_server_runtime",),
    ),
    _feature(
        "diagnostics.transfer.sftp.client",
        "SFTP 클라이언트",
        aliases=("SFTP 접속", "SFTP 업로드", "SFTP 다운로드"),
        intents=("SSH 기반 파일 전송", "호스트 키 확인"),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("SFTP 연결과 파일 관리"),
        inputs=("호스트, 계정, 원격 경로와 호스트 키 확인 정보"),
        risk=FeatureRisk.HIGH,
        tools=("ftp_client_runtime", "ftp_connect", "ftp_disconnect", "ftp_list", "ftp_upload", "ftp_download"),
    ),
    _feature(
        "diagnostics.transfer.sftp.server",
        "SFTP 임시 서버",
        aliases=("SFTP 서버", "SSH 파일 서버"),
        intents=("SFTP 임시 서버 열기",),
        operations=("SFTP 서버 준비 상태와 호스트 키 확인 안내"),
        risk=FeatureRisk.HIGH,
        tools=("ftp_server_runtime",),
    ),
    _feature(
        "diagnostics.transfer.scp.client",
        "SCP 클라이언트",
        aliases=("SCP 업로드", "SCP 다운로드", "SCP 전송"),
        intents=("SSH SCP 파일 복사",),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("SCP 업로드와 다운로드"),
        inputs=("호스트, 계정, 로컬·원격 경로와 호스트 키 확인 정보"),
        risk=FeatureRisk.HIGH,
        tools=("scp_client_runtime", "scp_upload", "scp_download", "scp_profiles"),
    ),
    _feature(
        "diagnostics.transfer.scp.server",
        "SCP 임시 서버",
        aliases=("SCP 서버", "임시 SCP"),
        intents=("SCP 임시 서버 열기",),
        operations=("SCP 서버 준비 상태와 읽기 전용 공유 안내"),
        risk=FeatureRisk.HIGH,
        tools=("scp_client_runtime",),
    ),
    _feature(
        "diagnostics.transfer.tftp.client",
        "TFTP 클라이언트",
        aliases=("TFTP 업로드", "TFTP 다운로드", "UDP 69"),
        intents=("TFTP 파일 전송",),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("TFTP 업로드와 다운로드"),
        inputs=("호스트, 로컬·원격 파일명과 재시도 설정"),
        risk=FeatureRisk.HIGH,
        tools=("tftp_runtime", "tftp_upload", "tftp_download"),
    ),
    _feature(
        "diagnostics.transfer.tftp.server",
        "TFTP 임시 서버",
        aliases=("TFTP 서버", "임시 TFTP"),
        intents=("TFTP 임시 서버 열기",),
        operations=("TFTP 서버 준비 상태와 공유 범위 안내"),
        constraints=("TFTP는 인증과 전송 암호화를 제공하지 않습니다.",),
        risk=FeatureRisk.HIGH,
        tools=("tftp_runtime",),
    ),
    _feature(
        "diagnostics.commands",
        "명령 출력",
        aliases=("ipconfig", "route print", "arp -a", "공인 IP", "DNS 캐시"),
        intents=("내 PC 네트워크 정보", "라우팅 테이블", "ARP 테이블"),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("공인 IP, IP 구성, 라우팅 테이블과 ARP 테이블 조회"),
        risk=FeatureRisk.MEDIUM,
        tools=("public_ip", "ipconfig", "route_print", "arp_table"),
    ),
    _feature(
        "wireless",
        "Wi-Fi 분석",
        aliases=("와이파이", "WiFi", "SSID", "BSSID", "주변 AP", "무선 채널"),
        intents=("무선 신호 확인", "주변 와이파이 검색", "채널 혼잡 확인"),
        support=SupportLevel.INTERNAL_EXECUTION,
        operations=("현재 Wi-Fi 상태 확인", "주변 AP 스캔"),
        tools=("wifi_status", "wifi_scan_nearby"),
    ),
    _feature(
        "inspector",
        "장비 점검 및 백업",
        aliases=("장비 점검", "장비 백업", "점검 백업", "스위치 백업", "SSH 장비 목록", "Telnet 장비 목록"),
        intents=("여러 네트워크 장비 점검", "장비 구성 백업", "Excel 장비 접속"),
        operations=("점검", "백업", "점검+백업", "사용자 명령"),
        inputs=("장비 목록 Excel", "SSH 또는 Telnet 접속 정보", "사용자 명령에서는 TXT·XLS·XLSX·XLSM 명령 파일"),
        constraints=("사용자 명령은 실행 전 반드시 먼저 검증합니다.", "현재 화면에서는 장비 목록 Excel의 개별 행을 선택할 수 없으며 유효한 전체 행이 대상입니다."),
        risk=FeatureRisk.HIGH,
        tools=("inspector_profiles_list",),
        steps=("장비 목록 Excel과 실행 모드를 선택합니다.", "사용자 명령이면 명령 파일을 선택하고 먼저 검증합니다.", "검증된 대상 장비 수와 명령 수를 확인한 뒤 실행합니다."),
        success=("대상 장비별 성공·실패 상태와 생성된 결과 파일을 확인합니다.",),
        stop=("검증 결과의 장비 수나 명령이 예상과 다르면 실행하지 않습니다.",),
    ),
    _feature(
        "inspector.user-commands",
        "장비 점검 및 백업 · 사용자 명령",
        guide_id="inspector",
        route="inspector",
        aliases=("사용자 명령", "커스텀 명령", "호스트네임", "hostname", "스위치 이름 변경", "장비 이름 변경"),
        intents=("장비마다 다른 명령", "여러 장비 호스트네임 수정", "Excel 값으로 명령 치환"),
        support=SupportLevel.PARTIAL,
        operations=("Excel의 장비별 값을 사용자 명령 패턴에 치환", "치환된 명령 사전 검증", "조회 명령 실행"),
        inputs=("장비 목록 Excel에 new_hostname 열을 추가합니다.", "TXT 명령 파일에서 hostname {{ new_hostname }} 형식을 사용합니다."),
        constraints=("실행 전에 먼저 검증하여 대상 장비 수, 명령 수와 치환 변수를 확인합니다.", "현재 화면에서는 장비 목록 Excel의 개별 행을 선택할 수 없으며 유효한 전체 행이 대상입니다.", "프롬프트를 바꾸는 설정 모드 명령과 호스트네임 변경의 안전한 실행은 현재 검증되지 않았으므로 NetOps 내부 기능이 직접 적용한다고 안내하면 안 됩니다.", "비밀번호와 enable_password는 명령 치환 변수로 사용할 수 없습니다."),
        risk=FeatureRisk.HIGH,
        tools=("inspector_profiles_list",),
        steps=("변경 전 백업을 먼저 수행합니다.", "장비 목록 Excel에 new_hostname 열과 장비별 새 이름을 입력합니다.", "TXT 명령 파일에 hostname {{ new_hostname }}를 작성합니다.", "사용자 명령을 선택하고 먼저 검증하여 렌더링 범위를 확인합니다."),
        success=("검증 화면에서 예상한 장비 수, 명령 수와 new_hostname 변수가 표시됩니다.", "현재 버전에서는 설정 변경을 직접 실행 성공으로 간주하지 않고, 승인된 별도 적용 절차에서 재접속과 running-config를 확인합니다."),
        stop=("한 장비라도 변수 누락, 줄바꿈, 접속 실패가 있거나 대상 수가 예상과 다르면 전체 실행을 중단합니다.",),
    ),
    _feature(
        "config-builder",
        "CLI 설정 생성",
        aliases=("CLI 설정", "설정 생성", "Config Builder", "Jinja2", "YAML 프로파일"),
        intents=("장비 설정 파일 만들기", "CSV로 설정 생성", "XLSX로 설정 생성"),
        operations=("YAML 프로파일과 CSV·XLSX 입력으로 CLI 설정 생성"),
        inputs=("YAML 프로파일", "CSV 또는 XLSX 장비 데이터"),
        constraints=("생성 결과는 검토용이며 장비에 자동 적용하지 않습니다.",),
        risk=FeatureRisk.MEDIUM,
        tools=("config_builder_profiles_list",),
    ),
    _feature(
        "assistant",
        "NetOps 어시스턴트",
        aliases=("AI 채팅", "Codex", "Codex CLI", "AI 모델"),
        intents=("Codex 연결 설정", "Codex 로그인", "권한 모드", "대화 내보내기"),
        operations=("NetOps 기능 안내", "승인 기반 내부 도구 연결", "Codex CLI 대화"),
        constraints=("민감 정보와 비밀번호를 프롬프트에 포함하지 않습니다.",),
        risk=FeatureRisk.HIGH,
    ),
    _feature(
        "settings",
        "설정",
        aliases=("앱 설정", "환경 설정", "저장 위치", "도구 연동", "초기화"),
        intents=("NetOps 설정 변경", "iperf3 경로", "OUI 데이터", "업데이트 설정"),
        operations=("저장 위치, 외부 도구, 데이터와 업데이트 설정 관리"),
        risk=FeatureRisk.HIGH,
        tools=("app_paths", "update_check"),
    ),
    _feature(
        "data-security",
        "데이터 및 보안",
        aliases=("보안", "민감 정보", "비밀번호", "로그 보관", "암호화 키"),
        intents=("데이터 저장 위치", "비밀번호 보호", "로그 개인정보"),
        operations=("로컬 데이터와 민감 정보 처리 정책 안내"),
    ),
    _feature(
        "troubleshooting",
        "문제 해결",
        aliases=("오류 해결", "시작 실패", "저장 실패", "로그 확인"),
        intents=("NetOps가 안 됨", "기능 실행 실패", "진단 로그"),
        operations=("오류별 확인 순서와 진단 정보 수집 안내"),
        tools=("app_paths", "artifacts_list"),
    ),
)


DEFAULT_FEATURE_CAPABILITY_CATALOG = FeatureCapabilityCatalog(
    DEFAULT_FEATURE_CAPABILITIES
)


def all_feature_capabilities() -> tuple[FeatureCapability, ...]:
    """Return every bundled public capability in deterministic display order."""

    return DEFAULT_FEATURE_CAPABILITIES


__all__ = [
    "DEFAULT_FEATURE_CAPABILITIES",
    "DEFAULT_FEATURE_CAPABILITY_CATALOG",
    "FeatureCapability",
    "FeatureCapabilityCatalog",
    "FeatureRisk",
    "SupportLevel",
    "all_feature_capabilities",
]
