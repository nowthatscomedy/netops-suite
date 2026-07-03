from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QLabel, QComboBox, QListWidget, QPushButton, QScrollArea, QTabWidget

from app.ui.tabs.ai_chat_tab import AiChatTab
from app.models.ftp_models import FtpProfile
from app.models.ai_models import AiProviderConfig, normalize_ai_chat_config
from app.models.network_models import NetworkAdapterInfo
from app.models.profile_models import IPProfile
from app.models.result_models import OperationResult
from app.models.scp_models import ScpProfile
from app.services.ai_agent_service import (
    build_chat_invocation,
    build_help_invocation,
    decode_cli_output,
    diagnose_cli_error,
    extra_arg_options_from_help,
    extract_text_from_cli_line,
    is_blocking_cli_configuration_error,
    model_options_for_provider,
    parse_cli_help_options,
    repair_cli_configuration_error,
    should_ignore_cli_output_text,
)


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

    assert Path(invocation.program).name.lower() in {"codex", "codex.exe"}
    assert invocation.args == ["exec", "--json", "--model", "gpt-5", "-"]
    assert "Agent role:\nreview" in invocation.stdin_text
    assert "User request:\nhello" in invocation.stdin_text
    assert invocation.working_dir == "C:/repo"


def test_codex_help_invocation_targets_exec_help():
    invocation = build_help_invocation(AiProviderConfig(key="codex", command_path="codex"), working_dir="C:/repo")

    assert invocation.args == ["exec", "--help"]
    assert invocation.working_dir == "C:/repo"


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
    assert "Codex CLI 설정 파일" in message
    assert "C:\\Users\\PC\\.codex\\config.toml" in message
    assert 'service_tier = "fast"' in message
    assert 'service_tier = "flex"' in message
    assert "추가 인자" in message
    assert raw_error in message


def test_codex_service_tier_config_error_can_be_repaired_automatically(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('model = "gpt-5.5"\nservice_tier = "priority"\n', encoding="utf-8")
    raw_error = (
        f"Error loading configuration: {config_path}:2:16: "
        "unknown variant `priority`, expected `fast` or `flex`"
    )

    result = repair_cli_configuration_error("codex", raw_error)

    assert result.attempted is True
    assert result.repaired is True
    assert result.config_path == str(config_path)
    assert Path(result.backup_path).exists()
    assert 'service_tier = "fast"' in config_path.read_text(encoding="utf-8")
    assert 'service_tier = "priority"' in Path(result.backup_path).read_text(encoding="utf-8")


def test_model_options_include_default_and_preserve_custom_model():
    codex_options = model_options_for_provider("codex")
    custom_options = model_options_for_provider("gemini", "my-custom-model")

    assert codex_options[0] == ("CLI 기본값", "")
    assert ("GPT-5.5", "gpt-5.5") in codex_options
    assert ("사용자 설정: my-custom-model", "my-custom-model") in custom_options


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
        assert isinstance(tab.attachment_list, QListWidget)
        assert "첨부 2개" in tab.context_status_label.text()
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

        tab._active_context_status_text = tab._context_status_text("실행 중", 12, 100, 1)
        tab.prompt_edit.clear()
        tab._refresh_attachment_view()
        assert tab.context_status_label.text().startswith("실행 중")

        tab._active_context_status_text = ""
        tab._update_context_status()
        assert tab.context_status_label.text().startswith("이번 요청")
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
        assert captured["worker_args"] == ("네트워크 상태 점검해줘",)
        assert captured["internal_context"] == "internal context from netops tools"
        assert captured["payload"]["prompt"] == "네트워크 상태 점검해줘"
        assert tab._pending_prompt_payload is None
        assert tab._context_collecting is False
        assert tab.prompt_edit.isEnabled()
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
        tab._pending_prompt_payload = {"prompt": "네트워크 상태 점검"}
        tab._set_preparing(True)

        tab.cancel_prompt()

        assert tab._context_collection_cancelled is True
        assert tab._pending_prompt_payload is None
        assert tab._context_collecting is False
        assert tab.prompt_edit.isEnabled()
        assert tab.send_button.isEnabled()
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
        categories = tab._netops_context_categories("장비 점검/백업 템플릿이랑 CLI 설정 생성 프로파일 만들어줘")
        context = tab._collect_internal_netops_context("장비 점검/백업 템플릿이랑 CLI 설정 생성 프로파일 만들어줘")

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
        assert tab._blocked_direct_extra_args(["--output-format=json", "--dangerously-bypass-approvals-and-sandbox"]) == [
            "--output-format",
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
        assert tab.prompt_edit.placeholderText() == "선택한 CLI 계정으로 보낼 요청..."
        assert tab.model_combo.currentData() == "my-custom-model"
        assert tab.model_combo.currentText() == "사용자 설정: my-custom-model"

        tabs = tab.findChildren(QTabWidget)[0]
        assert [tabs.tabText(index) for index in range(tabs.count())] == ["채팅", "옵션 선택"]

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
        rendered_labels = "\n".join(label.text() for label in tab.findChildren(QLabel))
        assert "hello <world>" in rendered_labels

        tab._populate_help_options(
            """
Options:
  -p, --profile <CONFIG_PROFILE>
          Configuration profile
      --search
          Enable search
"""
        )
        tab._set_combo_data(tab.option_combo, tab.option_combo.itemData(0))
        tab.option_value_edit.setText("work")
        tab.add_selected_extra_arg()
        assert "--profile work" in tab.extra_args_edit.text()

        tab._set_combo_data(tab.option_combo, tab.option_combo.itemData(1))
        tab.add_selected_extra_arg()
        assert "--search" in tab.extra_args_edit.text()
    finally:
        tab.close()
