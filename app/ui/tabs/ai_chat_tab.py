from __future__ import annotations

import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDateTime, QProcess, QProcessEnvironment, QThreadPool, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.models.ai_models import AiProviderConfig, KNOWN_AI_PROVIDERS, normalize_ai_chat_config
from app.services.ai_agent_service import (
    CliHelpOption,
    PROVIDER_SPECS,
    build_chat_invocation,
    build_help_invocation,
    build_login_invocation,
    build_status_invocation,
    decode_cli_output,
    diagnose_cli_error,
    extract_text_from_cli_line,
    extra_arg_options_from_help,
    inspect_provider,
    is_blocking_cli_configuration_error,
    model_options_for_provider,
    provider_configs_from_app_config,
    repair_cli_configuration_error,
    safe_env_for_cli,
    should_ignore_cli_output_text,
)
from app.ui.common import JobRunner, make_step_hint
from app.utils.file_utils import timestamped_export_path
from netops_suite.modules.config_builder import ConfigBuilderService
from netops_suite.modules.inspector import InspectorService
from netops_suite.ui.actions import ActionKind, make_action_button


IMAGE_ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
TEXT_ATTACHMENT_EXTENSIONS = {
    ".bat",
    ".cfg",
    ".conf",
    ".csv",
    ".css",
    ".env",
    ".htm",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".log",
    ".md",
    ".ps1",
    ".py",
    ".rst",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
MAX_ATTACHMENT_BYTES = 180_000
MAX_ATTACHMENT_CONTEXT_CHARS = 360_000
MAX_INTERNAL_CONTEXT_SECTION_CHARS = 6_000
MAX_INTERNAL_CONTEXT_TOTAL_CHARS = 28_000
NETWORK_STATUS_TEXT_KEYWORDS = (
    "네트워크",
    "인터넷",
    "wi-fi",
    "와이파이",
    "어댑터",
    "아이피",
    "게이트웨이",
    "dns",
    "연결",
    "라우팅",
    "공인 ip",
    "공인 아이피",
    "네트워크 상태",
    "연결 진단",
)
NETWORK_STATUS_WORD_RE = re.compile(
    r"(?<![a-z0-9])(ip|ipv4|ipv6|dns|ping|gateway|adapter|wifi|wi-fi|lan|route|connect|connectivity|public ip)(?![a-z0-9])"
)
NETWORK_STATUS_KOREAN_SHORT_RE = re.compile(r"(?<![가-힣])(랜|핑)(?![가-힣])")
INSPECTOR_CONTEXT_KEYWORDS = (
    "장비 점검",
    "장비 백업",
    "점검/백업",
    "점검 백업",
    "인벤토리",
    "inspection",
    "custom_rules",
    "커스텀 룰",
    "점검 템플릿",
    "백업 템플릿",
)
CONFIG_BUILDER_CONTEXT_KEYWORDS = (
    "cli 설정",
    "설정 생성",
    "config 생성",
    "configuration",
    "config builder",
    "스위치 설정",
    "라우터 설정",
    "장비 설정",
    "프로파일 yaml",
    "profile yaml",
)
PROFILE_CONTEXT_KEYWORDS = (
    "ip 프로파일",
    "저장된 프로파일",
    "네트워크 프로파일",
    "전송 프로파일",
    "preset",
    "프리셋",
)
TRANSFER_CONTEXT_KEYWORDS = (
    "ftp 프로파일",
    "ftp 전송",
    "scp 프로파일",
    "scp 전송",
    "sftp 프로파일",
    "tftp 전송",
    "파일 전송",
    "전송 프로파일",
)
BASIC_NETOPS_CONTEXT_KEYWORDS = (
    "netops",
    "netops suite",
    "프로그램 기능",
    "내부 기능",
    "앱 기능",
)
BASIC_NETWORK_DIAGNOSTIC_TARGETS = (("Google DNS", "8.8.8.8"), ("Cloudflare DNS", "1.1.1.1"))
BLOCKED_DIRECT_EXTRA_ARG_FLAGS = {
    "-h",
    "--help",
    "-V",
    "--version",
    "-m",
    "--model",
    "-i",
    "--image",
    "--output-format",
    "--json",
    "--verbose",
    "--dangerously-bypass-approvals-and-sandbox",
}
BINARY_ATTACHMENT_MAGIC = (
    b"%PDF",
    b"PK\x03\x04",
    b"\x7fELF",
    b"\xd0\xcf\x11\xe0",
    b"\x89PNG",
    b"\xff\xd8\xff",
    b"GIF8",
)


class AiChatTab(QWidget):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._providers: dict[str, AiProviderConfig] = provider_configs_from_app_config(self._ai_config())
        self._process: QProcess | None = None
        self._status_process: QProcess | None = None
        self._help_process: QProcess | None = None
        thread_pool = getattr(self.state, "thread_pool", None) or QThreadPool.globalInstance()
        self._job_runner = JobRunner(thread_pool, self, default_error_title="AI 준비 실패")
        self._stdout_buffer = b""
        self._help_stdout = b""
        self._help_stderr = b""
        self._stderr_text = ""
        self._help_loaded_for = ""
        self._messages: list[dict[str, str]] = []
        self._attachments: list[Path] = []
        self._active_context_status_text = ""
        self._context_collecting = False
        self._context_collection_cancelled = False
        self._pending_prompt_payload: dict[str, Any] | None = None
        self._stream_message_index: int | None = None
        self._last_render_width = 0
        self._render_deferred = False
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(50)
        self._render_timer.timeout.connect(self._flush_transcript_render)
        self._active_provider = self._ai_config().get("active_provider", "codex")
        self._build_ui()
        self._load_config_into_ui()
        self.refresh_provider_status()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(make_step_hint("개인 계정 CLI 채팅: 제공자와 모델을 선택하고 상태 확인 후 요청을 보냅니다."))

        self.ai_chat_tabs = QTabWidget()
        layout.addWidget(self.ai_chat_tabs, 1)

        self.chat_page = QWidget()
        chat_page = self.chat_page
        chat_layout = QVBoxLayout(chat_page)
        chat_layout.setContentsMargins(0, 8, 0, 0)
        chat_layout.setSpacing(10)

        provider_group = QGroupBox("제공자 설정")
        provider_group.setObjectName("aiProviderSettingsGroup")
        provider_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        provider_layout = QGridLayout(provider_group)
        provider_layout.setContentsMargins(8, 12, 8, 8)
        provider_layout.setHorizontalSpacing(8)
        provider_layout.setVerticalSpacing(6)
        for column in range(4):
            provider_layout.setColumnStretch(column, 1)

        self.provider_combo = QComboBox()
        for key in KNOWN_AI_PROVIDERS:
            self.provider_combo.addItem(PROVIDER_SPECS[key].display_name, key)
        self.command_edit = QLineEdit()
        self.model_combo = QComboBox()
        self.reasoning_combo = QComboBox()
        self.reasoning_combo.addItem("CLI 기본값", "")
        self.reasoning_combo.addItem("낮음", "low")
        self.reasoning_combo.addItem("보통", "medium")
        self.reasoning_combo.addItem("높음", "high")
        self.reasoning_combo.addItem("엑스트라 하이", "xhigh")
        self.speed_combo = QComboBox()
        self.speed_combo.addItem("CLI 기본값", "")
        self.speed_combo.addItem("보통(flex)", "flex")
        self.speed_combo.addItem("빠름(fast)", "fast")
        self.extra_args_edit = QLineEdit()
        self.status_label = QLabel("미확인")
        self.context_status_label = QLabel("이번 요청 약 0토큰 · 첨부 0개")
        self.status_label.setWordWrap(False)
        self.context_status_label.setWordWrap(False)
        self.context_status_label.setToolTip("입력 글자 수 기준의 대략적인 추정치입니다. 실제 한도는 선택한 CLI와 모델이 판단합니다.")
        self.command_edit.setPlaceholderText("PATH에서 자동 탐지")
        self.extra_args_edit.setPlaceholderText("예: --profile work")

        provider_layout.addWidget(self._provider_field("제공자", self.provider_combo), 0, 0)
        provider_layout.addWidget(self._provider_field("모델", self.model_combo), 0, 1)
        provider_layout.addWidget(self._provider_field("추론 강도", self.reasoning_combo), 0, 2)
        provider_layout.addWidget(self._provider_field("속도", self.speed_combo), 0, 3)
        provider_layout.addWidget(self._provider_field("명령", self.command_edit), 1, 0, 1, 2)
        provider_layout.addWidget(self._provider_field("추가 인자", self.extra_args_edit), 1, 2, 1, 2)
        provider_layout.addWidget(self._provider_field("컨텍스트", self.context_status_label), 2, 0, 1, 2)
        provider_layout.addWidget(self._provider_field("상태", self.status_label), 2, 2, 1, 2)
        chat_layout.addWidget(provider_group)

        provider_actions = QHBoxLayout()
        self.check_button = make_action_button("상태 확인", ActionKind.REFRESH)
        self.login_button = make_action_button("로그인 터미널", ActionKind.START)
        self.save_button = make_action_button("설정 저장", ActionKind.SAVE)
        provider_actions.addWidget(self.check_button)
        provider_actions.addWidget(self.login_button)
        provider_actions.addWidget(self.save_button)
        provider_actions.addStretch(1)
        chat_layout.addLayout(provider_actions)

        self.transcript_scroll = QScrollArea()
        self.transcript_scroll.setObjectName("aiChatTranscript")
        self.transcript_scroll.setWidgetResizable(True)
        self.transcript_scroll.setMinimumHeight(180)
        self.transcript_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript_scroll.setStyleSheet(
            """
            QScrollArea#aiChatTranscript {
                background: #f6f8fb;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
            }
            QWidget#aiChatMessageContainer { background: #f6f8fb; }
            """
        )
        self.message_container = QWidget()
        self.message_container.setObjectName("aiChatMessageContainer")
        self.message_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.message_layout = QVBoxLayout(self.message_container)
        self.message_layout.setContentsMargins(12, 12, 12, 12)
        self.message_layout.setSpacing(8)
        self.transcript_scroll.setWidget(self.message_container)
        self._render_transcript()
        chat_layout.addWidget(self.transcript_scroll, 1)

        attachment_group = QGroupBox("첨부 파일")
        attachment_layout = QVBoxLayout(attachment_group)
        attachment_layout.setContentsMargins(2, 10, 2, 2)
        attachment_layout.setSpacing(6)
        self.attachment_list = QListWidget()
        self.attachment_list.setMaximumHeight(58)
        self.attachment_list.setAlternatingRowColors(True)
        self.attachment_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        attachment_layout.addWidget(self.attachment_list)

        attachment_actions = QHBoxLayout()
        self.attach_button = make_action_button("파일 첨부", ActionKind.ADD)
        self.remove_attachment_button = make_action_button("선택 제거", ActionKind.DELETE, enabled=False)
        self.clear_attachments_button = make_action_button("전체 비우기", ActionKind.DELETE, enabled=False)
        attachment_actions.addWidget(self.attach_button)
        attachment_actions.addWidget(self.remove_attachment_button)
        attachment_actions.addWidget(self.clear_attachments_button)
        attachment_actions.addStretch(1)
        attachment_layout.addLayout(attachment_actions)
        chat_layout.addWidget(attachment_group)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("선택한 CLI 계정으로 보낼 요청...")
        self.prompt_edit.setMinimumHeight(72)
        self.prompt_edit.setMaximumHeight(130)
        chat_layout.addWidget(self.prompt_edit)

        chat_actions = QHBoxLayout()
        self.send_button = make_action_button("보내기", ActionKind.START)
        self.stop_button = make_action_button("중지", ActionKind.STOP, enabled=False)
        self.export_button = make_action_button("내보내기", ActionKind.EXPORT)
        self.clear_button = make_action_button("비우기", ActionKind.DELETE)
        chat_actions.addWidget(self.send_button)
        chat_actions.addWidget(self.stop_button)
        chat_actions.addWidget(self.export_button)
        chat_actions.addWidget(self.clear_button)
        chat_actions.addStretch(1)
        chat_layout.addLayout(chat_actions)

        self.ai_chat_tabs.addTab(chat_page, "채팅")

        options_page = QWidget()
        options_layout = QVBoxLayout(options_page)
        options_layout.setContentsMargins(0, 8, 0, 0)
        options_layout.setSpacing(10)

        help_actions = QHBoxLayout()
        self.help_refresh_button = make_action_button("옵션 새로고침", ActionKind.REFRESH)
        self.help_status_label = QLabel("로그인 확인 후 CLI --help에서 옵션을 불러옵니다.")
        self.help_status_label.setWordWrap(True)
        help_actions.addWidget(self.help_refresh_button)
        help_actions.addWidget(self.help_status_label, 1)
        options_layout.addLayout(help_actions)

        option_group = QGroupBox("추가 인자 선택")
        option_form = QFormLayout(option_group)
        option_form.setContentsMargins(2, 10, 2, 2)
        option_form.setHorizontalSpacing(8)
        option_form.setVerticalSpacing(6)

        self.option_combo = QComboBox()
        self.option_value_edit = QLineEdit()
        self.option_value_edit.setPlaceholderText("값이 필요한 옵션이면 입력")
        self.option_description = QTextEdit()
        self.option_description.setReadOnly(True)
        self.option_description.setMaximumHeight(120)
        self.option_description.setStyleSheet(
            """
            QTextEdit {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 8px;
            }
            """
        )
        option_form.addRow("옵션", self.option_combo)
        option_form.addRow("값", self.option_value_edit)
        option_form.addRow("설명", self.option_description)
        options_layout.addWidget(option_group)

        option_actions = QHBoxLayout()
        self.add_option_button = make_action_button("추가 인자에 붙이기", ActionKind.ADD)
        self.remove_option_button = make_action_button("선택 옵션 제거", ActionKind.DELETE)
        option_actions.addWidget(self.add_option_button)
        option_actions.addWidget(self.remove_option_button)
        option_actions.addStretch(1)
        options_layout.addLayout(option_actions)

        self.raw_help_edit = QPlainTextEdit()
        self.raw_help_edit.setReadOnly(True)
        self.raw_help_edit.setPlaceholderText("CLI --help 원문이 여기에 표시됩니다.")
        self.raw_help_edit.setMinimumHeight(120)
        options_layout.addWidget(self.raw_help_edit, 1)

        self.ai_chat_tabs.addTab(options_page, "옵션 선택")
        self.ai_chat_tabs.currentChanged.connect(self._handle_ai_chat_tab_changed)

        self.provider_combo.currentIndexChanged.connect(self._handle_provider_changed)
        self.command_edit.editingFinished.connect(self.save_current_config)
        self.model_combo.currentIndexChanged.connect(self.save_current_config)
        self.model_combo.currentIndexChanged.connect(self._update_context_status)
        self.reasoning_combo.currentIndexChanged.connect(self.save_current_config)
        self.speed_combo.currentIndexChanged.connect(self.save_current_config)
        self.extra_args_edit.editingFinished.connect(self.save_current_config)
        self.prompt_edit.textChanged.connect(self._update_context_status)
        self.attachment_list.itemSelectionChanged.connect(self._update_attachment_buttons)
        self.attach_button.clicked.connect(self.attach_files)
        self.remove_attachment_button.clicked.connect(self.remove_selected_attachments)
        self.clear_attachments_button.clicked.connect(self.clear_attachments)
        self.check_button.clicked.connect(lambda _checked=False: self.refresh_provider_status(allow_repair=True))
        self.login_button.clicked.connect(self.open_provider_login)
        self.save_button.clicked.connect(self.save_current_config)
        self.send_button.clicked.connect(self.send_prompt)
        self.stop_button.clicked.connect(self.cancel_prompt)
        self.export_button.clicked.connect(self.export_session)
        self.clear_button.clicked.connect(self.clear_transcript)
        self.help_refresh_button.clicked.connect(self.refresh_cli_help_options)
        self.option_combo.currentIndexChanged.connect(self._handle_help_option_changed)
        self.add_option_button.clicked.connect(self.add_selected_extra_arg)
        self.remove_option_button.clicked.connect(self.remove_selected_extra_arg)
        self._populate_help_options("")

    @staticmethod
    def _provider_field(label_text: str, widget: QWidget) -> QWidget:
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(2)

        label = QLabel(label_text)
        label.setWordWrap(False)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label.setStyleSheet("color: #344054; font-size: 11px;")

        widget.setMinimumWidth(0)
        widget.setMinimumHeight(24)
        if isinstance(widget, (QComboBox, QLineEdit)):
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        elif isinstance(widget, QLabel):
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        container_layout.addWidget(label)
        container_layout.addWidget(widget)
        return container

    def _ai_config(self) -> dict[str, Any]:
        return normalize_ai_chat_config(self.state.app_config.get("ai_chat", {}))

    def _load_config_into_ui(self) -> None:
        self._set_combo_data(self.provider_combo, self._active_provider)
        self._load_provider_fields(self.current_provider_key())

    def _handle_provider_changed(self) -> None:
        self._active_provider = self.current_provider_key()
        self._reset_help_options()
        self._load_provider_fields(self._active_provider)
        self.save_current_config()
        self.refresh_provider_status()

    def _load_provider_fields(self, key: str) -> None:
        config = self._providers.get(key, AiProviderConfig(key=key))
        self.command_edit.setText(config.command_path)
        self._populate_model_combo(key, config.model)
        self.reasoning_combo.blockSignals(True)
        self.speed_combo.blockSignals(True)
        self._set_combo_data(self.reasoning_combo, config.reasoning_effort if key == "codex" else "")
        self._set_combo_data(self.speed_combo, config.speed if key == "codex" else "")
        self.reasoning_combo.blockSignals(False)
        self.speed_combo.blockSignals(False)
        self._sync_codex_controls()
        self.extra_args_edit.setText(" ".join(config.extra_args))
        self._update_context_status()

    def _populate_model_combo(self, key: str, current_model: str = "") -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for label, model in model_options_for_provider(key, current_model):
            self.model_combo.addItem(label, model)
        self._set_combo_data(self.model_combo, current_model.strip())
        self.model_combo.blockSignals(False)

    def _sync_codex_controls(self) -> None:
        is_codex = self.current_provider_key() == "codex"
        self.reasoning_combo.setEnabled(is_codex)
        self.speed_combo.setEnabled(is_codex)

    def current_provider_key(self) -> str:
        return str(self.provider_combo.currentData() or "codex")

    def current_provider_config(self) -> AiProviderConfig:
        key = self.current_provider_key()
        existing = self._providers.get(key, AiProviderConfig(key=key))
        extra_args = self._split_extra_args()
        config = AiProviderConfig(
            key=key,
            enabled=True,
            command_path=self.command_edit.text().strip(),
            model=str(self.model_combo.currentData() or ""),
            reasoning_effort=str(self.reasoning_combo.currentData() or "") if key == "codex" else "",
            speed=str(self.speed_combo.currentData() or "") if key == "codex" else "",
            role_prompt="",
            extra_args=extra_args,
            timeout_seconds=existing.timeout_seconds,
        )
        self._providers[key] = config
        return config

    def save_current_config(self) -> None:
        self.current_provider_config()
        config = dict(self.state.app_config)
        providers = {key: provider.to_dict() for key, provider in self._providers.items()}
        config["ai_chat"] = normalize_ai_chat_config(
            {
                "version": 1,
                "active_provider": self.current_provider_key(),
                "auto_export": self._ai_config().get("auto_export", False),
                "providers": providers,
            }
        )
        self.state.save_app_config(config)

    def save_ui_state(self) -> dict:
        self.save_current_config()
        return {
            "draft_prompt": self.prompt_edit.toPlainText(),
            "active_provider": self.current_provider_key(),
        }

    def restore_ui_state(self, ui_state: dict | None) -> None:
        state = ui_state if isinstance(ui_state, dict) else {}
        active_provider = str(state.get("active_provider", "") or "")
        if active_provider in KNOWN_AI_PROVIDERS:
            self._set_combo_data(self.provider_combo, active_provider)
        if state.get("draft_prompt"):
            self.prompt_edit.setPlainText(str(state.get("draft_prompt", "")))

    def attach_files(self) -> None:
        files, _selected_filter = QFileDialog.getOpenFileNames(self, "첨부할 파일 선택", str(self.state.paths.root))
        if not files:
            return
        known = {str(path).casefold() for path in self._attachments}
        for file_name in files:
            path = Path(file_name)
            key = str(path).casefold()
            if key in known:
                continue
            self._attachments.append(path)
            known.add(key)
        self._refresh_attachment_view()

    def remove_selected_attachments(self) -> None:
        selected_paths = {
            str(item.data(Qt.ItemDataRole.UserRole)).casefold()
            for item in self.attachment_list.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        }
        if not selected_paths:
            return
        self._attachments = [path for path in self._attachments if str(path).casefold() not in selected_paths]
        self._refresh_attachment_view()

    def clear_attachments(self) -> None:
        if not self._attachments:
            return
        self._attachments.clear()
        self._refresh_attachment_view()

    def _refresh_attachment_view(self) -> None:
        self.attachment_list.clear()
        for path in self._attachments:
            label = f"{path.name} · {self._attachment_display_detail(path)}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.attachment_list.addItem(item)
        self._update_attachment_buttons()
        self._update_context_status()

    def _update_attachment_buttons(self) -> None:
        has_attachments = bool(self._attachments)
        self.remove_attachment_button.setEnabled(bool(self.attachment_list.selectedItems()))
        self.clear_attachments_button.setEnabled(has_attachments)

    def _attachment_display_detail(self, path: Path) -> str:
        if not path.exists():
            return "파일 없음"
        try:
            size = path.stat().st_size
        except OSError:
            return "읽기 불가"
        kind = "이미지" if path.suffix.lower() in IMAGE_ATTACHMENT_EXTENSIONS else "파일"
        return f"{kind}, {self._format_bytes(size)}"

    def _update_context_status(self) -> None:
        if not hasattr(self, "context_status_label"):
            return
        if self._active_context_status_text:
            self.context_status_label.setText(self._active_context_status_text)
            return
        prompt_chars = len(self.prompt_edit.toPlainText()) if hasattr(self, "prompt_edit") else 0
        attachment_chars = self._estimated_attachment_context_chars()
        self.context_status_label.setText(
            self._context_status_text("이번 요청", prompt_chars, attachment_chars, len(self._attachments))
        )

    @staticmethod
    def _context_status_text(prefix: str, prompt_chars: int, attachment_chars: int, attachment_count: int) -> str:
        total_chars = prompt_chars + attachment_chars
        approx_tokens = (total_chars + 3) // 4 if total_chars else 0
        suffix = " · 첨부 일부만 포함" if attachment_chars >= MAX_ATTACHMENT_CONTEXT_CHARS else ""
        return f"{prefix} 약 {approx_tokens:,}토큰 · 첨부 {attachment_count}개 · {total_chars:,}자{suffix}"

    def _estimated_attachment_context_chars(self) -> int:
        total = 0
        for path in self._attachments:
            if not path.exists() or path.suffix.lower() in IMAGE_ATTACHMENT_EXTENSIONS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            total += min(size, MAX_ATTACHMENT_BYTES)
            if total >= MAX_ATTACHMENT_CONTEXT_CHARS:
                return MAX_ATTACHMENT_CONTEXT_CHARS
        return total

    def refresh_provider_status(self, allow_repair: bool = False) -> None:
        self._stop_status_process()
        config = self.current_provider_config()
        health = inspect_provider(config)
        if not health.installed:
            self._set_status("CLI 없음", health.detail)
            return

        invocation = build_status_invocation(config, str(self.state.paths.root))
        process = QProcess(self)
        self._status_process = process
        process.setProperty("provider_key", config.key)
        process.setProperty("allow_repair", allow_repair)
        process.setProgram(invocation.program)
        process.setArguments(invocation.args)
        process.setWorkingDirectory(invocation.working_dir or str(self.state.paths.root))
        process.setProcessEnvironment(self._process_environment())
        process.finished.connect(lambda exit_code, _status: self._finish_status(exit_code))
        process.errorOccurred.connect(lambda _error: self._set_status("상태 확인 실패", "상태 확인 명령을 시작하지 못했습니다."))
        self._set_status("확인 중", invocation.program)
        process.start()
        QTimer.singleShot(invocation.timeout_seconds * 1000, self._timeout_status)

    def _finish_status(self, exit_code: int) -> None:
        process = self._status_process
        self._status_process = None
        if process is None:
            return
        try:
            stdout = decode_cli_output(bytes(process.readAllStandardOutput())).strip()
            stderr = decode_cli_output(bytes(process.readAllStandardError())).strip()
        except RuntimeError:
            return
        provider_key = str(process.property("provider_key") or self.current_provider_key())
        allow_repair = bool(process.property("allow_repair"))
        if exit_code == 0:
            detail = stdout or stderr or "사용 가능"
            self._set_status("사용 가능", detail)
            process.deleteLater()
            if self._help_loaded_for != provider_key:
                QTimer.singleShot(0, self.refresh_cli_help_options)
            return

        detail = "\n".join(part for part in (stderr, stdout) if part).strip()
        if is_blocking_cli_configuration_error(provider_key, detail):
            repair = repair_cli_configuration_error(provider_key, detail) if allow_repair else None
            if repair is not None and repair.repaired:
                self._append_block("시스템", repair.message)
                self._set_status("CLI 설정 자동 복구", repair.message)
                process.deleteLater()
                QTimer.singleShot(200, self.refresh_provider_status)
                return
            message = diagnose_cli_error(provider_key, detail)
            if repair is not None and repair.attempted and repair.message:
                message = f"{repair.message}\n\n{message}"
            self._set_status("CLI 설정 오류", message)
        else:
            self._set_status("로그인 필요", diagnose_cli_error(provider_key, detail))
        process.deleteLater()

    def _timeout_status(self) -> None:
        process = self._status_process
        if process is None:
            return
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                self._set_status("상태 확인 시간 초과", "상태 확인 명령이 끝나지 않았습니다.")
        except RuntimeError:
            self._status_process = None

    def refresh_cli_help_options(self) -> None:
        self._stop_help_process()
        config = self.current_provider_config()
        health = inspect_provider(config)
        if not health.installed:
            self.help_status_label.setText(f"CLI를 찾지 못했습니다: {health.detail}")
            self._populate_help_options("")
            return

        invocation = build_help_invocation(config, str(self.state.paths.root))
        process = QProcess(self)
        self._help_process = process
        self._help_stdout = b""
        self._help_stderr = b""
        process.setProperty("provider_key", config.key)
        process.setProgram(invocation.program)
        process.setArguments(invocation.args)
        process.setWorkingDirectory(invocation.working_dir or str(self.state.paths.root))
        process.setProcessEnvironment(self._process_environment())
        process.readyReadStandardOutput.connect(self._read_help_stdout)
        process.readyReadStandardError.connect(self._read_help_stderr)
        process.finished.connect(lambda exit_code, _status: self._finish_help_options(exit_code))
        process.errorOccurred.connect(lambda _error: self._fail_help_options("CLI help 명령을 시작하지 못했습니다."))
        self.help_status_label.setText(f"--help 불러오는 중: {subprocess.list2cmdline([invocation.program, *invocation.args])}")
        process.start()
        QTimer.singleShot(invocation.timeout_seconds * 1000, self._timeout_help_options)

    def _read_help_stdout(self) -> None:
        if self._help_process is None:
            return
        self._help_stdout += bytes(self._help_process.readAllStandardOutput())

    def _read_help_stderr(self) -> None:
        if self._help_process is None:
            return
        self._help_stderr += bytes(self._help_process.readAllStandardError())

    def _finish_help_options(self, exit_code: int) -> None:
        process = self._help_process
        self._help_process = None
        if process is None:
            return
        provider_key = str(process.property("provider_key") or self.current_provider_key())
        process.deleteLater()
        help_text = "\n".join(
            part.strip()
            for part in (decode_cli_output(self._help_stdout), decode_cli_output(self._help_stderr))
            if part.strip()
        )
        self.raw_help_edit.setPlainText(help_text)
        options = self._populate_help_options(help_text)
        self._help_loaded_for = provider_key if options else ""
        if options:
            self.help_status_label.setText(f"{PROVIDER_SPECS[provider_key].display_name} 옵션 {len(options)}개를 불러왔습니다.")
        elif exit_code == 0:
            self.help_status_label.setText("help 출력은 받았지만 옵션을 자동 인식하지 못했습니다. 추가 인자를 직접 입력할 수 있습니다.")
        else:
            self.help_status_label.setText("help 명령이 실패했습니다. 추가 인자를 직접 입력할 수 있습니다.")

    def _fail_help_options(self, message: str) -> None:
        self._help_process = None
        self.help_status_label.setText(message)
        self._populate_help_options("")

    def _timeout_help_options(self) -> None:
        process = self._help_process
        if process is None:
            return
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                self.help_status_label.setText("help 명령 시간이 초과되었습니다.")
        except RuntimeError:
            self._help_process = None

    def _populate_help_options(self, help_text: str) -> list[CliHelpOption]:
        options = extra_arg_options_from_help(help_text) if help_text.strip() else []
        self.option_combo.blockSignals(True)
        self.option_combo.clear()
        if options:
            for option in options:
                self.option_combo.addItem(self._help_option_label(option), option)
        else:
            self.option_combo.addItem("불러온 옵션 없음", None)
        self.option_combo.blockSignals(False)
        self.option_value_edit.clear()
        self.add_option_button.setEnabled(bool(options))
        self.remove_option_button.setEnabled(bool(options))
        self._handle_help_option_changed()
        return options

    def _reset_help_options(self) -> None:
        self._stop_help_process()
        self._help_loaded_for = ""
        if hasattr(self, "raw_help_edit"):
            self.raw_help_edit.clear()
            self.help_status_label.setText("로그인 확인 후 CLI --help에서 옵션을 불러옵니다.")
            self._populate_help_options("")

    def _handle_help_option_changed(self) -> None:
        option = self._current_help_option()
        if option is None:
            self.option_value_edit.setEnabled(False)
            self.option_value_edit.clear()
            self.option_description.setPlainText("")
            return
        self.option_value_edit.setEnabled(option.takes_value)
        self.option_value_edit.setPlaceholderText(option.value_hint or "값이 필요한 옵션이면 입력")
        if not option.takes_value:
            self.option_value_edit.clear()
        description = option.description or "이 옵션은 CLI help에 별도 설명이 없습니다."
        value_note = f"\n\n값 필요: {option.value_hint}" if option.takes_value else "\n\n값 필요 없음"
        self.option_description.setPlainText(f"{option.flag}{value_note}\n\n{description}")

    def add_selected_extra_arg(self) -> None:
        option = self._current_help_option()
        if option is None:
            return
        value = self.option_value_edit.text().strip()
        if option.takes_value and not value:
            QMessageBox.warning(self, "옵션 값 필요", f"{option.flag} 옵션 값을 입력하세요.")
            return

        tokens = self._split_extra_args()
        self._upsert_extra_arg(tokens, option.flag, value if option.takes_value else "")
        self._set_extra_args_tokens(tokens)
        self.save_current_config()

    def remove_selected_extra_arg(self) -> None:
        option = self._current_help_option()
        if option is None:
            return
        tokens = self._split_extra_args()
        removed = self._remove_extra_arg(tokens, option.flag, option.takes_value)
        if removed:
            self._set_extra_args_tokens(tokens)
            self.save_current_config()

    def _current_help_option(self) -> CliHelpOption | None:
        option = self.option_combo.currentData()
        return option if isinstance(option, CliHelpOption) else None

    def _help_option_label(self, option: CliHelpOption) -> str:
        value = f" {option.value_hint}" if option.value_hint else ""
        return f"{option.flag}{value}"

    def _split_extra_args(self) -> list[str]:
        text = self.extra_args_edit.text().strip()
        if not text:
            return []
        try:
            return shlex.split(text)
        except ValueError:
            return [part for part in text.split() if part.strip()]

    @staticmethod
    def _blocked_direct_extra_args(tokens: list[str]) -> list[str]:
        blocked: list[str] = []
        for token in tokens:
            normalized = token.split("=", 1)[0]
            if normalized in BLOCKED_DIRECT_EXTRA_ARG_FLAGS and normalized not in blocked:
                blocked.append(normalized)
        return blocked

    def _set_extra_args_tokens(self, tokens: list[str]) -> None:
        self.extra_args_edit.setText(subprocess.list2cmdline(tokens))

    def _upsert_extra_arg(self, tokens: list[str], flag: str, value: str) -> None:
        if flag in tokens:
            index = tokens.index(flag)
            if value:
                if index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                    tokens[index + 1] = value
                else:
                    tokens.insert(index + 1, value)
            return
        tokens.append(flag)
        if value:
            tokens.append(value)

    def _remove_extra_arg(self, tokens: list[str], flag: str, takes_value: bool) -> bool:
        if flag not in tokens:
            return False
        index = tokens.index(flag)
        del tokens[index]
        if takes_value and index < len(tokens) and not tokens[index].startswith("-"):
            del tokens[index]
        return True

    def _runtime_provider_config(self, config: AiProviderConfig, attachment_args: list[str]) -> AiProviderConfig:
        return AiProviderConfig(
            key=config.key,
            enabled=config.enabled,
            command_path=config.command_path,
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            speed=config.speed,
            role_prompt=config.role_prompt,
            extra_args=[*config.extra_args, *self._codex_runtime_config_args(config), *attachment_args],
            timeout_seconds=config.timeout_seconds,
        )

    @staticmethod
    def _codex_runtime_config_args(config: AiProviderConfig) -> list[str]:
        if config.key != "codex":
            return []
        args: list[str] = []
        if config.reasoning_effort.strip():
            args.extend(["-c", f'model_reasoning_effort="{config.reasoning_effort.strip()}"'])
        if config.speed.strip():
            args.extend(["-c", f'service_tier="{config.speed.strip()}"'])
        return args

    def _attachment_context_and_args(self, provider_key: str) -> tuple[str, list[str]]:
        sections: list[str] = []
        args: list[str] = []
        used_chars = 0
        for path in self._attachments:
            if not path.exists():
                sections.append(f"첨부 파일을 찾지 못했습니다: {path}")
                continue
            suffix = path.suffix.lower()
            if provider_key == "codex" and suffix in IMAGE_ATTACHMENT_EXTENSIONS:
                args.extend(["--image", str(path)])
                sections.append(f"이미지 첨부: {path} ({self._safe_file_size(path)}). CLI --image 인자로 함께 전달됨.")
                continue
            if suffix in IMAGE_ATTACHMENT_EXTENSIONS:
                sections.append(f"이미지 첨부: {path} ({self._safe_file_size(path)}). 현재 제공자에는 경로만 공유됨.")
                continue

            text, truncated = self._read_attachment_text(path)
            if text is None:
                sections.append(f"첨부 파일: {path} ({self._safe_file_size(path)}). 텍스트로 읽을 수 없어 경로만 공유됨.")
                continue

            remaining = MAX_ATTACHMENT_CONTEXT_CHARS - used_chars
            if remaining <= 0:
                sections.append(f"첨부 파일 생략: {path}. 첨부 컨텍스트 한도를 초과했습니다.")
                continue
            if len(text) > remaining:
                text = text[:remaining]
                truncated = True
            used_chars += len(text)
            note = "\n[첨부 내용 일부만 포함됨]" if truncated else ""
            sections.append(f"첨부 파일: {path}\n--- 첨부 내용 시작 ---\n{text.rstrip()}\n--- 첨부 내용 끝 ---{note}")
        return "\n\n".join(sections).strip(), args

    def _read_attachment_text(self, path: Path) -> tuple[str | None, bool]:
        try:
            with path.open("rb") as file:
                data = file.read(MAX_ATTACHMENT_BYTES + 1)
        except OSError:
            return None, False
        truncated = len(data) > MAX_ATTACHMENT_BYTES
        payload = data[:MAX_ATTACHMENT_BYTES]
        if not self._looks_like_text_attachment(path, payload):
            return None, False
        text = decode_cli_output(payload)
        if self._looks_like_garbled_text(text):
            return None, False
        return text, truncated

    @staticmethod
    def _looks_like_text_attachment(path: Path, data: bytes) -> bool:
        if not data:
            return True
        if any(data.startswith(magic) for magic in BINARY_ATTACHMENT_MAGIC):
            return False
        if b"\0" in data:
            return False
        suffix = path.suffix.lower()
        if suffix in TEXT_ATTACHMENT_EXTENSIONS:
            return True
        text = decode_cli_output(data)
        return not AiChatTab._looks_like_garbled_text(text)

    @staticmethod
    def _looks_like_garbled_text(text: str) -> bool:
        if not text:
            return False
        replacement_count = text.count("\ufffd")
        if replacement_count > max(3, len(text) // 80):
            return True
        control_count = sum(1 for char in text if ord(char) < 32 and char not in "\r\n\t\f\b")
        return control_count > max(8, len(text) // 80)

    def _display_prompt_with_attachments(self, prompt: str, attachments: list[Path]) -> str:
        if not attachments:
            return prompt
        names = "\n".join(f"- {path}" for path in attachments)
        return f"{prompt}\n\n첨부 파일:\n{names}"

    @staticmethod
    def _mask_ip_text(value: str) -> str:
        text = str(value or "").strip()
        if not text or text == "-":
            return "-"
        return re.sub(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}\b", r"\1.x", text)

    def _mask_host_text(self, value: str) -> str:
        text = str(value or "").strip()
        if not text or text == "-":
            return "-"
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", text):
            return self._mask_ip_text(text)
        if "." in text:
            parts = [part for part in text.split(".") if part]
            if len(parts) >= 2:
                return f"{parts[0][:2]}***.{parts[-1]}"
        return f"{text[:2]}***" if len(text) > 2 else "***"

    @staticmethod
    def _mask_remote_path(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "-"
        normalized = text.replace("\\", "/").rstrip("/")
        if normalized in {"", "/", "."}:
            return normalized or "-"
        tail = normalized.rsplit("/", 1)[-1]
        return f".../{tail}" if tail else "..."

    @staticmethod
    def _safe_context_path(path: object) -> str:
        text = str(path or "").strip()
        if not text:
            return "-"
        try:
            raw_path = Path(text)
            home = Path.home()
            try:
                relative = raw_path.resolve().relative_to(home.resolve())
                return str(Path("~") / relative)
            except (OSError, ValueError):
                return str(raw_path)
        except (OSError, ValueError):
            return text

    def _should_collect_internal_network_context(self, prompt: str) -> bool:
        return "network" in self._netops_context_categories(prompt)

    def _should_collect_internal_netops_context(self, prompt: str) -> bool:
        return bool(self._netops_context_categories(prompt))

    def _netops_context_categories(self, prompt: str) -> set[str]:
        normalized = prompt.casefold()
        categories: set[str] = set()
        if any(keyword in normalized for keyword in NETWORK_STATUS_TEXT_KEYWORDS):
            categories.add("network")
        if NETWORK_STATUS_WORD_RE.search(normalized) or NETWORK_STATUS_KOREAN_SHORT_RE.search(normalized):
            categories.add("network")
        if any(keyword in normalized for keyword in INSPECTOR_CONTEXT_KEYWORDS):
            categories.add("inspector")
        if any(keyword in normalized for keyword in CONFIG_BUILDER_CONTEXT_KEYWORDS):
            categories.add("config_builder")
        if any(keyword in normalized for keyword in PROFILE_CONTEXT_KEYWORDS):
            categories.add("profiles")
        if any(keyword in normalized for keyword in TRANSFER_CONTEXT_KEYWORDS):
            categories.add("transfer")
        if any(keyword in normalized for keyword in BASIC_NETOPS_CONTEXT_KEYWORDS):
            categories.add("overview")
        if "템플릿" in normalized and any(keyword in normalized for keyword in ("장비", "점검", "백업", "inspection", "backup")):
            categories.add("inspector")
        if "백업" in normalized and any(keyword in normalized for keyword in ("장비", "템플릿", "인벤토리", "점검")):
            categories.add("inspector")
        if "점검" in normalized and any(keyword in normalized for keyword in ("장비", "템플릿", "인벤토리", "백업")):
            categories.add("inspector")
        if "프로파일" in normalized and any(keyword in normalized for keyword in ("cli", "설정", "스위치", "라우터", "config")):
            categories.add("config_builder")
        if any(keyword in normalized for keyword in ("프로파일", "profile")) and any(
            keyword in normalized for keyword in ("ip", "네트워크", "전송", "ftp", "scp", "cli", "설정", "저장된")
        ):
            categories.add("profiles")
        if any(keyword in normalized for keyword in ("ftp", "scp", "sftp", "tftp")) and any(
            keyword in normalized for keyword in ("전송", "프로파일", "서버", "클라이언트", "업로드", "다운로드")
        ):
            categories.add("transfer")
        if categories:
            categories.add("overview")
        return categories

    def _base_internal_context_sections(self, prompt: str, categories: set[str]) -> list[tuple[str, str]]:
        return [
            (
                "사용 지침",
                "\n".join(
                    [
                        "아래 내용은 NetOps Suite 내부 기능과 저장 상태를 바탕으로 수집한 컨텍스트입니다.",
                        "답변은 이 컨텍스트를 근거로 한국어로 작성하세요.",
                        "NetOps Suite가 이미 제공하는 기능을 우선 활용하도록 안내하고, 민감정보는 요구하거나 노출하지 마세요.",
                        "실패한 항목이 있으면 외부 도구 차단이 아니라 해당 NetOps 내부 수집 항목의 실패로 설명하세요.",
                    ]
                ),
            ),
            ("요청", prompt.strip()),
            ("감지된 NetOps 컨텍스트", ", ".join(sorted(categories)) or "-"),
            ("수집 시각", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ]

    def _collect_internal_netops_context(self, prompt: str) -> str:
        categories = self._netops_context_categories(prompt)
        sections = self._base_internal_context_sections(prompt, categories)
        if "overview" in categories:
            self._collect_app_overview_sections(sections)
        if "profiles" in categories:
            self._collect_saved_profile_sections(sections)
        if "inspector" in categories:
            self._collect_inspector_context_sections(sections)
        if "config_builder" in categories:
            self._collect_config_builder_context_sections(sections)
        if "transfer" in categories:
            self._collect_transfer_context_sections(sections)
        if "network" in categories:
            self._collect_network_context_sections(sections, prompt)
        return self._format_internal_context_sections(sections)

    def _collect_internal_network_context(self, prompt: str) -> str:
        categories = {"network", "overview"}
        sections = self._base_internal_context_sections(prompt, categories)
        self._collect_app_overview_sections(sections)
        self._collect_network_context_sections(sections, prompt)
        return self._format_internal_context_sections(sections)

    def _collect_network_context_sections(self, sections: list[tuple[str, str]], prompt: str) -> None:
        adapters = self._collect_network_adapters_section(sections)
        self._collect_gateway_ping_sections(sections, adapters)
        self._collect_external_connectivity_sections(sections)
        self._collect_dns_section(sections)
        self._collect_public_ip_section(sections)
        self._collect_optional_command_sections(sections, prompt)

    def _collect_app_overview_sections(self, sections: list[tuple[str, str]]) -> None:
        sections.append(
            (
                "NetOps Suite 기능 지도",
                "\n".join(
                    [
                        "- 네트워크 설정: 어댑터 조회, DHCP/수동 IP, DNS, 저장된 IP 프로파일 적용",
                        "- 연결 진단: Ping, TCP 포트 확인, DNS 조회, tracert/pathping, ipconfig/route/ARP/OUI",
                        "- Wi-Fi 분석: 현재 무선 연결과 주변 AP 정보 확인",
                        "- 장비 점검/백업: Excel 인벤토리 기반 장비 접속, 점검 명령, 백업 명령, 사용자 명령 실행",
                        "- CLI 설정 생성: YAML 프로파일과 장비값 CSV/XLSX를 기반으로 장비별 CLI 생성",
                        "- 파일 전송: FTP/SCP/TFTP 클라이언트와 서버 도구",
                    ]
                ),
            )
        )

    def _collect_saved_profile_sections(self, sections: list[tuple[str, str]]) -> None:
        lines: list[str] = []
        ip_profiles = list(getattr(self.state, "ip_profiles", []) or [])
        lines.append(f"IP 프로파일: {len(ip_profiles)}개")
        for profile in ip_profiles[:12]:
            lines.append(
                "- "
                f"{getattr(profile, 'name', '-')}: {getattr(profile, 'mode', '-')}, "
                f"IP {self._mask_ip_text(getattr(profile, 'local_ip', '') or '-')} /{getattr(profile, 'prefix', '-')}, "
                f"GW {self._mask_ip_text(getattr(profile, 'gateway', '') or '-')}, "
                f"DNS {len(getattr(profile, 'dns', []) or [])}개"
            )
        if len(ip_profiles) > 12:
            lines.append(f"... IP 프로파일 {len(ip_profiles) - 12}개 생략")
        sections.append(("저장된 IP 프로파일", "\n".join(lines)))

    def _collect_transfer_context_sections(self, sections: list[tuple[str, str]]) -> None:
        lines: list[str] = []
        ftp_profiles = list(getattr(self.state, "ftp_profiles", []) or [])
        scp_profiles = list(getattr(self.state, "scp_profiles", []) or [])
        lines.append(f"FTP/SFTP 프로파일: {len(ftp_profiles)}개")
        for profile in ftp_profiles[:10]:
            lines.append(
                "- "
                f"{getattr(profile, 'name', '-')}: {getattr(profile, 'protocol', 'ftp')}, "
                f"host={self._mask_host_text(getattr(profile, 'host', '') or '-')}, "
                f"port={getattr(profile, 'port', '-')}, path={self._mask_remote_path(getattr(profile, 'remote_path', '') or '/')}"
            )
        if len(ftp_profiles) > 10:
            lines.append(f"... FTP/SFTP 프로파일 {len(ftp_profiles) - 10}개 생략")
        lines.append(f"SCP 프로파일: {len(scp_profiles)}개")
        for profile in scp_profiles[:10]:
            lines.append(
                "- "
                f"{getattr(profile, 'name', '-')}: "
                f"host={self._mask_host_text(getattr(profile, 'host', '') or '-')}, "
                f"port={getattr(profile, 'port', '-')} "
                f"path={self._mask_remote_path(getattr(profile, 'remote_path', '') or '.')}"
            )
        if len(scp_profiles) > 10:
            lines.append(f"... SCP 프로파일 {len(scp_profiles) - 10}개 생략")
        sections.append(("파일 전송 프로파일", "\n".join(lines)))

    def _collect_inspector_context_sections(self, sections: list[tuple[str, str]]) -> None:
        try:
            data_root = getattr(getattr(self.state, "paths", None), "data_root", Path.cwd())
            service = InspectorService(
                work_dir=Path(data_root) / "inspector" / "runs",
                user_data_dir=Path(data_root) / "inspector",
            )
            templates = service.supported_profile_templates()
            lines = [
                f"지원 템플릿: {len(templates)}개",
                f"사용자 custom_rules.yaml: {self._safe_context_path(service.custom_rules_path)}",
                f"사용자 custom_parsers 폴더: {self._safe_context_path(service.custom_parsers_dir)}",
                "인벤토리 필수 컬럼: ip, vendor, os, connection_type, port, password",
                "선택 컬럼: username, enable_password",
                "실행 모드: inspection, backup, inspection_backup, custom_commands",
                "템플릿 생성 요청 시 custom_rules.yaml 형식으로 inspection_commands, backup_commands, parsing_rules, connection_overrides를 작성하세요.",
            ]
            for template in templates[:16]:
                lines.append(
                    "- "
                    f"{template.get('display_name') or template.get('key')}: "
                    f"commands={template.get('command_count', 0)}, "
                    f"backup={'yes' if template.get('has_backup') else 'no'}, "
                    f"columns={len(template.get('output_columns') or [])}, "
                    f"source={template.get('source', '-')}"
                )
            if len(templates) > 16:
                lines.append(f"... 템플릿 {len(templates) - 16}개 생략")
            sections.append(("장비 점검/백업 컨텍스트", "\n".join(lines)))
        except Exception as exc:
            sections.append(("장비 점검/백업 컨텍스트", f"실패: {exc}"))

    def _collect_config_builder_context_sections(self, sections: list[tuple[str, str]]) -> None:
        try:
            data_root = getattr(getattr(self.state, "paths", None), "data_root", Path.cwd())
            service = ConfigBuilderService(user_data_dir=Path(data_root) / "config_builder")
            summaries = service.profile_summaries()
            lines = [
                f"CLI 설정 생성 프로파일: {len(summaries)}개",
                f"프로파일 폴더: {self._safe_context_path(service.profiles_dir)}",
                f"장비값 샘플 기본 경로: {self._safe_context_path(service.device_values_dir)}",
                "프로파일 생성 요청 시 YAML 프로파일과 장비값 CSV/XLSX 컬럼을 함께 제안하세요.",
                "렌더링 입력은 profile_id와 profile별 변수 컬럼을 가진 장비값 파일입니다.",
            ]
            profiles, _issues = service.load_profiles()
            for summary in summaries[:14]:
                variables = ", ".join(summary.get("variables", [])[:10]) or "-"
                blocks = ", ".join(summary.get("blocks", [])[:8]) or "-"
                sample_path = ""
                try:
                    profile = profiles.get(str(summary.get("id", "")))
                    sample = service.sample_device_values_for_profile(profile) if profile is not None else None
                    sample_path = self._safe_context_path(sample) if sample else ""
                except Exception:
                    sample_path = ""
                lines.append(
                    "- "
                    f"{summary.get('id')}: vendor={summary.get('vendor') or '-'}, "
                    f"model={summary.get('model') or '-'}, variables=[{variables}], "
                    f"blocks=[{blocks}], sample={sample_path or '-'}"
                )
            if len(summaries) > 14:
                lines.append(f"... CLI 설정 프로파일 {len(summaries) - 14}개 생략")
            sections.append(("CLI 설정 생성 컨텍스트", "\n".join(lines)))
        except Exception as exc:
            sections.append(("CLI 설정 생성 컨텍스트", f"실패: {exc}"))

    def _collect_network_adapters_section(self, sections: list[tuple[str, str]]) -> list[Any]:
        adapter_service = getattr(self.state, "network_interface_service", None)
        if adapter_service is None:
            sections.append(("네트워크 어댑터", "실패: NetworkInterfaceService가 준비되지 않았습니다."))
            return []
        try:
            adapters = list(adapter_service.list_adapters())
            formatter = getattr(adapter_service, "format_adapter_snapshot", None)
            if callable(formatter):
                body = formatter(adapters)
            else:
                body = "\n".join(str(adapter) for adapter in adapters)
            sections.append(("네트워크 어댑터", body or "어댑터 정보가 없습니다."))
            return adapters
        except Exception as exc:
            sections.append(("네트워크 어댑터", f"실패: {exc}"))
            return []

    def _collect_gateway_ping_sections(self, sections: list[tuple[str, str]], adapters: list[Any]) -> None:
        ping_service = getattr(self.state, "ping_service", None)
        if ping_service is None:
            sections.append(("게이트웨이 Ping", "실패: PingService가 준비되지 않았습니다."))
            return
        gateways: list[str] = []
        for adapter in adapters:
            gateway = str(getattr(adapter, "gateway", "") or "").strip()
            if gateway and gateway not in {"-", "0.0.0.0"} and gateway not in gateways:
                gateways.append(gateway)
        if not gateways:
            sections.append(("게이트웨이 Ping", "게이트웨이가 설정된 어댑터를 찾지 못했습니다."))
            return
        lines: list[str] = []
        for gateway in gateways[:4]:
            lines.append(self._run_ping_check(ping_service, f"Gateway {gateway}", gateway))
        sections.append(("게이트웨이 Ping", "\n".join(lines)))

    def _collect_external_connectivity_sections(self, sections: list[tuple[str, str]]) -> None:
        ping_service = getattr(self.state, "ping_service", None)
        if ping_service is None:
            sections.append(("외부 연결 Ping", "실패: PingService가 준비되지 않았습니다."))
            return
        lines = [self._run_ping_check(ping_service, label, target) for label, target in BASIC_NETWORK_DIAGNOSTIC_TARGETS]
        sections.append(("외부 연결 Ping", "\n".join(lines)))

    def _collect_dns_section(self, sections: list[tuple[str, str]]) -> None:
        dns_service = getattr(self.state, "dns_service", None)
        if dns_service is None:
            sections.append(("DNS 조회", "실패: DnsService가 준비되지 않았습니다."))
            return
        try:
            result = dns_service.lookup("google.com", "A")
            sections.append(("DNS 조회 google.com A", self._format_operation_result(result)))
        except Exception as exc:
            sections.append(("DNS 조회 google.com A", f"실패: {exc}"))

    def _collect_public_ip_section(self, sections: list[tuple[str, str]]) -> None:
        public_ip_service = getattr(self.state, "public_ip_service", None)
        if public_ip_service is None:
            return
        try:
            result = public_ip_service.check_public_ip(timeout_seconds=3)
            sections.append(("공인 IP", self._format_operation_result(result)))
        except Exception as exc:
            sections.append(("공인 IP", f"실패: {exc}"))

    def _collect_optional_command_sections(self, sections: list[tuple[str, str]], prompt: str) -> None:
        trace_service = getattr(self.state, "trace_service", None)
        if trace_service is None:
            return
        normalized = prompt.casefold()
        try:
            sections.append(("route print", self._format_operation_result(trace_service.run_route_print())))
        except Exception as exc:
            sections.append(("route print", f"실패: {exc}"))
        if any(keyword in normalized for keyword in ("ipconfig", "상세", "전체", "어댑터")):
            try:
                sections.append(("ipconfig /all", self._format_operation_result(trace_service.run_ipconfig_all())))
            except Exception as exc:
                sections.append(("ipconfig /all", f"실패: {exc}"))

    def _run_ping_check(self, ping_service: Any, label: str, target: str) -> str:
        try:
            result = ping_service.quick_ping(target, count=2, timeout_ms=2000)
            return f"- {label}: {self._format_operation_result(result)}"
        except Exception as exc:
            return f"- {label}: 실패: {exc}"

    @staticmethod
    def _format_operation_result(result: Any) -> str:
        success = "성공" if bool(getattr(result, "success", False)) else "실패"
        message = str(getattr(result, "message", "") or "").strip()
        details = str(getattr(result, "details", "") or "").strip()
        parts = [success]
        if message:
            parts.append(message)
        if details:
            parts.append(details)
        return "\n".join(parts)

    def _format_internal_context_sections(self, sections: list[tuple[str, str]]) -> str:
        output: list[str] = ["NetOps Suite internal diagnostics snapshot"]
        total_chars = len(output[0])
        for title, body in sections:
            section = self._truncate_text(str(body or "").strip(), MAX_INTERNAL_CONTEXT_SECTION_CHARS)
            block = f"\n\n[{title}]\n{section or '-'}"
            if total_chars + len(block) > MAX_INTERNAL_CONTEXT_TOTAL_CHARS:
                output.append("\n\n[생략]\n내부 진단 컨텍스트 한도 때문에 이후 항목을 생략했습니다.")
                break
            output.append(block)
            total_chars += len(block)
        return "".join(output).strip()

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        omitted = len(text) - limit
        return f"{text[:limit].rstrip()}\n...[{omitted:,}자 생략]"

    def _safe_file_size(self, path: Path) -> str:
        try:
            return self._format_bytes(path.stat().st_size)
        except OSError:
            return "크기 확인 불가"

    @staticmethod
    def _format_bytes(size: int) -> str:
        units = ("B", "KB", "MB", "GB")
        value = float(max(size, 0))
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)}{unit}"
                return f"{value:.1f}{unit}"
            value /= 1024

    def open_provider_login(self) -> None:
        config = self.current_provider_config()
        health = inspect_provider(config)
        if not health.installed:
            QMessageBox.warning(self, "CLI 없음", f"{PROVIDER_SPECS[config.key].display_name}: {health.detail}")
            return

        invocation = build_login_invocation(config, str(self.state.paths.root))
        preflight_error = self._login_preflight_error(config)
        if preflight_error:
            repair = repair_cli_configuration_error(config.key, preflight_error)
            if repair.repaired:
                self._set_status("CLI 설정 자동 복구", repair.message)
                self._append_block("시스템", repair.message)
                preflight_error = self._login_preflight_error(config)
            if preflight_error:
                message = preflight_error
                if repair.attempted and repair.message and not repair.repaired:
                    message = f"{repair.message}\n\n{preflight_error}"
                elif repair.repaired:
                    message = f"Codex 설정을 자동 복구했지만 CLI 상태 확인이 아직 실패합니다.\n\n{preflight_error}"
                self._set_status("CLI 설정 오류", message)
                self._append_block("오류", message)
                QMessageBox.warning(self, "CLI 설정 오류", message)
                return

        command = subprocess.list2cmdline([invocation.program, *invocation.args])
        if sys.platform == "win32":
            ok = QProcess.startDetached("cmd.exe", ["/k", command], invocation.working_dir)
        else:
            ok = QProcess.startDetached(invocation.program, invocation.args, invocation.working_dir)
        if not ok:
            QMessageBox.warning(self, "로그인 실행 실패", "로그인 터미널을 열지 못했습니다.")
            return
        self._append_block("시스템", f"로그인 터미널을 열었습니다.\n{command}")

    def _login_preflight_error(self, config: AiProviderConfig) -> str:
        invocation = build_status_invocation(config, str(self.state.paths.root))
        run_kwargs: dict[str, Any] = {
            "cwd": invocation.working_dir or str(self.state.paths.root),
            "capture_output": True,
            "timeout": min(invocation.timeout_seconds, 8),
            "env": safe_env_for_cli(),
        }
        if sys.platform == "win32":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            completed = subprocess.run([invocation.program, *invocation.args], **run_kwargs)
        except (OSError, subprocess.SubprocessError):
            return ""

        detail = "\n".join(
            part.strip()
            for part in (decode_cli_output(completed.stdout), decode_cli_output(completed.stderr))
            if isinstance(part, str) and part.strip()
        )
        if is_blocking_cli_configuration_error(config.key, detail):
            return diagnose_cli_error(config.key, detail)
        return ""

    def send_prompt(self) -> None:
        if self._process is not None:
            QMessageBox.information(self, "실행 중", "현재 요청이 아직 실행 중입니다.")
            return
        if self._context_collecting:
            QMessageBox.information(self, "준비 중", "NetOps 내부 진단 결과를 수집하는 중입니다.")
            return
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt:
            QMessageBox.warning(self, "요청 필요", "먼저 요청을 입력하세요.")
            return

        config = self.current_provider_config()
        blocked_args = self._blocked_direct_extra_args(config.extra_args)
        if blocked_args:
            QMessageBox.warning(
                self,
                "추가 인자 확인",
                "앱이 관리하는 옵션이거나 위험한 옵션은 직접 추가 인자로 보낼 수 없습니다.\n\n"
                + ", ".join(blocked_args),
            )
            return
        health = inspect_provider(config)
        if not health.installed:
            QMessageBox.warning(self, "CLI 없음", health.detail)
            self._set_status("CLI 없음", health.detail)
            return

        try:
            attachment_context, attachment_args = self._attachment_context_and_args(config.key)
        except ValueError as exc:
            QMessageBox.warning(self, "요청 실행 불가", str(exc))
            return

        self.save_current_config()
        sent_attachments = list(self._attachments)
        payload = {
            "prompt": prompt,
            "config": config,
            "attachment_context": attachment_context,
            "attachment_args": attachment_args,
            "sent_attachments": sent_attachments,
            "estimated_attachment_chars": self._estimated_attachment_context_chars(),
        }
        if self._should_collect_internal_netops_context(prompt):
            self._pending_prompt_payload = payload
            self._context_collection_cancelled = False
            self._set_preparing(True)
            self._set_status("내부 컨텍스트 수집 중", "NetOps Suite 기능 정보를 먼저 수집합니다.")
            self._append_block("시스템", "NetOps Suite 내부 기능 정보를 수집한 뒤 AI에게 전달합니다.")
            self._job_runner.start(
                self._collect_internal_netops_context,
                prompt,
                on_result=self._continue_prompt_with_internal_context,
                on_error=self._handle_internal_context_error,
                on_finished=self._finish_internal_context_collection,
            )
            return

        self._start_prompt_process(payload, "")

    def _continue_prompt_with_internal_context(self, internal_context: object) -> None:
        if self._context_collection_cancelled:
            self._pending_prompt_payload = None
            return
        payload = self._pending_prompt_payload
        self._pending_prompt_payload = None
        if payload is None:
            return
        context_text = str(internal_context or "").strip()
        if context_text:
            self._append_block("시스템", "NetOps Suite 내부 컨텍스트를 AI 요청에 포함했습니다.")
        self._start_prompt_process(payload, context_text)

    def _handle_internal_context_error(self, message: str) -> None:
        if self._context_collection_cancelled:
            self._pending_prompt_payload = None
            self._set_preparing(False)
            return
        payload = self._pending_prompt_payload
        self._pending_prompt_payload = None
        self._set_preparing(False)
        if payload is None:
            return
        detail = f"NetOps Suite 내부 진단 수집 중 오류가 발생했습니다: {message}"
        self._append_block("오류", detail)
        self._start_prompt_process(payload, detail)

    def _finish_internal_context_collection(self) -> None:
        self._context_collecting = False
        if self._process is None:
            self._set_preparing(False)

    def _start_prompt_process(self, payload: dict[str, Any], internal_context: str) -> None:
        prompt = str(payload["prompt"])
        config = payload["config"]
        attachment_context = str(payload.get("attachment_context", "") or "")
        attachment_args = list(payload.get("attachment_args", []))
        sent_attachments = list(payload.get("sent_attachments", []))
        estimated_attachment_chars = int(payload.get("estimated_attachment_chars", 0) or 0)
        combined_context = "\n\n".join(part for part in (attachment_context, internal_context.strip()) if part)

        try:
            runtime_config = self._runtime_provider_config(config, attachment_args)
            invocation = build_chat_invocation(
                runtime_config,
                prompt,
                context=combined_context,
                working_dir=str(self.state.paths.root),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "요청 실행 불가", str(exc))
            return

        self._stdout_buffer = b""
        self._stderr_text = ""
        self._stream_message_index = None
        self._active_context_status_text = self._context_status_text(
            "실행 중",
            len(prompt) + len(internal_context),
            estimated_attachment_chars,
            len(sent_attachments),
        )
        self._append_block("사용자", self._display_prompt_with_attachments(prompt, sent_attachments))
        self.prompt_edit.clear()
        self.prompt_edit.setEnabled(True)
        if sent_attachments:
            self._attachments.clear()
            self._refresh_attachment_view()
        else:
            self._update_context_status()
        self._set_running(True)
        self._set_status("실행 중", invocation.program)

        process = QProcess(self)
        self._process = process
        process.setProperty("provider_key", config.key)
        process.setProgram(invocation.program)
        process.setArguments(invocation.args)
        process.setWorkingDirectory(invocation.working_dir or str(self.state.paths.root))
        process.setProcessEnvironment(self._process_environment())
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.finished.connect(lambda exit_code, _status: self._finish_prompt(exit_code))
        process.errorOccurred.connect(lambda _error: self._fail_prompt("CLI 프로세스를 시작하지 못했습니다."))
        process.start()
        if invocation.stdin_text:
            process.write(invocation.stdin_text.encode("utf-8"))
            process.closeWriteChannel()
        QTimer.singleShot(invocation.timeout_seconds * 1000, self._timeout_prompt)

    def _read_stdout(self) -> None:
        if self._process is None:
            return
        self._stdout_buffer += bytes(self._process.readAllStandardOutput())
        while b"\n" in self._stdout_buffer:
            line, rest = self._stdout_buffer.split(b"\n", 1)
            self._stdout_buffer = rest
            decoded_line = decode_cli_output(line.rstrip(b"\r"))
            if should_ignore_cli_output_text(decoded_line):
                continue
            chunk = extract_text_from_cli_line(decoded_line)
            if chunk and not should_ignore_cli_output_text(chunk):
                self._append_stream(chunk + "\n")

    def _read_stderr(self) -> None:
        if self._process is None:
            return
        self._stderr_text += decode_cli_output(bytes(self._process.readAllStandardError()))

    def _finish_prompt(self, exit_code: int) -> None:
        process = self._process
        provider_key = self.current_provider_key()
        self._process = None
        if process is not None:
            provider_key = str(process.property("provider_key") or provider_key)
            process.deleteLater()
        tail_text = decode_cli_output(self._stdout_buffer.strip())
        tail = extract_text_from_cli_line(tail_text)
        if tail and not should_ignore_cli_output_text(tail):
            self._append_stream(tail + "\n")
        if exit_code == 0:
            self._set_status("사용 가능", "요청 완료")
        else:
            detail = self._stderr_text.strip() or f"CLI가 종료 코드 {exit_code}로 끝났습니다."
            repair = repair_cli_configuration_error(provider_key, detail)
            if repair.repaired:
                detail = f"{repair.message}\n\n설정을 자동 복구했습니다. 요청을 다시 보내세요."
            else:
                detail = diagnose_cli_error(provider_key, detail)
                if repair.attempted and repair.message:
                    detail = f"{repair.message}\n\n{detail}"
            self._append_block("오류", detail)
            status_text = (
                "CLI 설정 자동 복구"
                if repair.repaired
                else "CLI 설정 오류"
                if is_blocking_cli_configuration_error(provider_key, detail)
                else "실패"
            )
            self._set_status(status_text, detail[:500])
        self._stream_message_index = None
        self._set_running(False)
        self._active_context_status_text = ""
        self._update_context_status()

    def _fail_prompt(self, message: str) -> None:
        self._append_block("오류", message)
        self._set_status("실패", message)
        self._process = None
        self._stream_message_index = None
        self._set_running(False)
        self._active_context_status_text = ""
        self._update_context_status()

    def _timeout_prompt(self) -> None:
        if self._process is None:
            return
        try:
            if self._process.state() != QProcess.ProcessState.NotRunning:
                self._process.kill()
                self._append_block("시스템", "요청 시간이 초과되어 중지했습니다.")
        except RuntimeError:
            self._process = None

    def cancel_prompt(self) -> None:
        if self._context_collecting:
            self._context_collection_cancelled = True
            self._pending_prompt_payload = None
            self._set_preparing(False)
            self._append_block("시스템", "내부 진단 수집 결과를 무시하고 요청을 중지했습니다.")
            self._set_status("중지됨")
            return
        if self._process is None:
            return
        try:
            self._process.kill()
        except RuntimeError:
            pass
        self._process = None
        self._append_block("시스템", "사용자가 요청을 중지했습니다.")
        self._set_status("중지됨")
        self._set_running(False)
        self._active_context_status_text = ""
        self._update_context_status()

    def export_session(self) -> Path | None:
        text = self._plain_transcript_text().strip()
        if not text:
            QMessageBox.information(self, "내보낼 내용 없음", "내보낼 대화 내용이 없습니다.")
            return None
        path = timestamped_export_path(self.state.paths.exports_dir, "ai_chat_session", "md")
        path.write_text("# AI 채팅 세션\n\n" + text + "\n", encoding="utf-8")
        QMessageBox.information(self, "내보내기 완료", f"대화 내용을 저장했습니다:\n{path}")
        return path

    def clear_transcript(self) -> None:
        if self._process is not None:
            QMessageBox.information(self, "실행 중", "실행 중인 요청을 중지한 뒤 비우세요.")
            return
        self._messages.clear()
        self._stream_message_index = None
        self._render_transcript()

    def shutdown(self) -> None:
        self._stop_status_process()
        self._stop_help_process()
        if self._process is not None:
            try:
                self._process.blockSignals(True)
                if self._process.state() != QProcess.ProcessState.NotRunning:
                    self._process.kill()
            except RuntimeError:
                pass
            self._process = None

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "message_layout") or not hasattr(self, "ai_chat_tabs"):
            return
        if self.ai_chat_tabs.currentWidget() is not self.chat_page:
            self._render_deferred = True
            return
        width = self._transcript_container_width()
        if abs(width - self._last_render_width) > 24:
            self._schedule_transcript_render()

    def _handle_ai_chat_tab_changed(self, _index: int) -> None:
        if self.ai_chat_tabs.currentWidget() is self.chat_page and self._render_deferred:
            self._render_deferred = False
            self._render_transcript()

    def _stop_status_process(self) -> None:
        if self._status_process is None:
            return
        try:
            self._status_process.blockSignals(True)
            if self._status_process.state() != QProcess.ProcessState.NotRunning:
                self._status_process.kill()
        except RuntimeError:
            pass
        self._status_process = None

    def _stop_help_process(self) -> None:
        if self._help_process is None:
            return
        try:
            self._help_process.blockSignals(True)
            if self._help_process.state() != QProcess.ProcessState.NotRunning:
                self._help_process.kill()
        except RuntimeError:
            pass
        self._help_process = None

    def _set_status(self, text: str, tooltip: str = "") -> None:
        timestamp = self._time_text()
        self.status_label.setText(f"{text} · {timestamp}")
        if tooltip:
            self.status_label.setToolTip(f"{tooltip}\n마지막 업데이트: {timestamp}")
        else:
            self.status_label.setToolTip(f"마지막 업데이트: {timestamp}")

    def _set_running(self, running: bool) -> None:
        self.send_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.check_button.setEnabled(not running)
        self.login_button.setEnabled(not running)
        self.save_button.setEnabled(not running)
        if hasattr(self, "attach_button"):
            self.attach_button.setEnabled(not running)
            self.attachment_list.setEnabled(not running)
            if running:
                self.remove_attachment_button.setEnabled(False)
                self.clear_attachments_button.setEnabled(False)
            else:
                self._update_attachment_buttons()

    def _set_preparing(self, preparing: bool) -> None:
        self._context_collecting = preparing
        self.send_button.setEnabled(not preparing)
        self.stop_button.setEnabled(preparing)
        self.check_button.setEnabled(not preparing)
        self.login_button.setEnabled(not preparing)
        self.save_button.setEnabled(not preparing)
        self.prompt_edit.setEnabled(not preparing)
        if hasattr(self, "attach_button"):
            self.attach_button.setEnabled(not preparing)
            self.attachment_list.setEnabled(not preparing)
            if preparing:
                self.remove_attachment_button.setEnabled(False)
                self.clear_attachments_button.setEnabled(False)
            else:
                self._update_attachment_buttons()

    def _append_block(self, title: str, body: str) -> None:
        text = body.strip()
        if not text:
            return
        self._stream_message_index = None
        self._messages.append({"title": title, "body": text, "time": self._time_text()})
        self._request_transcript_render(immediate=True)

    def _append_stream(self, chunk: str) -> None:
        if not chunk:
            return
        if (
            self._stream_message_index is None
            or self._stream_message_index >= len(self._messages)
            or self._messages[self._stream_message_index].get("title") != "AI"
        ):
            self._messages.append({"title": "AI", "body": "", "time": self._time_text()})
            self._stream_message_index = len(self._messages) - 1
        self._messages[self._stream_message_index]["body"] += chunk
        self._request_transcript_render(immediate=False)

    def _plain_transcript_text(self) -> str:
        return "\n\n".join(
            f"[{message.get('time', '')}] {message.get('title', '')}\n{message.get('body', '').strip()}"
            for message in self._messages
            if message.get("body", "").strip()
        )

    def _render_transcript(self) -> None:
        if hasattr(self, "_render_timer") and self._render_timer.isActive():
            self._render_timer.stop()
        container_width = self._transcript_container_width()
        self._last_render_width = container_width
        self.message_container.setMinimumWidth(container_width)
        self._clear_message_widgets()
        if not self._messages:
            placeholder = QLabel("아직 대화가 없습니다. 제공자와 모델을 선택한 뒤 요청을 보내세요.")
            placeholder.setWordWrap(True)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(
                """
                QLabel {
                    color: #667085;
                    background: #ffffff;
                    border: 1px dashed #cbd5e1;
                    border-radius: 6px;
                    padding: 18px;
                }
                """
            )
            self.message_layout.addWidget(placeholder)
            self.message_layout.addStretch(1)
            self._scroll_transcript()
            return

        for message in self._messages:
            body = message.get("body", "")
            if not body.strip():
                continue
            title = message.get("title", "")
            timestamp = message.get("time", "")
            kind = self._message_kind(title)
            self._add_message_widget(title, timestamp, body, kind)
        self.message_layout.addStretch(1)
        self._scroll_transcript()

    def _clear_message_widgets(self) -> None:
        while self.message_layout.count():
            item = self.message_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _schedule_transcript_render(self) -> None:
        if not hasattr(self, "_render_timer"):
            return
        if not self._is_chat_tab_current():
            self._render_deferred = True
            return
        self._render_timer.start()

    def _flush_transcript_render(self) -> None:
        if not hasattr(self, "message_layout"):
            return
        if not self._is_chat_tab_current():
            self._render_deferred = True
            return
        self._render_transcript()

    def _request_transcript_render(self, *, immediate: bool) -> None:
        if not self._is_chat_tab_current():
            self._render_deferred = True
            return
        if immediate:
            self._render_transcript()
        else:
            self._schedule_transcript_render()

    def _is_chat_tab_current(self) -> bool:
        return hasattr(self, "ai_chat_tabs") and self.ai_chat_tabs.currentWidget() is self.chat_page

    def _transcript_container_width(self) -> int:
        return max(self.transcript_scroll.viewport().width() - 2, self.width() - 40, 480)

    def _add_message_widget(self, title: str, timestamp: str, body: str, kind: str) -> None:
        background, border, text_color, align = self._message_styles(kind)

        available_width = max(self.transcript_scroll.viewport().width(), self.width() - 80, 480)
        width_ratio = 0.62 if align == "center" else 0.74
        bubble_width = min(780, max(240, int(available_width * width_ratio)))

        bubble = QFrame()
        bubble.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        bubble.setFixedWidth(bubble_width)
        bubble.setStyleSheet(
            f"""
            QFrame {{
                background: {background};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QLabel {{
                border: none;
                background: transparent;
            }}
            """
        )
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(10, 8, 10, 10)
        bubble_layout.setSpacing(4)

        meta = QLabel(f"{title} · {timestamp}")
        meta.setStyleSheet("color: #667085; font-size: 11px;")

        text = QLabel(body)
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setWordWrap(True)
        text.setFixedWidth(max(160, bubble_width - 22))
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.setStyleSheet(f"color: {text_color}; font-size: 13px; line-height: 145%;")

        bubble_layout.addWidget(meta)
        bubble_layout.addWidget(text)

        if align == "right":
            qt_align = Qt.AlignmentFlag.AlignRight
        elif align == "center":
            qt_align = Qt.AlignmentFlag.AlignHCenter
        else:
            qt_align = Qt.AlignmentFlag.AlignLeft
        self.message_layout.addWidget(bubble, 0, qt_align)

    @staticmethod
    def _message_kind(title: str) -> str:
        if title == "사용자":
            return "user"
        if title == "오류":
            return "error"
        if title == "시스템":
            return "system"
        return "assistant"

    @staticmethod
    def _message_styles(kind: str) -> tuple[str, str, str, str]:
        if kind == "user":
            return "#e8f2ff", "#b8d8ff", "#172033", "right"
        if kind == "error":
            return "#fff1f0", "#ffccc7", "#7a271a", "left"
        if kind == "system":
            return "#eef2f6", "#d0d5dd", "#344054", "center"
        return "#ffffff", "#d6deeb", "#172033", "left"

    def _scroll_transcript(self) -> None:
        QTimer.singleShot(0, self._scroll_transcript_now)

    def _scroll_transcript_now(self) -> None:
        try:
            scrollbar = self.transcript_scroll.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        except RuntimeError:
            return

    @staticmethod
    def _time_text() -> str:
        return QDateTime.currentDateTime().toString("HH:mm:ss")

    def _process_environment(self) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        env.insert("NO_COLOR", "1")
        env.insert("CLICOLOR", "0")
        return env

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
