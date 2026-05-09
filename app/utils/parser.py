from __future__ import annotations

import re

from app.models.network_models import NearbyAccessPoint, TraceHop, WirelessInfo


def parse_target_entries(raw_text: str) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    for line in raw_text.splitlines():
        text = line.strip()
        if not text:
            continue
        if "," in text:
            name, target = [part.strip() for part in text.split(",", 1)]
            targets.append((name or target, target))
        else:
            targets.append((text, text))
    return targets


def parse_port_list(raw_text: str) -> list[int]:
    ports: list[int] = []
    for part in re.split(r"[\s,;]+", raw_text.strip()):
        if not part:
            continue
        if "-" in part:
            start_text, end_text = [token.strip() for token in part.split("-", 1)]
            if not start_text or not end_text:
                raise ValueError(f"포트 범위 형식이 올바르지 않습니다: {part}")
            start_port = int(start_text)
            end_port = int(end_text)
            if start_port > end_port:
                raise ValueError(f"포트 범위 시작값이 끝값보다 클 수 없습니다: {part}")
            if not 1 <= start_port <= 65535 or not 1 <= end_port <= 65535:
                raise ValueError(f"포트 번호는 1~65535 범위여야 합니다: {part}")
            ports.extend(range(start_port, end_port + 1))
            continue

        port = int(part)
        if not 1 <= port <= 65535:
            raise ValueError(f"포트 번호는 1~65535 범위여야 합니다: {port}")
        ports.append(port)

    if not ports:
        raise ValueError("최소 1개 이상의 포트를 입력해 주세요.")
    return sorted(set(ports))


def band_from_channel(channel_text: str, radio_type: str = "") -> str:
    try:
        channel = int(re.findall(r"\d+", channel_text)[0])
    except (IndexError, ValueError):
        return "알 수 없음"

    if 1 <= channel <= 14:
        return "2.4 GHz"
    if 32 <= channel <= 177:
        return "5 GHz"
    if 178 <= channel <= 233:
        return "6 GHz"
    if "6" in radio_type and "ax" in radio_type.lower():
        return "6 GHz(추정)"
    return "알 수 없음"


