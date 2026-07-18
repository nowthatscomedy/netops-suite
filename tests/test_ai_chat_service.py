from __future__ import annotations

import json
import re
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPoint, QProcess, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QKeyEvent, QTextCursor, QTextTable
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QStyle,
    QStyleOptionComboBox,
    QTabWidget,
    QToolButton,
    QWidget,
)

from app.ui.tabs.ai_chat_tab import AiChatTab
from app.ui.common.theme import APP_STYLE_SHEET
from app.models.ftp_models import FtpProfile
from app.models.ai_models import (
    AiModelCatalog,
    AiModelDescriptor,
    AiProviderConfig,
    normalize_ai_chat_config,
)
from app.models.network_models import NetworkAdapterInfo
from app.models.profile_models import IPProfile
from app.models.result_models import OperationResult
from app.models.scp_models import ScpProfile
from app.assistant import (
    ToolResult,
    build_netops_tool_registry,
    tool_call_from_netops_action,
)
from app.assistant.planner import ACTION_TOOL_MAP
from app.services import ai_agent_service as ai_service
from app.services.ai_agent_service import (
    build_chat_invocation,
    build_help_invocation,
    decode_cli_output,
    diagnose_cli_error,
    extra_arg_options_from_help,
    extract_assistant_text_from_cli_line,
    extract_cli_session_id,
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


def _show_chat_tab(
    qapp, tab: AiChatTab, *, width: int = 900, height: int = 720
) -> None:
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


def _document_tables(body: QWidget) -> list[QTextTable]:
    tables: list[QTextTable] = []

    def collect(frame) -> None:
        for child in frame.childFrames():
            if isinstance(child, QTextTable):
                tables.append(child)
            collect(child)

    collect(body.document().rootFrame())
    return tables


def _table_cell_text(table: QTextTable, row: int, column: int) -> str:
    cell = table.cellAt(row, column)
    cursor = cell.firstCursorPosition()
    cursor.setPosition(
        cell.lastCursorPosition().position(), QTextCursor.MoveMode.KeepAnchor
    )
    return cursor.selectedText().replace("\u2028", "\n").replace("\u2029", "\n").strip()


def _widget_visible_text(widget: QWidget) -> str:
    reader = getattr(widget, "toPlainText", None)
    if callable(reader):
        return str(reader())
    label_text = getattr(widget, "text", None)
    if callable(label_text):
        return str(label_text())
    return "\n".join(
        _widget_visible_text(child) for child in widget.findChildren(QWidget)
    )


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
    config = AiProviderConfig(
        key="codex", command_path="codex", model="gpt-5", role_prompt="review"
    )

    invocation = build_chat_invocation(config, "hello", working_dir="C:/repo")

    assert Path(invocation.program).name.lower() in {"codex", "codex.exe", "codex.cmd"}
    assert invocation.args == [
        "-a",
        "never",
        "-s",
        "read-only",
        "exec",
        "--skip-git-repo-check",
        "--json",
        "--model",
        "gpt-5",
        "-",
    ]
    assert "Agent role:\nreview" in invocation.stdin_text
    assert "User request:\nhello" in invocation.stdin_text
    assert invocation.working_dir == "C:/repo"


def test_codex_help_invocation_targets_exec_help():
    invocation = build_help_invocation(
        AiProviderConfig(key="codex", command_path="codex"), working_dir="C:/repo"
    )

    assert invocation.args == ["-a", "never", "-s", "read-only", "exec", "--help"]
    assert invocation.working_dir == "C:/repo"


def test_codex_chat_invocation_maps_workspace_and_full_access_without_bypass(
    tmp_path,
):
    workspace_root = tmp_path / "NetOps Suite" / "config"
    inspector_dir = tmp_path / "NetOps Suite" / "inspector"
    logs_dir = tmp_path / "NetOps Suite" / "logs"
    config = AiProviderConfig(key="codex", command_path="codex", model="gpt-5")

    workspace = build_chat_invocation(
        config,
        "create profile",
        codex_sandbox="workspace-write",
        codex_workspace_root=str(workspace_root),
        codex_writable_dirs=(str(inspector_dir), str(logs_dir), str(inspector_dir)),
    )
    full = build_chat_invocation(
        config,
        "continue",
        session_id="codex-thread-id",
        codex_sandbox="danger-full-access",
        codex_workspace_root=str(workspace_root),
        codex_writable_dirs=(str(inspector_dir),),
    )

    assert workspace.args[:8] == [
        "-a",
        "never",
        "-s",
        "workspace-write",
        "-C",
        str(workspace_root),
        "--add-dir",
        str(inspector_dir),
    ]
    assert workspace.args.count("--add-dir") == 2
    assert workspace.args[8:10] == ["--add-dir", str(logs_dir)]
    assert workspace.args[10:13] == [
        "exec",
        "--skip-git-repo-check",
        "--json",
    ]
    assert "--dangerously-bypass-approvals-and-sandbox" not in workspace.args

    assert full.args[:6] == [
        "-a",
        "never",
        "-s",
        "danger-full-access",
        "-C",
        str(workspace_root),
    ]
    assert full.args[6:10] == [
        "exec",
        "resume",
        "--skip-git-repo-check",
        "--json",
    ]
    assert "--add-dir" not in full.args
    assert "--dangerously-bypass-approvals-and-sandbox" not in full.args

    claude = build_chat_invocation(
        AiProviderConfig(key="claude", command_path="claude"),
        "hello",
        codex_sandbox="danger-full-access",
        codex_workspace_root=str(workspace_root),
        codex_writable_dirs=(str(inspector_dir),),
    )
    assert "-s" not in claude.args
    assert "--add-dir" not in claude.args
    assert "-C" not in claude.args


def test_codex_chat_invocation_rejects_unknown_sandbox_mode():
    with pytest.raises(ValueError, match="Unsupported Codex sandbox mode"):
        build_chat_invocation(
            AiProviderConfig(key="codex", command_path="codex"),
            "hello",
            codex_sandbox="unbounded",
        )


def test_codex_windowsapps_alias_command_uses_real_candidate(monkeypatch):
    local_codex = r"C:\Users\me\AppData\Local\OpenAI\Codex\bin\codex.exe"
    config = AiProviderConfig(
        key="codex",
        command_path=r"C:\Program Files\WindowsApps\OpenAI.Codex_x64__2p2nqsd0c76g0\codex.exe",
    )

    monkeypatch.setattr(
        ai_service, "_provider_program_candidates", lambda _spec: [local_codex]
    )

    assert resolve_provider_program(config) == local_codex


def test_codex_auto_discovery_prefers_appdata_npm_wrapper_over_desktop_bundle(
    tmp_path, monkeypatch
):
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

    candidates = ai_service._provider_program_candidates(
        ai_service.PROVIDER_SPECS["codex"]
    )

    assert candidates[:2] == [str(npm_codex), str(desktop_codex)]
    assert resolve_provider_program(AiProviderConfig(key="codex")) == str(npm_codex)


def test_codex_auto_discovery_falls_back_to_desktop_bundle_without_npm_wrapper(
    tmp_path, monkeypatch
):
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
    claude = build_chat_invocation(
        AiProviderConfig(key="claude", command_path="claude"), "hello"
    )
    gemini = build_chat_invocation(
        AiProviderConfig(key="gemini", command_path="gemini"), "hello"
    )

    assert claude.args[0] == "-p"
    assert "hello" in claude.args[1]
    assert "--output-format" in claude.args
    assert gemini.args[0] == "-p"
    assert "hello" in gemini.args[1]
    assert "--output-format" in gemini.args
    assert "stream-json" in gemini.args


def test_chat_invocations_resume_exact_provider_sessions():
    codex = build_chat_invocation(
        AiProviderConfig(key="codex", command_path="codex", model="gpt-5"),
        "continue",
        session_id="codex-thread-id",
    )
    claude = build_chat_invocation(
        AiProviderConfig(key="claude", command_path="claude", model="sonnet"),
        "continue",
        session_id="claude-session-id",
    )
    gemini = build_chat_invocation(
        AiProviderConfig(key="gemini", command_path="gemini", model="gemini-2.5-pro"),
        "continue",
        session_id="gemini-session-id",
    )

    assert codex.args == [
        "-a",
        "never",
        "-s",
        "read-only",
        "exec",
        "resume",
        "--skip-git-repo-check",
        "--json",
        "--model",
        "gpt-5",
        "codex-thread-id",
        "-",
    ]
    assert codex.stdin_text.endswith("User request:\ncontinue")
    assert claude.args[:4] == ["--resume", "claude-session-id", "--model", "sonnet"]
    assert claude.args[4] == "-p"
    assert claude.args[-3:] == ["--output-format", "stream-json", "--verbose"]
    assert gemini.args[:4] == [
        "--resume",
        "gemini-session-id",
        "--model",
        "gemini-2.5-pro",
    ]
    assert gemini.args[4] == "-p"
    assert gemini.args[-2:] == ["--output-format", "stream-json"]


def test_cli_session_ids_and_assistant_text_are_extracted_from_provider_events():
    codex_init = json.dumps({"type": "thread.started", "thread_id": "codex-thread"})
    claude_init = json.dumps(
        {"type": "system", "subtype": "init", "session_id": "claude-session"}
    )
    gemini_init = json.dumps(
        {"type": "init", "session_id": "gemini-session", "model": "gemini"}
    )

    assert extract_cli_session_id("codex", codex_init) == "codex-thread"
    assert extract_cli_session_id("claude", claude_init) == "claude-session"
    assert extract_cli_session_id("gemini", gemini_init) == "gemini-session"
    assert (
        extract_cli_session_id("codex", '{"type":"turn.started","thread_id":"wrong"}')
        == ""
    )
    assert extract_cli_session_id("gemini", '{"type":"init","session_id":""}') == ""

    claude_assistant = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Claude reply"}]},
            "session_id": "claude-session",
        }
    )
    gemini_user = json.dumps(
        {"type": "message", "role": "user", "content": "do not echo"}
    )
    gemini_assistant = json.dumps(
        {"type": "message", "role": "assistant", "content": "Gemini reply"}
    )

    assert (
        extract_assistant_text_from_cli_line("claude", claude_assistant)
        == "Claude reply"
    )
    assert extract_assistant_text_from_cli_line("claude", claude_init) == ""
    assert extract_assistant_text_from_cli_line("gemini", gemini_user) == ""
    assert (
        extract_assistant_text_from_cli_line("gemini", gemini_assistant)
        == "Gemini reply"
    )


