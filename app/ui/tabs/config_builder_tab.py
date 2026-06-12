from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QVBoxLayout, QWidget

from app.app_state import AppState
from app.ui.common import make_step_hint
from netops_suite.modules.config_builder import ConfigBuilderService
from netops_suite.modules.config_builder.switch_configurator.desktop_impl import (
    DesktopWindow,
    SwitchConfigBuilderWidget,
)
from netops_suite.ui.actions import ActionKind, make_action_button


class ConfigBuilderTab(QWidget):
    def __init__(self, state: AppState | None = None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        user_data = state.paths.data_root / "config_builder" if state else None
        self.service = ConfigBuilderService(user_data_dir=user_data)
        self._builder_window: DesktopWindow | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(
            make_step_hint("장비 변수 파일을 열거나 샘플로 시작한 뒤, 행을 선택해 CLI를 확인합니다."),
            1,
        )
        self.full_editor_button = make_action_button(
            "전체 창",
            ActionKind.EDIT,
            tooltip="같은 CLI 설정 생성 화면을 별도 창으로 크게 열 때 사용합니다.",
            object_name="configBuilderFullEditorButton",
        )
        self.full_editor_button.clicked.connect(self._open_full_editor)
        header.addWidget(self.full_editor_button)
        layout.addLayout(header)

        self.builder_widget = SwitchConfigBuilderWidget(
            profiles_dir=self.service.profiles_dir,
            parent=self,
            embedded=True,
        )
        self.builder_widget.setObjectName("configBuilderEmbeddedBuilder")
        layout.addWidget(self.builder_widget, 1)

    def _open_full_editor(self) -> None:
        try:
            window = DesktopWindow(profiles_dir=self.service.profiles_dir)
        except Exception as exc:
            QMessageBox.warning(self, "전체 편집기", str(exc))
            return

        profile_id = self._current_profile_id()
        if profile_id and hasattr(window, "add_profile_combo"):
            window.add_profile_combo.setCurrentText(profile_id)

        device_values_path = self._current_device_values_path()
        if device_values_path and hasattr(window, "load_device_file"):
            window.load_device_file(device_values_path)

        self._builder_window = window
        self._builder_window.show()
        self._builder_window.raise_()
        self._builder_window.activateWindow()

    def _current_profile_id(self) -> str:
        combo = getattr(self.builder_widget, "add_profile_combo", None)
        return combo.currentText() if combo is not None else ""

    def _current_device_values_path(self) -> Path | None:
        path = getattr(self.builder_widget, "current_file_path", None)
        return Path(path) if path else None
