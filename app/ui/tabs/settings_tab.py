from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.ui.common import make_selectable_wrapped_label
from app.utils.file_utils import default_update_config, open_in_explorer
from app.version import __version__
from netops_suite.ui.actions import ActionKind, make_action_button


class SettingsTab(QWidget):
    check_updates_requested = Signal(dict)

    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._build_ui()
        self.state.config_reloaded.connect(self.reload_view)
        self.reload_view()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        update_group = QGroupBox("프로그램 업데이트")
        update_layout = QVBoxLayout(update_group)

        summary_label = QLabel(
            "공식 배포 채널에서 새 버전을 확인합니다. 설치 파일은 다운로드 후 SHA-256으로 무결성을 검증하고, "
            "게시자 신뢰는 Windows 코드서명 정보로 확인합니다. 사용자가 확인한 경우에만 설치 프로그램을 실행합니다."
        )
        summary_label.setWordWrap(True)
        update_layout.addWidget(summary_label)

        form = QFormLayout()
        self.version_label = QLabel(__version__)
        self.check_on_startup_check = QCheckBox("프로그램 시작 시 업데이트 확인")

        form.addRow("현재 버전", self.version_label)
        form.addRow("", self.check_on_startup_check)
        update_layout.addLayout(form)

        button_row = QHBoxLayout()
        self.check_update_button = make_action_button("업데이트 확인", ActionKind.START)
        button_row.addWidget(self.check_update_button)
        button_row.addStretch(1)
        update_layout.addLayout(button_row)

        self.update_status_label = QLabel("업데이트는 프로그램 내부에 고정된 공식 배포 채널을 사용합니다.")
        self.update_status_label.setWordWrap(True)
        self.update_details = QPlainTextEdit()
        self.update_details.setReadOnly(True)
        self.update_details.setMaximumHeight(180)
        update_layout.addWidget(self.update_status_label)
        update_layout.addWidget(self.update_details)
        layout.addWidget(update_group)

        path_group = QGroupBox("경로")
        path_layout = QVBoxLayout(path_group)
        self.config_dir_label = make_selectable_wrapped_label()
        self.ip_profile_label = make_selectable_wrapped_label()
        self.log_dir_label = make_selectable_wrapped_label()
        path_layout.addWidget(self.config_dir_label)
        path_layout.addWidget(self.ip_profile_label)
        path_layout.addWidget(self.log_dir_label)

        folder_button_row = QHBoxLayout()
        self.open_config_button = make_action_button("설정 폴더 열기", ActionKind.OPEN)
        self.open_logs_button = make_action_button("로그 폴더 열기", ActionKind.OPEN)
        self.reload_button = make_action_button("다시 불러오기", ActionKind.REFRESH)
        folder_button_row.addWidget(self.open_config_button)
        folder_button_row.addWidget(self.open_logs_button)
        folder_button_row.addWidget(self.reload_button)
        path_layout.addLayout(folder_button_row)
        layout.addWidget(path_group)
        layout.addStretch(1)

        self.open_config_button.clicked.connect(lambda: open_in_explorer(self.state.paths.config_dir))
        self.open_logs_button.clicked.connect(lambda: open_in_explorer(self.state.paths.logs_dir))
        self.reload_button.clicked.connect(self.state.reload_config_files)
        self.check_on_startup_check.toggled.connect(self._save_startup_update_preference)
        self.check_update_button.clicked.connect(self._request_update_check)

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
        if details:
            self.update_details.setPlainText(details)
        elif message:
            self.update_details.clear()

    def set_update_busy(self, busy: bool) -> None:
        self.check_update_button.setEnabled(not busy)

    def reload_view(self) -> None:
        update_config = self.state.app_config.get("update", {})
        was_blocked = self.check_on_startup_check.blockSignals(True)
        self.check_on_startup_check.setChecked(bool(update_config.get("check_on_startup", False)))
        self.check_on_startup_check.blockSignals(was_blocked)

        self.config_dir_label.setText(f"설정 폴더: {self.state.paths.config_dir}")
        self.ip_profile_label.setText(f"IP 프로파일: {self.state.paths.ip_profiles}")
        self.log_dir_label.setText(f"로그 폴더: {self.state.paths.logs_dir}")
        self.version_label.setText(__version__)
        self.set_update_status("업데이트는 프로그램 내부에 고정된 공식 배포 채널을 사용합니다.")

    def _request_update_check(self) -> None:
        self.check_updates_requested.emit(self.current_update_config())