def test_cli_json_line_text_extraction_handles_common_stream_shapes():
    assert (
        extract_text_from_cli_line('{"content":[{"type":"text","text":"hi"}]}') == "hi"
    )
    assert extract_text_from_cli_line('{"delta":" there"}') == "there"
    assert extract_text_from_cli_line("plain output") == "plain output"


def test_cli_output_decodes_and_filters_windows_process_noise():
    cp949_noise = (
        "성공: PID 7708인 프로세스(PID 21356의 자식 프로세스)가 종료되었습니다."
    )
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

    assert any(
        option.flag == "--profile" and option.value_hint == "<CONFIG_PROFILE>"
        for option in parsed
    )
    assert any(option.flag == "--sandbox" and option.takes_value for option in parsed)
    assert any(
        option.flag == "--search" and not option.takes_value for option in parsed
    )
    assert "--model" not in {option.flag for option in extra_options}
    assert "--output-format" not in {option.flag for option in extra_options}
    assert "--dangerously-bypass-approvals-and-sandbox" not in {
        option.flag for option in extra_options
    }
    assert "--sandbox" not in {option.flag for option in extra_options}
    assert {"--profile", "--search"}.issubset({option.flag for option in extra_options})


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
    config_path.write_text(
        'model = "gpt-5.5"\nservice_tier = "priority"\n', encoding="utf-8"
    )
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


def test_codex_blocking_reasoning_value_is_repaired_without_changing_service_tier(
    tmp_path,
):
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
    assert (
        'model_reasoning_effort는 "none", "minimal", "low", "medium", "high", "xhigh"만 지원'
        in diagnosis
    )
    assert 'model_reasoning_effort = "xhigh"로 자동 복구' in diagnosis

    result = repair_cli_configuration_error("codex", raw_error)

    assert result.attempted is True
    assert result.repaired is True
    repaired = config_path.read_text(encoding="utf-8")
    assert 'model_reasoning_effort = "xhigh"' in repaired
    assert 'service_tier = "priority"' in repaired
    assert 'model_reasoning_effort = "ultra"' in Path(result.backup_path).read_text(
        encoding="utf-8"
    )


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
    regular, separated_warning = split_codex_model_cache_warning(
        f"{warning}\nprimary failure"
    )
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
    english_wifi_scan = plan_netops_chat_action(
        "nearby wi-fi scan for 2 minutes every 30 seconds"
    )
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


def test_netops_chat_action_planner_preserves_multi_target_requests():
    ping = plan_netops_chat_action("8.8.8.8, 1.1.1.1 핑테스트 계속 해줘")
    external_ping = plan_netops_chat_action("9.9.9.9, one.one.one.one 외부 핑 테스트")
    tcp_batch = plan_netops_chat_action("example.com, 192.0.2.10 TCP 80, 443 포트 확인")
    paired_tcp = plan_netops_chat_action("example.com:80, 192.0.2.10:443 TCP 확인")
    dns = plan_netops_chat_action("example.com, example.net DNS MX 조회")
    tracert = plan_netops_chat_action("8.8.8.8, 1.1.1.1 tracert")
    pathping = plan_netops_chat_action("8.8.8.8, 1.1.1.1 pathping")
    subnet = plan_netops_chat_action("192.168.10.0/24, 10.0.0.0/8 서브넷 계산")
    oui = plan_netops_chat_action("00:11:22:33:44:55, AA-BB-CC-DD-EE-FF OUI 조회")

    assert ping is not None
    assert ping.kind == "ping_batch"
    assert ping.targets == ("8.8.8.8", "1.1.1.1")
    assert ping.continuous is True
    ping_call = tool_call_from_netops_action(ping)
    assert ping_call.tool_name == "net.ping.batch"
    assert ping_call.arguments == {
        "targets": ["8.8.8.8", "1.1.1.1"],
        "continuous": True,
    }

    assert external_ping is not None
    assert external_ping.kind == "external_ping"
    assert external_ping.targets == ("9.9.9.9", "one.one.one.one")
    assert tool_call_from_netops_action(external_ping).arguments == {
        "targets": ["9.9.9.9", "one.one.one.one"]
    }

    assert tcp_batch is not None
    assert tcp_batch.kind == "tcp_batch"
    assert tcp_batch.targets == ("example.com", "192.0.2.10")
    assert tcp_batch.ports == (80, 443)
    assert tool_call_from_netops_action(tcp_batch).arguments == {
        "targets": ["example.com", "192.0.2.10"],
        "ports": [80, 443],
    }

    assert paired_tcp is not None
    assert paired_tcp.endpoints == (
        ("example.com", 80),
        ("192.0.2.10", 443),
    )
    paired_actions = AiChatTab._expanded_netops_chat_actions(paired_tcp)
    assert [(action.target, action.port) for action in paired_actions] == [
        ("example.com", 80),
        ("192.0.2.10", 443),
    ]
    assert [
        (call.tool_name, call.arguments)
        for call in map(tool_call_from_netops_action, paired_actions)
    ] == [
        ("net.tcp_check", {"target": "example.com", "port": 80}),
        ("net.tcp_check", {"target": "192.0.2.10", "port": 443}),
    ]
    with pytest.raises(ValueError, match="다중 대상"):
        tool_call_from_netops_action(paired_tcp)
    with pytest.raises(ValueError, match="다중 대상"):
        tool_call_from_netops_action(dns)

    repeatable_actions = {
        dns: ("example.com", "example.net"),
        tracert: ("8.8.8.8", "1.1.1.1"),
        pathping: ("8.8.8.8", "1.1.1.1"),
        subnet: ("192.168.10.0/24", "10.0.0.0/8"),
        oui: ("00:11:22:33:44:55", "AA-BB-CC-DD-EE-FF"),
    }
    for action, expected_targets in repeatable_actions.items():
        assert action is not None
        assert action.targets == expected_targets
        expanded = AiChatTab._expanded_netops_chat_actions(action)
        assert [item.target for item in expanded] == list(expected_targets)


@pytest.mark.parametrize(
    ("prompt", "expected_calls"),
    [
        (
            "example.com, example.net DNS MX 8.8.8.8 서버로 조회",
            [
                (
                    "net.dns.lookup",
                    {
                        "query": "example.com",
                        "record_type": "MX",
                        "server": "8.8.8.8",
                    },
                ),
                (
                    "net.dns.lookup",
                    {
                        "query": "example.net",
                        "record_type": "MX",
                        "server": "8.8.8.8",
                    },
                ),
            ],
        ),
        (
            "8.8.8.8, 1.1.1.1 tracert -d",
            [
                ("tracert", {"target": "8.8.8.8", "resolve_names": False}),
                ("tracert", {"target": "1.1.1.1", "resolve_names": False}),
            ],
        ),
        (
            "8.8.8.8, 1.1.1.1 pathping -n",
            [
                ("pathping", {"target": "8.8.8.8", "resolve_names": False}),
                ("pathping", {"target": "1.1.1.1", "resolve_names": False}),
            ],
        ),
        (
            "192.168.10.0/24, 10.0.0.0/8 서브넷 계산",
            [
                ("net.subnet.calculate", {"cidr": "192.168.10.0/24"}),
                ("net.subnet.calculate", {"cidr": "10.0.0.0/8"}),
            ],
        ),
        (
            "00:11:22:33:44:55, AA-BB-CC-DD-EE-FF OUI 조회",
            [
                ("oui.lookup", {"mac_address": "00:11:22:33:44:55"}),
                ("oui.lookup", {"mac_address": "AA-BB-CC-DD-EE-FF"}),
            ],
        ),
    ],
)
def test_repeatable_netops_actions_expand_to_every_exact_tool_call(
    prompt, expected_calls
):
    action = plan_netops_chat_action(prompt)
    registry = build_netops_tool_registry()

    assert action is not None
    expanded = AiChatTab._expanded_netops_chat_actions(action)
    calls = [tool_call_from_netops_action(item) for item in expanded]

    assert [(call.tool_name, call.arguments) for call in calls] == expected_calls
    assert all(registry.resolve(call) is not None for call in calls)
    assert all(item.targets == (item.target,) for item in expanded)