def normalize_radio_standard(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""

    match = re.search(r"802\.11\s*([0-9A-Za-z/+\-_.]+)", text, re.IGNORECASE)
    if match:
        return f"802.11{match.group(1).strip()}"

    return re.sub(r"\s+", " ", text)


def extract_radio_standard(raw_text: str) -> str:
    match = re.search(r"802\.11\s*([0-9A-Za-z/+\-_.]+)", raw_text or "", re.IGNORECASE)
    if not match:
        return ""
    return f"802.11{match.group(1).strip()}"


def _normalize_label(label: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", label.strip().lower())


INTERFACE_FIELD_MAP: dict[str, str] = {
    "name": "interface_name",
    "이름": "interface_name",
    "description": "description",
    "설명": "description",
    "state": "state",
    "status": "state",
    "상태": "state",
    "ssid": "ssid",
    "bssid": "bssid",
    "apbssid": "bssid",
    "radiotype": "radio_type",
    "phytype": "radio_type",
    "라디오종류": "radio_type",
    "라디오유형": "radio_type",
    "라디오타입": "radio_type",
    "라디오형식": "radio_type",
    "phy유형": "radio_type",
    "phy종류": "radio_type",
    "phy타입": "radio_type",
    "라디오유형": "radio_type",
    "라디오종류": "radio_type",
    "무선유형": "radio_type",
    "무선종류": "radio_type",
    "무선규격": "radio_type",
    "channel": "channel",
    "채널": "channel",
    "band": "band",
    "대역": "band",
    "밴드": "band",
    "signal": "signal",
    "신호": "signal",
    "receiveratembps": "receive_rate_mbps",
    "receiverate": "receive_rate_mbps",
    "수신속도mbps": "receive_rate_mbps",
    "수신속도": "receive_rate_mbps",
    "transmitratembps": "transmit_rate_mbps",
    "transmitrate": "transmit_rate_mbps",
    "전송속도mbps": "transmit_rate_mbps",
    "송신속도mbps": "transmit_rate_mbps",
    "전송속도": "transmit_rate_mbps",
    "송신속도": "transmit_rate_mbps",
    "rssi": "rssi",
}


STATE_MAP = {
    "connected": "연결됨",
    "disconnected": "연결 안 됨",
    "disconnecting": "연결 해제 중",
    "not ready": "준비 안 됨",
    "authenticating": "인증 중",
    "discovering": "검색 중",
    "associating": "연결 시도 중",
    "ad hoc network formed": "애드혹 구성됨",
}


NEARBY_SSID_FIELD_MAP: dict[str, str] = {
    "networktype": "network_type",
    "네트워크종류": "network_type",
    "네트워크유형": "network_type",
    "authentication": "authentication",
    "인증": "authentication",
    "encryption": "encryption",
    "암호화": "encryption",
}


NEARBY_AP_FIELD_MAP: dict[str, str] = {
    "signal": "signal_percent",
    "신호": "signal_percent",
    "radiotype": "radio_standard",
    "phytype": "radio_standard",
    "라디오종류": "radio_standard",
    "라디오유형": "radio_standard",
    "라디오타입": "radio_standard",
    "라디오형식": "radio_standard",
    "phy유형": "radio_standard",
    "phy종류": "radio_standard",
    "phy타입": "radio_standard",
    "라디오유형": "radio_standard",
    "라디오종류": "radio_standard",
    "무선유형": "radio_standard",
    "무선종류": "radio_standard",
    "무선규격": "radio_standard",
    "band": "band",
    "대역": "band",
    "밴드": "band",
    "channel": "channel",
    "채널": "channel",
}


TRACE_HOP_PATTERN = re.compile(
    r"^\s*(?P<hop>\d+)\s+"
    r"(?P<probe1>(?:<\d+|\d+)\s*ms|\*)\s+"
    r"(?P<probe2>(?:<\d+|\d+)\s*ms|\*)\s+"
    r"(?P<probe3>(?:<\d+|\d+)\s*ms|\*)\s*"
    r"(?P<endpoint>.*)$",
    re.IGNORECASE,
)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def localize_wireless_state(value: str) -> str:
    text = value.strip()
    return STATE_MAP.get(text.lower(), text)


def parse_netsh_wlan_output(raw_output: str) -> WirelessInfo:
    info = WirelessInfo(raw_output=raw_output)
    if not raw_output.strip():
        info.parser_message = "netsh 출력이 비어 있습니다."
        return info

    lower_output = raw_output.lower()
    if "there is no wireless interface" in lower_output or "무선 인터페이스가 없습니다" in raw_output:
        info.state = "무선 어댑터 없음"
        info.parser_message = "시스템에서 무선 인터페이스를 찾지 못했습니다."
        return info

    values: dict[str, str] = {}
    for line in raw_output.splitlines():
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        field_name = INTERFACE_FIELD_MAP.get(_normalize_label(label))
        if field_name:
            values[field_name] = value.strip()

    info.interface_name = values.get("interface_name", "")
    info.description = values.get("description", "")
    info.state = localize_wireless_state(values.get("state", ""))
    info.ssid = values.get("ssid", "")
    info.bssid = values.get("bssid", "")
    info.radio_type = normalize_radio_standard(values.get("radio_type", "") or extract_radio_standard(raw_output))
    info.channel = values.get("channel", "")
    info.band = values.get("band", "") or band_from_channel(info.channel, info.radio_type)
    info.receive_rate_mbps = values.get("receive_rate_mbps", "")
    info.transmit_rate_mbps = values.get("transmit_rate_mbps", "")
    info.rssi = values.get("rssi", "").replace("dBm", "").strip()

    signal_match = re.search(r"(\d+)", values.get("signal", ""))
    info.signal_percent = int(signal_match.group(1)) if signal_match else None

    if not any([info.interface_name, info.state, info.ssid, info.bssid, info.channel, info.receive_rate_mbps]):
        info.parser_message = "Wi-Fi 정보를 파싱하지 못했습니다. 원본 출력을 확인해 주세요."
    return info


def parse_arp_table(raw_output: str) -> list[dict[str, str]]:
    current_interface = ""
    entries: list[dict[str, str]] = []

    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        interface_match = re.match(
            r"^\s*(?:Interface|인터페이스)\s*:\s*(?P<ip>(?:\d{1,3}\.){3}\d{1,3})",
            line,
            re.IGNORECASE,
        )
        if interface_match:
            current_interface = interface_match.group("ip")
            continue

        entry_match = re.match(
            r"^\s*(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\s+"
            r"(?P<mac>(?:[0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2})\s+"
            r"(?P<type>[A-Za-z가-힣]+)",
            line,
        )
        if entry_match:
            entries.append(
                {
                    "interface_ip": current_interface,
                    "ip_address": entry_match.group("ip"),
                    "mac_address": entry_match.group("mac").replace(":", "-").upper(),
                    "arp_type": entry_match.group("type"),
                }
            )

    return entries


def parse_netsh_wlan_networks_output(raw_output: str) -> list[NearbyAccessPoint]:
    access_points: list[NearbyAccessPoint] = []
    current_interface = ""
    current_ssid = ""
    ssid_meta: dict[str, str] = {}
    current_ap: NearbyAccessPoint | None = None
    current_block: list[str] = []

    def finalize_ap() -> None:
        nonlocal current_ap, current_block
        if current_ap is None:
            return
        current_ap.raw_block = "\n".join(current_block).strip()
        current_ap.radio_standard = normalize_radio_standard(
            current_ap.radio_standard or extract_radio_standard(current_ap.raw_block)
        )
        current_ap.band = current_ap.band or band_from_channel(current_ap.channel, current_ap.radio_standard)
        access_points.append(current_ap)
        current_ap = None
        current_block = []

    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        interface_match = re.match(
            r"^\s*(?:Interface name|인터페이스 이름)\s*:\s*(.+)$",
            line,
            re.IGNORECASE,
        )
        if interface_match:
            finalize_ap()
            current_interface = interface_match.group(1).strip()
            current_ssid = ""
            ssid_meta = {}
            continue

        ssid_match = re.match(r"^\s*SSID\s+\d+\s*:\s*(.*)$", line, re.IGNORECASE)
        if ssid_match:
            finalize_ap()
            current_ssid = ssid_match.group(1).strip()
            ssid_meta = {}
            continue

        bssid_match = re.match(r"^\s*BSSID\s+\d+\s*:\s*((?:[0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2})", line, re.IGNORECASE)
        if bssid_match:
            finalize_ap()
            current_ap = NearbyAccessPoint(
                interface_name=current_interface,
                ssid=current_ssid or "(숨김 SSID)",
                bssid=bssid_match.group(1).replace("-", ":").lower(),
                network_type=ssid_meta.get("network_type", ""),
                authentication=ssid_meta.get("authentication", ""),
                encryption=ssid_meta.get("encryption", ""),
            )
            current_block = [line.rstrip()]
            continue

        if ":" not in line:
            if current_ap is not None:
                current_block.append(line.rstrip())
            continue

        label, value = line.split(":", 1)
        normalized_label = _normalize_label(label)
        text_value = value.strip()

        if current_ap is None:
            field_name = NEARBY_SSID_FIELD_MAP.get(normalized_label)
            if field_name:
                ssid_meta[field_name] = text_value
            continue

        current_block.append(line.rstrip())
        field_name = NEARBY_AP_FIELD_MAP.get(normalized_label)
        if field_name == "signal_percent":
            match = re.search(r"(\d+)", text_value)
            current_ap.signal_percent = int(match.group(1)) if match else None
        elif field_name == "radio_standard":
            current_ap.radio_standard = normalize_radio_standard(text_value)
        elif field_name == "band":
            current_ap.band = text_value
        elif field_name == "channel":
            current_ap.channel = text_value
        elif normalized_label in {"connectedstations", "연결된스테이션"}:
            match = re.search(r"\d+", text_value)
            current_ap.connected_stations = int(match.group(0)) if match else None
        elif normalized_label in {"channelutilization", "채널사용률"}:
            percent_match = re.search(r"\((\d+)\s*%\)", text_value)
            if percent_match:
                current_ap.channel_utilization_percent = int(percent_match.group(1))
            else:
                match = re.search(r"\d+", text_value)
                current_ap.channel_utilization_percent = int(match.group(0)) if match else None

    finalize_ap()
    return access_points


def summarize_channels(access_points: list[NearbyAccessPoint]) -> str:
    counters: dict[str, dict[str, int]] = {}
    for ap in access_points:
        band = ap.band or "알 수 없음"
        channel = ap.channel or "-"
        counters.setdefault(band, {})
        counters[band][channel] = counters[band].get(channel, 0) + 1

    if not counters:
        return "감지된 주변 AP가 없습니다."

    parts: list[str] = []
    for band in sorted(counters):
        channel_text = ", ".join(
            f"{channel}({count})"
            for channel, count in sorted(counters[band].items(), key=lambda item: item[0])
        )
        parts.append(f"{band}: {channel_text}")
    return " | ".join(parts)


def parse_trace_hop_line(line: str) -> TraceHop | None:
    match = TRACE_HOP_PATTERN.match(line)
    if not match:
        return None

    hop = int(match.group("hop"))
    probe_1 = match.group("probe1").strip()
    probe_2 = match.group("probe2").strip()
    probe_3 = match.group("probe3").strip()
    endpoint = match.group("endpoint").strip()

    probe_values = [_probe_to_ms(value) for value in (probe_1, probe_2, probe_3)]
    numeric_values = [value for value in probe_values if value is not None]
    average_ms = round(sum(numeric_values) / len(numeric_values), 2) if numeric_values else None

    hostname = ""
    address = ""
    status = "정상"

    lower_endpoint = endpoint.lower()
    if any(marker in lower_endpoint for marker in ("request timed out", "transmit failed")) or "요청 시간이 만료" in endpoint:
        status = "시간 초과"
    elif not endpoint and "*" in (probe_1, probe_2, probe_3):
        status = "시간 초과"

    ip_match = IPV4_RE.search(endpoint)
    if ip_match:
        address = ip_match.group(0)
        host_text = endpoint.replace(f"[{address}]", "").replace(address, "").strip()
        hostname = host_text
    elif endpoint:
        hostname = endpoint

    return TraceHop(
        hop_number=hop,
        probe_1=probe_1,
        probe_2=probe_2,
        probe_3=probe_3,
        average_ms=average_ms,
        address=address,
        hostname=hostname,
        status=status,
    )


def parse_trace_hops(raw_output: str) -> list[TraceHop]:
    hops: list[TraceHop] = []
    for line in raw_output.splitlines():
        hop = parse_trace_hop_line(line)
        if hop is not None:
            hops.append(hop)
    return hops


def _probe_to_ms(value: str) -> float | None:
    if "*" in value:
        return None
    match = re.search(r"(\d+)", value)
    if not match:
        return None
    return float(match.group(1))
