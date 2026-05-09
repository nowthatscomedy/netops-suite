from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass(slots=True)
class NetworkAdapterInfo:
    name: str
    interface_description: str
    mac_address: str
    status: str
    link_speed: str = ""
    interface_index: int = 0
    ipv4: str = ""
    prefix_length: int | None = None
    gateway: str = ""
    dns_servers: list[str] = field(default_factory=list)
    dhcp_enabled: bool = False
    interface_type: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "NetworkAdapterInfo":
        dns_servers = data.get("DNS") or data.get("Dns") or data.get("dns_servers") or []
        if isinstance(dns_servers, str):
            dns_servers = [item.strip() for item in dns_servers.split(",") if item.strip()]
        return cls(
            name=data.get("Name", ""),
            interface_description=data.get("InterfaceDescription", ""),
            mac_address=data.get("MacAddress", ""),
            status=data.get("Status", ""),
            link_speed=data.get("LinkSpeed", ""),
            interface_index=int(data.get("InterfaceIndex", 0) or 0),
            ipv4=data.get("IPv4", "") or "",
            prefix_length=int(data["PrefixLength"]) if data.get("PrefixLength") else None,
            gateway=data.get("Gateway", "") or "",
            dns_servers=list(dns_servers),
            dhcp_enabled=bool(data.get("DhcpEnabled", False)),
            interface_type=data.get("InterfaceType", "") or "",
        )

    def dns_text(self) -> str:
        return ", ".join(self.dns_servers)


@dataclass(slots=True)
class WirelessInfo:
    interface_name: str = ""
    description: str = ""
    state: str = ""
    ssid: str = ""
    bssid: str = ""
    radio_type: str = ""
    channel: str = ""
    band: str = ""
    signal_percent: int | None = None
    rssi: str = ""
    receive_rate_mbps: str = ""
    transmit_rate_mbps: str = ""
    raw_output: str = ""
    parser_message: str = ""

    @property
    def signal_text(self) -> str:
        if self.signal_percent is not None and self.rssi:
            return f"{self.signal_percent}% / {self.rssi} dBm"
        if self.signal_percent is not None:
            return f"{self.signal_percent}%"
        if self.rssi:
            return f"{self.rssi} dBm"
        return "-"


@dataclass(slots=True)
class NearbyAccessPoint:
    interface_name: str = ""
    ssid: str = ""
    bssid: str = ""
    vendor: str = ""
    network_type: str = ""
    authentication: str = ""
    encryption: str = ""
    radio_standard: str = ""
    band: str = ""
    channel: str = ""
    signal_percent: int | None = None
    connected_stations: int | None = None
    channel_utilization_percent: int | None = None
    raw_block: str = ""

    @property
    def signal_text(self) -> str:
        if self.signal_percent is None:
            return "-"
        return f"{self.signal_percent}%"


@dataclass(slots=True)
class ArpScanEntry:
    ip_address: str
    mac_address: str = ""
    vendor: str = ""
    hostname: str = ""
    interface_name: str = ""
    arp_type: str = ""
    reachable: bool = False
    response_ms: float | None = None

    @property
    def status_text(self) -> str:
        if self.reachable:
            return "응답"
        if self.mac_address:
            return "ARP 발견"
        return "미발견"


@dataclass(slots=True)
class TraceHop:
    hop_number: int
    probe_1: str = ""
    probe_2: str = ""
    probe_3: str = ""
    average_ms: float | None = None
    address: str = ""
    hostname: str = ""
    status: str = ""

    @property
    def endpoint_text(self) -> str:
        if self.hostname and self.address and self.hostname != self.address:
            return f"{self.hostname} ({self.address})"
        return self.hostname or self.address or "-"


@dataclass(slots=True)
class OuiRecord:
    prefix: str
    prefix_bits: int
    organization: str
    registry: str


@dataclass(slots=True)
class PublicIperfServer:
    name: str
    host: str
    port_spec: str
    default_port: int
    region: str = ""
    country_code: str = ""
    site: str = ""
    speed: str = ""
    options: str = ""
    source: str = ""
    source_url: str = ""
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.host}|{self.port_spec}"

    @property
    def display_name(self) -> str:
        location = self.site or self.name or self.host
        country = f" ({self.country_code})" if self.country_code and self.country_code not in location else ""
        parts = [f"{location}{country} - {self.host}:{self.port_spec}"]
        if self.region:
            parts.append(self.region)
        if self.speed:
            parts.append(f"{self.speed} Gb/s")
        if self.options:
            parts.append(self.options)
        return " | ".join(parts)

    @property
    def summary_text(self) -> str:
        parts: list[str] = []
        if self.region:
            parts.append(self.region)
        if self.site and self.site != self.name:
            parts.append(self.site)
        if self.speed:
            parts.append(f"속도 {self.speed}")
        if self.options:
            parts.append(f"옵션 {self.options}")
        if self.notes:
            parts.append(self.notes)
        return " | ".join(part for part in parts if part)

    @property
    def option_tokens(self) -> set[str]:
        raw = self.options.strip().lower()
        if not raw:
            return set()
        return {token.strip() for token in re.findall(r"(?:-[a-z0-9]+|ipv6-only|ipv6|udp|reverse)", raw) if token.strip()}

    def supports_option(self, flag: str) -> bool:
        normalized = flag.strip().lower()
        if not normalized:
            return False
        tokens = self.option_tokens
        aliases = {
            "-r": {"-r", "reverse"},
            "-u": {"-u", "udp"},
            "-6": {"-6", "ipv6", "ipv6-only"},
        }.get(normalized, {normalized})
        return any(alias in tokens for alias in aliases)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "host": self.host,
            "port_spec": self.port_spec,
            "default_port": self.default_port,
            "region": self.region,
            "country_code": self.country_code,
            "site": self.site,
            "speed": self.speed,
            "options": self.options,
            "source": self.source,
            "source_url": self.source_url,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PublicIperfServer":
        return cls(
            name=str(data.get("name", "") or ""),
            host=str(data.get("host", "") or ""),
            port_spec=str(data.get("port_spec", "") or ""),
            default_port=int(data.get("default_port", 5201) or 5201),
            region=str(data.get("region", "") or ""),
            country_code=str(data.get("country_code", "") or ""),
            site=str(data.get("site", "") or ""),
            speed=str(data.get("speed", "") or ""),
            options=str(data.get("options", "") or ""),
            source=str(data.get("source", "") or ""),
            source_url=str(data.get("source_url", "") or ""),
            notes=str(data.get("notes", "") or ""),
        )