def test_netops_chat_action_planner_preserves_probe_options_and_roles():
    timed_tcp = plan_netops_chat_action(
        "example.com, openai.com TCP 80, 443 포트 5회 timeout 2초"
    )
    port_range = plan_netops_chat_action("example.com TCP 포트 80-82 확인")
    repeated_pairs = plan_netops_chat_action(
        "example.com:80, example.com:443, openai.com:22 TCP check"
    )
    ambiguous_pairs = plan_netops_chat_action(
        "example.com:80, openai.com:443, 8443 TCP check"
    )
    dns = plan_netops_chat_action("example.com, example.net DNS MX 8.8.8.8 서버로 조회")
    alias_ping = plan_netops_chat_action(
        "구글 핑 7회 timeout 1500ms 중지할 때까지 계속"
    )

    assert timed_tcp is not None
    assert timed_tcp.kind == "tcp_batch"
    assert timed_tcp.targets == ("example.com", "openai.com")
    assert timed_tcp.ports == (80, 443)
    assert timed_tcp.count == 5
    assert timed_tcp.timeout_ms == 2000
    assert tool_call_from_netops_action(timed_tcp).arguments == {
        "targets": ["example.com", "openai.com"],
        "ports": [80, 443],
        "count": 5,
        "timeout_ms": 2000,
    }

    assert port_range is not None
    assert port_range.kind == "tcp_batch"
    assert port_range.ports == (80, 81, 82)

    assert repeated_pairs is not None
    assert repeated_pairs.endpoints == (
        ("example.com", 80),
        ("example.com", 443),
        ("openai.com", 22),
    )
    assert [
        (item.target, item.port)
        for item in AiChatTab._expanded_netops_chat_actions(repeated_pairs)
    ] == list(repeated_pairs.endpoints)
    assert ambiguous_pairs is None

    assert dns is not None
    assert dns.targets == ("example.com", "example.net")
    assert dns.server == "8.8.8.8"
    dns_calls = [
        tool_call_from_netops_action(item)
        for item in AiChatTab._expanded_netops_chat_actions(dns)
    ]
    assert [call.arguments for call in dns_calls] == [
        {
            "query": "example.com",
            "record_type": "MX",
            "server": "8.8.8.8",
        },
        {
            "query": "example.net",
            "record_type": "MX",
            "server": "8.8.8.8",
        },
    ]

    assert alias_ping is not None
    assert alias_ping.kind == "ping_batch"
    assert alias_ping.targets == ("google.com",)
    assert alias_ping.count == 7
    assert alias_ping.timeout_ms == 1500
    assert alias_ping.continuous is True
    assert tool_call_from_netops_action(alias_ping).arguments == {
        "targets": ["google.com"],
        "count": 7,
        "timeout_ms": 1500,
        "continuous": True,
    }


def test_netops_chat_action_does_not_silently_apply_multi_interface_changes():
    assert plan_netops_chat_action("인터페이스 Ethernet, Wi-Fi DHCP로 변경") is None
    assert (
        plan_netops_chat_action('"Ethernet" DNS 8.8.8.8, "Wi-Fi" DNS 1.1.1.1로 변경')
        is None
    )
    assert (
        plan_netops_chat_action(
            '"Ethernet" IP 192.168.1.20/24, "Wi-Fi" IP 10.0.0.20/24 설정'
        )
        is None
    )


def test_every_planned_netops_action_kind_maps_to_a_registered_tool():
    expected_action_kinds = {
        "ping",
        "ping_batch",
        "external_ping",
        "tcp_check",
        "tcp_batch",
        "subnet_calculate",
        "dns_lookup",
        "dns_flush_cache",
        "public_ip",
        "tracert",
        "pathping",
        "ipconfig",
        "route_print",
        "arp_table",
        "interface_snapshot",
        "set_dns",
        "set_dhcp",
        "set_static_ip",
        "wireless_status",
        "wireless_scan",
        "oui_lookup",
        "oui_cache_refresh",
    }
    registry = build_netops_tool_registry()

    assert set(ACTION_TOOL_MAP) == expected_action_kinds
    for tool_name in ACTION_TOOL_MAP.values():
        assert registry.resolve(tool_name) is not None


@pytest.mark.parametrize(
    ("prompt", "kind", "tool_name", "arguments"),
    [
        ("ipconfig /all 보여줘", "ipconfig", "net.ipconfig.read", {}),
        ("route print 보여줘", "route_print", "net.route.print", {}),
        ("ARP 테이블 보여줘", "arp_table", "net.arp.table", {}),
        (
            "네트워크 인터페이스 상태",
            "interface_snapshot",
            "net.interface.snapshot",
            {},
        ),
        ("현재 Wi-Fi 상태", "wireless_status", "wifi.status", {}),
        (
            "주변 Wi-Fi 스캔 30초 동안 5초 간격",
            "wireless_scan",
            "wifi.scan_nearby",
            {"duration_seconds": 30, "interval_seconds": 5},
        ),
    ],
)
def test_common_netops_read_prompts_translate_to_registered_tools(
    prompt, kind, tool_name, arguments
):
    action = plan_netops_chat_action(prompt)
    registry = build_netops_tool_registry()

    assert action is not None
    assert action.kind == kind
    call = tool_call_from_netops_action(action)
    assert call.tool_name == tool_name
    assert call.arguments == arguments
    assert registry.resolve(call) is not None


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


def test_ai_chat_tab_builds_codex_runtime_options_and_attachments(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)

    note = tmp_path / "note.txt"
    note.write_text("hello from attachment", encoding="utf-8")
    image = tmp_path / "screen.png"
    image.write_bytes(b"\x89PNG\r\n")
    state = SimpleNamespace(
        app_config={
            "ai_chat": {
                "active_provider": "codex",
                "providers": {"codex": {"model": "gpt-5.5"}},
            }
        },
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
        invocation = build_chat_invocation(
            runtime_config, "summarize", context=context, working_dir=str(tmp_path)
        )

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


def test_ai_chat_tab_rejects_binary_attachment_context_and_keeps_active_context_status(
    qapp, tmp_path, monkeypatch
):
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


def test_ai_chat_tab_collects_internal_network_context_for_network_requests(
    qapp, tmp_path, monkeypatch
):
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
        dns_service=SimpleNamespace(
            lookup=lambda *_args: OperationResult(
                True, "dns ok", "google.com A 142.250.1.1"
            )
        ),
        public_ip_service=SimpleNamespace(
            check_public_ip=lambda **_kwargs: OperationResult(
                True, "public ip ok", "1.2.3.4"
            )
        ),
        trace_service=SimpleNamespace(
            run_route_print=lambda: (
                trace_calls.append("route")
                or OperationResult(True, "route ok", "0.0.0.0/0 via 192.168.10.1")
            ),
            run_ipconfig_all=lambda: (
                trace_calls.append("ipconfig")
                or OperationResult(True, "ipconfig ok", "Windows IP Configuration")
            ),
        ),
    )

    tab = AiChatTab(state)
    try:
        assert (
            tab._should_collect_internal_network_context("네트워크 상태 점검해줘")
            is True
        )
        assert tab._should_collect_internal_network_context("IP 주소 확인해줘") is True
        assert tab._should_collect_internal_network_context("안녕") is False
        assert (
            tab._should_collect_internal_network_context("zip 파일 압축해줘") is False
        )
        assert tab._should_collect_internal_network_context("플랜 작성해줘") is False
        assert (
            tab._should_collect_internal_network_context("language 설정 알려줘")
            is False
        )

        context = tab._collect_internal_network_context("네트워크 상태 점검해줘")

        assert "NetOps Suite internal diagnostics snapshot" in context
        assert "adapter snapshot" in context
        assert "192.168.10.1" in context
        assert "8.8.8.8" in context
        assert "google.com" in context
        assert "1.2.3.4" in context
        assert "route ok" in context
        assert {"192.168.10.1", "8.8.8.8", "1.1.1.1"}.issubset(
            set(ping_service.targets)
        )
        assert trace_calls == ["route"]
    finally:
        tab.close()


def test_ai_chat_tab_send_prompt_runs_internal_context_before_cli(
    qapp, tmp_path, monkeypatch
):
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

    def fake_job_start(
        fn, *args, on_result=None, on_error=None, on_finished=None, **_kwargs
    ):
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

        assert (
            getattr(captured["worker_fn"], "__name__", "")
            == "_collect_internal_netops_context"
        )
        assert captured["worker_args"][0] == "네트워크 상태 점검해줘"
        assert isinstance(captured["worker_args"][1], Event)
        assert captured["internal_context"] == "internal context from netops tools"
        assert captured["payload"]["prompt"] == "네트워크 상태 점검해줘"
        assert tab._pending_prompt_payload is None
        assert tab._context_collecting is False
        assert tab.prompt_edit.isEnabled()
        assert all(
            "internal context from netops tools" not in message["body"]
            for message in tab._messages
        )
    finally:
        tab.close()


