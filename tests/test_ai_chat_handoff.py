from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from PySide6.QtWidgets import QPushButton

from app.main_window import MainWindow
from app.services.ai_agent_service import ProviderEventAccumulator
from app.ui.tabs.ai_chat_tab import AiChatTab


def _state(tmp_path):
    return SimpleNamespace(
        app_config={"ai_chat": {"active_provider": "codex"}},
        paths=SimpleNamespace(
            root=tmp_path,
            data_root=tmp_path,
            logs_dir=tmp_path / "logs",
            exports_dir=tmp_path / "exports",
        ),
        logger=logging.getLogger("test-ai-chat-handoff"),
        save_app_config=lambda _config: None,
    )


def test_grounding_keeps_inspector_context_for_follow_up_and_resets(
    qapp, tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    caplog.set_level(logging.INFO, logger="test-ai-chat-handoff")
    tab = AiChatTab(_state(tmp_path))
    try:
        first = tab._ground_user_prompt(
            "스위치 10대의 서로 다른 호스트네임을 SSH로 수정하고 싶어 "
            r"password=topsecret C:\Users\PC\secret.txt"
        )
        assert first is not None
        assert first.feature_id == "inspector.user-commands"
        assert first.route == "inspector"
        assert first.guide_id == "inspector"
        assert first.consent_required is True

        prompt_context = tab._grounding_prompt_context(first)
        assert "{{ new_hostname }}" in prompt_context
        assert "먼저 검증" in prompt_context
        assert "동의를 먼저" in prompt_context
        assert "custom_commands" not in prompt_context
        assert "inspection_backup" not in prompt_context
        diagnostic_log = "\n".join(record.getMessage() for record in caplog.records)
        assert "feature_id=inspector.user-commands" in diagnostic_log
        assert "action=partial_support" in diagnostic_log
        assert "support_level=partial" in diagnostic_log
        assert "consent_required=True" in diagnostic_log
        assert "topsecret" not in diagnostic_log
        assert r"C:\Users\PC" not in diagnostic_log

        follow_up = tab._ground_user_prompt("각 장비마다 호스트네임이 다른데")
        assert follow_up is not None
        assert follow_up.feature_id == "inspector.user-commands"
        assert len(tab._grounding_state.recent_user_utterances) == 2

        consented = tab._ground_user_prompt("동의해")
        assert consented is not None
        assert consented.consent_required is False
        assert "이번 요청에 한해" in consented.public_context

        next_request = tab._ground_user_prompt("계속")
        assert next_request is not None
        assert next_request.consent_required is True
        assert len(tab._grounding_state.recent_user_utterances) == 3

        tab.reset_session()
        assert tab._grounding_state.active_feature_id == ""
        assert tab._grounding_state.recent_user_utterances == ()
        assert tab._grounding_state.pending_alternative_feature_id == ""
    finally:
        tab.close()


def test_assistant_message_handoff_buttons_emit_public_targets(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    tab = AiChatTab(_state(tmp_path))
    feature_requests: list[str] = []
    guide_requests: list[str] = []
    tab.feature_requested.connect(feature_requests.append)
    tab.guide_requested.connect(guide_requests.append)
    try:
        tab._active_response_handoff = {
            "feature_id": "inspector.user-commands",
            "route": "inspector",
            "guide_id": "inspector",
            "public_name": "장비 점검 및 백업 · 사용자 명령",
        }
        tab._append_stream("내부 기능을 이용하는 정확한 안내입니다.")
        tab._render_transcript()
        qapp.processEvents()

        feature_button = tab.findChild(QPushButton, "aiChatOpenFeatureButton")
        guide_button = tab.findChild(QPushButton, "aiChatOpenGuideButton")
        assert feature_button is not None
        assert guide_button is not None
        assert feature_button.accessibleName() == (
            "장비 점검 및 백업 · 사용자 명령 기능 열기"
        )
        assert guide_button.accessibleName() == (
            "장비 점검 및 백업 · 사용자 명령 가이드 열기"
        )

        feature_button.click()
        guide_button.click()
        assert feature_requests == ["inspector"]
        assert guide_requests == ["inspector"]
        assert "inspector.user-commands" not in tab._plain_transcript_text()
    finally:
        tab.close()


def test_partial_feature_is_answered_before_any_external_cli(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    monkeypatch.setattr(
        "app.ui.tabs.ai_chat_tab.inspect_provider",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("external CLI preflight must not run")
        ),
    )
    tab = AiChatTab(_state(tmp_path))
    try:
        tab.prompt_edit.setPlainText(
            "스위치 10대에 잘못 넣은 서로 다른 호스트네임을 SSH로 수정하고 싶어"
        )
        tab.send_prompt()
        qapp.processEvents()

        assert tab._process is None
        assert [message["title"] for message in tab._messages] == ["사용자", "NetOps"]
        response = tab._messages[-1]["body"]
        assert "{{ new_hostname }}" in response
        assert "먼저 검증" in response
        assert "이번 요청에 한해 동의" in response
        assert "custom_commands" not in response
        assert "inspection_backup" not in response
        assert tab.findChild(QPushButton, "aiChatOpenFeatureButton") is not None
        assert tab.findChild(QPushButton, "aiChatOpenGuideButton") is not None
    finally:
        tab.close()


def test_managed_provider_stream_keeps_only_final_codex_answer(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda self: None)
    tab = AiChatTab(_state(tmp_path))

    class FakePromptProcess:
        def __init__(self) -> None:
            self.stdout = b""
            self.properties = {
                "provider_key": "codex",
                "response_title": "ChatGPT Codex · 테스트 모델",
            }

        def readAllStandardOutput(self):
            output, self.stdout = self.stdout, b""
            return output

        def property(self, name):
            return self.properties.get(name)

        def setProperty(self, name, value):
            self.properties[name] = value

    process = FakePromptProcess()
    tab._process = process
    tab._provider_event_accumulators[id(process)] = ProviderEventAccumulator("codex")
    try:
        process.stdout = (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "내부 파일을 확인하겠습니다.",
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )
        tab._read_stdout(process)
        assert not tab._messages

        process.stdout = (
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {"type": "reasoning", "text": "내부 추론"},
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "agent_message",
                                "text": "완성된 최종 답변",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps({"type": "turn.completed"}),
                    "",
                ]
            ).encode("utf-8")
        )
        tab._read_stdout(process)

        transcript = tab._plain_transcript_text()
        assert "완성된 최종 답변" in transcript
        assert "내부 파일을 확인하겠습니다" not in transcript
        assert "내부 추론" not in transcript
    finally:
        tab._process = None
        tab._provider_event_accumulators.clear()
        tab.close()


