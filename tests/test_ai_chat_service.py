from __future__ import annotations

import json
import re
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QMimeData, QProcess, Qt, QUrl
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QLabel,
    QComboBox,
    QListWidget,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QStyle,
    QStyleOptionComboBox,
    QTabWidget,
    QWidget,
)

from app.ui.tabs.ai_chat_tab import AiChatTab
from app.models.ftp_models import FtpProfile
from app.models.ai_models import AiModelCatalog, AiModelDescriptor, AiProviderConfig, normalize_ai_chat_config
from app.models.network_models import NetworkAdapterInfo
from app.models.profile_models import IPProfile
from app.models.result_models import OperationResult
from app.models.scp_models import ScpProfile
from app.services import ai_agent_service as ai_service
from app.services.ai_agent_service import (
    build_chat_invocation,
    build_help_invocation,
    decode_cli_output,
    diagnose_cli_error,
    extra_arg_options_from_help,
    extract_error_from_cli_line,
    extract_text_from_cli_line,
    is_blocking_cli_configuration_error,
    model_options_for_provider,
    parse_cli_help_options,
    plan_netops_chat_action,
    repair_cli_configuration_error,
    resolve_provider_program,
    should_ignore_cli_output_text,
    split_codex_model_cache_warning,
)


def _show_chat_tab(qapp, tab: AiChatTab, *, width: int = 900, height: int = 720) -> None:
    tab.resize(width, height)
    tab.show()
    qapp.processEvents()
    tab.message_container.layout().activate()
    tab.message_layout.activate()
    qapp.processEvents()


def _message_bubbles(tab: AiChatTab) -> list[QWidget]:
    bubbles: list[QWidget] = []
    for index in range(tab.message_layout.count()):
        widget = tab.message_layout.itemAt(index).widget()
        if widget is not None:
            bubbles.append(widget)
    return bubbles


def _message_body_widget(bubble: QWidget) -> QWidget:
    layout = bubble.layout()
    assert layout is not None
    widgets = [layout.itemAt(index).widget() for index in range(layout.count())]
    body_widgets = [widget for widget in widgets if widget is not None]
    assert body_widgets
    return body_widgets[-1]


def _widget_visible_text(widget: QWidget) -> str:
    reader = getattr(widget, "toPlainText", None)
    if callable(reader):
        return str(reader())
    label_text = getattr(widget, "text", None)
    if callable(label_text):
        return str(label_text())
    return "\n".join(_widget_visible_text(child) for child in widget.findChildren(QWidget))


def _assert_message_container_contains(tab: AiChatTab, bubble: QWidget) -> None:
    margins = tab.message_layout.contentsMargins()
    bubble_bottom = bubble.geometry().bottom() + margins.bottom() + 1
    assert bubble.height() > 0
    assert bubble_bottom > tab.transcript_scroll.viewport().height()
    assert tab.message_container.minimumHeight() >= bubble_bottom


def _assert_outer_transcript_scrolls(tab: AiChatTab) -> None:
    scrollbar = tab.transcript_scroll.verticalScrollBar()
    assert scrollbar.maximum() > 0
    assert scrollbar.value() == scrollbar.maximum()


def _assert_body_has_no_internal_scroll(body: QWidget) -> None:
    for bar_getter_name in ("verticalScrollBar", "horizontalScrollBar"):
        bar_getter = getattr(body, bar_getter_name, None)
        if callable(bar_getter):
            scrollbar = bar_getter()
            assert scrollbar.maximum() == 0
            assert scrollbar.isVisible() is False

    assert all(scrollbar.maximum() == 0 for scrollbar in body.findChildren(QScrollBar))


def test_ai_chat_config_normalizes_known_providers_without_secret_fields():
    config = normalize_ai_chat_config(
        {
            "active_provider": "claude",
            "auto_export": True,
            "providers": {
                "codex": {
                    "enabled": False,
                    "command_path": "codex-dev",
                    "model": "gpt-5",
                    "reasoning_effort": "xhigh",
                    "speed": "fast",
                    "access_token": "must-not-survive",
                }
            },
        }
    )

    assert config["active_provider"] == "claude"
    assert config["auto_export"] is True
    assert config["providers"]["codex"]["enabled"] is False
    assert config["providers"]["codex"]["command_path"] == "codex-dev"
    assert config["providers"]["codex"]["reasoning_effort"] == "xhigh"
    assert config["providers"]["codex"]["speed"] == "fast"
    assert "access_token" not in config["providers"]["codex"]
    assert set(config["providers"]) == {"codex", "claude", "gemini"}


def test_codex_chat_invocation_uses_stdin_and_keeps_model_after_exec_prefix():
    config = AiProviderConfig(key="codex", command_path="codex", model="gpt-5", role_prompt="review")

    invocation = build_chat_invocation(config, "hello", working_dir="C:/repo")

    assert Path(invocation.program).name.lower() in {"codex", "codex.exe", "codex.cmd"}
    assert invocation.args == ["-a", "never", "-s", "read-only", "exec", "--json", "--model", "gpt-5", "-"]
    assert "Agent role:\nreview" in invocation.stdin_text
    assert "User request:\nhello" in invocation.stdin_text
    assert invocation.working_dir == "C:/repo"


def test_codex_help_invocation_targets_exec_help():
    invocation = build_help_invocation(AiProviderConfig(key="codex", command_path="codex"), working_dir="C:/repo")

    assert invocation.args == ["-a", "never", "-s", "read-only", "exec", "--help"]
    assert invocation.working_dir == "C:/repo"


def test_codex_windowsapps_alias_command_uses_real_candidate(monkeypatch):
    local_codex = r"C:\Users\me\AppData\Local\OpenAI\Codex\bin\codex.exe"
    config = AiProviderConfig(
        key="codex",
        command_path=r"C:\Program Files\WindowsApps\OpenAI.Codex_x64__2p2nqsd0c76g0\codex.exe",
    )

    monkeypatch.setattr(ai_service, "_provider_program_candidates", lambda _spec: [local_codex])

    assert resolve_provider_program(config) == local_codex