def test_ai_chat_tab_runs_netops_tool_request_before_ai_cli(
    qapp, tmp_path, monkeypatch
):
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

    def fake_job_start(
        fn, *args, on_result=None, on_error=None, on_finished=None, **_kwargs
    ):
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

        assert (
            getattr(captured["worker_fn"], "__name__", "") == "_run_netops_chat_action"
        )
        assert captured["worker_args"][0].kind == "ping"
        assert ping_service.calls == [("8.8.8.8", 4, 4000)]
        assert captured["payload"]["prompt"] == "8.8.8.8 ping 해줘"
        assert "NetOps Suite tool result" in captured["internal_context"]
        assert "8.8.8.8 reachable" in captured["internal_context"]
        assert any(message["title"] == "NetOps" for message in tab._messages)
        titles = [message["title"] for message in tab._messages]
        assert titles[:3] == ["사용자", "시스템", "NetOps"]
        assert titles.count("사용자") == 1
        transcript = tab._plain_transcript_text()
        assert transcript.index("8.8.8.8 ping 해줘") < transcript.index(
            "NetOps Suite 기능을 실행합니다."
        )
        assert transcript.index("NetOps Suite 기능을 실행합니다.") < transcript.index(
            "NetOps Suite tool result"
        )
        assert tab.prompt_edit.toPlainText() == ""
    finally:
        tab.close()


def test_ai_chat_tab_runs_every_requested_ping_target_and_grounds_ai_context(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.inspect_provider",
        lambda _config: SimpleNamespace(installed=True, detail="ok"),
    )

    class FakePingService:
        def __init__(self):
            self.calls: list[tuple[str, int, int, int, bool]] = []

        def run_multi_ping(
            self,
            raw_targets,
            count,
            timeout_ms,
            max_workers,
            continuous=False,
            cancel_event=None,
        ):
            self.calls.append((raw_targets, count, timeout_ms, max_workers, continuous))
            assert cancel_event is not None
            return [
                SimpleNamespace(
                    target=target,
                    success=True,
                    status="ok",
                    sent=count,
                    received=count,
                    packet_loss=0,
                    min_rtt=1.0,
                    avg_rtt=2.0,
                    max_rtt=3.0,
                    error="",
                )
                for target in raw_targets.splitlines()
            ]

    ping_service = FakePingService()
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
        ping_service=ping_service,
    )
    tab = AiChatTab(state)
    captured: dict[str, object] = {}

    def fake_job_start(
        fn, *args, on_result=None, on_error=None, on_finished=None, **_kwargs
    ):
        if on_result:
            on_result(fn(*args))
        if on_finished:
            on_finished()

    try:
        tab._job_runner.start = fake_job_start
        monkeypatch.setattr(
            tab,
            "_start_prompt_process",
            lambda payload, context: captured.update(
                payload=payload,
                context=context,
            ),
        )
        tab.prompt_edit.setPlainText("8.8.8.8, 1.1.1.1 핑테스트 계속 해줘")

        tab.send_prompt()

        assert ping_service.calls == [("8.8.8.8\n1.1.1.1", 2, 4000, 2, True)]
        netops_message = next(
            message for message in tab._messages if message["title"] == "NetOps"
        )
        assert "- 대상: 8.8.8.8" in netops_message["body"]
        assert "- 대상: 1.1.1.1" in netops_message["body"]
        assert "Target: 8.8.8.8" in netops_message["body"]
        assert "Target: 1.1.1.1" in netops_message["body"]
        assert "2/2 result(s) returned" in netops_message["body"]
        assert "중지할 때까지 연속 실행" in netops_message["body"]
        assert "실제로 포함된 대상과 상태만 설명하세요" in captured["context"]
        assert [message["title"] for message in tab._messages][:3] == [
            "사용자",
            "시스템",
            "NetOps",
        ]
    finally:
        tab.close()


def test_ai_chat_tab_executes_every_expanded_repeatable_action(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)

    class FakeExecutor:
        def __init__(self):
            self.calls = []

        def execute(self, call, _context, *, cancel_event=None):
            self.calls.append(call)
            query = str(call.arguments.get("query", ""))
            return SimpleNamespace(allowed=True), ToolResult.ok(f"result for {query}")

    executor = FakeExecutor()
    tab._assistant_executor = executor
    try:
        action = plan_netops_chat_action("example.com, example.net DNS MX 조회")

        assert action is not None
        output = tab._execute_netops_chat_action(action)

        assert [call.arguments["query"] for call in executor.calls] == [
            "example.com",
            "example.net",
        ]
        assert output.count("### DNS 조회") == 2
        assert "result for example.com" in output
        assert "result for example.net" in output
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

    def fake_job_start(
        fn, *args, on_result=None, on_error=None, on_finished=None, **_kwargs
    ):
        captured["worker_args"] = args
        if on_result:
            on_result(fn(*args))
        if on_finished:
            on_finished()

    try:
        tab._job_runner.start = fake_job_start
        monkeypatch.setattr(
            tab,
            "_start_prompt_process",
            lambda payload, context: captured.update(context=context),
        )
        tab.prompt_edit.setPlainText("외부 핑테스트 해봐")

        tab.send_prompt()

        assert captured["worker_args"][0].kind == "external_ping"
        assert ping_service.targets == ["8.8.8.8", "1.1.1.1", "google.com"]
        assert "8.8.8.8 reachable" in captured["context"]
        assert "google.com reachable" in captured["context"]
    finally:
        tab.close()


def test_ai_chat_tab_blocks_risky_netops_action_without_admin(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.QMessageBox.warning", lambda *_args, **_kwargs: None
    )
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


def test_ai_chat_tab_records_user_denial_of_netops_change(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
        is_admin=True,
    )

    tab = AiChatTab(state)
    try:
        action = plan_netops_chat_action(
            "인터페이스 Ethernet DNS 8.8.8.8, 1.1.1.1로 변경"
        )

        assert action is not None
        assert tab._confirm_netops_chat_action(action) is False
        assert "사용자가 NetOps 변경 작업을 취소했습니다." in tab._messages[-1][
            "body"
        ]
        assert action.title in tab._messages[-1]["body"]
    finally:
        tab.close()


def test_ai_chat_tab_runs_approved_dns_change_through_network_service(
    qapp, tmp_path, monkeypatch
):
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

    def fake_job_start(
        fn, *args, on_result=None, on_error=None, on_finished=None, **_kwargs
    ):
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


def test_ai_chat_tab_can_cancel_internal_context_collection(
    qapp, tmp_path, monkeypatch
):
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


def test_ai_chat_tab_ignores_stale_internal_context_callbacks(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)
    started: list[tuple[dict, str]] = []
    monkeypatch.setattr(
        tab,
        "_start_prompt_process",
        lambda payload, context: started.append((payload, context)),
    )
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


def test_ai_chat_tab_stale_process_timeouts_cannot_kill_new_process(
    qapp, tmp_path, monkeypatch
):
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


def test_ai_chat_tab_collects_inspector_and_config_builder_context(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(
            root=tmp_path, exports_dir=tmp_path, data_root=tmp_path / "data"
        ),
        save_app_config=lambda _config: None,
        ip_profiles=[],
        ftp_profiles=[],
        scp_profiles=[],
    )

    tab = AiChatTab(state)
    try:
        categories = tab._netops_context_categories(
            "장비 점검/백업 프로파일이랑 CLI 설정 생성 프로파일 만들어줘"
        )
        context = tab._collect_internal_netops_context(
            "장비 점검/백업 프로파일이랑 CLI 설정 생성 프로파일 만들어줘"
        )

        assert {"inspector", "config_builder", "profiles", "overview"}.issubset(
            categories
        )
        assert "장비 점검/백업 컨텍스트" in context
        assert "CLI 설정 생성 컨텍스트" in context
        assert "custom_rules.yaml" in context
        assert "프로파일 폴더" in context
        assert "NetOps Suite 기능 지도" in context
    finally:
        tab.close()


