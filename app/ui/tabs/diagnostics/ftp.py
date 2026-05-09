from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.ftp_models import FtpProfile, FtpRemoteEntry, FtpServerRuntime, FtpTransferResult
from app.models.result_models import OperationResult
from app.ui.dialogs.ftp_profile_dialog import FtpProfileDialog
from app.utils.file_utils import open_in_explorer, timestamped_export_path
from app.utils.validators import (
    ValidationError,
    default_ftp_port,
    normalize_remote_path,
    parse_positive_int,
)


class FtpDiagnosticsMixin:
    def _build_ftp_tab(self) -> QWidget:
        self._ftp_session_id = ""
        self._ftp_connected_runtime: dict = {}
        self._ftp_remote_entries: list[FtpRemoteEntry] = []
        self._ftp_transfer_row_map: dict[tuple[str, str, str, str], int] = {}
        self._ftp_client_logs: list[str] = []
        self._ftp_server_logs: list[str] = []
        self._ftp_server_runtime: FtpServerRuntime | None = None
        self._ftp_client_connected = False
        self._ftp_client_busy = False
        self._ftp_server_running = False
        self._scp_transfer_row_map: dict[tuple[str, str, str, str], int] = {}
        self._scp_client_logs: list[str] = []
        self._scp_server_logs: list[str] = []
        self._scp_server_runtime = None
        self._scp_client_busy = False
        self._scp_server_running = False
        self._tftp_transfer_row_map: dict[tuple[str, str, str, str], int] = {}
        self._tftp_client_logs: list[str] = []
        self._tftp_server_logs: list[str] = []
        self._tftp_server_runtime = None
        self._tftp_client_busy = False
        self._tftp_server_running = False
        self._file_transfer_role_modes = {0: 0, 1: 0}

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        selector_widget = QWidget()
        selector_layout = QHBoxLayout(selector_widget)
        selector_layout.setContentsMargins(0, 0, 0, 0)

        self.file_transfer_role_combo = QComboBox()
        self.file_transfer_role_combo.addItem("클라이언트", 0)
        self.file_transfer_role_combo.addItem("임시 서버", 1)
        self.file_transfer_mode_combo = QComboBox()
        self.file_transfer_mode_combo.addItem("FTP/FTPS/SFTP", 0)
        self.file_transfer_mode_combo.addItem("SCP", 1)
        self.file_transfer_mode_combo.addItem("TFTP", 2)

        selector_layout.addWidget(QLabel("역할"))
        selector_layout.addWidget(self.file_transfer_role_combo)
        selector_layout.addSpacing(12)
        selector_layout.addWidget(QLabel("전송 방식"))
        selector_layout.addWidget(self.file_transfer_mode_combo)
        selector_layout.addStretch(1)
        layout.addWidget(selector_widget)

        self.file_transfer_page_stack = QStackedWidget()
        self.file_transfer_page_stack.addWidget(self._build_ftp_client_page())
        self.file_transfer_page_stack.addWidget(self._build_scp_client_page())
        self.file_transfer_page_stack.addWidget(self._build_tftp_client_page())
        self.file_transfer_page_stack.addWidget(self._build_ftp_server_page())
        self.file_transfer_page_stack.addWidget(self._build_scp_server_page())
        self.file_transfer_page_stack.addWidget(self._build_tftp_server_page())

        self.file_transfer_page_stack.setStyleSheet("background:#ffffff;")
        self.file_transfer_page_stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout.addWidget(self.file_transfer_page_stack)
        layout.addStretch(1)

        self.file_transfer_role_combo.currentIndexChanged.connect(self._handle_file_transfer_role_changed)
        self.file_transfer_mode_combo.currentIndexChanged.connect(self._handle_file_transfer_mode_changed)

        self.ftp_inner_tab = self.file_transfer_page_stack

        self._reload_ftp_profiles()
        self._reload_scp_profiles()
        self._restore_ftp_runtime_state()
        self._restore_scp_runtime_state()
        self._restore_tftp_runtime_state()
        self._set_ftp_client_connected(False)
        self._set_ftp_client_busy(False)
        self._set_ftp_server_running(False)
        self._set_scp_client_busy(False)
        self._set_scp_server_running(False)
        self._set_tftp_client_busy(False)
        self._set_tftp_server_running(False)
        self._refresh_ftp_client_support_notice()
        self._refresh_ftp_server_support_notice()
        self._refresh_scp_client_support_notice()
        self._refresh_scp_server_support_notice()
        self._refresh_tftp_support_notice()
        self._sync_file_transfer_page()
        return page

    def _current_ftp_legacy_subtab(self) -> int:
        role_index = self.file_transfer_role_combo.currentIndex()
        mode_index = self._file_transfer_role_modes.get(role_index, 0)
        return role_index * self.file_transfer_mode_combo.count() + mode_index

    def _build_ftp_tab_state(self) -> dict:
        return {
            "role_tab": self.file_transfer_role_combo.currentIndex(),
            "client_protocol_tab": self._file_transfer_role_modes.get(0, 0),
            "server_protocol_tab": self._file_transfer_role_modes.get(1, 0),
            "current_subtab": self._current_ftp_legacy_subtab(),
        }

    def _restore_ftp_tab_state(self, ftp_state: dict | None, scp_state: dict | None = None) -> None:
        ftp_data = ftp_state if isinstance(ftp_state, dict) else {}
        scp_data = scp_state if isinstance(scp_state, dict) else {}

        role_index = ftp_data.get("role_tab")
        client_protocol_index = ftp_data.get("client_protocol_tab")
        server_protocol_index = ftp_data.get("server_protocol_tab")

        if role_index is None and client_protocol_index is None and server_protocol_index is None:
            if "current_subtab" in ftp_data:
                legacy_index = int(ftp_data.get("current_subtab", 0) or 0)
            elif "current_subtab" in scp_data:
                legacy_index = 2 + int(scp_data.get("current_subtab", 0) or 0)
            else:
                legacy_index = 0

            if legacy_index >= 4:
                role_index = legacy_index // self.file_transfer_mode_combo.count()
                if role_index <= 0:
                    client_protocol_index = legacy_index % self.file_transfer_mode_combo.count()
                else:
                    server_protocol_index = legacy_index % self.file_transfer_mode_combo.count()
            elif legacy_index <= 0:
                role_index = 0
                client_protocol_index = 0
            elif legacy_index == 1:
                role_index = 1
                server_protocol_index = 0
            elif legacy_index == 2:
                role_index = 0
                client_protocol_index = 1
            else:
                role_index = 1
                server_protocol_index = 1

        role_index = int(role_index or 0)
        client_protocol_index = int(client_protocol_index or 0)
        server_protocol_index = int(server_protocol_index or 0)
        role_index = 0 if role_index < 0 else 1 if role_index > 1 else role_index
        mode_max = self.file_transfer_mode_combo.count() - 1
        client_protocol_index = 0 if client_protocol_index < 0 else mode_max if client_protocol_index > mode_max else client_protocol_index
        server_protocol_index = 0 if server_protocol_index < 0 else mode_max if server_protocol_index > mode_max else server_protocol_index

        self._file_transfer_role_modes[0] = client_protocol_index
        self._file_transfer_role_modes[1] = server_protocol_index
        self.file_transfer_role_combo.blockSignals(True)
        self.file_transfer_role_combo.setCurrentIndex(role_index)
        self.file_transfer_role_combo.blockSignals(False)
        self.file_transfer_mode_combo.blockSignals(True)
        self.file_transfer_mode_combo.setCurrentIndex(self._file_transfer_role_modes.get(role_index, 0))
        self.file_transfer_mode_combo.blockSignals(False)
        self._sync_file_transfer_page()

    def _handle_file_transfer_role_changed(self, index: int) -> None:
        mode_index = self._file_transfer_role_modes.get(index, 0)
        self.file_transfer_mode_combo.blockSignals(True)
        self.file_transfer_mode_combo.setCurrentIndex(mode_index)
        self.file_transfer_mode_combo.blockSignals(False)
        self._sync_file_transfer_page()
        self._handle_ftp_tab_changed(index)

    def _handle_file_transfer_mode_changed(self, index: int) -> None:
        role_index = self.file_transfer_role_combo.currentIndex()
        self._file_transfer_role_modes[role_index] = index
        self._sync_file_transfer_page()
        self._handle_ftp_tab_changed(index)

    def _sync_file_transfer_page(self) -> None:
        role_index = self.file_transfer_role_combo.currentIndex()
        mode_index = self._file_transfer_role_modes.get(role_index, self.file_transfer_mode_combo.currentIndex())
        page_index = role_index * self.file_transfer_mode_combo.count() + mode_index
        if 0 <= page_index < self.file_transfer_page_stack.count():
            self.file_transfer_page_stack.setCurrentIndex(page_index)

    def _set_compact_transfer_group(self, *widgets: QWidget) -> None:
        for widget in widgets:
            widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def _build_ftp_client_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        connection_group = QGroupBox("FTP 클라이언트")
        connection_layout = QVBoxLayout(connection_group)

        profile_row = QHBoxLayout()
        self.ftp_profile_combo = QComboBox()
        self.ftp_profile_add_button = QPushButton("추가")
        self.ftp_profile_edit_button = QPushButton("수정")
        self.ftp_profile_delete_button = QPushButton("삭제")
        profile_row.addWidget(QLabel("프로필"))
        profile_row.addWidget(self.ftp_profile_combo, 1)
        profile_row.addWidget(self.ftp_profile_add_button)
        profile_row.addWidget(self.ftp_profile_edit_button)
        profile_row.addWidget(self.ftp_profile_delete_button)
        connection_layout.addLayout(profile_row)

        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)

        self.ftp_client_protocol_combo = QComboBox()
        self.ftp_client_protocol_combo.addItem("FTP", "ftp")
        self.ftp_client_protocol_combo.addItem("FTPS", "ftps")
        self.ftp_client_protocol_combo.addItem("SFTP", "sftp")
        self.ftp_client_host_edit = QLineEdit()
        self.ftp_client_host_edit.setPlaceholderText("예: 192.168.0.10 또는 ftp.example.com")
        self.ftp_client_port_edit = QLineEdit()
        self.ftp_client_port_edit.setPlaceholderText("21")
        self.ftp_client_username_edit = QLineEdit()
        self.ftp_client_username_edit.setPlaceholderText("예: operator")
        self.ftp_client_password_edit = QLineEdit()
        self.ftp_client_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ftp_client_password_edit.setPlaceholderText("세션 중에만 사용합니다")
        self.ftp_client_passive_check = QCheckBox("패시브 모드")
        self.ftp_client_timeout_edit = QLineEdit()
        self.ftp_client_timeout_edit.setPlaceholderText("15")
        self.ftp_client_remote_path_edit = QLineEdit()
        self.ftp_client_remote_path_edit.setPlaceholderText("/")
        self.ftp_client_local_folder_edit = QLineEdit()
        self.ftp_client_local_folder_edit.setPlaceholderText("예: C:\\Temp")
        self.ftp_client_local_browse_button = QPushButton("로컬 폴더")

        form.addWidget(QLabel("프로토콜"), 0, 0)
        form.addWidget(self.ftp_client_protocol_combo, 0, 1)
        form.addWidget(QLabel("호스트"), 0, 2)
        form.addWidget(self.ftp_client_host_edit, 0, 3)

        form.addWidget(QLabel("포트"), 1, 0)
        form.addWidget(self.ftp_client_port_edit, 1, 1)
        form.addWidget(QLabel("사용자명"), 1, 2)
        form.addWidget(self.ftp_client_username_edit, 1, 3)

        form.addWidget(QLabel("비밀번호"), 2, 0)
        form.addWidget(self.ftp_client_password_edit, 2, 1)
        form.addWidget(QLabel("타임아웃(초)"), 2, 2)
        form.addWidget(self.ftp_client_timeout_edit, 2, 3)

        form.addWidget(QLabel("원격 경로"), 3, 0)
        form.addWidget(self.ftp_client_remote_path_edit, 3, 1)
        form.addWidget(QLabel("로컬 폴더"), 3, 2)
        local_row = QWidget()
        local_row_layout = QHBoxLayout(local_row)
        local_row_layout.setContentsMargins(0, 0, 0, 0)
        local_row_layout.addWidget(self.ftp_client_local_folder_edit, 1)
        local_row_layout.addWidget(self.ftp_client_local_browse_button)
        form.addWidget(local_row, 3, 3)

        form.addWidget(self.ftp_client_passive_check, 4, 1)
        connection_layout.addLayout(form)

        button_row = QHBoxLayout()
        self.ftp_client_connect_button = QPushButton("연결")
        self.ftp_client_refresh_button = QPushButton("새로고침")
        self.ftp_client_disconnect_button = QPushButton("연결 종료")
        self.ftp_client_upload_button = QPushButton("업로드")
        self.ftp_client_download_button = QPushButton("다운로드")
        self.ftp_client_mkdir_button = QPushButton("새 폴더")
        self.ftp_client_rename_button = QPushButton("이름 변경")
        self.ftp_client_delete_button = QPushButton("삭제")
        self.ftp_client_cancel_button = QPushButton("취소")
        for button in (
            self.ftp_client_connect_button,
            self.ftp_client_refresh_button,
            self.ftp_client_disconnect_button,
            self.ftp_client_upload_button,
            self.ftp_client_download_button,
            self.ftp_client_mkdir_button,
            self.ftp_client_rename_button,
            self.ftp_client_delete_button,
            self.ftp_client_cancel_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)
        connection_layout.addLayout(button_row)
        self.ftp_client_support_label = QLabel("")
        self.ftp_client_support_label.setWordWrap(True)
        self.ftp_client_support_label.hide()

        self.ftp_client_status_label = QLabel("연결 안 됨")
        self.ftp_client_fingerprint_label = QLabel("-")
        self.ftp_client_fingerprint_label.setWordWrap(True)
        self.ftp_client_fingerprint_label.hide()
        connection_layout.addWidget(self.ftp_client_status_label)
        connection_layout.addWidget(self.ftp_client_fingerprint_label)
        connection_layout.addWidget(self.ftp_client_support_label)
        self._set_compact_transfer_group(connection_group)
        layout.addWidget(connection_group)

        self.ftp_remote_group = QGroupBox("원격 목록")
        remote_layout = QVBoxLayout(self.ftp_remote_group)
        self.ftp_remote_status_label = QLabel("연결 후 원격 목록이 여기에 표시됩니다.")
        self.ftp_remote_status_label.setStyleSheet("color:#667085;")
        remote_layout.addWidget(self.ftp_remote_status_label)
        self.ftp_remote_table = QTableWidget(0, 6)
        self.ftp_remote_table.setHorizontalHeaderLabels(
            ["이름", "종류", "크기", "수정 시각", "권한", "원격 경로"]
        )
        self._setup_table(self.ftp_remote_table)
        self._set_stretch_columns(self.ftp_remote_table, 0, 5)
        self.ftp_remote_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.ftp_remote_table.setMinimumHeight(150)
        self.ftp_remote_table.setMaximumHeight(220)
        self.ftp_remote_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.ftp_remote_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.ftp_remote_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        remote_layout.addWidget(self.ftp_remote_table)
        self._set_compact_transfer_group(self.ftp_remote_group)
        layout.addWidget(self.ftp_remote_group)

        self.ftp_client_activity_splitter = QGroupBox("전송 결과 / 실시간 로그")
        result_layout = QVBoxLayout(self.ftp_client_activity_splitter)
        self.ftp_transfer_table = QTableWidget(0, 9)
        self.ftp_transfer_table.setHorizontalHeaderLabels(
            ["시각", "작업", "원본", "대상", "크기", "전송량", "소요시간", "상태", "오류"]
        )
        self._setup_table(self.ftp_transfer_table)
        self._set_stretch_columns(self.ftp_transfer_table, 2, 3, 8)
        self.ftp_transfer_table.setMinimumHeight(130)
        self.ftp_transfer_table.setMaximumHeight(180)
        result_layout.addWidget(self.ftp_transfer_table)

        result_button_row = QHBoxLayout()
        self.ftp_transfer_export_button = QPushButton("전송 결과 CSV 저장")
        self.ftp_client_log_export_button = QPushButton("클라이언트 로그 TXT 저장")
        result_button_row.addWidget(self.ftp_transfer_export_button)
        result_button_row.addWidget(self.ftp_client_log_export_button)
        result_button_row.addStretch(1)
        result_layout.addLayout(result_button_row)
        result_layout.addWidget(QLabel("실시간 로그"))
        self.ftp_client_log_output = self._output()
        self.ftp_client_log_output.setPlaceholderText("연결 후 작업을 시작하면 로그가 여기에 표시됩니다.")
        self.ftp_client_log_output.setMinimumHeight(110)
        self.ftp_client_log_output.setMaximumHeight(150)
        result_layout.addWidget(self.ftp_client_log_output)
        self._set_compact_transfer_group(self.ftp_client_activity_splitter)
        layout.addWidget(self.ftp_client_activity_splitter)
        layout.addStretch(1)

        self.ftp_profile_combo.currentIndexChanged.connect(self._apply_selected_ftp_profile)
        self.ftp_profile_add_button.clicked.connect(self._add_ftp_profile)
        self.ftp_profile_edit_button.clicked.connect(self._edit_selected_ftp_profile)
        self.ftp_profile_delete_button.clicked.connect(self._delete_selected_ftp_profile)
        self.ftp_client_protocol_combo.currentIndexChanged.connect(self._sync_ftp_client_protocol_state)
        self.ftp_client_local_browse_button.clicked.connect(self._choose_ftp_local_folder)
        self.ftp_client_connect_button.clicked.connect(self._connect_ftp_client)
        self.ftp_client_refresh_button.clicked.connect(self._refresh_ftp_remote_list)
        self.ftp_client_disconnect_button.clicked.connect(self._disconnect_ftp_client)
        self.ftp_client_upload_button.clicked.connect(self._upload_ftp_files)
        self.ftp_client_download_button.clicked.connect(self._download_ftp_files)
        self.ftp_client_mkdir_button.clicked.connect(self._create_ftp_remote_folder)
        self.ftp_client_rename_button.clicked.connect(self._rename_ftp_remote_entry)
        self.ftp_client_delete_button.clicked.connect(self._delete_ftp_remote_entries)
        self.ftp_client_cancel_button.clicked.connect(self._cancel_ftp_client_job)
        self.ftp_transfer_export_button.clicked.connect(self._export_ftp_transfer_results)
        self.ftp_client_log_export_button.clicked.connect(self._export_ftp_client_logs)
        self.ftp_remote_table.itemDoubleClicked.connect(self._handle_ftp_remote_double_click)
        return page

    def _build_ftp_server_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        server_group = QGroupBox("임시 FTP 서버")
        server_layout = QVBoxLayout(server_group)

        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)

        self.ftp_server_protocol_combo = QComboBox()
        self.ftp_server_protocol_combo.addItem("FTP", "ftp")
        self.ftp_server_protocol_combo.addItem("FTPS", "ftps")
        self.ftp_server_protocol_combo.addItem("SFTP", "sftp")
        self.ftp_server_bind_host_edit = QLineEdit()
        self.ftp_server_bind_host_edit.setPlaceholderText("예: 0.0.0.0")
        self.ftp_server_port_edit = QLineEdit()
        self.ftp_server_port_edit.setPlaceholderText("2121")
        self.ftp_server_root_edit = QLineEdit()
        self.ftp_server_root_edit.setPlaceholderText("예: C:\\Transfer")
        self.ftp_server_root_browse_button = QPushButton("루트 폴더")
        self.ftp_server_username_edit = QLineEdit()
        self.ftp_server_username_edit.setPlaceholderText("예: netops")
        self.ftp_server_password_edit = QLineEdit()
        self.ftp_server_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ftp_server_password_edit.setPlaceholderText("서버 접속 비밀번호")
        self.ftp_server_readonly_check = QCheckBox("읽기 전용")
        self.ftp_server_anonymous_check = QCheckBox("익명 읽기 전용 허용")

        form.addWidget(QLabel("프로토콜"), 0, 0)
        form.addWidget(self.ftp_server_protocol_combo, 0, 1)
        form.addWidget(QLabel("바인드 IP"), 0, 2)
        form.addWidget(self.ftp_server_bind_host_edit, 0, 3)

        form.addWidget(QLabel("포트"), 1, 0)
        form.addWidget(self.ftp_server_port_edit, 1, 1)
        form.addWidget(QLabel("공유 루트"), 1, 2)
        root_row = QWidget()
        root_row_layout = QHBoxLayout(root_row)
        root_row_layout.setContentsMargins(0, 0, 0, 0)
        root_row_layout.addWidget(self.ftp_server_root_edit, 1)
        root_row_layout.addWidget(self.ftp_server_root_browse_button)
        form.addWidget(root_row, 1, 3)

        form.addWidget(QLabel("계정"), 2, 0)
        form.addWidget(self.ftp_server_username_edit, 2, 1)
        form.addWidget(QLabel("비밀번호"), 2, 2)
        form.addWidget(self.ftp_server_password_edit, 2, 3)

        form.addWidget(self.ftp_server_readonly_check, 3, 1)
        form.addWidget(self.ftp_server_anonymous_check, 3, 3)
        server_layout.addLayout(form)

        button_row = QHBoxLayout()
        self.ftp_server_start_button = QPushButton("시작")
        self.ftp_server_stop_button = QPushButton("중지")
        self.ftp_server_open_root_button = QPushButton("루트 폴더 열기")
        button_row.addWidget(self.ftp_server_start_button)
        button_row.addWidget(self.ftp_server_stop_button)
        button_row.addWidget(self.ftp_server_open_root_button)
        button_row.addStretch(1)
        server_layout.addLayout(button_row)
        self.ftp_server_support_label = QLabel("")
        self.ftp_server_support_label.setWordWrap(True)
        self.ftp_server_support_label.hide()
        server_layout.addWidget(self.ftp_server_support_label)

        status_form = QFormLayout()
        self.ftp_server_state_label = QLabel("중지됨")
        self.ftp_server_endpoint_label = QLabel("-")
        self.ftp_server_sessions_label = QLabel("0")
        self.ftp_server_fingerprint_label = QLabel("-")
        self.ftp_server_fingerprint_label.setWordWrap(True)
        status_form.addRow("상태", self.ftp_server_state_label)
        status_form.addRow("접속 주소", self.ftp_server_endpoint_label)
        status_form.addRow("세션 수", self.ftp_server_sessions_label)
        status_form.addRow("지문", self.ftp_server_fingerprint_label)
        server_layout.addLayout(status_form)
        self._set_compact_transfer_group(server_group)
        layout.addWidget(server_group)

        self.ftp_server_log_group = QGroupBox("서버 로그")
        server_log_layout = QVBoxLayout(self.ftp_server_log_group)
        self.ftp_server_log_output = self._output()
        self.ftp_server_log_output.setPlaceholderText("서버를 시작하면 접속 및 전송 로그가 여기에 표시됩니다.")
        self.ftp_server_log_output.setMinimumHeight(120)
        self.ftp_server_log_output.setMaximumHeight(170)
        server_log_layout.addWidget(self.ftp_server_log_output)
        server_log_button_row = QHBoxLayout()
        self.ftp_server_log_export_button = QPushButton("서버 로그 TXT 저장")
        server_log_button_row.addWidget(self.ftp_server_log_export_button)
        server_log_button_row.addStretch(1)
        server_log_layout.addLayout(server_log_button_row)
        self._set_compact_transfer_group(self.ftp_server_log_group)
        layout.addWidget(self.ftp_server_log_group)
        layout.addStretch(1)

        self.ftp_server_protocol_combo.currentIndexChanged.connect(self._sync_ftp_server_protocol_state)
        self.ftp_server_root_browse_button.clicked.connect(self._choose_ftp_server_root)
        self.ftp_server_start_button.clicked.connect(self._start_ftp_server)
        self.ftp_server_stop_button.clicked.connect(self._stop_ftp_server)
        self.ftp_server_open_root_button.clicked.connect(self._open_ftp_server_root)
        self.ftp_server_log_export_button.clicked.connect(self._export_ftp_server_logs)
        return page

    def _reload_ftp_profiles(self) -> None:
        current_name = self.ftp_profile_combo.currentText().strip() if hasattr(self, "ftp_profile_combo") else ""
        self.ftp_profile_combo.blockSignals(True)
        self.ftp_profile_combo.clear()
        self.ftp_profile_combo.addItem("프로필 선택", "")
        for profile in self.state.ftp_profiles:
            self.ftp_profile_combo.addItem(profile.name, profile.name)
        index = self.ftp_profile_combo.findData(current_name)
        self.ftp_profile_combo.setCurrentIndex(index if index >= 0 else 0)
        self.ftp_profile_combo.blockSignals(False)

    def _get_ftp_profile_by_name(self, name: str) -> FtpProfile | None:
        for profile in self.state.ftp_profiles:
            if profile.name == name:
                return profile
        return None

    def _apply_selected_ftp_profile(self) -> None:
        profile_name = str(self.ftp_profile_combo.currentData() or "").strip()
        profile = self._get_ftp_profile_by_name(profile_name)
        if profile is None:
            return
        self._apply_ftp_profile_to_form(profile)

    def _apply_ftp_profile_to_form(self, profile: FtpProfile) -> None:
        index = self.ftp_client_protocol_combo.findData(profile.protocol)
        if index >= 0:
            self.ftp_client_protocol_combo.setCurrentIndex(index)
        self.ftp_client_host_edit.setText(profile.host)
        self.ftp_client_port_edit.setText(str(profile.port))
        self.ftp_client_username_edit.setText(profile.username)
        self.ftp_client_remote_path_edit.setText(profile.remote_path)
        self.ftp_client_passive_check.setChecked(profile.passive_mode)
        self.ftp_client_timeout_edit.setText(str(profile.timeout_seconds))
        self._sync_ftp_client_protocol_state()

    def _add_ftp_profile(self) -> None:
        dialog = FtpProfileDialog(self)
        if dialog.exec():
            profile = dialog.profile_data()
            profiles = list(self.state.ftp_profiles)
            profiles.append(profile)
            profiles.sort(key=lambda item: item.name.lower())
            self.state.save_ftp_profiles(profiles)
            self._reload_ftp_profiles()
            index = self.ftp_profile_combo.findData(profile.name)
            if index >= 0:
                self.ftp_profile_combo.setCurrentIndex(index)

    def _edit_selected_ftp_profile(self) -> None:
        profile_name = str(self.ftp_profile_combo.currentData() or "").strip()
        profile = self._get_ftp_profile_by_name(profile_name)
        if profile is None:
            QMessageBox.warning(self, "선택 필요", "수정할 FTP 프로필을 먼저 선택해 주세요.")
            return
        dialog = FtpProfileDialog(self, profile)
        if dialog.exec():
            updated = dialog.profile_data()
            profiles = [updated if item.name == profile.name else item for item in self.state.ftp_profiles]
            profiles.sort(key=lambda item: item.name.lower())
            self.state.save_ftp_profiles(profiles)
            self._reload_ftp_profiles()
            index = self.ftp_profile_combo.findData(updated.name)
            if index >= 0:
                self.ftp_profile_combo.setCurrentIndex(index)

    def _delete_selected_ftp_profile(self) -> None:
        profile_name = str(self.ftp_profile_combo.currentData() or "").strip()
        profile = self._get_ftp_profile_by_name(profile_name)
        if profile is None:
            QMessageBox.warning(self, "선택 필요", "삭제할 FTP 프로필을 먼저 선택해 주세요.")
            return
        if QMessageBox.question(self, "프로필 삭제", f"'{profile.name}' 프로필을 삭제할까요?") != QMessageBox.Yes:
            return
        profiles = [item for item in self.state.ftp_profiles if item.name != profile.name]
        self.state.save_ftp_profiles(profiles)
        self._reload_ftp_profiles()

    def _sync_ftp_client_protocol_state(self) -> None:
        protocol = str(self.ftp_client_protocol_combo.currentData() or "ftp")
        passive_enabled = protocol in {"ftp", "ftps"}
        self.ftp_client_passive_check.setEnabled(passive_enabled)
        if not passive_enabled:
            self.ftp_client_passive_check.setChecked(False)
        self.ftp_client_port_edit.setPlaceholderText(str(default_ftp_port(protocol)))
        self._refresh_ftp_client_support_notice()

    def _sync_ftp_server_protocol_state(self) -> None:
        protocol = str(self.ftp_server_protocol_combo.currentData() or "ftp")
        self.ftp_server_port_edit.setPlaceholderText(str(default_ftp_port(protocol, server_mode=True)))
        sftp_mode = protocol == "sftp"
        self.ftp_server_anonymous_check.setEnabled(not sftp_mode)
        if sftp_mode:
            self.ftp_server_anonymous_check.setChecked(False)
        self._refresh_ftp_server_support_notice()

    def _handle_ftp_tab_changed(self, _index: int) -> None:
        self._refresh_ftp_client_support_notice()
        self._refresh_ftp_server_support_notice()
        self._refresh_scp_client_support_notice()
        self._refresh_scp_server_support_notice()
        self._refresh_tftp_support_notice()

    def _refresh_ftp_client_support_notice(self) -> None:
        support = self.state.ftp_client_service.runtime_support_status(
            str(self.ftp_client_protocol_combo.currentData() or "ftp")
        )
        self._apply_ftp_support_label(self.ftp_client_support_label, support)

    def _refresh_ftp_server_support_notice(self) -> None:
        support = self.state.ftp_server_service.runtime_support_status(
            str(self.ftp_server_protocol_combo.currentData() or "ftp")
        )
        self._apply_ftp_support_label(self.ftp_server_support_label, support)

    def _apply_ftp_support_label(self, label: QLabel, support: OperationResult) -> None:
        if support.success:
            label.clear()
            label.hide()
            return
        label.setText(support.message)
        label.setStyleSheet("color:#b71c1c;")
        label.show()

    def _show_ftp_support_warning(self, title: str, support: OperationResult) -> None:
        text = support.message
        if support.details:
            text += f"\n\n{support.details}"
        QMessageBox.warning(self, title, text)

    def _choose_ftp_local_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "로컬 폴더 선택",
            self.ftp_client_local_folder_edit.text().strip() or str(self.state.paths.root),
        )
        if folder:
            self.ftp_client_local_folder_edit.setText(folder)

    def _connect_ftp_client(self) -> None:
        if self._ftp_client_connected:
            QMessageBox.information(self, "이미 연결됨", "먼저 현재 연결을 종료해 주세요.")
            return
        support = self.state.ftp_client_service.runtime_support_status(
            str(self.ftp_client_protocol_combo.currentData() or "ftp")
        )
        self._apply_ftp_support_label(self.ftp_client_support_label, support)
        if not support.success:
            self._show_ftp_support_warning("FTP 클라이언트 준비 필요", support)
            return
        self.ftp_client_log_output.clear()
        self._ftp_client_logs = []
        self._ftp_transfer_row_map.clear()
        self.ftp_transfer_table.setRowCount(0)
        self.ftp_client_cancel_event = None
        self._set_ftp_client_busy(True)
        self._start_worker(
            self.state.ftp_client_service.connect,
            str(self.ftp_client_protocol_combo.currentData() or "ftp"),
            self.ftp_client_host_edit.text().strip(),
            self.ftp_client_port_edit.text().strip(),
            self.ftp_client_username_edit.text().strip(),
            self.ftp_client_password_edit.text(),
            self.ftp_client_passive_check.isChecked(),
            self.ftp_client_timeout_edit.text().strip() or "15",
            self.ftp_client_remote_path_edit.text().strip() or "/",
            on_progress=self._handle_ftp_client_progress,
            on_result=self._finish_ftp_connect,
            on_finished=lambda: self._set_ftp_client_busy(False),
            error_title="FTP 연결 실패",
        )

    def _finish_ftp_connect(self, result: OperationResult) -> None:
        if not result.success:
            QMessageBox.warning(self, "FTP 연결 실패", result.message)
            return
        payload = result.payload if isinstance(result.payload, dict) else {}
        self._ftp_session_id = str(payload.get("session_id", "") or "")
        self._ftp_connected_runtime = payload
        self.ftp_client_password_edit.clear()
        self.ftp_client_remote_path_edit.setText(str(payload.get("cwd", "/") or "/"))
        self._populate_ftp_remote_entries(payload.get("entries", []))
        fingerprint = str(payload.get("host_key_fingerprint", "") or "")
        self.ftp_client_fingerprint_label.setText(fingerprint or "-")
        self.ftp_client_status_label.setText(result.message)
        self._set_ftp_client_connected(True)

    def _disconnect_ftp_client(self) -> None:
        if not self._ftp_session_id:
            return
        session_id = self._ftp_session_id
        self._set_ftp_client_busy(True)
        self._start_worker(
            self.state.ftp_client_service.disconnect,
            session_id,
            on_result=self._finish_ftp_disconnect,
            on_finished=lambda: self._set_ftp_client_busy(False),
            error_title="FTP 연결 종료 실패",
        )

    def _finish_ftp_disconnect(self, result: OperationResult) -> None:
        self._ftp_session_id = ""
        self._ftp_connected_runtime = {}
        self._ftp_remote_entries = []
        self.ftp_remote_table.setRowCount(0)
        self.ftp_client_status_label.setText(result.message)
        self.ftp_client_fingerprint_label.setText("-")
        self._set_ftp_client_connected(False)

    def _refresh_ftp_remote_list(self) -> None:
        if not self._ftp_session_id:
            QMessageBox.warning(self, "연결 필요", "먼저 FTP 서버에 연결해 주세요.")
            return
        self._set_ftp_client_busy(True)
        self._start_worker(
            self.state.ftp_client_service.list_directory,
            self._ftp_session_id,
            self.ftp_client_remote_path_edit.text().strip() or "/",
            on_progress=self._handle_ftp_client_progress,
            on_result=self._finish_ftp_refresh,
            on_finished=lambda: self._set_ftp_client_busy(False),
            error_title="원격 목록 조회 실패",
        )

    def _finish_ftp_refresh(self, result: OperationResult) -> None:
        payload = result.payload if isinstance(result.payload, dict) else {}
        self.ftp_client_remote_path_edit.setText(str(payload.get("cwd", "/") or "/"))
        self._populate_ftp_remote_entries(payload.get("entries", []))
        self.ftp_client_status_label.setText(result.message)

    def _populate_ftp_remote_entries(self, entries) -> None:
        self._ftp_remote_entries = list(entries or [])
        self.ftp_remote_table.setRowCount(0)
        for entry in self._ftp_remote_entries:
            row = self.ftp_remote_table.rowCount()
            self.ftp_remote_table.insertRow(row)
            values = [
                entry.name,
                "폴더" if entry.is_dir else "파일",
                entry.size_text,
                entry.modified_at or "-",
                entry.permissions or "-",
                entry.remote_path,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1 and entry.is_dir:
                    item.setForeground(QColor("#1565c0"))
                self.ftp_remote_table.setItem(row, column, item)

    def _selected_ftp_remote_entries(self) -> list[FtpRemoteEntry]:
        rows = self._selected_rows(self.ftp_remote_table)
        return [self._ftp_remote_entries[row] for row in rows if 0 <= row < len(self._ftp_remote_entries)]

    def _upload_ftp_files(self) -> None:
        if not self._ftp_session_id:
            QMessageBox.warning(self, "연결 필요", "먼저 FTP 서버에 연결해 주세요.")
            return
        files, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "업로드할 파일 선택",
            self.ftp_client_local_folder_edit.text().strip() or str(self.state.paths.root),
        )
        if not files:
            return
        self.ftp_client_cancel_event = self._new_ftp_cancel_event()
        self._set_ftp_client_busy(True)
        self._start_worker(
            self.state.ftp_client_service.upload_files,
            self._ftp_session_id,
            files,
            self.ftp_client_remote_path_edit.text().strip() or "/",
            cancel_event=self.ftp_client_cancel_event,
            on_progress=self._handle_ftp_client_progress,
            on_result=self._finish_ftp_transfer_job,
            on_finished=self._finish_ftp_job_with_refresh,
            error_title="FTP 업로드 실패",
        )

    def _download_ftp_files(self) -> None:
        if not self._ftp_session_id:
            QMessageBox.warning(self, "연결 필요", "먼저 FTP 서버에 연결해 주세요.")
            return
        entries = self._selected_ftp_remote_entries()
        if not entries:
            QMessageBox.warning(self, "선택 필요", "다운로드할 원격 파일을 선택해 주세요.")
            return
        local_folder = self.ftp_client_local_folder_edit.text().strip()
        if not local_folder:
            self._choose_ftp_local_folder()
            local_folder = self.ftp_client_local_folder_edit.text().strip()
        if not local_folder:
            return
        self.ftp_client_cancel_event = self._new_ftp_cancel_event()
        self._set_ftp_client_busy(True)
        self._start_worker(
            self.state.ftp_client_service.download_files,
            self._ftp_session_id,
            entries,
            local_folder,
            cancel_event=self.ftp_client_cancel_event,
            on_progress=self._handle_ftp_client_progress,
            on_result=self._finish_ftp_transfer_job,
            on_finished=self._finish_ftp_job_with_refresh,
            error_title="FTP 다운로드 실패",
        )

    def _create_ftp_remote_folder(self) -> None:
        if not self._ftp_session_id:
            QMessageBox.warning(self, "연결 필요", "먼저 FTP 서버에 연결해 주세요.")
            return
        folder_name, ok = QInputDialog.getText(self, "새 폴더", "원격 폴더 이름")
        if not ok or not folder_name.strip():
            return
        self._set_ftp_client_busy(True)
        self._start_worker(
            self.state.ftp_client_service.make_directory,
            self._ftp_session_id,
            self.ftp_client_remote_path_edit.text().strip() or "/",
            folder_name,
            on_progress=self._handle_ftp_client_progress,
            on_result=self._finish_ftp_simple_action,
            on_finished=self._finish_ftp_job_with_refresh,
            error_title="원격 폴더 생성 실패",
        )

    def _rename_ftp_remote_entry(self) -> None:
        if not self._ftp_session_id:
            QMessageBox.warning(self, "연결 필요", "먼저 FTP 서버에 연결해 주세요.")
            return
        entries = self._selected_ftp_remote_entries()
        if len(entries) != 1:
            QMessageBox.warning(self, "선택 필요", "이름을 변경할 항목을 한 개만 선택해 주세요.")
            return
        new_name, ok = QInputDialog.getText(self, "이름 변경", "새 이름", text=entries[0].name)
        if not ok or not new_name.strip():
            return
        self._set_ftp_client_busy(True)
        self._start_worker(
            self.state.ftp_client_service.rename_path,
            self._ftp_session_id,
            entries[0].remote_path,
            new_name,
            on_progress=self._handle_ftp_client_progress,
            on_result=self._finish_ftp_simple_action,
            on_finished=self._finish_ftp_job_with_refresh,
            error_title="원격 이름 변경 실패",
        )

    def _delete_ftp_remote_entries(self) -> None:
        if not self._ftp_session_id:
            QMessageBox.warning(self, "연결 필요", "먼저 FTP 서버에 연결해 주세요.")
            return
        entries = self._selected_ftp_remote_entries()
        if not entries:
            QMessageBox.warning(self, "선택 필요", "삭제할 항목을 선택해 주세요.")
            return
        if QMessageBox.question(
            self,
            "원격 항목 삭제",
            f"{len(entries)}개 항목을 삭제할까요?",
        ) != QMessageBox.Yes:
            return
        self._set_ftp_client_busy(True)
        self._start_worker(
            self.state.ftp_client_service.delete_entries,
            self._ftp_session_id,
            entries,
            on_progress=self._handle_ftp_client_progress,
            on_result=self._finish_ftp_simple_action,
            on_finished=self._finish_ftp_job_with_refresh,
            error_title="원격 삭제 실패",
        )

    def _finish_ftp_simple_action(self, result: OperationResult) -> None:
        self.ftp_client_status_label.setText(result.message)
        self._update_ftp_client_activity_visibility()

    def _finish_ftp_transfer_job(self, result: OperationResult) -> None:
        self.ftp_client_status_label.setText(result.message)
        self._update_ftp_client_activity_visibility()

    def _finish_ftp_job_with_refresh(self) -> None:
        self._set_ftp_client_busy(False)
        if self._ftp_client_connected and self._ftp_session_id:
            self._refresh_ftp_remote_list()

    def _cancel_ftp_client_job(self) -> None:
        if self.ftp_client_cancel_event is not None:
            self.ftp_client_cancel_event.set()

    def _handle_ftp_remote_double_click(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if 0 <= row < len(self._ftp_remote_entries):
            entry = self._ftp_remote_entries[row]
            if entry.is_dir:
                self.ftp_client_remote_path_edit.setText(entry.remote_path)
                self._refresh_ftp_remote_list()

    def _handle_ftp_client_progress(self, event: dict) -> None:
        kind = str(event.get("kind", "") or "")
        if kind == "log":
            message = str(event.get("message", "") or "")
            if message:
                self._ftp_client_logs.append(message)
                self.ftp_client_log_output.appendPlainText(message)
                self._update_ftp_client_activity_visibility()
            return
        if kind == "transfer":
            result = event.get("result")
            if isinstance(result, FtpTransferResult):
                self._upsert_ftp_transfer_result(result)

    def _upsert_ftp_transfer_result(self, result: FtpTransferResult) -> None:
        key = (result.timestamp, result.action, result.source_path, result.target_path)
        row = self._ftp_transfer_row_map.get(key)
        if row is None:
            row = self.ftp_transfer_table.rowCount()
            self.ftp_transfer_table.insertRow(row)
            self._ftp_transfer_row_map[key] = row

        values = [
            result.timestamp,
            result.action,
            result.source_path,
            result.target_path,
            result.size_text,
            result.progress_text,
            result.duration_text,
            result.status,
            result.error,
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 7:
                if result.status == "완료":
                    item.setForeground(QColor("#1b5e20"))
                elif result.status == "중지":
                    item.setForeground(QColor("#ef6c00"))
                elif result.status == "오류":
                    item.setForeground(QColor("#b71c1c"))
            self.ftp_transfer_table.setItem(row, column, item)

    def _export_ftp_transfer_results(self) -> None:
        if self.ftp_transfer_table.rowCount() == 0:
            QMessageBox.warning(self, "내보내기 불가", "저장할 전송 결과가 없습니다.")
            return
        path = timestamped_export_path(self.state.paths.exports_dir, "ftp_transfers", "csv")
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [self.ftp_transfer_table.horizontalHeaderItem(column).text() for column in range(self.ftp_transfer_table.columnCount())]
            )
            for row in range(self.ftp_transfer_table.rowCount()):
                writer.writerow(
                    [self._cell(self.ftp_transfer_table, row, column) for column in range(self.ftp_transfer_table.columnCount())]
                )
        QMessageBox.information(self, "CSV 저장 완료", f"전송 결과를 저장했습니다.\n{path}")

    def _export_ftp_client_logs(self) -> None:
        if not self._ftp_client_logs:
            QMessageBox.warning(self, "내보내기 불가", "저장할 클라이언트 로그가 없습니다.")
            return
        path = timestamped_export_path(self.state.paths.exports_dir, "ftp_client_log", "txt")
        path.write_text("\n".join(self._ftp_client_logs) + "\n", encoding="utf-8")
        QMessageBox.information(self, "TXT 저장 완료", f"클라이언트 로그를 저장했습니다.\n{path}")

    def _start_ftp_server(self) -> None:
        if self._ftp_server_running:
            return
        support = self.state.ftp_server_service.runtime_support_status(
            str(self.ftp_server_protocol_combo.currentData() or "ftp")
        )
        self._apply_ftp_support_label(self.ftp_server_support_label, support)
        if not support.success:
            self._show_ftp_support_warning("FTP 서버 준비 필요", support)
            return
        self.ftp_server_log_output.clear()
        self._ftp_server_logs = []
        self._ftp_server_runtime = None
        self.ftp_server_cancel_event = self._new_ftp_cancel_event()
        self._set_ftp_server_running(True)
        self._start_worker(
            self.state.ftp_server_service.run_temporary_server,
            str(self.ftp_server_protocol_combo.currentData() or "ftp"),
            self.ftp_server_bind_host_edit.text().strip(),
            self.ftp_server_port_edit.text().strip(),
            self.ftp_server_root_edit.text().strip(),
            self.ftp_server_username_edit.text().strip(),
            self.ftp_server_password_edit.text(),
            self.ftp_server_readonly_check.isChecked(),
            self.ftp_server_anonymous_check.isChecked(),
            cancel_event=self.ftp_server_cancel_event,
            on_progress=self._handle_ftp_server_progress,
            on_result=self._finish_ftp_server_job,
            on_finished=lambda: self._set_ftp_server_running(False),
            error_title="FTP 서버 실행 실패",
        )

    def _handle_ftp_server_progress(self, event: dict) -> None:
        kind = str(event.get("kind", "") or "")
        if kind == "server_log":
            message = str(event.get("message", "") or "")
            if message:
                self._ftp_server_logs.append(message)
                self.ftp_server_log_output.appendPlainText(message)
                self._update_ftp_server_log_visibility()
            return
        if kind == "server_runtime":
            runtime = event.get("runtime")
            if isinstance(runtime, FtpServerRuntime):
                self._ftp_server_runtime = runtime
                self._update_ftp_server_runtime_labels(runtime)

    def _finish_ftp_server_job(self, result: OperationResult) -> None:
        self.ftp_server_state_label.setText(result.message)
        self.ftp_server_cancel_event = None
        self._update_ftp_server_log_visibility()

    def _stop_ftp_server(self) -> None:
        if self.ftp_server_cancel_event is not None:
            self.ftp_server_cancel_event.set()

    def _choose_ftp_server_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "공유 루트 폴더 선택",
            self.ftp_server_root_edit.text().strip() or str(self.state.paths.root),
        )
        if folder:
            self.ftp_server_root_edit.setText(folder)

    def _open_ftp_server_root(self) -> None:
        root_path = self.ftp_server_root_edit.text().strip()
        if not root_path:
            QMessageBox.warning(self, "경로 필요", "먼저 공유 루트 폴더를 지정해 주세요.")
            return
        open_in_explorer(Path(root_path))

    def _update_ftp_server_runtime_labels(self, runtime: FtpServerRuntime) -> None:
        self.ftp_server_state_label.setText(f"{runtime.protocol.upper()} 실행 중")
        self.ftp_server_endpoint_label.setText(f"{runtime.bind_host}:{runtime.port}")
        self.ftp_server_sessions_label.setText(str(runtime.session_count))
        self.ftp_server_fingerprint_label.setText(
            runtime.certificate_fingerprint or runtime.host_key_fingerprint or "-"
        )

    def _export_ftp_server_logs(self) -> None:
        if not self._ftp_server_logs:
            QMessageBox.warning(self, "내보내기 불가", "저장할 서버 로그가 없습니다.")
            return
        path = timestamped_export_path(self.state.paths.exports_dir, "ftp_server_log", "txt")
        path.write_text("\n".join(self._ftp_server_logs) + "\n", encoding="utf-8")
        QMessageBox.information(self, "TXT 저장 완료", f"서버 로그를 저장했습니다.\n{path}")

    def _set_ftp_client_connected(self, connected: bool) -> None:
        self._ftp_client_connected = connected
        self.ftp_client_connect_button.setEnabled(not connected and not self._ftp_client_busy)
        self.ftp_client_disconnect_button.setEnabled(connected and not self._ftp_client_busy)
        self.ftp_client_refresh_button.setEnabled(connected and not self._ftp_client_busy)
        self.ftp_client_upload_button.setEnabled(connected and not self._ftp_client_busy)
        self.ftp_client_download_button.setEnabled(connected and not self._ftp_client_busy)
        self.ftp_client_mkdir_button.setEnabled(connected and not self._ftp_client_busy)
        self.ftp_client_rename_button.setEnabled(connected and not self._ftp_client_busy)
        self.ftp_client_delete_button.setEnabled(connected and not self._ftp_client_busy)
        self.ftp_client_cancel_button.setEnabled(self._ftp_client_busy)
        self.ftp_remote_table.setEnabled(connected)
        self.ftp_remote_status_label.setVisible(not connected)
        self.ftp_client_fingerprint_label.setVisible(connected and self.ftp_client_fingerprint_label.text().strip() not in {"", "-"})
        self._update_ftp_client_activity_visibility()

    def _set_ftp_client_busy(self, busy: bool) -> None:
        self._ftp_client_busy = busy
        self._set_ftp_client_connected(self._ftp_client_connected)
        self.ftp_profile_combo.setEnabled(not busy)
        self.ftp_profile_add_button.setEnabled(not busy)
        self.ftp_profile_edit_button.setEnabled(not busy)
        self.ftp_profile_delete_button.setEnabled(not busy)
        self._update_ftp_client_activity_visibility()

    def _set_ftp_server_running(self, running: bool) -> None:
        self._ftp_server_running = running
        self.ftp_server_start_button.setEnabled(not running)
        self.ftp_server_stop_button.setEnabled(running)
        self.ftp_server_protocol_combo.setEnabled(not running)
        self.ftp_server_bind_host_edit.setEnabled(not running)
        self.ftp_server_port_edit.setEnabled(not running)
        self.ftp_server_root_edit.setEnabled(not running)
        self.ftp_server_root_browse_button.setEnabled(not running)
        self.ftp_server_username_edit.setEnabled(not running)
        self.ftp_server_password_edit.setEnabled(not running)
        self.ftp_server_readonly_check.setEnabled(not running)
        self.ftp_server_anonymous_check.setEnabled(not running and self.ftp_server_protocol_combo.currentData() != "sftp")
        self._update_ftp_server_log_visibility()

    def _update_ftp_client_activity_visibility(self) -> None:
        has_activity = self._ftp_client_busy or bool(self._ftp_client_logs) or self.ftp_transfer_table.rowCount() > 0
        self.ftp_transfer_export_button.setEnabled(self.ftp_transfer_table.rowCount() > 0)
        self.ftp_client_log_export_button.setEnabled(bool(self._ftp_client_logs))
        self.ftp_client_log_output.setPlaceholderText(
            "연결 후 작업을 시작하면 로그가 여기에 표시됩니다."
            if self._ftp_client_connected
            else "먼저 FTP/FTPS/SFTP 서버에 연결해 주세요."
        )

    def _update_ftp_server_log_visibility(self) -> None:
        self.ftp_server_log_export_button.setEnabled(bool(self._ftp_server_logs))

    def _new_ftp_cancel_event(self):
        from threading import Event

        return Event()

    def _collect_ftp_runtime_state(self) -> dict:
        return {
            "client": {
                "protocol": str(self.ftp_client_protocol_combo.currentData() or "ftp"),
                "host": self.ftp_client_host_edit.text().strip(),
                "port": self.ftp_client_port_edit.text().strip(),
                "username": self.ftp_client_username_edit.text().strip(),
                "passive_mode": self.ftp_client_passive_check.isChecked(),
                "timeout_seconds": self.ftp_client_timeout_edit.text().strip(),
                "local_folder": self.ftp_client_local_folder_edit.text().strip(),
                "remote_path": self.ftp_client_remote_path_edit.text().strip() or "/",
                "selected_profile": str(self.ftp_profile_combo.currentData() or ""),
            },
            "server": {
                "protocol": str(self.ftp_server_protocol_combo.currentData() or "ftp"),
                "bind_host": self.ftp_server_bind_host_edit.text().strip(),
                "port": self.ftp_server_port_edit.text().strip(),
                "root_folder": self.ftp_server_root_edit.text().strip(),
                "username": self.ftp_server_username_edit.text().strip(),
                "read_only": self.ftp_server_readonly_check.isChecked(),
                "anonymous_readonly": self.ftp_server_anonymous_check.isChecked(),
            },
        }

    def _restore_ftp_runtime_state(self) -> None:
        runtime = self.state.ftp_runtime if isinstance(self.state.ftp_runtime, dict) else {}
        client_state = runtime.get("client", {}) if isinstance(runtime.get("client", {}), dict) else {}
        server_state = runtime.get("server", {}) if isinstance(runtime.get("server", {}), dict) else {}

        client_protocol = str(client_state.get("protocol", "ftp") or "ftp")
        index = self.ftp_client_protocol_combo.findData(client_protocol)
        self.ftp_client_protocol_combo.setCurrentIndex(index if index >= 0 else 0)
        self.ftp_client_host_edit.setText(str(client_state.get("host", "") or ""))
        self.ftp_client_port_edit.setText(str(client_state.get("port", "") or ""))
        self.ftp_client_username_edit.setText(str(client_state.get("username", "") or ""))
        self.ftp_client_passive_check.setChecked(bool(client_state.get("passive_mode", True)))
        self.ftp_client_timeout_edit.setText(str(client_state.get("timeout_seconds", "") or ""))
        self.ftp_client_local_folder_edit.setText(str(client_state.get("local_folder", "") or ""))
        self.ftp_client_remote_path_edit.setText(str(client_state.get("remote_path", "/") or "/"))
        selected_profile = str(client_state.get("selected_profile", "") or "")
        if selected_profile:
            combo_index = self.ftp_profile_combo.findData(selected_profile)
            if combo_index >= 0:
                self.ftp_profile_combo.blockSignals(True)
                self.ftp_profile_combo.setCurrentIndex(combo_index)
                self.ftp_profile_combo.blockSignals(False)

        server_protocol = str(server_state.get("protocol", "ftp") or "ftp")
        server_index = self.ftp_server_protocol_combo.findData(server_protocol)
        self.ftp_server_protocol_combo.setCurrentIndex(server_index if server_index >= 0 else 0)
        self.ftp_server_bind_host_edit.setText(str(server_state.get("bind_host", "") or ""))
        self.ftp_server_port_edit.setText(str(server_state.get("port", "") or ""))
        self.ftp_server_root_edit.setText(str(server_state.get("root_folder", "") or ""))
        self.ftp_server_username_edit.setText(str(server_state.get("username", "") or ""))
        self.ftp_server_readonly_check.setChecked(bool(server_state.get("read_only", False)))
        self.ftp_server_anonymous_check.setChecked(bool(server_state.get("anonymous_readonly", False)))

        self._sync_ftp_client_protocol_state()
        self._sync_ftp_server_protocol_state()