def test_codex_auto_discovery_prefers_appdata_npm_wrapper_over_desktop_bundle(tmp_path, monkeypatch):
    appdata = tmp_path / "Roaming"
    local_appdata = tmp_path / "Local"
    npm_codex = appdata / "npm" / "codex.cmd"
    desktop_codex = local_appdata / "OpenAI" / "Codex" / "bin" / "codex.exe"
    npm_codex.parent.mkdir(parents=True)
    desktop_codex.parent.mkdir(parents=True)
    npm_codex.write_text("@echo off\n", encoding="utf-8")
    desktop_codex.write_text("", encoding="utf-8")
    monkeypatch.setattr(ai_service.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(ai_service.shutil, "which", lambda _name: None)

    candidates = ai_service._provider_program_candidates(ai_service.PROVIDER_SPECS["codex"])

    assert candidates[:2] == [str(npm_codex), str(desktop_codex)]
    assert resolve_provider_program(AiProviderConfig(key="codex")) == str(npm_codex)


def test_codex_auto_discovery_falls_back_to_desktop_bundle_without_npm_wrapper(tmp_path, monkeypatch):
    appdata = tmp_path / "Roaming"
    local_appdata = tmp_path / "Local"
    desktop_codex = local_appdata / "OpenAI" / "Codex" / "bin" / "codex.exe"
    desktop_codex.parent.mkdir(parents=True)
    desktop_codex.write_text("", encoding="utf-8")
    monkeypatch.setattr(ai_service.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(ai_service.shutil, "which", lambda _name: None)

    assert resolve_provider_program(AiProviderConfig(key="codex")) == str(desktop_codex)


def test_claude_and_gemini_prompt_invocations_use_argument_prompt_flag():
    claude = build_chat_invocation(AiProviderConfig(key="claude", command_path="claude"), "hello")
    gemini = build_chat_invocation(AiProviderConfig(key="gemini", command_path="gemini"), "hello")

    assert claude.args[0] == "-p"
    assert "hello" in claude.args[1]
    assert "--output-format" in claude.args
    assert gemini.args[0] == "-p"
    assert "hello" in gemini.args[1]
    assert "--output-format" in gemini.args
    assert "json" in gemini.args


def test_cli_json_line_text_extraction_handles_common_stream_shapes():
    assert extract_text_from_cli_line('{"content":[{"type":"text","text":"hi"}]}') == "hi"
    assert extract_text_from_cli_line('{"delta":" there"}') == "there"
    assert extract_text_from_cli_line("plain output") == "plain output"


def test_cli_output_decodes_and_filters_windows_process_noise():
    cp949_noise = "성공: PID 7708인 프로세스(PID 21356의 자식 프로세스)가 종료되었습니다."
    decoded = decode_cli_output(cp949_noise.encode("cp949"))

    assert decoded == cp949_noise
    assert should_ignore_cli_output_text(decoded) is True
    assert should_ignore_cli_output_text("���� PID 7708�� ������") is True
    assert should_ignore_cli_output_text("공인 IP는 1.232.102.124 입니다.") is False


def test_cli_help_options_are_parsed_and_internal_flags_filtered():
    help_text = """
Options:
  -m, --model <MODEL>
          Model the agent should use
  -p, --profile <CONFIG_PROFILE>
          Configuration profile from config.toml
      --sandbox <SANDBOX_MODE>
          Select the sandbox policy
      --search
          Enable web search
      --output-format <FORMAT>
          Internal output format
      --dangerously-bypass-approvals-and-sandbox
          Skip all confirmation prompts
"""

    parsed = parse_cli_help_options(help_text)
    extra_options = extra_arg_options_from_help(help_text)

    assert any(option.flag == "--profile" and option.value_hint == "<CONFIG_PROFILE>" for option in parsed)
    assert any(option.flag == "--sandbox" and option.takes_value for option in parsed)
    assert any(option.flag == "--search" and not option.takes_value for option in parsed)
    assert "--model" not in {option.flag for option in extra_options}
    assert "--output-format" not in {option.flag for option in extra_options}
    assert "--dangerously-bypass-approvals-and-sandbox" not in {option.flag for option in extra_options}
    assert {"--profile", "--sandbox", "--search"}.issubset({option.flag for option in extra_options})


def test_codex_service_tier_config_error_gets_actionable_korean_diagnosis():
    raw_error = (
        "Error loading configuration: C:\\Users\\PC\\.codex\\config.toml:3:16: "
        "unknown variant `priority`, expected `fast` or `flex`"
    )

    message = diagnose_cli_error("codex", raw_error)

    assert is_blocking_cli_configuration_error("codex", raw_error) is True
    assert "최신 Codex CLI로 업데이트" in message
    assert 'service_tier = "priority"' in message
    assert "자동 변경하지 않고 그대로 보존" in message
    assert raw_error in message


def test_codex_newer_service_tier_is_preserved_when_an_older_cli_rejects_it(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('model = "gpt-5.5"\nservice_tier = "priority"\n', encoding="utf-8")
    raw_error = (
        f"Error loading configuration: {config_path}:2:16: "
        "unknown variant `priority`, expected `fast` or `flex`"
    )

    result = repair_cli_configuration_error("codex", raw_error)

    assert result.attempted is True
    assert result.repaired is False
    assert result.config_path == str(config_path)
    assert result.backup_path == ""
    assert 'service_tier = "priority"' in config_path.read_text(encoding="utf-8")
    assert "최신 Codex CLI로 업데이트" in result.message


def test_codex_blocking_reasoning_value_is_repaired_without_changing_service_tier(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'model = "gpt-5.6-sol"\nmodel_reasoning_effort = "ultra"\nservice_tier = "priority"\n',
        encoding="utf-8",
    )
    raw_error = (
        f"Error loading configuration: {config_path}:2:26: "
        "unknown variant `ultra`, expected one of `none`, `minimal`, `low`, `medium`, `high`, `xhigh`"
    )

    assert is_blocking_cli_configuration_error("codex", raw_error) is True
    diagnosis = diagnose_cli_error("codex", raw_error)
    assert 'model_reasoning_effort는 "none", "minimal", "low", "medium", "high", "xhigh"만 지원' in diagnosis
    assert 'model_reasoning_effort = "xhigh"로 자동 복구' in diagnosis

    result = repair_cli_configuration_error("codex", raw_error)

    assert result.attempted is True
    assert result.repaired is True
    repaired = config_path.read_text(encoding="utf-8")
    assert 'model_reasoning_effort = "xhigh"' in repaired
    assert 'service_tier = "priority"' in repaired
    assert 'model_reasoning_effort = "ultra"' in Path(result.backup_path).read_text(encoding="utf-8")


def test_codex_legacy_invalid_setting_is_repaired_atomically(tmp_path):
    config_path = tmp_path / "config.toml"
    original = 'model = "gpt-5.4"\nmodel_reasoning_effort = "max"\n'
    config_path.write_text(original, encoding="utf-8")
    raw_error = (
        f"Error loading configuration: {config_path}:2:26: "
        "unknown variant `max`, expected one of `none`, `minimal`, `low`, `medium`, `high`, `xhigh`"
    )

    result = repair_cli_configuration_error("codex", raw_error)

    assert result.repaired is True
    assert 'model_reasoning_effort = "xhigh"' in config_path.read_text(encoding="utf-8")
    assert Path(result.backup_path).read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".*.tmp")) == []


def test_codex_config_repair_replace_failure_preserves_original(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    original = 'model = "gpt-5.4"\nmodel_reasoning_effort = "max"\n'
    config_path.write_text(original, encoding="utf-8")
    raw_error = (
        f"Error loading configuration: {config_path}:2:26: "
        "unknown variant `max`, expected one of `none`, `minimal`, `low`, `medium`, `high`, `xhigh`"
    )

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr(ai_service.os, "replace", fail_replace)
    result = repair_cli_configuration_error("codex", raw_error)

    assert result.repaired is False
    assert config_path.read_text(encoding="utf-8") == original
    assert Path(result.backup_path).read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".*.tmp")) == []


def test_codex_nested_json_error_is_extracted_and_cache_warning_is_separated():
    message = "The 'gpt-5.6-sol' model requires a newer version of Codex. Please update and try again."
    line = json.dumps(
        {
            "type": "error",
            "status": 400,
            "error": json.dumps({"type": "invalid_request_error", "message": message}),
        }
    )

    assert extract_error_from_cli_line(line) == message
    assert extract_text_from_cli_line(line) == message
    diagnosis = diagnose_cli_error("codex", line)
    assert "새 버전이 필요" in diagnosis
    assert "모델 목록 새로고침" in diagnosis
    assert "저장된 모델 선택은 변경하지 않았습니다" in diagnosis
    assert '"invalid_request_error"' not in diagnosis

    warning = (
        "ERROR codex_models::manager::cache: failed to load models cache: "
        "unknown variant max, expected one of none, minimal, low, medium, high, xhigh"
    )
    regular, separated_warning = split_codex_model_cache_warning(f"{warning}\nprimary failure")
    assert regular == "primary failure"
    assert separated_warning == warning


def test_fallback_model_options_do_not_guess_codex_models_and_preserve_custom_model():
    codex_options = model_options_for_provider("codex")
    custom_options = model_options_for_provider("gemini", "my-custom-model")

    assert codex_options == [("자동 선택 (권장)", "")]
    assert ("사용자 설정: my-custom-model", "my-custom-model") in custom_options


