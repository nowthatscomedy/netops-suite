from app.utils.parser import parse_netsh_wlan_networks_output, parse_netsh_wlan_output


ENGLISH_INTERFACES = """
Name                   : Wi-Fi
Description            : Intel(R) Wi-Fi 6 AX200 160MHz
State                  : connected
SSID                   : TestNet
BSSID                  : aa:bb:cc:dd:ee:ff
Radio type             : 802.11ax
Channel                : 149
Receive rate (Mbps)    : 1200
Transmit rate (Mbps)   : 1200
Signal                 : 88%
"""

KOREAN_INTERFACES = """
이름                   : Wi-Fi
설명                   : Intel(R) Wi-Fi 6 AX200 160MHz
상태                   : 연결됨
SSID                   : 테스트망
BSSID                  : 11:22:33:44:55:66
라디오 유형            : 802.11ax
채널                   : 44
수신 속도(Mbps)        : 866.7
전송 속도(Mbps)        : 866.7
신호                   : 75%
"""

ENGLISH_NETWORKS = """
Interface name : Wi-Fi
There are 1 networks currently visible.

SSID 1 : TestNet
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Signal             : 88%
         Radio type         : 802.11ax
         Band               : 5 GHz
         Channel            : 149
         Connected stations : 12
         Channel utilization: 34 (%)
"""

KOREAN_NETWORKS = """
인터페이스 이름 : Wi-Fi
보이는 네트워크 1개

SSID 1 : 테스트망
    네트워크 종류          : Infrastructure
    인증                   : WPA2-개인
    암호화                 : CCMP
    BSSID 1                : 11:22:33:44:55:66
         신호              : 75%
         라디오 유형       : 802.11ax
         대역              : 5 GHz
         채널              : 44
         연결된 스테이션   : 3
         채널 사용률       : 12 (%)
"""


def test_parse_netsh_wlan_output_english():
    info = parse_netsh_wlan_output(ENGLISH_INTERFACES)
    assert info.interface_name == "Wi-Fi"
    assert info.ssid == "TestNet"
    assert info.bssid == "aa:bb:cc:dd:ee:ff"
    assert info.radio_type == "802.11ax"
    assert info.channel == "149"
    assert info.signal_percent == 88
    assert info.band == "5 GHz"


def test_parse_netsh_wlan_output_korean():
    info = parse_netsh_wlan_output(KOREAN_INTERFACES)
    assert info.interface_name == "Wi-Fi"
    assert info.ssid == "테스트망"
    assert info.bssid == "11:22:33:44:55:66"
    assert info.radio_type == "802.11ax"
    assert info.channel == "44"
    assert info.signal_percent == 75
    assert info.band == "5 GHz"


def test_parse_netsh_wlan_networks_output_english():
    aps = parse_netsh_wlan_networks_output(ENGLISH_NETWORKS)
    assert len(aps) == 1
    ap = aps[0]
    assert ap.interface_name == "Wi-Fi"
    assert ap.ssid == "TestNet"
    assert ap.bssid == "aa:bb:cc:dd:ee:ff"
    assert ap.radio_standard == "802.11ax"
    assert ap.signal_percent == 88
    assert ap.channel == "149"
    assert ap.connected_stations == 12
    assert ap.channel_utilization_percent == 34


def test_parse_netsh_wlan_networks_output_korean():
    aps = parse_netsh_wlan_networks_output(KOREAN_NETWORKS)
    assert len(aps) == 1
    ap = aps[0]
    assert ap.interface_name == "Wi-Fi"
    assert ap.ssid == "테스트망"
    assert ap.bssid == "11:22:33:44:55:66"
    assert ap.radio_standard == "802.11ax"
    assert ap.signal_percent == 75
    assert ap.channel == "44"
    assert ap.connected_stations == 3
    assert ap.channel_utilization_percent == 12


def test_parse_netsh_wlan_networks_output_extracts_80211_pattern_without_known_label():
    raw_output = """
Interface name : Wi-Fi

SSID 1 : TestNet
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Signal             : 88%
         Wireless mode      : 802.11 ax
         Band               : 5 GHz
         Channel            : 149
"""
    aps = parse_netsh_wlan_networks_output(raw_output)
    assert len(aps) == 1
    assert aps[0].radio_standard == "802.11ax"
