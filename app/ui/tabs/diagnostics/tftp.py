from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.result_models import OperationResult
from app.models.tftp_models import TftpServerRuntime, TftpTransferResult
from app.utils.file_utils import open_in_explorer, timestamped_export_path


class TftpDiagnosticsMixin:
    def _build_tftp_client_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        connection_group = QGroupBox("TFTP 클라이언트")
        connection_layout = QVBoxLayout(connection_group)

        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)

        self.tftp_client_host_edit = QLineEdit()
        self.tftp_client_host_edit.setPlaceholderText("예: 192.168.0.10 또는 tftp.example.com")
        self.tftp_client_port_edit = QLineEdit()
        self.tftp_client_port_edit.setPlaceholderText("69")
        self.tftp_client_timeout_edit = QLineEdit()
        self.tftp_client_timeout_edit.setPlaceholderText("5")
        self.tftp_client_retries_edit = QLineEdit()
        self.tftp_client_retries_edit.setPlaceholderText("3")
        self.tftp_client_remote_path_edit = QLineEdit()
        self.tftp_client_remote_path_edit.setPlaceholderText("예: config/startup.cfg")
        self.tftp_client_upload_path_edit = QLineEdit()
        self.tftp_client_upload_path_edit.setPlaceholderText("업로드할 로컬 파일 경로")
        self.tftp_client_upload_browse_button = QPushButton("파일 선택")
        self.tftp_client_local_folder_edit = QLineEdit()
        self.tftp_client_local_folder_edit.setPlaceholderText("다운로드 저장 폴더. 예: C:\\Temp")
        self.tftp_client_local_browse_button = QPushButton("로컬 폴더")

        form.addWidget(QLabel("호스트"), 0, 0)
        form.addWidget(self.tftp_client_host_edit, 0, 1)
        form.addWidget(QLabel("포트"), 0, 2)
        form.addWidget(self.tftp_client_port_edit, 0, 3)
        form.addWidget(QLabel("타임아웃(초)"), 1, 0)
        form.addWidget(self.tftp_client_timeout_edit, 1, 1)
        form.addWidget(QLabel("재시도"), 1, 2)
        form.addWidget(self.tftp_client_retries_edit, 1, 3)
        form.addWidget(QLabel("원격 경로"), 2, 0)
        form.addWidget(self.tftp_client_remote_path_edit, 2, 1, 1, 3)
        form.addWidget(QLabel("업로드 파일"), 3, 0)
        upload_row = QWidget()
        upload_layout = QHBoxLayout(upload_row)
        upload_layout.setContentsMargins(0, 0, 0, 0)
        upload_layout.addWidget(self.tftp_client_upload_path_edit, 1)
        upload_layout.addWidget(self.tftp_client_upload_browse_button)
        form.addWidget(upload_row, 3, 1, 1, 3)
        form.addWidget(QLabel("로컬 폴더"), 4, 0)
        local_row = QWidget()
        local_layout = QHBoxLayout(local_row)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.addWidget(self.tftp_client_local_folder_edit, 1)
        local_layout.addWidget(self.tftp_client_local_browse_button)
        form.addWidget(local_row, 4, 1, 1, 3)
        connection_layout.addLayout(form)

        button_row = QHBoxLayout()
        self.tftp_client_upload_button = QPushButton("업로드 실행")
        self.tftp_client_download_button = QPushButton("다운로드 실행")
        self.tftp_client_cancel_button = QPushButton("중지")
        button_row.addWidget(self.tftp_client_upload_button)
        button_row.addWidget(self.tftp_client_download_button)
        button_row.addWidget(self.tftp_client_cancel_button)
        button_row.addStretch(1)
        connection_layout.addLayout(button_row)

        self.tftp_client_status_label = QLabel("대기 중")
        self.tftp_client_support_label = QLabel("")
        self.tftp_client_support_label.setWordWrap(True)
        self.tftp_client_support_label.hide()
        connection_layout.addWidget(self.tftp_client_status_label)
        connection_layout.addWidget(self.tftp_client_support_label)
        self._set_compact_transfer_group(connection_group)
        layout.addWidget(connection_group)

        activity_group = QGroupBox("전송 결과 / 실시간 로그")
        activity_layout = QVBoxLayout(activity_group)
        self.tftp_transfer_table = QTableWidget(0, 9)
        self.tftp_transfer_table.setHorizontalHeaderLabels(
            ["시각", "작업", "원본", "대상", "크기", "전송량", "소요시간", "상태", "오류"]
        )
        self._setup_table(self.tftp_transfer_table)
        self._set_stretch_columns(self.tftp_transfer_table, 2, 3, 8)
        self.tftp_transfer_table.setMinimumHeight(140)
        self.tftp_transfer_table.setMaximumHeight(180)
        activity_layout.addWidget(self.tftp_transfer_table)

        result_button_row = QHBoxLayout()
        self.tftp_transfer_export_button = QPushButton("전송 결과 CSV 저장")
        self.tftp_client_log_export_button = QPushButton("클라이언트 로그 TXT 저장")
        result_button_row.addWidget(self.tftp_transfer_export_button)
        result_button_row.addWidget(self.tftp_client_log_export_button)
        result_button_row.addStretch(1)
        activity_layout.addLayout(result_button_row)

        activity_layout.addWidget(QLabel("실시간 로그"))
        self.tftp_client_log_output = self._output()
        self.tftp_client_log_output.setPlaceholderText("업로드 또는 다운로드를 실행하면 로그가 여기에 표시됩니다.")
        self.tftp_client_log_output.setMinimumHeight(110)
        self.tftp_client_log_output.setMaximumHeight(150)
        activity_layout.addWidget(self.tftp_client_log_output)
        self._set_compact_transfer_group(activity_group)
        layout.addWidget(activity_group)
        layout.addStretch(1)

        self.tftp_client_upload_browse_button.clicked.connect(self._choose_tftp_upload_file)
        self.tftp_client_local_browse_button.clicked.connect(self._choose_tftp_local_folder)
        self.tftp_client_upload_button.clicked.connect(self._start_tftp_upload)
        self.tftp_client_download_button.clicked.connect(self._start_tftp_download)
        self.tftp_client_cancel_button.clicked.connect(self._cancel_tftp_client_job)
        self.tftp_transfer_export_button.clicked.connect(self._export_tftp_transfer_results)
        self.tftp_client_log_export_button.clicked.connect(self._export_tftp_client_logs)
        return page

    def _build_tftp_server_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        server_group = QGroupBox("임시 TFTP 서버")
        server_layout = QVBoxLayout(server_group)

        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)

        self.tftp_server_bind_host_edit = QLineEdit()
        self.tftp_server_bind_host_edit.setPlaceholderText("예: 0.0.0.0")
        self.tftp_server_port_edit = QLineEdit()
        self.tftp_server_port_edit.setPlaceholderText("69")
        self.tftp_server_root_edit = QLineEdit()
        self.tftp_server_root_edit.setPlaceholderText("예: C:\\Transfer")
        self.tftp_server_root_browse_button = QPushButton("공유 폴더")
        self.tftp_server_readonly_check = QCheckBox("읽기 전용")

        form.addWidget(QLabel("바인드 IP"), 0, 0)
        form.addWidget(self.tftp_server_bind_host_edit, 0, 1)
        form.addWidget(QLabel("포트"), 0, 2)
        form.addWidget(self.tftp_server_port_edit, 0, 3)
        form.addWidget(QLabel("공유 루트"), 1, 0)
        root_row = QWidget()
        root_layout = QHBoxLayout(root_row)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(self.tftp_server_root_edit, 1)
        root_layout.addWidget(self.tftp_server_root_browse_button)
        form.addWidget(root_row, 1, 1, 1, 3)
        form.addWidget(QLabel("권한"), 2, 0)
        form.addWidget(self.tftp_server_readonly_check, 2, 1)
        server_layout.addLayout(form)

        button_row = QHBoxLayout()
        self.tftp_server_start_button = QPushButton("시작")
        self.tftp_server_stop_button = QPushButton("중지")
        self.tftp_server_open_root_button = QPushButton("루트 폴더 열기")
        button_row.addWidget(self.tftp_server_start_button)
        button_row.addWidget(self.tftp_server_stop_button)
        button_row.addWidget(self.tftp_server_open_root_button)
        button_row.addStretch(1)
        server_layout.addLayout(button_row)

        self.tftp_server_support_label = QLabel("")
        self.tftp_server_support_label.setWordWrap(True)
        self.tftp_server_support_label.hide()
        server_layout.addWidget(self.tftp_server_support_label)

        status_form = QFormLayout()
        self.tftp_server_state_label = QLabel("중지됨")
        self.tftp_server_endpoint_label = QLabel("-")
        self.tftp_server_access_label = QLabel("읽기 전용")
        self.tftp_server_sessions_label = QLabel("0")
        status_form.addRow("상태", self.tftp_server_state_label)
        status_form.addRow("접속 주소", self.tftp_server_endpoint_label)
        status_form.addRow("권한", self.tftp_server_access_label)
        status_form.addRow("세션 수", self.tftp_server_sessions_label)
        server_layout.addLayout(status_form)
        self._set_compact_transfer_group(server_group)
        layout.addWidget(server_group)

        self.tftp_server_log_group = QGroupBox("서버 로그")
        log_layout = QVBoxLayout(self.tftp_server_log_group)
        self.tftp_server_log_output = self._output()
        self.tftp_server_log_output.setPlaceholderText("서버를 시작하면 접속 및 전송 로그가 여기에 표시됩니다.")
        self.tftp_server_log_output.setMinimumHeight(120)
        self.tftp_server_log_output.setMaximumHeight(170)
        log_layout.addWidget(self.tftp_server_log_output)
        log_button_row = QHBoxLayout()
        self.tftp_server_log_export_button = QPushButton("서버 로그 TXT 저장")
        log_button_row.addWidget(self.tftp_server_log_export_button)
        log_button_row.addStretch(1)
        log_layout.addLayout(log_button_row)
        self._set_compact_transfer_group(self.tftp_server_log_group)
        layout.addWidget(self.tftp_server_log_group)
        layout.addStretch(1)

        self.tftp_server_root_browse_button.clicked.connect(self._choose_tftp_server_root)
        self.tftp_server_start_button.clicked.connect(self._start_tftp_server)
        self.tftp_server_stop_button.clicked.connect(self._stop_tftp_server)
        self.tftp_server_open_root_button.clicked.connect(self._open_tftp_server_root)
        self.tftp_server_log_export_button.clicked.connect(self._export_tftp_server_logs)
        self.tftp_server_readonly_check.toggled.connect(self._sync_tftp_server_access_label)
        return page

    def _refresh_tftp_support_notice(self) -> None:
        support = self.state.tftp_service.runtime_support_status()
        self._apply_tftp_support_label(self.tftp_client_support_label, support)
        self._apply_tftp_support_label(self.tftp_server_support_label, support)

    def _apply_tftp_support_label(self, label: QLabel, support: OperationResult) -> None:
        if support.success:
            label.clear()
            label.hide()
            return
        label.setText(support.message)
        label.setStyleSheet("color:#b71c1c;")
        label.show()

    def _show_tftp_support_warning(self, title: str, support: OperationResult) -> None:
        text = support.message
        if support.details:
            text += f"\n\n{support.details}"
        QMessageBox.warning(self, title, text)

    def _choose_tftp_upload_file(self) -> None:
        file_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "업로드할 파일 선택",
            self.tftp_client_local_folder_edit.text().strip() or str(self.state.paths.root),
        )
        if file_path:
            self.tftp_client_upload_path_edit.setText(file_path)

    def _choose_tftp_local_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "로컬 폴더 선택",
            self.tftp_client_local_folder_edit.text().strip() or str(self.state.paths.root),
        )
        if folder:
            self.tftp_client_local_folder_edit.setText(folder)

    def _start_tftp_upload(self) -> None:
        support = self.state.tftp_service.runtime_support_status()
        self._apply_tftp_support_label(self.tftp_client_support_label, support)
        if not support.success:
            self._show_tftp_support_warning("TFTP 준비 필요", support)
            return

        self.tftp_client_log_output.clear()
        self._tftp_client_logs = []
        self._tftp_transfer_row_map.clear()
        self.tftp_transfer_table.setRowCount(0)
        self.tftp_client_cancel_event = self._new_tftp_cancel_event()
        self._set_tftp_client_busy(True)
        self._start_worker(
            self.state.tftp_service.upload_file,
            self.tftp_client_host_edit.text().strip(),
            self.tftp_client_port_edit.text().strip() or "69",
            self.tftp_client_upload_path_edit.text().strip(),
            self.tftp_client_remote_path_edit.text().strip(),
            self.tftp_client_timeout_edit.text().strip() or "5",
            self.tftp_client_retries_edit.text().strip() or "3",
            cancel_event=self.tftp_client_cancel_event,
            on_progress=self._handle_tftp_client_progress,
            on_result=self._finish_tftp_client_job,
            on_finished=lambda: self._set_tftp_client_busy(False),
            error_title="TFTP 업로드 실패",
        )

    def _start_tftp_download(self) -> None:
        support = self.state.tftp_service.runtime_support_status()
        self._apply_tftp_support_label(self.tftp_client_support_label, support)
        if not support.success:
            self._show_tftp_support_warning("TFTP 준비 필요", support)
            return

        local_folder = self.tftp_client_local_folder_edit.text().strip()
        if not local_folder:
            self._choose_tftp_local_folder()
            local_folder = self.tftp_client_local_folder_edit.text().strip()
        if not local_folder:
            return

        self.tftp_client_log_output.clear()
        self._tftp_client_logs = []
        self._tftp_transfer_row_map.clear()
        self.tftp_transfer_table.setRowCount(0)
        self.tftp_client_cancel_event = self._new_tftp_cancel_event()
        self._set_tftp_client_busy(True)
        self._start_worker(
            self.state.tftp_service.download_file,
            self.tftp_client_host_edit.text().strip(),
            self.tftp_client_port_edit.text().strip() or "69",
            self.tftp_client_remote_path_edit.text().strip(),
            local_folder,
            self.tftp_client_timeout_edit.text().strip() or "5",
            self.tftp_client_retries_edit.text().strip() or "3",
            cancel_event=self.tftp_client_cancel_event,
            on_progress=self._handle_tftp_client_progress,
            on_result=self._finish_tftp_client_job,
            on_finished=lambda: self._set_tftp_client_busy(False),
            error_title="TFTP 다운로드 실패",
        )

    def _handle_tftp_client_progress(self, event: dict) -> None:
        kind = str(event.get("kind", "") or "")
        if kind == "log":
            message = str(event.get("message", "") or "")
            if message:
                self._tftp_client_logs.append(message)
                self.tftp_client_log_output.appendPlainText(message)
            self._update_tftp_client_activity_state()
            return
        if kind == "transfer":
            result = event.get("result")
            if isinstance(result, TftpTransferResult):
                self._upsert_tftp_transfer_result(result)

    def _finish_tftp_client_job(self, result: OperationResult) -> None:
        self.tftp_client_status_label.setText(result.message)
        payload = result.payload if isinstance(result.payload, list) else []
        for item in payload:
            if isinstance(item, TftpTransferResult):
                self._upsert_tftp_transfer_result(item)
        self._update_tftp_client_activity_state()

    def _upsert_tftp_transfer_result(self, result: TftpTransferResult) -> None:
        key = (result.timestamp, result.action, result.source_path, result.target_path)
        row = self._tftp_transfer_row_map.get(key)
        if row is None:
            row = self.tftp_transfer_table.rowCount()
            self.tftp_transfer_table.insertRow(row)
            self._tftp_transfer_row_map[key] = row

        values = [
            result.timestamp,
            result.action,
            result.source_path,
            result.target_path,
            result.size_text,
            result.progress_text,
            result.duration_text,
            result.status,
            result.error or "-",
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
            self.tftp_transfer_table.setItem(row, column, item)

        self._update_tftp_client_activity_state()

    def _export_tftp_transfer_results(self) -> None:
        if self.tftp_transfer_table.rowCount() == 0:
            QMessageBox.warning(self, "내보내기 불가", "저장할 TFTP 전송 결과가 없습니다.")
            return
        path = timestamped_export_path(self.state.paths.exports_dir, "tftp_transfers", "csv")
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [self.tftp_transfer_table.horizontalHeaderItem(column).text() for column in range(self.tftp_transfer_table.columnCount())]
            )
            for row in range(self.tftp_transfer_table.rowCount()):
                writer.writerow(
                    [self._cell(self.tftp_transfer_table, row, column) for column in range(self.tftp_transfer_table.columnCount())]
                )
        QMessageBox.information(self, "CSV 저장 완료", f"TFTP 전송 결과를 저장했습니다.\n{path}")

    def _export_tftp_client_logs(self) -> None:
        if not self._tftp_client_logs:
            QMessageBox.warning(self, "내보내기 불가", "저장할 TFTP 클라이언트 로그가 없습니다.")
            return
        path = timestamped_export_path(self.state.paths.exports_dir, "tftp_client_log", "txt")
        path.write_text("\n".join(self._tftp_client_logs) + "\n", encoding="utf-8")
        QMessageBox.information(self, "TXT 저장 완료", f"TFTP 클라이언트 로그를 저장했습니다.\n{path}")

    def _start_tftp_server(self) -> None:
        if self._tftp_server_running:
            return
        support = self.state.tftp_service.runtime_support_status()
        self._apply_tftp_support_label(self.tftp_server_support_label, support)
        if not support.success:
            self._show_tftp_support_warning("TFTP 서버 준비 필요", support)
            return

        self.tftp_server_log_output.clear()
        self._tftp_server_logs = []
        self._tftp_server_runtime = None
        self.tftp_server_cancel_event = self._new_tftp_cancel_event()
        self._set_tftp_server_running(True)
        self._start_worker(
            self.state.tftp_service.run_temporary_server,
            self.tftp_server_bind_host_edit.text().strip(),
            self.tftp_server_port_edit.text().strip() or "69",
            self.tftp_server_root_edit.text().strip(),
            self.tftp_server_readonly_check.isChecked(),
            cancel_event=self.tftp_server_cancel_event,
            on_progress=self._handle_tftp_server_progress,
            on_result=self._finish_tftp_server_job,
            on_finished=lambda: self._set_tftp_server_running(False),
            error_title="TFTP 서버 실행 실패",
        )

    def _handle_tftp_server_progress(self, event: dict) -> None:
        kind = str(event.get("kind", "") or "")
        if kind == "server_log":
            message = str(event.get("message", "") or "")
            if message:
                self._tftp_server_logs.append(message)
                self.tftp_server_log_output.appendPlainText(message)
            self._update_tftp_server_activity_state()
            return
        if kind == "server_runtime":
            runtime = event.get("runtime")
            if isinstance(runtime, TftpServerRuntime):
                self._tftp_server_runtime = runtime
                self._update_tftp_server_runtime_labels(runtime)

    def _finish_tftp_server_job(self, result: OperationResult) -> None:
        self.tftp_server_state_label.setText(result.message)
        self.tftp_server_cancel_event = None
        self._update_tftp_server_activity_state()

    def _stop_tftp_server(self) -> None:
        if self.tftp_server_cancel_event is not None:
            self.tftp_server_cancel_event.set()

    def _choose_tftp_server_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "공유 루트 폴더 선택",
            self.tftp_server_root_edit.text().strip() or str(self.state.paths.root),
        )
        if folder:
            self.tftp_server_root_edit.setText(folder)

    def _open_tftp_server_root(self) -> None:
        root_path = self.tftp_server_root_edit.text().strip()
        if not root_path:
            QMessageBox.warning(self, "경로 필요", "먼저 공유 루트 폴더를 지정해 주세요.")
            return
        open_in_explorer(Path(root_path))

    def _update_tftp_server_runtime_labels(self, runtime: TftpServerRuntime) -> None:
        self.tftp_server_state_label.setText("TFTP 실행 중")
        self.tftp_server_endpoint_label.setText(f"{runtime.bind_host}:{runtime.port}")
        self.tftp_server_access_label.setText("읽기 전용" if runtime.read_only else "읽기/쓰기")
        self.tftp_server_sessions_label.setText(str(runtime.session_count))

    def _sync_tftp_server_access_label(self) -> None:
        self.tftp_server_access_label.setText("읽기 전용" if self.tftp_server_readonly_check.isChecked() else "읽기/쓰기")

    def _export_tftp_server_logs(self) -> None:
        if not self._tftp_server_logs:
            QMessageBox.warning(self, "내보내기 불가", "저장할 TFTP 서버 로그가 없습니다.")
            return
        path = timestamped_export_path(self.state.paths.exports_dir, "tftp_server_log", "txt")
        path.write_text("\n".join(self._tftp_server_logs) + "\n", encoding="utf-8")
        QMessageBox.information(self, "TXT 저장 완료", f"TFTP 서버 로그를 저장했습니다.\n{path}")

    def _set_tftp_client_busy(self, busy: bool) -> None:
        self._tftp_client_busy = busy
        self.tftp_client_host_edit.setEnabled(not busy)
        self.tftp_client_port_edit.setEnabled(not busy)
        self.tftp_client_timeout_edit.setEnabled(not busy)
        self.tftp_client_retries_edit.setEnabled(not busy)
        self.tftp_client_remote_path_edit.setEnabled(not busy)
        self.tftp_client_upload_path_edit.setEnabled(not busy)
        self.tftp_client_upload_browse_button.setEnabled(not busy)
        self.tftp_client_local_folder_edit.setEnabled(not busy)
        self.tftp_client_local_browse_button.setEnabled(not busy)
        self.tftp_client_upload_button.setEnabled(not busy)
        self.tftp_client_download_button.setEnabled(not busy)
        self.tftp_client_cancel_button.setEnabled(busy)
        self._update_tftp_client_activity_state()

    def _set_tftp_server_running(self, running: bool) -> None:
        self._tftp_server_running = running
        self.tftp_server_start_button.setEnabled(not running)
        self.tftp_server_stop_button.setEnabled(running)
        self.tftp_server_bind_host_edit.setEnabled(not running)
        self.tftp_server_port_edit.setEnabled(not running)
        self.tftp_server_root_edit.setEnabled(not running)
        self.tftp_server_root_browse_button.setEnabled(not running)
        self.tftp_server_readonly_check.setEnabled(not running)
        self._update_tftp_server_activity_state()

    def _update_tftp_client_activity_state(self) -> None:
        has_results = self.tftp_transfer_table.rowCount() > 0
        has_logs = bool(self._tftp_client_logs)
        self.tftp_transfer_export_button.setEnabled(has_results)
        self.tftp_client_log_export_button.setEnabled(has_logs)

    def _update_tftp_server_activity_state(self) -> None:
        self.tftp_server_log_export_button.setEnabled(bool(self._tftp_server_logs))

    def _cancel_tftp_client_job(self) -> None:
        if self.tftp_client_cancel_event is not None:
            self.tftp_client_cancel_event.set()

    def _new_tftp_cancel_event(self):
        from threading import Event

        return Event()

    def _collect_tftp_runtime_state(self) -> dict:
        return {
            "client": {
                "host": self.tftp_client_host_edit.text().strip(),
                "port": self.tftp_client_port_edit.text().strip(),
                "remote_path": self.tftp_client_remote_path_edit.text().strip(),
                "local_folder": self.tftp_client_local_folder_edit.text().strip(),
                "local_upload_path": self.tftp_client_upload_path_edit.text().strip(),
                "timeout_seconds": self.tftp_client_timeout_edit.text().strip(),
                "retries": self.tftp_client_retries_edit.text().strip(),
            },
            "server": {
                "bind_host": self.tftp_server_bind_host_edit.text().strip(),
                "port": self.tftp_server_port_edit.text().strip(),
                "root_folder": self.tftp_server_root_edit.text().strip(),
                "read_only": self.tftp_server_readonly_check.isChecked(),
            },
        }

    def _restore_tftp_runtime_state(self) -> None:
        runtime = self.state.tftp_runtime if isinstance(self.state.tftp_runtime, dict) else {}
        client_state = runtime.get("client", {}) if isinstance(runtime.get("client", {}), dict) else {}
        server_state = runtime.get("server", {}) if isinstance(runtime.get("server", {}), dict) else {}

        self.tftp_client_host_edit.setText(str(client_state.get("host", "") or ""))
        self.tftp_client_port_edit.setText(str(client_state.get("port", "") or ""))
        self.tftp_client_remote_path_edit.setText(str(client_state.get("remote_path", "") or ""))
        self.tftp_client_local_folder_edit.setText(str(client_state.get("local_folder", "") or ""))
        self.tftp_client_upload_path_edit.setText(str(client_state.get("local_upload_path", "") or ""))
        self.tftp_client_timeout_edit.setText(str(client_state.get("timeout_seconds", "") or ""))
        self.tftp_client_retries_edit.setText(str(client_state.get("retries", "") or ""))

        self.tftp_server_bind_host_edit.setText(str(server_state.get("bind_host", "") or ""))
        self.tftp_server_port_edit.setText(str(server_state.get("port", "") or ""))
        self.tftp_server_root_edit.setText(str(server_state.get("root_folder", "") or ""))
        self.tftp_server_readonly_check.setChecked(bool(server_state.get("read_only", True)))
        self._sync_tftp_server_access_label()