def test_netops_chat_action_planner_detects_common_tool_requests():
    ping = plan_netops_chat_action("8.8.8.8 ping 해줘")
    dns = plan_netops_chat_action("google.com DNS MX 조회")
    tcp = plan_netops_chat_action("google.com 443 포트 확인")
    public_ip = plan_netops_chat_action("공인 IP 확인해줘")
    external_ping = plan_netops_chat_action("외부 핑테스트 해봐")
    google_ping = plan_netops_chat_action("구글 핑 테스트")
    wifi_scan = plan_netops_chat_action("주변 와이파이 스캔 30초 동안 5초 간격")
    korean_minute_wifi_scan = plan_netops_chat_action("무선 AP 스캔 1분 동안 15초 간격")
    english_wifi_scan = plan_netops_chat_action("nearby wi-fi scan for 2 minutes every 30 seconds")
    default_korean_wifi_scan = plan_netops_chat_action("주변 무선 상태 점검")
    wireless_status = plan_netops_chat_action("현재 무선 상태")
    dns_flush = plan_netops_chat_action("DNS 캐시 비워줘")
    subnet = plan_netops_chat_action("192.168.10.0/24 서브넷 계산")
    oui_refresh = plan_netops_chat_action("OUI 캐시 갱신")

    assert ping is not None
    assert ping.kind == "ping"
    assert ping.target == "8.8.8.8"
    assert dns is not None
    assert dns.kind == "dns_lookup"
    assert dns.target == "google.com"
    assert dns.record_type == "MX"
    assert tcp is not None
    assert tcp.kind == "tcp_check"
    assert tcp.target == "google.com"
    assert tcp.port == 443
    assert public_ip is not None
    assert public_ip.kind == "public_ip"
    assert external_ping is not None
    assert external_ping.kind == "external_ping"
    assert external_ping.targets == ("8.8.8.8", "1.1.1.1", "google.com")
    assert google_ping is not None
    assert google_ping.kind == "ping"
    assert google_ping.target == "google.com"
    assert wifi_scan is not None
    assert wifi_scan.kind == "wireless_scan"
    assert wifi_scan.duration_seconds == 30
    assert wifi_scan.interval_seconds == 5
    assert wifi_scan.risk_level == "low"
    assert korean_minute_wifi_scan is not None
    assert korean_minute_wifi_scan.kind == "wireless_scan"
    assert korean_minute_wifi_scan.duration_seconds == 60
    assert korean_minute_wifi_scan.interval_seconds == 15
    assert english_wifi_scan is not None
    assert english_wifi_scan.kind == "wireless_scan"
    assert english_wifi_scan.duration_seconds == 120
    assert english_wifi_scan.interval_seconds == 30
    assert default_korean_wifi_scan is not None
    assert default_korean_wifi_scan.kind == "wireless_scan"
    assert default_korean_wifi_scan.duration_seconds == 20
    assert default_korean_wifi_scan.interval_seconds == 5
    assert wireless_status is not None
    assert wireless_status.kind == "wireless_status"
    assert dns_flush is not None
    assert dns_flush.kind == "dns_flush_cache"
    assert dns_flush.requires_approval is True
    assert dns_flush.admin_required is True
    assert subnet is not None
    assert subnet.kind == "subnet_calculate"
    assert subnet.target == "192.168.10.0/24"
    assert oui_refresh is not None
    assert oui_refresh.kind == "oui_cache_refresh"
    assert oui_refresh.requires_approval is True


def test_netops_chat_action_planner_marks_network_changes_for_approval():
    dhcp = plan_netops_chat_action("인터페이스 Ethernet DHCP로 변경")
    dns = plan_netops_chat_action("인터페이스 Ethernet DNS 8.8.8.8, 1.1.1.1로 변경")
    static_ip = plan_netops_chat_action(
        "인터페이스 Ethernet IP 192.168.10.20/24 게이트웨이 192.168.10.1 DNS 8.8.8.8 설정"
    )

    assert dhcp is not None
    assert dhcp.kind == "set_dhcp"
    assert dhcp.interface_name == "Ethernet"
    assert dhcp.requires_approval is True
    assert dhcp.admin_required is True
    assert dns is not None
    assert dns.kind == "set_dns"
    assert dns.dns_servers == ("8.8.8.8", "1.1.1.1")
    assert dns.requires_approval is True
    assert static_ip is not None
    assert static_ip.kind == "set_static_ip"
    assert static_ip.ip_address == "192.168.10.20"
    assert static_ip.prefix == 24
    assert static_ip.gateway == "192.168.10.1"
    assert static_ip.dns_servers == ("8.8.8.8",)
    assert static_ip.requires_approval is True


def test_ai_chat_tab_builds_codex_runtime_options_and_attachments(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)

    note = tmp_path / "note.txt"
    note.write_text("hello from attachment", encoding="utf-8")
    image = tmp_path / "screen.png"
    image.write_bytes(b"\x89PNG\r\n")
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex", "providers": {"codex": {"model": "gpt-5.5"}}}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        tab._set_combo_data(tab.reasoning_combo, "xhigh")
        tab._set_combo_data(tab.speed_combo, "fast")
        tab._attachments = [note, image]
        tab._refresh_attachment_view()

        config = tab.current_provider_config()
        context, attachment_args = tab._attachment_context_and_args(config.key)
        runtime_config = tab._runtime_provider_config(config, attachment_args)
        invocation = build_chat_invocation(runtime_config, "summarize", context=context, working_dir=str(tmp_path))

        assert config.reasoning_effort == "xhigh"
        assert config.speed == "fast"
        assert 'model_reasoning_effort="xhigh"' in invocation.args
        assert 'service_tier="fast"' in invocation.args
        assert "--image" in invocation.args
        assert str(image) in invocation.args
        assert "hello from attachment" in invocation.stdin_text
        assert tab._codex_runtime_config_args(
            AiProviderConfig(key="codex", reasoning_effort="max")
        ) == ["-c", 'model_reasoning_effort="max"']
        assert tab._codex_runtime_config_args(
            AiProviderConfig(key="codex", reasoning_effort="ultra")
        ) == ["-c", 'model_reasoning_effort="ultra"']
        assert isinstance(tab.attachment_list, QListWidget)
        assert tab.attachment_list.count() == 2
        assert not hasattr(tab, "context_status_label")
    finally:
        tab.close()


def test_ai_chat_tab_rejects_binary_attachment_context_and_keeps_active_context_status(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)

    binary = tmp_path / "manual.pdf"
    binary.write_bytes(b"%PDF-1.7\n" + bytes(range(1, 128)) * 2000)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        tab._attachments = [binary]
        context, attachment_args = tab._attachment_context_and_args("codex")

        assert attachment_args == []
        assert "텍스트로 읽을 수 없어" in context
        assert "--- 첨부 내용 시작 ---" not in context

        tab.prompt_edit.clear()
        tab._refresh_attachment_view()
        assert not hasattr(tab, "context_status_label")

        assert not hasattr(tab, "context_status_label")
    finally:
        tab.close()


def test_ai_chat_tab_collects_internal_network_context_for_network_requests(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)

    class FakeNetworkInterfaceService:
        def list_adapters(self):
            return [
                NetworkAdapterInfo(
                    name="Ethernet",
                    interface_description="Intel NIC",
                    mac_address="00-11-22-33-44-55",
                    status="Up",
                    ipv4="192.168.10.20",
                    prefix_length=24,
                    gateway="192.168.10.1",
                    dns_servers=["8.8.8.8"],
                    dhcp_enabled=True,
                )
            ]

        def format_adapter_snapshot(self, adapters):
            return f"adapter snapshot: {len(adapters)} adapter, gateway {adapters[0].gateway}"

    class FakePingService:
        def __init__(self):
            self.targets: list[str] = []

        def quick_ping(self, target, count=2, timeout_ms=2000):
            self.targets.append(target)
            return OperationResult(True, f"{target} reachable", "loss 0%")

    ping_service = FakePingService()
    trace_calls: list[str] = []
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
        network_interface_service=FakeNetworkInterfaceService(),
        ping_service=ping_service,
        dns_service=SimpleNamespace(lookup=lambda *_args: OperationResult(True, "dns ok", "google.com A 142.250.1.1")),
        public_ip_service=SimpleNamespace(check_public_ip=lambda **_kwargs: OperationResult(True, "public ip ok", "1.2.3.4")),
        trace_service=SimpleNamespace(
            run_route_print=lambda: trace_calls.append("route") or OperationResult(True, "route ok", "0.0.0.0/0 via 192.168.10.1"),
            run_ipconfig_all=lambda: trace_calls.append("ipconfig") or OperationResult(True, "ipconfig ok", "Windows IP Configuration"),
        ),
    )

    tab = AiChatTab(state)
    try:
        assert tab._should_collect_internal_network_context("네트워크 상태 점검해줘") is True
        assert tab._should_collect_internal_network_context("IP 주소 확인해줘") is True
        assert tab._should_collect_internal_network_context("안녕") is False
        assert tab._should_collect_internal_network_context("zip 파일 압축해줘") is False
        assert tab._should_collect_internal_network_context("플랜 작성해줘") is False
        assert tab._should_collect_internal_network_context("language 설정 알려줘") is False

        context = tab._collect_internal_network_context("네트워크 상태 점검해줘")

        assert "NetOps Suite internal diagnostics snapshot" in context
        assert "adapter snapshot" in context
        assert "192.168.10.1" in context
        assert "8.8.8.8" in context
        assert "google.com" in context
        assert "1.2.3.4" in context
        assert "route ok" in context
        assert {"192.168.10.1", "8.8.8.8", "1.1.1.1"}.issubset(set(ping_service.targets))
        assert trace_calls == ["route"]
    finally:
        tab.close()