def test_ai_chat_tab_masks_saved_profile_context_and_avoids_broad_keyword_matches(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(
            root=tmp_path, exports_dir=tmp_path, data_root=tmp_path / "data"
        ),
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
        scp_profiles=[
            ScpProfile(
                name="Core SCP",
                host="10.10.10.50",
                port=22,
                remote_path="/network/backups",
            )
        ],
    )

    tab = AiChatTab(state)
    try:
        assert tab._netops_context_categories("profile picture 만들어줘") == set()
        assert tab._netops_context_categories("backup file 압축해줘") == set()
        assert tab._netops_context_categories("ftp가 뭔지 설명해줘") == set()

        context = tab._collect_internal_netops_context(
            "저장된 IP 프로파일과 FTP 전송 프로파일 요약해줘"
        )

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
        assert tab._blocked_direct_extra_args(
            [
                "--output-format=json",
                "--ask-for-approval",
                "never",
                "--resume=other-session",
                "--sandbox=danger-full-access",
                "--add-dir=C:/outside",
                "-s",
                "workspace-write",
                "-C",
                "C:/outside",
                "--dangerously-bypass-approvals-and-sandbox",
            ]
        ) == [
            "--output-format",
            "--ask-for-approval",
            "--resume",
            "--sandbox",
            "--add-dir",
            "-s",
            "-C",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        tab._set_combo_data(tab.provider_combo, "claude")
        assert tab._blocked_direct_extra_args(["-c", "-r", "session-id"]) == [
            "-c",
            "-r",
        ]
    finally:
        tab.close()


def test_ai_chat_codex_permission_menu_scopes_workspace_and_confirms_full_access(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.QMessageBox.information",
        lambda *_args, **_kwargs: None,
    )
    app_root = tmp_path / "source"
    data_root = tmp_path / "data"
    config_dir = tmp_path / "settings"
    logs_dir = tmp_path / "logs"
    exports_dir = tmp_path / "results exports"
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(
            root=app_root,
            data_root=data_root,
            config_dir=config_dir,
            logs_dir=logs_dir,
            exports_dir=exports_dir,
        ),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        _show_chat_tab(qapp, tab, width=1100, height=720)
        assert tab.permission_button.isVisible()
        assert tab.permission_button.text() == "읽기 전용"

        menu = tab._build_permission_menu()
        option_widgets = [action.defaultWidget() for action in menu.actions()]
        assert [widget.accessibleName() for widget in option_widgets] == [
            "읽기 전용",
            "작업공간 액세스",
            "전체 권한",
        ]
        assert [bool(widget.property("current")) for widget in option_widgets] == [
            True,
            False,
            False,
        ]
        menu.deleteLater()

        assert tab._select_codex_permission_mode("workspace-write") is True
        assert tab.permission_button.text() == "작업공간 액세스"
        sandbox, workspace, writable, working_dir = tab._codex_invocation_access()
        assert sandbox == "workspace-write"
        assert Path(workspace) == config_dir.resolve()
        assert Path(working_dir) == config_dir.resolve()
        assert set(map(Path, writable)) == {
            (data_root / "inspector").resolve(),
            (data_root / "config_builder").resolve(),
            logs_dir.resolve(),
            exports_dir.resolve(),
        }
        assert app_root.resolve() not in set(map(Path, writable))
        assert all(Path(path).is_dir() for path in (workspace, *writable))

        answers = iter([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
        monkeypatch.setattr(
            "app.ui.tabs.ai_chat_tab.QMessageBox.warning",
            lambda *_args, **_kwargs: next(answers),
        )
        assert tab._select_codex_permission_mode("danger-full-access") is False
        assert tab._codex_permission_mode == "workspace-write"
        assert tab._select_codex_permission_mode("danger-full-access") is True
        assert tab._codex_permission_mode == "danger-full-access"
        assert tab.permission_button.text() == "전체 액세스"

        tab._set_running(True)
        assert not tab.permission_button.isEnabled()
        tab._set_running(False)
        assert tab.permission_button.isEnabled()

        tab._set_combo_data(tab.provider_combo, "gemini")
        qapp.processEvents()
        assert tab.permission_button.isHidden()
        tab._set_combo_data(tab.provider_combo, "codex")
        qapp.processEvents()
        assert not tab.permission_button.isHidden()

        tab.reset_session()
        assert tab._codex_permission_mode == "danger-full-access"
        assert tab.permission_button.text() == "전체 액세스"
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
        assert (
            tab.prompt_edit.placeholderText() == "NetOps 작업을 자연어로 입력하세요..."
        )
        assert tab.model_combo.currentData() == "my-custom-model"
        assert (
            tab.model_combo.currentText()
            == "현재 설정: my-custom-model (목록에서 확인되지 않음)"
        )
        codex_model_values = [
            tab.model_combo.itemData(index) for index in range(tab.model_combo.count())
        ]
        assert codex_model_values == ["", "my-custom-model"]

        tabs = tab.findChildren(QTabWidget)[0]
        assert [tabs.tabText(index) for index in range(tabs.count())] == [
            "채팅",
            "연결 설정",
            "고급 옵션",
        ]
        assert (
            tab.reasoning_combo.itemText(tab.reasoning_combo.findData("xhigh"))
            == "매우 높음 (가장 깊은 분석)"
        )
        assert tab.speed_combo.itemText(tab.speed_combo.findData("fast")) == "빠른 응답"
        assert tab.raw_help_group.title() == "CLI 도움말 원문 보기"
        assert tab.raw_help_edit.isHidden()

        tab._set_combo_data(tab.provider_combo, "gemini")
        qapp.processEvents()

        gemini_model_labels = [
            tab.model_combo.itemText(index) for index in range(tab.model_combo.count())
        ]
        assert "Gemini 2.5 Pro" in gemini_model_labels
        button_texts = {button.text() for button in tab.findChildren(QPushButton)}
        assert "세션 초기화" in button_texts
        assert "대화 내용 저장" in button_texts
        assert "대화 복사" not in button_texts
        assert "대화 지우기" not in button_texts
        assert "선택 제거" not in button_texts

        tab._append_block("사용자", "hello <world>")
        plain_text = tab._plain_transcript_text()
        assert "사용자" in plain_text
        assert re.search(r"\d{2}:\d{2}:\d{2}", plain_text)
        assert "hello <world>" in plain_text
        rendered_bodies = "\n".join(
            _widget_visible_text(_message_body_widget(bubble))
            for bubble in _message_bubbles(tab)
        )
        assert "hello <world>" in rendered_bodies

        tab._append_block("AI", "**요약**\n- 첫 번째\n`ping 8.8.8.8`")
        rendered_plain = "\n".join(
            _widget_visible_text(_message_body_widget(bubble))
            for bubble in _message_bubbles(tab)
        )
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


def test_ai_chat_assistant_header_uses_request_provider_and_model(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={
            "ai_chat": {
                "active_provider": "codex",
                "providers": {"codex": {"model": "gpt-5.4"}},
            }
        },
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)
    catalog = AiModelCatalog(
        provider_key="codex",
        source="live",
        models=[
            AiModelDescriptor(
                id="gpt-5.4",
                model="gpt-5.4",
                display_name="GPT-5.4",
                is_default=True,
                source="live",
            )
        ],
    )

    try:
        tab._model_catalogs["codex"] = catalog
        config = AiProviderConfig(key="codex", model="gpt-5.4")
        response_title = tab._assistant_response_title(config)
        assert response_title == "ChatGPT Codex · GPT-5.4"

        tab._append_stream("모델을 표시한 응답입니다.\n", response_title)
        assert tab._messages[-1]["title"] == response_title
        assert all(message["title"] != "AI" for message in tab._messages)

        # The response keeps the request-time label even if the current UI changes later.
        tab._providers["codex"].model = "different-model"
        tab._append_stream("같은 응답의 다음 부분입니다.\n", response_title)
        assert len(tab._messages) == 1
        assert tab._messages[0]["title"] == response_title

        tab._providers["codex"].model = ""
        assert tab._assistant_response_title(tab._providers["codex"]) == response_title
    finally:
        tab.close()


def test_ai_chat_renders_markdown_tables_and_copies_individual_messages(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    table_markdown = "\n".join(
        [
            "| 필수 컬럼 | 내용 |",
            "|:---|---|",
            "| ip | 장비 관리 IP |",
            "| vendor | 제조사 |",
        ]
    )
    tab = AiChatTab(state)

    try:
        _show_chat_tab(qapp, tab)
        assert not hasattr(tab, "copy_transcript_button")
        assert not hasattr(tab, "clear_button")

        tab._append_block("사용자", "장비 점검 방법을 알려줘")
        tab._append_block("AI", table_markdown)
        qapp.processEvents()

        bubbles = _message_bubbles(tab)
        assert len(bubbles) == 2
        table_bubble = bubbles[-1]
        table_body = _message_body_widget(table_bubble)
        assert "<table" in table_body.toHtml().lower()
        assert "필수 컬럼" in table_body.toPlainText()
        assert "장비 관리 IP" in table_body.toPlainText()
        assert "|:---|---|" not in table_body.toPlainText()
        assert table_body.sourceText() == table_markdown
        _assert_body_has_no_internal_scroll(table_body)

        message_copy_button = table_bubble.findChild(
            QPushButton, "aiChatMessageCopyButton"
        )
        assert message_copy_button is not None
        message_copy_button.click()
        assert qapp.clipboard().text() == table_markdown

        table_body.selectAll()
        table_body.copy()
        assert "필수 컬럼" in qapp.clipboard().text()
        assert "|:---|---|" not in qapp.clipboard().text()
    finally:
        tab.close()


def test_ai_chat_message_body_supports_mouse_drag_partial_copy(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)

    try:
        _show_chat_tab(qapp, tab)
        tab._append_block("AI", "alpha beta gamma delta epsilon")
        qapp.processEvents()
        body = _message_body_widget(_message_bubbles(tab)[0])
        viewport = body.viewport()

        QTest.mousePress(
            viewport,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(2, 8),
        )
        QTest.mouseMove(viewport, QPoint(125, 8), 10)
        QTest.mouseRelease(
            viewport,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(125, 8),
        )

        assert body.hasSelection()
        selected = body.selectedText()
        assert selected
        assert selected in body.toPlainText()

        body.setFocus()
        QTest.keyClick(body, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert qapp.clipboard().text() == selected
    finally:
        tab.close()


def test_ai_chat_renders_multiple_long_tables_with_html_breaks_without_clipping(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    markdown = r"""| 단계 | 점검/백업 방법 | 입력·선택 사항 | 참고 |
|---|---|---|---|
| 1. 장비 목록 작성 | Excel(XLSX)에 대상 장비를 한 행씩 등록 | 필수: `ip`, `vendor`, `os`, `connection_type`, `port`, `password` | 민감정보가 포함되므로 파일을 안전하게 보관하세요. |
| 2. 선택 정보 입력 | 계정이나 Enable 진입이 필요한 장비에 추가 정보 입력 | 선택: `username`, `enable_password` | 필요한 장비에만 입력합니다. |
| 3. 장비 목록 불러오기 | NetOps Suite의 **장비 점검/백업**에서 Excel 파일 선택 | 작성한 XLSX 파일 | 장비별 `vendor`와 `os`가 지원 프로파일과 일치해야 합니다. |
| 4. 실행 모드 선택 | 목적에 맞는 모드 선택 | `inspection`: 점검만<br>`backup`: 백업만<br>`inspection_backup`: 점검 후 백업<br>`custom_commands`: 사용자 명령 | 정기 작업은 `inspection_backup`이 편리합니다. |
| 5. 프로파일 확인 | 장비 제조사와 OS에 맞는 내장 프로파일 적용 | 현재 지원 프로파일 19개 | Cisco, Juniper, Aruba, AXGATE, Piolink, Ruckus 등 주요 프로파일은 백업을 지원합니다. |
| 6. 작업 실행 | 장비 접속 및 명령 실행 시작 | 접속 방식, 포트, 인증정보 | 실행 전에 관리망 연결 상태와 접근 권한을 확인하세요. |
| 7. 결과 확인 | 장비별 접속 성공 여부, 점검 결과 및 백업 결과 확인 | 결과의 오류·누락 항목 | 실패는 해당 장비의 접속·인증·명령 등 **NetOps 내부 수집 항목 실패**로 확인합니다. |
| 8. 결과 보관 | 생성된 점검 결과와 구성 백업 파일을 안전한 위치에 보관 | 날짜·장비명 기준으로 분류 권장 | 백업 파일에도 민감한 네트워크 설정이 포함될 수 있습니다. |

### 지원 프로파일 예시

| 제조사 / OS | 기본 점검 명령 수 | 백업 |
|---|---:|---|
| Cisco / IOS | 5 | 지원 |
| Cisco / IOS-XE | 2 | 지원 |
| Cisco / Legacy | 2 | 지원 |
| Juniper / Junos | 4 | 지원 |
| Aruba / Aruba OS | 4 | 지원 |
| AXGATE / AXGATE | 9 | 지원 |
| Piolink / TiFRONT | 8 | 지원 |
| Ruckus / ICX | 5 | 지원 |
| Alcatel-Lucent / AOS6·AOS8 | 각 9 | 지원 |
| Handreamnet / HN·SG | 6·7 | 지원 |

기본 프로파일에 없는 장비나 명령은 `custom_commands` 모드를 사용하거나 다음 사용자 설정에 프로파일을 추가할 수 있습니다.

- 규칙 파일: `~\AppData\Local\NetOps Suite\inspector\custom_rules.yaml`
- 사용자 파서: `~\AppData\Local\NetOps Suite\inspector\custom_parsers`
- 사용자 프로파일 구성 항목: `inspection_commands`, `backup_commands`, `parsing_rules`, `connection_overrides`"""
    tab = AiChatTab(state)

    try:
        _show_chat_tab(qapp, tab, width=1100, height=760)
        tab._append_block("AI", markdown)
        qapp.processEvents()

        bubble = _message_bubbles(tab)[-1]
        body = _message_body_widget(bubble)
        tables = _document_tables(body)
        assert [(table.rows(), table.columns()) for table in tables] == [
            (9, 4),
            (11, 3),
        ]
        assert all(
            _table_cell_text(table, row, column)
            for table in tables
            for row in range(table.rows())
            for column in range(table.columns())
        )

        first_table, profile_table = tables
        assert _table_cell_text(first_table, 8, 3).startswith("백업 파일에도")
        mode_text = _table_cell_text(first_table, 4, 2)
        assert all(
            value in mode_text for value in ("inspection", "backup", "custom_commands")
        )
        assert _table_cell_text(profile_table, 10, 0) == "Handreamnet / HN·SG"
        assert _table_cell_text(profile_table, 10, 2) == "지원"

        assert body.document().textWidth() == pytest.approx(body.width(), abs=1)
        for table in tables:
            table_rect = body.document().documentLayout().frameBoundingRect(table)
            assert table_rect.right() <= body.contentsRect().right()

        visible_text = body.toPlainText()
        assert "8. 결과 보관" in visible_text
        assert "지원 프로파일 예시" in visible_text
        assert "custom_rules.yaml" in visible_text
        assert body.sourceText() == markdown
        _assert_body_has_no_internal_scroll(body)
        _assert_message_container_contains(tab, bubble)
    finally:
        tab.close()


def test_ai_chat_preserves_cli_path_managed_by_settings(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    saved_configs: list[dict] = []
    state = SimpleNamespace(
        app_config={
            "ai_chat": {
                "active_provider": "codex",
                "providers": {"codex": {"command_path": "old-codex.exe"}},
            }
        },
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path, logs_dir=tmp_path),
    )

    def save_app_config(config: dict) -> None:
        state.app_config = config
        saved_configs.append(config)

    state.save_app_config = save_app_config
    tab = AiChatTab(state)
    try:
        updated = normalize_ai_chat_config(state.app_config["ai_chat"])
        updated["providers"]["codex"]["command_path"] = "central-codex.exe"
        state.app_config = {**state.app_config, "ai_chat": updated}

        tab.reload_integration_settings()
        tab.save_current_config()

        assert tab.current_provider_config().command_path == "central-codex.exe"
        assert (
            saved_configs[-1]["ai_chat"]["providers"]["codex"]["command_path"]
            == "central-codex.exe"
        )
        assert tab.tool_settings_button.text() == "도구 연동 열기"
    finally:
        tab.close()


def test_ai_chat_login_preflight_starts_in_background_with_immediate_feedback(
    qapp, tmp_path, monkeypatch
):
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


def test_ai_chat_login_preflight_repairs_then_rechecks_before_launch(
    qapp, tmp_path, monkeypatch
):
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
            assert any(
                "로그인 터미널을 열었습니다" in item["body"] for item in tab._messages
            )
        else:
            assert warnings == ["로그인 터미널을 열지 못했습니다."]
            assert "로그인 실행 실패" in tab.status_label.text()
            assert not any(
                "로그인 터미널을 열었습니다" in item["body"] for item in tab._messages
            )
    finally:
        tab.close()


def test_ai_chat_structured_stdout_error_is_not_rendered_as_raw_ai_message(
    qapp, tmp_path, monkeypatch
):
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
                "error": json.dumps(
                    {"type": "invalid_request_error", "message": message}
                ),
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


def test_ai_chat_promotes_successful_cli_session_and_resumes_exact_id(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.QMessageBox.warning", lambda *_args, **_kwargs: None
    )
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    stdout_payload = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-exact-123"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "첫 답변"},
                }
            ),
            "",
        ]
    ).encode("utf-8")

    class FakePromptProcess:
        def __init__(self) -> None:
            self.stdout = stdout_payload
            self.properties = {
                "provider_key": "codex",
                "response_title": "ChatGPT Codex · GPT-5.4",
                "session_id_candidate": "",
            }

        def readAllStandardOutput(self):
            data, self.stdout = self.stdout, b""
            return data

        def property(self, name):
            return self.properties.get(name)

        def setProperty(self, name, value):
            self.properties[name] = value

        def deleteLater(self):
            return None

    tab = AiChatTab(state)
    try:
        process = FakePromptProcess()
        tab._process = process
        tab._read_stdout(process)
        assert process.property("session_id_candidate") == "thread-exact-123"
        assert any("첫 답변" in message["body"] for message in tab._messages)

        tab._finish_prompt(process, 0)
        assert tab._provider_session_ids == {"codex": "thread-exact-123"}

        captured: dict[str, object] = {}

        def capture_invocation(config, prompt, **kwargs):
            captured.update(config=config, prompt=prompt, **kwargs)
            raise ValueError("capture only")

        monkeypatch.setattr(
            "app.ui.tabs.ai_chat_tab.build_chat_invocation", capture_invocation
        )
        tab._start_prompt_process(
            {
                "prompt": "이어서 설명해줘",
                "config": AiProviderConfig(key="codex", command_path="codex"),
                "attachment_context": "",
                "attachment_args": [],
                "sent_attachments": [],
            },
            "",
        )

        assert captured["prompt"] == "이어서 설명해줘"
        assert captured["session_id"] == "thread-exact-123"
    finally:
        tab.close()


def test_ai_chat_prompt_enter_sends_and_shift_enter_adds_line(
    qapp, tmp_path, monkeypatch
):
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
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
            )
        )
        assert sent == [None]
        assert tab.prompt_edit.toPlainText() == "hello"

        tab.prompt_edit.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Return,
                Qt.KeyboardModifier.ShiftModifier,
            )
        )
        assert "\n" in tab.prompt_edit.toPlainText()
    finally:
        tab.close()


