from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.models.ai_models import KNOWN_AI_PROVIDERS, normalize_ai_chat_config
from app.models.result_models import OperationResult
from app.services.ai_agent_service import (
    PROVIDER_SPECS,
    inspect_provider,
    provider_configs_from_app_config,
)
from app.ui.common import (
    JobRunner,
    confirm_risky_action,
    make_selectable_wrapped_label,
    make_step_hint,
)
from app.utils.file_utils import (
    default_effective_path_settings,
    default_update_config,
    effective_path_settings,
    load_json,
    normalize_path_settings,
    open_in_explorer,
)
from app.version import __version__
from netops_suite.ui.actions import ActionKind, make_action_button


class SettingsTab(QWidget):
    check_updates_requested = Signal(dict)
    integration_changed = Signal(str)

    _SECTION_KEYS = ("program", "storage", "tools", "maintenance")

    _PATH_FIELDS = (
        ("config_dir", "설정 파일 폴더", "프로파일과 기능별 JSON 설정 파일을 저장합니다."),
        ("logs_dir", "로그 폴더", "프로그램 로그와 AI 감사 로그를 저장합니다."),
        (
            "exports_dir",
            "결과/내보내기 폴더",
            "자동 생성 결과·백업을 저장하고, 수동 저장 대화상자에서 처음 제안할 기본 위치로 사용합니다.",
        ),
    )

    _CONFIG_FILE_NAMES = (
        ("주 설정", "app_config.json"),
        ("IP 프로파일", "ip_profiles.json"),
        ("FTP 프로파일", "ftp_profiles.json"),
        ("FTP 화면 상태", "ftp_runtime.json"),
        ("SCP 프로파일", "scp_profiles.json"),
        ("SCP 화면 상태", "scp_runtime.json"),
        ("TFTP 화면 상태", "tftp_runtime.json"),
        ("공개 iperf 서버 캐시", "public_iperf_servers_cache.json"),
        ("AI 모델 목록 캐시", "ai_model_catalog_cache.json"),
        ("OUI 캐시", "oui_cache.json"),
        ("FTP/SCP 키 폴더", "ftp_keys"),
    )

    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._path_dirty = False
        self._saved_path_values: dict[str, str] = {}
        self._tools_loaded = False
        self._tool_status_busy = False
        self._tool_manage_state: dict[str, object] = {}
        self._iperf_install_cancel_event: Event | None = None
        self._oui_operation_busy = False
        self._oui_operation_kind = ""
        thread_pool = getattr(self.state, "thread_pool", None) or QThreadPool.globalInstance()
        self._job_runner = JobRunner(thread_pool, self, default_error_title="도구 상태 확인 실패")
        self._build_ui()
        self.state.config_reloaded.connect(self.reload_view)
        paths_changed = getattr(self.state, "paths_changed", None)
        if paths_changed is not None and callable(getattr(paths_changed, "connect", None)):
            paths_changed.connect(self.reload_view)
        self.reload_view()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(12, 12, 12, 12)
        outer_layout.setSpacing(10)
        outer_layout.addWidget(
            make_step_hint(
                "프로그램 업데이트, 저장 위치, 외부 도구 연동, 설정 파일 관리를 한곳에서 변경합니다."
            )
        )

        self.section_tabs = QTabWidget()
        self.section_tabs.setObjectName("settingsSectionTabs")
        self.section_tabs.setDocumentMode(True)
        self.section_tabs.setUsesScrollButtons(False)
        outer_layout.addWidget(self.section_tabs, 1)

        self.program_scroll, program_layout = self._new_scroll_page("settingsProgramScroll")
        self.storage_scroll, storage_layout = self._new_scroll_page("settingsStorageScroll")
        self.tools_scroll, tools_layout = self._new_scroll_page("settingsToolsScroll")
        self.maintenance_scroll, maintenance_layout = self._new_scroll_page("settingsMaintenanceScroll")
        # Compatibility for callers and tests that used the original single scroll area.
        self.settings_scroll = self.storage_scroll

        self._build_program_page(program_layout)
        self._build_storage_page(storage_layout)
        self._build_tools_page(tools_layout)
        self._build_maintenance_page(maintenance_layout)

        self.section_tabs.addTab(self.program_scroll, "프로그램")
        self.section_tabs.addTab(self.storage_scroll, "저장 위치")
        self.section_tabs.addTab(self.tools_scroll, "도구 연동")
        self.section_tabs.addTab(self.maintenance_scroll, "설정 관리")
        self.section_tabs.currentChanged.connect(self._handle_section_changed)

    @staticmethod
    def _new_scroll_page(object_name: str) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setObjectName(object_name)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 10, 4, 10)
        layout.setSpacing(10)
        scroll.setWidget(content)
        return scroll, layout

    def _build_program_page(self, layout: QVBoxLayout) -> None:
        update_group = QGroupBox("프로그램 업데이트")
        update_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        update_layout = QVBoxLayout(update_group)
        summary_label = QLabel(
            "공식 배포 채널에서 새 버전을 확인합니다. 설치 파일은 다운로드 후 SHA-256 무결성과 "
            "게시자 정보를 확인하며, 사용자가 승인한 경우에만 설치 프로그램을 실행합니다."
        )
        summary_label.setWordWrap(True)
        update_layout.addWidget(summary_label)

        update_form = QFormLayout()
        self.version_label = QLabel(__version__)
        self.check_on_startup_check = QCheckBox("프로그램 시작 시 업데이트 확인")
        update_form.addRow("현재 버전", self.version_label)
        update_form.addRow("", self.check_on_startup_check)
        update_layout.addLayout(update_form)

        update_actions = QHBoxLayout()
        self.check_update_button = make_action_button(
            "업데이트 확인",
            ActionKind.START,
            tooltip="새 버전 업데이트를 확인합니다.",
        )
        update_actions.addWidget(self.check_update_button)
        update_actions.addStretch(1)
        update_layout.addLayout(update_actions)

        self.update_status_label = QLabel("업데이트는 프로그램 이름에 고정된 공식 배포 채널을 사용합니다.")
        self.update_status_label.setWordWrap(True)
        self.update_details = QPlainTextEdit()
        self.update_details.setReadOnly(True)
        self.update_details.setMaximumHeight(140)
        self.update_details.hide()
        update_layout.addWidget(self.update_status_label)
        update_layout.addWidget(self.update_details)
        layout.addWidget(update_group)
        layout.addStretch(1)

        self.check_on_startup_check.toggled.connect(self._save_startup_update_preference)
        self.check_update_button.clicked.connect(self._request_update_check)

    def _build_storage_page(self, layout: QVBoxLayout) -> None:
        storage_group = QGroupBox("저장 위치")
        storage_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        storage_layout = QVBoxLayout(storage_group)
        storage_help = QLabel(
            "설정 파일 폴더와 결과/내보내기 폴더는 저장 즉시 이후 파일 작업에 적용됩니다. "
            "결과/내보내기 폴더는 자동 결과·백업 저장 위치이자 수동 저장 대화상자의 기본 제안 위치이며, "
            "수동 저장 파일은 대화상자에서 사용자가 선택한 경로에 저장됩니다. "
            "로그 폴더는 열려 있는 로그 파일의 경로가 섞이지 않도록 프로그램을 다시 시작한 뒤 적용됩니다. "
            "설정 폴더를 바꾸면 기존 파일은 새 폴더에 복사하되 기존 대상 파일은 덮어쓰지 않습니다."
        )
        storage_help.setWordWrap(True)
        storage_layout.addWidget(storage_help)

        path_form = QFormLayout()
        path_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.path_edits: dict[str, QLineEdit] = {}
        self.path_change_buttons = {}
        self.path_open_buttons = {}
        for key, label, tooltip in self._PATH_FIELDS:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            edit = QLineEdit()
            edit.setObjectName(f"{key}Edit")
            edit.setClearButtonEnabled(True)
            edit.setToolTip(tooltip)
            edit.setAccessibleName(label)
            edit.setAccessibleDescription(tooltip)
            edit.setMinimumWidth(0)
            change_button = make_action_button(
                "변경",
                ActionKind.BROWSE,
                tooltip=f"새 {label} 위치를 선택합니다.",
            )
            change_button.setAccessibleName(f"{label} 위치 변경")
            change_button.clicked.connect(
                lambda _checked=False, field=key: self._change_directory(field)
            )
            open_button = make_action_button(
                "폴더 열기",
                ActionKind.OPEN,
                tooltip=f"입력된 {label}를 파일 탐색기에서 엽니다.",
            )
            open_button.setAccessibleName(f"{label} 폴더 열기")
            open_button.clicked.connect(lambda _checked=False, field=key: self._open_path_directory(field))
            edit.textChanged.connect(self._path_fields_changed)
            row_layout.addWidget(edit, 1)
            row_layout.addWidget(change_button)
            row_layout.addWidget(open_button)
            label_widget = QLabel(label)
            label_widget.setBuddy(edit)
            path_form.addRow(label_widget, row_widget)
            self.path_edits[key] = edit
            self.path_change_buttons[key] = change_button
            self.path_open_buttons[key] = open_button

        self.config_dir_edit = self.path_edits["config_dir"]
        self.logs_dir_edit = self.path_edits["logs_dir"]
        self.exports_dir_edit = self.path_edits["exports_dir"]
        storage_layout.addLayout(path_form)

        path_action_row = QHBoxLayout()
        self.save_paths_button = make_action_button(
            "경로 설정 저장",
            ActionKind.SAVE,
            enabled=False,
        )
        self.reset_paths_button = make_action_button(
            "기본 경로로 되돌리기",
            ActionKind.UTILITY,
            enabled=False,
        )
        path_action_row.addWidget(self.save_paths_button)
        path_action_row.addWidget(self.reset_paths_button)
        path_action_row.addStretch(1)
        storage_layout.addLayout(path_action_row)
        self.path_status_label = make_selectable_wrapped_label()
        storage_layout.addWidget(self.path_status_label)
        layout.addWidget(storage_group)

        applied_group = QGroupBox("현재 적용 중인 위치")
        applied_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        applied_layout = QVBoxLayout(applied_group)
        self.config_dir_label = make_selectable_wrapped_label()
        self.ip_profile_label = make_selectable_wrapped_label()
        self.log_dir_label = make_selectable_wrapped_label()
        self.export_dir_label = make_selectable_wrapped_label()
        for label in (
            self.config_dir_label,
            self.ip_profile_label,
            self.log_dir_label,
            self.export_dir_label,
        ):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            applied_layout.addWidget(label)
        layout.addWidget(applied_group)
        layout.addStretch(1)

        self.save_paths_button.clicked.connect(self._save_path_settings)
        self.reset_paths_button.clicked.connect(self._reset_path_fields)

    def _build_tools_page(self, layout: QVBoxLayout) -> None:
        intro_row = QHBoxLayout()
        intro_label = QLabel(
            "실행 프로그램의 설치 상태와 여러 기능이 함께 사용하는 제조사 데이터를 관리합니다. "
            "측정 대상, 모델, 응답 옵션은 각 기능 화면에서 설정합니다."
        )
        intro_label.setWordWrap(True)
        self.tool_refresh_button = make_action_button("전체 상태 새로고침", ActionKind.REFRESH)
        intro_row.addWidget(intro_label, 1)
        intro_row.addWidget(self.tool_refresh_button)
        layout.addLayout(intro_row)

        self.iperf_tool_group = QGroupBox("iperf3")
        self.iperf_tool_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        iperf_layout = QVBoxLayout(self.iperf_tool_group)
        iperf_help = QLabel(
            "대역폭 측정에 사용하는 iperf3 실행 파일을 확인하고, 지원되는 환경에서는 winget으로 설치하거나 업데이트합니다."
        )
        iperf_help.setWordWrap(True)
        iperf_layout.addWidget(iperf_help)
        self.iperf_tool_status_label = make_selectable_wrapped_label("상태를 확인하지 않았습니다.")
        self.iperf_tool_path_label = make_selectable_wrapped_label("사용 경로: 확인 전")
        iperf_layout.addWidget(self.iperf_tool_status_label)
        iperf_layout.addWidget(self.iperf_tool_path_label)

        iperf_actions = QHBoxLayout()
        self.iperf_tool_manage_button = make_action_button("상태 확인 필요", ActionKind.UTILITY, enabled=False)
        self.iperf_tool_cancel_button = make_action_button("설치 중지", ActionKind.STOP, enabled=False)
        self.iperf_tool_cancel_button.hide()
        iperf_actions.addWidget(self.iperf_tool_manage_button)
        iperf_actions.addWidget(self.iperf_tool_cancel_button)
        iperf_actions.addStretch(1)
        iperf_layout.addLayout(iperf_actions)
        self.iperf_tool_log = QPlainTextEdit()
        self.iperf_tool_log.setReadOnly(True)
        self.iperf_tool_log.setMaximumHeight(150)
        self.iperf_tool_log.hide()
        iperf_layout.addWidget(self.iperf_tool_log)
        layout.addWidget(self.iperf_tool_group)

        self.oui_tool_group = QGroupBox("OUI 제조사 데이터")
        self.oui_tool_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        oui_layout = QVBoxLayout(self.oui_tool_group)
        oui_help = QLabel(
            "ARP 스캔, Wi-Fi 분석, MAC 제조사 조회가 공통으로 사용하는 IEEE 등록기관 데이터를 "
            "관리합니다. 온라인 확인과 업데이트는 아래 버튼을 눌렀을 때만 실행됩니다."
        )
        oui_help.setWordWrap(True)
        oui_layout.addWidget(oui_help)
        self.oui_tool_status_label = make_selectable_wrapped_label(
            "로컬 OUI 데이터 상태를 확인하지 않았습니다."
        )
        self.oui_tool_version_label = make_selectable_wrapped_label(
            "데이터 버전: 확인 전"
        )
        self.oui_tool_source_label = make_selectable_wrapped_label(
            "원본: IEEE Registration Authority"
        )
        self.oui_tool_result_label = make_selectable_wrapped_label()
        self.oui_tool_status_label.setAccessibleName("OUI 로컬 데이터 상태")
        self.oui_tool_version_label.setAccessibleName("OUI 데이터 버전")
        self.oui_tool_source_label.setAccessibleName("OUI 데이터 원본")
        self.oui_tool_result_label.setAccessibleName("OUI 업데이트 확인 결과")
        oui_layout.addWidget(self.oui_tool_status_label)
        oui_layout.addWidget(self.oui_tool_version_label)
        oui_layout.addWidget(self.oui_tool_source_label)

        oui_actions = QHBoxLayout()
        self.oui_check_updates_button = make_action_button(
            "최신 여부 확인",
            ActionKind.REFRESH,
            tooltip="IEEE 공식 원본과 로컬 OUI 데이터의 내용 버전을 비교합니다.",
        )
        self.oui_update_button = make_action_button(
            "데이터 업데이트",
            ActionKind.START,
            tooltip="IEEE 공식 원본 4개를 모두 받은 뒤 로컬 OUI 데이터를 교체합니다.",
        )
        self.oui_check_updates_button.setAccessibleName("OUI 데이터 최신 여부 확인")
        self.oui_update_button.setAccessibleName("OUI 제조사 데이터 업데이트")
        oui_actions.addWidget(self.oui_check_updates_button)
        oui_actions.addWidget(self.oui_update_button)
        oui_actions.addStretch(1)
        oui_layout.addLayout(oui_actions)
        oui_layout.addWidget(self.oui_tool_result_label)

        self.oui_tool_log = QPlainTextEdit()
        self.oui_tool_log.setObjectName("ouiToolLog")
        self.oui_tool_log.setReadOnly(True)
        self.oui_tool_log.setMaximumHeight(150)
        self.oui_tool_log.hide()
        oui_layout.addWidget(self.oui_tool_log)
        layout.addWidget(self.oui_tool_group)

        self.ai_cli_group = QGroupBox("AI CLI 실행 파일")
        self.ai_cli_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        ai_layout = QVBoxLayout(self.ai_cli_group)
        ai_help = QLabel(
            "비워 두면 설치된 CLI를 자동으로 찾습니다. 자동 감지가 실패할 때만 실제 실행 파일을 지정하세요. "
            "로그인과 모델·응답 옵션은 NetOps 어시스턴트에서 관리합니다."
        )
        ai_help.setWordWrap(True)
        ai_layout.addWidget(ai_help)

        self.ai_cli_path_edits: dict[str, QLineEdit] = {}
        self.ai_cli_browse_buttons = {}
        self.ai_cli_status_labels: dict[str, QLabel] = {}
        for key in KNOWN_AI_PROVIDERS:
            provider_row = QWidget()
            provider_layout = QVBoxLayout(provider_row)
            provider_layout.setContentsMargins(0, 2, 0, 4)
            provider_layout.setSpacing(3)
            edit_row = QHBoxLayout()
            name_label = QLabel(PROVIDER_SPECS[key].display_name)
            name_label.setMinimumWidth(125)
            edit = QLineEdit()
            edit.setObjectName(f"{key}CliPathEdit")
            edit.setPlaceholderText(f"자동 감지: {PROVIDER_SPECS[key].executable}")
            edit.setClearButtonEnabled(True)
            edit.setAccessibleName(f"{PROVIDER_SPECS[key].display_name} 실행 파일")
            edit.setAccessibleDescription(
                "비워 두면 설치된 CLI를 자동으로 찾습니다. 자동 감지가 실패할 때만 실행 파일을 지정합니다."
            )
            edit.setMinimumWidth(0)
            browse_button = make_action_button(
                "찾아보기",
                ActionKind.OPEN,
                tooltip=f"{PROVIDER_SPECS[key].display_name} 실행 파일을 선택합니다.",
            )
            browse_button.setAccessibleName(
                f"{PROVIDER_SPECS[key].display_name} 실행 파일 찾아보기"
            )
            browse_button.clicked.connect(lambda _checked=False, provider=key: self._browse_ai_cli(provider))
            name_label.setBuddy(edit)
            edit_row.addWidget(name_label)
            edit_row.addWidget(edit, 1)
            edit_row.addWidget(browse_button)
            provider_layout.addLayout(edit_row)
            status_label = make_selectable_wrapped_label("감지 상태: 확인 전")
            status_label.setAccessibleName(f"{PROVIDER_SPECS[key].display_name} 감지 상태")
            status_label.setContentsMargins(131, 0, 0, 0)
            provider_layout.addWidget(status_label)
            ai_layout.addWidget(provider_row)
            self.ai_cli_path_edits[key] = edit
            self.ai_cli_browse_buttons[key] = browse_button
            self.ai_cli_status_labels[key] = status_label

        ai_actions = QHBoxLayout()
        self.save_ai_cli_paths_button = make_action_button("AI CLI 경로 저장", ActionKind.SAVE)
        self.reset_ai_cli_paths_button = make_action_button("자동 감지 사용", ActionKind.UTILITY)
        ai_actions.addWidget(self.save_ai_cli_paths_button)
        ai_actions.addWidget(self.reset_ai_cli_paths_button)
        ai_actions.addStretch(1)
        ai_layout.addLayout(ai_actions)
        self.ai_cli_path_status_label = make_selectable_wrapped_label()
        ai_layout.addWidget(self.ai_cli_path_status_label)
        layout.addWidget(self.ai_cli_group)

        layout.addStretch(1)

        self.tool_refresh_button.clicked.connect(self.refresh_tool_statuses)
        self.iperf_tool_manage_button.clicked.connect(self._manage_iperf)
        self.iperf_tool_cancel_button.clicked.connect(self._cancel_iperf_install)
        self.oui_check_updates_button.clicked.connect(self._check_oui_updates)
        self.oui_update_button.clicked.connect(self._update_oui_data)
        self.save_ai_cli_paths_button.clicked.connect(self._save_ai_cli_paths)
        self.reset_ai_cli_paths_button.clicked.connect(self._reset_ai_cli_paths)

    def _build_maintenance_page(self, layout: QVBoxLayout) -> None:
        files_group = QGroupBox("설정 파일")
        files_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        files_layout = QVBoxLayout(files_group)
        files_help = QLabel(
            "아래 목록은 현재 설정 폴더를 기준으로 표시합니다. "
            "각 저장 폴더는 저장 위치 탭에서 바로 열 수 있습니다."
        )
        files_help.setWordWrap(True)
        files_layout.addWidget(files_help)

        self.settings_files_view = QPlainTextEdit()
        self.settings_files_view.setObjectName("settingsFilesView")
        self.settings_files_view.setReadOnly(True)
        self.settings_files_view.setMinimumHeight(180)
        self.settings_files_view.setMaximumHeight(300)
        files_layout.addWidget(self.settings_files_view)

        folder_button_row = QHBoxLayout()
        self.reload_button = make_action_button("설정 파일 다시 불러오기", ActionKind.REFRESH)
        folder_button_row.addWidget(self.reload_button)
        folder_button_row.addStretch(1)
        files_layout.addLayout(folder_button_row)
        self.maintenance_status_label = make_selectable_wrapped_label()
        files_layout.addWidget(self.maintenance_status_label)
        layout.addWidget(files_group)

        reset_group = QGroupBox("모든 설정 초기화")
        reset_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        reset_layout = QVBoxLayout(reset_group)
        reset_help = QLabel(
            "프로그램 옵션, 화면 상태, 네트워크·파일 전송 프로파일과 입력값, AI 설정, "
            "사용자 장비 점검 규칙을 처음 상태로 되돌립니다. 저장 위치 설정도 기본 경로로 "
            "돌아가지만 기존 애플리케이션 로그와 실행 결과·백업·내보낸 파일은 삭제하지 않습니다."
        )
        reset_help.setWordWrap(True)
        reset_layout.addWidget(reset_help)
        reset_actions = QHBoxLayout()
        self.reset_all_settings_button = make_action_button(
            "모든 설정 초기화",
            ActionKind.DANGER,
            tooltip="사용자 설정과 프로파일을 기본값으로 되돌립니다.",
        )
        self.reset_all_settings_button.setAccessibleName("모든 사용자 설정 초기화")
        reset_actions.addWidget(self.reset_all_settings_button)
        reset_actions.addStretch(1)
        reset_layout.addLayout(reset_actions)
        self.reset_settings_status_label = make_selectable_wrapped_label()
        reset_layout.addWidget(self.reset_settings_status_label)
        layout.addWidget(reset_group)
        layout.addStretch(1)

        self.reload_button.clicked.connect(self._reload_config_files)
        self.reset_all_settings_button.clicked.connect(self._reset_all_settings)

    def show_section(self, section_key: str, tool_key: str = "") -> None:
        aliases = {"general": "program", "paths": "storage", "integrations": "tools"}
        key = aliases.get(section_key, section_key)
        if key not in self._SECTION_KEYS:
            key = "program"
        self.section_tabs.setCurrentIndex(self._SECTION_KEYS.index(key))
        if key != "tools":
            return
        if tool_key in KNOWN_AI_PROVIDERS or tool_key == "ai":
            target: QWidget = self.ai_cli_group
        elif tool_key in {"oui", "oui_cache"}:
            target = self.oui_tool_group
        else:
            target = self.iperf_tool_group
        self.tools_scroll.ensureWidgetVisible(target)
        if tool_key in self.ai_cli_path_edits:
            self.ai_cli_path_edits[tool_key].setFocus()

    def save_ui_state(self) -> dict[str, str]:
        index = self.section_tabs.currentIndex()
        key = self._SECTION_KEYS[index] if 0 <= index < len(self._SECTION_KEYS) else "program"
        return {"section": key}

    def restore_ui_state(self, ui_state: dict | None) -> None:
        state = ui_state if isinstance(ui_state, dict) else {}
        self.show_section(str(state.get("section", "program") or "program"))

    def _handle_section_changed(self, index: int) -> None:
        if index != self._SECTION_KEYS.index("tools") or self._tools_loaded:
            return
        self._tools_loaded = True
        self.refresh_tool_statuses()

    def current_update_config(self) -> dict:
        config = default_update_config()
        config["check_on_startup"] = self.check_on_startup_check.isChecked()
        return config

    def _save_startup_update_preference(self, checked: bool) -> None:
        config = dict(self.state.app_config)
        update_config = default_update_config()
        update_config["check_on_startup"] = checked
        config["update"] = update_config
        self.state.save_app_config(config)

    def set_update_status(self, message: str, details: str = "") -> None:
        self.update_status_label.setText(message)
        self.update_details.setPlainText(details)
        self.update_details.setVisible(bool(details.strip()))

    def set_update_busy(self, busy: bool) -> None:
        self.check_update_button.setEnabled(not busy)

    def reload_view(self) -> None:
        update_config = self.state.app_config.get("update", {})
        was_blocked = self.check_on_startup_check.blockSignals(True)
        self.check_on_startup_check.setChecked(bool(update_config.get("check_on_startup", False)))
        self.check_on_startup_check.blockSignals(was_blocked)

        self._load_ai_cli_paths()
        self._refresh_effective_path_labels()
        if not self._path_dirty:
            self._load_saved_path_fields()
        else:
            self._refresh_settings_file_preview(self._current_path_values())
        self.version_label.setText(__version__)
        self.set_update_status("업데이트는 프로그램 이름에 고정된 공식 배포 채널을 사용합니다.")
        self._refresh_oui_action_states()

    def _load_ai_cli_paths(self) -> None:
        ai_config = normalize_ai_chat_config(self.state.app_config.get("ai_chat", {}))
        providers = ai_config["providers"]
        for key, edit in self.ai_cli_path_edits.items():
            blocked = edit.blockSignals(True)
            edit.setText(str(providers[key].get("command_path", "") or ""))
            edit.blockSignals(blocked)

    def _browse_ai_cli(self, provider_key: str) -> None:
        edit = self.ai_cli_path_edits[provider_key]
        initial = edit.text().strip() or str(getattr(self.state.paths, "root", Path.cwd()))
        selected, _selected_filter = QFileDialog.getOpenFileName(
            self,
            f"{PROVIDER_SPECS[provider_key].display_name} 실행 파일 선택",
            initial,
            "실행 파일 (*.exe *.cmd *.bat);;모든 파일 (*)",
        )
        if selected:
            edit.setText(selected)

    def _reset_ai_cli_paths(self) -> None:
        for edit in self.ai_cli_path_edits.values():
            edit.clear()
        self.ai_cli_path_status_label.setText("자동 감지를 사용하려면 변경된 경로를 저장하세요.")

    def _save_ai_cli_paths(self) -> None:
        ai_config = normalize_ai_chat_config(self.state.app_config.get("ai_chat", {}))
        providers = ai_config["providers"]
        for key, edit in self.ai_cli_path_edits.items():
            providers[key]["command_path"] = edit.text().strip()
        config = dict(self.state.app_config)
        config["ai_chat"] = ai_config
        self.state.save_app_config(config)
        self.ai_cli_path_status_label.setText("AI CLI 실행 파일 경로를 저장했습니다.")
        self.integration_changed.emit("ai")
        self.refresh_tool_statuses()

    def refresh_tool_statuses(self) -> None:
        if (
            self._tool_status_busy
            or self._iperf_install_cancel_event is not None
            or self._oui_operation_busy
        ):
            return
        self._tool_status_busy = True
        self.tool_refresh_button.setEnabled(False)
        self.iperf_tool_manage_button.setEnabled(False)
        self.oui_check_updates_button.setEnabled(False)
        self.oui_update_button.setEnabled(False)
        self.iperf_tool_status_label.setText("iperf3 상태를 확인하는 중입니다...")
        self.oui_tool_status_label.setText("로컬 OUI 데이터 상태를 확인하는 중입니다...")
        for label in self.ai_cli_status_labels.values():
            label.setText("감지 상태: 확인 중...")
        ai_config = normalize_ai_chat_config(self.state.app_config.get("ai_chat", {}))
        self._job_runner.start(
            self._collect_tool_status,
            ai_config,
            on_result=self._apply_tool_status,
            on_error=self._handle_tool_status_error,
            on_finished=self._finish_tool_status_refresh,
        )

    def _collect_tool_status(self, ai_config: dict[str, Any]) -> dict[str, Any]:
        ai_status = {
            key: inspect_provider(config)
            for key, config in provider_configs_from_app_config(ai_config).items()
        }
        oui_service = getattr(self.state, "oui_service", None)
        cache_status = getattr(oui_service, "cache_status", None)
        oui_status = cache_status() if callable(cache_status) else None

        service = getattr(self.state, "iperf_service", None)
        iperf_status = None
        if service is not None:
            executable_path, source = service.executable_details()
            version = service.executable_version(executable_path) if executable_path else ""
            iperf_status = {
                "path": executable_path or "",
                "source": source,
                "version": version or "",
                "manage": service.managed_install_state(),
            }
        return {
            "ai": ai_status,
            "iperf": iperf_status,
            "oui": oui_status,
        }

    def _apply_tool_status(self, result: object) -> None:
        if not isinstance(result, dict):
            self._handle_tool_status_error("도구 상태 응답 형식이 올바르지 않습니다.")
            return
        for key, health in dict(result.get("ai", {})).items():
            label = self.ai_cli_status_labels.get(key)
            if label is None:
                continue
            if bool(getattr(health, "installed", False)):
                resolved = str(getattr(health, "resolved_path", "") or "")
                label.setText(f"감지됨: {resolved}")
                label.setToolTip(resolved)
            else:
                detail = str(getattr(health, "detail", "") or "실행 파일을 찾지 못했습니다.")
                label.setText("찾지 못함 · 경로를 지정하거나 CLI를 설치하세요.")
                label.setToolTip(detail)

        self._apply_oui_status(result.get("oui"))

        iperf = result.get("iperf")
        if not isinstance(iperf, dict):
            self._tool_manage_state = {}
            self.iperf_tool_status_label.setText("현재 환경에서는 iperf3 서비스를 사용할 수 없습니다.")
            self.iperf_tool_path_label.setText("사용 경로: 없음")
            self.iperf_tool_manage_button.setText("사용 불가")
            return

        path = str(iperf.get("path", "") or "")
        source = str(iperf.get("source", "") or "")
        version = str(iperf.get("version", "") or "")
        self._tool_manage_state = dict(iperf.get("manage", {}) or {})
        if path:
            status_parts = ["준비됨"]
            if version:
                status_parts.append(f"버전 {version}")
            if source:
                status_parts.append(self._iperf_source_label(source))
            self.iperf_tool_status_label.setText(" · ".join(status_parts))
            self.iperf_tool_path_label.setText(f"사용 경로: {path}")
            self.iperf_tool_path_label.setToolTip(path)
        else:
            self.iperf_tool_status_label.setText("iperf3를 찾지 못했습니다.")
            self.iperf_tool_path_label.setText("사용 경로: 없음")
            self.iperf_tool_path_label.setToolTip("")
        self.iperf_tool_manage_button.setText(
            str(self._tool_manage_state.get("action_label", "수동 설치 필요"))
        )

    @staticmethod
    def _iperf_source_label(source: str) -> str:
        return {
            "program folder": "프로그램 폴더",
            "winget": "winget",
            "system PATH": "시스템 PATH",
        }.get(source, source)

    def _handle_tool_status_error(self, message: str) -> None:
        self.iperf_tool_status_label.setText(f"상태 확인 실패: {message}")
        self.oui_tool_status_label.setText(f"로컬 상태 확인 실패: {message}")
        for label in self.ai_cli_status_labels.values():
            if "확인 중" in label.text():
                label.setText("감지 상태를 확인하지 못했습니다.")

    def _finish_tool_status_refresh(self) -> None:
        self._tool_status_busy = False
        self.tool_refresh_button.setEnabled(not self._oui_operation_busy)
        self.iperf_tool_manage_button.setEnabled(bool(self._tool_manage_state.get("button_enabled", False)))
        self._refresh_oui_action_states()

    def _apply_oui_status(self, status: object) -> None:
        if not isinstance(status, dict):
            self.oui_tool_status_label.setText(
                "현재 환경에서는 OUI 제조사 데이터를 관리할 수 없습니다."
            )
            self.oui_tool_version_label.setText("데이터 버전: 없음")
            self.oui_tool_source_label.setText("원본: 확인할 수 없음")
            return

        available = bool(status.get("available", False))
        record_count = int(status.get("record_count", 0) or 0)
        updated_at = str(status.get("updated_at", "") or "알 수 없음")
        age_days = status.get("age_days")
        stale = bool(status.get("stale", False))
        if available:
            status_parts = [
                f"로컬 데이터 {record_count:,}건",
                f"갱신 {updated_at}",
            ]
            if isinstance(age_days, int):
                status_parts.append(f"{age_days}일 경과")
            if stale:
                status_parts.append("최신 여부 확인 권장")
            self.oui_tool_status_label.setText(" · ".join(status_parts))
        else:
            self.oui_tool_status_label.setText(
                "로컬 OUI 데이터가 없습니다. 최신 여부를 확인하거나 데이터를 업데이트하세요."
            )

        version_label = str(status.get("version_label", "") or "구형 캐시 · 버전 정보 없음")
        source_updated_at = str(status.get("source_updated_at", "") or "")
        version_text = f"데이터 버전: {version_label}"
        if source_updated_at:
            version_text += f" · 원본 갱신 {source_updated_at}"
        self.oui_tool_version_label.setText(version_text)

        source_name = str(status.get("source_name", "") or "IEEE Registration Authority")
        source_url = str(status.get("source_url", "") or "")
        source_text = f"원본: {source_name}"
        if source_url:
            source_text += f" · {source_url}"
        self.oui_tool_source_label.setText(source_text)
        self.oui_tool_source_label.setToolTip(source_url)

    def _refresh_oui_action_states(self) -> None:
        service = getattr(self.state, "oui_service", None)
        can_check = callable(getattr(service, "check_for_updates", None))
        can_update = callable(getattr(service, "refresh_cache", None))
        idle = not self._oui_operation_busy and not self._tool_status_busy
        self.oui_check_updates_button.setEnabled(idle and can_check)
        self.oui_update_button.setEnabled(idle and can_update)

    def _check_oui_updates(self) -> None:
        service = getattr(self.state, "oui_service", None)
        method = getattr(service, "check_for_updates", None)
        if not callable(method):
            QMessageBox.warning(
                self,
                "OUI 최신 여부 확인 불가",
                "현재 실행 환경에서는 OUI 데이터의 최신 여부를 확인할 수 없습니다.",
            )
            return
        self._start_oui_operation(
            "check",
            method,
            "IEEE 공식 원본과 로컬 OUI 데이터의 버전을 비교하는 중입니다...",
        )

    def _update_oui_data(self) -> None:
        service = getattr(self.state, "oui_service", None)
        method = getattr(service, "refresh_cache", None)
        if not callable(method):
            QMessageBox.warning(
                self,
                "OUI 데이터 업데이트 불가",
                "현재 실행 환경에서는 OUI 제조사 데이터를 업데이트할 수 없습니다.",
            )
            return
        self._start_oui_operation(
            "update",
            method,
            "IEEE 공식 OUI 원본 4개를 다운로드하고 검증하는 중입니다...",
        )

    def _start_oui_operation(
        self,
        kind: str,
        method,
        status_text: str,
    ) -> None:
        if (
            self._oui_operation_busy
            or self._tool_status_busy
            or self._iperf_install_cancel_event is not None
        ):
            return
        self._oui_operation_busy = True
        self._oui_operation_kind = kind
        self.tool_refresh_button.setEnabled(False)
        self.oui_check_updates_button.setEnabled(False)
        self.oui_update_button.setEnabled(False)
        self.oui_tool_result_label.setText(status_text)
        self.oui_tool_log.clear()
        self.oui_tool_log.show()
        self._job_runner.start(
            method,
            on_progress=lambda value: self.oui_tool_log.appendPlainText(str(value)),
            on_result=self._handle_oui_operation_result,
            on_error=self._handle_oui_operation_error,
            on_finished=self._finish_oui_operation,
        )

    def _handle_oui_operation_result(self, result: object) -> None:
        if not isinstance(result, OperationResult):
            self._handle_oui_operation_error(
                "OUI 작업 결과 형식을 확인하지 못했습니다."
            )
            return
        self.oui_tool_result_label.setText(result.message)
        details = result.details.strip()
        if details:
            self.oui_tool_log.appendPlainText(f"\n[결과]\n{details}")
        if self._oui_operation_kind == "update" and result.success:
            service = getattr(self.state, "oui_service", None)
            cache_status = getattr(service, "cache_status", None)
            if callable(cache_status):
                self._apply_oui_status(cache_status())
            self.integration_changed.emit("oui")

    def _handle_oui_operation_error(self, message: str) -> None:
        self.oui_tool_result_label.setText(f"OUI 작업 실패: {message}")
        self.oui_tool_log.appendPlainText(f"\n[오류] {message}")

    def _finish_oui_operation(self) -> None:
        self._oui_operation_busy = False
        self._oui_operation_kind = ""
        self.tool_refresh_button.setEnabled(
            not self._tool_status_busy
            and self._iperf_install_cancel_event is None
        )
        self._refresh_oui_action_states()

    def _manage_iperf(self) -> None:
        service = getattr(self.state, "iperf_service", None)
        manage_state = self._tool_manage_state
        if service is None or not bool(manage_state.get("available", False)):
            QMessageBox.warning(
                self,
                "winget 사용 불가",
                "이 시스템에서는 winget을 찾지 못해 프로그램 내에서 설치를 진행할 수 없습니다.",
            )
            return
        if not bool(manage_state.get("button_enabled", False)):
            QMessageBox.information(self, "최신 버전 사용 중", "현재 winget 기준 최신 iperf3가 설치되어 있습니다.")
            return

        action_label = "업데이트" if bool(manage_state.get("installed", False)) else "설치"
        if not confirm_risky_action(
            self,
            "iperf3 관리형 설치",
            impact=(
                f"winget으로 iperf3를 현재 사용자 범위에 {action_label}합니다. "
                f"패키지 ID: {manage_state.get('package_id', '')} / "
                f"패키지 페이지: {manage_state.get('package_url', '')}"
            ),
            reversibility="설치 후 제거는 winget 또는 Windows 앱 관리에서 별도로 수행해야 합니다.",
            output_location="설정 > 도구 연동의 진행 로그와 애플리케이션 로그에 기록됩니다.",
            question=f"iperf3를 winget 패키지로 {action_label}할까요?",
            confirm_text="설치 실행" if action_label == "설치" else "업데이트 실행",
        ):
            return

        self.iperf_tool_log.clear()
        self.iperf_tool_log.show()
        self._iperf_install_cancel_event = Event()
        self._set_iperf_install_running(True)
        self._job_runner.start(
            service.install_or_update_managed,
            cancel_event=self._iperf_install_cancel_event,
            on_progress=lambda value: self.iperf_tool_log.appendPlainText(str(value)),
            on_result=self._finish_iperf_install,
            on_finished=self._finish_iperf_install_job,
            error_title="iperf3 설치 실패",
        )

    def _finish_iperf_install(self, result: object) -> None:
        if not isinstance(result, OperationResult):
            self.iperf_tool_log.appendPlainText("\n[결과] 설치 결과 형식을 확인하지 못했습니다.")
            return
        summary = result.message
        if result.details:
            summary = f"{summary}\n{result.details}"
        self.iperf_tool_log.appendPlainText(f"\n[결과] {summary}")
        if result.success:
            self.integration_changed.emit("iperf3")

    def _finish_iperf_install_job(self) -> None:
        self._iperf_install_cancel_event = None
        self._set_iperf_install_running(False)
        self.refresh_tool_statuses()

    def _set_iperf_install_running(self, running: bool) -> None:
        self.tool_refresh_button.setEnabled(not running and not self._tool_status_busy)
        self.iperf_tool_manage_button.setEnabled(
            (not running) and bool(self._tool_manage_state.get("button_enabled", False))
        )
        self.iperf_tool_cancel_button.setVisible(running)
        self.iperf_tool_cancel_button.setEnabled(running)
        self.oui_check_updates_button.setEnabled(
            not running and not self._oui_operation_busy
        )
        self.oui_update_button.setEnabled(
            not running and not self._oui_operation_busy
        )
        for edit in self.ai_cli_path_edits.values():
            edit.setEnabled(not running)
        self.save_ai_cli_paths_button.setEnabled(not running)
        self.reset_ai_cli_paths_button.setEnabled(not running)

    def _cancel_iperf_install(self) -> None:
        if self._iperf_install_cancel_event is not None:
            self._iperf_install_cancel_event.set()

    def _path_settings_file(self) -> Path:
        explicit = getattr(self.state.paths, "path_settings", None)
        if explicit:
            return Path(explicit)
        return Path(self.state.paths.data_root) / "path_settings.json"

    def _load_saved_path_fields(self) -> None:
        effective = effective_path_settings(self.state.paths)
        stored = normalize_path_settings(load_json(self._path_settings_file(), {}))
        values = {
            key: str(stored.get(key, "") or effective[key])
            for key, _label, _tooltip in self._PATH_FIELDS
        }
        self._set_path_fields(values)
        self._saved_path_values = dict(values)
        self._path_dirty = False
        self._refresh_settings_file_preview(values)
        self._update_path_status()
        self._update_path_action_states()

    def _set_path_fields(self, values: dict[str, str]) -> None:
        for key, edit in self.path_edits.items():
            blocked = edit.blockSignals(True)
            edit.setText(str(values.get(key, "")))
            edit.blockSignals(blocked)

    def _current_path_values(self) -> dict[str, str]:
        return {key: edit.text().strip() for key, edit in self.path_edits.items()}

    def _path_fields_changed(self, _text: str = "") -> None:
        self._path_dirty = self._current_path_values() != self._saved_path_values
        self._refresh_settings_file_preview(self._current_path_values())
        self._update_path_action_states()
        if self._path_dirty:
            self.path_status_label.setText("저장되지 않은 경로 변경이 있습니다.")
        else:
            self._update_path_status()

    def _update_path_action_states(self) -> None:
        current_values = self._current_path_values()
        defaults = default_effective_path_settings(self.state.paths)
        differs_from_defaults = any(
            self._normalized_path_text(current_values.get(key, ""))
            != self._normalized_path_text(defaults.get(key, ""))
            for key, _label, _tooltip in self._PATH_FIELDS
        )
        self.save_paths_button.setEnabled(self._path_dirty)
        self.reset_paths_button.setEnabled(differs_from_defaults)

    def _change_directory(self, key: str) -> None:
        edit = self.path_edits[key]
        initial = edit.text().strip() or str(self.state.paths.data_root)
        label = next(label for field, label, _tooltip in self._PATH_FIELDS if field == key)
        selected = QFileDialog.getExistingDirectory(self, f"{label} 변경", initial)
        if selected:
            edit.setText(str(Path(selected)))

    def _open_path_directory(self, key: str) -> None:
        label = next(label for field, label, _tooltip in self._PATH_FIELDS if field == key)
        edited_text = self.path_edits[key].text().strip()
        try:
            target = Path(edited_text).expanduser() if edited_text else None
            target_is_directory = (
                target is not None
                and target.is_absolute()
                and target.is_dir()
            )
        except (OSError, RuntimeError, ValueError):
            target = None
            target_is_directory = False
        if not target_is_directory:
            QMessageBox.warning(
                self,
                "폴더를 열 수 없음",
                (
                    f"현재 입력된 {label} 경로가 존재하지 않거나 폴더가 아닙니다.\n"
                    "유효한 폴더로 변경하거나 경로 설정을 저장한 뒤 다시 시도해 주세요."
                ),
            )
            return

        try:
            open_in_explorer(target)
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "폴더 열기 실패",
                f"{label}를 열지 못했습니다: {exc}",
            )

    def _reset_path_fields(self) -> None:
        self._set_path_fields(default_effective_path_settings(self.state.paths))
        self._path_fields_changed()

    def _save_path_settings(self) -> None:
        requested_values = self._current_path_values()
        effective_before = effective_path_settings(self.state.paths)
        changed_fields = {
            key
            for key, _label, _tooltip in self._PATH_FIELDS
            if self._normalized_path_text(requested_values.get(key, ""))
            != self._normalized_path_text(effective_before.get(key, ""))
        }
        try:
            result = self.state.save_path_settings(requested_values)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "경로 설정 저장 실패", str(exc))
            self.path_status_label.setText(f"경로 설정을 저장하지 못했습니다: {exc}")
            return

        target_paths = result.get("target_paths")
        if target_paths is not None:
            effective = effective_path_settings(target_paths)
            self._saved_path_values = {
                key: str(effective[key])
                for key, _label, _tooltip in self._PATH_FIELDS
            }
            self._set_path_fields(self._saved_path_values)
        else:
            self._saved_path_values = self._current_path_values()
        self._path_dirty = False
        self._update_path_action_states()
        self._refresh_effective_path_labels()
        self._refresh_settings_file_preview(self._saved_path_values)

        copied = len(result.get("copied_files", ()))
        skipped = len(result.get("skipped_files", ()))
        details = ["경로 설정을 저장했습니다."]
        if copied:
            details.append(f"기존 설정 파일 {copied}개를 새 폴더에 복사했습니다.")
        if skipped:
            details.append(f"대상에 이미 있던 파일 {skipped}개는 덮어쓰지 않았습니다.")
        if "config_dir" in changed_fields:
            details.append("설정 파일 폴더 변경을 이후 설정 파일 작업에 적용했습니다.")
        if "exports_dir" in changed_fields:
            details.append("결과/내보내기 폴더 변경을 적용했습니다.")
        if "logs_dir" in changed_fields:
            details.append("로그 폴더 변경은 프로그램을 다시 시작하면 적용됩니다.")
        self.path_status_label.setText(" ".join(details))

    def _update_path_status(self) -> None:
        if not self._saved_path_values:
            self.path_status_label.clear()
            return
        current = effective_path_settings(self.state.paths)
        restart_required = (
            self._normalized_path_text(self._saved_path_values.get("logs_dir", ""))
            != self._normalized_path_text(current.get("logs_dir", ""))
        )
        if restart_required:
            self.path_status_label.setText("경로 설정이 저장되었습니다. 로그 폴더 변경은 프로그램 재시작 후 적용됩니다.")
        else:
            self.path_status_label.setText("표시된 저장 위치가 현재 적용 중입니다.")

    @staticmethod
    def _normalized_path_text(value: str) -> str:
        try:
            return str(Path(value).resolve(strict=False)).casefold()
        except (OSError, ValueError):
            return value.strip().casefold()

    def _refresh_effective_path_labels(self) -> None:
        paths = self.state.paths
        self.config_dir_label.setText(f"설정 파일 폴더: {paths.config_dir}")
        self.ip_profile_label.setText(f"주 설정 파일: {paths.app_config}\nIP 프로파일: {paths.ip_profiles}")
        self.log_dir_label.setText(f"로그 폴더: {paths.logs_dir}")
        self.export_dir_label.setText(f"결과/내보내기 폴더: {paths.exports_dir}")

    def _refresh_settings_file_preview(self, values: dict[str, str]) -> None:
        config_dir_text = str(values.get("config_dir", "") or "").strip()
        config_dir = Path(config_dir_text) if config_dir_text else Path(self.state.paths.config_dir)
        lines = [f"경로 설정(고정 위치): {self._path_settings_file()}"]
        lines.extend(f"{label}: {config_dir / filename}" for label, filename in self._CONFIG_FILE_NAMES)
        self.settings_files_view.setPlainText("\n".join(lines))

    def _reload_config_files(self) -> None:
        discarded_changes = self._path_dirty
        if discarded_changes and not confirm_risky_action(
            self,
            "저장하지 않은 경로 변경 버리기",
            impact="저장 위치 탭에 입력했지만 저장하지 않은 경로 변경을 버리고 디스크의 설정 파일을 다시 불러옵니다.",
            reversibility="버린 입력값은 자동으로 복구되지 않습니다.",
            output_location="설정 파일은 변경하지 않고 현재 화면만 디스크의 값으로 되돌립니다.",
            question="저장하지 않은 경로 변경을 버리고 다시 불러올까요?",
            confirm_text="버리고 다시 불러오기",
        ):
            return

        self._path_dirty = False
        try:
            self.state.reload_config_files()
        except (OSError, ValueError) as exc:
            self._path_dirty = discarded_changes
            self._update_path_action_states()
            QMessageBox.warning(self, "설정 파일 다시 불러오기 실패", str(exc))
            self.maintenance_status_label.setText(f"설정 파일을 다시 불러오지 못했습니다: {exc}")
            return

        suffix = " 저장하지 않은 경로 변경을 버렸습니다." if discarded_changes else ""
        self.maintenance_status_label.setText(f"설정 파일을 다시 불러왔습니다.{suffix}")

    def _reset_all_settings(self) -> None:
        if not confirm_risky_action(
            self,
            "모든 설정 초기화",
            impact=(
                "프로그램 옵션과 화면 상태, 저장된 IP·FTP·SCP 프로파일, 파일 전송 입력값, "
                "AI CLI·모델 설정, 사용자 장비 점검 규칙·파서를 기본값으로 되돌립니다. "
                "저장 위치는 기본 경로로 변경됩니다."
            ),
            reversibility=(
                "초기화한 사용자 설정과 프로파일은 자동으로 복구할 수 없습니다. "
                "애플리케이션 로그와 실행 결과·백업·내보낸 파일은 그대로 보존됩니다."
            ),
            output_location=(
                "초기화 수행 기록은 현재 애플리케이션 로그에 남습니다. "
                "기존 로그와 결과 파일의 위치 및 내용은 변경하지 않습니다."
            ),
            question="모든 사용자 설정을 초기화할까요?",
            confirm_text="모든 설정 초기화",
        ):
            return

        reset_method = getattr(self.state, "reset_all_settings", None)
        if not callable(reset_method):
            QMessageBox.warning(
                self,
                "설정 초기화 실패",
                "현재 실행 환경에서는 모든 설정 초기화를 지원하지 않습니다.",
            )
            return

        path_dirty_before = self._path_dirty
        self._path_dirty = False
        try:
            result = reset_method()
        except (OSError, RuntimeError, ValueError) as exc:
            self._path_dirty = path_dirty_before
            self._update_path_action_states()
            self.reset_settings_status_label.setText(
                f"설정을 초기화하지 못했습니다: {exc}"
            )
            QMessageBox.warning(self, "설정 초기화 실패", str(exc))
            return

        self._path_dirty = False
        self.reset_all_settings_button.setEnabled(False)
        self.integration_changed.emit("ai")
        restart_required = bool(
            result.get("restart_required", True)
            if isinstance(result, dict)
            else True
        )
        restart_message = (
            " 변경 사항을 완전히 적용하고 현재 화면 값이 다시 저장되지 않도록 프로그램을 "
            "종료한 뒤 다시 시작해 주세요."
            if restart_required
            else ""
        )
        message = (
            "모든 사용자 설정을 기본값으로 초기화했습니다. 기존 애플리케이션 로그와 "
            f"실행 결과·백업·내보낸 파일은 보존했습니다.{restart_message}"
        )
        self.reset_settings_status_label.setText(message)
        QMessageBox.information(self, "설정 초기화 완료", message)

    def _request_update_check(self) -> None:
        self.check_updates_requested.emit(self.current_update_config())

    def shutdown(self) -> None:
        self._cancel_iperf_install()