class _FakeTabWidget:
    def __init__(self) -> None:
        self.current = None

    def setCurrentWidget(self, widget) -> None:  # noqa: N802 - Qt-compatible fake
        self.current = widget


class _FakeStatusBar:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def showMessage(self, message: str, _timeout: int = 0) -> None:  # noqa: N802
        self.messages.append(message)


class _FakeDiagnostics:
    def __init__(self) -> None:
        self.selected: list[str] = []

    def select_diagnostic_tab(self, key: str) -> bool:
        self.selected.append(key)
        return True


def test_main_window_opens_public_feature_routes_without_internal_ids():
    status = _FakeStatusBar()
    diagnostics = _FakeDiagnostics()
    window = SimpleNamespace(
        _MAIN_PAGE_SPECS=MainWindow._MAIN_PAGE_SPECS,
        interface_tab=object(),
        diagnostics_tab=diagnostics,
        wireless_tab=object(),
        inspector_tab=object(),
        config_builder_tab=object(),
        ai_chat_tab=object(),
        settings_tab=object(),
        tab_widget=_FakeTabWidget(),
        statusBar=lambda: status,
    )

    assert MainWindow.open_feature_route(window, "inspector") is True
    assert window.tab_widget.current is window.inspector_tab

    assert MainWindow.open_feature_route(window, "diagnostics.ping") is True
    assert window.tab_widget.current is diagnostics
    assert diagnostics.selected == ["ping"]

    assert MainWindow.open_feature_route(window, "config_builder") is True
    assert window.tab_widget.current is window.config_builder_tab

    assert MainWindow.open_feature_route(window, "unknown-internal-value") is False
    assert "찾을 수 없습니다" in status.messages[-1]
