from __future__ import annotations

import math
import re
import shlex
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any, Callable

from PySide6.QtCore import (
    QDateTime,
    QDir,
    QPoint,
    QProcess,
    QProcessEnvironment,
    QSize,
    QSizeF,
    QTemporaryFile,
    QThreadPool,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QDesktopServices,
    QFontMetrics,
    QIcon,
    QImage,
    QPixmap,
    QTextCursor,
    QTextDocument,
    QTextLength,
    QTextOption,
    QTextTable,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from app.app_state import AppState
from app.assistant import (
    AuditLogger,
    PolicyContext,
    PolicyDecision,
    ToolExecutor,
    ToolResult,
    build_netops_tool_registry,
    tool_call_from_netops_action,
)
from app.models.ai_models import (
    AiModelCatalog,
    AiModelDescriptor,
    AiProviderConfig,
    KNOWN_AI_PROVIDERS,
    normalize_ai_chat_config,
)
from app.services.ai_agent_service import (
    CliHelpOption,
    NetOpsChatAction,
    PROVIDER_SPECS,
    build_chat_invocation,
    build_help_invocation,
    build_login_invocation,
    build_status_invocation,
    decode_cli_output,
    diagnose_cli_error,
    extract_assistant_text_from_cli_line,
    extract_cli_session_id,
    extract_error_from_cli_line,
    extra_arg_options_from_help,
    inspect_provider,
    is_blocking_cli_configuration_error,
    plan_netops_chat_action,
    provider_configs_from_app_config,
    repair_cli_configuration_error,
    safe_env_for_cli,
    should_ignore_cli_output_text,
    split_codex_model_cache_warning,
)
from app.services.ai_model_catalog_service import AiModelCatalogService
from app.ui.common import JobRunner, make_step_hint
from app.utils.file_utils import timestamped_export_path
from netops_suite.modules.config_builder import ConfigBuilderService
from netops_suite.modules.inspector import InspectorService
from netops_suite.ui.actions import ActionKind, make_action_button
from netops_suite.ui.selection_inputs import NoWheelComboBox


IMAGE_ATTACHMENT_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
}
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
CUSTOM_MODEL_ID_RE = re.compile(r"[A-Za-z0-9._:/-]{1,128}\Z")
MODEL_DESCRIPTOR_ROLE = int(Qt.ItemDataRole.UserRole) + 1
REASONING_EFFORT_LABELS = {
    "none": "없음 (추론 사용 안 함)",
    "minimal": "최소 (가장 빠른 추론)",
    "low": "낮음 (빠른 단순 작업)",
    "medium": "보통 (일반 작업)",
    "high": "높음 (복잡한 분석)",
    "xhigh": "매우 높음 (가장 깊은 분석)",
    "max": "최대 (장시간 심층 추론)",
    "ultra": "울트라 (다중 에이전트 병렬 작업)",
}
REASONING_EFFORT_ORDER = tuple(REASONING_EFFORT_LABELS)
SPEED_LABELS = {
    "fast": "빠른 응답",
    "flex": "유연 처리 (기존 설정)",
}
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
    "대상 장비 목록",
    "장비 목록",
    # 기존 사용자 표현과 저장된 대화의 라우팅 호환성을 유지합니다.
    "인벤토리",
    "inspection",
    "custom_rules",
    "커스텀 룰",
    "점검 프로파일",
    "백업 프로파일",
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
BASIC_NETWORK_DIAGNOSTIC_TARGETS = (
    ("Google DNS", "8.8.8.8"),
    ("Cloudflare DNS", "1.1.1.1"),
)
BLOCKED_DIRECT_EXTRA_ARG_FLAGS = {
    "-a",
    "-s",
    "-C",
    "-h",
    "-r",
    "--help",
    "-V",
    "--version",
    "--ask-for-approval",
    "--sandbox",
    "--cd",
    "--add-dir",
    "-m",
    "--model",
    "-i",
    "--image",
    "--output-format",
    "--json",
    "--verbose",
    "--continue",
    "--resume",
    "--last",
    "--session-id",
    "--fork-session",
    "--full-auto",
    "--skip-git-repo-check",
    "--dangerously-bypass-approvals-and-sandbox",
}
CODEX_PERMISSION_MODES = (
    "read-only",
    "workspace-write",
    "danger-full-access",
)
CODEX_PERMISSION_TITLES = {
    "read-only": "읽기 전용",
    "workspace-write": "작업공간 액세스",
    "danger-full-access": "전체 권한",
}
CODEX_PERMISSION_BUTTON_TITLES = {
    **CODEX_PERMISSION_TITLES,
    "danger-full-access": "전체 액세스",
}
CODEX_PERMISSION_DESCRIPTIONS = {
    "read-only": "파일을 읽을 수 있지만 변경하지는 않습니다.",
    "workspace-write": (
        "설정·로그·결과·사용자 프로파일 폴더 안의 파일을 변경할 수 있습니다."
    ),
    "danger-full-access": (
        "인터넷과 컴퓨터의 모든 파일에 제한 없이 액세스할 수 있습니다."
    ),
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
CLI_OPTION_KOREAN_HELP: dict[str, tuple[str, str, str]] = {
    "--profile": (
        "설정 프로파일",
        "선택한 CLI의 저장된 설정 프로파일을 사용합니다. 회사/개인/프로젝트별로 분리해 둔 CLI 설정이 있을 때만 지정하세요.",
        "예: work",
    ),
    "--sandbox": (
        "샌드박스 범위",
        "AI CLI가 파일 시스템과 명령 실행에 접근할 수 있는 범위를 정합니다. NetOps Suite에서는 기본적으로 읽기 전용 실행을 권장합니다.",
        "예: read-only",
    ),
    "--search": (
        "웹 검색 사용",
        "CLI가 지원하는 웹 검색 기능을 켭니다. 최신 문서나 외부 정보를 확인해야 할 때만 사용하세요.",
        "",
    ),
    "--cwd": (
        "작업 폴더",
        "CLI 요청을 실행할 기준 폴더를 지정합니다. 지정하지 않으면 NetOps Suite 작업 폴더를 사용합니다.",
        r"예: C:\work\repo",
    ),
    "--working-directory": (
        "작업 폴더",
        "CLI 요청을 실행할 기준 폴더를 지정합니다. 지정하지 않으면 NetOps Suite 작업 폴더를 사용합니다.",
        r"예: C:\work\repo",
    ),
    "--add-dir": (
        "추가 폴더 허용",
        "기본 작업 폴더 외에 CLI가 읽을 수 있는 폴더를 추가합니다. 민감한 파일이 있는 폴더는 추가하지 마세요.",
        r"예: C:\work\shared",
    ),
    "--include": (
        "추가 포함 대상",
        "CLI 요청에 포함할 추가 경로나 패턴을 지정합니다. 필요한 파일만 좁게 지정하는 것이 좋습니다.",
        "예: README.md",
    ),
    "--config": (
        "설정값 지정",
        "CLI 설정값을 명령줄에서 직접 덮어씁니다. 값의 의미를 정확히 아는 경우에만 사용하세요.",
        "예: key=value",
    ),
    "--mode": (
        "실행 모드",
        "CLI의 실행 방식을 지정합니다. 채팅/일회성 실행/자동 실행처럼 CLI마다 의미가 다를 수 있습니다.",
        "",
    ),
    "--permission-mode": (
        "권한 모드",
        "CLI가 도구 사용이나 파일 접근을 승인받는 방식을 정합니다. 쓰기나 명령 실행 권한을 넓히면 위험할 수 있습니다.",
        "",
    ),
    "--allowedTools": (
        "허용 도구",
        "CLI가 사용할 수 있는 도구 목록을 제한합니다. 필요한 도구만 허용하는 방식으로 사용하세요.",
        "예: Read,Grep",
    ),
    "--allowed-tools": (
        "허용 도구",
        "CLI가 사용할 수 있는 도구 목록을 제한합니다. 필요한 도구만 허용하는 방식으로 사용하세요.",
        "예: Read,Grep",
    ),
    "--disallowedTools": (
        "차단 도구",
        "CLI가 사용하면 안 되는 도구 목록을 지정합니다. 쓰기, 삭제, 셸 실행 관련 도구를 막을 때 유용합니다.",
        "예: Bash,Write",
    ),
    "--disallowed-tools": (
        "차단 도구",
        "CLI가 사용하면 안 되는 도구 목록을 지정합니다. 쓰기, 삭제, 셸 실행 관련 도구를 막을 때 유용합니다.",
        "예: Bash,Write",
    ),
    "--yolo": (
        "승인 생략 모드",
        "승인 절차를 줄이거나 생략할 수 있는 위험 옵션입니다. NetOps Suite에서는 권장하지 않습니다.",
        "",
    ),
    "--telemetry": (
        "사용량 정보 전송",
        "CLI의 사용량/진단 정보 전송 여부를 설정합니다. 조직 보안 정책에 맞춰 사용하세요.",
        "",
    ),
    "--cd": (
        "작업 폴더",
        "요청을 실행할 기준 폴더를 지정합니다. 지정하지 않으면 NetOps Suite 작업 폴더를 사용합니다.",
        r"예: C:\work\repo",
    ),
    "--include-directories": (
        "추가 폴더 허용",
        "기본 작업 폴더 외에 CLI가 읽을 수 있는 폴더를 추가합니다. 필요한 폴더만 좁게 지정하세요.",
        r"예: C:\work\shared",
    ),
    "--enable": (
        "기능 켜기",
        "CLI의 선택 기능을 켭니다. 기능 이름과 영향을 CLI 문서에서 확인한 뒤 사용하세요.",
        "예: web_search",
    ),
    "--disable": (
        "기능 끄기",
        "CLI의 선택 기능을 끕니다. 기존 작업 흐름에 필요한 기능인지 확인한 뒤 사용하세요.",
        "예: web_search",
    ),
    "--full-auto": (
        "자동 실행 모드",
        "CLI가 일부 작업을 자동으로 진행하도록 합니다. NetOps Suite의 승인 정책과 충돌할 수 있어 사용을 권장하지 않습니다.",
        "",
    ),
    "--ephemeral": (
        "대화 기록 저장 안 함",
        "이번 CLI 실행의 세션 기록을 저장하지 않습니다. 재개가 필요 없는 일회성 작업에 사용하세요.",
        "",
    ),
    "--skip-git-repo-check": (
        "Git 저장소 확인 생략",
        "현재 폴더가 Git 저장소인지 확인하는 절차를 생략합니다. 일반 폴더에서 작업할 때만 사용하세요.",
        "",
    ),
    "--output-schema": (
        "출력 형식 정의",
        "응답 구조를 정의한 JSON Schema 파일을 지정합니다. 자동 처리할 결과가 필요할 때 사용하세요.",
        "예: result.schema.json",
    ),
    "--output-last-message": (
        "마지막 답변 파일 저장",
        "CLI의 마지막 답변을 지정한 파일에 저장합니다. 기존 파일을 덮어쓸 수 있으므로 경로를 확인하세요.",
        "예: result.txt",
    ),
    "--color": (
        "터미널 색상 출력",
        "CLI 출력에 터미널 색상 코드를 사용할지 정합니다. 로그 파일로 저장할 때는 끄는 편이 읽기 쉽습니다.",
        "예: never",
    ),
    "--oss": (
        "로컬 오픈소스 모델 사용",
        "클라우드 모델 대신 로컬 오픈소스 모델 연결을 사용합니다. 로컬 모델 서버가 준비된 경우에만 선택하세요.",
        "",
    ),
    "--local-provider": (
        "로컬 모델 제공자",
        "연결할 로컬 모델 실행 환경을 지정합니다. 해당 프로그램이 설치되고 실행 중이어야 합니다.",
        "예: ollama",
    ),
    "--continue": (
        "최근 대화 이어가기",
        "가장 최근 CLI 대화를 이어서 사용합니다. 이전 대화의 내용이 현재 요청에 섞일 수 있습니다.",
        "",
    ),
    "--resume": (
        "저장된 대화 이어가기",
        "선택한 CLI 대화 세션을 이어서 사용합니다. 올바른 세션을 선택했는지 확인하세요.",
        "예: 세션 ID",
    ),
    "--system-prompt": (
        "기본 지시문 교체",
        "CLI의 기본 시스템 지시문을 교체합니다. 답변 동작이 크게 달라질 수 있는 전문가용 설정입니다.",
        "예: 지시문 또는 파일 경로",
    ),
    "--append-system-prompt": (
        "기본 지시문에 추가",
        "CLI의 기본 시스템 지시문 뒤에 추가 지시를 붙입니다. 기존 안전 지침과 충돌하지 않게 작성하세요.",
        "예: 추가 지시문",
    ),
    "--max-turns": (
        "최대 작업 횟수",
        "AI가 도구를 사용하며 반복할 수 있는 최대 횟수를 제한합니다. 과도한 실행을 막을 때 사용하세요.",
        "예: 10",
    ),
    "--fallback-model": (
        "대체 모델",
        "기본 모델을 사용할 수 없을 때 대신 사용할 모델을 지정합니다.",
        "예: sonnet",
    ),
    "--mcp-config": (
        "MCP 연결 설정",
        "CLI가 사용할 MCP 서버 설정 파일을 지정합니다. 신뢰할 수 있는 설정 파일만 사용하세요.",
        "예: mcp.json",
    ),
    "--proxy": (
        "프록시 서버",
        "CLI의 외부 연결에 사용할 프록시 주소를 지정합니다. 조직 네트워크 정책에 맞는 주소만 사용하세요.",
        "예: http://127.0.0.1:8080",
    ),
    "--screen-reader": (
        "화면 읽기 지원",
        "화면 읽기 프로그램에 맞춘 접근성 출력 모드를 사용합니다.",
        "",
    ),
}
CLI_VALUE_HINT_KOREAN: dict[str, str] = {
    "<CONFIG_PROFILE>": "설정 프로파일 이름",
    "<PROFILE>": "프로파일 이름",
    "<MODEL>": "모델 이름",
    "<PATH>": "파일 또는 폴더 경로",
    "<DIR>": "폴더 경로",
    "<DIRECTORY>": "폴더 경로",
    "<CONFIG>": "설정값",
    "<MODE>": "모드",
    "<TOOLS>": "도구 목록",
    "<FEATURE>": "기능 이름",
    "<SESSION_ID>": "세션 ID",
    "<FILE>": "파일 경로",
    "<URL>": "주소",
    "<NUMBER>": "숫자",
}


class PromptTextEdit(QPlainTextEdit):
    sendRequested = Signal()
    filesAttached = Signal(list)
    imagePasted = Signal(object)

    MIN_HEIGHT = 44
    MAX_HEIGHT = 132

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiChatMessageBody")
        self._stored_placeholder = ""
        self._placeholder_label = QLabel(self.viewport())
        self._placeholder_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self._placeholder_label.setStyleSheet(
            "color: #98a2b3; background: transparent; padding: 0;"
        )
        self._placeholder_label.setWordWrap(True)
        self.setAcceptDrops(True)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setMaximumHeight(self.MAX_HEIGHT)
        self.setFixedHeight(self.MIN_HEIGHT)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.textChanged.connect(self._sync_placeholder)
        self.textChanged.connect(self._adjust_height)
        self.cursorPositionChanged.connect(self._sync_placeholder)

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._stored_placeholder = str(text or "")
        self._placeholder_label.setText(self._stored_placeholder)
        super().setPlaceholderText("")
        self._layout_placeholder()
        self._sync_placeholder()

    def placeholderText(self) -> str:  # noqa: N802 - Qt API
        return self._stored_placeholder

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_placeholder()
        self._adjust_height()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            event.accept()
            if self.toPlainText().strip():
                self.sendRequested.emit()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - Qt API
        paths = self._paths_from_mime_data(source)
        if paths:
            self.filesAttached.emit(paths)
            return
        if source.hasImage():
            self.imagePasted.emit(source.imageData())
            return
        super().insertFromMimeData(source)

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802 - Qt API
        return (
            bool(self._paths_from_mime_data(source))
            or source.hasImage()
            or super().canInsertFromMimeData(source)
        )

    def dragEnterEvent(self, event) -> None:
        if self._paths_from_mime_data(event.mimeData()) or event.mimeData().hasImage():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._paths_from_mime_data(event.mimeData()) or event.mimeData().hasImage():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = self._paths_from_mime_data(event.mimeData())
        if paths:
            self.filesAttached.emit(paths)
            event.acceptProposedAction()
            return
        if event.mimeData().hasImage():
            self.imagePasted.emit(event.mimeData().imageData())
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._sync_placeholder()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._sync_placeholder()

    def _layout_placeholder(self) -> None:
        margin = 8
        self._placeholder_label.setGeometry(
            margin,
            margin,
            max(40, self.viewport().width() - (margin * 2)),
            24,
        )

    def _sync_placeholder(self) -> None:
        self._placeholder_label.setVisible(
            not self.hasFocus() and not self.toPlainText()
        )

    def _adjust_height(self) -> None:
        document = self.document()
        document.setTextWidth(max(1, self.viewport().width()))
        document_height = math.ceil(document.size().height())
        line_height = max(1, self.fontMetrics().lineSpacing())
        line_height_estimate = max(1, self.document().blockCount()) * line_height
        target_height = max(
            self.MIN_HEIGHT,
            min(self.MAX_HEIGHT, max(document_height, line_height_estimate) + 14),
        )
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if target_height >= self.MAX_HEIGHT
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        if self.height() != target_height:
            self.setFixedHeight(target_height)
            self.updateGeometry()

    @staticmethod
    def _paths_from_mime_data(mime_data) -> list[Path]:
        paths: list[Path] = []
        if mime_data.hasUrls():
            for url in mime_data.urls():
                if url.isLocalFile():
                    PromptTextEdit._append_existing_file(paths, url.toLocalFile())
        if not paths:
            for mime_format in mime_data.formats():
                lowered_format = str(mime_format).casefold()
                if (
                    "filenamew" not in lowered_format
                    and "filename" not in lowered_format
                ):
                    continue
                payload = bytes(mime_data.data(mime_format))
                if not payload:
                    continue
                encoding = (
                    "utf-16-le"
                    if "filenamew" in lowered_format
                    else sys.getfilesystemencoding()
                )
                try:
                    decoded = payload.decode(encoding, errors="ignore")
                except LookupError:
                    decoded = payload.decode("utf-8", errors="ignore")
                for candidate in decoded.split("\x00"):
                    PromptTextEdit._append_existing_file(paths, candidate)
        if not paths and mime_data.hasText():
            for line in mime_data.text().splitlines():
                candidate = line.strip().strip('"')
                if not candidate:
                    continue
                url = QUrl(candidate)
                if candidate.casefold().startswith("file:") and url.isLocalFile():
                    candidate = url.toLocalFile()
                PromptTextEdit._append_existing_file(paths, candidate)
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path.resolve()).casefold()
            if key not in seen:
                unique.append(path)
                seen.add(key)
        return unique

    @staticmethod
    def _append_existing_file(paths: list[Path], candidate: str) -> None:
        text = str(candidate or "").strip().strip('"')
        if not text:
            return
        try:
            path = Path(text)
            if path.exists() and path.is_file():
                paths.append(path)
        except (OSError, ValueError):
            return