def test_ai_chat_tab_send_prompt_runs_internal_context_before_cli(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.inspect_provider",
        lambda _config: SimpleNamespace(installed=True, detail="ok"),
    )
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    captured: dict[str, object] = {}

    def fake_job_start(fn, *args, on_result=None, on_error=None, on_finished=None, **_kwargs):
        captured["worker_fn"] = fn
        captured["worker_args"] = args
        if on_result:
            on_result("internal context from netops tools")
        if on_finished:
            on_finished()

    def fake_start_prompt_process(payload, internal_context):
        captured["payload"] = payload
        captured["internal_context"] = internal_context

    try:
        tab._job_runner.start = fake_job_start
        monkeypatch.setattr(tab, "_start_prompt_process", fake_start_prompt_process)
        tab.prompt_edit.setPlainText("네트워크 상태 점검해줘")

        tab.send_prompt()

        assert getattr(captured["worker_fn"], "__name__", "") == "_collect_internal_netops_context"
        assert captured["worker_args"][0] == "네트워크 상태 점검해줘"
        assert isinstance(captured["worker_args"][1], Event)
        assert captured["internal_context"] == "internal context from netops tools"
        assert captured["payload"]["prompt"] == "네트워크 상태 점검해줘"
        assert tab._pending_prompt_payload is None
        assert tab._context_collecting is False
        assert tab.prompt_edit.isEnabled()
        assert all("internal context from netops tools" not in message["body"] for message in tab._messages)
    finally:
        tab.close()


def test_ai_chat_tab_runs_netops_tool_request_before_ai_cli(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.inspect_provider",
        lambda _config: SimpleNamespace(installed=True, detail="ok"),
    )

    class FakePingService:
        def __init__(self):
            self.calls: list[tuple[str, int, int]] = []

        def quick_ping(self, target, count=2, timeout_ms=2000):
            self.calls.append((target, count, timeout_ms))
            return OperationResult(True, f"{target} reachable", "loss 0%")

    ping_service = FakePingService()
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
        ping_service=ping_service,
    )

    tab = AiChatTab(state)
    captured: dict[str, object] = {}

    def fake_job_start(fn, *args, on_result=None, on_error=None, on_finished=None, **_kwargs):
        captured["worker_fn"] = fn
        captured["worker_args"] = args
        if on_result:
            on_result(fn(*args))
        if on_finished:
            on_finished()

    def fake_start_prompt_process(payload, internal_context):
        captured["payload"] = payload
        captured["internal_context"] = internal_context

    try:
        tab._job_runner.start = fake_job_start
        monkeypatch.setattr(tab, "_start_prompt_process", fake_start_prompt_process)
        tab.prompt_edit.setPlainText("8.8.8.8 ping 해줘")

        tab.send_prompt()

        assert getattr(captured["worker_fn"], "__name__", "") == "_run_netops_chat_action"
        assert captured["worker_args"][0].kind == "ping"
        assert ping_service.calls == [("8.8.8.8", 4, 4000)]
        assert captured["payload"]["prompt"] == "8.8.8.8 ping 해줘"
        assert "NetOps Suite tool result" in captured["internal_context"]
        assert "8.8.8.8 reachable" in captured["internal_context"]
        assert any(message["title"] == "NetOps" for message in tab._messages)
    finally:
        tab.close()


def test_ai_chat_tab_runs_natural_external_ping_request(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.inspect_provider",
        lambda _config: SimpleNamespace(installed=True, detail="ok"),
    )

    class FakePingService:
        def __init__(self):
            self.targets: list[str] = []

        def quick_ping(self, target, count=2, timeout_ms=2000):
            self.targets.append(target)
            return OperationResult(True, f"{target} reachable", "loss 0%")

    ping_service = FakePingService()
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
        ping_service=ping_service,
    )

    tab = AiChatTab(state)
    captured: dict[str, object] = {}

    def fake_job_start(fn, *args, on_result=None, on_error=None, on_finished=None, **_kwargs):
        captured["worker_args"] = args
        if on_result:
            on_result(fn(*args))
        if on_finished:
            on_finished()

    try:
        tab._job_runner.start = fake_job_start
        monkeypatch.setattr(tab, "_start_prompt_process", lambda payload, context: captured.update(context=context))
        tab.prompt_edit.setPlainText("외부 핑테스트 해봐")

        tab.send_prompt()

        assert captured["worker_args"][0].kind == "external_ping"
        assert ping_service.targets == ["8.8.8.8", "1.1.1.1", "google.com"]
        assert "8.8.8.8 reachable" in captured["context"]
        assert "google.com reachable" in captured["context"]
    finally:
        tab.close()


def test_ai_chat_tab_blocks_risky_netops_action_without_admin(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr("app.ui.tabs.ai_chat_tab.QMessageBox.warning", lambda *_args, **_kwargs: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
        is_admin=False,
    )

    tab = AiChatTab(state)
    try:
        action = plan_netops_chat_action("인터페이스 Ethernet DHCP로 변경")

        assert action is not None
        assert tab._confirm_netops_chat_action(action) is False
        assert any("관리자 권한" in message["body"] for message in tab._messages)
    finally:
        tab.close()


def test_ai_chat_tab_runs_approved_dns_change_through_network_service(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.inspect_provider",
        lambda _config: SimpleNamespace(installed=True, detail="ok"),
    )

    class FakeNetworkInterfaceService:
        def __init__(self):
            self.calls: list[tuple[str, list[str]]] = []

        def set_dns(self, interface_name, dns_servers):
            self.calls.append((interface_name, dns_servers))
            return OperationResult(True, "dns changed", "ok")

    network_service = FakeNetworkInterfaceService()
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
        is_admin=True,
        network_interface_service=network_service,
    )

    tab = AiChatTab(state)
    captured: dict[str, object] = {}

    def fake_job_start(fn, *args, on_result=None, on_error=None, on_finished=None, **_kwargs):
        captured["worker_fn"] = fn
        captured["worker_args"] = args
        if on_result:
            on_result(fn(*args))
        if on_finished:
            on_finished()

    def fake_start_prompt_process(payload, internal_context):
        captured["payload"] = payload
        captured["internal_context"] = internal_context

    try:
        tab._job_runner.start = fake_job_start
        monkeypatch.setattr(tab, "_confirm_netops_chat_action", lambda _action: True)
        monkeypatch.setattr(tab, "_start_prompt_process", fake_start_prompt_process)
        tab.prompt_edit.setPlainText("인터페이스 Ethernet DNS 8.8.8.8, 1.1.1.1로 변경")

        tab.send_prompt()

        assert captured["worker_args"][0].kind == "set_dns"
        assert network_service.calls == [("Ethernet", ["8.8.8.8", "1.1.1.1"])]
        assert "dns changed" in captured["internal_context"]
    finally:
        tab.close()


