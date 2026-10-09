from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel, QMessageBox, QVBoxLayout, QWidget

from app.app_state import AppState
from app.ui.common.disclosure import add_page_header_action, make_page_header
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
        paths_changed = getattr(self.state, "paths_changed", None)
        if paths_changed is not None and callable(
            getattr(paths_changed, "connect", None)
        ):
            paths_changed.connect(self._sync_exports_dir)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._content_layout = layout
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self._page_header = make_page_header(
            "설정 명령 만들기", "장비 값을 입력하고 CLI를 확인·저장하세요."
        )
        self.full_editor_button = make_action_button(
            "전체 창",
            ActionKind.EDIT,
            tooltip="현재 편집기를 별도 창으로 옮겨 크게 표시합니다. 창을 닫으면 편집 중인 값 그대로 돌아옵니다.",
            object_name="configBuilderFullEditorButton",
        )
        self.full_editor_button.clicked.connect(self._open_full_editor)
        layout.addWidget(self._page_header)
        self.full_editor_placeholder = QLabel("현재 편집기가 전체 창에 열려 있습니다. 전체 창을 닫으면 이 화면으로 돌아옵니다.")
        self.full_editor_placeholder.setWordWrap(True)
        self.full_editor_placeholder.hide()
        layout.addWidget(self.full_editor_placeholder)

        self.builder_widget = SwitchConfigBuilderWidget(
            profiles_dir=self.service.profiles_dir,
            parent=self,
            embedded=True,
            exports_dir=self._current_exports_dir(),
        )
        self.builder_widget.setObjectName("configBuilderEmbeddedBuilder")
        self.profile_management_button = self.builder_widget.create_profile_management_button(self)
        add_page_header_action(self._page_header, self.profile_management_button)
        add_page_header_action(self._page_header, self.full_editor_button)
        layout.addWidget(self.builder_widget, 1)

    def _open_full_editor(self) -> None:
        if self._builder_window is not None and self._builder_window.isVisible():
            self._builder_window.raise_()
            self._builder_window.activateWindow()
            return
        try:
            window = DesktopWindow(
                profiles_dir=self.service.profiles_dir,
                exports_dir=self._current_exports_dir(),
                existing_builder=self.builder_widget,
            )
        except Exception as exc:
            QMessageBox.warning(self, "전체 편집기", str(exc))
            return

        window.builder_released.connect(self._restore_embedded_builder)
        self.full_editor_placeholder.show()
        self._builder_window = window
        self._builder_window.show()
        self._builder_window.raise_()
        self._builder_window.activateWindow()

    def _restore_embedded_builder(self, builder: SwitchConfigBuilderWidget) -> None:
        self._content_layout.addWidget(builder, 1)
        self.full_editor_placeholder.hide()
        builder.show()

    def _current_exports_dir(self) -> Path | None:
        paths = getattr(self.state, "paths", None)
        value = getattr(paths, "exports_dir", None)
        return Path(value) if value else None

    def _sync_exports_dir(self) -> None:
        exports_dir = self._current_exports_dir()
        if exports_dir is None:
            return
        self.builder_widget.exports_dir = exports_dir
        if self._builder_window is not None:
            self._builder_window.builder.exports_dir = exports_dir

    def _current_profile_id(self) -> str:
        combo = getattr(self.builder_widget, "add_profile_combo", None)
        return combo.currentText() if combo is not None else ""

    def _current_device_values_path(self) -> Path | None:
        path = getattr(self.builder_widget, "current_file_path", None)
        return Path(path) if path else None

    def prepare_close(self) -> bool:
        if self._builder_window is not None and self._builder_window.isVisible():
            if not self._builder_window.close():
                return False
        return self.builder_widget.prepare_close()