def test_ai_chat_prompt_auto_grows_and_paste_attaches_files(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    note = tmp_path / "note.txt"
    note.write_text("hello from pasted file", encoding="utf-8")
    second_note = tmp_path / "두 번째 파일.txt"
    second_note.write_text("second pasted file", encoding="utf-8")
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    previous_style = qapp.styleSheet()
    qapp.setStyleSheet(APP_STYLE_SHEET)
    tab = AiChatTab(state)
    try:
        _show_chat_tab(qapp, tab, width=1100, height=720)
        initial_height = tab.prompt_edit.height()
        tab.prompt_edit.setPlainText("\n".join(f"line {index}" for index in range(8)))
        qapp.processEvents()
        assert tab.prompt_edit.height() > initial_height
        assert tab.prompt_edit.height() <= tab.prompt_edit.MAX_HEIGHT

        mime = QMimeData()
        mime.setUrls(
            [QUrl.fromLocalFile(str(note)), QUrl.fromLocalFile(str(second_note))]
        )
        qapp.clipboard().setMimeData(mime)
        tab.prompt_edit.paste()

        assert tab._attachments == [note, second_note]
        assert tab.attachment_list.count() == 2
        assert tab.attachment_list.isHidden() is False
        assert not hasattr(tab, "remove_attachment_button")

        first_card = tab.attachment_list.itemWidget(tab.attachment_list.item(0))
        second_card = tab.attachment_list.itemWidget(tab.attachment_list.item(1))
        assert first_card is not None
        assert second_card is not None
        first_remove = first_card.findChild(QToolButton, "attachmentRemoveButton")
        second_remove = second_card.findChild(QToolButton, "attachmentRemoveButton")
        assert first_remove is not None
        assert second_remove is not None
        assert first_remove.text() == "×"
        first_item_rect = tab.attachment_list.visualItemRect(
            tab.attachment_list.item(0)
        )
        assert first_card.geometry() == first_item_rect
        assert first_remove.size() == QSize(20, 20)
        assert (
            first_card.rect().adjusted(1, 1, -4, -1).contains(first_remove.geometry())
        )

        first_remove.click()
        qapp.processEvents()
        assert tab._attachments == [second_note]
        assert tab.attachment_list.count() == 1

        tab.clear_attachments_button.click()
        assert tab._attachments == []
        assert tab.attachment_list.isHidden()

        filename_mime = QMimeData()
        filename_mime.setData(
            'application/x-qt-windows-mime;value="FileNameW"',
            (str(note) + "\x00").encode("utf-16-le"),
        )
        tab.prompt_edit.insertFromMimeData(filename_mime)
        assert tab._attachments == [note]

        tab.prompt_edit.clear()
        qapp.clipboard().setText("일반 텍스트 붙여넣기")
        tab.prompt_edit.paste()
        assert tab.prompt_edit.toPlainText() == "일반 텍스트 붙여넣기"
    finally:
        tab.close()
        qapp.setStyleSheet(previous_style)


def test_ai_chat_paste_clipboard_image_creates_temporary_png_attachment(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    temporary_paths: list[Path] = []
    try:
        _show_chat_tab(qapp, tab, width=900, height=640)
        image = QImage(24, 16, QImage.Format.Format_ARGB32)
        image.fill(QColor("#2f80ed"))
        mime = QMimeData()
        mime.setImageData(image)
        mime.setText("image clipboard text must not be inserted")
        qapp.clipboard().setMimeData(mime)

        tab.prompt_edit.paste()
        qapp.processEvents()

        assert tab.prompt_edit.toPlainText() == ""
        assert len(tab._attachments) == 1
        pasted_path = tab._attachments[0]
        temporary_paths.append(pasted_path)
        assert pasted_path.name.startswith("netops_clipboard_")
        assert pasted_path.suffix.lower() == ".png"
        assert pasted_path.exists()
        assert QImage(str(pasted_path)).size() == QSize(24, 16)
        assert tab.attachment_list.count() == 1

        tab.remove_attachment(str(pasted_path))
        qapp.processEvents()
        assert tab._attachments == []
        assert not pasted_path.exists()

        qapp.clipboard().setImage(image)
        tab.prompt_edit.paste()
        qapp.processEvents()
        shutdown_path = tab._attachments[0]
        temporary_paths.append(shutdown_path)
        assert shutdown_path.exists()
        tab.shutdown()
        assert not shutdown_path.exists()
    finally:
        tab.close()
        for path in temporary_paths:
            path.unlink(missing_ok=True)


def test_ai_chat_visible_transcript_expands_for_long_plain_and_markdown_without_body_scrollers(
    qapp, tmp_path, monkeypatch
):
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


def test_ai_chat_stream_render_preserves_selected_text_and_reader_scroll(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        _show_chat_tab(qapp, tab, width=900, height=620)
        for message_index in range(6):
            tab._append_block(
                "AI",
                "\n".join(
                    f"메시지 {message_index}의 분석 줄 {line_index:02d}"
                    for line_index in range(22)
                ),
            )
        qapp.processEvents()

        first_body = _message_body_widget(_message_bubbles(tab)[0])
        cursor = first_body.textCursor()
        cursor.setPosition(4)
        cursor.setPosition(18, QTextCursor.MoveMode.KeepAnchor)
        first_body.setTextCursor(cursor)
        selected_text = first_body.textCursor().selectedText()

        scrollbar = tab.transcript_scroll.verticalScrollBar()
        assert scrollbar.maximum() > 100
        reader_position = max(1, scrollbar.maximum() // 3)
        scrollbar.setValue(reader_position)

        tab._append_stream("실시간 응답 첫 줄\n", "테스트 모델")
        QTest.qWait(100)
        qapp.processEvents()

        visible_bodies = [
            _message_body_widget(bubble) for bubble in _message_bubbles(tab)
        ]
        restored_body = next(
            body for body in visible_bodies if body.property("messageIndex") == 0
        )
        assert restored_body.textCursor().selectedText() == selected_text
        assert scrollbar.value() == min(reader_position, scrollbar.maximum())
        assert scrollbar.value() < scrollbar.maximum()
    finally:
        tab.close()


def test_ai_chat_stream_render_follows_bottom_only_for_active_reader(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        _show_chat_tab(qapp, tab, width=780, height=520)
        tab._append_stream(
            "\n".join(f"초기 응답 {index:02d}" for index in range(45)),
            "테스트 모델",
        )
        QTest.qWait(100)
        qapp.processEvents()
        scrollbar = tab.transcript_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

        tab._append_stream(
            "\n" + "\n".join(f"추가 응답 {index:02d}" for index in range(20)),
            "테스트 모델",
        )
        QTest.qWait(100)
        qapp.processEvents()

        assert scrollbar.maximum() > 0
        assert scrollbar.value() == scrollbar.maximum()
    finally:
        tab.close()


def test_ai_chat_message_container_stays_tall_enough_after_long_content_reflow(
    qapp, tmp_path, monkeypatch
):
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
                *(
                    f"- 분석 항목 {index:02d}: 채널 혼잡도와 신호 품질을 확인했습니다."
                    for index in range(24)
                ),
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


def test_ai_chat_session_reset_keeps_visible_history_attachments_and_draft(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    notices: list[str] = []
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.QMessageBox.information",
        lambda _parent, _title, message: notices.append(message),
    )
    note = tmp_path / "keep.txt"
    note.write_text("keep attached", encoding="utf-8")
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        _show_chat_tab(qapp, tab, width=900, height=720)
        tab._provider_session_ids = {"codex": "thread-one", "claude": "session-two"}
        tab._append_block("사용자", "이전 요청")
        tab._append_block("AI", "이전 답변")
        tab.prompt_edit.setPlainText("작성 중인 다음 요청")
        tab.attach_paths([note])
        previous_bodies = [message["body"] for message in tab._messages]

        tab._set_preparing(True)
        assert not tab.reset_session_button.isEnabled()
        tab.reset_session()
        assert tab._provider_session_ids == {
            "codex": "thread-one",
            "claude": "session-two",
        }
        assert notices == ["현재 요청이 끝난 뒤 세션을 초기화하세요."]
        tab._set_preparing(False)
        assert tab.reset_session_button.isEnabled()

        tab.reset_session()
        qapp.processEvents()

        assert tab._provider_session_ids == {}
        assert [message["body"] for message in tab._messages[:2]] == previous_bodies
        assert "화면 기록은 유지" in tab._messages[-1]["body"]
        assert tab.prompt_edit.toPlainText() == "작성 중인 다음 요청"
        assert tab._attachments == [note]
        assert tab.attachment_list.count() == 1
    finally:
        tab.close()


def test_ai_chat_session_save_uses_selected_path_and_repairs_extension(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    exports_dir = tmp_path / "exports"
    selected = tmp_path / "chosen" / "saved-conversation"
    proposed_paths: list[Path] = []

    def choose_path(_parent, _title, proposed, _file_filter):
        proposed_paths.append(Path(proposed))
        return str(selected), "Markdown Files (*.md)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_path)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=exports_dir),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        tab._append_block("사용자", "저장할 대화")
        tab._append_block("AI", "저장할 답변")
        saved_path = tab.export_session()

        assert saved_path == selected.with_suffix(".md")
        saved_text = saved_path.read_text(encoding="utf-8")
        assert "# NetOps 어시스턴트 세션" in saved_text
        assert "저장할 대화" in saved_text
        assert "저장할 답변" in saved_text
        assert proposed_paths[0].parent == exports_dir
        assert proposed_paths[0].name.startswith("ai_chat_session_")
        assert proposed_paths[0].suffix == ".md"
    finally:
        tab.close()


def test_ai_chat_session_save_respects_text_filter(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    selected = tmp_path / "chosen" / "saved-conversation"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(selected), "Text Files (*.txt)"),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path / "exports"),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        tab._append_block("사용자", "텍스트로 저장할 대화")

        saved_path = tab.export_session()

        assert saved_path == selected.with_suffix(".txt")
        assert "텍스트로 저장할 대화" in saved_path.read_text(encoding="utf-8")
    finally:
        tab.close()


def test_ai_chat_session_save_cancel_creates_no_file(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    exports_dir = tmp_path / "exports"
    information_calls: list[tuple] = []
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: ("", ""),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: information_calls.append((args, kwargs)),
    )
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=exports_dir),
        save_app_config=lambda _config: None,
    )

    tab = AiChatTab(state)
    try:
        tab._append_block("사용자", "취소할 저장")

        assert tab.export_session() is None
        assert not list(tmp_path.rglob("ai_chat_session*.md"))
        assert information_calls == []
    finally:
        tab.close()


def test_ai_chat_model_catalog_preserves_selection_and_rebuilds_supported_options(
    qapp, tmp_path, monkeypatch
):
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
        paths=SimpleNamespace(
            root=tmp_path, config_dir=tmp_path / "config", exports_dir=tmp_path
        ),
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
        assert (
            saved_configs[-1]["ai_chat"]["providers"]["codex"]["reasoning_effort"] == ""
        )
        assert saved_configs[-1]["ai_chat"]["providers"]["codex"]["speed"] == ""
        assert "현재 계정에서 사용 가능" in tab.model_detail_label.text()
        assert "입력: 텍스트" in tab.model_detail_label.text()

        tab._set_combo_data(tab.model_combo, "model-a")
        qapp.processEvents()

        reasoning_values = [
            tab.reasoning_combo.itemData(index)
            for index in range(tab.reasoning_combo.count())
        ]
        speed_values = [
            tab.speed_combo.itemData(index) for index in range(tab.speed_combo.count())
        ]
        assert reasoning_values == ["", "high", "low", "xhigh", "max", "ultra"]
        assert tab.reasoning_combo.currentData() == ""
        assert speed_values == ["", "fast"]
        assert tab.speed_combo.currentData() == ""
        assert tab.current_provider_config().model == "model-a"
        assert "입력: 텍스트, 이미지" in tab.model_detail_label.text()
        assert (
            "추론 단계: 높음, 낮음, 매우 높음, 최대, 울트라"
            in tab.model_detail_label.text()
        )
        assert "빠른 응답: 지원" in tab.model_detail_label.text()

        tab._set_combo_data(tab.reasoning_combo, "xhigh")
        tab._set_combo_data(tab.speed_combo, "fast")
        qapp.processEvents()
        assert tab._providers["codex"].reasoning_effort == "xhigh"
        assert tab._providers["codex"].speed == "fast"
        assert (
            saved_configs[-1]["ai_chat"]["providers"]["codex"]["reasoning_effort"]
            == "xhigh"
        )
        assert saved_configs[-1]["ai_chat"]["providers"]["codex"]["speed"] == "fast"

        tab._set_combo_data(tab.reasoning_combo, "ultra")
        qapp.processEvents()
        assert tab._providers["codex"].reasoning_effort == "ultra"
        assert (
            saved_configs[-1]["ai_chat"]["providers"]["codex"]["reasoning_effort"]
            == "ultra"
        )

        tab._providers["codex"].speed = "flex"
        tab._populate_model_combo("codex", "model-a")
        assert tab.speed_combo.currentData() == "flex"
        assert tab.speed_combo.currentText() == "유연 처리 (기존 설정)"
        assert (
            tab._codex_runtime_config_args(
                AiProviderConfig(key="codex", speed="priority")
            )
            == []
        )
    finally:
        tab.close()


def test_ai_chat_new_catalog_does_not_auto_change_current_model_or_other_provider_ui(
    qapp, tmp_path, monkeypatch
):
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
            AiModelDescriptor(
                id="new-default", model="new-default", is_default=True, source="live"
            ),
        ],
    )

    try:
        tab._active_model_catalog_request = ("codex", 1)
        tab._accept_model_catalog_result("codex", 1, refreshed)
        assert tab.model_combo.currentData() == "selected-model"
        assert "목록에서 확인되지 않음" in tab.model_combo.currentText()

        tab._set_combo_data(tab.provider_combo, "gemini")
        qapp.processEvents()
        gemini_values_before = [
            tab.model_combo.itemData(index) for index in range(tab.model_combo.count())
        ]
        tab._active_model_catalog_request = ("codex", 2)
        tab._accept_model_catalog_result("codex", 2, refreshed)
        gemini_values_after = [
            tab.model_combo.itemData(index) for index in range(tab.model_combo.count())
        ]

        assert tab.current_provider_key() == "gemini"
        assert gemini_values_after == gemini_values_before
        assert "new-default" not in gemini_values_after
    finally:
        tab.close()


def test_ai_chat_catalog_error_keeps_source_time_status_and_cached_selection(
    qapp, tmp_path, monkeypatch
):
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
        assert (
            "마지막 확인 기준 현재 계정에서 사용 가능" in tab.model_detail_label.text()
        )

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


def test_ai_chat_direct_model_id_validates_and_marks_support_unknown(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
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
def test_ai_chat_connection_model_details_are_not_clipped(
    qapp, tmp_path, monkeypatch, width, height
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        AiChatTab, "_ensure_model_catalog_fresh", lambda self, *_args, **_kwargs: None
    )
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
                supported_reasoning_efforts=[
                    "minimal",
                    "low",
                    "medium",
                    "high",
                    "xhigh",
                ],
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

        assert tab.model_detail_label.height() >= tab.model_detail_label.heightForWidth(
            tab.model_detail_label.width()
        )
        assert (
            tab.model_catalog_status_label.height()
            >= tab.model_catalog_status_label.heightForWidth(
                tab.model_catalog_status_label.width()
            )
        )
        assert tab.provider_group.geometry().bottom() <= tab.connection_page.height()
    finally:
        tab.close()


def test_ai_chat_model_combo_text_fits_minimum_main_window_workspace(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        AiChatTab, "_ensure_model_catalog_fresh", lambda self, *_args, **_kwargs: None
    )
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
            rendered_text_width = tab.model_combo.fontMetrics().horizontalAdvance(
                tab.model_combo.currentText()
            )
            assert rendered_text_width <= edit_rect.width(), (
                tab.model_combo.currentText(),
                rendered_text_width,
                edit_rect.width(),
            )
    finally:
        tab.close()


def test_ai_chat_working_indicator_is_single_ephemeral_widget(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    tab = AiChatTab(state)

    try:
        tab._append_block("사용자", "상태를 확인해줘")
        original_messages = list(tab._messages)
        tab._set_working_status("NetOps 기능 실행 중")
        _show_chat_tab(qapp, tab)

        visible_indicators = [
            widget
            for widget in tab.findChildren(QWidget, "aiChatWorkingIndicator")
            if widget.isVisible()
        ]
        assert len(visible_indicators) == 1
        assert "NetOps 기능 실행 중" in _widget_visible_text(visible_indicators[0])
        assert tab._messages == original_messages
        assert "NetOps 기능 실행 중" not in tab._plain_transcript_text()

        tab._set_working_status("NetOps 내부 컨텍스트 수집 중")
        qapp.processEvents()
        visible_indicators = [
            widget
            for widget in tab.findChildren(QWidget, "aiChatWorkingIndicator")
            if widget.isVisible()
        ]
        assert len(visible_indicators) == 1
        assert "내부 컨텍스트 수집 중" in _widget_visible_text(visible_indicators[0])

        tab._set_running(False)
        qapp.processEvents()
        assert not any(
            widget.isVisible()
            for widget in tab.findChildren(QWidget, "aiChatWorkingIndicator")
        )
        assert tab._messages == original_messages
    finally:
        tab.close()


@pytest.mark.parametrize(
    ("prompt", "expected_status"),
    [
        ("8.8.8.8 ping 해줘", "NetOps 기능 실행 중"),
        ("현재 네트워크 상태를 분석해줘", "NetOps 내부 컨텍스트 수집 중"),
    ],
)
def test_ai_chat_working_indicator_tracks_pre_cli_phases_and_cancel(
    qapp, tmp_path, monkeypatch, prompt, expected_status
):
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

    try:
        tab._job_runner.start = lambda *_args, **_kwargs: None
        tab.prompt_edit.setPlainText(prompt)
        tab.send_prompt()

        assert tab._context_collecting is True
        assert tab._working_status_text == expected_status
        assert expected_status not in tab._plain_transcript_text()

        tab.cancel_prompt()

        assert tab._working_status_text == ""
        assert tab._working_status_timer.isActive() is False
        assert all(expected_status not in message["body"] for message in tab._messages)
    finally:
        tab.close()


def test_ai_chat_working_indicator_hides_on_first_stream_chunk(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    state = SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(root=tmp_path, exports_dir=tmp_path),
        save_app_config=lambda _config: None,
    )
    payload = (
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "스트리밍 응답"},
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
            values = {
                "provider_key": "codex",
                "response_title": "ChatGPT Codex · 테스트 모델",
            }
            return values.get(name)

        def setProperty(self, _name, _value):
            return None

    tab = AiChatTab(state)
    try:
        process = FakePromptProcess()
        tab._process = process
        tab._set_working_status("ChatGPT Codex · 테스트 모델 응답 대기 중")

        tab._read_stdout(process)

        assert tab._working_status_text == ""
        assert tab._working_status_timer.isActive() is False
        assert any("스트리밍 응답" in message["body"] for message in tab._messages)
        assert "응답 대기 중" not in tab._plain_transcript_text()
        tab._process = None
    finally:
        tab.close()