def test_ai_chat_tab_can_cancel_internal_context_collection(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        cancel_event = Event()
        tab._context_cancel_event = cancel_event
        tab._pending_prompt_payload = {"prompt": "네트워크 상태 점검"}
        tab._set_preparing(True)

        tab.cancel_prompt()

        assert tab._context_collection_cancelled is True
        assert cancel_event.is_set()
        assert tab._context_cancel_event is None
        assert tab._pending_prompt_payload is None
        assert tab._context_collecting is False
        assert tab.prompt_edit.isEnabled()
        assert tab.send_button.isEnabled()
    finally:
        tab.close()


def test_ai_chat_tab_ignores_stale_internal_context_callbacks(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)
    started: list[tuple[dict, str]] = []
    monkeypatch.setattr(tab, "_start_prompt_process", lambda payload, context: started.append((payload, context)))
    try:
        tab._active_context_request = 1
        tab._pending_prompt_payload = {"prompt": "이전 요청"}
        tab._set_preparing(True)
        tab.cancel_prompt()

        new_payload = {"prompt": "새 요청"}
        tab._active_context_request = 2
        tab._pending_prompt_payload = new_payload
        tab._context_collection_cancelled = False
        tab._set_preparing(True)

        tab._continue_prompt_with_internal_context(1, "이전 결과")
        tab._finish_internal_context_collection(1)

        assert tab._pending_prompt_payload is new_payload
        assert tab._active_context_request == 2
        assert tab._context_collecting is True
        assert started == []

        tab._continue_prompt_with_internal_context(2, "새 결과")
        tab._finish_internal_context_collection(2)

        assert started == [(new_payload, "새 결과")]
        assert tab._active_context_request is None
        assert tab._context_collecting is False
    finally:
        tab.close()


def test_ai_chat_tab_stale_process_timeouts_cannot_kill_new_process(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    class FakeRunningProcess:
        def __init__(self) -> None:
            self.killed = False
            self.deleted = False
            self.signals_blocked = False

        def state(self):
            return QProcess.ProcessState.Running

        def kill(self) -> None:
            self.killed = True

        def blockSignals(self, blocked: bool) -> None:
            self.signals_blocked = blocked

        def deleteLater(self) -> None:
            self.deleted = True

    tab = AiChatTab(state)
    try:
        stale_prompt = FakeRunningProcess()
        active_prompt = FakeRunningProcess()
        stale_status = FakeRunningProcess()
        active_status = FakeRunningProcess()
        stale_help = FakeRunningProcess()
        active_help = FakeRunningProcess()
        tab._process = active_prompt
        tab._status_process = active_status
        tab._help_process = active_help

        tab._timeout_prompt(stale_prompt)
        tab._timeout_status(stale_status)
        tab._timeout_help_options(stale_help)

        assert not stale_prompt.killed
        assert not stale_status.killed
        assert not stale_help.killed
        assert not active_prompt.killed
        assert not active_status.killed
        assert not active_help.killed

        tab.cancel_prompt()

        assert active_prompt.killed
        assert active_prompt.signals_blocked
        assert active_prompt.deleted
        assert tab._process is None
        tab._finish_prompt(active_prompt, 1)
        assert not any(item["title"] == "오류" for item in tab._messages)
    finally:
        tab.close()


def test_ai_chat_tab_collects_inspector_and_config_builder_context(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path, data_root=tmp_path / "data"),
        save_app_config=lambda _config: None,
        ip_profiles=[],
        ftp_profiles=[],
        scp_profiles=[],
    )

    tab = AiChatTab(state)
    try:
        categories = tab._netops_context_categories("장비 점검/백업 프로파일이랑 CLI 설정 생성 프로파일 만들어줘")
        context = tab._collect_internal_netops_context("장비 점검/백업 프로파일이랑 CLI 설정 생성 프로파일 만들어줘")

        assert {"inspector", "config_builder", "profiles", "overview"}.issubset(categories)
        assert "장비 점검/백업 컨텍스트" in context
        assert "CLI 설정 생성 컨텍스트" in context
        assert "custom_rules.yaml" in context
        assert "프로파일 폴더" in context
        assert "NetOps Suite 기능 지도" in context
    finally:
        tab.close()


def test_ai_chat_tab_masks_saved_profile_context_and_avoids_broad_keyword_matches(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path, data_root=tmp_path / "data"),
        save_app_config=lambda _config: None,
        ip_profiles=[
            IPProfile(
                name="Lab static",
                mode="static",
                local_ip="192.168.77.23",
                prefix=24,
                gateway="192.168.77.1",
                dns=["10.0.0.10"],
            )
        ],
        ftp_profiles=[
            FtpProfile(
                name="Backup FTP",
                protocol="sftp",
                host="backup.internal.local",
                port=2222,
                remote_path="/very/secret/backups",
            )
        ],
        scp_profiles=[ScpProfile(name="Core SCP", host="10.10.10.50", port=22, remote_path="/network/backups")],
    )

    tab = AiChatTab(state)
    try:
        assert tab._netops_context_categories("profile picture 만들어줘") == set()
        assert tab._netops_context_categories("backup file 압축해줘") == set()
        assert tab._netops_context_categories("ftp가 뭔지 설명해줘") == set()

        context = tab._collect_internal_netops_context("저장된 IP 프로파일과 FTP 전송 프로파일 요약해줘")

        assert "192.168.77.23" not in context
        assert "192.168.77.1" not in context
        assert "10.0.0.10" not in context
        assert "192.168.77.x" in context
        assert "backup.internal.local" not in context
        assert "ba***.local" in context
        assert "/very/secret/backups" not in context
        assert ".../backups" in context
    finally:
        tab.close()


def test_ai_chat_tab_blocks_reserved_direct_extra_args(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        assert tab._blocked_direct_extra_args(["--profile", "work"]) == []
        assert tab._blocked_direct_extra_args(["--output-format=json", "--ask-for-approval", "never", "--dangerously-bypass-approvals-and-sandbox"]) == [
            "--output-format",
            "--ask-for-approval",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
    finally:
        tab.close()


def test_ai_chat_tab_uses_korean_labels_and_model_combo(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)

    saved_configs: list[dict] = []
    state = SimpleNamespace(
        app_config={
            "ai_chat": {
                "active_provider": "codex",
                "providers": {
                    "codex": {"model": "my-custom-model"},
                },
            }
        },
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda config: saved_configs.append(config),
    )

    tab = AiChatTab(state)
    try:
        assert tab.windowTitle() == ""
        assert isinstance(tab.model_combo, QComboBox)
        assert isinstance(tab.transcript_scroll, QScrollArea)
        assert tab.check_button.text() == "상태 확인"
        assert tab.login_button.text() == "로그인 터미널"
        assert tab.send_button.text() == "보내기"
        assert tab.prompt_edit.placeholderText() == "NetOps 작업을 자연어로 입력하세요..."
        assert tab.model_combo.currentData() == "my-custom-model"
        assert tab.model_combo.currentText() == "현재 설정: my-custom-model (목록에서 확인되지 않음)"
        codex_model_values = [tab.model_combo.itemData(index) for index in range(tab.model_combo.count())]
        assert codex_model_values == ["", "my-custom-model"]

        tabs = tab.findChildren(QTabWidget)[0]
        assert [tabs.tabText(index) for index in range(tabs.count())] == ["채팅", "연결 설정", "고급 옵션"]
        assert tab.reasoning_combo.itemText(tab.reasoning_combo.findData("xhigh")) == "매우 높음 (가장 깊은 분석)"
        assert tab.speed_combo.itemText(tab.speed_combo.findData("fast")) == "빠른 응답"
        assert tab.raw_help_group.title() == "CLI 도움말 원문 보기"
        assert tab.raw_help_edit.isHidden()

        tab._set_combo_data(tab.provider_combo, "gemini")
        qapp.processEvents()

        gemini_model_labels = [tab.model_combo.itemText(index) for index in range(tab.model_combo.count())]
        assert "Gemini 2.5 Pro" in gemini_model_labels
        assert any(button.text() == "내보내기" for button in tab.findChildren(QPushButton))

        tab._append_block("사용자", "hello <world>")
        plain_text = tab._plain_transcript_text()
        assert "사용자" in plain_text
        assert re.search(r"\d{2}:\d{2}:\d{2}", plain_text)
        assert "hello <world>" in plain_text
        rendered_bodies = "\n".join(_widget_visible_text(_message_body_widget(bubble)) for bubble in _message_bubbles(tab))
        assert "hello <world>" in rendered_bodies

        tab._append_block("AI", "**요약**\n- 첫 번째\n`ping 8.8.8.8`")
        rendered_plain = "\n".join(_widget_visible_text(_message_body_widget(bubble)) for bubble in _message_bubbles(tab))
        assert "요약" in rendered_plain
        assert "**요약**" not in rendered_plain

        tab._populate_help_options(
            """
Options:
  -p, --profile <CONFIG_PROFILE>
          Configuration profile
      --search
          Enable search
      --mystery-mode <MODE>
          Experimental mode
"""
        )
        assert tab.option_combo.itemText(0) == "설정 프로파일"
        assert "설정 프로파일" in tab.option_description.text()
        assert "실제 CLI 옵션: -p, --profile" in tab.option_description.text()
        assert "값: 필요 (설정 프로파일 이름)" in tab.option_description.text()
        assert "Configuration profile" not in tab.option_description.text()
        assert not hasattr(tab.option_description, "verticalScrollBar")
        assert tab.option_value_edit.placeholderText() == "예: work"

        tab._set_combo_data(tab.option_combo, tab.option_combo.itemData(0))
        tab.option_value_edit.setText("work")
        tab.add_selected_extra_arg()
        assert "--profile work" in tab.extra_args_edit.text()

        tab._set_combo_data(tab.option_combo, tab.option_combo.itemData(1))
        assert tab.option_combo.currentText() == "웹 검색 사용"
        assert "웹 검색 기능을 켭니다" in tab.option_description.text()
        assert "값: 필요 없음" in tab.option_description.text()
        tab.add_selected_extra_arg()
        assert "--search" in tab.extra_args_edit.text()

        tab._set_combo_data(tab.option_combo, tab.option_combo.itemData(2))
        assert tab.option_combo.currentText() == "기타 고급 설정"
        assert "--mystery-mode" not in tab.option_combo.currentText()
        assert "실제 CLI 옵션: --mystery-mode" in tab.option_description.text()
        assert "값: 필요 (모드)" in tab.option_description.text()
    finally:
        tab.close()


def test_ai_chat_login_preflight_starts_in_background_with_immediate_feedback(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.inspect_provider",
        lambda _config: SimpleNamespace(installed=True, detail="available"),
    )
    detached_calls: list[tuple] = []
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.QProcess.startDetached",
        lambda *args: detached_calls.append(args) or (True, 1234),
    )
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)
    jobs: list[tuple] = []
    tab._job_runner.start = lambda fn, *args, **kwargs: jobs.append((fn, args, kwargs))

    try:
        tab.open_provider_login()

        assert len(jobs) == 1
        assert jobs[0][0] == tab._prepare_provider_login
        assert detached_calls == []
        assert tab._login_preflight_active is True
        assert tab.login_button.isEnabled() is False
        assert tab.login_button.text() == "로그인 준비 중…"
        assert "로그인 준비 중" in tab.status_label.text()
    finally:
        tab._finish_provider_login_preflight()
        tab.close()


