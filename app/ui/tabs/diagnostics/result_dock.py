from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)


class ResultDockWidget(QDockWidget):
    def __init__(self, title: str, on_restore, parent=None) -> None:
        super().__init__(title, parent)
        self._on_restore = on_restore
        self._closing_from_restore = False

    def closeEvent(self, event) -> None:
        if not self._closing_from_restore and self._on_restore is not None:
            self._on_restore(from_dock_close=True)
        super().closeEvent(event)


class ResultDockMixin:
    def _build_log_panel(self, title: str, output) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(title))
        layout.addWidget(output)
        return panel

    def _build_result_splitter(
        self,
        key: str,
        table: QTableWidget,
        log_panel: QWidget,
    ) -> QSplitter:
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)

        result_host = QWidget()
        result_host_layout = QVBoxLayout(result_host)
        result_host_layout.setContentsMargins(0, 0, 0, 0)

        result_panel = QWidget()
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.addWidget(table, 1)

        button_row = QHBoxLayout()
        csv_button = QPushButton("전체 결과 CSV 저장")
        log_button = QPushButton("선택 항목 로그 저장")
        button_row.addWidget(csv_button)
        button_row.addWidget(log_button)
        button_row.addStretch(1)
        result_layout.addLayout(button_row)

        placeholder = QLabel("결과 표가 분리되어 있습니다. 상단 `보기` 메뉴에서 다시 복원할 수 있습니다.")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("color:#666; padding:6px 10px; border:1px dashed #bbb;")
        placeholder.setMaximumHeight(34)
        placeholder.hide()

        self._result_hosts[key] = result_host
        result_host_layout.addWidget(result_panel)
        result_host_layout.addWidget(placeholder)

        self._result_host_layouts[key] = result_host_layout
        self._result_panels[key] = result_panel
        self._result_placeholders[key] = placeholder
        self._result_splitters[key] = splitter

        if key == "ping":
            self.ping_csv_button = csv_button
            self.ping_log_export_button = log_button
            self.ping_csv_button.clicked.connect(lambda: self._export_table_to_csv(self.ping_table, "ping_results"))
            self.ping_log_export_button.clicked.connect(self.export_selected_ping_logs)
        else:
            self.tcp_csv_button = csv_button
            self.tcp_log_export_button = log_button
            self.tcp_csv_button.clicked.connect(lambda: self._export_table_to_csv(self.tcp_table, "tcp_results"))
            self.tcp_log_export_button.clicked.connect(self.export_selected_tcp_logs)

        splitter.addWidget(result_host)
        splitter.addWidget(log_panel)
        splitter.setSizes([430, 170])
        return splitter

    def _detach_result_panel(self, key: str) -> None:
        main_window = self.window()
        if not isinstance(main_window, QMainWindow):
            QMessageBox.warning(self, "분리 실패", "메인 창을 찾지 못해 결과 표를 분리할 수 없습니다.")
            return

        panel = self._result_panels[key]
        result_host = self._result_hosts[key]
        host_layout = self._result_host_layouts[key]
        placeholder = self._result_placeholders[key]

        host_layout.removeWidget(panel)
        panel.setParent(None)
        placeholder.show()
        result_host.setMaximumHeight(40)

        window_title = "Ping 결과 표" if key == "ping" else "TCPing 결과 표"
        dock = ResultDockWidget(
            window_title,
            lambda from_dock_close=False, mode=key: self._attach_result_panel(mode, from_dock_close),
            main_window,
        )
        dock.setObjectName(f"{key}_result_dock")
        dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        dock.setFeatures(
            QDockWidget.DockWidgetClosable
            | QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
        )
        dock.setMinimumHeight(180)
        dock.setWidget(panel)
        main_window.addDockWidget(Qt.BottomDockWidgetArea, dock)
        if hasattr(main_window, "log_dock"):
            try:
                main_window.tabifyDockWidget(main_window.log_dock, dock)
            except Exception:
                pass
        dock.show()
        dock.raise_()
        self._floating_result_docks[key] = dock
        self._result_splitters[key].setSizes([40, 200])
        self.result_dock_visibility_changed.emit(key, True)

    def _attach_result_panel(self, key: str, from_dock_close: bool = False) -> None:
        result_host = self._result_hosts[key]
        host_layout = self._result_host_layouts[key]
        panel = self._result_panels[key]
        placeholder = self._result_placeholders[key]
        dock = self._floating_result_docks.get(key)

        if dock is not None:
            current_widget = dock.widget()
            if current_widget is not None and current_widget is not panel:
                panel = current_widget
                self._result_panels[key] = panel
            if current_widget is not None:
                current_widget.setParent(None)
            dock._closing_from_restore = True
            if not from_dock_close:
                dock.close()
            dock.deleteLater()
            self._floating_result_docks[key] = None

        placeholder.hide()
        result_host.setMaximumHeight(16777215)
        host_layout.insertWidget(0, panel)
        panel.show()
        self._result_splitters[key].setSizes([430, 170])
        self.result_dock_visibility_changed.emit(key, False)

    def set_result_dock_visible(self, key: str, visible: bool) -> None:
        if visible:
            if self._floating_result_docks.get(key) is None:
                self._detach_result_panel(key)
            else:
                dock = self._floating_result_docks[key]
                if dock is not None:
                    dock.show()
                    dock.raise_()
            return
        if self._floating_result_docks.get(key) is not None:
            self._attach_result_panel(key)

    def is_result_dock_visible(self, key: str) -> bool:
        return self._floating_result_docks.get(key) is not None
