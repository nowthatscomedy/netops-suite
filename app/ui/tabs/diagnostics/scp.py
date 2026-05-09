from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.result_models import OperationResult
from app.models.scp_models import ScpProfile, ScpServerRuntime, ScpTransferResult
from app.ui.dialogs.scp_profile_dialog import ScpProfileDialog
from app.utils.file_utils import open_in_explorer, timestamped_export_path


class ScpDiagnosticsMixin:
    def _build_scp_client_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        connection_group = QGroupBox("SCP 클라이언트")
        connection_layout = QVBoxLayout(connection_group)

        profile_row = QHBoxLayout()
        self.scp_profile_combo = QComboBox()
        self.scp_profile_add_button = QPushButton("추가")
        self.scp_profile_edit_button = QPushButton("수정")
        self.scp_profile_delete_button = QPushButton("삭제")
        profile_row.addWidget(QLabel("프로필"))
        profile_row.addWidget(self.scp_profile_combo, 1)
        profile_row.addWidget(self.scp_profile_add_button)
        profile_row.addWidget(self.scp_profile_edit_button)
        profile_row.addWidget(self.scp_profile_delete_button)
        connection_layout.addLayout(profile_row)

        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)

        self.scp_client_host_edit = QLineEdit()
        self.scp_client_host_edit.setPlaceholderText("예: 192.168.0.10 또는 scp.example.com")
        self.scp_client_port_edit = QLineEdit()
        self.scp_client_port_edit.setPlaceholderText("예: 22")
        self.scp_client_username_edit = QLineEdit()
        self.scp_client_username_edit.setPlaceholderText("예: operator")
        self.scp_client_password_edit = QLineEdit()
        self.scp_client_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.scp_client_password_edit.setPlaceholderText("세션 중에만 사용합니다.")
        self.scp_client_timeout_edit = QLineEdit()
        self.scp_client_timeout_edit.setPlaceholderText("예: 15")
        self.scp_client_remote_path_edit = QLineEdit()
        self.scp_client_remote_path_edit.setPlaceholderText("업로드 대상 경로. 예: . 또는 /upload")
        self.scp_client_local_folder_edit = QLineEdit()
        self.scp_client_local_folder_edit.setPlaceholderText("다운로드 저장 폴더. 예: C:\\Temp")
        self.scp_client_local_browse_button = QPushButton("로컬 폴더")

        form.addWidget(QLabel("호스트"), 0, 0)
        form.addWidget(self.scp_client_host_edit, 0, 1)
        form.addWidget(QLabel("포트"), 0, 2)
        form.addWidget(self.scp_client_port_edit, 0, 3)
        form.addWidget(QLabel("사용자명"), 1, 0)
        form.addWidget(self.scp_client_username_edit, 1, 1)
        form.addWidget(QLabel("비밀번호"), 1, 2)
        form.addWidget(self.scp_client_password_edit, 1, 3)
        form.addWidget(QLabel("타임아웃(초)"), 2, 0)
        form.addWidget(self.scp_client_timeout_edit, 2, 1)
        form.addWidget(QLabel("업로드 대상"), 2, 2)
        form.addWidget(self.scp_client_remote_path_edit, 2, 3)
        form.addWidget(QLabel("로컬 폴더"), 3, 0)
        local_row = QWidget()
        local_row_layout = QHBoxLayout(local_row)
        local_row_layout.setContentsMargins(0, 0, 0, 0)
        local_row_layout.addWidget(self.scp_client_local_folder_edit, 1)
        local_row_layout.addWidget(self.scp_client_local_browse_button)
        form.addWidget(local_row, 3, 1, 1, 3)
        connection_layout.addLayout(form)

        self.scp_client_remote_sources_edit = QPlainTextEdit()
        self.scp_client_remote_sources_edit.setPlaceholderText(
            "다운로드할 원격 경로를 한 줄에 하나씩 입력하세요.\n예:\n./config.cfg\n/var/log/messages"
        )
        self.scp_client_remote_sources_edit.setMaximumHeight(100)
        connection_layout.addWidget(QLabel("다운로드 원격 경로"))
        connection_layout.addWidget(self.scp_client_remote_sources_edit)

        button_row = QHBoxLayout()
        self.scp_client_upload_button = QPushButton("업로드 실행")
        self.scp_client_download_button = QPushButton("다운로드 실행")
        self.scp_client_cancel_button = QPushButton("중지")
        button_row.addWidget(self.scp_client_upload_button)
        button_row.addWidget(self.scp_client_download_button)
        button_row.addWidget(self.scp_client_cancel_button)
        button_row.addStretch(1)
        connection_layout.addLayout(button_row)

        self.scp_client_status_label = QLabel("대기 중")
        self.scp_client_fingerprint_label = QLabel("-")
        self.scp_client_support_label = QLabel("")
        self.scp_client_fingerprint_label.setWordWrap(True)
        self.scp_client_support_label.setWordWrap(True)
        self.scp_client_fingerprint_label.hide()
        self.scp_client_support_label.hide()
        connection_layout.addWidget(self.scp_client_status_label)
        connection_layout.addWidget(self.scp_client_fingerprint_label)
        connection_layout.addWidget(self.scp_client_support_label)
        self._set_compact_transfer_group(connection_group)
        layout.addWidget(connection_group)

        self.scp_client_activity_splitter = QGroupBox("전송 결과 / 실시간 로그")
        result_layout = QVBoxLayout(self.scp_client_activity_splitter)
        self.scp_transfer_table = QTableWidget(0, 8)
        self.scp_transfer_table.setHorizontalHeaderLabels(
            ["시각", "작업", "원본", "대상", "크기", "전송량", "소요시간", "상태"]
        )
        self._setup_table(self.scp_transfer_table)
        self._set_stretch_columns(self.scp_transfer_table, 2, 3)
        self.scp_transfer_table.setMinimumHeight(130)
        self.scp_transfer_table.setMaximumHeight(180)
        result_layout.addWidget(self.scp_transfer_table)

        result_button_row = QHBoxLayout()
        self.scp_transfer_export_button = QPushButton("전송 결과 CSV 저장")
        self.scp_client_log_export_button = QPushButton("클라이언트 로그 TXT 저장")
        result_button_row.addWidget(self.scp_transfer_export_button)
        result_button_row.addWidget(self.scp_client_log_export_button)
        result_button_row.addStretch(1)
        result_layout.addLayout(result_button_row)
        result_layout.addWidget(QLabel("실시간 로그"))
        self.scp_client_log_output = self._output()
        self.scp_client_log_output.setPlaceholderText("업로드 또는 다운로드를 실행하면 로그가 여기에 표시됩니다.")
        self.scp_client_log_output.setMinimumHeight(110)
        self.scp_client_log_output.setMaximumHeight(150)
        result_layout.addWidget(self.scp_client_log_output)
        self._set_compact_transfer_group(self.scp_client_activity_splitter)
        layout.addWidget(self.scp_client_activity_splitter)
        layout.addStretch(1)

        self.scp_profile_combo.currentIndexChanged.connect(self._apply_selected_scp_profile)
        self.scp_profile_add_button.clicked.connect(self._add_scp_profile)
        self.scp_profile_edit_button.clicked.connect(self._edit_selected_scp_profile)
        self.scp_profile_delete_button.clicked.connect(self._delete_selected_scp_profile)
        self.scp_client_local_browse_button.clicked.connect(self._choose_scp_local_folder)
        self.scp_client_upload_button.clicked.connect(self._upload_scp_files)
        self.scp_client_download_button.clicked.connect(self._download_scp_files)
        self.scp_client_cancel_button.clicked.connect(self._cancel_scp_client_job)
        self.scp_transfer_export_button.clicked.connect(self._export_scp_transfer_results)
        self.scp_client_log_export_button.clicked.connect(self._export_scp_client_logs)
        return page

    def _build_scp_server_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        server_group = QGroupBox("임시 SCP 서버")
        server_layout = QVBoxLayout(server_group)

        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)

        self.scp_server_bind_host_edit = QLineEdit()
        self.scp_server_bind_host_edit.setPlaceholderText("예: 0.0.0.0")
        self.scp_server_port_edit = QLineEdit()
        self.scp_server_port_edit.setPlaceholderText("예: 2223")
        self.scp_server_root_edit = QLineEdit()
        self.scp_server_root_edit.setPlaceholderText("예: C:\\Transfer")
        self.scp_server_root_browse_button = QPushButton("공유 폴더")
        self.scp_server_username_edit = QLineEdit()
        self.scp_server_username_edit.setPlaceholderText("예: netops")
        self.scp_server_password_edit = QLineEdit()
        self.scp_server_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.scp_server_password_edit.setPlaceholderText("접속 비밀번호")
        self.scp_server_readonly_combo = QComboBox()
        self.scp_server_readonly_combo.addItem("읽기/쓰기", False)
        self.scp_server_readonly_combo.addItem("읽기 전용", True)

        form.addWidget(QLabel("바인드 IP"), 0, 0)
        form.addWidget(self.scp_server_bind_host_edit, 0, 1)
        form.addWidget(QLabel("포트"), 0, 2)
        form.addWidget(self.scp_server_port_edit, 0, 3)
        form.addWidget(QLabel("공유 루트"), 1, 0)
        root_row = QWidget()
        root_row_layout = QHBoxLayout(root_row)
        root_row_layout.setContentsMargins(0, 0, 0, 0)
        root_row_layout.addWidget(self.scp_server_root_edit, 1)
        root_row_layout.addWidget(self.scp_server_root_browse_button)
        form.addWidget(root_row, 1, 1, 1, 3)
        form.addWidget(QLabel("계정"), 2, 0)
        form.addWidget(self.scp_server_username_edit, 2, 1)
        form.addWidget(QLabel("비밀번호"), 2, 2)
        form.addWidget(self.scp_server_password_edit, 2, 3)
        form.addWidget(QLabel("권한"), 3, 0)
        form.addWidget(self.scp_server_readonly_combo, 3, 1)
        server_layout.addLayout(form)

        button_row = QHBoxLayout()
        self.scp_server_start_button = QPushButton("시작")
        self.scp_server_stop_button = QPushButton("중지")
        self.scp_server_open_root_button = QPushButton("루트 폴더 열기")
        button_row.addWidget(self.scp_server_start_button)
        button_row.addWidget(self.scp_server_stop_button)
        button_row.addWidget(self.scp_server_open_root_button)
        button_row.addStretch(1)
        server_layout.addLayout(button_row)

        self.scp_server_support_label = QLabel("")
        self.scp_server_support_label.setWordWrap(True)
        self.scp_server_support_label.hide()
        server_layout.addWidget(self.scp_server_support_label)

        status_form = QFormLayout()
        self.scp_server_state_label = QLabel("중지됨")
        self.scp_server_endpoint_label = QLabel("-")
        self.scp_server_sessions_label = QLabel("0")
        self.scp_server_fingerprint_label = QLabel("-")
        self.scp_server_fingerprint_label.setWordWrap(True)
        status_form.addRow("상태", self.scp_server_state_label)
        status_form.addRow("접속 주소", self.scp_server_endpoint_label)
        status_form.addRow("세션 수", self.scp_server_sessions_label)
        status_form.addRow("호스트 키 지문", self.scp_server_fingerprint_label)
        server_layout.addLayout(status_form)
        self._set_compact_transfer_group(server_group)
        layout.addWidget(server_group)

        self.scp_server_log_group = QGroupBox("서버 로그")
        log_layout = QVBoxLayout(self.scp_server_log_group)
        self.scp_server_log_output = self._output()
        self.scp_server_log_output.setPlaceholderText("서버를 시작하면 접속 및 전송 로그가 여기에 표시됩니다.")
        self.scp_server_log_output.setMinimumHeight(120)
        self.scp_server_log_output.setMaximumHeight(170)
        log_layout.addWidget(self.scp_server_log_output)
        log_button_row = QHBoxLayout()
        self.scp_server_log_export_button = QPushButton("서버 로그 TXT 저장")
        log_button_row.addWidget(self.scp_server_log_export_button)
        log_button_row.addStretch(1)
        log_layout.addLayout(log_button_row)
        self._set_compact_transfer_group(self.scp_server_log_group)
        layout.addWidget(self.scp_server_log_group)
        layout.addStretch(1)

        self.scp_server_root_browse_button.clicked.connect(self._choose_scp_server_root)
        self.scp_server_start_button.clicked.connect(self._start_scp_server)
        self.scp_server_stop_button.clicked.connect(self._stop_scp_server)
        self.scp_server_open_root_button.clicked.connect(self._open_scp_server_root)
        self.scp_server_log_export_button.clicked.connect(self._export_scp_server_logs)
        self.scp_server_readonly_combo.currentIndexChanged.connect(self._refresh_scp_server_support_notice)
        return page

    def _reload_scp_profiles(self) -> None:
        current_name = self.scp_profile_combo.currentText().strip() if hasattr(self, "scp_profile_combo") else ""
        self.scp_profile_combo.blockSignals(True)
        self.scp_profile_combo.clear()
        self.scp_profile_combo.addItem("프로필 선택", "")
        for profile in self.state.scp_profiles:
            self.scp_profile_combo.addItem(profile.name, profile.name)
        index = self.scp_profile_combo.findData(current_name)
        self.scp_profile_combo.setCurrentIndex(index if index >= 0 else 0)
        self.scp_profile_combo.blockSignals(False)

    def _get_scp_profile_by_name(self, name: str) -> ScpProfile | None:
        for profile in self.state.scp_profiles:
            if profile.name == name:
                return profile
        return None

    def _apply_selected_scp_profile(self) -> None:
        profile_name = str(self.scp_profile_combo.currentData() or "").strip()
        profile = self._get_scp_profile_by_name(profile_name)
        if profile is None:
            return
        self.scp_client_host_edit.setText(profile.host)
        self.scp_client_port_edit.setText(str(profile.port))
        self.scp_client_username_edit.setText(profile.username)
        self.scp_client_remote_path_edit.setText(profile.remote_path)
        self.scp_client_timeout_edit.setText(str(profile.timeout_seconds))

    def _add_scp_profile(self) -> None:
        dialog = ScpProfileDialog(self)
        if dialog.exec():
            profile = dialog.profile_data()
            profiles = list(self.state.scp_profiles)
            profiles.append(profile)
            profiles.sort(key=lambda item: item.name.lower())
            self.state.save_scp_profiles(profiles)
            self._reload_scp_profiles()
            index = self.scp_profile_combo.findData(profile.name)
            if index >= 0:
                self.scp_profile_combo.setCurrentIndex(index)

    def _edit_selected_scp_profile(self) -> None:
        profile_name = str(self.scp_profile_combo.currentData() or "").strip()
        profile = self._get_scp_profile_by_name(profile_name)
        if profile is None:
            QMessageBox.warning(self, "선택 필요", "수정할 SCP 프로필을 먼저 선택해 주세요.")
            return
        dialog = ScpProfileDialog(self, profile)
        if dialog.exec():
            updated = dialog.profile_data()
            profiles = [updated if item.name == profile.name else item for item in self.state.scp_profiles]
            profiles.sort(key=lambda item: item.name.lower())
            self.state.save_scp_profiles(profiles)
            self._reload_scp_profiles()
            index = self.scp_profile_combo.findData(updated.name)
            if index >= 0:
                self.scp_profile_combo.setCurrentIndex(index)

    def _delete_selected_scp_profile(self) -> None:
        profile_name = str(self.scp_profile_combo.currentData() or "").strip()
        profile = self._get_scp_profile_by_name(profile_name)
        if profile is None:
            QMessageBox.warning(self, "선택 필요", "삭제할 SCP 프로필을 먼저 선택해 주세요.")
            return
        if QMessageBox.question(self, "프로필 삭제", f"'{profile.name}' 프로필을 삭제할까요?") != QMessageBox.Yes:
            return
        profiles = [item for item in self.state.scp_profiles if item.name != profile.name]
        self.state.save_scp_profiles(profiles)
        self._reload_scp_profiles()

    def _refresh_scp_client_support_notice(self) -> None:
        support = self.state.scp_client_service.runtime_support_status()
        self._apply_scp_support_label(self.scp_client_support_label, support)

    def _refresh_scp_server_support_notice(self) -> None:
        support = self.state.scp_server_service.runtime_support_status()
        self._apply_scp_support_label(self.scp_server_support_label, support)

    def _apply_scp_support_label(self, label: QLabel, support: OperationResult) -> None:
        if support.success:
            label.clear()
            label.hide()
            return
        label.setText(support.message)
        label.setStyleSheet("color:#b71c1c;")
        label.show()

    def _show_scp_support_warning(self, title: str, support: OperationResult) -> None:
        text = support.message
        if support.details:
            text += f"\n\n{support.details}"
        QMessageBox.warning(self, title, text)

    def _choose_scp_local_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "로컬 폴더 선택",
            self.scp_client_local_folder_edit.text().strip() or str(self.state.paths.root),
        )
        if folder:
            self.scp_client_local_folder_edit.setText(folder)

    def _upload_scp_files(self) -> None:
        support = self.state.scp_client_service.runtime_support_status()
        self._apply_scp_support_label(self.scp_client_support_label, support)
        if not support.success:
            self._show_scp_support_warning("SCP 클라이언트 준비 필요", support)
            return

        files, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "업로드할 파일 선택",
            self.scp_client_local_folder_edit.text().strip() or str(self.state.paths.root),
        )
        if not files:
            return
        self.scp_client_log_output.clear()
        self._scp_client_logs = []
        self._scp_transfer_row_map.clear()
        self.scp_transfer_table.setRowCount(0)
        self.scp_client_cancel_event = self._new_scp_cancel_event()
        self._set_scp_client_busy(True)
        self._start_worker(
            self.state.scp_client_service.upload_files,
            self.scp_client_host_edit.text().strip(),
            self.scp_client_port_edit.text().strip() or "22",
            self.scp_client_username_edit.text().strip(),
            self.scp_client_password_edit.text(),
            files,
            self.scp_client_remote_path_edit.text().strip() or ".",
            self.scp_client_timeout_edit.text().strip() or "15",
            cancel_event=self.scp_client_cancel_event,
            on_progress=self._handle_scp_client_progress,
            on_result=self._finish_scp_client_job,
            on_finished=lambda: self._set_scp_client_busy(False),
            error_title="SCP 업로드 실패",
        )

    def _download_scp_files(self) -> None:
        support = self.state.scp_client_service.runtime_support_status()
        self._apply_scp_support_label(self.scp_client_support_label, support)
        if not support.success:
            self._show_scp_support_warning("SCP 클라이언트 준비 필요", support)
            return

        local_folder = self.scp_client_local_folder_edit.text().strip()
        if not local_folder:
            self._choose_scp_local_folder()
            local_folder = self.scp_client_local_folder_edit.text().strip()
        if not local_folder:
            return

        remote_sources = [line.strip() for line in self.scp_client_remote_sources_edit.toPlainText().splitlines() if line.strip()]
        if not remote_sources:
            QMessageBox.warning(self, "입력 필요", "다운로드할 원격 경로를 한 줄에 하나씩 입력해 주세요.")
            return

        self.scp_client_log_output.clear()
        self._scp_client_logs = []
        self._scp_transfer_row_map.clear()
        self.scp_transfer_table.setRowCount(0)
        self.scp_client_cancel_event = self._new_scp_cancel_event()
        self._set_scp_client_busy(True)
        self._start_worker(
            self.state.scp_client_service.download_files,
            self.scp_client_host_edit.text().strip(),
            self.scp_client_port_edit.text().strip() or "22",
            self.scp_client_username_edit.text().strip(),
            self.scp_client_password_edit.text(),
            remote_sources,
            local_folder,
            self.scp_client_timeout_edit.text().strip() or "15",
            cancel_event=self.scp_client_cancel_event,
            on_progress=self._handle_scp_client_progress,
            on_result=self._finish_scp_client_job,
            on_finished=lambda: self._set_scp_client_busy(False),
            error_title="SCP 다운로드 실패",
        )

    def _handle_scp_client_progress(self, event: dict) -> None:
        kind = str(event.get("kind", "") or "")
        if kind == "log":
            message = str(event.get("message", "") or "")
            if message:
                self._scp_client_logs.append(message)
                self.scp_client_log_output.appendPlainText(message)
                self._update_scp_client_activity_visibility()
            return
        if kind == "transfer":
            result = event.get("result")
            if isinstance(result, ScpTransferResult):
                self._upsert_scp_transfer_result(result)

    def _finish_scp_client_job(self, result: OperationResult) -> None:
        payload = result.payload if isinstance(result.payload, dict) else {}
        fingerprint = str(payload.get("host_key_fingerprint", "") or "")
        self.scp_client_status_label.setText(result.message)
        self.scp_client_fingerprint_label.setText(fingerprint or "-")
        self.scp_client_password_edit.clear()
        self.scp_client_fingerprint_label.setVisible(fingerprint.strip() not in {"", "-"})
        self._update_scp_client_activity_visibility()

    def _upsert_scp_transfer_result(self, result: ScpTransferResult) -> None:
        key = (result.timestamp, result.action, result.source_path, result.target_path)
        row = self._scp_transfer_row_map.get(key)
        if row is None:
            row = self.scp_transfer_table.rowCount()
            self.scp_transfer_table.insertRow(row)
            self._scp_transfer_row_map[key] = row

        values = [
            result.timestamp,
            result.action,
            result.source_path,
            result.target_path,
            result.size_text,
            result.progress_text,
            result.duration_text,
            result.status,
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
            self.scp_transfer_table.setItem(row, column, item)

    def _export_scp_transfer_results(self) -> None:
        if self.scp_transfer_table.rowCount() == 0:
            QMessageBox.warning(self, "내보내기 불가", "저장할 SCP 전송 결과가 없습니다.")
            return
        path = timestamped_export_path(self.state.paths.exports_dir, "scp_transfers", "csv")
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [self.scp_transfer_table.horizontalHeaderItem(column).text() for column in range(self.scp_transfer_table.columnCount())]
            )
            for row in range(self.scp_transfer_table.rowCount()):
                writer.writerow(
                    [self._cell(self.scp_transfer_table, row, column) for column in range(self.scp_transfer_table.columnCount())]
                )
        QMessageBox.information(self, "CSV 저장 완료", f"SCP 전송 결과를 저장했습니다.\n{path}")

    def _export_scp_client_logs(self) -> None:
        if not self._scp_client_logs:
            QMessageBox.warning(self, "내보내기 불가", "저장할 SCP 클라이언트 로그가 없습니다.")
            return
        path = timestamped_export_path(self.state.paths.exports_dir, "scp_client_log", "txt")
        path.write_text("\n".join(self._scp_client_logs) + "\n", encoding="utf-8")
        QMessageBox.information(self, "TXT 저장 완료", f"SCP 클라이언트 로그를 저장했습니다.\n{path}")

    def _start_scp_server(self) -> None:
        if self._scp_server_running:
            return
        support = self.state.scp_server_service.runtime_support_status()
        self._apply_scp_support_label(self.scp_server_support_label, support)
        if not support.success:
            self._show_scp_support_warning("SCP 서버 준비 필요", support)
            return

        self.scp_server_log_output.clear()
        self._scp_server_logs = []
        self._scp_server_runtime = None
        self.scp_server_cancel_event = self._new_scp_cancel_event()
        self._set_scp_server_running(True)
        self._start_worker(
            self.state.scp_server_service.run_temporary_server,
            self.scp_server_bind_host_edit.text().strip(),
            self.scp_server_port_edit.text().strip() or "2223",
            self.scp_server_root_edit.text().strip(),
            self.scp_server_username_edit.text().strip(),
            self.scp_server_password_edit.text(),
            bool(self.scp_server_readonly_combo.currentData()),
            cancel_event=self.scp_server_cancel_event,
            on_progress=self._handle_scp_server_progress,
            on_result=self._finish_scp_server_job,
            on_finished=lambda: self._set_scp_server_running(False),
            error_title="SCP 서버 실행 실패",
        )

    def _handle_scp_server_progress(self, event: dict) -> None:
        kind = str(event.get("kind", "") or "")
        if kind == "server_log":
            message = str(event.get("message", "") or "")
            if message:
                self._scp_server_logs.append(message)
                self.scp_server_log_output.appendPlainText(message)
                self._update_scp_server_log_visibility()
            return
        if kind == "server_runtime":
            runtime = event.get("runtime")
            if isinstance(runtime, ScpServerRuntime):
                self._scp_server_runtime = runtime
                self._update_scp_server_runtime_labels(runtime)

    def _finish_scp_server_job(self, result: OperationResult) -> None:
        self.scp_server_state_label.setText(result.message)
        self.scp_server_cancel_event = None
        self._update_scp_server_log_visibility()

    def _stop_scp_server(self) -> None:
        if self.scp_server_cancel_event is not None:
            self.scp_server_cancel_event.set()

    def _choose_scp_server_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "공유 루트 폴더 선택",
            self.scp_server_root_edit.text().strip() or str(self.state.paths.root),
        )
        if folder:
            self.scp_server_root_edit.setText(folder)

    def _open_scp_server_root(self) -> None:
        root_path = self.scp_server_root_edit.text().strip()
        if not root_path:
            QMessageBox.warning(self, "경로 필요", "먼저 공유 루트 폴더를 지정해 주세요.")
            return
        open_in_explorer(Path(root_path))

    def _update_scp_server_runtime_labels(self, runtime: ScpServerRuntime) -> None:
        self.scp_server_state_label.setText("SCP 실행 중")
        self.scp_server_endpoint_label.setText(f"{runtime.bind_host}:{runtime.port}")
        self.scp_server_sessions_label.setText(str(runtime.session_count))
        self.scp_server_fingerprint_label.setText(runtime.host_key_fingerprint or "-")

    def _export_scp_server_logs(self) -> None:
        if not self._scp_server_logs:
            QMessageBox.warning(self, "내보내기 불가", "저장할 SCP 서버 로그가 없습니다.")
            return
        path = timestamped_export_path(self.state.paths.exports_dir, "scp_server_log", "txt")
        path.write_text("\n".join(self._scp_server_logs) + "\n", encoding="utf-8")
        QMessageBox.information(self, "TXT 저장 완료", f"SCP 서버 로그를 저장했습니다.\n{path}")

    def _set_scp_client_busy(self, busy: bool) -> None:
        self._scp_client_busy = busy
        self.scp_profile_combo.setEnabled(not busy)
        self.scp_profile_add_button.setEnabled(not busy)
        self.scp_profile_edit_button.setEnabled(not busy)
        self.scp_profile_delete_button.setEnabled(not busy)
        self.scp_client_host_edit.setEnabled(not busy)
        self.scp_client_port_edit.setEnabled(not busy)
        self.scp_client_username_edit.setEnabled(not busy)
        self.scp_client_password_edit.setEnabled(not busy)
        self.scp_client_timeout_edit.setEnabled(not busy)
        self.scp_client_remote_path_edit.setEnabled(not busy)
        self.scp_client_local_folder_edit.setEnabled(not busy)
        self.scp_client_local_browse_button.setEnabled(not busy)
        self.scp_client_remote_sources_edit.setEnabled(not busy)
        self.scp_client_upload_button.setEnabled(not busy)
        self.scp_client_download_button.setEnabled(not busy)
        self.scp_client_cancel_button.setEnabled(busy)
        self._update_scp_client_activity_visibility()

    def _set_scp_server_running(self, running: bool) -> None:
        self._scp_server_running = running
        self.scp_server_start_button.setEnabled(not running)
        self.scp_server_stop_button.setEnabled(running)
        self.scp_server_bind_host_edit.setEnabled(not running)
        self.scp_server_port_edit.setEnabled(not running)
        self.scp_server_root_edit.setEnabled(not running)
        self.scp_server_root_browse_button.setEnabled(not running)
        self.scp_server_username_edit.setEnabled(not running)
        self.scp_server_password_edit.setEnabled(not running)
        self.scp_server_readonly_combo.setEnabled(not running)
        self._update_scp_server_log_visibility()

    def _update_scp_client_activity_visibility(self) -> None:
        self.scp_transfer_export_button.setEnabled(self.scp_transfer_table.rowCount() > 0)
        self.scp_client_log_export_button.setEnabled(bool(self._scp_client_logs))

    def _update_scp_server_log_visibility(self) -> None:
        self.scp_server_log_export_button.setEnabled(bool(self._scp_server_logs))

    def _cancel_scp_client_job(self) -> None:
        if self.scp_client_cancel_event is not None:
            self.scp_client_cancel_event.set()

    def _new_scp_cancel_event(self):
        from threading import Event

        return Event()

    def _collect_scp_runtime_state(self) -> dict:
        return {
            "client": {
                "host": self.scp_client_host_edit.text().strip(),
                "port": self.scp_client_port_edit.text().strip(),
                "username": self.scp_client_username_edit.text().strip(),
                "timeout_seconds": self.scp_client_timeout_edit.text().strip(),
                "remote_path": self.scp_client_remote_path_edit.text().strip() or ".",
                "remote_sources": self.scp_client_remote_sources_edit.toPlainText().strip(),
                "local_folder": self.scp_client_local_folder_edit.text().strip(),
                "selected_profile": str(self.scp_profile_combo.currentData() or ""),
            },
            "server": {
                "bind_host": self.scp_server_bind_host_edit.text().strip(),
                "port": self.scp_server_port_edit.text().strip(),
                "root_folder": self.scp_server_root_edit.text().strip(),
                "username": self.scp_server_username_edit.text().strip(),
                "read_only": bool(self.scp_server_readonly_combo.currentData()),
            },
        }

    def _restore_scp_runtime_state(self) -> None:
        runtime = self.state.scp_runtime if isinstance(self.state.scp_runtime, dict) else {}
        client_state = runtime.get("client", {}) if isinstance(runtime.get("client", {}), dict) else {}
        server_state = runtime.get("server", {}) if isinstance(runtime.get("server", {}), dict) else {}

        self.scp_client_host_edit.setText(str(client_state.get("host", "") or ""))
        self.scp_client_port_edit.setText(str(client_state.get("port", "") or ""))
        self.scp_client_username_edit.setText(str(client_state.get("username", "") or ""))
        self.scp_client_timeout_edit.setText(str(client_state.get("timeout_seconds", "") or ""))
        self.scp_client_remote_path_edit.setText(str(client_state.get("remote_path", ".") or "."))
        self.scp_client_remote_sources_edit.setPlainText(str(client_state.get("remote_sources", "") or ""))
        self.scp_client_local_folder_edit.setText(str(client_state.get("local_folder", "") or ""))
        selected_profile = str(client_state.get("selected_profile", "") or "")
        if selected_profile:
            combo_index = self.scp_profile_combo.findData(selected_profile)
            if combo_index >= 0:
                self.scp_profile_combo.blockSignals(True)
                self.scp_profile_combo.setCurrentIndex(combo_index)
                self.scp_profile_combo.blockSignals(False)

        self.scp_server_bind_host_edit.setText(str(server_state.get("bind_host", "") or ""))
        self.scp_server_port_edit.setText(str(server_state.get("port", "") or ""))
        self.scp_server_root_edit.setText(str(server_state.get("root_folder", "") or ""))
        self.scp_server_username_edit.setText(str(server_state.get("username", "") or ""))
        readonly = bool(server_state.get("read_only", False))
        index = self.scp_server_readonly_combo.findData(readonly)
        self.scp_server_readonly_combo.setCurrentIndex(index if index >= 0 else 0)