def test_ai_chat_login_preflight_repairs_then_rechecks_before_launch(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'model_reasoning_effort = "turbo"\nservice_tier = "premium"\n',
        encoding="utf-8",
    )
    raw_error = (
        f"Error loading configuration: {config_path}:1:26: "
        "unknown variant `turbo`, expected one of `none`, `minimal`, `low`, `medium`, `high`, `xhigh`"
    )
    preflight_results = iter([diagnose_cli_error("codex", raw_error), ""])
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)
    calls: list[str] = []

    def fake_preflight(_config):
        calls.append("status")
        return next(preflight_results)

    monkeypatch.setattr(tab, "_login_preflight_error", fake_preflight)

    try:
        result = tab._prepare_provider_login(AiProviderConfig(key="codex"))

        assert calls == ["status", "status"]
        assert result["error"] == ""
        assert len(result["repair_messages"]) == 1
        repaired = config_path.read_text(encoding="utf-8")
        assert 'model_reasoning_effort = "xhigh"' in repaired
        assert 'service_tier = "fast"' in repaired
    finally:
        tab.close()


@pytest.mark.parametrize(
    ("detached_result", "expected_success"),
    [((False, 0), False), ((True, 4321), True), (False, False), (True, True)],
)
def test_ai_chat_login_detached_result_is_interpreted_correctly(
    qapp,
    tmp_path,
    monkeypatch,
    detached_result,
    expected_success,
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    warnings: list[str] = []
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.QProcess.startDetached",
        lambda *_args: detached_result,
    )
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)

    try:
        tab._launch_provider_login(AiProviderConfig(key="codex"))

        if expected_success:
            assert warnings == []
            assert "로그인 터미널 열림" in tab.status_label.text()
            assert any("로그인 터미널을 열었습니다" in item["body"] for item in tab._messages)
        else:
            assert warnings == ["로그인 터미널을 열지 못했습니다."]
            assert "로그인 실행 실패" in tab.status_label.text()
            assert not any("로그인 터미널을 열었습니다" in item["body"] for item in tab._messages)
    finally:
        tab.close()


def test_ai_chat_structured_stdout_error_is_not_rendered_as_raw_ai_message(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    message = "The 'gpt-5.6-sol' model requires a newer version of Codex. Please update and try again."
    payload = (
        json.dumps(
            {
                "type": "error",
                "status": 400,
                "error": json.dumps({"type": "invalid_request_error", "message": message}),
            }
        ).encode("utf-8")
        + b"\n"
    )

    class FakePromptProcess:
        def __init__(self) -> None:
            self.stdout = payload

        def readAllStandardOutput(self):
            data, self.stdout = self.stdout, b""
            return data

        def property(self, name):
            return "codex" if name == "provider_key" else None

        def deleteLater(self):
            return None

    tab = AiChatTab(state)
    tab._process = FakePromptProcess()
    tab._stderr_text = (
        "ERROR codex_models::manager::cache: failed to load models cache: "
        "unknown variant max, expected one of none, minimal, low, medium, high, xhigh"
    )

    try:
        process = tab._process
        tab._read_stdout(process)
        assert tab._cli_error_text == message
        assert not any(item["title"] == "AI" for item in tab._messages)

        tab._finish_prompt(process, 1)

        rendered = "\n".join(item["body"] for item in tab._messages)
        assert '"type": "error"' not in rendered
        assert '"invalid_request_error"' not in rendered
        assert "새 버전이 필요" in rendered
        assert "모델 목록 새로고침" in rendered
        assert "unknown variant max" not in rendered
        assert any(item["title"] == "오류" for item in tab._messages)
    finally:
        tab.close()


def test_ai_chat_prompt_enter_sends_and_shift_enter_adds_line(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    sent: list[None] = []
    try:
        tab.prompt_edit.sendRequested.disconnect()
        tab.prompt_edit.sendRequested.connect(lambda: sent.append(None))
        tab.prompt_edit.setPlainText("hello")

        tab.prompt_edit.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        )
        assert sent == [None]
        assert tab.prompt_edit.toPlainText() == "hello"

        tab.prompt_edit.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        )
        assert "\n" in tab.prompt_edit.toPlainText()
    finally:
        tab.close()


def test_ai_chat_prompt_auto_grows_and_paste_attaches_files(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    note = tmp_path / "note.txt"
    note.write_text("hello from pasted file", encoding="utf-8")
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        initial_height = tab.prompt_edit.height()
        tab.prompt_edit.setPlainText("\n".join(f"line {index}" for index in range(8)))
        qapp.processEvents()
        assert tab.prompt_edit.height() > initial_height
        assert tab.prompt_edit.height() <= tab.prompt_edit.MAX_HEIGHT

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(note))])
        tab.prompt_edit.insertFromMimeData(mime)

        assert tab._attachments == [note]
        assert tab.attachment_list.count() == 1
        assert tab.attachment_list.isHidden() is False
    finally:
        tab.close()