class CodexPermissionOption(QFrame):
    selected = Signal(str)

    def __init__(
        self,
        mode: str,
        title: str,
        description: str,
        *,
        current: bool,
        icon: QIcon,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.mode = mode
        self.setObjectName("codexPermissionOption")
        self.setProperty("current", current)
        self.setFixedWidth(410)
        self.setMinimumHeight(58)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setAccessibleDescription(description)
        self.setStyleSheet(
            """
            QFrame#codexPermissionOption {
                border: none;
                border-radius: 7px;
                background: transparent;
            }
            QFrame#codexPermissionOption:hover,
            QFrame#codexPermissionOption:focus {
                background: #f2f4f7;
            }
            QFrame#codexPermissionOption[current="true"] {
                background: #f8fafc;
            }
            QFrame#codexPermissionOption QLabel {
                border: none;
                background: transparent;
            }
            """
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(11, 8, 11, 8)
        row.setSpacing(10)

        icon_label = QLabel(self)
        icon_label.setFixedSize(20, 20)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(icon.pixmap(QSize(18, 18)))
        row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(2)
        title_label = QLabel(title, self)
        title_label.setStyleSheet("color: #1d2939; font-size: 12px; font-weight: 600;")
        description_label = QLabel(description, self)
        description_label.setWordWrap(True)
        description_label.setStyleSheet("color: #667085; font-size: 10px;")
        labels.addWidget(title_label)
        labels.addWidget(description_label)
        row.addLayout(labels, 1)

        current_label = QLabel(self)
        current_label.setFixedSize(20, 20)
        current_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if current:
            current_label.setPixmap(
                self.style()
                .standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
                .pixmap(QSize(16, 16))
            )
        row.addWidget(current_label, 0, Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.selected.emit(self.mode)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.selected.emit(self.mode)
            event.accept()
            return
        super().keyPressEvent(event)


class AttachmentPreviewWidget(QWidget):
    removeRequested = Signal(str)
    CARD_SIZE = QSize(176, 56)

    def __init__(self, path: Path, detail: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.setObjectName("attachmentPreviewCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(self.CARD_SIZE)
        self.setToolTip(str(path))
        self.setStyleSheet(
            """
            QWidget#attachmentPreviewCard {
                border: 1px solid #e4e7ec;
                border-radius: 8px;
                background: #f9fafb;
            }
            QWidget#attachmentPreviewCard QLabel {
                border: none;
                background: transparent;
            }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 5, 4)
        layout.setSpacing(5)

        icon_label = QLabel(self)
        icon_label.setFixedSize(36, 36)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = (
            QIcon(str(path))
            if path.suffix.lower() in IMAGE_ATTACHMENT_EXTENSIONS
            else self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        )
        icon_label.setPixmap(icon.pixmap(QSize(34, 34)))
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 2, 0, 1)
        text_layout.setSpacing(1)
        filename_label = QLabel(
            QFontMetrics(self.font()).elidedText(
                path.name, Qt.TextElideMode.ElideRight, 92
            )
        )
        filename_label.setObjectName("attachmentFilenameLabel")
        filename_label.setToolTip(str(path))
        detail_label = QLabel(detail)
        detail_label.setStyleSheet("color: #667085; font-size: 10px;")
        text_layout.addWidget(filename_label)
        text_layout.addWidget(detail_label)
        layout.addLayout(text_layout, 1)

        remove_button = QToolButton(self)
        remove_button.setObjectName("attachmentRemoveButton")
        remove_button.setText("×")
        remove_button.setToolTip(f"{path.name} 제거")
        remove_button.setAccessibleName(f"{path.name} 제거")
        remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_button.setFixedSize(20, 20)
        remove_button.setStyleSheet(
            """
            QToolButton#attachmentRemoveButton {
                border: none;
                border-radius: 10px;
                background: transparent;
                color: #667085;
                font-size: 15px;
                padding: 0;
                width: 20px;
                min-width: 20px;
                max-width: 20px;
                height: 20px;
                min-height: 20px;
                max-height: 20px;
            }
            QToolButton#attachmentRemoveButton:hover {
                background: #fee4e2;
                color: #b42318;
            }
            QToolButton#attachmentRemoveButton:disabled {
                color: #cbd5e1;
            }
            """
        )
        remove_button.clicked.connect(
            lambda _checked=False: self.removeRequested.emit(str(self.path))
        )
        layout.addWidget(remove_button, 0, Qt.AlignmentFlag.AlignTop)


class AttachmentDropFrame(QFrame):
    filesAttached = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if PromptTextEdit._paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if PromptTextEdit._paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = PromptTextEdit._paths_from_mime_data(event.mimeData())
        if paths:
            self.filesAttached.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class MessageBodyView(QTextBrowser):
    HEIGHT_PADDING = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document = self.document()
        self._body_width = 120
        self._content_height = 1
        self._source_text = ""
        self._wrap_mode = QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        self.setObjectName("aiChatMessageBody")
        policy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(QDesktopServices.openUrl)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setContentsMargins(0, 0, 0, 0)
        self.setViewportMargins(0, 0, 0, 0)
        self.setStyleSheet(
            "QTextBrowser#aiChatMessageBody {"
            "border: none; background: transparent; padding: 0; margin: 0;"
            "}"
        )
        self._document.setDocumentMargin(0)
        self._apply_document_width(self._body_width)

    def set_message_body(
        self,
        body: str,
        *,
        rich_text: bool,
        width: int,
        text_color: str,
        source_text: str | None = None,
    ) -> None:
        body_width = max(120, int(width))
        self._body_width = body_width
        self.setFixedWidth(body_width)
        self._document.setDefaultStyleSheet(
            f"""
            body {{
                color: {text_color};
                font-size: 13px;
                line-height: 145%;
                margin: 0;
                white-space: normal;
            }}
            pre {{
                white-space: pre-wrap;
                word-wrap: break-word;
            }}
            code {{
                white-space: pre-wrap;
                word-wrap: break-word;
            }}
            table {{
                border-collapse: collapse;
                margin-top: 6px;
                margin-bottom: 6px;
            }}
            th {{
                background: #f2f4f7;
                font-weight: 600;
                padding: 4px 6px;
            }}
            td {{
                padding: 4px 6px;
            }}
            """
        )
        self._apply_document_width(body_width)
        if rich_text:
            self.setHtml(body)
            self._fit_tables_to_document_width()
        else:
            self.setPlainText(body)
        self._apply_document_width(body_width)
        self._source_text = str(source_text if source_text is not None else body)
        self.clearSelection()
        self._content_height = self._document_height()
        self.setFixedHeight(self._content_height)
        self.resize(body_width, self._content_height)
        self.updateGeometry()

    def _document_height(self) -> int:
        layout_height = self._document.documentLayout().documentSize().height()
        document_height = self._document.size().height()
        return max(
            1, math.ceil(max(layout_height, document_height)) + self.HEIGHT_PADDING
        )

    def _apply_document_width(self, body_width: int) -> None:
        document = self._document
        option = document.defaultTextOption()
        option.setWrapMode(self._wrap_mode)
        document.setDefaultTextOption(option)
        document.setDocumentMargin(0)
        document.setPageSize(QSizeF(body_width, 1_000_000))
        document.setTextWidth(body_width)

    def _fit_tables_to_document_width(self) -> None:
        def fit_child_tables(frame) -> None:
            for child in frame.childFrames():
                if isinstance(child, QTextTable):
                    table_format = child.format()
                    table_format.setWidth(
                        QTextLength(QTextLength.Type.PercentageLength, 96)
                    )
                    child.setFormat(table_format)
                fit_child_tables(child)

        fit_child_tables(self._document.rootFrame())

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        # The widget itself has a fixed bubble width. Qt may query heightForWidth
        # with the much wider parent layout width; applying that value to only
        # the document would make tables render outside the visible widget.
        _ = width
        return self._content_height

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self._body_width, self._content_height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.sizeHint()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        selection_action = menu.addAction("선택 영역 복사")
        selection_action.setEnabled(self.hasSelection())
        selection_action.triggered.connect(self.copy)
        copy_action = menu.addAction("본문 전체 복사")
        copy_action.triggered.connect(self.copySource)
        menu.exec(event.globalPos())

    def hasSelection(self) -> bool:  # noqa: N802 - QTextEdit compatibility
        return self.textCursor().hasSelection()

    def selectedText(self) -> str:  # noqa: N802 - QTextEdit compatibility
        return (
            self.textCursor()
            .selectedText()
            .replace("\u2029", "\n")
            .replace("\u2028", "\n")
        )

    def clearSelection(self) -> None:  # noqa: N802 - QTextEdit compatibility
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)

    def copySource(self) -> None:  # noqa: N802 - QTextEdit compatibility
        QApplication.clipboard().setText(self._source_text)

    def sourceText(self) -> str:  # noqa: N802 - test/read API
        return self._source_text

    def wordWrapMode(self) -> QTextOption.WrapMode:  # noqa: N802 - QTextEdit compatibility for tests
        return self._wrap_mode


class AiChatTab(QWidget):
    tool_settings_requested = Signal(str)

    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._providers: dict[str, AiProviderConfig] = provider_configs_from_app_config(
            self._ai_config()
        )
        self._process: QProcess | None = None
        self._status_process: QProcess | None = None
        self._help_process: QProcess | None = None
        self._prompt_timeout_timer: QTimer | None = None
        self._status_timeout_timer: QTimer | None = None
        self._help_timeout_timer: QTimer | None = None
        thread_pool = (
            getattr(self.state, "thread_pool", None) or QThreadPool.globalInstance()
        )
        self._job_runner = JobRunner(
            thread_pool, self, default_error_title="AI 준비 실패"
        )
        injected_catalog_service = getattr(self.state, "ai_model_catalog_service", None)
        self._model_catalog_service = injected_catalog_service or AiModelCatalogService(
            self._model_catalog_cache_path()
        )
        self._model_catalogs: dict[str, AiModelCatalog] = {}
        self._model_catalog_cancel_event = Event()
        self._model_catalog_request_generation = 0
        self._active_model_catalog_request: tuple[str, int] | None = None
        self._pending_model_catalog_refreshes: dict[str, bool] = {}
        self._stdout_buffer = b""
        self._cli_error_text = ""
        self._help_stdout = b""
        self._help_stderr = b""
        self._stderr_text = ""
        self._help_loaded_for = ""
        self._messages: list[dict[str, str]] = []
        self._working_status_text = ""
        self._working_status_step = 0
        self._working_status_label: QLabel | None = None
        self._working_status_timer = QTimer(self)
        self._working_status_timer.setInterval(420)
        self._working_status_timer.timeout.connect(
            self._advance_working_status_animation
        )
        self._provider_session_ids: dict[str, str] = {}
        self._codex_permission_mode = "read-only"
        self._permission_menu: QMenu | None = None
        self._attachments: list[Path] = []
        self._clipboard_image_files: dict[str, QTemporaryFile] = {}
        self._context_collecting = False
        self._context_collection_cancelled = False
        self._context_request_generation = 0
        self._active_context_request: int | None = None
        self._context_cancel_event: Event | None = None
        self._context_operation_label = ""
        self._login_preflight_active = False
        self._pending_prompt_payload: dict[str, Any] | None = None
        self._assistant_registry = build_netops_tool_registry()
        paths = getattr(self.state, "paths", None)
        logs_dir = Path(getattr(paths, "logs_dir", getattr(paths, "root", Path.cwd())))
        self._assistant_executor = ToolExecutor(
            self.state,
            self._assistant_registry,
            audit_logger=AuditLogger(logs_dir / "netops_assistant_audit.jsonl"),
        )
        self._stream_message_index: int | None = None
        self._last_render_width = 0
        self._render_deferred = False
        self._transcript_render_generation = 0
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(50)
        self._render_timer.timeout.connect(self._flush_transcript_render)
        self._deferred_timers: list[QTimer] = []
        self._active_provider = self._ai_config().get("active_provider", "codex")
        self._build_ui()
        self._load_config_into_ui()
        self.refresh_provider_status()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(
            make_step_hint(
                "NetOps 어시스턴트: 조회와 진단은 바로 실행하고, 변경 작업은 내용을 확인하고 승인한 뒤 실행합니다."
            )
        )

        self.ai_chat_tabs = QTabWidget()
        layout.addWidget(self.ai_chat_tabs, 1)

        self.chat_page = QWidget()
        chat_page = self.chat_page
        chat_layout = QVBoxLayout(chat_page)
        chat_layout.setContentsMargins(0, 8, 0, 8)
        chat_layout.setSpacing(10)

        self.connection_page = QWidget()
        connection_layout = QVBoxLayout(self.connection_page)
        connection_layout.setContentsMargins(0, 8, 0, 0)
        connection_layout.setSpacing(10)
        connection_layout.addWidget(
            make_step_hint(
                "사용할 AI 서비스와 모델을 선택합니다. 일반적으로 자동 선택을 사용하면 됩니다."
            )
        )

        self.provider_group = QGroupBox("AI 연결 설정")
        provider_group = self.provider_group
        provider_group.setObjectName("aiProviderSettingsGroup")
        provider_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        provider_layout = QGridLayout(provider_group)
        provider_layout.setContentsMargins(10, 14, 10, 10)
        provider_layout.setHorizontalSpacing(10)
        provider_layout.setVerticalSpacing(8)
        for column in range(2):
            provider_layout.setColumnStretch(column, 1)

        self.provider_combo = NoWheelComboBox()
        for key in KNOWN_AI_PROVIDERS:
            self.provider_combo.addItem(PROVIDER_SPECS[key].display_name, key)
        # Kept as an internal mirror for existing runtime helpers. The editable path now lives in Settings.
        self.command_edit = QLineEdit(self)
        self.command_edit.hide()
        self.model_combo = NoWheelComboBox()
        self.reasoning_combo = NoWheelComboBox()
        self.reasoning_combo.addItem("자동 선택 (권장)", "")
        self.speed_combo = NoWheelComboBox()
        self.speed_combo.addItem("자동 선택 (권장)", "")
        self.extra_args_edit = QLineEdit()
        self.status_label = QLabel("미확인")
        self.status_label.setWordWrap(False)
        self.model_detail_label = QLabel("모델 정보를 확인하는 중입니다.")
        self.model_detail_label.setWordWrap(True)
        self.model_detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.model_catalog_status_label = QLabel(
            "저장된 모델 목록을 확인하는 중입니다."
        )
        self.model_catalog_status_label.setWordWrap(True)
        self.extra_args_edit.setPlaceholderText(
            "보통은 비워 두세요. 아래에서 필요한 기능을 선택할 수 있습니다."
        )
        self.provider_combo.setToolTip("요청을 처리할 AI CLI 서비스를 선택합니다.")
        self.model_combo.setToolTip(
            "사용할 모델을 선택합니다. 자동 선택은 CLI의 기본 모델을 사용합니다."
        )
        self.reasoning_combo.setToolTip(
            "복잡한 작업일수록 높은 단계를 선택하면 더 깊게 검토합니다."
        )
        self.speed_combo.setToolTip(
            "빠른 응답은 지원되는 CLI에서 우선 처리 모드를 사용합니다."
        )

        tool_settings_row = QWidget()
        tool_settings_layout = QHBoxLayout(tool_settings_row)
        tool_settings_layout.setContentsMargins(0, 0, 0, 0)
        tool_settings_layout.setSpacing(8)
        tool_settings_label = QLabel(
            "실행 파일 경로와 설치 상태는 설정 > 도구 연동에서 관리합니다."
        )
        tool_settings_label.setWordWrap(True)
        self.tool_settings_button = make_action_button(
            "도구 연동 열기", ActionKind.UTILITY
        )
        tool_settings_layout.addWidget(tool_settings_label, 1)
        tool_settings_layout.addWidget(self.tool_settings_button)

        provider_layout.addWidget(
            self._provider_field("AI 서비스", self.provider_combo), 0, 0
        )
        provider_layout.addWidget(
            self._provider_field("연결 상태", self.status_label), 0, 1
        )
        provider_layout.addWidget(
            self._provider_field("사용 모델", self.model_combo), 1, 0, 1, 2
        )
        provider_layout.addWidget(
            self._provider_field("답변 사고 깊이", self.reasoning_combo), 2, 0
        )
        provider_layout.addWidget(
            self._provider_field("응답 속도", self.speed_combo), 2, 1
        )
        provider_layout.addWidget(
            self._provider_field("CLI 실행 파일", tool_settings_row), 3, 0, 1, 2
        )
        provider_layout.addWidget(
            self._provider_field("모델 세부 정보", self.model_detail_label), 4, 0, 1, 2
        )
        provider_layout.addWidget(
            self._provider_field("모델 목록 상태", self.model_catalog_status_label),
            5,
            0,
            1,
            2,
        )
        connection_layout.addWidget(provider_group)

        catalog_actions = QHBoxLayout()
        catalog_actions.setContentsMargins(4, 0, 4, 0)
        catalog_actions.setSpacing(8)
        self.model_refresh_button = make_action_button(
            "모델 목록 새로고침", ActionKind.REFRESH
        )
        self.custom_model_button = make_action_button(
            "모델 ID 직접 입력", ActionKind.ADD
        )
        catalog_actions.addWidget(self.model_refresh_button)
        catalog_actions.addWidget(self.custom_model_button)
        catalog_actions.addStretch(1)
        connection_layout.addLayout(catalog_actions)

        provider_actions = QHBoxLayout()
        provider_actions.setContentsMargins(4, 0, 4, 0)
        provider_actions.setSpacing(8)
        self.check_button = make_action_button("상태 확인", ActionKind.REFRESH)
        self.login_button = make_action_button("로그인 터미널", ActionKind.START)
        self.save_button = make_action_button("설정 저장", ActionKind.SAVE)
        provider_actions.addWidget(self.check_button)
        provider_actions.addWidget(self.login_button)
        provider_actions.addWidget(self.save_button)
        provider_actions.addStretch(1)
        connection_layout.addLayout(provider_actions)
        connection_layout.addStretch(1)

        self.transcript_scroll = QScrollArea()
        self.transcript_scroll.setObjectName("aiChatTranscript")
        self.transcript_scroll.setWidgetResizable(True)
        self.transcript_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
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
        self.message_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.message_layout = QVBoxLayout(self.message_container)
        self.message_layout.setContentsMargins(12, 12, 12, 12)
        self.message_layout.setSpacing(8)
        self.transcript_scroll.setWidget(self.message_container)
        self._render_transcript()
        chat_layout.addWidget(self.transcript_scroll, 1)

        composer_frame = AttachmentDropFrame()
        composer_frame.setObjectName("assistantComposer")
        composer_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        composer_frame.setStyleSheet(
            """
            QFrame#assistantComposer {
                background: #ffffff;
                border: 1px solid #d0d5dd;
                border-radius: 12px;
            }
            QListWidget#attachmentPreviewList {
                background: transparent;
                border: none;
                padding: 0;
            }
            QListWidget#attachmentPreviewList::item {
                border: none;
                padding: 0;
                margin: 0;
                background: transparent;
            }
            QPlainTextEdit#assistantPromptEdit {
                background: transparent;
                border: none;
                padding: 5px 6px;
                color: #111827;
            }
            """
        )
        composer_layout = QVBoxLayout(composer_frame)
        composer_layout.setContentsMargins(10, 8, 10, 8)
        composer_layout.setSpacing(6)

        self.attachment_list = QListWidget()
        self.attachment_list.setObjectName("attachmentPreviewList")
        self.attachment_list.setMaximumHeight(66)
        self.attachment_list.setIconSize(QSize(64, 42))
        self.attachment_list.setViewMode(QListView.ViewMode.IconMode)
        self.attachment_list.setMovement(QListView.Movement.Static)
        self.attachment_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.attachment_list.setFlow(QListView.Flow.LeftToRight)
        self.attachment_list.setWrapping(False)
        self.attachment_list.setSpacing(6)
        self.attachment_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.attachment_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.attachment_list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.attachment_list.setVisible(False)
        composer_layout.addWidget(self.attachment_list)

        self.prompt_edit = PromptTextEdit()
        self.prompt_edit.setObjectName("assistantPromptEdit")
        self.prompt_edit.setPlaceholderText("NetOps 작업을 자연어로 입력하세요...")
        self.prompt_edit.setToolTip(
            "Enter로 보내고 Shift+Enter로 줄을 바꿉니다. 파일과 클립보드 이미지는 붙여넣거나 끌어놓을 수 있습니다."
        )
        composer_layout.addWidget(self.prompt_edit)

        composer_actions = QHBoxLayout()
        composer_actions.setContentsMargins(0, 0, 0, 0)
        composer_actions.setSpacing(8)
        self.permission_button = make_action_button(
            "읽기 전용", ActionKind.UTILITY, object_name="aiPermissionButton"
        )
        self.permission_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.permission_button.setStyleSheet(
            """
            QPushButton#aiPermissionButton {
                min-height: 24px;
                padding: 2px 8px;
                border: 1px solid #d0d5dd;
                border-radius: 6px;
                background: #ffffff;
                color: #344054;
                text-align: left;
            }
            QPushButton#aiPermissionButton:hover {
                background: #f8fafc;
                border-color: #98a2b3;
            }
            QPushButton#aiPermissionButton[permissionMode="workspace-write"] {
                color: #175cd3;
                border-color: #84adff;
                background: #eff8ff;
            }
            QPushButton#aiPermissionButton[permissionMode="danger-full-access"] {
                color: #b54708;
                border-color: #f79009;
                background: #fffaeb;
            }
            """
        )
        self.attach_button = make_action_button("파일 첨부", ActionKind.ADD)
        self.clear_attachments_button = make_action_button(
            "전체 비우기", ActionKind.DELETE, enabled=False
        )
        self.send_button = make_action_button("보내기", ActionKind.START)
        self.stop_button = make_action_button("중지", ActionKind.STOP, enabled=False)
        self.reset_session_button = make_action_button(
            "세션 초기화",
            ActionKind.REFRESH,
            tooltip="화면의 대화 기록은 유지하고 다음 요청부터 새 대화 세션을 시작합니다.",
        )
        self.export_button = make_action_button("대화 내용 저장", ActionKind.EXPORT)
        composer_actions.addWidget(self.permission_button)
        composer_actions.addWidget(self.attach_button)
        composer_actions.addWidget(self.clear_attachments_button)
        composer_actions.addStretch(1)
        composer_actions.addWidget(self.send_button)
        composer_actions.addWidget(self.stop_button)
        composer_actions.addWidget(self.reset_session_button)
        composer_actions.addWidget(self.export_button)
        composer_layout.addLayout(composer_actions)
        chat_layout.addWidget(composer_frame)

        self.ai_chat_tabs.addTab(chat_page, "채팅")
        self.ai_chat_tabs.addTab(self.connection_page, "연결 설정")

        options_page = QWidget()
        options_layout = QVBoxLayout(options_page)
        options_layout.setContentsMargins(0, 8, 0, 0)
        options_layout.setSpacing(10)

        option_hint = QLabel(
            "대부분은 설정할 필요가 없습니다. 필요한 기능을 한국어 설명으로 확인한 뒤 선택하세요. "
            "권한을 넓히거나 승인 절차를 우회하는 항목은 표시하지 않습니다."
        )
        option_hint.setWordWrap(True)
        option_hint.setStyleSheet(
            "color: #344054; background: #f8fafc; border-left: 3px solid #cbd5e1; padding: 8px;"
        )
        options_layout.addWidget(option_hint)

        applied_group = QGroupBox("현재 적용된 고급 옵션")
        applied_form = QFormLayout(applied_group)
        applied_form.setContentsMargins(10, 14, 10, 10)
        applied_form.setHorizontalSpacing(10)
        applied_form.addRow("직접 입력", self.extra_args_edit)
        options_layout.addWidget(applied_group)

        help_actions = QHBoxLayout()
        self.help_refresh_button = make_action_button(
            "사용 가능한 기능 불러오기", ActionKind.REFRESH
        )
        self.help_status_label = QLabel(
            "연결 상태를 확인하면 선택 가능한 기능을 자동으로 불러옵니다."
        )
        self.help_status_label.setWordWrap(True)
        help_actions.addWidget(self.help_refresh_button)
        help_actions.addWidget(self.help_status_label, 1)
        options_layout.addLayout(help_actions)

        option_group = QGroupBox("기능 선택")
        option_form = QFormLayout(option_group)
        option_form.setContentsMargins(10, 14, 10, 10)
        option_form.setHorizontalSpacing(10)
        option_form.setVerticalSpacing(8)

        self.option_combo = NoWheelComboBox()
        self.option_value_edit = QLineEdit()
        self.option_value_edit.setPlaceholderText("값이 필요한 옵션이면 입력")
        self.option_description = QLabel()
        self.option_description.setObjectName("assistantOptionDescription")
        self.option_description.setWordWrap(True)
        self.option_description.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.option_description.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.option_description.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.option_description.setMinimumHeight(160)
        self.option_description.setStyleSheet(
            """
            QLabel#assistantOptionDescription {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 8px;
            }
            """
        )
        option_form.addRow("사용할 기능", self.option_combo)
        option_form.addRow("설정값", self.option_value_edit)
        option_form.addRow("설명 및 주의사항", self.option_description)
        options_layout.addWidget(option_group)

        option_actions = QHBoxLayout()
        self.add_option_button = make_action_button("선택한 기능 적용", ActionKind.ADD)
        self.remove_option_button = make_action_button(
            "선택한 기능 해제", ActionKind.DELETE
        )
        option_actions.addWidget(self.add_option_button)
        option_actions.addWidget(self.remove_option_button)
        option_actions.addStretch(1)
        options_layout.addLayout(option_actions)

        self.raw_help_group = QGroupBox("CLI 도움말 원문 보기")
        self.raw_help_group.setCheckable(True)
        self.raw_help_group.setChecked(False)
        self.raw_help_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        raw_help_layout = QVBoxLayout(self.raw_help_group)
        raw_help_layout.setContentsMargins(10, 12, 10, 10)
        self.raw_help_edit = QPlainTextEdit()
        self.raw_help_edit.setReadOnly(True)
        self.raw_help_edit.setPlaceholderText(
            "CLI가 제공한 원문 도움말이 여기에 표시됩니다."
        )
        self.raw_help_edit.setMinimumHeight(120)
        self.raw_help_edit.setVisible(False)
        raw_help_layout.addWidget(self.raw_help_edit)
        options_layout.addWidget(self.raw_help_group)
        options_layout.addStretch(1)

        self.ai_chat_tabs.addTab(options_page, "고급 옵션")
        self.ai_chat_tabs.currentChanged.connect(self._handle_ai_chat_tab_changed)

        self.provider_combo.currentIndexChanged.connect(self._handle_provider_changed)
        self.model_combo.currentIndexChanged.connect(self._handle_model_changed)
        self.reasoning_combo.currentIndexChanged.connect(self.save_current_config)
        self.speed_combo.currentIndexChanged.connect(self.save_current_config)
        self.extra_args_edit.editingFinished.connect(self.save_current_config)
        self.prompt_edit.sendRequested.connect(self.send_prompt)
        self.prompt_edit.filesAttached.connect(self.attach_paths)
        self.prompt_edit.imagePasted.connect(self.attach_clipboard_image)
        composer_frame.filesAttached.connect(self.attach_paths)
        self.attach_button.clicked.connect(self.attach_files)
        self.clear_attachments_button.clicked.connect(self.clear_attachments)
        self.check_button.clicked.connect(
            lambda _checked=False: self.refresh_provider_status(allow_repair=True)
        )
        self.model_refresh_button.clicked.connect(self._refresh_model_catalog_manually)
        self.custom_model_button.clicked.connect(self._enter_custom_model)
        self.login_button.clicked.connect(self.open_provider_login)
        self.save_button.clicked.connect(self.save_current_config)
        self.tool_settings_button.clicked.connect(
            lambda _checked=False: self.tool_settings_requested.emit(
                self.current_provider_key()
            )
        )
        self.send_button.clicked.connect(self.send_prompt)
        self.permission_button.clicked.connect(self._show_permission_menu)
        self.stop_button.clicked.connect(self.cancel_prompt)
        self.reset_session_button.clicked.connect(self.reset_session)
        self.export_button.clicked.connect(self.export_session)
        self.help_refresh_button.clicked.connect(self.refresh_cli_help_options)
        self.raw_help_group.toggled.connect(self._set_raw_help_visible)
        self.option_combo.currentIndexChanged.connect(self._handle_help_option_changed)
        self.add_option_button.clicked.connect(self.add_selected_extra_arg)
        self.remove_option_button.clicked.connect(self.remove_selected_extra_arg)
        self._populate_help_options("")
        self._update_permission_button()
        self._update_attachment_buttons()

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
            vertical_policy = (
                QSizePolicy.Policy.Preferred
                if widget.wordWrap()
                else QSizePolicy.Policy.Fixed
            )
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, vertical_policy)

        container_layout.addWidget(label)
        container_layout.addWidget(widget)
        return container

    def _set_raw_help_visible(self, visible: bool) -> None:
        self.raw_help_edit.setVisible(visible)
        self.raw_help_group.setTitle(
            "CLI 도움말 원문 숨기기" if visible else "CLI 도움말 원문 보기"
        )
        self.raw_help_group.updateGeometry()

    def _ai_config(self) -> dict[str, Any]:
        return normalize_ai_chat_config(self.state.app_config.get("ai_chat", {}))

    def _permission_icon(self, mode: str) -> QIcon:
        pixmap = {
            "read-only": QStyle.StandardPixmap.SP_MessageBoxInformation,
            "workspace-write": QStyle.StandardPixmap.SP_DirOpenIcon,
            "danger-full-access": QStyle.StandardPixmap.SP_MessageBoxWarning,
        }.get(mode, QStyle.StandardPixmap.SP_MessageBoxInformation)
        return self.style().standardIcon(pixmap)

    def _build_permission_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setObjectName("codexPermissionMenu")
        menu.setStyleSheet(
            """
            QMenu#codexPermissionMenu {
                background: #ffffff;
                border: 1px solid #d0d5dd;
                border-radius: 9px;
                padding: 5px;
            }
            """
        )
        for mode in CODEX_PERMISSION_MODES:
            widget_action = QWidgetAction(menu)
            option = CodexPermissionOption(
                mode,
                CODEX_PERMISSION_TITLES[mode],
                CODEX_PERMISSION_DESCRIPTIONS[mode],
                current=mode == self._codex_permission_mode,
                icon=self._permission_icon(mode),
                parent=menu,
            )
            option.selected.connect(self._select_codex_permission_mode)
            widget_action.setDefaultWidget(option)
            menu.addAction(widget_action)
        return menu

    def _show_permission_menu(self, _checked: bool = False) -> None:
        if (
            self.current_provider_key() != "codex"
            or not self.permission_button.isEnabled()
        ):
            return
        if self._permission_menu is not None:
            self._permission_menu.close()

        menu = self._build_permission_menu()
        self._permission_menu = menu

        def clear_menu_reference() -> None:
            if self._permission_menu is menu:
                self._permission_menu = None
            menu.deleteLater()

        menu.aboutToHide.connect(clear_menu_reference)
        menu.adjustSize()
        menu_size = menu.sizeHint()
        button_origin = self.permission_button.mapToGlobal(QPoint(0, 0))
        screen = QApplication.screenAt(button_origin)
        available = screen.availableGeometry() if screen is not None else None
        x = button_origin.x()
        y = button_origin.y() - menu_size.height() - 5
        if available is not None:
            x = max(
                available.left() + 4,
                min(x, available.right() - menu_size.width() - 4),
            )
            if y < available.top() + 4:
                y = self.permission_button.mapToGlobal(
                    QPoint(0, self.permission_button.height() + 5)
                ).y()
        menu.popup(QPoint(x, y))

    def _select_codex_permission_mode(self, mode: str) -> bool:
        if mode not in CODEX_PERMISSION_MODES:
            return False
        if mode == self._codex_permission_mode:
            if self._permission_menu is not None:
                self._permission_menu.close()
            return True
        if mode == "danger-full-access":
            answer = QMessageBox.warning(
                self,
                "전체 권한 허용",
                "전체 권한을 사용하면 Codex가 인터넷과 컴퓨터의 모든 파일에 제한 없이 "
                "접근하고 명령을 실행할 수 있습니다.\n\n"
                "신뢰할 수 있는 요청에서만 사용하세요. 전체 권한으로 변경할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False

        self._codex_permission_mode = mode
        self._update_permission_button()
        if self._permission_menu is not None:
            self._permission_menu.close()
        return True

    def _update_permission_button(self) -> None:
        if not hasattr(self, "permission_button"):
            return
        is_codex = self.current_provider_key() == "codex"
        self.permission_button.setVisible(is_codex)
        mode = self._codex_permission_mode
        self.permission_button.setText(CODEX_PERMISSION_BUTTON_TITLES[mode])
        self.permission_button.setIcon(self._permission_icon(mode))
        self.permission_button.setProperty("permissionMode", mode)
        self.permission_button.setToolTip(
            f"{CODEX_PERMISSION_TITLES[mode]}\n{CODEX_PERMISSION_DESCRIPTIONS[mode]}"
        )
        self.permission_button.style().unpolish(self.permission_button)
        self.permission_button.style().polish(self.permission_button)
        self.permission_button.update()

    @staticmethod
    def _resolved_workspace_path(value: object, fallback: Path) -> Path:
        raw_path = Path(value) if value not in (None, "") else fallback
        try:
            return raw_path.expanduser().resolve(strict=False)
        except OSError:
            return raw_path.expanduser().absolute()

    @staticmethod
    def _path_is_within(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            path_text = str(path).casefold().rstrip("\\/")
            parent_text = str(parent).casefold().rstrip("\\/")
            return path_text == parent_text or path_text.startswith(
                parent_text + ("\\" if "\\" in parent_text else "/")
            )

    def _codex_workspace_paths(self) -> tuple[Path, tuple[Path, ...]]:
        paths = getattr(self.state, "paths", None)
        app_root = self._resolved_workspace_path(
            getattr(paths, "root", None), Path.cwd()
        )
        data_root = self._resolved_workspace_path(
            getattr(paths, "data_root", None), app_root
        )
        config_dir = self._resolved_workspace_path(
            getattr(paths, "config_dir", None), data_root / "config"
        )
        logs_dir = self._resolved_workspace_path(
            getattr(paths, "logs_dir", None), data_root / "logs"
        )
        exports_dir = self._resolved_workspace_path(
            getattr(paths, "exports_dir", None), logs_dir / "exports"
        )

        candidates = (
            data_root / "inspector",
            data_root / "config_builder",
            logs_dir,
            exports_dir,
        )
        writable: list[Path] = []
        for candidate in candidates:
            resolved = self._resolved_workspace_path(candidate, candidate)
            if self._path_is_within(resolved, config_dir):
                continue
            if any(self._path_is_within(resolved, existing) for existing in writable):
                continue
            writable = [
                existing
                for existing in writable
                if not self._path_is_within(existing, resolved)
            ]
            writable.append(resolved)
        return config_dir, tuple(writable)

    def _codex_invocation_access(
        self,
    ) -> tuple[str, str, tuple[str, ...], str]:
        paths = getattr(self.state, "paths", None)
        default_working_dir = str(getattr(paths, "root", Path.cwd()))
        mode = self._codex_permission_mode
        if mode == "read-only":
            return mode, "", (), default_working_dir

        workspace_root, writable_dirs = self._codex_workspace_paths()
        required_dirs = (
            (workspace_root, *writable_dirs)
            if mode == "workspace-write"
            else (workspace_root,)
        )
        try:
            for directory in required_dirs:
                directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(
                f"Codex 작업공간 폴더를 준비할 수 없습니다: {directory}\n{exc}"
            ) from exc
        return (
            mode,
            str(workspace_root),
            tuple(str(path) for path in writable_dirs)
            if mode == "workspace-write"
            else (),
            str(workspace_root),
        )

    def _model_catalog_cache_path(self) -> Path:
        paths = getattr(self.state, "paths", None)
        explicit_path = getattr(paths, "ai_model_catalog_cache", None)
        if explicit_path:
            return Path(explicit_path)
        config_dir = getattr(paths, "config_dir", None)
        if config_dir:
            return Path(config_dir) / "ai_model_catalog_cache.json"
        root = Path(getattr(paths, "root", Path.cwd()))
        return root / "config" / "ai_model_catalog_cache.json"

    def _load_config_into_ui(self) -> None:
        for key in KNOWN_AI_PROVIDERS:
            config = self._providers.get(key, AiProviderConfig(key=key))
            self._model_catalogs[key] = self._model_catalog_service.load_catalog(
                key, config.model
            )
        self._set_combo_data(self.provider_combo, self._active_provider)
        self._load_provider_fields(self.current_provider_key())

    def _handle_provider_changed(self, _index: int = -1) -> None:
        self._active_provider = self.current_provider_key()
        self._reset_help_options()
        self._load_provider_fields(self._active_provider)
        self.save_current_config()
        self.refresh_provider_status()
        if self.ai_chat_tabs.currentWidget() is self.connection_page:
            self._ensure_model_catalog_fresh(self._active_provider)

    def _load_provider_fields(self, key: str) -> None:
        config = self._providers.get(key, AiProviderConfig(key=key))
        self.command_edit.setText(config.command_path)
        self._populate_model_combo(key, config.model)
        self._sync_codex_controls()
        self.extra_args_edit.setText(" ".join(config.extra_args))
        self._update_permission_button()

    def _populate_model_combo(self, key: str, current_model: str = "") -> None:
        selected_model = current_model.strip()
        catalog = self._model_catalogs.get(key)
        if catalog is None:
            catalog = self._model_catalog_service.load_catalog(key, selected_model)
            self._model_catalogs[key] = catalog
        if selected_model and selected_model not in {
            model.model for model in catalog.models
        }:
            catalog.models.append(
                AiModelDescriptor(
                    id=selected_model,
                    model=selected_model,
                    display_name=selected_model,
                    source="custom",
                )
            )

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        default_descriptor = next(
            (
                model
                for model in catalog.models
                if model.is_default and not model.hidden
            ),
            None,
        )
        auto_label = "자동 선택"
        if default_descriptor is not None:
            auto_label = f"자동 선택 (현재 기본: {default_descriptor.display_name})"
        self.model_combo.addItem(auto_label, "")
        self.model_combo.setItemData(
            0,
            "CLI가 현재 계정에 지정한 기본 모델을 사용합니다.",
            Qt.ItemDataRole.ToolTipRole,
        )

        seen: set[str] = set()
        for descriptor in catalog.models:
            if descriptor.hidden or descriptor.model in seen:
                continue
            seen.add(descriptor.model)
            if descriptor.source == "custom":
                prefix = (
                    "현재 설정" if descriptor.model == selected_model else "직접 입력"
                )
                label = f"{prefix}: {descriptor.model} (목록에서 확인되지 않음)"
                tooltip = "저장된 모델 ID를 유지합니다. 현재 계정 지원 여부는 확인되지 않았습니다."
            else:
                label = descriptor.display_name or descriptor.model
                tooltip = f"실제 CLI 모델 ID: {descriptor.model}"
            self.model_combo.addItem(label, descriptor.model)
            index = self.model_combo.count() - 1
            self.model_combo.setItemData(index, descriptor, MODEL_DESCRIPTOR_ROLE)
            self.model_combo.setItemData(index, tooltip, Qt.ItemDataRole.ToolTipRole)

        self._set_combo_data(self.model_combo, selected_model)
        self.model_combo.blockSignals(False)
        config = self._providers.get(key, AiProviderConfig(key=key))
        normalized_reasoning, normalized_speed = self._rebuild_model_dependent_controls(
            key,
            self._effective_model_descriptor(),
            config.reasoning_effort,
            config.speed,
        )
        options_changed = (
            config.reasoning_effort != normalized_reasoning
            or config.speed != normalized_speed
        )
        if options_changed:
            config.reasoning_effort = normalized_reasoning
            config.speed = normalized_speed
            self._providers[key] = config
            self._persist_ai_config()
        self._update_model_detail()
        self._update_model_catalog_status(catalog)

    def _handle_model_changed(self, _index: int = -1) -> None:
        key = self.current_provider_key()
        existing = self._providers.get(key, AiProviderConfig(key=key))
        normalized_reasoning, normalized_speed = self._rebuild_model_dependent_controls(
            key,
            self._effective_model_descriptor(),
            existing.reasoning_effort,
            existing.speed,
        )
        existing.reasoning_effort = normalized_reasoning
        existing.speed = normalized_speed
        self._providers[key] = existing
        self._update_model_detail()
        self.save_current_config()

    def _current_model_descriptor(self) -> AiModelDescriptor | None:
        descriptor = self.model_combo.currentData(MODEL_DESCRIPTOR_ROLE)
        return descriptor if isinstance(descriptor, AiModelDescriptor) else None

    def _effective_model_descriptor(self) -> AiModelDescriptor | None:
        descriptor = self._current_model_descriptor()
        if descriptor is not None:
            return descriptor
        catalog = self._model_catalogs.get(self.current_provider_key())
        return next(
            (
                model
                for model in (catalog.models if catalog else [])
                if model.is_default and not model.hidden
            ),
            None,
        )

    def _rebuild_model_dependent_controls(
        self,
        key: str,
        descriptor: AiModelDescriptor | None,
        current_reasoning: str,
        current_speed: str,
    ) -> tuple[str, str]:
        self.reasoning_combo.blockSignals(True)
        self.reasoning_combo.clear()
        self.reasoning_combo.addItem("자동 선택 (권장)", "")

        if key == "codex":
            if descriptor is None or descriptor.source in {"custom", "fallback"}:
                supported_reasoning = list(REASONING_EFFORT_ORDER)
            else:
                supported_reasoning = [
                    value
                    for value in descriptor.supported_reasoning_efforts
                    if value in REASONING_EFFORT_LABELS
                ]
            for value in supported_reasoning:
                self.reasoning_combo.addItem(REASONING_EFFORT_LABELS[value], value)
            self._set_combo_data(
                self.reasoning_combo,
                current_reasoning
                if self.reasoning_combo.findData(current_reasoning) >= 0
                else "",
            )
        self.reasoning_combo.blockSignals(False)
        normalized_reasoning = (
            str(self.reasoning_combo.currentData() or "") if key == "codex" else ""
        )

        self.speed_combo.blockSignals(True)
        self.speed_combo.clear()
        self.speed_combo.addItem("자동 선택 (권장)", "")
        if key == "codex":
            support_unknown = descriptor is None or descriptor.source in {
                "custom",
                "fallback",
            }
            if support_unknown or "fast" in descriptor.speed_tiers:
                self.speed_combo.addItem(SPEED_LABELS["fast"], "fast")
            if current_speed == "flex":
                self.speed_combo.addItem(SPEED_LABELS["flex"], "flex")
            self._set_combo_data(
                self.speed_combo,
                current_speed if self.speed_combo.findData(current_speed) >= 0 else "",
            )
        self.speed_combo.blockSignals(False)
        normalized_speed = (
            str(self.speed_combo.currentData() or "") if key == "codex" else ""
        )
        return normalized_reasoning, normalized_speed

    def _update_model_detail(self) -> None:
        descriptor = self._effective_model_descriptor()
        if descriptor is None:
            self.model_detail_label.setText(
                "자동 선택을 사용합니다. 모델별 지원 정보는 목록을 갱신하면 표시됩니다."
            )
            return

        catalog = self._model_catalogs.get(self.current_provider_key())
        catalog_source = catalog.source if catalog is not None else "fallback"
        support_unknown = (
            descriptor.source in {"custom", "fallback"} or catalog_source == "fallback"
        )
        if support_unknown:
            availability = (
                "지원 여부 미확인"
                if descriptor.source == "custom"
                else "내장 목록 항목 · 계정 지원 여부 미확인"
            )
            self.model_detail_label.setText(
                f"{availability} · 입력: 지원 여부 미확인 · "
                "추론 단계: 지원 여부 미확인 · 빠른 응답: 지원 여부 미확인"
            )
            return

        availability = (
            "마지막 확인 기준 현재 계정에서 사용 가능"
            if catalog_source == "cache"
            else "현재 계정에서 사용 가능"
        )
        modality_labels = [
            "텍스트" if value == "text" else "이미지"
            for value in descriptor.input_modalities
        ]
        reasoning_labels = [
            REASONING_EFFORT_LABELS[value].split(" (")[0]
            for value in descriptor.supported_reasoning_efforts
            if value in REASONING_EFFORT_LABELS
        ]
        reasoning_text = (
            ", ".join(reasoning_labels) if reasoning_labels else "지원 안 함"
        )
        speed_text = "지원" if "fast" in descriptor.speed_tiers else "미지원"
        self.model_detail_label.setText(
            f"{availability} · 입력: {', '.join(modality_labels) or '미확인'} · "
            f"추론 단계: {reasoning_text} · 빠른 응답: {speed_text}"
        )

    def _update_model_catalog_status(
        self, catalog: AiModelCatalog, activity: str = ""
    ) -> None:
        source_labels = {
            "live": "실시간 모델 목록",
            "cache": "저장된 모델 목록",
            "fallback": "내장 대체 목록",
            "custom": "직접 입력",
        }
        parts = [activity] if activity else []
        parts.append(source_labels.get(catalog.source, "모델 목록"))
        if catalog.fetched_at:
            fetched = QDateTime.fromString(catalog.fetched_at, Qt.DateFormat.ISODate)
            if fetched.isValid():
                parts.append(
                    f"마지막 갱신 {fetched.toLocalTime().toString('yyyy-MM-dd HH:mm')}"
                )
        if catalog.cli_version:
            parts.append(catalog.cli_version)
        if catalog.provider_key != "codex" and catalog.source == "fallback":
            parts.append("실시간 조회는 아직 지원하지 않음")
        self.model_catalog_status_label.setText(" · ".join(parts))
        self.model_catalog_status_label.setToolTip("")

    def _enter_custom_model(self) -> None:
        current_model = str(self.model_combo.currentData() or "")
        model_id, accepted = QInputDialog.getText(
            self,
            "모델 ID 직접 입력",
            "CLI에 전달할 모델 ID를 입력하세요.",
            QLineEdit.EchoMode.Normal,
            current_model,
        )
        if not accepted:
            return
        model_id = str(model_id)
        if not CUSTOM_MODEL_ID_RE.fullmatch(model_id):
            QMessageBox.warning(
                self,
                "모델 ID 확인",
                "1~128자의 영문, 숫자, 마침표, 밑줄, 콜론, 슬래시, 하이픈만 사용할 수 있습니다. "
                "공백과 제어문자는 사용할 수 없습니다.",
            )
            return

        key = self.current_provider_key()
        catalog = self._model_catalogs.get(
            key
        ) or self._model_catalog_service.fallback_catalog(key)
        if model_id not in {model.model for model in catalog.models}:
            catalog.models.append(
                AiModelDescriptor(
                    id=model_id,
                    model=model_id,
                    display_name=model_id,
                    source="custom",
                )
            )
        self._model_catalogs[key] = catalog
        self._populate_model_combo(key, model_id)
        self._handle_model_changed()

    def _sync_codex_controls(self) -> None:
        is_codex = self.current_provider_key() == "codex"
        self.reasoning_combo.setEnabled(is_codex)
        self.speed_combo.setEnabled(is_codex)

    def _refresh_model_catalog_manually(self) -> None:
        self._ensure_model_catalog_fresh(self.current_provider_key(), force=True)

    def _ensure_model_catalog_fresh(
        self, provider_key: str | None = None, *, force: bool = False
    ) -> None:
        key = provider_key or self.current_provider_key()
        if key not in KNOWN_AI_PROVIDERS:
            return
        config = (
            self.current_provider_config()
            if key == self.current_provider_key()
            else self._providers.get(key, AiProviderConfig(key=key))
        )
        catalog = self._model_catalogs.get(
            key
        ) or self._model_catalog_service.load_catalog(key, config.model)
        self._model_catalogs[key] = catalog
        if self._active_model_catalog_request is not None:
            active_key, _active_generation = self._active_model_catalog_request
            if active_key != key or force:
                self._pending_model_catalog_refreshes[key] = (
                    self._pending_model_catalog_refreshes.get(key, False) or force
                )
            if key == self.current_provider_key():
                self._update_model_catalog_status(
                    catalog, "모델 목록을 갱신하는 중입니다."
                )
            return

        self._pending_model_catalog_refreshes.pop(key, None)
        if key != "codex":
            catalog = self._model_catalog_service.fallback_catalog(key, config.model)
            self._model_catalogs[key] = catalog
            if key == self.current_provider_key():
                self._populate_model_combo(key, config.model)
            return

        health = inspect_provider(config)
        if not health.installed:
            if key == self.current_provider_key():
                self._update_model_catalog_status(
                    catalog, "Codex CLI를 찾지 못해 기존 목록을 사용합니다."
                )
            return

        self._model_catalog_request_generation += 1
        generation = self._model_catalog_request_generation
        self._active_model_catalog_request = (key, generation)
        self._model_catalog_cancel_event = Event()
        if key == self.current_provider_key():
            self.model_refresh_button.setEnabled(False)
            self._update_model_catalog_status(
                catalog, "현재 계정에서 사용 가능한 모델을 확인하는 중입니다."
            )

        self._job_runner.start(
            self._model_catalog_service.refresh,
            config,
            catalog,
            force,
            self._model_catalog_cancel_event,
            on_result=lambda result, key=key, generation=generation: (
                self._accept_model_catalog_result(key, generation, result)
            ),
            on_error=lambda message, key=key, generation=generation: (
                self._handle_model_catalog_error(key, generation, message)
            ),
            on_finished=lambda key=key, generation=generation: (
                self._finish_model_catalog_refresh(key, generation)
            ),
        )

    def _accept_model_catalog_result(
        self, key: str, generation: int, result: object
    ) -> None:
        if not isinstance(result, AiModelCatalog):
            self._handle_model_catalog_error(
                key, generation, "모델 목록 응답 형식이 올바르지 않습니다."
            )
            return
        self._model_catalogs[key] = result
        if (
            self._active_model_catalog_request != (key, generation)
            or key != self.current_provider_key()
        ):
            return
        current_model = self._providers.get(key, AiProviderConfig(key=key)).model
        self._populate_model_combo(key, current_model)

    def _handle_model_catalog_error(
        self, key: str, generation: int, message: str
    ) -> None:
        if (
            self._active_model_catalog_request != (key, generation)
            or key != self.current_provider_key()
        ):
            return
        catalog = self._model_catalogs.get(
            key
        ) or self._model_catalog_service.fallback_catalog(key)
        source = (
            "기존 목록을 유지합니다."
            if catalog and catalog.models
            else "자동 선택을 계속 사용할 수 있습니다."
        )
        self._update_model_catalog_status(catalog, f"모델 목록 갱신 실패 · {source}")
        self.model_catalog_status_label.setToolTip(message)

    def _finish_model_catalog_refresh(self, key: str, generation: int) -> None:
        if self._active_model_catalog_request == (key, generation):
            self._active_model_catalog_request = None
        if self._active_model_catalog_request is None:
            try:
                self.model_refresh_button.setEnabled(
                    self._process is None and not self._context_collecting
                )
            except RuntimeError:
                return
            self._retry_pending_model_catalog_refresh()

    def _retry_pending_model_catalog_refresh(self) -> None:
        if (
            self._active_model_catalog_request is not None
            or not self._pending_model_catalog_refreshes
        ):
            return
        key = self.current_provider_key()
        if key not in self._pending_model_catalog_refreshes:
            return
        force = self._pending_model_catalog_refreshes.pop(key)
        self._run_later(
            0,
            lambda key=key, force=force: self._ensure_model_catalog_fresh(
                key, force=force
            ),
        )

    def current_provider_key(self) -> str:
        return str(self.provider_combo.currentData() or "codex")

    def current_provider_config(self) -> AiProviderConfig:
        key = self.current_provider_key()
        existing = self._providers.get(key, AiProviderConfig(key=key))
        extra_args = self._split_extra_args()
        config = AiProviderConfig(
            key=key,
            enabled=True,
            command_path=existing.command_path,
            model=str(self.model_combo.currentData() or ""),
            reasoning_effort=(
                str(self.reasoning_combo.currentData() or "")
                if key == "codex"
                and str(self.reasoning_combo.currentData() or "")
                in {"", *REASONING_EFFORT_ORDER}
                else ""
            ),
            speed=(
                str(self.speed_combo.currentData() or "")
                if key == "codex"
                and str(self.speed_combo.currentData() or "") in {"", "fast", "flex"}
                else ""
            ),
            role_prompt="",
            extra_args=extra_args,
            timeout_seconds=existing.timeout_seconds,
        )
        self._providers[key] = config
        return config

    def reload_integration_settings(self) -> None:
        """Reload only externally managed CLI paths without replacing current model edits."""
        latest = provider_configs_from_app_config(self._ai_config())
        for key, provider in latest.items():
            existing = self._providers.get(key, AiProviderConfig(key=key))
            existing.command_path = provider.command_path
            self._providers[key] = existing
        self.command_edit.setText(
            self._providers[self.current_provider_key()].command_path
        )
        self.refresh_provider_status()

    def save_current_config(self) -> None:
        self.current_provider_config()
        self._persist_ai_config()

    def _persist_ai_config(self) -> None:
        config = dict(self.state.app_config)
        providers = {
            key: provider.to_dict() for key, provider in self._providers.items()
        }
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
        files, _selected_filter = QFileDialog.getOpenFileNames(
            self, "첨부할 파일 선택", str(self.state.paths.root)
        )
        if not files:
            return
        self.attach_paths([Path(file_name) for file_name in files])

    def attach_clipboard_image(self, image_data: object) -> None:
        if isinstance(image_data, QPixmap):
            image = image_data.toImage()
        elif isinstance(image_data, QImage):
            image = image_data.copy()
        else:
            try:
                image = QImage(image_data)
            except (TypeError, ValueError):
                image = QImage()
        if image.isNull():
            QMessageBox.warning(
                self, "이미지 붙여넣기 실패", "클립보드 이미지를 읽을 수 없습니다."
            )
            return

        temporary_name_pattern = str(
            Path(QDir.tempPath()) / "netops_clipboard_XXXXXX.png"
        )
        temporary_file = QTemporaryFile(temporary_name_pattern, self)
        temporary_file.setAutoRemove(True)
        if not temporary_file.open():
            QMessageBox.warning(
                self,
                "이미지 붙여넣기 실패",
                "클립보드 이미지를 저장할 임시 파일을 만들 수 없습니다.",
            )
            temporary_file.deleteLater()
            return
        image_path = Path(temporary_file.fileName())
        temporary_file.close()
        if not image.save(str(image_path), "PNG"):
            temporary_file.remove()
            temporary_file.deleteLater()
            QMessageBox.warning(
                self,
                "이미지 붙여넣기 실패",
                "클립보드 이미지를 PNG 파일로 저장할 수 없습니다.",
            )
            return

        key = self._attachment_key(image_path)
        self._clipboard_image_files[key] = temporary_file
        self.attach_paths([image_path])
        if image_path not in self._attachments:
            self._release_temporary_attachment(image_path)

    def attach_paths(self, paths: list[Path | str]) -> None:
        known = {self._attachment_key(path) for path in self._attachments}
        for item in paths:
            path = Path(item)
            if not path.exists() or not path.is_file():
                continue
            key = self._attachment_key(path)
            if key in known:
                continue
            self._attachments.append(path)
            known.add(key)
        self._refresh_attachment_view()

    def remove_attachment(self, path: str) -> None:
        target_key = self._attachment_key(Path(path))
        self._attachments = [
            item
            for item in self._attachments
            if self._attachment_key(item) != target_key
        ]
        self._release_temporary_attachment(Path(path))
        self._refresh_attachment_view()

    def clear_attachments(self) -> None:
        if not self._attachments:
            return
        attachments = list(self._attachments)
        self._attachments.clear()
        self._release_temporary_attachments(attachments)
        self._refresh_attachment_view()

    def _release_temporary_attachment(self, path: Path | str) -> None:
        key = self._attachment_key(Path(path))
        temporary_file = self._clipboard_image_files.pop(key, None)
        if temporary_file is None:
            return
        try:
            removed = temporary_file.remove()
        except RuntimeError:
            removed = False
        if not removed:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        temporary_file.deleteLater()

    def _release_temporary_attachments(self, paths: list[Path | str]) -> None:
        for path in paths:
            self._release_temporary_attachment(path)

    def _release_all_temporary_attachments(self) -> None:
        paths = [
            temporary_file.fileName()
            for temporary_file in self._clipboard_image_files.values()
        ]
        self._release_temporary_attachments(paths)

    def _temporary_attachment_paths(self, paths: list[Path | str]) -> list[str]:
        return [
            str(path)
            for path in paths
            if self._attachment_key(Path(path)) in self._clipboard_image_files
        ]

    def _release_process_temporary_attachments(self, process: QProcess) -> None:
        try:
            paths = process.property("temporary_attachment_paths") or []
        except (AttributeError, RuntimeError):
            paths = []
        if isinstance(paths, str):
            paths = [paths]
        if isinstance(paths, (list, tuple)):
            self._release_temporary_attachments(
                [Path(path) for path in paths if isinstance(path, str) and path]
            )

    def _refresh_attachment_view(self) -> None:
        self.attachment_list.clear()
        for path in self._attachments:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            item.setSizeHint(AttachmentPreviewWidget.CARD_SIZE)
            self.attachment_list.addItem(item)
            preview = AttachmentPreviewWidget(
                path, self._attachment_display_detail(path), self.attachment_list
            )
            preview.removeRequested.connect(self.remove_attachment)
            self.attachment_list.setItemWidget(item, preview)
        self.attachment_list.setVisible(bool(self._attachments))
        self._update_attachment_buttons()

    def _update_attachment_buttons(self) -> None:
        has_attachments = bool(self._attachments)
        self.clear_attachments_button.setVisible(has_attachments)
        self.clear_attachments_button.setEnabled(has_attachments)

    @staticmethod
    def _attachment_key(path: Path) -> str:
        try:
            return str(path.resolve()).casefold()
        except OSError:
            return str(path).casefold()

    def _attachment_display_detail(self, path: Path) -> str:
        if not path.exists():
            return "파일 없음"
        try:
            size = path.stat().st_size
        except OSError:
            return "읽기 불가"
        kind = (
            "이미지" if path.suffix.lower() in IMAGE_ATTACHMENT_EXTENSIONS else "파일"
        )
        return f"{kind}, {self._format_bytes(size)}"

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
        process.setWorkingDirectory(
            invocation.working_dir or str(self.state.paths.root)
        )
        process.setProcessEnvironment(self._process_environment())
        process.finished.connect(
            lambda exit_code, _status, process=process: self._finish_status(
                process, exit_code
            )
        )
        process.errorOccurred.connect(
            lambda _error, process=process: self._fail_status_process(
                process,
                "상태 확인 명령을 시작하지 못했습니다.",
            )
        )
        self._set_status("확인 중", invocation.program)
        process.start()
        self._status_timeout_timer = self._run_later(
            invocation.timeout_seconds * 1000,
            lambda process=process: self._timeout_status(process),
        )

    def _finish_status(self, process: QProcess, exit_code: int) -> None:
        if self._status_process is not process:
            process.deleteLater()
            return
        self._status_process = None
        self._cancel_named_timer("_status_timeout_timer")
        try:
            stdout = decode_cli_output(bytes(process.readAllStandardOutput())).strip()
            stderr = decode_cli_output(bytes(process.readAllStandardError())).strip()
        except RuntimeError:
            return
        provider_key = str(
            process.property("provider_key") or self.current_provider_key()
        )
        allow_repair = bool(process.property("allow_repair"))
        if exit_code == 0:
            detail = stdout or stderr or "사용 가능"
            self._set_status("사용 가능", detail)
            process.deleteLater()
            if self._help_loaded_for != provider_key:
                self._run_later(0, self.refresh_cli_help_options)
            self._run_later(
                0,
                lambda provider_key=provider_key, force=allow_repair: (
                    self._ensure_model_catalog_fresh(
                        provider_key,
                        force=force,
                    )
                ),
            )
            return

        detail = "\n".join(part for part in (stderr, stdout) if part).strip()
        if is_blocking_cli_configuration_error(provider_key, detail):
            repair = (
                repair_cli_configuration_error(provider_key, detail)
                if allow_repair
                else None
            )
            if repair is not None and repair.repaired:
                self._append_block("시스템", repair.message)
                self._set_status("CLI 설정 자동 복구", repair.message)
                process.deleteLater()
                self._run_later(200, self.refresh_provider_status)
                return
            message = diagnose_cli_error(provider_key, detail)
            if repair is not None and repair.attempted and repair.message:
                message = f"{repair.message}\n\n{message}"
            self._set_status("CLI 설정 오류", message)
        else:
            self._set_status("로그인 필요", diagnose_cli_error(provider_key, detail))
        process.deleteLater()

    def _fail_status_process(self, process: QProcess, message: str) -> None:
        if self._status_process is not process:
            return
        self._status_process = None
        self._cancel_named_timer("_status_timeout_timer")
        process.deleteLater()
        self._set_status("상태 확인 실패", message)

    def _timeout_status(self, process: QProcess) -> None:
        if self._status_process is not process:
            return
        self._status_timeout_timer = None
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                self._set_status(
                    "상태 확인 시간 초과", "상태 확인 명령이 끝나지 않았습니다."
                )
        except RuntimeError:
            if self._status_process is process:
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
        process.setWorkingDirectory(
            invocation.working_dir or str(self.state.paths.root)
        )
        process.setProcessEnvironment(self._process_environment())
        process.readyReadStandardOutput.connect(
            lambda process=process: self._read_help_stdout(process)
        )
        process.readyReadStandardError.connect(
            lambda process=process: self._read_help_stderr(process)
        )
        process.finished.connect(
            lambda exit_code, _status, process=process: self._finish_help_options(
                process, exit_code
            )
        )
        process.errorOccurred.connect(
            lambda _error, process=process: self._fail_help_options(
                process,
                "CLI help 명령을 시작하지 못했습니다.",
            )
        )
        self.help_status_label.setText(
            f"--help 불러오는 중: {subprocess.list2cmdline([invocation.program, *invocation.args])}"
        )
        process.start()
        self._help_timeout_timer = self._run_later(
            invocation.timeout_seconds * 1000,
            lambda process=process: self._timeout_help_options(process),
        )

    def _read_help_stdout(self, process: QProcess) -> None:
        if self._help_process is not process:
            return
        self._help_stdout += bytes(process.readAllStandardOutput())

    def _read_help_stderr(self, process: QProcess) -> None:
        if self._help_process is not process:
            return
        self._help_stderr += bytes(process.readAllStandardError())

    def _finish_help_options(self, process: QProcess, exit_code: int) -> None:
        if self._help_process is not process:
            process.deleteLater()
            return
        self._help_process = None
        self._cancel_named_timer("_help_timeout_timer")
        provider_key = str(
            process.property("provider_key") or self.current_provider_key()
        )
        process.deleteLater()
        help_text = "\n".join(
            part.strip()
            for part in (
                decode_cli_output(self._help_stdout),
                decode_cli_output(self._help_stderr),
            )
            if part.strip()
        )
        self.raw_help_edit.setPlainText(help_text)
        options = self._populate_help_options(help_text)
        self._help_loaded_for = provider_key if options else ""
        if options:
            self.help_status_label.setText(
                f"{PROVIDER_SPECS[provider_key].display_name}에서 선택 가능한 기능 {len(options)}개를 불러왔습니다."
            )
        elif exit_code == 0:
            self.help_status_label.setText(
                "선택 가능한 기능을 자동으로 찾지 못했습니다. 필요한 경우 위 입력란에 직접 설정할 수 있습니다."
            )
        else:
            self.help_status_label.setText(
                "사용 가능한 기능을 불러오지 못했습니다. 연결 상태를 확인한 뒤 다시 시도하세요."
            )

    def _fail_help_options(self, process: QProcess, message: str) -> None:
        if self._help_process is not process:
            return
        self._help_process = None
        self._cancel_named_timer("_help_timeout_timer")
        process.deleteLater()
        self.help_status_label.setText(message)
        self._populate_help_options("")

    def _timeout_help_options(self, process: QProcess) -> None:
        if self._help_process is not process:
            return
        self._help_timeout_timer = None
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                self.help_status_label.setText("help 명령 시간이 초과되었습니다.")
        except RuntimeError:
            if self._help_process is process:
                self._help_process = None

    def _populate_help_options(self, help_text: str) -> list[CliHelpOption]:
        options = extra_arg_options_from_help(help_text) if help_text.strip() else []
        self.option_combo.blockSignals(True)
        self.option_combo.clear()
        if options:
            for option in options:
                self.option_combo.addItem(self._help_option_label(option), option)
        else:
            self.option_combo.addItem("선택 가능한 기능 없음", None)
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
            self.help_status_label.setText(
                "연결 상태를 확인하면 선택 가능한 기능을 자동으로 불러옵니다."
            )
            self._populate_help_options("")

    def _handle_help_option_changed(self) -> None:
        option = self._current_help_option()
        if option is None:
            self.option_value_edit.setEnabled(False)
            self.option_value_edit.clear()
            self.option_value_edit.setPlaceholderText(
                "선택한 기능에 설정값이 필요한 경우 입력합니다"
            )
            self.option_description.setText(
                "기능을 불러오면 쉬운 한국어 설명과 입력 예시를 확인할 수 있습니다."
            )
            return
        self.option_value_edit.setEnabled(option.takes_value)
        self.option_value_edit.setPlaceholderText(
            self._help_option_value_placeholder(option)
        )
        if not option.takes_value:
            self.option_value_edit.clear()
        self.option_description.setText(self._help_option_korean_description(option))

    def add_selected_extra_arg(self) -> None:
        option = self._current_help_option()
        if option is None:
            return
        value = self.option_value_edit.text().strip()
        if option.takes_value and not value:
            QMessageBox.warning(
                self, "설정값 필요", "선택한 기능에 사용할 설정값을 입력하세요."
            )
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
        title, _description, _example = self._help_option_korean_info(option)
        return title

    @staticmethod
    def _help_option_korean_info(option: CliHelpOption) -> tuple[str, str, str]:
        if option.flag in CLI_OPTION_KOREAN_HELP:
            return CLI_OPTION_KOREAN_HELP[option.flag]
        title = "기타 고급 설정"
        description = (
            "CLI에서 자동으로 찾은 고급 설정입니다. "
            "아래 실제 옵션과 원문 설명을 확인한 뒤 필요한 경우에만 적용하세요."
        )
        return title, description, ""

    @classmethod
    def _help_option_korean_description(cls, option: CliHelpOption) -> str:
        title, description, example = cls._help_option_korean_info(option)
        visible_flag = option.flag
        if option.short_flag:
            visible_flag = f"{option.short_flag}, {option.flag}"
        value_hint = cls._localized_value_hint(option.value_hint)
        value_note = (
            f"값: 필요 ({value_hint})" if option.takes_value else "값: 필요 없음"
        )
        lines = [
            title,
            f"실제 CLI 옵션: {visible_flag}",
            value_note,
            "",
            description,
        ]
        if example:
            lines.extend(["", f"입력 예시: {example}"])
        return "\n".join(lines).strip()

    @classmethod
    def _help_option_value_placeholder(cls, option: CliHelpOption) -> str:
        if not option.takes_value:
            return "이 옵션은 값을 입력하지 않습니다."
        _title, _description, example = cls._help_option_korean_info(option)
        if example:
            return example
        return cls._localized_value_hint(option.value_hint) or "옵션 값을 입력하세요."

    @staticmethod
    def _localized_value_hint(value_hint: str) -> str:
        hint = str(value_hint or "").strip()
        if not hint:
            return "값"
        return CLI_VALUE_HINT_KOREAN.get(hint, "입력값")

    def _split_extra_args(self) -> list[str]:
        text = self.extra_args_edit.text().strip()
        if not text:
            return []
        try:
            return shlex.split(text)
        except ValueError:
            return [part for part in text.split() if part.strip()]

    def _blocked_direct_extra_args(self, tokens: list[str]) -> list[str]:
        blocked: list[str] = []
        for token in tokens:
            normalized = token.split("=", 1)[0]
            is_claude_continue = (
                self.current_provider_key() == "claude" and normalized == "-c"
            )
            if (
                normalized in BLOCKED_DIRECT_EXTRA_ARG_FLAGS or is_claude_continue
            ) and normalized not in blocked:
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

    def _remove_extra_arg(
        self, tokens: list[str], flag: str, takes_value: bool
    ) -> bool:
        if flag not in tokens:
            return False
        index = tokens.index(flag)
        del tokens[index]
        if takes_value and index < len(tokens) and not tokens[index].startswith("-"):
            del tokens[index]
        return True

    def _runtime_provider_config(
        self, config: AiProviderConfig, attachment_args: list[str]
    ) -> AiProviderConfig:
        return AiProviderConfig(
            key=config.key,
            enabled=config.enabled,
            command_path=config.command_path,
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            speed=config.speed,
            role_prompt=config.role_prompt,
            extra_args=[
                *config.extra_args,
                *self._codex_runtime_config_args(config),
                *attachment_args,
            ],
            timeout_seconds=config.timeout_seconds,
        )

    @staticmethod
    def _codex_runtime_config_args(config: AiProviderConfig) -> list[str]:
        if config.key != "codex":
            return []
        args: list[str] = []
        reasoning_effort = config.reasoning_effort.strip()
        if reasoning_effort in REASONING_EFFORT_ORDER:
            args.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        speed = config.speed.strip()
        if speed in {"fast", "flex"}:
            args.extend(["-c", f'service_tier="{speed}"'])
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
                sections.append(
                    f"이미지 첨부: {path} ({self._safe_file_size(path)}). CLI --image 인자로 함께 전달됨."
                )
                continue
            if suffix in IMAGE_ATTACHMENT_EXTENSIONS:
                sections.append(
                    f"이미지 첨부: {path} ({self._safe_file_size(path)}). 현재 제공자에는 경로만 공유됨."
                )
                continue

            text, truncated = self._read_attachment_text(path)
            if text is None:
                sections.append(
                    f"첨부 파일: {path} ({self._safe_file_size(path)}). 텍스트로 읽을 수 없어 경로만 공유됨."
                )
                continue

            remaining = MAX_ATTACHMENT_CONTEXT_CHARS - used_chars
            if remaining <= 0:
                sections.append(
                    f"첨부 파일 생략: {path}. 첨부 컨텍스트 한도를 초과했습니다."
                )
                continue
            if len(text) > remaining:
                text = text[:remaining]
                truncated = True
            used_chars += len(text)
            note = "\n[첨부 내용 일부만 포함됨]" if truncated else ""
            sections.append(
                f"첨부 파일: {path}\n--- 첨부 내용 시작 ---\n{text.rstrip()}\n--- 첨부 내용 끝 ---{note}"
            )
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
        control_count = sum(
            1 for char in text if ord(char) < 32 and char not in "\r\n\t\f\b"
        )
        return control_count > max(8, len(text) // 80)

    def _display_prompt_with_attachments(
        self, prompt: str, attachments: list[Path]
    ) -> str:
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
        if NETWORK_STATUS_WORD_RE.search(
            normalized
        ) or NETWORK_STATUS_KOREAN_SHORT_RE.search(normalized):
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
        if "프로파일" in normalized and any(
            keyword in normalized
            for keyword in ("장비", "점검", "백업", "inspection", "backup")
        ):
            categories.add("inspector")
        if "백업" in normalized and any(
            keyword in normalized
            for keyword in ("장비", "프로파일", "장비 목록", "인벤토리", "점검")
        ):
            categories.add("inspector")
        if "점검" in normalized and any(
            keyword in normalized
            for keyword in ("장비", "프로파일", "장비 목록", "인벤토리", "백업")
        ):
            categories.add("inspector")
        if "프로파일" in normalized and any(
            keyword in normalized
            for keyword in ("cli", "설정", "스위치", "라우터", "config")
        ):
            categories.add("config_builder")
        if any(keyword in normalized for keyword in ("프로파일", "profile")) and any(
            keyword in normalized
            for keyword in (
                "ip",
                "네트워크",
                "전송",
                "ftp",
                "scp",
                "cli",
                "설정",
                "저장된",
            )
        ):
            categories.add("profiles")
        if any(
            keyword in normalized for keyword in ("ftp", "scp", "sftp", "tftp")
        ) and any(
            keyword in normalized
            for keyword in (
                "전송",
                "프로파일",
                "서버",
                "클라이언트",
                "업로드",
                "다운로드",
            )
        ):
            categories.add("transfer")
        if categories:
            categories.add("overview")
        return categories

    def _base_internal_context_sections(
        self, prompt: str, categories: set[str]
    ) -> list[tuple[str, str]]:
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

    @staticmethod
    def _context_was_cancelled(cancel_event: Event | None) -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    def _collect_internal_netops_context(
        self, prompt: str, cancel_event: Event | None = None
    ) -> str:
        categories = self._netops_context_categories(prompt)
        sections = self._base_internal_context_sections(prompt, categories)
        if "overview" in categories and not self._context_was_cancelled(cancel_event):
            self._collect_app_overview_sections(sections)
        if "profiles" in categories and not self._context_was_cancelled(cancel_event):
            self._collect_saved_profile_sections(sections)
        if "inspector" in categories and not self._context_was_cancelled(cancel_event):
            self._collect_inspector_context_sections(sections)
        if "config_builder" in categories and not self._context_was_cancelled(
            cancel_event
        ):
            self._collect_config_builder_context_sections(sections)
        if "transfer" in categories and not self._context_was_cancelled(cancel_event):
            self._collect_transfer_context_sections(sections)
        if "network" in categories and not self._context_was_cancelled(cancel_event):
            self._collect_network_context_sections(sections, prompt, cancel_event)
        return self._format_internal_context_sections(sections)

    def _collect_internal_network_context(
        self, prompt: str, cancel_event: Event | None = None
    ) -> str:
        categories = {"network", "overview"}
        sections = self._base_internal_context_sections(prompt, categories)
        if not self._context_was_cancelled(cancel_event):
            self._collect_app_overview_sections(sections)
        if not self._context_was_cancelled(cancel_event):
            self._collect_network_context_sections(sections, prompt, cancel_event)
        return self._format_internal_context_sections(sections)

    def _collect_network_context_sections(
        self,
        sections: list[tuple[str, str]],
        prompt: str,
        cancel_event: Event | None = None,
    ) -> None:
        adapters = self._collect_network_adapters_section(sections)
        if self._context_was_cancelled(cancel_event):
            return
        self._collect_gateway_ping_sections(sections, adapters)
        if self._context_was_cancelled(cancel_event):
            return
        self._collect_external_connectivity_sections(sections)
        if self._context_was_cancelled(cancel_event):
            return
        self._collect_dns_section(sections)
        if self._context_was_cancelled(cancel_event):
            return
        self._collect_public_ip_section(sections)
        if self._context_was_cancelled(cancel_event):
            return
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
                        "- 장비 점검/백업: Excel 장비 목록 기반 장비 접속, 점검 명령, 백업 명령, 사용자 명령 실행",
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

    def _collect_transfer_context_sections(
        self, sections: list[tuple[str, str]]
    ) -> None:
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

    def _collect_inspector_context_sections(
        self, sections: list[tuple[str, str]]
    ) -> None:
        try:
            data_root = getattr(
                getattr(self.state, "paths", None), "data_root", Path.cwd()
            )
            service = InspectorService(
                work_dir=Path(data_root) / "inspector" / "runs",
                user_data_dir=Path(data_root) / "inspector",
            )
            profiles = service.supported_profile_definitions()
            lines = [
                f"지원 프로파일: {len(profiles)}개",
                f"사용자 custom_rules.yaml: {self._safe_context_path(service.custom_rules_path)}",
                f"사용자 custom_parsers 폴더: {self._safe_context_path(service.custom_parsers_dir)}",
                "장비 목록 필수 컬럼: ip, vendor, os, connection_type, port, password",
                "선택 컬럼: username, enable_password",
                "실행 모드: inspection, backup, inspection_backup, custom_commands",
                "프로파일 생성 요청 시 custom_rules.yaml 형식으로 inspection_commands, backup_commands, parsing_rules, connection_overrides를 작성하세요.",
            ]
            for profile in profiles[:16]:
                lines.append(
                    "- "
                    f"{profile.get('display_name') or profile.get('key')}: "
                    f"commands={profile.get('command_count', 0)}, "
                    f"backup={'yes' if profile.get('has_backup') else 'no'}, "
                    f"columns={len(profile.get('output_columns') or [])}, "
                    f"source={profile.get('source', '-')}"
                )
            if len(profiles) > 16:
                lines.append(f"... 프로파일 {len(profiles) - 16}개 생략")
            sections.append(("장비 점검/백업 컨텍스트", "\n".join(lines)))
        except Exception as exc:
            sections.append(("장비 점검/백업 컨텍스트", f"실패: {exc}"))

    def _collect_config_builder_context_sections(
        self, sections: list[tuple[str, str]]
    ) -> None:
        try:
            data_root = getattr(
                getattr(self.state, "paths", None), "data_root", Path.cwd()
            )
            service = ConfigBuilderService(
                user_data_dir=Path(data_root) / "config_builder"
            )
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
                    sample = (
                        service.sample_device_values_for_profile(profile)
                        if profile is not None
                        else None
                    )
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

    def _collect_network_adapters_section(
        self, sections: list[tuple[str, str]]
    ) -> list[Any]:
        adapter_service = getattr(self.state, "network_interface_service", None)
        if adapter_service is None:
            sections.append(
                (
                    "네트워크 어댑터",
                    "실패: NetworkInterfaceService가 준비되지 않았습니다.",
                )
            )
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

    def _collect_gateway_ping_sections(
        self, sections: list[tuple[str, str]], adapters: list[Any]
    ) -> None:
        ping_service = getattr(self.state, "ping_service", None)
        if ping_service is None:
            sections.append(
                ("게이트웨이 Ping", "실패: PingService가 준비되지 않았습니다.")
            )
            return
        gateways: list[str] = []
        for adapter in adapters:
            gateway = str(getattr(adapter, "gateway", "") or "").strip()
            if gateway and gateway not in {"-", "0.0.0.0"} and gateway not in gateways:
                gateways.append(gateway)
        if not gateways:
            sections.append(
                ("게이트웨이 Ping", "게이트웨이가 설정된 어댑터를 찾지 못했습니다.")
            )
            return
        lines: list[str] = []
        for gateway in gateways[:4]:
            lines.append(
                self._run_ping_check(ping_service, f"Gateway {gateway}", gateway)
            )
        sections.append(("게이트웨이 Ping", "\n".join(lines)))

    def _collect_external_connectivity_sections(
        self, sections: list[tuple[str, str]]
    ) -> None:
        ping_service = getattr(self.state, "ping_service", None)
        if ping_service is None:
            sections.append(
                ("외부 연결 Ping", "실패: PingService가 준비되지 않았습니다.")
            )
            return
        lines = [
            self._run_ping_check(ping_service, label, target)
            for label, target in BASIC_NETWORK_DIAGNOSTIC_TARGETS
        ]
        sections.append(("외부 연결 Ping", "\n".join(lines)))

    def _collect_dns_section(self, sections: list[tuple[str, str]]) -> None:
        dns_service = getattr(self.state, "dns_service", None)
        if dns_service is None:
            sections.append(("DNS 조회", "실패: DnsService가 준비되지 않았습니다."))
            return
        try:
            result = dns_service.lookup("google.com", "A")
            sections.append(
                ("DNS 조회 google.com A", self._format_operation_result(result))
            )
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

    def _collect_optional_command_sections(
        self, sections: list[tuple[str, str]], prompt: str
    ) -> None:
        trace_service = getattr(self.state, "trace_service", None)
        if trace_service is None:
            return
        normalized = prompt.casefold()
        try:
            sections.append(
                (
                    "route print",
                    self._format_operation_result(trace_service.run_route_print()),
                )
            )
        except Exception as exc:
            sections.append(("route print", f"실패: {exc}"))
        if any(
            keyword in normalized for keyword in ("ipconfig", "상세", "전체", "어댑터")
        ):
            try:
                sections.append(
                    (
                        "ipconfig /all",
                        self._format_operation_result(trace_service.run_ipconfig_all()),
                    )
                )
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

    def _run_netops_chat_action(
        self,
        action: NetOpsChatAction,
        approved: bool = False,
        cancel_event: Event | None = None,
    ) -> str:
        sections: list[tuple[str, str]] = [
            ("NetOps 기능", action.title),
        ]
        scope = self._netops_action_scope(action)
        if scope:
            sections.append(("요청 범위", scope))
        sections.append(("실행 시각", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        try:
            sections.append(
                (
                    "실행 결과",
                    self._execute_netops_chat_action(
                        action,
                        approved=approved,
                        cancel_event=cancel_event,
                    ),
                )
            )
        except Exception as exc:
            sections.append(("실행 결과", f"실패: {exc}"))
        return self._format_netops_tool_sections(sections)

    def _execute_netops_chat_action(
        self,
        action: NetOpsChatAction,
        *,
        approved: bool = False,
        cancel_event: Event | None = None,
    ) -> str:
        actions = self._expanded_netops_chat_actions(action)
        output: list[str] = []
        for item in actions:
            if cancel_event is not None and cancel_event.is_set():
                output.append("실행 중지 요청으로 나머지 항목을 실행하지 않았습니다.")
                break
            call = tool_call_from_netops_action(item, user_intent=item.title)
            decision, result = self._assistant_executor.execute(
                call,
                self._assistant_policy_context(approved=approved),
                cancel_event=cancel_event,
            )
            if not decision.allowed:
                item_result = self._format_assistant_policy_decision(decision)
            elif result is None:
                item_result = "실패: 등록된 도구 실행 결과가 없습니다."
            else:
                item_result = self._format_assistant_tool_result(result)
            if len(actions) == 1:
                return item_result
            output.append(f"### {item.title}\n{item_result}")
        return "\n\n".join(output).strip() or "실행 결과가 없습니다."

    @staticmethod
    def _expanded_netops_chat_actions(
        action: NetOpsChatAction,
    ) -> list[NetOpsChatAction]:
        if action.endpoints:
            return [
                replace(
                    action,
                    title=f"TCP 포트 확인: {target}:{port}",
                    target=target,
                    port=port,
                    targets=(target,),
                    ports=(port,),
                    endpoints=(),
                )
                for target, port in action.endpoints
            ]
        repeatable_kinds = {
            "dns_lookup": "DNS 조회",
            "tracert": "tracert",
            "pathping": "pathping",
            "subnet_calculate": "Subnet calculate",
            "oui_lookup": "OUI 제조사 조회",
        }
        if action.kind in repeatable_kinds and len(action.targets) > 1:
            prefix = repeatable_kinds[action.kind]
            return [
                replace(
                    action,
                    title=f"{prefix}: {target}",
                    target=target,
                    targets=(target,),
                )
                for target in action.targets
            ]
        return [action]

    @staticmethod
    def _netops_action_scope(action: NetOpsChatAction) -> str:
        if action.endpoints:
            return "\n".join(f"- {target}:{port}" for target, port in action.endpoints)
        lines: list[str] = []
        if action.targets:
            lines.extend(f"- 대상: {target}" for target in action.targets)
        if action.ports:
            lines.append("- 포트: " + ", ".join(str(port) for port in action.ports))
        if action.continuous:
            lines.append("- 실행 방식: 중지할 때까지 연속 실행")
        elif action.count > 0:
            lines.append(f"- 반복 횟수: {action.count}회")
        if action.timeout_ms > 0:
            lines.append(f"- 제한 시간: {action.timeout_ms} ms")
        return "\n".join(lines)

    def _assistant_policy_context(self, *, approved: bool = False) -> PolicyContext:
        return PolicyContext(
            is_admin=bool(getattr(self.state, "is_admin", False)),
            actor="netops_assistant_tab",
            approved=approved,
        )

    @staticmethod
    def _format_assistant_tool_result(result: ToolResult) -> str:
        text = (
            result.to_text()
            if hasattr(result, "to_text")
            else str(getattr(result, "output", "") or "")
        )
        error = str(getattr(result, "error", "") or "").strip()
        if not bool(getattr(result, "success", False)) and error:
            text = "\n".join(part for part in (text, error) if part)
        return text.strip() or (
            "성공" if bool(getattr(result, "success", False)) else "실패"
        )

    @staticmethod
    def _format_assistant_policy_decision(decision: PolicyDecision) -> str:
        status = str(getattr(decision, "status", "") or "")
        reason = str(getattr(decision, "reason", "") or "").strip()
        metadata = dict(getattr(decision, "metadata", {}) or {})
        tool_name = str(metadata.get("tool_name", "") or "")
        permission = getattr(decision, "permission_class", None)
        permission_text = str(getattr(permission, "value", permission or "") or "")
        lines = ["정책 결정: " + (status or "denied")]
        if tool_name:
            lines.append(f"도구: {tool_name}")
        if permission_text:
            lines.append(f"권한 등급: {permission_text}")
        if reason:
            lines.append(f"사유: {reason}")
        if bool(getattr(decision, "requires_approval", False)):
            lines.append("사용자 승인이 필요해서 실행하지 않았습니다.")
        if bool(getattr(decision, "blocked", False)):
            lines.append("차단되어 실행하지 않았습니다.")
        return "\n".join(lines)

    def _confirm_netops_chat_action(self, action: NetOpsChatAction) -> bool:
        representative_action = self._expanded_netops_chat_actions(action)[0]
        call = tool_call_from_netops_action(
            representative_action, user_intent=action.title
        )
        descriptor = self._assistant_registry.resolve(call)
        decision = self._assistant_executor.evaluate(
            call, self._assistant_policy_context(approved=False)
        )

        if decision.allowed:
            return True

        if decision.blocked:
            message = (
                "NetOps 어시스턴트 정책에 의해 실행할 수 없습니다.\n\n"
                f"작업: {action.title}\n"
                f"사유: {decision.reason}"
            )
            if (
                descriptor is not None
                and descriptor.admin_required
                and not bool(getattr(self.state, "is_admin", False))
            ):
                message += "\n\n이 작업은 Windows 관리자 권한이 필요합니다. 왼쪽 아래 관리자 버튼으로 관리자 권한으로 다시 실행한 뒤 요청해 주세요."
            title = (
                "관리자 권한 필요" if "관리자 권한" in message else "NetOps 실행 차단"
            )
            QMessageBox.warning(self, title, message)
            self._append_block("시스템", message)
            return False

        if not decision.requires_approval:
            message = (
                "NetOps 어시스턴트 정책이 이 요청을 허용하지 않았습니다.\n\n"
                f"작업: {action.title}\n"
                f"사유: {decision.reason or '알 수 없는 정책 결정'}"
            )
            QMessageBox.warning(self, "NetOps 실행 불가", message)
            self._append_block("시스템", message)
            return False

        display_name = (
            descriptor.display_name if descriptor is not None else action.title
        )
        permission = getattr(
            decision.permission_class, "value", str(decision.permission_class or "")
        )
        risk_level = (
            descriptor.risk_level if descriptor is not None else action.risk_level
        )
        impact = (descriptor.impact if descriptor is not None else "") or action.impact
        reversibility = descriptor.reversibility if descriptor is not None else ""
        lines = [
            "이 요청은 NetOps 어시스턴트가 등록된 도구를 통해 시스템 또는 설정을 변경합니다.",
            "",
            f"작업: {action.title}",
            f"도구: {display_name}",
            f"권한 등급: {permission or '-'}",
            f"리스크: {risk_level or '-'}",
        ]
        if impact:
            lines.extend(["", "영향:", impact])
        if reversibility:
            lines.extend(["", "복구/되돌리기:", reversibility])
        lines.extend(
            ["", "승인하면 즉시 실행됩니다. 취소하면 아무 작업도 하지 않습니다."]
        )
        confirmed = (
            QMessageBox.question(
                self,
                "NetOps 실행 승인",
                "\n".join(lines),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )
        if not confirmed:
            self._append_block(
                "시스템", f"사용자가 NetOps 변경 작업을 취소했습니다.\n{action.title}"
            )
        return confirmed

    def _required_state_service(self, attr_name: str, display_name: str) -> Any:
        service = getattr(self.state, attr_name, None)
        if service is None:
            raise RuntimeError(f"{display_name}가 준비되지 않았습니다.")
        return service

    @staticmethod
    def _format_tcp_check_result(result: Any) -> str:
        lines = [
            f"대상: {getattr(result, 'target', '-')}:{getattr(result, 'port', '-')}",
            f"상태: {getattr(result, 'status', '-')}",
            f"성공/시도: {getattr(result, 'successful', 0)}/{getattr(result, 'sent', 0)}",
            f"손실률: {float(getattr(result, 'packet_loss', 0) or 0):.0f}%",
        ]
        response_ms = getattr(result, "response_ms", None)
        if response_ms is not None:
            lines.append(f"평균 응답: {response_ms} ms")
        error = str(getattr(result, "error", "") or "").strip()
        if error:
            lines.append(f"오류: {error}")
        return "\n".join(lines)

    @staticmethod
    def _format_wireless_info(info: Any) -> str:
        return "\n".join(
            [
                f"인터페이스: {getattr(info, 'interface_name', '') or '-'}",
                f"설명: {getattr(info, 'description', '') or '-'}",
                f"상태: {getattr(info, 'state', '') or '-'}",
                f"SSID: {getattr(info, 'ssid', '') or '-'}",
                f"BSSID: {getattr(info, 'bssid', '') or '-'}",
                f"무선 규격: {getattr(info, 'radio_type', '') or '-'}",
                f"채널/대역: {getattr(info, 'channel', '') or '-'} / {getattr(info, 'band', '') or '-'}",
                f"신호: {getattr(info, 'signal_text', '-')}",
                f"송수신 속도: {getattr(info, 'receive_rate_mbps', '') or '-'} / {getattr(info, 'transmit_rate_mbps', '') or '-'} Mbps",
            ]
        )

    @staticmethod
    def _format_netops_tool_sections(sections: list[tuple[str, str]]) -> str:
        output: list[str] = ["NetOps Suite tool result"]
        for title, body in sections:
            text = str(body or "").strip()
            if text:
                output.append(f"\n## {title}\n{text}")
        return "\n".join(output).strip()

    def _format_internal_context_sections(self, sections: list[tuple[str, str]]) -> str:
        output: list[str] = ["NetOps Suite internal diagnostics snapshot"]
        total_chars = len(output[0])
        for title, body in sections:
            section = self._truncate_text(
                str(body or "").strip(), MAX_INTERNAL_CONTEXT_SECTION_CHARS
            )
            block = f"\n\n[{title}]\n{section or '-'}"
            if total_chars + len(block) > MAX_INTERNAL_CONTEXT_TOTAL_CHARS:
                output.append(
                    "\n\n[생략]\n내부 진단 컨텍스트 한도 때문에 이후 항목을 생략했습니다."
                )
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
        if self._login_preflight_active:
            return
        config = self.current_provider_config()
        health = inspect_provider(config)
        if not health.installed:
            QMessageBox.warning(
                self,
                "CLI 없음",
                f"{PROVIDER_SPECS[config.key].display_name}: {health.detail}",
            )
            return

        self._login_preflight_active = True
        self.login_button.setEnabled(False)
        self.login_button.setText("로그인 준비 중…")
        self._set_status(
            "로그인 준비 중", "Codex CLI 설정과 로그인 명령을 확인하고 있습니다."
        )
        self._job_runner.start(
            self._prepare_provider_login,
            config,
            on_result=self._complete_provider_login_preflight,
            on_error=self._fail_provider_login_preflight,
            on_finished=self._finish_provider_login_preflight,
        )

    def _prepare_provider_login(self, config: AiProviderConfig) -> dict[str, Any]:
        repair_messages: list[str] = []
        for _attempt in range(3):
            preflight_error = self._login_preflight_error(config)
            if not preflight_error:
                return {
                    "config": config,
                    "repair_messages": repair_messages,
                    "error": "",
                }
            repair = repair_cli_configuration_error(config.key, preflight_error)
            if repair.repaired:
                repair_messages.append(repair.message)
                continue
            message = preflight_error
            if repair.attempted and repair.message:
                message = f"{repair.message}\n\n{preflight_error}"
            return {
                "config": config,
                "repair_messages": repair_messages,
                "error": message,
            }
        return {
            "config": config,
            "repair_messages": repair_messages,
            "error": "Codex 설정을 자동 복구했지만 CLI 상태 확인이 계속 실패합니다.",
        }

    def _complete_provider_login_preflight(self, result: object) -> None:
        if not isinstance(result, dict) or not isinstance(
            result.get("config"), AiProviderConfig
        ):
            self._fail_provider_login_preflight(
                "로그인 준비 결과 형식이 올바르지 않습니다."
            )
            return
        for message in result.get("repair_messages", []):
            if isinstance(message, str) and message.strip():
                self._append_block("시스템", message)
        error = str(result.get("error", "") or "").strip()
        if error:
            self._set_status("CLI 설정 오류", error)
            self._append_block("오류", error)
            QMessageBox.warning(self, "CLI 설정 오류", error)
            return
        self._launch_provider_login(result["config"])

    def _launch_provider_login(self, config: AiProviderConfig) -> None:
        invocation = build_login_invocation(config, str(self.state.paths.root))
        command = subprocess.list2cmdline([invocation.program, *invocation.args])
        if sys.platform == "win32":
            started = QProcess.startDetached(
                "cmd.exe", ["/k", command], invocation.working_dir
            )
        else:
            started = QProcess.startDetached(
                invocation.program, invocation.args, invocation.working_dir
            )
        ok = bool(started[0]) if isinstance(started, tuple) else bool(started)
        if not ok:
            self._set_status("로그인 실행 실패", "로그인 터미널을 열지 못했습니다.")
            QMessageBox.warning(
                self, "로그인 실행 실패", "로그인 터미널을 열지 못했습니다."
            )
            return
        self._set_status("로그인 터미널 열림", invocation.program)
        self._append_block("시스템", f"로그인 터미널을 열었습니다.\n{command}")

    def _fail_provider_login_preflight(self, message: str) -> None:
        detail = str(message or "로그인 준비 중 오류가 발생했습니다.")
        self._set_status("로그인 준비 실패", detail)
        QMessageBox.warning(self, "로그인 준비 실패", detail)

    def _finish_provider_login_preflight(self) -> None:
        self._login_preflight_active = False
        self.login_button.setText("로그인 터미널")
        self.login_button.setEnabled(
            self._process is None and not self._context_collecting
        )

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
            completed = subprocess.run(
                [invocation.program, *invocation.args], **run_kwargs
            )
        except (OSError, subprocess.SubprocessError):
            return ""

        detail = "\n".join(
            part.strip()
            for part in (
                decode_cli_output(completed.stdout),
                decode_cli_output(completed.stderr),
            )
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
            QMessageBox.information(
                self, "준비 중", "NetOps 내부 진단 결과를 수집하는 중입니다."
            )
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
        netops_action = plan_netops_chat_action(prompt)
        health = inspect_provider(config) if netops_action is None else None
        if health is not None and not health.installed:
            QMessageBox.warning(self, "CLI 없음", health.detail)
            self._set_status("CLI 없음", health.detail)
            return

        try:
            attachment_context, attachment_args = self._attachment_context_and_args(
                config.key
            )
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
        }
        self._ensure_user_prompt_message(payload)
        if netops_action is not None:
            action_approved = self._confirm_netops_chat_action(netops_action)
            if not action_approved:
                self._set_status("NetOps 실행 취소", netops_action.title)
                self._release_payload_temporary_attachments(payload)
                return
            request_id = self._next_context_request_id()
            cancel_event = Event()
            self._pending_prompt_payload = payload
            self._context_collection_cancelled = False
            self._active_context_request = request_id
            self._context_cancel_event = cancel_event
            self._context_operation_label = netops_action.title
            self._set_preparing(True)
            self._set_status("NetOps 기능 실행 중", netops_action.title)
            self._set_working_status("NetOps 기능 실행 중")
            self._append_block(
                "시스템", f"NetOps Suite 기능을 실행합니다.\n{netops_action.title}"
            )
            self._job_runner.start(
                self._run_netops_chat_action,
                netops_action,
                action_approved,
                cancel_event,
                on_result=lambda result, request_id=request_id: (
                    self._continue_prompt_with_netops_action_result(
                        request_id,
                        result,
                    )
                ),
                on_error=lambda message, request_id=request_id: (
                    self._handle_netops_action_error(
                        request_id,
                        message,
                    )
                ),
                on_finished=lambda request_id=request_id: (
                    self._finish_internal_context_collection(request_id)
                ),
            )
            return

        if self._should_collect_internal_netops_context(prompt):
            request_id = self._next_context_request_id()
            cancel_event = Event()
            self._pending_prompt_payload = payload
            self._context_collection_cancelled = False
            self._active_context_request = request_id
            self._context_cancel_event = cancel_event
            self._context_operation_label = "내부 진단 수집"
            self._set_preparing(True)
            self._set_status(
                "내부 컨텍스트 수집 중", "NetOps Suite 기능 정보를 먼저 수집합니다."
            )
            self._set_working_status("NetOps 내부 컨텍스트 수집 중")
            self._job_runner.start(
                self._collect_internal_netops_context,
                prompt,
                cancel_event,
                on_result=lambda result, request_id=request_id: (
                    self._continue_prompt_with_internal_context(
                        request_id,
                        result,
                    )
                ),
                on_error=lambda message, request_id=request_id: (
                    self._handle_internal_context_error(
                        request_id,
                        message,
                    )
                ),
                on_finished=lambda request_id=request_id: (
                    self._finish_internal_context_collection(request_id)
                ),
            )
            return

        self._start_prompt_process(payload, "")

    def _next_context_request_id(self) -> int:
        self._context_request_generation += 1
        return self._context_request_generation

    def _is_active_context_request(self, request_id: int) -> bool:
        return self._active_context_request == request_id

    def _continue_prompt_with_netops_action_result(
        self, request_id: int, result_context: object
    ) -> None:
        if not self._is_active_context_request(request_id):
            return
        payload = self._pending_prompt_payload
        self._pending_prompt_payload = None
        if payload is None:
            return

        context_text = str(result_context or "").strip()
        if context_text:
            self._append_block("NetOps", self._truncate_text(context_text, 12000))

        config = payload["config"]
        health = inspect_provider(config)
        if not health.installed:
            self._clear_working_status()
            self._set_status("NetOps 기능 완료", f"AI CLI 없음: {health.detail}")
            self._append_block(
                "시스템",
                f"AI CLI를 찾지 못해 NetOps 실행 결과만 표시했습니다.\n{health.detail}",
            )
            self._release_payload_temporary_attachments(payload)
            return
        grounded_context = "\n\n".join(
            part
            for part in (
                context_text,
                (
                    "NetOps 실행 근거 규칙: 위 실행 결과에 실제로 포함된 대상과 상태만 설명하세요. "
                    "결과에 없는 대상이 실행되었거나 성공했다고 추정하지 말고, 시작되지 않은 "
                    "지속 실행 또는 백그라운드 모니터링을 실행 중이라고 말하지 마세요."
                ),
            )
            if part
        )
        self._start_prompt_process(payload, grounded_context)

    def _handle_netops_action_error(self, request_id: int, message: str) -> None:
        if not self._is_active_context_request(request_id):
            return
        payload = self._pending_prompt_payload
        self._pending_prompt_payload = None
        self._set_preparing(False)
        detail = f"NetOps Suite 기능 실행 중 오류가 발생했습니다: {message}"
        self._append_block("오류", detail)
        if payload is None:
            self._clear_working_status()
            return
        config = payload["config"]
        health = inspect_provider(config)
        if health.installed:
            self._start_prompt_process(payload, detail)
        else:
            self._clear_working_status()
            self._release_payload_temporary_attachments(payload)
            self._set_status("NetOps 기능 실패", detail)

    def _continue_prompt_with_internal_context(
        self, request_id: int, internal_context: object
    ) -> None:
        if not self._is_active_context_request(request_id):
            return
        payload = self._pending_prompt_payload
        self._pending_prompt_payload = None
        if payload is None:
            self._clear_working_status()
            return
        context_text = str(internal_context or "").strip()
        self._start_prompt_process(payload, context_text)

    def _handle_internal_context_error(self, request_id: int, message: str) -> None:
        if not self._is_active_context_request(request_id):
            return
        payload = self._pending_prompt_payload
        self._pending_prompt_payload = None
        self._set_preparing(False)
        if payload is None:
            self._clear_working_status()
            return
        detail = f"NetOps Suite 내부 진단 수집 중 오류가 발생했습니다: {message}"
        self._append_block("오류", detail)
        self._start_prompt_process(payload, detail)

    def _finish_internal_context_collection(self, request_id: int) -> None:
        if not self._is_active_context_request(request_id):
            return
        self._active_context_request = None
        self._context_cancel_event = None
        self._context_operation_label = ""
        self._context_collecting = False
        if self._process is None:
            self._set_preparing(False)
            self._clear_working_status()

    def _start_prompt_process(
        self, payload: dict[str, Any], internal_context: str
    ) -> None:
        prompt = str(payload["prompt"])
        config = payload["config"]
        attachment_context = str(payload.get("attachment_context", "") or "")
        attachment_args = list(payload.get("attachment_args", []))
        sent_attachments = list(payload.get("sent_attachments", []))
        combined_context = "\n\n".join(
            part for part in (attachment_context, internal_context.strip()) if part
        )
        session_id = self._provider_session_ids.get(config.key, "")

        try:
            runtime_config = self._runtime_provider_config(config, attachment_args)
            working_dir = str(self.state.paths.root)
            invocation_access: dict[str, object] = {}
            if config.key == "codex":
                (
                    sandbox_mode,
                    workspace_root,
                    writable_dirs,
                    working_dir,
                ) = self._codex_invocation_access()
                invocation_access = {
                    "codex_sandbox": sandbox_mode,
                    "codex_workspace_root": workspace_root,
                    "codex_writable_dirs": writable_dirs,
                }
            invocation = build_chat_invocation(
                runtime_config,
                prompt,
                context=combined_context,
                working_dir=working_dir,
                session_id=session_id,
                **invocation_access,
            )
        except ValueError as exc:
            self._clear_working_status()
            QMessageBox.warning(self, "요청 실행 불가", str(exc))
            self._append_block("오류", str(exc))
            self._set_status("요청 실행 불가", str(exc))
            self._release_payload_temporary_attachments(payload)
            return

        self._stdout_buffer = b""
        self._cli_error_text = ""
        self._stderr_text = ""
        self._stream_message_index = None
        self._ensure_user_prompt_message(payload)
        self.prompt_edit.setEnabled(True)
        self._set_working_status(
            f"{self._assistant_response_title(config)} 응답 대기 중"
        )
        self._set_running(True)
        self._set_status("실행 중", invocation.program)

        process = QProcess(self)
        self._process = process
        process.setProperty("provider_key", config.key)
        process.setProperty("response_title", self._assistant_response_title(config))
        process.setProperty("resume_session_id", session_id)
        process.setProperty("session_id_candidate", "")
        process.setProperty(
            "temporary_attachment_paths",
            self._temporary_attachment_paths(sent_attachments),
        )
        process.setProgram(invocation.program)
        process.setArguments(invocation.args)
        process.setWorkingDirectory(
            invocation.working_dir or str(self.state.paths.root)
        )
        process.setProcessEnvironment(self._process_environment())
        process.readyReadStandardOutput.connect(
            lambda process=process: self._read_stdout(process)
        )
        process.readyReadStandardError.connect(
            lambda process=process: self._read_stderr(process)
        )
        process.finished.connect(
            lambda exit_code, _status, process=process: self._finish_prompt(
                process, exit_code
            )
        )
        process.errorOccurred.connect(
            lambda _error, process=process: self._fail_prompt(
                process,
                "CLI 프로세스를 시작하지 못했습니다.",
            )
        )
        process.start()
        if invocation.stdin_text:
            process.write(invocation.stdin_text.encode("utf-8"))
            process.closeWriteChannel()
        self._prompt_timeout_timer = self._run_later(
            invocation.timeout_seconds * 1000,
            lambda process=process: self._timeout_prompt(process),
        )

    def _ensure_user_prompt_message(self, payload: dict[str, Any]) -> None:
        if bool(payload.get("_user_message_appended", False)):
            return
        prompt = str(payload.get("prompt", "") or "")
        sent_attachments = list(payload.get("sent_attachments", []))
        self._append_block(
            "사용자", self._display_prompt_with_attachments(prompt, sent_attachments)
        )
        payload["_user_message_appended"] = True
        if not bool(payload.get("_composer_consumed", False)):
            self.prompt_edit.clear()
            if sent_attachments:
                sent_keys = {
                    self._attachment_key(Path(path)) for path in sent_attachments
                }
                self._attachments = [
                    path
                    for path in self._attachments
                    if self._attachment_key(path) not in sent_keys
                ]
                self._refresh_attachment_view()
            payload["_composer_consumed"] = True

    def _release_payload_temporary_attachments(
        self, payload: dict[str, Any] | None
    ) -> None:
        if not payload or bool(payload.get("_temporary_attachments_released", False)):
            return
        payload["_temporary_attachments_released"] = True
        sent_attachments = list(payload.get("sent_attachments", []))
        self._release_temporary_attachments(sent_attachments)

    def _read_stdout(self, process: QProcess) -> None:
        if self._process is not process:
            return
        self._stdout_buffer += bytes(process.readAllStandardOutput())
        while b"\n" in self._stdout_buffer:
            line, rest = self._stdout_buffer.split(b"\n", 1)
            self._stdout_buffer = rest
            decoded_line = decode_cli_output(line.rstrip(b"\r"))
            provider_key = str(
                process.property("provider_key") or self.current_provider_key()
            )
            session_id = extract_cli_session_id(provider_key, decoded_line)
            if session_id:
                process.setProperty("session_id_candidate", session_id)
            if should_ignore_cli_output_text(decoded_line):
                continue
            cli_error = extract_error_from_cli_line(decoded_line)
            if cli_error:
                self._cli_error_text = cli_error
                continue
            chunk = extract_assistant_text_from_cli_line(provider_key, decoded_line)
            if chunk and not should_ignore_cli_output_text(chunk):
                self._append_stream(
                    chunk + "\n", self._response_title_for_process(process)
                )

    def _read_stderr(self, process: QProcess) -> None:
        if self._process is not process:
            return
        self._stderr_text += decode_cli_output(bytes(process.readAllStandardError()))

    def _finish_prompt(self, process: QProcess, exit_code: int) -> None:
        if self._process is not process:
            self._release_process_temporary_attachments(process)
            process.deleteLater()
            return
        provider_key = self.current_provider_key()
        self._process = None
        self._cancel_named_timer("_prompt_timeout_timer")
        provider_key = str(process.property("provider_key") or provider_key)
        response_title = self._response_title_for_process(process)
        session_id_candidate = str(
            process.property("session_id_candidate") or ""
        ).strip()
        self._release_process_temporary_attachments(process)
        process.deleteLater()
        tail_text = decode_cli_output(self._stdout_buffer.strip())
        tail_session_id = extract_cli_session_id(provider_key, tail_text)
        if tail_session_id:
            session_id_candidate = tail_session_id
        tail_error = extract_error_from_cli_line(tail_text)
        if tail_error:
            self._cli_error_text = tail_error
        else:
            tail = extract_assistant_text_from_cli_line(provider_key, tail_text)
            if tail and not should_ignore_cli_output_text(tail):
                self._append_stream(tail + "\n", response_title)
        if exit_code == 0 and not self._cli_error_text:
            if session_id_candidate:
                self._provider_session_ids[provider_key] = session_id_candidate
            self._set_status("사용 가능", "요청 완료")
        else:
            stderr_detail, cache_warning = split_codex_model_cache_warning(
                self._stderr_text
            )
            detail = (
                self._cli_error_text.strip()
                or stderr_detail
                or f"CLI가 종료 코드 {exit_code}로 끝났습니다."
            )
            if cache_warning and not self._cli_error_text and not stderr_detail:
                detail = (
                    "Codex 모델 캐시가 현재 CLI 버전과 호환되지 않습니다. "
                    "모델 목록을 새로고침한 뒤 요청을 다시 보내세요."
                )
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
        self._cli_error_text = ""
        self._stream_message_index = None
        self._set_running(False)

    def _fail_prompt(self, process: QProcess, message: str) -> None:
        if self._process is not process:
            self._release_process_temporary_attachments(process)
            return
        self._append_block("오류", message)
        self._set_status("실패", message)
        self._process = None
        self._cancel_named_timer("_prompt_timeout_timer")
        self._release_process_temporary_attachments(process)
        process.deleteLater()
        self._stream_message_index = None
        self._set_running(False)

    def _timeout_prompt(self, process: QProcess) -> None:
        if self._process is not process:
            return
        self._prompt_timeout_timer = None
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                self._clear_working_status()
                self._append_block("시스템", "요청 시간이 초과되어 중지했습니다.")
        except RuntimeError:
            if self._process is process:
                self._process = None
                self._release_process_temporary_attachments(process)
                self._clear_working_status()

    def cancel_prompt(self) -> None:
        if self._context_collecting:
            if self._context_cancel_event is not None:
                self._context_cancel_event.set()
            self._context_request_generation += 1
            self._active_context_request = None
            self._context_cancel_event = None
            self._context_collection_cancelled = True
            payload = self._pending_prompt_payload
            self._pending_prompt_payload = None
            self._set_preparing(False)
            self._clear_working_status()
            self._release_payload_temporary_attachments(payload)
            operation_label = self._context_operation_label
            self._context_operation_label = ""
            self._append_block(
                "시스템",
                (
                    f"{operation_label} 실행을 중지했습니다."
                    if operation_label
                    else "진행 중인 요청을 중지했습니다."
                ),
            )
            self._set_status("중지됨")
            return
        if self._process is None:
            return
        process = self._process
        self._process = None
        self._cancel_named_timer("_prompt_timeout_timer")
        try:
            process.blockSignals(True)
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
        except RuntimeError:
            pass
        self._release_process_temporary_attachments(process)
        process.deleteLater()
        self._append_block("시스템", "사용자가 요청을 중지했습니다.")
        self._set_status("중지됨")
        self._set_running(False)

    def export_session(self) -> Path | None:
        text = self._plain_transcript_text().strip()
        if not text:
            QMessageBox.information(
                self, "저장할 내용 없음", "저장할 대화 내용이 없습니다."
            )
            return None
        exports_dir = Path(self.state.paths.exports_dir)
        suggested_path = timestamped_export_path(
            exports_dir, "ai_chat_session", "md"
        )
        selected_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "대화 내용 저장",
            str(suggested_path),
            "Markdown Files (*.md);;Text Files (*.txt)",
        )
        if not selected_path:
            return None
        path = Path(selected_path)
        if path.suffix.casefold() not in {".md", ".txt"}:
            suffix = ".txt" if "*.txt" in selected_filter else ".md"
            path = path.with_suffix(suffix) if path.suffix else Path(f"{path}{suffix}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "# NetOps 어시스턴트 세션\n\n" + text + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.warning(self, "저장 실패", f"대화 내용을 저장하지 못했습니다.\n{exc}")
            return None
        QMessageBox.information(self, "저장 완료", f"대화 내용을 저장했습니다:\n{path}")
        return path

    def reset_session(self) -> None:
        if self._process is not None or self._context_collecting:
            QMessageBox.information(
                self, "실행 중", "현재 요청이 끝난 뒤 세션을 초기화하세요."
            )
            return
        self._provider_session_ids.clear()
        self._stream_message_index = None
        self._clear_working_status()
        self._append_block(
            "시스템",
            "대화 세션을 초기화했습니다. 화면 기록은 유지되며 다음 요청부터 새 세션으로 시작합니다.",
        )
        self._set_status("새 세션 준비됨")

    def shutdown(self) -> None:
        self._clear_working_status()
        self._model_catalog_cancel_event.set()
        self._pending_model_catalog_refreshes.clear()
        self._context_request_generation += 1
        if self._context_cancel_event is not None:
            self._context_cancel_event.set()
        self._active_context_request = None
        self._context_cancel_event = None
        self._context_collection_cancelled = True
        self._pending_prompt_payload = None
        self._context_collecting = False
        self._cancel_deferred_timers()
        self._stop_status_process()
        self._stop_help_process()
        if self._process is not None:
            process = self._process
            try:
                process.blockSignals(True)
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.kill()
            except RuntimeError:
                pass
            self._release_process_temporary_attachments(process)
            self._process = None
        self._release_all_temporary_attachments()

    def _run_later(self, msec: int, callback: Callable[[], None]) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        self._deferred_timers.append(timer)

        def _on_timeout() -> None:
            if timer in self._deferred_timers:
                self._deferred_timers.remove(timer)
            timer.deleteLater()
            callback()

        timer.timeout.connect(_on_timeout)
        timer.start(max(0, msec))
        return timer

    def _cancel_named_timer(self, attribute: str) -> None:
        timer = getattr(self, attribute, None)
        setattr(self, attribute, None)
        if timer is None:
            return
        if timer in self._deferred_timers:
            self._deferred_timers.remove(timer)
        try:
            timer.stop()
            timer.deleteLater()
        except RuntimeError:
            return

    def _cancel_deferred_timers(self) -> None:
        for timer in list(getattr(self, "_deferred_timers", [])):
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        self._deferred_timers = []
        self._prompt_timeout_timer = None
        self._status_timeout_timer = None
        self._help_timeout_timer = None

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
        if (
            self.ai_chat_tabs.currentWidget() is self.chat_page
            and self._render_deferred
        ):
            self._render_deferred = False
            self._render_transcript()
        if self.ai_chat_tabs.currentWidget() is self.connection_page:
            self._ensure_model_catalog_fresh(self.current_provider_key())

    def _stop_status_process(self) -> None:
        self._cancel_named_timer("_status_timeout_timer")
        process = self._status_process
        self._status_process = None
        if process is None:
            return
        try:
            process.blockSignals(True)
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
        except RuntimeError:
            pass
        process.deleteLater()

    def _stop_help_process(self) -> None:
        self._cancel_named_timer("_help_timeout_timer")
        process = self._help_process
        self._help_process = None
        if process is None:
            return
        try:
            process.blockSignals(True)
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
        except RuntimeError:
            pass
        process.deleteLater()

    def _set_status(self, text: str, tooltip: str = "") -> None:
        timestamp = self._time_text()
        self.status_label.setText(f"{text} · {timestamp}")
        if tooltip:
            self.status_label.setToolTip(f"{tooltip}\n마지막 업데이트: {timestamp}")
        else:
            self.status_label.setToolTip(f"마지막 업데이트: {timestamp}")

    def _set_running(self, running: bool) -> None:
        if not running:
            self._clear_working_status()
        self.send_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.check_button.setEnabled(not running)
        self.login_button.setEnabled(not running and not self._login_preflight_active)
        self.save_button.setEnabled(not running)
        self.model_refresh_button.setEnabled(
            not running and self._active_model_catalog_request is None
        )
        self.custom_model_button.setEnabled(not running)
        self.reset_session_button.setEnabled(not running)
        self.permission_button.setEnabled(not running)
        if hasattr(self, "attach_button"):
            self.attach_button.setEnabled(not running)
            self.attachment_list.setEnabled(not running)
            if running:
                self.clear_attachments_button.setEnabled(False)
            else:
                self._update_attachment_buttons()

    def _set_preparing(self, preparing: bool) -> None:
        self._context_collecting = preparing
        self.send_button.setEnabled(not preparing)
        self.stop_button.setEnabled(preparing)
        self.check_button.setEnabled(not preparing)
        self.login_button.setEnabled(not preparing and not self._login_preflight_active)
        self.save_button.setEnabled(not preparing)
        self.model_refresh_button.setEnabled(
            not preparing and self._active_model_catalog_request is None
        )
        self.custom_model_button.setEnabled(not preparing)
        self.reset_session_button.setEnabled(not preparing)
        self.permission_button.setEnabled(not preparing)
        self.prompt_edit.setEnabled(not preparing)
        if hasattr(self, "attach_button"):
            self.attach_button.setEnabled(not preparing)
            self.attachment_list.setEnabled(not preparing)
            if preparing:
                self.clear_attachments_button.setEnabled(False)
            else:
                self._update_attachment_buttons()

    def _assistant_response_title(self, config: AiProviderConfig | None = None) -> str:
        if config is None:
            key = self.current_provider_key()
            config = self._providers.get(key, AiProviderConfig(key=key))
        key = config.key
        spec = PROVIDER_SPECS.get(key)
        provider_label = (
            spec.display_name if spec is not None else (key or "어시스턴트")
        )
        catalog = self._model_catalogs.get(key)
        selected_model = config.model.strip()
        descriptor = None
        if catalog is not None:
            if selected_model:
                descriptor = next(
                    (
                        item
                        for item in catalog.models
                        if not item.hidden and selected_model in {item.model, item.id}
                    ),
                    None,
                )
            else:
                descriptor = next(
                    (
                        item
                        for item in catalog.models
                        if item.is_default and not item.hidden
                    ),
                    None,
                )
        model_label = (
            (descriptor.display_name or descriptor.model)
            if descriptor is not None
            else selected_model or "자동 선택"
        )
        return f"{provider_label} · {model_label}"

    def _response_title_for_process(self, process: QProcess) -> str:
        title = str(process.property("response_title") or "").strip()
        if title:
            return title
        provider_key = str(
            process.property("provider_key") or self.current_provider_key()
        )
        config = self._providers.get(provider_key, AiProviderConfig(key=provider_key))
        return self._assistant_response_title(config)

    def _set_working_status(self, text: str) -> None:
        normalized = str(text or "").strip()
        if normalized == self._working_status_text:
            return
        self._working_status_text = normalized
        self._working_status_step = 0
        if normalized:
            self._working_status_timer.start()
        else:
            self._working_status_timer.stop()
        self._request_transcript_render(immediate=True)

    def _clear_working_status(self) -> None:
        self._set_working_status("")

    def _advance_working_status_animation(self) -> None:
        if not self._working_status_text:
            self._working_status_timer.stop()
            return
        self._working_status_step = (self._working_status_step + 1) % 4
        label = self._working_status_label
        if label is None:
            return
        try:
            label.setText(self._working_status_display_text())
        except RuntimeError:
            self._working_status_label = None

    def _working_status_display_text(self) -> str:
        return self._working_status_text + "." * self._working_status_step

    def _append_block(self, title: str, body: str) -> None:
        text = body.strip()
        if not text:
            return
        display_title = self._assistant_response_title() if title == "AI" else title
        self._stream_message_index = None
        self._messages.append(
            {"title": display_title, "body": text, "time": self._time_text()}
        )
        self._request_transcript_render(immediate=True)

    def _append_stream(self, chunk: str, title: str = "") -> None:
        if not chunk:
            return
        self._clear_working_status()
        response_title = title.strip() or self._assistant_response_title()
        if (
            self._stream_message_index is None
            or self._stream_message_index >= len(self._messages)
            or self._messages[self._stream_message_index].get("title") != response_title
        ):
            self._messages.append(
                {"title": response_title, "body": "", "time": self._time_text()}
            )
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
        view_state = self._capture_transcript_view_state()
        self._transcript_render_generation += 1
        render_generation = self._transcript_render_generation
        container_width = self._transcript_container_width()
        self._last_render_width = container_width
        self.message_container.setMinimumWidth(container_width)
        self._clear_message_widgets()
        if not self._messages and not self._working_status_text:
            placeholder = QLabel(
                "아직 대화가 없습니다. 제공자와 모델을 선택한 뒤 요청을 보내세요."
            )
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
            self._sync_message_container_height()
            self._run_later(0, self._sync_message_container_height)
            self._run_later(
                0,
                lambda: self._restore_transcript_view_state(
                    view_state, render_generation
                ),
            )
            return

        for message_index, message in enumerate(self._messages):
            body = message.get("body", "")
            if not body.strip():
                continue
            title = message.get("title", "")
            timestamp = message.get("time", "")
            kind = self._message_kind(title)
            self._add_message_widget(
                title, timestamp, body, kind, message_index=message_index
            )
        if self._working_status_text:
            self._add_working_status_widget()
        self.message_layout.addStretch(1)
        self._sync_message_container_height()
        self._run_later(0, self._sync_message_container_height)
        self._run_later(
            0,
            lambda: self._restore_transcript_view_state(view_state, render_generation),
        )

    def _capture_transcript_view_state(self) -> dict[str, Any]:
        if not hasattr(self, "transcript_scroll"):
            return {"scroll_value": 0, "follow_bottom": True, "selections": []}
        scrollbar = self.transcript_scroll.verticalScrollBar()
        selections: list[tuple[int, int, int]] = []
        if hasattr(self, "message_container"):
            for view in self.message_container.findChildren(MessageBodyView):
                if not view.isVisible():
                    continue
                message_index = view.property("messageIndex")
                cursor = view.textCursor()
                if message_index is None or not cursor.hasSelection():
                    continue
                selections.append(
                    (
                        int(message_index),
                        cursor.selectionStart(),
                        cursor.selectionEnd(),
                    )
                )
        follow_bottom = (
            not selections and scrollbar.maximum() - scrollbar.value() <= 24
        )
        return {
            "scroll_value": scrollbar.value(),
            "follow_bottom": follow_bottom,
            "selections": selections,
        }

    def _restore_transcript_view_state(
        self, view_state: dict[str, Any], render_generation: int
    ) -> None:
        if render_generation != self._transcript_render_generation:
            return
        try:
            self._sync_message_container_height()
            for view in self.message_container.findChildren(MessageBodyView):
                if not view.isVisible():
                    continue
                message_index = view.property("messageIndex")
                for saved_index, start, end in view_state.get("selections", []):
                    if message_index is None or int(message_index) != saved_index:
                        continue
                    cursor = QTextCursor(view.document())
                    cursor.setPosition(min(start, view.document().characterCount() - 1))
                    cursor.setPosition(
                        min(end, view.document().characterCount() - 1),
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    view.setTextCursor(cursor)
                    break
            scrollbar = self.transcript_scroll.verticalScrollBar()
            if view_state.get("follow_bottom", True):
                scrollbar.setValue(scrollbar.maximum())
            else:
                scrollbar.setValue(
                    min(int(view_state.get("scroll_value", 0)), scrollbar.maximum())
                )
        except RuntimeError:
            return

    def _sync_message_container_height(self) -> None:
        if not hasattr(self, "message_layout"):
            return
        margins = self.message_layout.contentsMargins()
        spacing = max(0, self.message_layout.spacing())
        content_height = margins.top() + margins.bottom()
        visible_items = 0
        for index in range(self.message_layout.count()):
            item = self.message_layout.itemAt(index)
            widget = item.widget()
            if widget is None:
                continue
            if widget.isHidden():
                continue
            item_height = max(
                widget.height(),
                widget.minimumHeight(),
                widget.sizeHint().height(),
                widget.minimumSizeHint().height(),
                item.sizeHint().height(),
                item.minimumSize().height(),
            )
            content_height += item_height
            visible_items += 1
        if visible_items > 1:
            content_height += spacing * (visible_items - 1)
        target_height = max(self.transcript_scroll.viewport().height(), content_height)
        self.message_container.setMinimumHeight(target_height)
        self.message_container.resize(
            max(self.message_container.width(), self._transcript_container_width()),
            target_height,
        )
        self.message_container.updateGeometry()

    def _clear_message_widgets(self) -> None:
        self._working_status_label = None
        while self.message_layout.count():
            item = self.message_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
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
        return (
            hasattr(self, "ai_chat_tabs")
            and self.ai_chat_tabs.currentWidget() is self.chat_page
        )

    def _transcript_container_width(self) -> int:
        return max(
            self.transcript_scroll.viewport().width() - 2, self.width() - 40, 480
        )

    def _add_message_widget(
        self,
        title: str,
        timestamp: str,
        body: str,
        kind: str,
        *,
        message_index: int,
    ) -> None:
        background, border, text_color, align = self._message_styles(kind)

        available_width = max(
            self.transcript_scroll.viewport().width(), self.width() - 80, 480
        )
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
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)
        copy_button = make_action_button(
            "복사",
            ActionKind.COPY,
            tooltip="이 메시지의 원문을 클립보드에 복사합니다.",
            object_name="aiChatMessageCopyButton",
        )
        copy_button.clicked.connect(
            lambda _checked=False, source=body: self._copy_message_source(source)
        )
        meta_row.addWidget(meta)
        meta_row.addStretch(1)
        meta_row.addWidget(copy_button)

        text = MessageBodyView()
        text.setProperty("messageIndex", message_index)
        body_width = max(160, bubble_width - 22)
        if self._message_body_has_markdown(body):
            text.set_message_body(
                self._markdown_to_html(body),
                rich_text=True,
                width=body_width,
                text_color=text_color,
                source_text=body,
            )
        else:
            text.set_message_body(
                body,
                rich_text=False,
                width=body_width,
                text_color=text_color,
                source_text=body,
            )

        bubble_layout.addLayout(meta_row)
        bubble_layout.addWidget(text)
        bubble.setMinimumHeight(bubble_layout.sizeHint().height())

        if align == "right":
            qt_align = Qt.AlignmentFlag.AlignRight
        elif align == "center":
            qt_align = Qt.AlignmentFlag.AlignHCenter
        else:
            qt_align = Qt.AlignmentFlag.AlignLeft
        self.message_layout.addWidget(bubble, 0, qt_align)

    def _add_working_status_widget(self) -> None:
        indicator = QFrame()
        indicator.setObjectName("aiChatWorkingIndicator")
        indicator.setAccessibleName("작업 중")
        indicator.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        indicator.setStyleSheet(
            """
            QFrame#aiChatWorkingIndicator {
                background: #f8fafc;
                border: 1px solid #d8e1ec;
                border-radius: 9px;
            }
            QFrame#aiChatWorkingIndicator QLabel {
                border: none;
                background: transparent;
            }
            """
        )

        row = QHBoxLayout(indicator)
        row.setContentsMargins(11, 8, 13, 8)
        row.setSpacing(8)

        pulse = QLabel("●")
        pulse.setObjectName("aiChatWorkingIndicatorPulse")
        pulse.setStyleSheet("color: #2563eb; font-size: 10px;")
        pulse.setAccessibleName("")

        label = QLabel(self._working_status_display_text())
        label.setObjectName("aiChatWorkingIndicatorLabel")
        label.setStyleSheet("color: #475467; font-size: 12px;")
        label.setAccessibleName(self._working_status_text)

        row.addWidget(pulse, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._working_status_label = label
        self.message_layout.addWidget(indicator, 0, Qt.AlignmentFlag.AlignLeft)

    @classmethod
    def _message_body_has_markdown(cls, body: str) -> bool:
        text = str(body or "")
        return bool(
            re.search(r"(^|\n)\s{0,3}#{1,6}\s+", text)
            or re.search(r"(^|\n)\s*([-*]|\d+\.)\s+", text)
            or "```" in text
            or re.search(r"\*\*[^*]+\*\*", text)
            or re.search(r"`[^`\n]+`", text)
            or re.search(r"\[[^\]]+\]\(https?://[^)]+\)", text)
            or cls._has_markdown_table(text)
        )

    @staticmethod
    def _has_markdown_table(body: str) -> bool:
        lines = str(body or "").splitlines()
        for index in range(len(lines) - 1):
            header = lines[index].strip()
            separator = lines[index + 1].strip()
            if "|" not in header or "|" not in separator:
                continue
            cells = [
                cell.strip().replace(" ", "")
                for cell in separator.strip("|").split("|")
            ]
            if len(cells) >= 2 and all(
                re.fullmatch(r":?-{3,}:?", cell) for cell in cells
            ):
                return True
        return False

    @classmethod
    def _markdown_to_html(cls, body: str) -> str:
        markdown = cls._normalize_markdown_break_tags(str(body or ""))
        document = QTextDocument()
        document.setMarkdown(
            markdown, QTextDocument.MarkdownFeature.MarkdownDialectGitHub
        )
        return document.toHtml()

    @staticmethod
    def _normalize_markdown_break_tags(body: str) -> str:
        protected = re.split(r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)", body)
        for index in range(0, len(protected), 2):
            protected[index] = re.sub(
                r"<br\s*/?\s*>", "<br/>", protected[index], flags=re.IGNORECASE
            )
        return "".join(protected)

    @staticmethod
    def _copy_message_source(source: str) -> None:
        QApplication.clipboard().setText(str(source or ""))

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
        self._run_later(0, self._scroll_transcript_now)

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