def test_ai_chat_visible_transcript_expands_for_long_plain_and_markdown_without_body_scrollers(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        _show_chat_tab(qapp, tab, width=900, height=720)
        long_text = "\n".join(
            f"긴 한글 응답 줄 {index:03d}: 주변 무선 네트워크 상태를 분석한 결과입니다."
            for index in range(120)
        )
        long_token = "netsh-" + ("verylongcommandsegment" * 20)
        long_url = "https://example.internal/" + ("network-diagnostic/" * 18)
        markdown = "\n".join(
            [
                "**요약**",
                "- 주변 AP 신호와 채널 사용량을 확인했습니다.",
                f"- 긴 URL: {long_url}",
                "",
                "```powershell",
                long_token,
                "```",
            ]
        )

        tab._append_block("AI", long_text)
        tab._append_block("AI", markdown)
        qapp.processEvents()
        qapp.processEvents()

        bubbles = _message_bubbles(tab)
        assert len(bubbles) == 2
        plain_body = _message_body_widget(bubbles[0])
        markdown_body = _message_body_widget(bubbles[-1])
        assert "긴 한글 응답 줄 119" in _widget_visible_text(plain_body)
        assert "요약" in _widget_visible_text(markdown_body)
        assert long_token in _widget_visible_text(markdown_body)
        _assert_body_has_no_internal_scroll(plain_body)
        _assert_body_has_no_internal_scroll(markdown_body)
        _assert_message_container_contains(tab, bubbles[-1])
        _assert_outer_transcript_scrolls(tab)
    finally:
        tab.close()


def test_ai_chat_message_container_stays_tall_enough_after_long_content_reflow(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        _show_chat_tab(qapp, tab, width=940, height=720)
        long_token = "netsh-" + ("verylongcommandsegment" * 18)
        long_url = "https://example.internal/" + ("network-diagnostic/" * 16)
        markdown = "\n".join(
            [
                "**요약**",
                "- 주변 AP 신호와 채널 사용량을 확인했습니다.",
                f"- 긴 URL: {long_url}",
                *(f"- 분석 항목 {index:02d}: 채널 혼잡도와 신호 품질을 확인했습니다." for index in range(24)),
                "",
                "```powershell",
                long_token,
                "```",
            ]
        )

        tab._append_block("AI", markdown)
        qapp.processEvents()

        wide_bubble = _message_bubbles(tab)[-1]
        wide_body = _message_body_widget(wide_bubble)
        wide_body_width = wide_body.width()
        assert "요약" in _widget_visible_text(wide_body)
        assert long_token in _widget_visible_text(wide_body)
        _assert_body_has_no_internal_scroll(wide_body)
        _assert_message_container_contains(tab, wide_bubble)

        tab.resize(560, 720)
        tab._render_transcript()
        qapp.processEvents()

        narrow_bubble = _message_bubbles(tab)[-1]
        narrow_body = _message_body_widget(narrow_bubble)
        assert narrow_body.width() <= wide_body_width
        assert "요약" in _widget_visible_text(narrow_body)
        assert long_token in _widget_visible_text(narrow_body)
        _assert_body_has_no_internal_scroll(narrow_body)
        _assert_message_container_contains(tab, narrow_bubble)
        _assert_outer_transcript_scrolls(tab)
    finally:
        tab.close()


def test_ai_chat_clear_transcript_resets_oversized_minimum_height(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        _show_chat_tab(qapp, tab, width=900, height=720)
        long_text = "\n".join(
            f"긴 한글 응답 줄 {index:03d}: 채팅 기록을 지운 뒤 최소 높이가 줄어드는지 확인합니다."
            for index in range(140)
        )

        tab._append_block("AI", long_text)
        qapp.processEvents()
        qapp.processEvents()

        inflated_minimum_height = tab.message_container.minimumHeight()
        assert inflated_minimum_height > tab.transcript_scroll.viewport().height()
        assert tab.transcript_scroll.verticalScrollBar().maximum() > 0

        tab.clear_transcript()
        qapp.processEvents()
        qapp.processEvents()

        assert tab._messages == []
        assert tab.message_container.minimumHeight() <= tab.transcript_scroll.viewport().height()
        assert tab.transcript_scroll.verticalScrollBar().maximum() == 0
    finally:
        tab.close()


def test_ai_chat_model_catalog_preserves_selection_and_rebuilds_supported_options(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    saved_configs: list[dict] = []
    state = SimpleNamespace(
        app_config={
            "ai_chat": {
                "active_provider": "codex",
                "providers": {
                    "codex": {
                        "model": "model-b",
                        "reasoning_effort": "xhigh",
                        "speed": "fast",
                    }
                },
            }
        },
        paths=SimpleNamespace(root=tmp_path, config_dir=tmp_path / "config", exports_dir=tmp_path),
        save_app_config=lambda config: saved_configs.append(config),
    )
    tab = AiChatTab(state)
    catalog = AiModelCatalog(
        provider_key="codex",
        fetched_at="2026-07-10T00:00:00Z",
        cli_path="C:/Codex/codex.exe",
        cli_version="codex-cli 9.9",
        source="live",
        models=[
            AiModelDescriptor(
                id="id-a",
                model="model-a",
                display_name="모델 A",
                supported_reasoning_efforts=["high", "low", "xhigh", "max", "ultra"],
                default_reasoning_effort="high",
                input_modalities=["text", "image"],
                speed_tiers=["fast"],
                is_default=True,
                source="live",
            ),
            AiModelDescriptor(
                id="id-b",
                model="model-b",
                display_name="모델 B",
                supported_reasoning_efforts=["low"],
                default_reasoning_effort="low",
                input_modalities=["text"],
                source="live",
            ),
        ],
    )

    try:
        tab._model_catalogs["codex"] = catalog
        tab._populate_model_combo("codex", "model-b")

        assert tab.model_combo.itemText(0) == "자동 선택 (현재 기본: 모델 A)"
        assert tab.model_combo.currentData() == "model-b"
        assert tab.reasoning_combo.currentData() == ""
        assert tab.speed_combo.currentData() == ""
        assert tab._providers["codex"].reasoning_effort == ""
        assert tab._providers["codex"].speed == ""
        assert saved_configs[-1]["ai_chat"]["providers"]["codex"]["reasoning_effort"] == ""
        assert saved_configs[-1]["ai_chat"]["providers"]["codex"]["speed"] == ""
        assert "현재 계정에서 사용 가능" in tab.model_detail_label.text()
        assert "입력: 텍스트" in tab.model_detail_label.text()

        tab._set_combo_data(tab.model_combo, "model-a")
        qapp.processEvents()

        reasoning_values = [tab.reasoning_combo.itemData(index) for index in range(tab.reasoning_combo.count())]
        speed_values = [tab.speed_combo.itemData(index) for index in range(tab.speed_combo.count())]
        assert reasoning_values == ["", "high", "low", "xhigh", "max", "ultra"]
        assert tab.reasoning_combo.currentData() == ""
        assert speed_values == ["", "fast"]
        assert tab.speed_combo.currentData() == ""
        assert tab.current_provider_config().model == "model-a"
        assert "입력: 텍스트, 이미지" in tab.model_detail_label.text()
        assert "추론 단계: 높음, 낮음, 매우 높음, 최대, 울트라" in tab.model_detail_label.text()
        assert "빠른 응답: 지원" in tab.model_detail_label.text()

        tab._set_combo_data(tab.reasoning_combo, "xhigh")
        tab._set_combo_data(tab.speed_combo, "fast")
        qapp.processEvents()
        assert tab._providers["codex"].reasoning_effort == "xhigh"
        assert tab._providers["codex"].speed == "fast"
        assert saved_configs[-1]["ai_chat"]["providers"]["codex"]["reasoning_effort"] == "xhigh"
        assert saved_configs[-1]["ai_chat"]["providers"]["codex"]["speed"] == "fast"

        tab._set_combo_data(tab.reasoning_combo, "ultra")
        qapp.processEvents()
        assert tab._providers["codex"].reasoning_effort == "ultra"
        assert saved_configs[-1]["ai_chat"]["providers"]["codex"]["reasoning_effort"] == "ultra"

        tab._providers["codex"].speed = "flex"
        tab._populate_model_combo("codex", "model-a")
        assert tab.speed_combo.currentData() == "flex"
        assert tab.speed_combo.currentText() == "유연 처리 (기존 설정)"
        assert tab._codex_runtime_config_args(AiProviderConfig(key="codex", speed="priority")) == []
    finally:
        tab.close()


def test_ai_chat_new_catalog_does_not_auto_change_current_model_or_other_provider_ui(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={
            "ai_chat": {
                "active_provider": "codex",
                "providers": {"codex": {"model": "selected-model"}},
            }
        },
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)
    refreshed = AiModelCatalog(
        provider_key="codex",
        source="live",
        models=[
            AiModelDescriptor(id="new-default", model="new-default", is_default=True, source="live"),
        ],
    )

    try:
        tab._active_model_catalog_request = ("codex", 1)
        tab._accept_model_catalog_result("codex", 1, refreshed)
        assert tab.model_combo.currentData() == "selected-model"
        assert "목록에서 확인되지 않음" in tab.model_combo.currentText()

        tab._set_combo_data(tab.provider_combo, "gemini")
        qapp.processEvents()
        gemini_values_before = [tab.model_combo.itemData(index) for index in range(tab.model_combo.count())]
        tab._active_model_catalog_request = ("codex", 2)
        tab._accept_model_catalog_result("codex", 2, refreshed)
        gemini_values_after = [tab.model_combo.itemData(index) for index in range(tab.model_combo.count())]

        assert tab.current_provider_key() == "gemini"
        assert gemini_values_after == gemini_values_before
        assert "new-default" not in gemini_values_after
    finally:
        tab.close()


def test_ai_chat_catalog_error_keeps_source_time_status_and_cached_selection(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={
            "ai_chat": {
                "active_provider": "codex",
                "providers": {"codex": {"model": "cached-model"}},
            }
        },
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)
    catalog = AiModelCatalog(
        provider_key="codex",
        source="cache",
        fetched_at="2026-07-10T00:00:00Z",
        cli_path="C:/Codex/codex.exe",
        cli_version="codex-cli 9.9",
        models=[
            AiModelDescriptor(
                id="cached-id",
                model="cached-model",
                display_name="저장된 모델",
                supported_reasoning_efforts=["medium"],
                default_reasoning_effort="medium",
                source="live",
            )
        ],
    )

    try:
        tab._model_catalogs["codex"] = catalog
        tab._populate_model_combo("codex", "cached-model")
        initial_status = tab.model_catalog_status_label.text()
        assert "저장된 모델 목록" in initial_status
        assert "마지막 갱신" in initial_status
        assert "codex-cli 9.9" in initial_status
        assert "마지막 확인 기준 현재 계정에서 사용 가능" in tab.model_detail_label.text()

        tab._active_model_catalog_request = ("codex", 7)
        tab._handle_model_catalog_error("codex", 7, "JSON-RPC model/list failed")

        failed_status = tab.model_catalog_status_label.text()
        assert "모델 목록 갱신 실패" in failed_status
        assert "기존 목록을 유지합니다" in failed_status
        assert "저장된 모델 목록" in failed_status
        assert "마지막 갱신" in failed_status
        assert "codex-cli 9.9" in failed_status
        assert tab.model_catalog_status_label.toolTip() == "JSON-RPC model/list failed"
        assert tab.model_combo.currentData() == "cached-model"
        assert tab.send_button.isEnabled()
    finally:
        tab.close()


def test_ai_chat_direct_model_id_validates_and_marks_support_unknown(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    warnings: list[str] = []
    monkeypatch.setattr("app.ui.tabs.ai_chat_tab.QMessageBox.warning", lambda _parent, _title, text: warnings.append(text))
    tab = AiChatTab(state)

    try:
        monkeypatch.setattr(
            "app.ui.tabs.ai_chat_tab.QInputDialog.getText",
            lambda *_args, **_kwargs: ("vendor/model:preview-1", True),
        )
        tab._enter_custom_model()

        assert tab.model_combo.currentData() == "vendor/model:preview-1"
        assert "목록에서 확인되지 않음" in tab.model_combo.currentText()
        assert tab.current_provider_config().model == "vendor/model:preview-1"
        assert "지원 여부 미확인" in tab.model_detail_label.text()

        monkeypatch.setattr(
            "app.ui.tabs.ai_chat_tab.QInputDialog.getText",
            lambda *_args, **_kwargs: ("invalid model id", True),
        )
        tab._enter_custom_model()

        assert warnings
        assert tab.model_combo.currentData() == "vendor/model:preview-1"
    finally:
        tab.close()


@pytest.mark.parametrize(
    ("model_id", "is_valid"),
    [
        ("a", True),
        ("m" * 128, True),
        ("m" * 129, False),
        (".preview", True),
        ("_preview", True),
        (":preview", True),
        ("/preview", True),
        ("-preview", True),
        (" model", False),
        ("model ", False),
        ("model\tpreview", False),
        ("model\npreview", False),
        ("model\x01preview", False),
    ],
)
def test_ai_chat_direct_model_id_boundary_and_character_rules(
    qapp,
    tmp_path,
    monkeypatch,
    model_id,
    is_valid,
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    warnings: list[str] = []
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.QInputDialog.getText",
        lambda *_args, **_kwargs: (model_id, True),
    )
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)

    try:
        tab._enter_custom_model()

        if is_valid:
            assert warnings == []
            assert tab.model_combo.currentData() == model_id
            assert tab.current_provider_config().model == model_id
            assert "지원 여부 미확인" in tab.model_detail_label.text()
        else:
            assert warnings
            assert tab.model_combo.currentData() == ""
            assert tab.current_provider_config().model == ""
    finally:
        tab.close()


@pytest.mark.parametrize("width,height", [(1024, 680), (1280, 800)])
def test_ai_chat_connection_model_details_are_not_clipped(qapp, tmp_path, monkeypatch, width, height):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(AiChatTab, "_ensure_model_catalog_fresh", lambda self, *_args, **_kwargs: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)
    catalog = AiModelCatalog(
        provider_key="codex",
        source="live",
        models=[
            AiModelDescriptor(
                id="long-model-id",
                model="long-model-id",
                display_name="긴 이름을 가진 현재 계정 사용 가능 모델",
                supported_reasoning_efforts=["minimal", "low", "medium", "high", "xhigh"],
                input_modalities=["text", "image"],
                speed_tiers=["fast"],
                is_default=True,
                source="live",
            )
        ],
    )

    try:
        tab._model_catalogs["codex"] = catalog
        tab._populate_model_combo("codex")
        tab.ai_chat_tabs.setCurrentWidget(tab.connection_page)
        tab.resize(width, height)
        tab.show()
        qapp.processEvents()

        assert tab.model_detail_label.height() >= tab.model_detail_label.heightForWidth(tab.model_detail_label.width())
        assert tab.model_catalog_status_label.height() >= tab.model_catalog_status_label.heightForWidth(
            tab.model_catalog_status_label.width()
        )
        assert tab.provider_group.geometry().bottom() <= tab.connection_page.height()
    finally:
        tab.close()


def test_ai_chat_model_combo_text_fits_minimum_main_window_workspace(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(AiChatTab, "_ensure_model_catalog_fresh", lambda self, *_args, **_kwargs: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)
    display_name = "GPT-5 Codex 엔터프라이즈 장기 컨텍스트 이미지 분석 고성능 미리보기"
    catalog = AiModelCatalog(
        provider_key="codex",
        source="live",
        models=[
            AiModelDescriptor(
                id="enterprise-preview",
                model="enterprise-preview",
                display_name=display_name,
                supported_reasoning_efforts=["high", "low", "xhigh"],
                input_modalities=["text", "image"],
                speed_tiers=["fast"],
                is_default=True,
                source="live",
            )
        ],
    )

    try:
        tab._model_catalogs["codex"] = catalog
        tab._populate_model_combo("codex")
        tab.ai_chat_tabs.setCurrentWidget(tab.connection_page)
        # MainWindow minimum width is 1024 and its navigation panel can consume 208 px.
        tab.resize(816, 620)
        tab.show()
        qapp.processEvents()

        assert tab.model_combo.width() > tab.reasoning_combo.width()
        for model_value in ("", "enterprise-preview"):
            tab._set_combo_data(tab.model_combo, model_value)
            qapp.processEvents()
            option = QStyleOptionComboBox()
            tab.model_combo.initStyleOption(option)
            edit_rect = tab.model_combo.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option,
                QStyle.SubControl.SC_ComboBoxEditField,
                tab.model_combo,
            )
            rendered_text_width = tab.model_combo.fontMetrics().horizontalAdvance(tab.model_combo.currentText())
            assert rendered_text_width <= edit_rect.width(), (
                tab.model_combo.currentText(),
                rendered_text_width,
                edit_rect.width(),
            )
    finally:
        tab.close()
