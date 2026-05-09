from __future__ import annotations

import ipaddress
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
import re
from uuid import uuid4

from PySide6.QtCore import QAbstractTableModel, QByteArray, QModelIndex, QObject, QPoint, QRect, QItemSelectionModel, QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QCloseEvent, QFont, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QStyledItemDelegate,
    QSpinBox,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .engine import ConfigEngine, build_bundle_text
from .io_utils import load_profiles_from_directory
from .models import BlockSpec, DeviceRecord, Profile, RenderedConfig, ValidationIssue, VariableSpec
from .models import (
    AUTO_INCREMENT_IPV4,
    AUTO_INCREMENT_NONE,
    AUTO_INCREMENT_SUFFIX_NUMBER,
)
from .profile_builder_dialog import ProfileBuilderDialog
from .table_data import DeviceTable, load_device_table_from_path, make_blank_table_row, save_device_table_to_path
from .app_icon import build_app_icon, set_windows_app_id


ROOT_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT_DIR / "profiles"
OUTPUT_DIR = ROOT_DIR / "outputs"
BACKUP_DIR = OUTPUT_DIR / "backups"
ACTIVITY_LOG_PATH = OUTPUT_DIR / "desktop_activity.log"
APP_STATE_PATH = ROOT_DIR / ".desktop_state.json"
TUTORIAL_WORKSPACE_DIR = OUTPUT_DIR / "tutorial"
TUTORIAL_WORKSPACE_PATH = TUTORIAL_WORKSPACE_DIR / "tutorial_hands_on_devices.csv"
TUTORIAL_PROFILE_ID = "TUTORIAL_HANDS_ON_SWITCH"
TUTORIAL_DEVICE_ID = "SW-TUTORIAL-01"
TUTORIAL_HOSTNAME = "SW-TUTORIAL-01"
TUTORIAL_MGMT_IP = "192.168.10.11"
TUTORIAL_MGMT_MASK = "255.255.255.0"
MAX_WIDGET_WIDTH = 16777215

DEFAULT_PIN_ORDER = ["device_id", "profile_id", "hostname", "mgmt_ip", "mgmt_vlan", "access_interface"]
DEFAULT_RECENT_LIMIT = 8
SECRET_HEADER_KEYWORDS = ("secret", "password", "community", "passphrase", "token", "key")
IP_LIKE_HEADER_KEYWORDS = ("_ip", "gateway", "loopback", "address")
TRAILING_NUMBER_PATTERN = re.compile(r"^(.*?)(\d+)$")
INTERNAL_ROW_UID = "__row_uid__"
ROW_STATE_PENDING = "pending"
ROW_STATE_COPIED = "copied"
ROW_STATE_DONE = "done"
TABLE_STYLE = """
QTableView {
    background: #ffffff;
    alternate-background-color: #f8f5ed;
    border: 1px solid #d7ccb8;
    border-radius: 8px;
    gridline-color: #e2d8c6;
    selection-background-color: #17212b;
    selection-color: #ffffff;
}
QTableView::item { padding: 4px 6px; }
QTableView::item:hover { background: #e9dfcf; color: #221b12; }
QTableView::item:selected { background: #17212b; color: #ffffff; }
QHeaderView::section {
    background: #efe8da;
    color: #221b12;
    padding: 6px 8px;
    border: 0;
    border-bottom: 1px solid #d7ccb8;
    border-right: 1px solid #e2d8c6;
    font-weight: 600;
}
"""

EMBEDDED_TABLE_STYLE = """
QTableView {
    background: #ffffff;
    alternate-background-color: #f8f5ed;
    border: 0;
    gridline-color: #e2d8c6;
    selection-background-color: #17212b;
    selection-color: #ffffff;
}
QTableView::item { padding: 4px 6px; }
QTableView::item:hover { background: #e9dfcf; color: #221b12; }
QTableView::item:selected { background: #17212b; color: #ffffff; }
QHeaderView::section {
    background: #efe8da;
    color: #221b12;
    padding: 6px 8px;
    border: 0;
    border-bottom: 1px solid #d7ccb8;
    border-right: 1px solid #e2d8c6;
    font-weight: 600;
}
"""

COMPACT_UI_STYLE = """
QGroupBox {
    font-weight: 600;
    margin-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}
QPushButton {
    padding: 2px 7px;
    min-height: 20px;
}
QComboBox, QLineEdit {
    min-height: 20px;
    padding: 2px 6px;
}
QLabel {
    color: #221b12;
}
QStatusBar {
    font-size: 11px;
}
QTabWidget::tab-bar {
    left: 2px;
}
QTabWidget::pane {
    border: 0;
    background: transparent;
    margin-top: 2px;
}
QTabBar::tab {
    background: #f5efe4;
    color: #6a6258;
    border: 1px solid #d7ccb8;
    border-radius: 6px;
    padding: 2px 8px;
    min-width: 32px;
    margin-right: 3px;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #221b12;
}
QLabel#GuideTitle {
    font-size: 11px;
    font-weight: 700;
    color: #1f1912;
}
QLabel#GuideMeta {
    color: #6a6258;
    padding: 0;
}
QLabel#GuideCard {
    color: #3f392f;
    padding: 0;
}
QLabel#GuideHint {
    background: #f5f7fa;
    color: #27415a;
    border: 1px solid #dce5f0;
    border-radius: 6px;
    padding: 4px 6px;
}
QLabel#SelectionName {
    font-weight: 700;
    color: #1f1912;
}
QFrame#DeviceTableShell {
    background: #ffffff;
    border: 1px solid #d7ccb8;
    border-radius: 8px;
}
QFrame#PinnedDivider {
    background: #efe4d2;
    min-width: 8px;
    max-width: 8px;
    border-left: 1px solid #cebca0;
    border-right: 1px solid #cebca0;
}
"""

ERROR_BG = QColor("#fff1ed")
WARNING_BG = QColor("#fff8e4")
DEFAULT_BG = QColor("#eef8eb")
COPIED_BG = QColor("#eef6ff")
DONE_BG = QColor("#e8f7ec")

AUTO_INCREMENT_LABELS = {
    AUTO_INCREMENT_NONE: "없음",
    AUTO_INCREMENT_SUFFIX_NUMBER: "끝 숫자 증가",
    AUTO_INCREMENT_IPV4: "IPv4 증가",
}

ROW_STATE_LABELS = {
    ROW_STATE_PENDING: "대기",
    ROW_STATE_COPIED: "복사 완료",
    ROW_STATE_DONE: "적용 완료",
}


class AutoScrollListWidget(QListWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_scroll_direction = 0
        self._drag_scroll_margin = 28
        self._drag_scroll_timer = QTimer(self)
        self._drag_scroll_timer.setInterval(40)
        self._drag_scroll_timer.timeout.connect(self._perform_drag_scroll)
        self.setAutoScroll(True)
        self.setAutoScrollMargin(self._drag_scroll_margin)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        position = event.position().toPoint() if hasattr(event, "position") else event.pos()
        self._update_drag_scroll_state(position.y())
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._stop_drag_scroll()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self._stop_drag_scroll()
        super().dropEvent(event)

    def _update_drag_scroll_state(self, y_pos: int) -> None:
        viewport_height = self.viewport().height()
        if y_pos <= self._drag_scroll_margin:
            self._drag_scroll_direction = -1
        elif y_pos >= viewport_height - self._drag_scroll_margin:
            self._drag_scroll_direction = 1
        else:
            self._drag_scroll_direction = 0

        if self._drag_scroll_direction == 0:
            self._drag_scroll_timer.stop()
        elif not self._drag_scroll_timer.isActive():
            self._drag_scroll_timer.start()

    def _perform_drag_scroll(self) -> None:
        if self._drag_scroll_direction == 0:
            self._drag_scroll_timer.stop()
            return
        scrollbar = self.verticalScrollBar()
        step = max(scrollbar.singleStep(), 24)
        scrollbar.setValue(scrollbar.value() + (self._drag_scroll_direction * step))

    def _stop_drag_scroll(self) -> None:
        self._drag_scroll_direction = 0
        self._drag_scroll_timer.stop()


class AutoScrollHeaderView(QHeaderView):
    boundaryDropRequested = Signal(int, bool)

    def __init__(self, orientation: Qt.Orientation, parent: QTableView | None = None) -> None:
        super().__init__(orientation, parent)
        self._drag_scroll_direction = 0
        self._drag_scroll_margin = 36
        self._drag_scroll_timer = QTimer(self)
        self._drag_scroll_timer.setInterval(40)
        self._drag_scroll_timer.timeout.connect(self._perform_drag_scroll)
        self._highlighted_sections: set[int] = set()
        self._pressed_logical_index = -1

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        position = event.position().toPoint() if hasattr(event, "position") else event.pos()
        self._pressed_logical_index = self.logicalIndexAt(position)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self.sectionsMovable() and event.buttons() & Qt.LeftButton:
            position = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self._update_drag_scroll_state(position.x())
        else:
            self._stop_drag_scroll()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._stop_drag_scroll()
        if self.sectionsMovable() and self._pressed_logical_index >= 0:
            position = event.position().toPoint() if hasattr(event, "position") else event.pos()
            role = str(self.parent().property("pinBoundaryRole") or "") if self.parent() is not None else ""
            viewport_width = self.viewport().width()
            if role == "main" and position.x() <= self._drag_scroll_margin:
                self.boundaryDropRequested.emit(self._pressed_logical_index, True)
            elif role == "pinned" and position.x() >= viewport_width - self._drag_scroll_margin:
                self.boundaryDropRequested.emit(self._pressed_logical_index, False)
        self._pressed_logical_index = -1
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._stop_drag_scroll()
        super().leaveEvent(event)

    def _update_drag_scroll_state(self, x_pos: int) -> None:
        viewport_width = self.viewport().width()
        if x_pos <= self._drag_scroll_margin:
            self._drag_scroll_direction = -1
        elif x_pos >= viewport_width - self._drag_scroll_margin:
            self._drag_scroll_direction = 1
        else:
            self._drag_scroll_direction = 0

        if self._drag_scroll_direction == 0:
            self._drag_scroll_timer.stop()
        elif not self._drag_scroll_timer.isActive():
            self._drag_scroll_timer.start()

    def _perform_drag_scroll(self) -> None:
        table_view = self.parent()
        if not isinstance(table_view, QTableView):
            self._stop_drag_scroll()
            return
        scrollbar = table_view.horizontalScrollBar()
        if scrollbar.maximum() <= scrollbar.minimum():
            self._stop_drag_scroll()
            return
        step = max(scrollbar.singleStep(), 28)
        scrollbar.setValue(scrollbar.value() + (self._drag_scroll_direction * step))

    def _stop_drag_scroll(self) -> None:
        self._drag_scroll_direction = 0
        self._drag_scroll_timer.stop()

    def set_highlighted_sections(self, sections: set[int] | list[int] | tuple[int, ...]) -> None:
        normalized = {int(section) for section in sections}
        if normalized == self._highlighted_sections:
            return
        self._highlighted_sections = normalized
        self.viewport().update()

    def highlighted_sections(self) -> set[int]:
        return set(self._highlighted_sections)

    def paintSection(self, painter: QPainter, rect, logicalIndex: int) -> None:  # type: ignore[override]
        if logicalIndex in self._highlighted_sections:
            painter.save()
            painter.fillRect(rect, QColor("#17212b"))
            painter.setPen(QColor("#ffffff"))
            text = str(self.model().headerData(logicalIndex, self.orientation(), Qt.DisplayRole) or "")
            sort_arrow = ""
            if self.isSortIndicatorShown() and self.sortIndicatorSection() == logicalIndex:
                sort_arrow = "▲" if self.sortIndicatorOrder() == Qt.AscendingOrder else "▼"
            text_rect = rect.adjusted(8, 0, -8, 0)
            if sort_arrow:
                text_rect = rect.adjusted(8, 0, -22, 0)
                painter.drawText(rect.adjusted(rect.width() - 18, 0, -6, 0), Qt.AlignVCenter | Qt.AlignRight, sort_arrow)
            painter.drawText(text_rect, Qt.AlignCenter, text)
            painter.setPen(QColor("#d7ccb8"))
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            painter.drawLine(rect.topRight(), rect.bottomRight())
            painter.restore()
            return
        super().paintSection(painter, rect, logicalIndex)


class RowHandleHeaderView(QHeaderView):
    rowMoveRequested = Signal(int, int)

    def __init__(self, parent: QTableView | None = None) -> None:
        super().__init__(Qt.Vertical, parent)
        self._pressed_logical_index = -1
        self._pressed_pos = QPoint()
        self._drag_active = False
        self.setDefaultAlignment(Qt.AlignCenter)
        self.setSectionsClickable(True)
        self.setHighlightSections(True)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        position = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if event.button() == Qt.LeftButton:
            self._pressed_logical_index = self.logicalIndexAt(position)
            self._pressed_pos = position
            self._drag_active = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._pressed_logical_index >= 0 and event.buttons() & Qt.LeftButton:
            position = event.position().toPoint() if hasattr(event, "position") else event.pos()
            if (position - self._pressed_pos).manhattanLength() >= QApplication.startDragDistance():
                self._drag_active = True
                self.viewport().setCursor(Qt.SizeAllCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_active and self._pressed_logical_index >= 0:
            position = event.position().toPoint() if hasattr(event, "position") else event.pos()
            target_index = self.logicalIndexAt(position)
            if target_index >= 0 and target_index != self._pressed_logical_index:
                self.rowMoveRequested.emit(self._pressed_logical_index, target_index)
        self._pressed_logical_index = -1
        self._drag_active = False
        self.viewport().unsetCursor()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.viewport().unsetCursor()
        super().leaveEvent(event)


class SpreadsheetTableView(QTableView):
    copyRequested = Signal()
    pasteRequested = Signal()
    cutRequested = Signal()
    clearRequested = Signal()
    fillRequested = Signal(QModelIndex)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fill_drag_active = False
        self._fill_target_index = QModelIndex()
        self._fill_handle_size = 8
        self.setMouseTracking(True)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.Copy):
            self.copyRequested.emit()
            event.accept()
            return
        if event.matches(QKeySequence.Paste):
            self.pasteRequested.emit()
            event.accept()
            return
        if event.matches(QKeySequence.Cut):
            self.cutRequested.emit()
            event.accept()
            return
        if event.key() in {Qt.Key_Delete, Qt.Key_Backspace}:
            self.clearRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        position = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if event.button() == Qt.LeftButton and self._fill_handle_rect().contains(position):
            self._fill_drag_active = True
            self._fill_target_index = QModelIndex()
            self.viewport().update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        position = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if self._fill_drag_active:
            target_index = self.indexAt(position)
            if target_index != self._fill_target_index:
                self._fill_target_index = target_index
                self.viewport().update()
            event.accept()
            return
        if self._fill_handle_rect().contains(position):
            self.viewport().setCursor(Qt.CrossCursor)
        else:
            self.viewport().unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._fill_drag_active:
            position = event.position().toPoint() if hasattr(event, "position") else event.pos()
            target_index = self._fill_target_index if self._fill_target_index.isValid() else self.indexAt(position)
            self._fill_drag_active = False
            self._fill_target_index = QModelIndex()
            self.viewport().unsetCursor()
            self.viewport().update()
            if target_index.isValid():
                self.fillRequested.emit(target_index)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if not self._fill_drag_active:
            self.viewport().unsetCursor()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        selection_rect = self._selection_visual_rect()
        handle_rect = self._fill_handle_rect()
        if selection_rect.isValid() and not selection_rect.isNull():
            painter.save()
            painter.setPen(QPen(QColor("#17212b"), 1, Qt.DashLine if self._fill_drag_active else Qt.SolidLine))
            painter.drawRect(selection_rect.adjusted(0, 0, -1, -1))
            if handle_rect.isValid() and not handle_rect.isNull():
                painter.fillRect(handle_rect, QColor("#17212b"))
                painter.setPen(QColor("#ffffff"))
                painter.drawRect(handle_rect.adjusted(0, 0, -1, -1))
            if self._fill_drag_active and self._fill_target_index.isValid():
                preview_rect = selection_rect.united(self.visualRect(self._fill_target_index))
                painter.setPen(QPen(QColor("#8c5a00"), 1, Qt.DashLine))
                painter.drawRect(preview_rect.adjusted(0, 0, -1, -1))
            painter.restore()

    def _selection_visual_rect(self) -> QRect:
        selection_model = self.selectionModel()
        rect = QRect()
        has_rect = False
        indexes = selection_model.selectedIndexes() if selection_model is not None else []
        if not indexes:
            current = self.currentIndex()
            indexes = [current] if current.isValid() else []
        for index in indexes:
            if not index.isValid():
                continue
            visual_rect = self.visualRect(index)
            if not visual_rect.isValid() or visual_rect.isNull() or visual_rect.isEmpty():
                continue
            rect = visual_rect if not has_rect else rect.united(visual_rect)
            has_rect = True
        return rect if has_rect else QRect()

    def _fill_handle_rect(self) -> QRect:
        rect = self._selection_visual_rect()
        if not rect.isValid() or rect.isNull() or rect.isEmpty():
            return QRect()
        size = self._fill_handle_size
        return QRect(rect.right() - size + 1, rect.bottom() - size + 1, size, size)


class IncrementCopyDialog(QDialog):
    def __init__(self, rules_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("연속 값 복사")
        layout = QVBoxLayout(self)

        description = QLabel("선택한 행을 복사하면서, 프로파일 변수에 지정된 연속 값 규칙을 적용합니다.")
        description.setWordWrap(True)
        layout.addWidget(description)

        rules_label = QLabel(rules_text)
        rules_label.setWordWrap(True)
        layout.addWidget(rules_label)

        form = QFormLayout()
        self.copy_count_spin = QSpinBox()
        self.copy_count_spin.setRange(1, 50)
        self.copy_count_spin.setValue(1)
        form.addRow("복사 개수", self.copy_count_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> int:
        return self.copy_count_spin.value()


class InAppTutorialDialog(QDialog):
    def __init__(self, host: "DesktopWindow") -> None:
        super().__init__(host)
        self.host = host
        self._current_step = 0
        self._current_action: Callable[[], None] | None = None
        self._steps: list[Callable[[], dict[str, Any]]] = [
            self._step_intro,
            self._step_template_authoring,
            self._step_prepare_device_file,
            self._step_fill_first_device,
            self._step_review_cli,
            self._step_copy_cli,
            self._step_finish,
        ]

        self.setWindowTitle("튜토리얼")
        self.setModal(False)
        self.resize(470, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("GuideMeta")
        layout.addWidget(self.progress_label)

        self.title_label = QLabel("")
        self.title_label.setWordWrap(True)
        self.title_label.setObjectName("GuideTitle")
        layout.addWidget(self.title_label)

        self.body_label = QLabel("")
        self.body_label.setWordWrap(True)
        self.body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body_label.setObjectName("GuideCard")
        layout.addWidget(self.body_label, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("GuideMeta")
        layout.addWidget(self.status_label)

        hint_label = QLabel("이 창은 켜 둔 채로, 메인 화면과 프로파일 편집기를 직접 만지면서 단계별로 따라오면 됩니다.")
        hint_label.setWordWrap(True)
        hint_label.setObjectName("GuideHint")
        layout.addWidget(hint_label)

        buttons = QDialogButtonBox(self)
        self.previous_button = buttons.addButton("이전", QDialogButtonBox.ActionRole)
        self.action_button = buttons.addButton("실습 열기", QDialogButtonBox.ActionRole)
        self.next_button = buttons.addButton("다음", QDialogButtonBox.ActionRole)
        self.close_button = buttons.addButton("닫기", QDialogButtonBox.RejectRole)
        self.previous_button.clicked.connect(self.go_previous)
        self.action_button.clicked.connect(self.run_step_action)
        self.next_button.clicked.connect(self.go_next)
        self.close_button.clicked.connect(self.close)
        layout.addWidget(buttons)

        self._apply_step(0)

    def _apply_step(self, index: int) -> None:
        self._current_step = index
        step = self._steps[index]()
        title = str(step.get("title", "")).strip()
        body = str(step.get("body", "")).strip()
        complete = bool(step.get("complete", True))
        action = step.get("action")
        self._current_action = action if callable(action) else None
        action_text = str(step.get("action_text", "")).strip()
        status_text = str(step.get("status_text", "")).strip()
        self.progress_label.setText(f"{index + 1} / {len(self._steps)}")
        self.title_label.setText(title)
        self.body_label.setText(body)
        self.status_label.setText(status_text)
        self.previous_button.setEnabled(index > 0)
        self.action_button.setVisible(bool(action_text and self._current_action))
        if self.action_button.isVisible():
            self.action_button.setText(action_text)
        self.next_button.setText("완료" if index == len(self._steps) - 1 else "다음")
        self.next_button.setEnabled(index == len(self._steps) - 1 or complete)
        QApplication.processEvents()

    def refresh_current_step(self) -> None:
        self._apply_step(self._current_step)

    def go_previous(self) -> None:
        if self._current_step <= 0:
            return
        self._apply_step(self._current_step - 1)

    def go_next(self) -> None:
        if self._current_step < len(self._steps) - 1 and not self.next_button.isEnabled():
            return
        if self._current_step >= len(self._steps) - 1:
            self.close()
            return
        self._apply_step(self._current_step + 1)

    def run_step_action(self) -> None:
        if self._current_action is None:
            return
        self._current_action()
        self.refresh_current_step()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.host.on_tutorial_dialog_closed(self)
        super().closeEvent(event)

    def _step_intro(self) -> dict[str, Any]:
        profile = self.host.tutorial_profile()
        status = f"현재 튜토리얼 프로파일: {profile.id}" if profile is not None else "현재 튜토리얼 프로파일: 아직 저장되지 않음"
        return {
            "title": "실습 흐름 소개",
            "body": (
                "이번 튜토리얼은 화면만 보는 방식이 아니라, 처음부터 직접 만들어 보는 실습입니다.\n\n"
                "순서는 1) 프로파일 저장 2) 실습용 장비 파일 만들기 3) 첫 장비 값 입력 4) CLI 확인 5) CLI 복사입니다.\n"
                "중간에 막히면 다시 이 창으로 돌아와 다음 안내를 확인하면 됩니다."
            ),
            "complete": True,
            "status_text": status,
        }

    def _step_template_authoring(self) -> dict[str, Any]:
        profile = self.host.tutorial_profile()
        complete = self.host.tutorial_profile_ready()
        if profile is None:
            status = "아직 저장된 튜토리얼 프로파일이 없습니다."
        else:
            status = f"저장됨: {profile.id} / 변수 {len(profile.variables)}개 / 블록 {len(profile.blocks)}개"
        return {
            "title": "1. 프로파일 작성",
            "body": (
                "버튼을 누르면 튜토리얼용 프로파일 편집기가 열립니다. 기본 예제가 채워진 상태로 열리니, "
                "변수와 명령 블록이 어떻게 연결되는지 확인한 뒤 그대로 저장하거나 직접 조금 수정해 보세요.\n\n"
                "권장 구성은 `hostname`, `mgmt_ip`, `mgmt_mask` 변수와 `base` 명령 블록 1개입니다."
            ),
            "action_text": "프로파일 작성 열기" if profile is None else "프로파일 다시 열기",
            "action": self.host.open_tutorial_profile_dialog,
            "complete": complete,
            "status_text": status,
        }

    def _step_prepare_device_file(self) -> dict[str, Any]:
        loaded = self.host.tutorial_workspace_loaded()
        current_name = self.host.current_file_path.name if self.host.current_file_path else "-"
        status = f"현재 파일: {current_name}" if loaded else f"생성될 파일: {TUTORIAL_WORKSPACE_PATH.name}"
        return {
            "title": "2. 장비 파일 만들기",
            "body": (
                "이 단계에서는 방금 저장한 프로파일로 실습용 장비 파일을 엽니다.\n\n"
                "버튼을 누르면 튜토리얼 작업 폴더에 CSV가 준비되고, 첫 번째 빈 행이 자동으로 선택됩니다. "
                "이미 파일이 있다면 이어서 다시 열어 줍니다."
            ),
            "action_text": "장비 파일 만들기" if not loaded else "장비 파일 다시 열기",
            "action": self.host.prepare_tutorial_device_file,
            "complete": loaded,
            "status_text": status,
        }

    def _step_fill_first_device(self) -> dict[str, Any]:
        return {
            "title": "3. 첫 장비 값 입력",
            "body": (
                "메인 표의 첫 행에 아래 값을 직접 입력해 보세요.\n\n"
                f"`device_id`: {TUTORIAL_DEVICE_ID}\n"
                f"`hostname`: {TUTORIAL_HOSTNAME}\n"
                f"`mgmt_ip`: {TUTORIAL_MGMT_IP}\n\n"
                f"프로파일 ID(profile_id)는 자동으로 채워지고, `mgmt_mask`는 비워 두면 기본값 `{TUTORIAL_MGMT_MASK}`가 사용됩니다."
            ),
            "action_text": "첫 행으로 이동",
            "action": lambda: self.host.focus_tutorial_workspace_row(detail_tab=0, column_name="device_id"),
            "complete": self.host.tutorial_first_row_complete(),
            "status_text": self.host.tutorial_first_row_status_text(),
        }

    def _step_review_cli(self) -> dict[str, Any]:
        return {
            "title": "4. CLI 확인",
            "body": (
                "입력을 마쳤다면 오른쪽 `CLI` 탭으로 가서 실제 생성 결과를 확인해 보세요.\n\n"
                "CLI가 보이지 않으면 `이슈` 탭에서 부족한 값이나 오류를 먼저 확인하면 됩니다."
            ),
            "action_text": "CLI 탭으로 이동",
            "action": lambda: self.host.focus_tutorial_workspace_row(detail_tab=3, column_name="hostname"),
            "complete": self.host.tutorial_cli_ready(),
            "status_text": self.host.tutorial_cli_status_text(),
        }

    def _step_copy_cli(self) -> dict[str, Any]:
        return {
            "title": "5. CLI 복사",
            "body": (
                "이제 메인 화면 오른쪽 위 `CLI` 영역의 `복사` 버튼을 직접 눌러 보세요.\n\n"
                "복사가 성공하면 선택 행의 작업 상태가 `복사 완료`로 바뀌고, 이 단계가 자동으로 완료됩니다."
            ),
            "action_text": "CLI 행 다시 보기",
            "action": lambda: self.host.focus_tutorial_workspace_row(detail_tab=3, column_name="hostname"),
            "complete": self.host.tutorial_cli_copied(),
            "status_text": self.host.tutorial_copy_status_text(),
        }

    def _step_finish(self) -> dict[str, Any]:
        return {
            "title": "튜토리얼 완료",
            "body": (
                "여기까지 하면 프로파일 작성부터 첫 장비 CLI 복사까지 한 번 끝낸 것입니다.\n\n"
                "이제 같은 파일에서 `행 추가`, `선택 행 복사`, `연속 값 복사`를 눌러 장비를 늘려 보고, "
                "`이슈` 탭으로 오류를 확인하는 연습까지 이어서 해보면 됩니다."
            ),
            "complete": True,
            "status_text": "현재 상태 그대로 계속 실습할 수 있습니다.",
        }


def is_secret_header(header_name: str) -> bool:
    normalized = str(header_name).strip().casefold()
    return any(keyword in normalized for keyword in SECRET_HEADER_KEYWORDS)


def mask_secret_value(value: Any) -> str:
    text = _display_value(value)
    return "••••••" if text else ""


def is_ip_like_header(header_name: str) -> bool:
    normalized = str(header_name).strip().casefold()
    return normalized.endswith("_ip") or any(keyword in normalized for keyword in IP_LIKE_HEADER_KEYWORDS)


def increment_identifier_value(value: str, offset: int) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    match = TRAILING_NUMBER_PATTERN.match(text)
    if match:
        prefix, digits = match.groups()
        return f"{prefix}{str(int(digits) + offset).zfill(len(digits))}"
    return f"{text}-{offset + 1}"


def increment_ipv4_value(value: str, offset: int) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    try:
        return str(ipaddress.IPv4Address(text) + offset)
    except ipaddress.AddressValueError:
        return text


def increment_spreadsheet_value(value: str, offset: int) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    if re.fullmatch(r"-?\d+", text):
        return str(int(text) + offset)
    if TRAILING_NUMBER_PATTERN.match(text):
        return increment_identifier_value(text, offset)
    incremented_ip = increment_ipv4_value(text, offset)
    if incremented_ip != text:
        return incremented_ip
    return text


def build_incremented_row(
    row: dict[str, str],
    offset: int,
    profile: Profile | None = None,
) -> dict[str, str]:
    updated = dict(row)
    if profile is None:
        return updated
    for variable_name, variable in profile.variables.items():
        if variable_name not in row:
            continue
        rule = str(getattr(variable, "auto_increment", AUTO_INCREMENT_NONE) or AUTO_INCREMENT_NONE).strip()
        if rule == AUTO_INCREMENT_SUFFIX_NUMBER:
            updated[variable_name] = increment_identifier_value(row.get(variable_name, ""), offset)
        elif rule == AUTO_INCREMENT_IPV4:
            updated[variable_name] = increment_ipv4_value(row.get(variable_name, ""), offset)
    return updated


def row_display_name(row: dict[str, Any], row_number: int) -> str:
    for key in ("device_id", "hostname", "name"):
        value = str(row.get(key, "")).strip()
        if value:
            return value
    return f"row-{row_number}"


def profile_increment_fields(profile: Profile | None) -> list[str]:
    if profile is None:
        return []
    fields: list[str] = []
    for variable_name, variable in profile.variables.items():
        rule = str(getattr(variable, "auto_increment", AUTO_INCREMENT_NONE) or AUTO_INCREMENT_NONE).strip()
        if rule != AUTO_INCREMENT_NONE:
            fields.append(f"{variable_name} ({AUTO_INCREMENT_LABELS.get(rule, rule)})")
    return fields


def build_table_consistency_issues(
    rows: list[dict[str, str]],
    headers: list[str],
) -> dict[int, list[ValidationIssue]]:
    issues_by_row: dict[int, list[ValidationIssue]] = {row_index: [] for row_index in range(len(rows))}

    def add_issue(row_index: int, message: str) -> None:
        issues_by_row.setdefault(row_index, []).append(
            ValidationIssue(
                level="error",
                scope="device",
                message=message,
                device_id=row_display_name(rows[row_index], row_index + 2),
                profile_id=str(rows[row_index].get("profile_id", "")).strip(),
                row_number=row_index + 2,
            )
        )

    def find_duplicates(header_name: str, message_prefix: str, *, validator: Callable[[str], Any] | None = None) -> None:
        duplicates: dict[str, list[tuple[int, str]]] = {}
        for row_index, row in enumerate(rows):
            raw_value = str(row.get(header_name, "")).strip()
            if not raw_value:
                continue
            if validator is not None:
                try:
                    if not validator(raw_value):
                        continue
                except Exception:
                    continue
            duplicates.setdefault(raw_value.casefold(), []).append((row_index, raw_value))
        for values in duplicates.values():
            if len(values) < 2:
                continue
            display = values[0][1]
            for row_index, _ in values:
                add_issue(row_index, f"{message_prefix} 값이 중복되었습니다 ({header_name}): {display}")

    if "hostname" in headers:
        find_duplicates("hostname", "hostname")
    for header in headers:
        if is_ip_like_header(header):
            find_duplicates(header, "IP", validator=lambda value: ipaddress.ip_address(value))

    gateway_header = next((header for header in ("mgmt_gw", "default_gateway") if header in headers), "")
    ip_header = next((header for header in ("mgmt_ip", "ip_address") if header in headers), "")
    mask_header = next((header for header in ("mgmt_mask", "subnet") if header in headers), "")
    for row_index, row in enumerate(rows):
        if not (gateway_header and ip_header and mask_header):
            continue
        gateway_value = str(row.get(gateway_header, "")).strip()
        ip_value = str(row.get(ip_header, "")).strip()
        mask_value = str(row.get(mask_header, "")).strip()
        if not (gateway_value and ip_value and mask_value):
            continue
        try:
            ip_address = ipaddress.IPv4Address(ip_value)
            gateway = ipaddress.IPv4Address(gateway_value)
            network = ipaddress.IPv4Network((ip_value, mask_value), strict=False)
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError):
            continue
        if gateway == ip_address:
            add_issue(row_index, f"{gateway_header} 값이 {ip_header}와 같습니다.")
        elif gateway not in network:
            add_issue(row_index, f"{gateway_header} 값이 {ip_header}/{mask_header} 대역과 맞지 않습니다.")

    return {row_index: issues for row_index, issues in issues_by_row.items() if issues}


def default_pinned_headers(headers: list[str]) -> list[str]:
    return []


def _normalize_profile_id(profile_id: str) -> str:
    return str(profile_id).strip().casefold()


def _find_profile(profiles: dict[str, Profile], profile_id: str) -> Profile | None:
    normalized = _normalize_profile_id(profile_id)
    if not normalized:
        return None
    for key, profile in profiles.items():
        if _normalize_profile_id(key) == normalized or _normalize_profile_id(profile.id) == normalized:
            return profile
    return None


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def expand_headers_for_referenced_profiles(headers: list[str], rows: list[dict[str, str]], profiles: dict[str, Profile]) -> list[str]:
    expanded = [header for header in headers if header]
    seen = set(expanded)
    if "profile_id" not in seen:
        expanded.append("profile_id")
        seen.add("profile_id")
    for row in rows:
        profile = _find_profile(profiles, row.get("profile_id", ""))
        if not profile:
            continue
        for variable_name in profile.variables:
            if variable_name not in seen:
                expanded.append(variable_name)
                seen.add(variable_name)
    return expanded


def build_profile_reference_rows(profile: Profile, row_values: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for variable_name, variable in profile.variables.items():
        current_value = str(row_values.get(variable_name, "") or "").strip()
        default_value = mask_secret_value(variable.default) if is_secret_header(variable_name) else _display_value(variable.default)
        current_display = current_value or ("(비움 -> 기본값 사용)" if default_value else "")
        if current_value and is_secret_header(variable_name):
            current_display = mask_secret_value(current_value)
        description = variable.description_ko or variable.description
        auto_increment = str(getattr(variable, "auto_increment", AUTO_INCREMENT_NONE) or AUTO_INCREMENT_NONE).strip()
        if auto_increment != AUTO_INCREMENT_NONE:
            rule_label = AUTO_INCREMENT_LABELS.get(auto_increment, auto_increment)
            description = f"{description} / 연속 값 복사: {rule_label}" if description else f"연속 값 복사: {rule_label}"
        rows.append(
            {
                "name": variable_name,
                "current": current_display,
                "type": variable.type,
                "required": "필수" if variable.required else "",
                "default": default_value,
                "description": description,
                "missing_required": "1" if variable.required and not current_value and not default_value else "",
            }
        )
    return rows


class DeviceTableModel(QAbstractTableModel):
    aboutToChange = Signal()
    tableChanged = Signal()

    def __init__(
        self,
        profile_provider: Callable[[], dict[str, Profile]] | None = None,
        row_state_provider: Callable[[int], str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile_provider = profile_provider or (lambda: {})
        self._row_state_provider = row_state_provider or (lambda _row_index: "")
        self._headers: list[str] = []
        self._rows: list[dict[str, str]] = []
        self._issues_by_row: dict[int, list[ValidationIssue]] = {}

    @property
    def headers(self) -> list[str]:
        return list(self._headers)

    @property
    def rows(self) -> list[dict[str, str]]:
        return [{key: value for key, value in row.items() if key != INTERNAL_ROW_UID} for row in self._rows]

    def stored_rows(self) -> list[dict[str, str]]:
        return [dict(row) for row in self._rows]

    def row_uid(self, row_index: int) -> str:
        if 0 <= row_index < len(self._rows):
            return str(self._rows[row_index].get(INTERNAL_ROW_UID, ""))
        return ""

    def row_uids(self) -> list[str]:
        return [self.row_uid(row_index) for row_index in range(len(self._rows))]

    def _normalize_stored_row(self, row: dict[str, str]) -> dict[str, str]:
        stored = dict(row)
        stored.setdefault(INTERNAL_ROW_UID, uuid4().hex)
        return stored

    def set_table(self, table: DeviceTable) -> None:
        self.aboutToChange.emit()
        self.beginResetModel()
        self._headers = list(table.headers)
        self._rows = [self._normalize_stored_row(dict(row)) for row in table.rows]
        self._issues_by_row = {}
        self.endResetModel()
        self.tableChanged.emit()

    def set_issue_map(self, issues_by_row: dict[int, list[ValidationIssue]]) -> None:
        self._issues_by_row = {index: list(issues) for index, issues in issues_by_row.items()}
        if self.rowCount() and self.columnCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1), [Qt.BackgroundRole])

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._headers)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        header = self._headers[index.column()]
        value = row.get(header, "")
        is_applicable = self._is_header_applicable(index.row(), header)
        if role == Qt.DisplayRole:
            normalized_value = str(value).strip()
            if not is_applicable and normalized_value.casefold() in {"", "-", "n/a", "na"}:
                return "해당 없음"
            return mask_secret_value(value) if is_secret_header(header) else value
        if role == Qt.EditRole:
            return value
        if role == Qt.ToolTipRole and not is_applicable:
            return "현재 프로파일에서는 사용하지 않는 컬럼입니다."
        if role == Qt.ForegroundRole and not is_applicable:
            return QBrush(QColor("#7c838c"))
        if role == Qt.FontRole and not is_applicable:
            font = QFont()
            font.setItalic(True)
            return font
        if role == Qt.BackgroundRole:
            issues = self._issues_by_row.get(index.row(), [])
            if any(issue.level == "error" for issue in issues):
                return ERROR_BG
            if any(issue.level == "warning" for issue in issues):
                return WARNING_BG
            if not is_applicable:
                return QColor("#f1f3f5")
            row_state = str(self._row_state_provider(index.row()) or "").strip()
            if row_state == ROW_STATE_DONE:
                return DONE_BG
            if row_state == ROW_STATE_COPIED:
                return COPIED_BG
        return None

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:
        if role != Qt.EditRole or not index.isValid():
            return False
        if not self._is_header_applicable(index.row(), self._headers[index.column()]):
            return False
        self.aboutToChange.emit()
        self._rows[index.row()][self._headers[index.column()]] = "" if value is None else str(value).strip()
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        self.tableChanged.emit()
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemIsEnabled
        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if self._is_header_applicable(index.row(), self._headers[index.column()]):
            flags |= Qt.ItemIsEditable
        return flags

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:
        if role != Qt.DisplayRole:
            return None
        return self._headers[section] if orientation == Qt.Horizontal else str(section + 1)

    def ensure_headers(self, headers: list[str]) -> None:
        normalized = [header for header in headers if header]
        if normalized == self._headers:
            return
        self.aboutToChange.emit()
        self.beginResetModel()
        for row in self._rows:
            for header in normalized:
                row.setdefault(header, "")
        self._headers = normalized
        self.endResetModel()
        self.tableChanged.emit()

    def append_row(self, row: dict[str, str]) -> None:
        position = len(self._rows)
        self.aboutToChange.emit()
        self.beginInsertRows(QModelIndex(), position, position)
        self._rows.append(self._normalize_stored_row(dict(row)))
        self.endInsertRows()
        self.tableChanged.emit()

    def append_rows(self, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        start = len(self._rows)
        end = start + len(rows) - 1
        self.aboutToChange.emit()
        self.beginInsertRows(QModelIndex(), start, end)
        self._rows.extend(self._normalize_stored_row(dict(row)) for row in rows)
        self.endInsertRows()
        self.tableChanged.emit()

    def duplicate_rows(self, row_indexes: list[int]) -> None:
        indexes = sorted(set(row_indexes))
        if not indexes:
            return
        self.aboutToChange.emit()
        new_rows = [dict(row) for row in self._rows]
        offset = 0
        for row_index in indexes:
            copied = self._normalize_stored_row({key: value for key, value in self._rows[row_index].items() if key != INTERNAL_ROW_UID})
            new_rows.insert(row_index + 1 + offset, copied)
            offset += 1
        self.beginResetModel()
        self._rows = new_rows
        self.endResetModel()
        self.tableChanged.emit()

    def remove_rows(self, row_indexes: list[int]) -> None:
        if not row_indexes:
            return
        self.aboutToChange.emit()
        for row_index in sorted(set(row_indexes), reverse=True):
            self.beginRemoveRows(QModelIndex(), row_index, row_index)
            self._rows.pop(row_index)
            self.endRemoveRows()
        self.tableChanged.emit()

    def move_row(self, source_row: int, target_row: int) -> int | None:
        if source_row < 0 or target_row < 0:
            return None
        if source_row >= len(self._rows) or target_row >= len(self._rows):
            return None
        if source_row == target_row:
            return source_row
        self.aboutToChange.emit()
        self.beginResetModel()
        moved_row = self._rows.pop(source_row)
        if target_row > source_row:
            target_row -= 1
        target_row = max(0, min(target_row, len(self._rows)))
        self._rows.insert(target_row, moved_row)
        self.endResetModel()
        self.tableChanged.emit()
        return target_row

    def remove_headers(self, headers: list[str]) -> None:
        removable = [header for header in headers if header in self._headers]
        if not removable:
            return
        removable_set = set(removable)
        self.aboutToChange.emit()
        self.beginResetModel()
        self._headers = [header for header in self._headers if header not in removable_set]
        for row in self._rows:
            for header in removable:
                row.pop(header, None)
        self._issues_by_row = {}
        self.endResetModel()
        self.tableChanged.emit()

    def apply_cell_changes(self, changes: list[tuple[int, int, Any]]) -> int:
        normalized_changes: list[tuple[int, int, str]] = []
        seen: set[tuple[int, int]] = set()
        for row_index, column_index, value in changes:
            if (row_index, column_index) in seen:
                continue
            if not (0 <= row_index < len(self._rows) and 0 <= column_index < len(self._headers)):
                continue
            header = self._headers[column_index]
            if not self._is_header_applicable(row_index, header):
                continue
            new_value = "" if value is None else str(value).strip()
            if str(self._rows[row_index].get(header, "")) == new_value:
                continue
            normalized_changes.append((row_index, column_index, new_value))
            seen.add((row_index, column_index))
        if not normalized_changes:
            return 0
        self.aboutToChange.emit()
        min_row = min(row_index for row_index, _, _ in normalized_changes)
        max_row = max(row_index for row_index, _, _ in normalized_changes)
        min_col = min(column_index for _, column_index, _ in normalized_changes)
        max_col = max(column_index for _, column_index, _ in normalized_changes)
        for row_index, column_index, new_value in normalized_changes:
            self._rows[row_index][self._headers[column_index]] = new_value
        self.dataChanged.emit(
            self.index(min_row, min_col),
            self.index(max_row, max_col),
            [Qt.DisplayRole, Qt.EditRole, Qt.BackgroundRole, Qt.ToolTipRole, Qt.ForegroundRole, Qt.FontRole],
        )
        self.tableChanged.emit()
        return len(normalized_changes)

    def _is_header_applicable(self, row_index: int, header: str) -> bool:
        if header in {"", "profile_id"}:
            return True
        profiles = self._profile_provider() or {}
        if not profiles:
            return True
        variable_headers = {name for profile in profiles.values() for name in profile.variables}
        if header not in variable_headers:
            return True
        if not (0 <= row_index < len(self._rows)):
            return True
        profile = _find_profile(profiles, self._rows[row_index].get("profile_id", ""))
        if profile is None:
            return True
        return header in profile.variables


class DeviceCellDelegate(QStyledItemDelegate):
    def __init__(self, profile_provider, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.profile_provider = profile_provider

    def createEditor(self, parent, option, index):
        header = self._header_name(index)
        if header == "profile_id":
            editor = QComboBox(parent)
            editor.addItem("")
            editor.addItems(sorted(self.profile_provider()))
            editor.setEditable(False)
            editor.setMaxVisibleItems(12)
            editor.setStyleSheet(
                "QComboBox { padding: 2px 6px; background: #ffffff; }"
                "QComboBox QAbstractItemView { selection-background-color: #17212b; selection-color: #ffffff; }"
            )
            return editor
        variable = self._variable_spec_for_index(index)
        if variable and variable.type == "bool":
            editor = QComboBox(parent)
            editor.addItems(["", "true", "false"])
            editor.setEditable(False)
            editor.setMaxVisibleItems(3)
            editor.setStyleSheet(
                "QComboBox { padding: 2px 6px; background: #ffffff; }"
                "QComboBox QAbstractItemView { selection-background-color: #17212b; selection-color: #ffffff; }"
            )
            return editor
        if is_secret_header(header):
            editor = QLineEdit(parent)
            editor.setEchoMode(QLineEdit.Password)
            return editor
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index) -> None:
        value = str(index.model().data(index, Qt.EditRole) or "")
        if isinstance(editor, QComboBox):
            editor.setCurrentText(value)
            return
        if isinstance(editor, QLineEdit):
            editor.setText(value)
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index) -> None:
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.EditRole)
            return
        if isinstance(editor, QLineEdit):
            model.setData(index, editor.text(), Qt.EditRole)
            return
        super().setModelData(editor, model, index)

    def _header_name(self, index: QModelIndex) -> str:
        model = index.model()
        source_model = model.sourceModel() if isinstance(model, QSortFilterProxyModel) else model
        if not isinstance(source_model, DeviceTableModel):
            return ""
        return source_model.headers[index.column()]

    def _variable_spec_for_index(self, index: QModelIndex):
        header = self._header_name(index)
        if header in {"", "profile_id"}:
            return None
        model = index.model()
        if isinstance(model, QSortFilterProxyModel):
            source_index = model.mapToSource(index)
            source_model = model.sourceModel()
        else:
            source_index = index
            source_model = model
        if not isinstance(source_model, DeviceTableModel):
            return None
        row = source_model.rows[source_index.row()]
        profile = _find_profile(self.profile_provider(), row.get("profile_id", ""))
        if not profile:
            return None
        return profile.variables.get(header)

    def updateEditorGeometry(self, editor, option, index) -> None:
        if isinstance(editor, QComboBox):
            rect = option.rect.adjusted(1, 1, -1, -1)
            editor.setGeometry(rect)
            return
        super().updateEditorGeometry(editor, option, index)


class DeviceFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, row_state_provider: Callable[[int], str] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._row_state_provider = row_state_provider or (lambda _row_index: "")
        self._filter_field = "전체"
        self._filter_text = ""

    def set_filter(self, field_name: str, filter_text: str) -> None:
        self._filter_field = str(field_name).strip() or "전체"
        self._filter_text = str(filter_text).strip().casefold()
        self.beginFilterChange()
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._filter_text:
            return True
        model = self.sourceModel()
        if not isinstance(model, DeviceTableModel):
            return True
        headers = model.headers
        if self._filter_field == "전체":
            for column, header in enumerate(headers):
                value = str(model.data(model.index(source_row, column), Qt.DisplayRole) or "").casefold()
                if self._filter_text in value or self._filter_text in header.casefold():
                    return True
            row_state = str(self._row_state_provider(source_row) or "").casefold()
            return self._filter_text in row_state
        if self._filter_field == "작업 상태":
            row_state = str(self._row_state_provider(source_row) or "").casefold()
            return self._filter_text in row_state
        if self._filter_field not in headers:
            return True
        column = headers.index(self._filter_field)
        value = str(model.data(model.index(source_row, column), Qt.DisplayRole) or "").casefold()
        return self._filter_text in value


class DesktopWindow(QMainWindow):
    def __init__(self, profiles_dir: str | Path | None = None) -> None:
        super().__init__()
        app = QApplication.instance()
        self.setWindowIcon(app.windowIcon() if app and not app.windowIcon().isNull() else build_app_icon())
        self.setWindowTitle("Switch Config Builder Desktop")
        self.resize(1520, 840)
        font = self.font()
        font.setPointSize(8)
        self.setFont(font)
        self.setStyleSheet(COMPACT_UI_STYLE)
        self.profile_dir = Path(profiles_dir) if profiles_dir else PROFILE_DIR
        self.profiles: dict[str, Profile] = {}
        self.profile_issues: list[ValidationIssue] = []
        self.current_file_path: Path | None = None
        self.current_rendered: dict[int, RenderedConfig] = {}
        self.current_row_issues: dict[int, list[ValidationIssue]] = {}
        self.row_work_state: dict[str, str] = {}
        self.disabled_blocks: dict[str, set[str]] = {}
        self.pinned_headers: list[str] = []
        self.visible_headers: list[str] = []
        self._visible_headers_initialized = False
        self.column_order: list[str] = []
        self._active_column_header: str = ""
        self._sort_column: int = -1
        self._sort_order = Qt.AscendingOrder
        self.file_column_state: dict[str, dict[str, list[str]]] = {}
        self.recent_files: list[str] = []
        self.is_dirty = False
        self._loading_table = False
        self._updating_pin_items = False
        self._syncing_width = False
        self._syncing_section_move = False
        self._history_blocked = False
        self._undo_stack: list[dict[str, Any]] = []
        self._redo_stack: list[dict[str, Any]] = []
        self._dismissed_obsolete_header_key: tuple[str, ...] | None = None
        self._restored_window_geometry = ""
        self._restored_splitter_sizes: list[int] = []
        self._restored_last_opened_file = ""
        self._restored_filter_field = "전체"
        self._restored_filter_value = ""
        self._restored_profile_id = ""
        self._restored_detail_tab_index = 0
        self._restored_selected_row: int | None = None
        self._restored_auto_save = True
        self.tutorial_dialog: InAppTutorialDialog | None = None
        self._load_app_state()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(220)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh_render_state)

        self.auto_save_timer = QTimer(self)
        self.auto_save_timer.setInterval(900)
        self.auto_save_timer.setSingleShot(True)
        self.auto_save_timer.timeout.connect(self.auto_save_current_file)

        self.resize_timer = QTimer(self)
        self.resize_timer.setInterval(0)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.auto_size_table_columns)

        self.table_model = DeviceTableModel(lambda: self.profiles, self._row_state_for_model, self)
        self.table_model.aboutToChange.connect(self._capture_history_snapshot)
        self.table_model.tableChanged.connect(self.on_table_changed)
        self.table_model.dataChanged.connect(self.schedule_auto_resize)
        self.table_model.modelReset.connect(self.schedule_auto_resize)
        self.table_model.rowsInserted.connect(self.schedule_auto_resize)
        self.table_model.rowsRemoved.connect(self.schedule_auto_resize)
        self.proxy_model = DeviceFilterProxyModel(self._row_state_label_for_model, self)
        self.proxy_model.setSourceModel(self.table_model)
        self.cell_delegate = DeviceCellDelegate(lambda: self.profiles, self)

        self._build_ui()
        self._apply_restored_window_state()
        self.reload_profiles()
        self._update_history_actions()
        self._set_empty_table()
        self._restore_last_session_state()

    def _row_state_for_model(self, row_index: int) -> str:
        return self.row_work_state.get(self.table_model.row_uid(row_index), "")

    def _row_state_label_for_model(self, row_index: int) -> str:
        return ROW_STATE_LABELS.get(self._row_state_for_model(row_index), ROW_STATE_LABELS[ROW_STATE_PENDING])

    def _selected_row_state(self) -> str:
        row_index = self._current_source_row()
        if row_index is None:
            return ROW_STATE_PENDING
        return self._row_state_for_model(row_index) or ROW_STATE_PENDING

    def _set_row_work_state(self, row_index: int, state: str) -> None:
        row_uid = self.table_model.row_uid(row_index)
        if not row_uid:
            return
        if state == ROW_STATE_PENDING:
            self.row_work_state.pop(row_uid, None)
        else:
            self.row_work_state[row_uid] = state

    def _set_selected_rows_work_state(self, state: str) -> None:
        row_indexes = self._selected_source_rows()
        if not row_indexes:
            return
        for row_index in row_indexes:
            self._set_row_work_state(row_index, state)
        self.table_model.set_issue_map(self.current_row_issues)
        self.apply_filter()
        self._refresh_tutorial_dialog()

    def _clear_missing_row_work_state(self) -> None:
        valid_uids = {uid for uid in self.table_model.row_uids() if uid}
        if not valid_uids:
            self.row_work_state = {}
            return
        self.row_work_state = {
            row_uid: state
            for row_uid, state in self.row_work_state.items()
            if row_uid in valid_uids and state in ROW_STATE_LABELS
        }

    def _build_ui(self) -> None:
        self._build_toolbar()
        central = QWidget(self)
        main = QVBoxLayout(central)
        main.setContentsMargins(10, 10, 10, 10)
        main.setSpacing(8)

        self.summary_label = QLabel("프로파일과 장비 파일을 열면 바로 CLI를 확인할 수 있습니다.")
        self.summary_label.setWordWrap(True)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self._build_template_group(), 2)
        controls.addWidget(self._build_block_toggle_group(), 5)
        controls.addWidget(self._build_filter_group(), 4)
        controls.addWidget(self._build_row_group(), 2)
        controls.addWidget(self._build_pin_group(), 2)
        controls.addWidget(self._build_file_group(), 1)
        main.addLayout(controls)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        self.right_panel = self._build_right_panel()
        splitter.addWidget(self.right_panel)
        splitter.setSizes([1180, 340])
        self.main_splitter = splitter
        main.addWidget(splitter, 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar(self))
        self.filter_field_combo.currentTextChanged.connect(self.apply_filter)
        self.filter_value_edit.textChanged.connect(self.apply_filter)
        self.add_profile_combo.currentTextChanged.connect(self.refresh_selected_preview)
        self.add_profile_combo.currentTextChanged.connect(self.refresh_block_toggle_panel)

    def _build_template_group(self) -> QGroupBox:
        group = QGroupBox("프로파일 작업")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 14, 10, 10)
        self.add_profile_combo = QComboBox()
        title_label = QLabel("기준 프로파일")
        reload_button = QPushButton("새로고침")
        reload_button.clicked.connect(self.reload_profiles)
        new_button = QPushButton("새 프로파일")
        new_button.clicked.connect(self.open_new_profile_dialog)
        clone_button = QPushButton("프로파일 복사")
        clone_button.clicked.connect(self.clone_current_profile_dialog)
        edit_button = QPushButton("프로파일 편집")
        edit_button.clicked.connect(self.edit_current_profile_dialog)
        delete_button = QPushButton("프로파일 삭제")
        delete_button.clicked.connect(self.delete_current_profile_dialog)

        actions_widget = QWidget()
        actions = QVBoxLayout(actions_widget)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        for button in (reload_button, new_button, clone_button, edit_button, delete_button):
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            actions.addWidget(button)

        layout.addWidget(title_label)
        layout.addWidget(self.add_profile_combo)
        layout.addWidget(actions_widget)
        layout.addStretch(1)
        return group

    def _build_block_toggle_group(self) -> QGroupBox:
        group = QGroupBox("명령 블록 선택")
        outer = QVBoxLayout(group)
        outer.setContentsMargins(6, 10, 6, 6)
        outer.setSpacing(0)
        self.block_toggle_container = QWidget()
        self.block_toggle_container_layout = QVBoxLayout(self.block_toggle_container)
        self.block_toggle_container_layout.setContentsMargins(0, 0, 0, 0)
        self.block_toggle_container_layout.setSpacing(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.block_toggle_container)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        return group

    def _build_filter_group(self) -> QGroupBox:
        group = QGroupBox("필터")
        layout = QFormLayout(group)
        self.filter_field_combo = QComboBox()
        self.filter_value_edit = QLineEdit()
        self.filter_value_edit.setPlaceholderText("예: A구역, DSW, 192.168.10")
        clear_button = QPushButton("필터 초기화")
        clear_button.clicked.connect(self.clear_filter)
        layout.addRow("필터 항목", self.filter_field_combo)
        layout.addRow("필터 값", self.filter_value_edit)
        layout.addRow("", clear_button)
        return group

    def _build_row_group(self) -> QGroupBox:
        group = QGroupBox("행 작업")
        layout = QVBoxLayout(group)
        self.add_row_button = QPushButton("행 추가")
        self.add_row_button.clicked.connect(self.add_row)
        self.duplicate_row_button = QPushButton("선택 행 복사")
        self.duplicate_row_button.clicked.connect(self.duplicate_selected_rows)
        self.increment_duplicate_row_button = QPushButton("연속 값 복사")
        self.increment_duplicate_row_button.clicked.connect(self.duplicate_selected_rows_with_increment)
        self.delete_row_button = QPushButton("선택 행 삭제")
        self.delete_row_button.clicked.connect(self.delete_selected_rows)
        layout.addWidget(self.add_row_button)
        layout.addWidget(self.duplicate_row_button)
        layout.addWidget(self.increment_duplicate_row_button)
        layout.addWidget(self.delete_row_button)
        layout.addStretch(1)
        return group

    def _build_pin_group(self) -> QGroupBox:
        group = QGroupBox("표시 컬럼")
        layout = QVBoxLayout(group)
        self.pinned_columns_list = AutoScrollListWidget()
        self.pinned_columns_list.setMaximumHeight(180)
        self.pinned_columns_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.pinned_columns_list.setDragEnabled(True)
        self.pinned_columns_list.setAcceptDrops(True)
        self.pinned_columns_list.setDropIndicatorShown(True)
        self.pinned_columns_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.pinned_columns_list.setDefaultDropAction(Qt.MoveAction)
        self.pinned_columns_list.itemChanged.connect(self.on_pinned_columns_changed)
        self.pinned_columns_list.model().rowsMoved.connect(self.on_pinned_columns_reordered)
        preset_row = QHBoxLayout()
        self.show_all_columns_button = QPushButton("모두 표시")
        self.show_all_columns_button.clicked.connect(self.show_all_columns)
        self.clear_all_columns_button = QPushButton("모두 해제")
        self.clear_all_columns_button.clicked.connect(self.clear_all_columns)
        preset_row.addWidget(self.show_all_columns_button)
        preset_row.addWidget(self.clear_all_columns_button)
        move_row = QHBoxLayout()
        self.column_move_up_button = QPushButton("위로")
        self.column_move_up_button.clicked.connect(lambda: self.move_selected_column_item(-1))
        self.column_move_down_button = QPushButton("아래로")
        self.column_move_down_button.clicked.connect(lambda: self.move_selected_column_item(1))
        self.delete_active_column_button = QPushButton("선택 컬럼 삭제")
        self.delete_active_column_button.setEnabled(False)
        self.delete_active_column_button.clicked.connect(self.delete_active_table_column)
        self.delete_active_column_button.setToolTip("컬럼 헤더와 저장된 값 자체를 삭제합니다. 숨기기와 다릅니다.")
        self.column_pin_button = QPushButton("고정")
        self.column_pin_button.clicked.connect(self.pin_selected_column)
        self.column_unpin_button = QPushButton("고정 해제")
        self.column_unpin_button.clicked.connect(self.unpin_selected_column)
        move_row.addWidget(self.column_move_up_button)
        move_row.addWidget(self.column_move_down_button)
        action_row = QHBoxLayout()
        action_row.addWidget(self.column_pin_button)
        action_row.addWidget(self.column_unpin_button)
        action_row.addWidget(self.delete_active_column_button)
        self.column_pin_summary_label = QLabel("")
        self.column_pin_summary_label.setWordWrap(True)
        layout.addWidget(self.pinned_columns_list)
        layout.addLayout(preset_row)
        layout.addLayout(move_row)
        layout.addLayout(action_row)
        layout.addWidget(self.column_pin_summary_label)
        return group

    def _build_file_group(self) -> QGroupBox:
        group = QGroupBox("파일 상태")
        group.setMinimumWidth(260)
        group.setMaximumWidth(360)
        group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        layout = QFormLayout(group)
        self.open_file_button = QPushButton("데이터 열기")
        self.open_file_button.clicked.connect(self.open_device_file_dialog)
        self.file_path_label = QLabel("-")
        self.file_path_label.setWordWrap(True)
        self.file_path_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.profile_summary_label = QLabel("-")
        self.profile_summary_label.setWordWrap(True)
        self.profile_summary_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.auto_save_check = QCheckBox("실시간 파일 저장")
        self.auto_save_check.setChecked(True)
        self.auto_save_check.toggled.connect(self.on_auto_save_toggled)
        self.allow_error_autosave_check = QCheckBox("오류 있어도 실시간 저장")
        self.allow_error_autosave_check.setChecked(self.allow_error_autosave)
        self.allow_error_autosave_check.toggled.connect(self.on_allow_error_autosave_toggled)
        self.allow_error_autosave_check.setEnabled(self.auto_save_check.isChecked())
        self.save_status_label = QLabel("-")
        self.save_status_label.setWordWrap(True)
        self.save_status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addRow("", self.open_file_button)
        layout.addRow("현재 파일", self.file_path_label)
        layout.addRow("프로파일 상태", self.profile_summary_label)
        layout.addRow("저장 방식", self.auto_save_check)
        layout.addRow("", self.allow_error_autosave_check)
        layout.addRow("저장 상태", self.save_status_label)
        return group

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.main_toolbar = toolbar
        self.addToolBar(toolbar)
        for text, handler in (
            ("저장", self.save_current_file),
            ("다른 이름 저장", self.save_current_file_as),
            ("실행 취소", self.undo_last_change),
            ("다시 실행", self.redo_last_change),
        ):
            action = QAction(text, self)
            action.triggered.connect(handler)
            toolbar.addAction(action)
            if text == "실행 취소":
                self.undo_action = action
            elif text == "다시 실행":
                self.redo_action = action

        spacer = QWidget(toolbar)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        self.tutorial_button = QPushButton("튜토리얼")
        self.tutorial_button.setToolTip("프로파일 작성부터 장비 값 입력, CLI 복사까지 직접 실습하는 튜토리얼을 시작합니다.")
        self.tutorial_button.clicked.connect(self.prompt_start_tutorial)
        self.tutorial_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        toolbar.addWidget(self.tutorial_button)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = QLabel("장비 설정 정보")
        layout.addWidget(title)

        self.pinned_table_view = SpreadsheetTableView()
        self.table_view = SpreadsheetTableView()
        self.pinned_table_view.setProperty("pinBoundaryRole", "pinned")
        self.table_view.setProperty("pinBoundaryRole", "main")
        self.pinned_table_view.setVerticalHeader(RowHandleHeaderView(self.pinned_table_view))
        self.table_view.setVerticalHeader(RowHandleHeaderView(self.table_view))
        for view in (self.pinned_table_view, self.table_view):
            self._configure_table_view(view)
            view.setModel(self.proxy_model)
            view.setItemDelegate(self.cell_delegate)
            view.setContextMenuPolicy(Qt.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda pos, target=view: self._show_table_context_menu(target, pos))
            view.copyRequested.connect(self.copy_selected_cells)
            view.pasteRequested.connect(self.paste_clipboard_cells)
            view.cutRequested.connect(self.cut_selected_cells)
            view.clearRequested.connect(self.clear_selected_cells)
            view.fillRequested.connect(self.fill_selection_to_target)
            view.setFrameShape(QTableView.NoFrame)
            view.setStyleSheet(EMBEDDED_TABLE_STYLE)
        self.pinned_table_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.pinned_table_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pinned_table_view.setSelectionModel(self.table_view.selectionModel())
        selection_model = self.table_view.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(self.refresh_selected_preview)
            selection_model.currentChanged.connect(self._on_table_current_changed)
        self.table_view.verticalScrollBar().valueChanged.connect(self.pinned_table_view.verticalScrollBar().setValue)
        self.pinned_table_view.verticalScrollBar().valueChanged.connect(self.table_view.verticalScrollBar().setValue)
        self.table_view.horizontalHeader().sectionResized.connect(self._sync_column_width_from_main)
        self.pinned_table_view.horizontalHeader().sectionResized.connect(self._sync_column_width_from_pinned)
        self.table_view.horizontalHeader().sectionMoved.connect(self._sync_column_move_from_main)
        self.pinned_table_view.horizontalHeader().sectionMoved.connect(self._sync_column_move_from_pinned)
        self.table_view.horizontalHeader().sectionClicked.connect(self._on_header_section_clicked)
        self.pinned_table_view.horizontalHeader().sectionClicked.connect(self._on_header_section_clicked)
        self.table_view.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.pinned_table_view.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_view.horizontalHeader().customContextMenuRequested.connect(
            lambda pos, target=self.table_view: self._show_header_context_menu(target, pos)
        )
        self.pinned_table_view.horizontalHeader().customContextMenuRequested.connect(
            lambda pos, target=self.pinned_table_view: self._show_header_context_menu(target, pos)
        )
        for row_view in (self.pinned_table_view, self.table_view):
            row_header = row_view.verticalHeader()
            row_header.setContextMenuPolicy(Qt.CustomContextMenu)
            row_header.customContextMenuRequested.connect(
                lambda pos, target=row_view: self._show_row_header_context_menu(target, pos)
            )
            if isinstance(row_header, RowHandleHeaderView):
                row_header.rowMoveRequested.connect(self._handle_row_move_requested)
            row_header.setDefaultSectionSize(self.table_view.verticalHeader().defaultSectionSize())
            row_header.setSectionResizeMode(QHeaderView.ResizeToContents)
            row_header.setMinimumWidth(44)
        self.pinned_table_view.verticalHeader().setVisible(True)
        self.table_view.verticalHeader().setVisible(False)

        self.table_shell = QFrame()
        self.table_shell.setObjectName("DeviceTableShell")
        self.tables_splitter = self.table_shell
        shell_layout = QHBoxLayout(self.table_shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.pinned_divider = QFrame()
        self.pinned_divider.setObjectName("PinnedDivider")
        self.pinned_divider.setToolTip("이 경계 기준으로 왼쪽은 고정 컬럼, 오른쪽은 일반 컬럼입니다.")
        self.pinned_divider.hide()
        shell_layout.addWidget(self.pinned_table_view)
        shell_layout.addWidget(self.pinned_divider)
        shell_layout.addWidget(self.table_view, 1)
        layout.addWidget(self.table_shell, 1)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        action_group = QGroupBox("CLI")
        action_layout = QVBoxLayout(action_group)
        action_layout.setContentsMargins(5, 5, 5, 5)
        action_layout.setSpacing(3)

        navigation_row = QHBoxLayout()
        self.previous_device_button = QPushButton("이전")
        self.previous_device_button.clicked.connect(lambda: self._select_relative_visible_row(-1))
        self.next_device_button = QPushButton("다음")
        self.next_device_button.clicked.connect(lambda: self._select_relative_visible_row(1))
        self.copy_cli_button = QPushButton("복사")
        self.copy_cli_button.clicked.connect(self.copy_selected_cli)
        self.copy_next_cli_button = QPushButton("복사+다음")
        self.copy_next_cli_button.clicked.connect(self.copy_selected_cli_and_advance)
        self.select_cli_button = QPushButton("전체 선택")
        self.select_cli_button.clicked.connect(self.select_cli_preview_text)
        self.copy_cli_button.setToolTip("현재 선택한 장비 CLI를 클립보드에 복사합니다.")
        self.copy_next_cli_button.setToolTip("CLI를 복사한 뒤 바로 다음 장비로 이동합니다.")
        self.select_cli_button.setToolTip("CLI 미리보기 전체를 선택합니다.")
        navigation_row.addWidget(self.previous_device_button)
        navigation_row.addWidget(self.next_device_button)
        navigation_row.addWidget(self.copy_cli_button)
        navigation_row.addWidget(self.copy_next_cli_button)
        navigation_row.addWidget(self.select_cli_button)
        action_layout.addLayout(navigation_row)

        work_actions = QHBoxLayout()
        self.mark_done_button = QPushButton("적용 완료")
        self.mark_done_button.clicked.connect(self.mark_selected_rows_done)
        self.reset_work_state_button = QPushButton("상태 초기화")
        self.reset_work_state_button.clicked.connect(self.reset_selected_rows_work_state)
        work_actions.addWidget(self.mark_done_button)
        work_actions.addWidget(self.reset_work_state_button)
        action_layout.addLayout(work_actions)
        layout.addWidget(action_group)

        summary_group = QGroupBox("선택")
        summary_layout = QVBoxLayout(summary_group)
        summary_layout.setContentsMargins(5, 5, 5, 5)
        summary_layout.setSpacing(3)
        self.selected_device_label = QLabel("장비 미선택")
        self.selected_device_label.setWordWrap(True)
        self.selected_device_label.setObjectName("SelectionName")
        self.cli_status_label = QLabel("CLI 대기")
        self.cli_status_label.setWordWrap(True)
        self.cli_status_label.setStyleSheet("padding: 3px 6px; border-radius: 6px; background: #eef2f6; color: #22303c;")
        self.work_state_label = QLabel("작업 상태 · 대기")
        self.work_state_label.setWordWrap(True)
        self.work_state_label.setStyleSheet("color: #5f6b76;")
        self.profile_effect_label = QLabel("입력값 요약")
        self.profile_effect_label.setWordWrap(True)
        self.profile_effect_label.setStyleSheet("color: #4e4a41;")
        summary_layout.addWidget(self.selected_device_label)
        summary_layout.addWidget(self.cli_status_label)
        summary_layout.addWidget(self.work_state_label)
        layout.addWidget(summary_group)

        self.detail_tabs = QTabWidget()
        self.detail_tabs.setDocumentMode(True)
        self.detail_tabs.setUsesScrollButtons(False)
        self.detail_tabs.setElideMode(Qt.ElideRight)
        self.detail_tabs.tabBar().setDrawBase(False)

        guide_tab = QWidget()
        guide_layout = QVBoxLayout(guide_tab)
        guide_layout.setContentsMargins(4, 4, 4, 4)
        guide_layout.setSpacing(3)
        self.profile_title_label = QLabel("프로파일")
        self.profile_title_label.setWordWrap(True)
        self.profile_title_label.setObjectName("GuideTitle")
        self.profile_meta_label = QLabel("-")
        self.profile_meta_label.setWordWrap(True)
        self.profile_meta_label.setObjectName("GuideMeta")
        self.profile_description_label = QLabel("설명 없음")
        self.profile_description_label.setWordWrap(True)
        self.profile_description_label.setObjectName("GuideCard")
        self.entry_rules_label = QLabel("규칙: 프로파일 ID 필수")
        self.entry_rules_label.setWordWrap(True)
        self.entry_rules_label.setObjectName("GuideHint")
        guide_layout.addWidget(self.profile_title_label)
        guide_layout.addWidget(self.profile_meta_label)
        guide_layout.addWidget(self.profile_description_label)
        guide_layout.addWidget(self.entry_rules_label)
        guide_layout.addStretch(1)
        self.detail_tabs.addTab(guide_tab, "안내")

        reference_tab = QWidget()
        reference_layout = QVBoxLayout(reference_tab)
        reference_layout.setContentsMargins(4, 4, 4, 4)
        self.profile_reference_table = QTableWidget(0, 6)
        self.profile_reference_table.setHorizontalHeaderLabels(["변수", "현재 값", "타입", "필수", "기본값", "설명"])
        self.profile_reference_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.profile_reference_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.profile_reference_table.verticalHeader().setVisible(False)
        self.profile_reference_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.profile_reference_table.horizontalHeader().setStretchLastSection(True)
        self.profile_reference_table.setMinimumHeight(88)
        reference_layout.addWidget(self.profile_reference_table)
        self.detail_tabs.addTab(reference_tab, "변수")

        issue_tab = QWidget()
        issue_layout = QVBoxLayout(issue_tab)
        issue_layout.setContentsMargins(4, 4, 4, 4)
        self.issue_list = QListWidget()
        self.issue_list.setMinimumHeight(72)
        issue_layout.addWidget(self.issue_list)
        self.detail_tabs.addTab(issue_tab, "이슈")

        cli_tab = QWidget()
        cli_layout = QVBoxLayout(cli_tab)
        cli_layout.setContentsMargins(4, 4, 4, 4)
        self.cli_preview = QPlainTextEdit()
        self.cli_preview.setReadOnly(True)
        self.cli_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.cli_preview.setMinimumHeight(200)
        cli_layout.addWidget(self.cli_preview)
        self.detail_tabs.addTab(cli_tab, "CLI")

        layout.addWidget(self.detail_tabs, 1)
        return panel

    def _configure_table_view(self, table_view: QTableView) -> None:
        table_view.setHorizontalHeader(AutoScrollHeaderView(Qt.Horizontal, table_view))
        header = table_view.horizontalHeader()
        if isinstance(header, AutoScrollHeaderView):
            header.boundaryDropRequested.connect(self._handle_header_boundary_drop)
        table_view.setAlternatingRowColors(True)
        table_view.setSelectionBehavior(QAbstractItemView.SelectItems)
        table_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table_view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table_view.horizontalHeader().setStretchLastSection(False)
        table_view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table_view.horizontalHeader().setMinimumSectionSize(80)
        table_view.horizontalHeader().setSectionsMovable(True)
        table_view.horizontalHeader().setSectionsClickable(True)
        table_view.horizontalHeader().setSortIndicatorShown(True)
        table_view.setWordWrap(False)
        table_view.setStyleSheet(TABLE_STYLE)
        table_view.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )

    def _load_app_state(self) -> None:
        self.recent_files = []
        self.pinned_headers = []
        self.visible_headers = []
        self._visible_headers_initialized = False
        self.column_order = []
        self.file_column_state = {}
        self.allow_error_autosave = False
        self._restored_window_geometry = ""
        self._restored_splitter_sizes = []
        self._restored_last_opened_file = ""
        self._restored_filter_field = "전체"
        self._restored_filter_value = ""
        self._restored_profile_id = ""
        self._restored_detail_tab_index = 0
        self._restored_selected_row = None
        self._restored_auto_save = True
        if not APP_STATE_PATH.exists():
            return
        try:
            state = json.loads(APP_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.recent_files = [str(path) for path in state.get("recent_files", []) if str(path).strip()][:DEFAULT_RECENT_LIMIT]
        self.pinned_headers = [str(header) for header in state.get("pinned_headers", []) if str(header).strip()]
        if "visible_headers" in state:
            self.visible_headers = [str(header) for header in state.get("visible_headers", []) if str(header).strip()]
            self._visible_headers_initialized = True
        self.column_order = [str(header) for header in state.get("column_order", []) if str(header).strip()]
        raw_file_column_state = state.get("file_column_state", {})
        if isinstance(raw_file_column_state, dict):
            for path_key, value in raw_file_column_state.items():
                if not isinstance(path_key, str) or not isinstance(value, dict):
                    continue
                self.file_column_state[path_key] = {
                    "pinned_headers": [str(header) for header in value.get("pinned_headers", []) if str(header).strip()],
                    "visible_headers": [str(header) for header in value.get("visible_headers", []) if str(header).strip()],
                    "column_order": [str(header) for header in value.get("column_order", []) if str(header).strip()],
                }
        self.allow_error_autosave = bool(state.get("allow_error_autosave", False))
        self._restored_auto_save = bool(state.get("auto_save_enabled", True))
        self._restored_last_opened_file = str(state.get("last_opened_file", "")).strip()
        self._restored_filter_field = str(state.get("filter_field", "전체") or "전체").strip()
        self._restored_filter_value = str(state.get("filter_value", "") or "")
        self._restored_profile_id = str(state.get("selected_profile_id", "") or "").strip()
        self._restored_detail_tab_index = max(0, int(state.get("detail_tab_index", 0) or 0))
        selected_row = state.get("selected_row")
        self._restored_selected_row = int(selected_row) if isinstance(selected_row, int) and selected_row >= 0 else None
        self._restored_splitter_sizes = [int(value) for value in state.get("main_splitter_sizes", []) if isinstance(value, int) and value > 0]
        geometry_text = str(state.get("window_geometry", "") or "").strip()
        if geometry_text:
            self._restored_window_geometry = geometry_text

    def _save_app_state(self) -> None:
        self._remember_current_file_column_state()
        selected_row = self._current_source_row()
        geometry = ""
        try:
            geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        except Exception:
            geometry = ""
        state = {
            "recent_files": self.recent_files[:DEFAULT_RECENT_LIMIT],
            "pinned_headers": self.pinned_headers,
            "visible_headers": self.visible_headers,
            "column_order": self.column_order,
            "file_column_state": self.file_column_state,
            "allow_error_autosave": bool(getattr(self, "allow_error_autosave_check", None).isChecked() if hasattr(self, "allow_error_autosave_check") else self.allow_error_autosave),
            "auto_save_enabled": bool(self.auto_save_check.isChecked()) if hasattr(self, "auto_save_check") else self._restored_auto_save,
            "last_opened_file": str(self.current_file_path) if self.current_file_path else "",
            "filter_field": self.filter_field_combo.currentText() if hasattr(self, "filter_field_combo") else self._restored_filter_field,
            "filter_value": self.filter_value_edit.text() if hasattr(self, "filter_value_edit") else self._restored_filter_value,
            "selected_profile_id": self.add_profile_combo.currentText() if hasattr(self, "add_profile_combo") else self._restored_profile_id,
            "detail_tab_index": self.detail_tabs.currentIndex() if hasattr(self, "detail_tabs") else self._restored_detail_tab_index,
            "selected_row": selected_row if selected_row is not None else -1,
            "main_splitter_sizes": self.main_splitter.sizes() if hasattr(self, "main_splitter") else self._restored_splitter_sizes,
            "window_geometry": geometry,
        }
        APP_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _apply_restored_window_state(self) -> None:
        if hasattr(self, "auto_save_check"):
            self.auto_save_check.setChecked(self._restored_auto_save)
        if hasattr(self, "allow_error_autosave_check"):
            self.allow_error_autosave_check.setChecked(self.allow_error_autosave)
        if self._restored_window_geometry:
            try:
                geometry = QByteArray.fromBase64(self._restored_window_geometry.encode("ascii"))
                if not geometry.isEmpty():
                    self.restoreGeometry(geometry)
            except Exception:
                pass
        if self._restored_splitter_sizes and hasattr(self, "main_splitter"):
            self.main_splitter.setSizes(self._restored_splitter_sizes)

    def _restore_last_session_state(self) -> None:
        if self._restored_profile_id and self._restored_profile_id in self.profiles:
            self.add_profile_combo.setCurrentText(self._restored_profile_id)
        if self._restored_last_opened_file:
            last_path = Path(self._restored_last_opened_file)
            if last_path.exists():
                self.load_device_file(last_path)
        self._restore_saved_filters()
        if hasattr(self, "detail_tabs") and self.detail_tabs.count() > 0:
            self.detail_tabs.setCurrentIndex(min(self._restored_detail_tab_index, self.detail_tabs.count() - 1))
        if self.current_file_path is not None and self._restored_selected_row is not None:
            self._select_source_row(self._restored_selected_row)

    def _restore_saved_filters(self) -> None:
        if not hasattr(self, "filter_field_combo") or not hasattr(self, "filter_value_edit"):
            return
        field_text = self._restored_filter_field or "전체"
        if self.filter_field_combo.findText(field_text) < 0:
            field_text = "전체"
        self.filter_field_combo.blockSignals(True)
        self.filter_value_edit.blockSignals(True)
        self.filter_field_combo.setCurrentText(field_text)
        self.filter_value_edit.setText(self._restored_filter_value)
        self.filter_field_combo.blockSignals(False)
        self.filter_value_edit.blockSignals(False)
        self.apply_filter()

    def _remember_current_file_column_state(self) -> None:
        if self.current_file_path is None:
            return
        path_key = str(self.current_file_path.resolve())
        self.file_column_state[path_key] = {
            "pinned_headers": list(self.pinned_headers),
            "visible_headers": list(self.visible_headers),
            "column_order": list(self.column_order),
        }

    def _restore_column_state_for_path(self, path: Path, headers: list[str]) -> None:
        path_key = str(path.resolve())
        saved_state = self.file_column_state.get(path_key)
        if saved_state is not None:
            self.pinned_headers = [header for header in saved_state.get("pinned_headers", []) if header in headers]
            self.visible_headers = [header for header in saved_state.get("visible_headers", []) if header in headers]
            self.column_order = [header for header in saved_state.get("column_order", []) if header in headers]
            self._visible_headers_initialized = True
            return
        self.pinned_headers = [header for header in self.pinned_headers if header in headers]
        self.visible_headers = [header for header in self.visible_headers if header in headers]
        self.column_order = [header for header in self.column_order if header in headers]
        if self.visible_headers:
            self._visible_headers_initialized = True

    def refresh_recent_files_widget(self) -> None:
        if not hasattr(self, "recent_files_combo"):
            return
        current_path = ""
        current_path = str(self.recent_files_combo.currentData(Qt.UserRole) or "").strip()
        self.recent_files_combo.blockSignals(True)
        self.recent_files_combo.clear()
        for recent in self.recent_files:
            recent_path = Path(recent)
            display_name = recent_path.name or str(recent_path)
            display_text = f"{display_name} ({recent_path.parent})" if recent_path.parent != recent_path else display_name
            self.recent_files_combo.addItem(display_text, recent)
            item_index = self.recent_files_combo.count() - 1
            self.recent_files_combo.setItemData(item_index, recent, Qt.ToolTipRole)
        if current_path and current_path in self.recent_files:
            current_index = self.recent_files_combo.findData(current_path, Qt.UserRole)
            if current_index >= 0:
                self.recent_files_combo.setCurrentIndex(current_index)
        elif self.current_file_path:
            current_index = self.recent_files_combo.findData(str(self.current_file_path.resolve()), Qt.UserRole)
            if current_index >= 0:
                self.recent_files_combo.setCurrentIndex(current_index)
        elif self.recent_files_combo.count() > 0:
            self.recent_files_combo.setCurrentIndex(0)
        self.recent_files_combo.blockSignals(False)

    def _remember_recent_file(self, path: Path) -> None:
        normalized = str(path.resolve())
        self.recent_files = [normalized, *[item for item in self.recent_files if item != normalized]]
        self.recent_files = self.recent_files[:DEFAULT_RECENT_LIMIT]
        self.refresh_recent_files_widget()
        self._save_app_state()

    def open_selected_recent_file(self) -> None:
        if not hasattr(self, "recent_files_combo"):
            return
        recent = str(self.recent_files_combo.currentData(Qt.UserRole) or "").strip()
        if not recent:
            recent = self.recent_files_combo.currentText().strip()
        if not recent:
            self.statusBar().showMessage("최근 파일을 먼저 선택하세요.", 3000)
            return
        recent_path = Path(recent)
        if not recent_path.exists():
            QMessageBox.warning(self, "최근 파일", "선택한 최근 파일을 찾을 수 없습니다.")
            self.recent_files = [item for item in self.recent_files if item != recent]
            self.refresh_recent_files_widget()
            self._save_app_state()
            return
        if self.current_file_path and recent_path.resolve() == self.current_file_path.resolve():
            self.statusBar().showMessage(f"이미 열려 있는 파일입니다: {recent_path.name}", 3000)
            return
        if not self._confirm_discard_changes():
            return
        self.load_device_file(recent_path)
        self._append_activity_log(f"최근 파일 열기: {recent_path}")
        self.statusBar().showMessage(f"최근 파일 열기: {recent_path.name}", 3000)

    def _capture_history_snapshot(self) -> None:
        if self._loading_table or self._history_blocked:
            return
        snapshot = {
            "headers": list(self.table_model.headers),
            "rows": self.table_model.stored_rows(),
        }
        if self._undo_stack and self._undo_stack[-1] == snapshot:
            return
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > 60:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_history_actions()

    def _reset_history(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_history_actions()

    def _restore_history_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._history_blocked = True
        self._loading_table = True
        try:
            self.table_model.set_table(
                DeviceTable(
                    path=self.current_file_path,
                    headers=list(snapshot["headers"]),
                    rows=[dict(row) for row in snapshot["rows"]],
                )
            )
        finally:
            self._loading_table = False
            self._history_blocked = False
        self.refresh_filter_fields()
        self.refresh_pinned_column_list()
        self.apply_pinned_columns()
        self.refresh_render_state()
        self.apply_filter()

    def undo_last_change(self) -> None:
        if not self._undo_stack:
            return
        current = {"headers": list(self.table_model.headers), "rows": [dict(row) for row in self.table_model.rows]}
        snapshot = self._undo_stack.pop()
        self._redo_stack.append(current)
        self._restore_history_snapshot(snapshot)
        self._update_history_actions()

    def redo_last_change(self) -> None:
        if not self._redo_stack:
            return
        current = {"headers": list(self.table_model.headers), "rows": [dict(row) for row in self.table_model.rows]}
        snapshot = self._redo_stack.pop()
        self._undo_stack.append(current)
        self._restore_history_snapshot(snapshot)
        self._update_history_actions()

    def _update_history_actions(self) -> None:
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(bool(self._undo_stack))
        if hasattr(self, "redo_action"):
            self.redo_action.setEnabled(bool(self._redo_stack))

    def _append_activity_log(self, message: str) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {message}"
        with ACTIVITY_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(log_line + "\n")

    def _create_backup(self, path: Path) -> Path | None:
        if not path.exists():
            return None
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = BACKUP_DIR / f"{path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}"
        shutil.copy2(path, backup_path)
        return backup_path

    def refresh_block_toggle_panel(self) -> None:
        while self.block_toggle_container_layout.count():
            child = self.block_toggle_container_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        profile_id = self.add_profile_combo.currentText() if hasattr(self, "add_profile_combo") else ""
        profile = self.profiles.get(profile_id) if profile_id else None
        if not profile or not profile.blocks:
            hint = QLabel("프로파일을 선택하면 블록이 표시됩니다.")
            hint.setStyleSheet("color: #8a8070;")
            hint.setWordWrap(True)
            self.block_toggle_container_layout.addWidget(hint)
            return
        disabled = self.disabled_blocks.get(profile_id, set())
        for block in profile.blocks:
            cb = QCheckBox(block.name)
            cb.setChecked(block.name not in disabled)
            cb.setProperty("_profile_id", profile_id)
            cb.setProperty("_block_name", block.name)
            cb.toggled.connect(self._on_block_toggle_changed)
            self.block_toggle_container_layout.addWidget(cb)

    def _on_block_toggle_changed(self, checked: bool) -> None:
        sender = self.sender()
        if not sender:
            return
        profile_id = sender.property("_profile_id")
        block_name = sender.property("_block_name")
        if not profile_id or not block_name:
            return
        if profile_id not in self.disabled_blocks:
            self.disabled_blocks[profile_id] = set()
        if checked:
            self.disabled_blocks[profile_id].discard(block_name)
        else:
            self.disabled_blocks[profile_id].add(block_name)
        self.refresh_timer.start()

    def reload_profiles(self) -> None:
        profiles, issues = load_profiles_from_directory(self.profile_dir)
        self.profiles = profiles
        self.profile_issues = issues
        self.profile_summary_label.setText(f"프로파일 {len(profiles)}개 / 오류 {len([i for i in issues if i.level == 'error'])}건")
        current = self.add_profile_combo.currentText()
        ids = sorted(self.profiles)
        self.add_profile_combo.blockSignals(True)
        self.add_profile_combo.clear()
        self.add_profile_combo.addItems(ids)
        if current in ids:
            self.add_profile_combo.setCurrentText(current)
        elif ids:
            self.add_profile_combo.setCurrentIndex(0)
        self.add_profile_combo.blockSignals(False)
        self._sync_headers_for_current_rows()
        self.refresh_filter_fields()
        self.refresh_pinned_column_list()
        self.apply_pinned_columns()
        self.refresh_block_toggle_panel()
        self.refresh_render_state()
        self.refresh_selected_preview()
        self._refresh_tutorial_dialog()

    def _set_empty_table(self) -> None:
        self._history_blocked = True
        self._loading_table = True
        try:
            self.table_model.set_table(DeviceTable(path=None, headers=["profile_id"], rows=[]))
        finally:
            self._loading_table = False
            self._history_blocked = False
        self.row_work_state = {}
        self.current_file_path = None
        self._update_file_path_label(None)
        self.is_dirty = False
        self._reset_history()
        self.refresh_filter_fields()
        self.refresh_pinned_column_list()
        self.apply_pinned_columns()
        self.refresh_render_state()
        self._refresh_tutorial_dialog()

    def open_device_file_dialog(self) -> None:
        if not self._confirm_discard_changes():
            return
        file_path, _ = QFileDialog.getOpenFileName(self, "장비 설정 정보 열기", str(ROOT_DIR), "Device Data (*.csv *.xlsx)")
        if file_path:
            self.load_device_file(Path(file_path))

    def prompt_start_tutorial(self) -> None:
        if self.tutorial_dialog is not None and self.tutorial_dialog.isVisible():
            self.tutorial_dialog.raise_()
            self.tutorial_dialog.activateWindow()
            return
        answer = QMessageBox.question(
            self,
            "튜토리얼",
            "튜토리얼을 시작하시겠습니까?\n\n프로파일 작성부터 장비 값 입력, CLI 복사까지 직접 실습하는 흐름으로 안내합니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        self.start_in_app_tutorial()

    def start_in_app_tutorial(self) -> bool:
        if not self._confirm_discard_changes():
            return False
        self.reload_profiles()
        self._set_empty_table()
        self.clear_filter()
        self.show_all_columns()
        if TUTORIAL_PROFILE_ID in self.profiles:
            self.add_profile_combo.setCurrentText(TUTORIAL_PROFILE_ID)
        self.detail_tabs.setCurrentIndex(0)
        self.tutorial_dialog = InAppTutorialDialog(self)
        self.tutorial_dialog.show()
        self.tutorial_dialog.raise_()
        self.tutorial_dialog.activateWindow()
        self._append_activity_log(f"튜토리얼 시작: {TUTORIAL_PROFILE_ID}")
        self.statusBar().showMessage("튜토리얼을 시작했습니다.", 3000)
        return True

    def on_tutorial_dialog_closed(self, dialog: InAppTutorialDialog) -> None:
        if self.tutorial_dialog is dialog:
            self.tutorial_dialog = None
            self.statusBar().showMessage("튜토리얼을 닫았습니다. 현재 상태에서 계속 실습할 수 있습니다.", 5000)

    def _refresh_tutorial_dialog(self) -> None:
        if self.tutorial_dialog is not None and self.tutorial_dialog.isVisible():
            self.tutorial_dialog.refresh_current_step()

    def _update_file_path_label(self, path: Path | None) -> None:
        if path is None:
            self.file_path_label.setText("-")
            self.file_path_label.setToolTip("")
            return
        display_name = path.name or str(path)
        self.file_path_label.setText(display_name)
        self.file_path_label.setToolTip(str(path))

    def _find_source_row_by_header_value(self, header_name: str, expected_value: str) -> int | None:
        normalized_expected = str(expected_value).strip().casefold()
        if not normalized_expected:
            return None
        for row_index, row in enumerate(self.table_model.rows):
            if str(row.get(header_name, "")).strip().casefold() == normalized_expected:
                return row_index
        return None

    def tutorial_profile(self) -> Profile | None:
        return self.profiles.get(TUTORIAL_PROFILE_ID)

    def tutorial_profile_ready(self) -> bool:
        profile = self.tutorial_profile()
        if profile is None or not profile.blocks:
            return False
        required_variables = {"hostname", "mgmt_ip", "mgmt_mask"}
        return required_variables.issubset(profile.variables)

    def _build_tutorial_starter_profile(self) -> Profile:
        return Profile(
            id=TUTORIAL_PROFILE_ID,
            vendor="CISCO",
            model="TUTORIAL_SWITCH",
            firmware="IOS-XE",
            description="프로그램 사용법을 익히기 위한 튜토리얼 실습용 프로파일입니다.",
            variables={
                "hostname": VariableSpec(
                    name="hostname",
                    required=True,
                    description="장비 hostname",
                    auto_increment=AUTO_INCREMENT_SUFFIX_NUMBER,
                ),
                "mgmt_ip": VariableSpec(
                    name="mgmt_ip",
                    required=True,
                    type="ipv4",
                    description="관리 IP 주소",
                    auto_increment=AUTO_INCREMENT_IPV4,
                ),
                "mgmt_mask": VariableSpec(
                    name="mgmt_mask",
                    default=TUTORIAL_MGMT_MASK,
                    description="관리 IP 마스크",
                ),
            },
            blocks=[
                BlockSpec(
                    name="base",
                    lines=[
                        "hostname {{ hostname }}",
                        "interface vlan 10",
                        " ip address {{ mgmt_ip }} {{ mgmt_mask }}",
                        " no shutdown",
                    ],
                )
            ],
        )

    def open_tutorial_profile_dialog(self) -> bool:
        profile = self.tutorial_profile() or self._build_tutorial_starter_profile()
        dialog = ProfileBuilderDialog(self.profile_dir, profile, self)
        dialog.setWindowTitle("튜토리얼 프로파일 작성")
        if not dialog.exec():
            self._refresh_tutorial_dialog()
            return False
        self._reload_after_profile_save(dialog.saved_profile_id or TUTORIAL_PROFILE_ID)
        self._refresh_tutorial_dialog()
        return self.tutorial_profile_ready()

    def tutorial_workspace_loaded(self) -> bool:
        return self.current_file_path == TUTORIAL_WORKSPACE_PATH and self.table_model.rowCount() > 0

    def prepare_tutorial_device_file(self) -> bool:
        profile = self.tutorial_profile()
        if profile is None or not self.tutorial_profile_ready():
            QMessageBox.information(self, "튜토리얼", "먼저 튜토리얼 프로파일을 저장해 주세요.")
            return False
        TUTORIAL_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        if not TUTORIAL_WORKSPACE_PATH.exists():
            headers = ["device_id", "profile_id", *profile.variables.keys()]
            rows = [make_blank_table_row(headers, profile)]
            save_device_table_to_path(TUTORIAL_WORKSPACE_PATH, headers, rows)
        self.load_device_file(TUTORIAL_WORKSPACE_PATH)
        self.clear_filter()
        self.show_all_columns()
        if TUTORIAL_PROFILE_ID in self.profiles:
            self.add_profile_combo.setCurrentText(TUTORIAL_PROFILE_ID)
        selected = self.focus_tutorial_workspace_row(detail_tab=0, column_name="device_id")
        self._append_activity_log(f"튜토리얼 장비 파일 열기: {TUTORIAL_WORKSPACE_PATH}")
        self._refresh_tutorial_dialog()
        return selected

    def _tutorial_source_row_index(self) -> int | None:
        if not self.table_model.rows:
            return None
        row_index = self._find_source_row_by_header_value("profile_id", TUTORIAL_PROFILE_ID)
        if row_index is not None:
            return row_index
        return 0

    def _tutorial_row(self) -> tuple[int, dict[str, str]] | None:
        row_index = self._tutorial_source_row_index()
        if row_index is None or row_index >= len(self.table_model.rows):
            return None
        return row_index, self.table_model.rows[row_index]

    def _select_source_cell(self, row_index: int, column_name: str) -> bool:
        if row_index < 0 or row_index >= len(self.table_model.rows):
            return False
        if column_name not in self.table_model.headers:
            return self._select_source_row(row_index)
        source_index = self.table_model.index(row_index, self.table_model.headers.index(column_name))
        if not source_index.isValid():
            return False
        proxy_index = self.proxy_model.mapFromSource(source_index)
        if not proxy_index.isValid():
            return self._select_source_row(row_index)
        self.table_view.setCurrentIndex(proxy_index)
        selection_model = self.table_view.selectionModel()
        if selection_model is not None:
            selection_model.setCurrentIndex(proxy_index, QItemSelectionModel.NoUpdate)
            selection_model.select(proxy_index, QItemSelectionModel.ClearAndSelect)
        self.table_view.scrollTo(proxy_index)
        self.refresh_selected_preview()
        return True

    def focus_tutorial_workspace_row(self, *, detail_tab: int, column_name: str) -> bool:
        row_index = self._tutorial_source_row_index()
        if row_index is None:
            return False
        selected = self._select_source_cell(row_index, column_name)
        self.detail_tabs.setCurrentIndex(max(0, min(detail_tab, self.detail_tabs.count() - 1)))
        return selected

    def tutorial_first_row_complete(self) -> bool:
        row_info = self._tutorial_row()
        if row_info is None:
            return False
        _row_index, row = row_info
        required_fields = ("device_id", "hostname", "mgmt_ip")
        return self.tutorial_workspace_loaded() and all(str(row.get(field, "")).strip() for field in required_fields)

    def tutorial_first_row_status_text(self) -> str:
        row_info = self._tutorial_row()
        if row_info is None:
            return "아직 입력할 튜토리얼 행이 없습니다."
        _row_index, row = row_info
        missing = [field for field in ("device_id", "hostname", "mgmt_ip") if not str(row.get(field, "")).strip()]
        if not missing:
            return "첫 번째 장비 입력이 완료되었습니다."
        return f"입력 필요: {', '.join(missing)}"

    def tutorial_cli_ready(self) -> bool:
        row_info = self._tutorial_row()
        if row_info is None:
            return False
        row_index, _row = row_info
        return row_index in self.current_rendered

    def tutorial_cli_status_text(self) -> str:
        row_info = self._tutorial_row()
        if row_info is None:
            return "먼저 튜토리얼 장비 파일을 준비해 주세요."
        row_index, _row = row_info
        rendered = self.current_rendered.get(row_index)
        if rendered is not None:
            line_count = len([line for line in rendered.text.splitlines() if line.strip()])
            return f"CLI 생성 완료: {line_count}줄"
        issues = self.current_row_issues.get(row_index, [])
        first_error = next((issue.message for issue in issues if issue.level == "error"), "")
        if first_error:
            return f"CLI 생성 전 확인 필요: {first_error}"
        return "아직 CLI가 생성되지 않았습니다."

    def tutorial_cli_copied(self) -> bool:
        row_info = self._tutorial_row()
        if row_info is None:
            return False
        row_index, _row = row_info
        return self._row_state_for_model(row_index) in {ROW_STATE_COPIED, ROW_STATE_DONE}

    def tutorial_copy_status_text(self) -> str:
        if self.tutorial_cli_copied():
            return "CLI 복사가 완료되었습니다."
        if not self.tutorial_cli_ready():
            return "먼저 CLI가 생성되어야 복사할 수 있습니다."
        return "메인 화면의 `복사` 버튼을 눌러 보세요."

    def load_device_file(self, path: Path) -> None:
        try:
            table = load_device_table_from_path(path)
        except Exception as exc:
            QMessageBox.critical(self, "파일 열기 실패", str(exc))
            return
        expanded = expand_headers_for_referenced_profiles(table.headers, table.rows, self.profiles)
        self._history_blocked = True
        self._loading_table = True
        try:
            self.table_model.set_table(DeviceTable(path=table.path, headers=expanded, rows=table.rows))
        finally:
            self._loading_table = False
            self._history_blocked = False
        self.row_work_state = {}
        self.current_file_path = path
        self._update_file_path_label(path)
        self.is_dirty = False
        self._reset_history()
        self._restore_column_state_for_path(path, expanded)
        self._remember_recent_file(path)
        self.refresh_filter_fields()
        self.refresh_pinned_column_list()
        self.apply_pinned_columns()
        self._apply_saved_column_order()
        self.refresh_render_state()
        self.apply_filter()
        self._append_activity_log(f"장비 파일 열기: {path}")
        self._refresh_tutorial_dialog()

    def save_current_file(self) -> None:
        if self.current_file_path is None:
            self.save_current_file_as()
            return
        self._save_to_path(self.current_file_path, autosave=False)

    def save_current_file_as(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "장비 설정 정보 저장",
            str(self.current_file_path or (ROOT_DIR / "device_values" / "site_devices.csv")),
            "Device Data (*.csv *.xlsx)",
        )
        if file_path:
            self._save_to_path(Path(file_path), autosave=False)

    def _save_to_path(self, path: Path, *, autosave: bool) -> bool:
        self.refresh_render_state()
        blocking_issues = self._collect_save_blockers()
        if blocking_issues:
            summary = "\n".join(f"- {message}" for message in blocking_issues[:10])
            if len(blocking_issues) > 10:
                summary += f"\n- 그 외 {len(blocking_issues) - 10}건"
            if autosave:
                if hasattr(self, "allow_error_autosave_check") and self.allow_error_autosave_check.isChecked():
                    pass
                else:
                    self.statusBar().showMessage(f"자동 저장 보류: {blocking_issues[0]}", 5000)
                    return False
            else:
                answer = QMessageBox.question(
                    self,
                    "저장 전 확인 필요",
                    f"오류가 발생했지만 그래도 저장하시겠습니까?\n\n{summary}",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return False
        backup_path = None
        try:
            if not autosave:
                backup_path = self._create_backup(path)
            save_device_table_to_path(path, self.table_model.headers, self.table_model.rows)
        except Exception as exc:
            if autosave:
                self.statusBar().showMessage(f"자동 저장 실패: {exc}", 4000)
                return False
            QMessageBox.critical(self, "저장 실패", str(exc))
            return False
        self.current_file_path = path
        self._update_file_path_label(path)
        self.is_dirty = False
        self._remember_recent_file(path)
        self._update_summary()
        log_message = f"{path.name} {'자동 저장' if autosave else '저장'} 완료"
        if backup_path:
            log_message += f" / 백업: {backup_path.name}"
        self._append_activity_log(log_message)
        self.statusBar().showMessage(f"{path.name} {'자동 저장' if autosave else '저장'} 완료", 3000)
        return True

    def auto_save_current_file(self) -> None:
        if self.auto_save_check.isChecked() and self.current_file_path and self.is_dirty:
            self._save_to_path(self.current_file_path, autosave=True)

    def on_auto_save_toggled(self, _: bool) -> None:
        if hasattr(self, "allow_error_autosave_check"):
            self.allow_error_autosave_check.setEnabled(self.auto_save_check.isChecked())
        if self.auto_save_check.isChecked() and self.current_file_path and self.is_dirty:
            self.auto_save_timer.start()
        self._save_app_state()
        self._update_summary()

    def on_allow_error_autosave_toggled(self, _: bool) -> None:
        self._save_app_state()
        self._update_summary()

    def refresh_filter_fields(self) -> None:
        current = self.filter_field_combo.currentText() or "전체"
        headers = ["전체", "작업 상태", *self.table_model.headers]
        self.filter_field_combo.blockSignals(True)
        self.filter_field_combo.clear()
        self.filter_field_combo.addItems(headers)
        if current in headers:
            self.filter_field_combo.setCurrentText(current)
        self.filter_field_combo.blockSignals(False)

    def apply_filter(self) -> None:
        self.proxy_model.set_filter(self.filter_field_combo.currentText(), self.filter_value_edit.text())
        self._update_summary()
        self.refresh_selected_preview()
        self.schedule_auto_resize()

    def clear_filter(self) -> None:
        self.filter_field_combo.setCurrentText("전체")
        self.filter_value_edit.clear()

    def refresh_pinned_column_list(self) -> None:
        headers = self.table_model.headers
        ordered_headers = [header for header in self.column_order if header in headers]
        ordered_headers.extend(header for header in headers if header not in ordered_headers)
        if not self._visible_headers_initialized:
            self.visible_headers = list(ordered_headers)
            self._visible_headers_initialized = True
        self.visible_headers = [header for header in self.visible_headers if header in headers]
        if not self.visible_headers and not self._visible_headers_initialized:
            self.visible_headers = list(ordered_headers)
        self.pinned_headers = [header for header in self.pinned_headers if header in self.visible_headers]
        pinned_visible = [header for header in ordered_headers if header in self.visible_headers and header in self.pinned_headers]
        regular_visible = [header for header in ordered_headers if header in self.visible_headers and header not in self.pinned_headers]
        hidden_headers = [header for header in ordered_headers if header not in self.visible_headers]
        self._updating_pin_items = True
        self.pinned_columns_list.clear()
        for header in [*pinned_visible, *regular_visible]:
            item = QListWidgetItem(header)
            item.setData(Qt.UserRole, header)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Checked)
            if header in pinned_visible:
                item.setBackground(DEFAULT_BG)
                item.setToolTip("고정 컬럼")
            else:
                item.setToolTip("표시 컬럼")
            self.pinned_columns_list.addItem(item)
        for header in hidden_headers:
            item = QListWidgetItem(header)
            item.setData(Qt.UserRole, header)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Unchecked)
            item.setToolTip("숨김 컬럼")
            self.pinned_columns_list.addItem(item)
        self._updating_pin_items = False
        self._update_pin_summary()

    def on_pinned_columns_changed(self, _: QListWidgetItem) -> None:
        if self._updating_pin_items:
            return
        self._sync_column_settings_from_list()

    def on_pinned_columns_reordered(self, *_: Any) -> None:
        if self._updating_pin_items:
            return
        self._sync_column_settings_from_list()

    def _sync_column_settings_from_list(self) -> None:
        headers_in_order: list[str] = []
        visible_headers: list[str] = []
        pinned_header_set = {header for header in self.pinned_headers if header in self.table_model.headers}
        for index in range(self.pinned_columns_list.count()):
            item = self.pinned_columns_list.item(index)
            header = self._column_item_header(item)
            headers_in_order.append(header)
            if item.checkState() == Qt.Checked:
                visible_headers.append(header)
        self.column_order = headers_in_order
        self.visible_headers = visible_headers
        self._visible_headers_initialized = True
        self.pinned_headers = [header for header in headers_in_order if header in visible_headers and header in pinned_header_set]
        self.apply_pinned_columns()
        self._update_pin_summary()
        self._save_app_state()

    def _column_item_header(self, item: QListWidgetItem) -> str:
        return str(item.data(Qt.UserRole) or item.text()).strip()

    def _update_pin_summary(self) -> None:
        if not hasattr(self, "column_pin_summary_label"):
            return
        if not self.visible_headers:
            self.column_pin_summary_label.setText("표시 컬럼 없음")
            return
        visible_count = len(self.visible_headers)
        pinned_text = ", ".join(self.pinned_headers) if self.pinned_headers else "없음"
        self.column_pin_summary_label.setText(f"표시 {visible_count}개 / 고정: {pinned_text}")

    def _selected_column_rows(self) -> list[int]:
        selected_rows = sorted(
            {
                self.pinned_columns_list.row(item)
                for item in self.pinned_columns_list.selectedItems()
                if self.pinned_columns_list.row(item) >= 0
            }
        )
        if selected_rows:
            return selected_rows
        current_row = self.pinned_columns_list.currentRow()
        if current_row < 0:
            return []
        current_item = self.pinned_columns_list.item(current_row)
        if current_item is None:
            return []
        return [current_row]

    def _selected_column_headers(self) -> list[str]:
        headers: list[str] = []
        for row in self._selected_column_rows():
            item = self.pinned_columns_list.item(row)
            if item is not None:
                headers.append(self._column_item_header(item))
        return headers

    def move_selected_column_item(self, offset: int) -> None:
        selected_rows = self._selected_column_rows()
        if not selected_rows:
            return
        total_items = self.pinned_columns_list.count()
        if offset < 0 and selected_rows[0] == 0:
            return
        if offset > 0 and selected_rows[-1] == total_items - 1:
            return
        headers = [self._column_item_header(self.pinned_columns_list.item(index)) for index in range(total_items)]
        selected_headers = [headers[row] for row in selected_rows]
        selected_set = set(selected_rows)
        if offset < 0:
            for row in selected_rows:
                if row - 1 not in selected_set:
                    headers[row - 1], headers[row] = headers[row], headers[row - 1]
        else:
            for row in reversed(selected_rows):
                if row + 1 not in selected_set:
                    headers[row + 1], headers[row] = headers[row], headers[row + 1]
        self._updating_pin_items = True
        original_items = [self.pinned_columns_list.takeItem(0) for _ in range(total_items)]
        items_by_header = {self._column_item_header(item): item for item in original_items if item is not None}
        for header in headers:
            self.pinned_columns_list.addItem(items_by_header[header])
        self._updating_pin_items = False
        self.pinned_columns_list.clearSelection()
        for index in range(self.pinned_columns_list.count()):
            item = self.pinned_columns_list.item(index)
            if self._column_item_header(item) in set(selected_headers):
                item.setSelected(True)
        if self.pinned_columns_list.selectedItems():
            self.pinned_columns_list.setCurrentItem(self.pinned_columns_list.selectedItems()[0])
        self._sync_column_settings_from_list()

    def _set_header_pinned_state(self, header_name: str, pinned: bool) -> None:
        if header_name not in self.table_model.headers:
            return
        visible_headers = {header for header in self.visible_headers if header in self.table_model.headers}
        current_pinned = {header for header in self.pinned_headers if header in visible_headers}
        if header_name not in visible_headers:
            current_pinned.discard(header_name)
        elif pinned:
            current_pinned.add(header_name)
        else:
            current_pinned.discard(header_name)
        ordered_headers = [header for header in self.column_order if header in self.table_model.headers]
        ordered_headers.extend(header for header in self.table_model.headers if header not in ordered_headers)
        updated = [header for header in ordered_headers if header in current_pinned]
        if updated == self.pinned_headers:
            self.refresh_pinned_column_list()
            self._save_app_state()
            return
        self.pinned_headers = updated
        self.refresh_pinned_column_list()
        self.apply_pinned_columns()
        self._save_app_state()

    def pin_selected_column(self) -> None:
        headers = self._selected_column_headers()
        if not headers:
            return
        for header in headers:
            if header not in self.visible_headers:
                row = self._find_column_item_row(header)
                if row >= 0:
                    self.pinned_columns_list.item(row).setCheckState(Qt.Checked)
        self.pinned_headers = [header for header in self.pinned_headers if header in self.visible_headers]
        for header in headers:
            if header in self.visible_headers and header not in self.pinned_headers:
                self.pinned_headers.append(header)
        self.refresh_pinned_column_list()
        self._restore_selected_column_headers(headers)
        self.apply_pinned_columns()
        self._save_app_state()

    def unpin_selected_column(self) -> None:
        headers = self._selected_column_headers()
        if not headers:
            return
        self.pinned_headers = [value for value in self.pinned_headers if value not in set(headers)]
        self.refresh_pinned_column_list()
        self._restore_selected_column_headers(headers)
        self.apply_pinned_columns()
        self._save_app_state()

    def delete_selected_columns(self) -> None:
        self._delete_columns(self._selected_column_headers())

    def delete_active_table_column(self) -> None:
        if not self._active_column_header:
            QMessageBox.information(self, "컬럼 삭제", "삭제할 컬럼을 먼저 선택하세요.")
            return
        self._delete_columns([self._active_column_header])

    def _delete_columns(self, headers: list[str]) -> None:
        if not headers:
            return
        required_headers = {"profile_id"}
        template_headers = {
            variable_name
            for profile in self.profiles.values()
            for variable_name in profile.variables
        }
        removable_headers = [
            header
            for header in headers
            if header not in required_headers and header not in template_headers
        ]
        blocked_required_headers = [header for header in headers if header in required_headers]
        blocked_template_headers = [header for header in headers if header in template_headers]
        if not removable_headers:
            if blocked_template_headers:
                QMessageBox.information(
                    self,
                    "컬럼 삭제",
                    "프로파일 변수 컬럼은 삭제할 수 없습니다.\n표시 컬럼에서 숨기기만 가능합니다.",
                )
            else:
                QMessageBox.information(self, "컬럼 삭제", "프로파일 ID(profile_id) 컬럼은 필수라 삭제할 수 없습니다.")
            return

        filled_count = sum(
            1
            for row in self.table_model.rows
            for header in removable_headers
            if str(row.get(header, "")).strip()
        )
        message = f"선택한 {len(removable_headers)}개 컬럼을 삭제하시겠습니까?"
        if filled_count:
            message += f"\n입력된 값 {filled_count}개도 함께 삭제됩니다."
        blocked_messages: list[str] = []
        if blocked_required_headers:
            blocked_messages.append(f"필수 컬럼 제외: {', '.join(blocked_required_headers)}")
        if blocked_template_headers:
            blocked_messages.append(f"프로파일 변수 컬럼 제외: {', '.join(blocked_template_headers)}")
        if blocked_messages:
            message += "\n" + "\n".join(blocked_messages)
        if QMessageBox.question(self, "컬럼 삭제", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._remove_headers(removable_headers, status_message=f"컬럼 {len(removable_headers)}개 삭제 완료")

    def _remove_headers(self, headers: list[str], *, status_message: str = "") -> None:
        removable_headers = [header for header in headers if header in self.table_model.headers]
        if not removable_headers:
            return
        self.is_dirty = True
        removable_set = set(removable_headers)
        self.visible_headers = [header for header in self.visible_headers if header not in removable_set]
        self.pinned_headers = [header for header in self.pinned_headers if header not in removable_set]
        self.column_order = [header for header in self.column_order if header not in removable_set]
        if self._active_column_header in removable_set:
            self._active_column_header = ""
            if hasattr(self, "delete_active_column_button"):
                self.delete_active_column_button.setEnabled(False)
        self._loading_table = True
        try:
            self.table_model.remove_headers(removable_headers)
        finally:
            self._loading_table = False
        self.refresh_filter_fields()
        self.refresh_pinned_column_list()
        self.apply_pinned_columns()
        self.refresh_render_state()
        self.refresh_selected_preview()
        self._save_app_state()
        if self.auto_save_check.isChecked() and self.current_file_path is not None:
            self.auto_save_timer.start()
        if status_message:
            self.statusBar().showMessage(status_message, 3000)

    def _find_column_item_row(self, header_name: str) -> int:
        for index in range(self.pinned_columns_list.count()):
            if self._column_item_header(self.pinned_columns_list.item(index)) == header_name:
                return index
        return -1

    def _restore_selected_column_headers(self, headers: list[str]) -> None:
        self.pinned_columns_list.clearSelection()
        for header in headers:
            row = self._find_column_item_row(header)
            if row >= 0:
                item = self.pinned_columns_list.item(row)
                item.setSelected(True)
                if self.pinned_columns_list.currentItem() is None:
                    self.pinned_columns_list.setCurrentItem(item)

    def show_all_columns(self) -> None:
        self.visible_headers = list(self.table_model.headers)
        self._visible_headers_initialized = True
        self.refresh_pinned_column_list()
        self.apply_pinned_columns()
        self._save_app_state()

    def clear_all_columns(self) -> None:
        self.visible_headers = []
        self._visible_headers_initialized = True
        self.pinned_headers = []
        self.refresh_pinned_column_list()
        self.apply_pinned_columns()
        self._save_app_state()

    def apply_pinned_columns(self) -> None:
        visible_set = set(self.visible_headers)
        pinned_set = set(self.pinned_headers) & visible_set
        for column, header in enumerate(self.table_model.headers):
            is_visible = header in visible_set
            is_pinned = header in pinned_set
            self.pinned_table_view.setColumnHidden(column, not (is_visible and is_pinned))
            self.table_view.setColumnHidden(column, not (is_visible and not is_pinned))
        has_pinned_columns = bool(pinned_set)
        self.pinned_table_view.setVisible(has_pinned_columns)
        self.table_view.verticalHeader().setVisible(not has_pinned_columns)
        self.pinned_table_view.verticalHeader().setVisible(has_pinned_columns)
        self._apply_saved_column_order()
        self.schedule_auto_resize()

    def _apply_saved_column_order(self) -> None:
        headers = self.table_model.headers
        if not headers:
            return
        desired = [header for header in self.column_order if header in headers]
        desired.extend(header for header in headers if header not in desired)
        self._syncing_section_move = True
        try:
            main_header = self.table_view.horizontalHeader()
            pinned_header = self.pinned_table_view.horizontalHeader()
            for visual_index, header_name in enumerate(desired):
                logical_index = headers.index(header_name)
                current_main = main_header.visualIndex(logical_index)
                if current_main != visual_index:
                    main_header.moveSection(current_main, visual_index)
                current_pinned = pinned_header.visualIndex(logical_index)
                if current_pinned != visual_index:
                    pinned_header.moveSection(current_pinned, visual_index)
        finally:
            self._syncing_section_move = False
        self._remember_current_column_order()

    def _remember_current_column_order(self) -> None:
        headers = self.table_model.headers
        if not headers:
            self.column_order = []
            return
        header_view = self.table_view.horizontalHeader()
        order: list[str] = []
        for visual_index in range(header_view.count()):
            logical_index = header_view.logicalIndex(visual_index)
            if 0 <= logical_index < len(headers):
                order.append(headers[logical_index])
        if order:
            self.column_order = order
            self._save_app_state()

    def schedule_auto_resize(self, *_: Any) -> None:
        self.resize_timer.start()

    def auto_size_table_columns(self) -> None:
        if self.proxy_model.columnCount() == 0:
            return
        self._syncing_width = True
        try:
            for column, header in enumerate(self.table_model.headers):
                view = None
                if not self.pinned_table_view.isColumnHidden(column):
                    view = self.pinned_table_view
                elif not self.table_view.isColumnHidden(column):
                    view = self.table_view
                if view is None:
                    continue
                view.resizeColumnToContents(column)
                width = max(view.columnWidth(column) + 18, max(96, len(header) * 9 + 28))
                width = min(width, 420)
                self.pinned_table_view.setColumnWidth(column, width)
                self.table_view.setColumnWidth(column, width)
            self._update_pinned_view_width()
        finally:
            self._syncing_width = False

    def _update_pinned_view_width(self) -> None:
        visible = [
            column
            for column, header in enumerate(self.table_model.headers)
            if header in set(self.pinned_headers) and header in set(self.visible_headers)
        ]
        if not visible:
            self.pinned_table_view.hide()
            self.pinned_table_view.setMinimumWidth(0)
            self.pinned_table_view.setMaximumWidth(0)
            self.table_view.verticalHeader().setVisible(True)
            if hasattr(self, "pinned_divider"):
                self.pinned_divider.setVisible(False)
            return
        row_handle_width = self.pinned_table_view.verticalHeader().width() + self.pinned_table_view.frameWidth() * 2 + 4
        content_width = sum(self.pinned_table_view.columnWidth(column) for column in visible)
        spacer_width = 6 if visible else 0
        total = row_handle_width + content_width + spacer_width
        shell_width = self.table_shell.size().width() if hasattr(self, "table_shell") else self.width()
        max_allowed = max(320, min(720, int(shell_width * 0.42)))
        target_width = min(total, max_allowed)
        self.pinned_table_view.setMinimumWidth(target_width)
        self.pinned_table_view.setMaximumWidth(target_width)
        self.pinned_table_view.show()
        self.table_view.verticalHeader().setVisible(False)
        if hasattr(self, "pinned_divider"):
            self.pinned_divider.setVisible(bool(visible))

    def add_row(self) -> None:
        profile = self.profiles.get(self.add_profile_combo.currentText())
        headers = list(self.table_model.headers or ["profile_id"])
        if profile:
            if "profile_id" not in headers:
                headers.append("profile_id")
            for variable_name in profile.variables:
                if variable_name not in headers:
                    headers.append(variable_name)
        if headers != self.table_model.headers:
            self._loading_table = True
            self.table_model.ensure_headers(headers)
            self._loading_table = False
            self.refresh_filter_fields()
            self.refresh_pinned_column_list()
            self.apply_pinned_columns()
        self.table_model.append_row(make_blank_table_row(headers, profile))
        last_source = self.table_model.index(self.table_model.rowCount() - 1, 0)
        proxy_index = self.proxy_model.mapFromSource(last_source)
        if proxy_index.isValid():
            self.table_view.selectRow(proxy_index.row())
            self.table_view.scrollTo(proxy_index)

    def duplicate_selected_rows(self) -> None:
        source_rows = self._selected_source_rows()
        if source_rows:
            self.table_model.duplicate_rows(source_rows)

    def duplicate_selected_rows_with_increment(self) -> None:
        source_rows = self._selected_source_rows()
        if not source_rows:
            QMessageBox.information(self, "연속 값 복사", "복사할 행을 먼저 선택하세요.")
            return
        selected_profiles = [
            _find_profile(self.profiles, self.table_model.rows[row_index].get("profile_id", ""))
            for row_index in source_rows
        ]
        unique_increment_fields: list[str] = []
        seen_fields: set[str] = set()
        for profile in selected_profiles:
            for field in profile_increment_fields(profile):
                if field not in seen_fields:
                    unique_increment_fields.append(field)
                    seen_fields.add(field)
        if not unique_increment_fields:
            QMessageBox.information(
                self,
                "연속 값 복사",
                "선택한 행의 프로파일 변수에 연속 값 규칙이 없습니다.\n프로파일 변수의 '연속 값 규칙'을 먼저 설정하세요.",
            )
            return
        rules_text = "적용 대상: " + ", ".join(unique_increment_fields)
        dialog = IncrementCopyDialog(rules_text, self)
        if dialog.exec() != QDialog.Accepted:
            return
        copies = dialog.values()
        selected = set(source_rows)
        new_rows: list[dict[str, str]] = []
        stored_rows = self.table_model.stored_rows()
        for row_index, stored_row in enumerate(stored_rows):
            row = {key: value for key, value in stored_row.items() if key != INTERNAL_ROW_UID}
            new_rows.append(dict(stored_row))
            if row_index not in selected:
                continue
            profile = _find_profile(self.profiles, row.get("profile_id", ""))
            for offset in range(1, copies + 1):
                new_rows.append(build_incremented_row(row, offset, profile))
        self._replace_table_rows(new_rows)
        self.statusBar().showMessage(f"선택 행 연속 값 복사 {copies}회 완료", 3000)

    def delete_selected_rows(self) -> None:
        source_rows = self._selected_source_rows()
        if not source_rows:
            return
        if QMessageBox.question(self, "행 삭제", f"선택한 {len(source_rows)}개 행을 삭제하시겠습니까?") == QMessageBox.Yes:
            self.table_model.remove_rows(source_rows)

    def _replace_table_rows(self, rows: list[dict[str, str]]) -> None:
        self._capture_history_snapshot()
        self._loading_table = True
        try:
            self.table_model.set_table(
                DeviceTable(
                    path=self.current_file_path,
                    headers=list(self.table_model.headers),
                    rows=rows,
                )
            )
        finally:
            self._loading_table = False
        self._clear_missing_row_work_state()
        self.refresh_filter_fields()
        self.refresh_pinned_column_list()
        self.apply_pinned_columns()
        self.refresh_render_state()
        self.apply_filter()

    def on_table_changed(self) -> None:
        if self._loading_table:
            return
        self._clear_missing_row_work_state()
        self.is_dirty = True
        if self._sync_headers_for_current_rows():
            return
        if self._prompt_remove_obsolete_profile_headers():
            return
        self.refresh_timer.start()
        if self.auto_save_check.isChecked() and self.current_file_path is not None:
            self.auto_save_timer.start()
        self._update_summary()
        self._refresh_tutorial_dialog()

    def _sync_headers_for_current_rows(self) -> bool:
        expanded = expand_headers_for_referenced_profiles(self.table_model.headers, self.table_model.rows, self.profiles)
        if expanded == self.table_model.headers:
            return False
        self._loading_table = True
        self.table_model.ensure_headers(expanded)
        self._loading_table = False
        self.refresh_filter_fields()
        self.refresh_pinned_column_list()
        self.apply_pinned_columns()
        return True

    def _referenced_profiles_for_rows(self) -> list[Profile]:
        seen: set[str] = set()
        profiles: list[Profile] = []
        for row in self.table_model.rows:
            profile = _find_profile(self.profiles, row.get("profile_id", ""))
            if not profile:
                continue
            normalized = _normalize_profile_id(profile.id)
            if normalized in seen:
                continue
            seen.add(normalized)
            profiles.append(profile)
        return profiles

    def _obsolete_profile_headers(self) -> list[str]:
        referenced_profiles = self._referenced_profiles_for_rows()
        if not referenced_profiles:
            return []
        referenced_headers = {
            variable_name
            for profile in referenced_profiles
            for variable_name in profile.variables
        }
        all_template_headers = {
            variable_name
            for profile in self.profiles.values()
            for variable_name in profile.variables
        }
        obsolete_headers = [
            header
            for header in self.table_model.headers
            if header != "profile_id" and header in all_template_headers and header not in referenced_headers
        ]
        return sorted(obsolete_headers)

    def _prompt_remove_obsolete_profile_headers(self) -> bool:
        obsolete_headers = self._obsolete_profile_headers()
        if not obsolete_headers:
            self._dismissed_obsolete_header_key = None
            return False
        obsolete_key = tuple(obsolete_headers)
        if obsolete_key == self._dismissed_obsolete_header_key:
            return False
        preview = ", ".join(obsolete_headers[:8])
        if len(obsolete_headers) > 8:
            preview += f" 외 {len(obsolete_headers) - 8}개"
        answer = QMessageBox.question(
            self,
            "불필요 컬럼 정리",
            "현재 남아 있는 프로파일 ID 기준으로 더 이상 사용하지 않는 프로파일 컬럼이 있습니다.\n"
            f"자동으로 삭제할까요?\n\n대상: {preview}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            self._dismissed_obsolete_header_key = obsolete_key
            return False
        self._dismissed_obsolete_header_key = None
        self._remove_headers(obsolete_headers, status_message=f"불필요 컬럼 {len(obsolete_headers)}개 정리 완료")
        return True

    def refresh_render_state(self) -> None:
        if not self.profiles:
            self.current_rendered = {}
            self.current_row_issues = {}
            self.table_model.set_issue_map({})
            self._update_summary()
            self.refresh_selected_preview()
            self._refresh_tutorial_dialog()
            return
        engine = ConfigEngine(self.profiles)
        profile_issues = engine.validate_profiles()
        table_issues = build_table_consistency_issues(self.table_model.rows, self.table_model.headers)
        profile_issue_map: dict[str, list[ValidationIssue]] = {}
        for issue in profile_issues:
            if issue.profile_id:
                profile_issue_map.setdefault(_normalize_profile_id(issue.profile_id), []).append(issue)

        self.current_rendered = {}
        self.current_row_issues = {}
        for row_index, row in enumerate(self.table_model.rows):
            record = DeviceRecord(row_number=row_index + 2, values={h: row.get(h, "") for h in self.table_model.headers})
            row_issues = list(table_issues.get(row_index, []))
            row_issues.extend(profile_issue_map.get(_normalize_profile_id(record.profile_id), []))
            if not any(str(v).strip() for k, v in record.values.items() if k != "profile_id"):
                self.current_row_issues[row_index] = row_issues
                continue
            if not record.profile_id:
                row_issues.append(ValidationIssue(level="error", scope="device", message="프로파일 ID 값이 비어 있습니다.", device_id=record.display_name, row_number=record.row_number))
                self.current_row_issues[row_index] = row_issues
                continue
            profile = _find_profile(self.profiles, record.profile_id)
            if not profile:
                row_issues.append(ValidationIssue(level="error", scope="device", message="프로파일 ID에 해당하는 프로파일을 찾을 수 없습니다.", profile_id=record.profile_id, device_id=record.display_name, row_number=record.row_number))
                self.current_row_issues[row_index] = row_issues
                continue
            _, device_issues = engine.resolve_values(record, profile)
            row_issues.extend(device_issues)
            if any(issue.level == "error" for issue in row_issues):
                self.current_row_issues[row_index] = row_issues
                continue
            try:
                skip = self.disabled_blocks.get(profile.id, set()) or None
                self.current_rendered[row_index] = engine.render_device(record, skip_blocks=skip)
            except Exception as exc:
                row_issues.append(ValidationIssue(level="error", scope="device", message=str(exc), profile_id=record.profile_id, device_id=record.display_name, row_number=record.row_number))
            self.current_row_issues[row_index] = row_issues
        self.table_model.set_issue_map(self.current_row_issues)
        self._update_summary()
        self.refresh_selected_preview()
        self._refresh_tutorial_dialog()

    def _collect_save_blockers(self) -> list[str]:
        seen: set[tuple[str, int | None]] = set()
        blockers: list[str] = []
        for issues in self.current_row_issues.values():
            for issue in issues:
                if issue.level != "error":
                    continue
                key = (issue.message, issue.row_number)
                if key in seen:
                    continue
                seen.add(key)
                prefix = f"{issue.device_id}: " if issue.device_id else ""
                blockers.append(prefix + issue.message)
        return blockers

    def refresh_selected_preview(self) -> None:
        source_rows = self._selected_source_rows()
        if not source_rows:
            self.selected_device_label.setText("장비 미선택")
            self.selected_device_label.setToolTip("")
            self.cli_status_label.setText("CLI 대기")
            self.cli_status_label.setStyleSheet("padding: 3px 6px; border-radius: 6px; background: #eef2f6; color: #22303c;")
            self.work_state_label.setText("작업 상태 · 대기")
            self.work_state_label.setStyleSheet("color: #5f6b76;")
            self.profile_effect_label.setText("입력값 요약")
            self._clear_detail_tabs()
            self.issue_list.clear()
            self.issue_list.addItem("선택한 장비 없음")
            self.cli_preview.setPlainText("")
            self._update_navigation_buttons()
            return
        row_index = source_rows[0]
        row = self.table_model.rows[row_index]
        device_label = row_display_name(row, row_index + 2)
        self.selected_device_label.setText(device_label)
        self.selected_device_label.setToolTip(f"{device_label} | {row.get('profile_id', '-') or '-'}")
        self._update_profile_reference(row)
        self._update_issue_list(row_index)
        rendered = self.current_rendered.get(row_index)
        self.cli_preview.setPlainText(rendered.text if rendered else "")
        issues = self.current_row_issues.get(row_index, [])
        row_state = self._row_state_for_model(row_index) or ROW_STATE_PENDING
        error_count = sum(1 for issue in issues if issue.level == "error")
        warning_count = sum(1 for issue in issues if issue.level == "warning")
        line_count = len([line for line in (rendered.text.splitlines() if rendered else []) if line.strip()])
        if rendered:
            self.cli_status_label.setText(f"복사 가능 · {line_count}줄 · 오류 {error_count} · 경고 {warning_count}")
            self.cli_status_label.setStyleSheet("padding: 3px 6px; border-radius: 6px; background: #eef8eb; color: #204227;")
        else:
            self.cli_status_label.setText(f"확인 필요 · 오류 {error_count} · 경고 {warning_count}")
            self.cli_status_label.setStyleSheet("padding: 3px 6px; border-radius: 6px; background: #fff1ed; color: #6f261c;")
        state_style = {
            ROW_STATE_PENDING: ("작업 상태 · 대기", "color: #5f6b76;"),
            ROW_STATE_COPIED: ("작업 상태 · 복사 완료", "color: #1f4f85;"),
            ROW_STATE_DONE: ("작업 상태 · 적용 완료", "color: #1f6a34;"),
        }
        state_text, state_css = state_style.get(row_state, state_style[ROW_STATE_PENDING])
        self.work_state_label.setText(state_text)
        self.work_state_label.setStyleSheet(state_css)
        self._update_profile_effect_label(row)
        self._update_navigation_buttons()

    def _on_table_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if current.isValid():
            source_index = self.proxy_model.mapToSource(current)
            self._set_highlighted_column(source_index.column() if source_index.isValid() else current.column())
        else:
            self._set_highlighted_column(None)

    def _on_header_section_clicked(self, logical_index: int) -> None:
        self._set_highlighted_column(logical_index)
        self._apply_column_sort(logical_index)

    def _set_highlighted_column(self, logical_index: int | None) -> None:
        sections = set()
        if logical_index is not None and logical_index >= 0:
            sections.add(logical_index)
            if 0 <= logical_index < len(self.table_model.headers):
                self._active_column_header = self.table_model.headers[logical_index]
                self._sync_column_list_selection(self._active_column_header)
                self.delete_active_column_button.setEnabled(True)
            else:
                self._active_column_header = ""
                self.delete_active_column_button.setEnabled(False)
        else:
            self._active_column_header = ""
            self.delete_active_column_button.setEnabled(False)
        for view in (self.table_view, self.pinned_table_view):
            header = view.horizontalHeader()
            if isinstance(header, AutoScrollHeaderView):
                header.set_highlighted_sections(sections)

    def _handle_header_boundary_drop(self, logical_index: int, pin_state: bool) -> None:
        if logical_index < 0 or logical_index >= len(self.table_model.headers):
            return
        header_name = self.table_model.headers[logical_index]
        if header_name not in self.visible_headers:
            return
        self._set_highlighted_column(logical_index)
        self._set_header_pinned_state(header_name, pin_state)

    def _handle_row_move_requested(self, from_proxy_row: int, to_proxy_row: int) -> None:
        if from_proxy_row < 0 or to_proxy_row < 0 or from_proxy_row == to_proxy_row:
            return
        if self._sort_column >= 0:
            QMessageBox.information(
                self,
                "행 이동",
                "정렬이 적용된 상태에서는 행 순서를 드래그로 바꿀 수 없습니다.\n정렬을 해제한 뒤 다시 시도해주세요.",
            )
            return
        source_row = self._proxy_row_to_source_row(from_proxy_row)
        target_row = self._proxy_row_to_source_row(to_proxy_row)
        if source_row is None or target_row is None:
            return
        row_uid = self.table_model.row_uid(source_row)
        moved_row = self.table_model.move_row(source_row, target_row)
        if moved_row is None:
            return
        self._clear_missing_row_work_state()
        if row_uid:
            for new_index, uid in enumerate(self.table_model.row_uids()):
                if uid == row_uid:
                    self._select_source_row(new_index)
                    break
        self.statusBar().showMessage("행 순서를 변경했습니다.", 3000)

    def _sync_column_list_selection(self, header_name: str) -> None:
        self.pinned_columns_list.clearSelection()
        for index in range(self.pinned_columns_list.count()):
            item = self.pinned_columns_list.item(index)
            if item is None:
                continue
            if self._column_item_header(item) == header_name:
                item.setSelected(True)
                self.pinned_columns_list.setCurrentItem(item)
                break

    def _activate_context_index(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        self.table_view.setCurrentIndex(index)
        selection_model = self.table_view.selectionModel()
        if selection_model is not None:
            selection_model.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
            selection_model.select(index, QItemSelectionModel.ClearAndSelect)
        self._set_highlighted_column(index.column())
        self.refresh_selected_preview()

    def _show_table_context_menu(self, view: QTableView, pos) -> None:
        index = view.indexAt(pos)
        if index.isValid():
            self._activate_context_index(index)
        menu = self._build_table_context_menu(include_row_actions=True)
        menu.exec(view.viewport().mapToGlobal(pos))

    def _show_header_context_menu(self, view: QTableView, pos) -> None:
        logical_index = view.horizontalHeader().logicalIndexAt(pos)
        if logical_index >= 0:
            self._set_highlighted_column(logical_index)
        menu = self._build_table_context_menu(include_row_actions=False)
        menu.exec(view.horizontalHeader().viewport().mapToGlobal(pos))

    def _show_row_header_context_menu(self, view: QTableView, pos) -> None:
        row_index = view.verticalHeader().logicalIndexAt(pos)
        if row_index >= 0:
            self._activate_context_row(row_index)
        menu = self._build_row_header_context_menu()
        menu.exec(view.verticalHeader().viewport().mapToGlobal(pos))

    def _build_row_header_context_menu(self) -> QMenu:
        menu = QMenu(self)
        has_selection = bool(self._selected_source_rows())

        add_row_action = menu.addAction("행 추가")
        add_row_action.triggered.connect(self.add_row)

        copy_row_action = menu.addAction("선택 행 복사")
        copy_row_action.setEnabled(has_selection)
        copy_row_action.triggered.connect(self.duplicate_selected_rows)

        increment_copy_action = menu.addAction("연속 값 복사")
        increment_copy_action.setEnabled(has_selection)
        increment_copy_action.triggered.connect(self.duplicate_selected_rows_with_increment)

        delete_row_action = menu.addAction("선택 행 삭제")
        delete_row_action.setEnabled(has_selection)
        delete_row_action.triggered.connect(self.delete_selected_rows)
        return menu

    def _activate_context_row(self, proxy_row: int) -> None:
        if proxy_row < 0:
            return
        visible_columns = [index for index, header in enumerate(self.table_model.headers) if header in set(self.visible_headers)]
        target_column = visible_columns[0] if visible_columns else 0
        proxy_index = self.proxy_model.index(proxy_row, target_column)
        if not proxy_index.isValid():
            return
        self.table_view.setCurrentIndex(proxy_index)
        selection_model = self.table_view.selectionModel()
        if selection_model is not None:
            selection_model.setCurrentIndex(proxy_index, QItemSelectionModel.NoUpdate)
            selection_model.select(proxy_index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        self.refresh_selected_preview()

    def _build_table_context_menu(self, include_row_actions: bool) -> QMenu:
        menu = QMenu(self)
        has_cell_selection = bool(self._selected_proxy_indexes())
        has_current_cell = self._current_proxy_index().isValid()
        copy_cell_action = menu.addAction("셀 복사")
        copy_cell_action.setEnabled(has_cell_selection)
        copy_cell_action.triggered.connect(self.copy_selected_cells)
        paste_cell_action = menu.addAction("셀 붙여넣기")
        paste_cell_action.setEnabled(has_current_cell)
        paste_cell_action.triggered.connect(self.paste_clipboard_cells)
        cut_cell_action = menu.addAction("셀 잘라내기")
        cut_cell_action.setEnabled(has_cell_selection)
        cut_cell_action.triggered.connect(self.cut_selected_cells)
        clear_cell_action = menu.addAction("선택 셀 비우기")
        clear_cell_action.setEnabled(has_cell_selection)
        clear_cell_action.triggered.connect(self.clear_selected_cells)
        has_selection = bool(self._selected_source_rows())
        has_active_column = bool(self._active_column_header)
        header_is_pinned = has_active_column and self._active_column_header in set(self.pinned_headers)
        header_is_visible = has_active_column and self._active_column_header in set(self.visible_headers)

        if include_row_actions:
            menu.addSeparator()
            add_row_action = menu.addAction("행 추가")
            add_row_action.triggered.connect(self.add_row)

            copy_row_action = menu.addAction("선택 행 복사")
            copy_row_action.setEnabled(has_selection)
            copy_row_action.triggered.connect(self.duplicate_selected_rows)

            increment_copy_action = menu.addAction("연속 값 복사")
            increment_copy_action.setEnabled(has_selection)
            increment_copy_action.triggered.connect(self.duplicate_selected_rows_with_increment)

            delete_row_action = menu.addAction("선택 행 삭제")
            delete_row_action.setEnabled(has_selection)
            delete_row_action.triggered.connect(self.delete_selected_rows)

            if has_selection:
                menu.addSeparator()
                mark_copied_action = menu.addAction("복사 완료로 표시")
                mark_copied_action.triggered.connect(lambda: self._set_selected_rows_work_state(ROW_STATE_COPIED))

                mark_done_action = menu.addAction("적용 완료로 표시")
                mark_done_action.triggered.connect(self.mark_selected_rows_done)

                reset_state_action = menu.addAction("작업 상태 초기화")
                reset_state_action.triggered.connect(self.reset_selected_rows_work_state)

        if has_active_column:
            if menu.actions():
                menu.addSeparator()

            sort_asc_action = menu.addAction("오름차순 정렬")
            sort_asc_action.triggered.connect(lambda: self._sort_active_column(Qt.AscendingOrder))

            sort_desc_action = menu.addAction("내림차순 정렬")
            sort_desc_action.triggered.connect(lambda: self._sort_active_column(Qt.DescendingOrder))
            clear_sort_action = menu.addAction("정렬 해제")
            clear_sort_action.setEnabled(self._sort_column >= 0)
            clear_sort_action.triggered.connect(self._clear_column_sort)

            hide_column_action = menu.addAction("선택 컬럼 숨기기")
            hide_column_action.setEnabled(header_is_visible)
            hide_column_action.triggered.connect(self.hide_active_table_column)

            pin_column_action = menu.addAction("선택 컬럼 고정")
            pin_column_action.setEnabled(header_is_visible and not header_is_pinned)
            pin_column_action.triggered.connect(self.pin_active_table_column)

            unpin_column_action = menu.addAction("선택 컬럼 고정 해제")
            unpin_column_action.setEnabled(header_is_visible and header_is_pinned)
            unpin_column_action.triggered.connect(self.unpin_active_table_column)

            delete_column_action = menu.addAction("선택 컬럼 삭제")
            delete_column_action.triggered.connect(self.delete_active_table_column)

        return menu

    def _sort_active_column(self, sort_order: Qt.SortOrder) -> None:
        if not self._active_column_header:
            return
        try:
            logical_index = self.table_model.headers.index(self._active_column_header)
        except ValueError:
            return
        self._sort_column = logical_index
        self._sort_order = sort_order
        self.proxy_model.sort(logical_index, sort_order)
        for view in (self.table_view, self.pinned_table_view):
            view.horizontalHeader().setSortIndicator(logical_index, sort_order)

    def _clear_column_sort(self) -> None:
        self._sort_column = -1
        self._sort_order = Qt.AscendingOrder
        self.proxy_model.sort(-1, Qt.AscendingOrder)
        for view in (self.table_view, self.pinned_table_view):
            header = view.horizontalHeader()
            header.setSortIndicatorShown(False)
            header.setSortIndicator(-1, Qt.AscendingOrder)
            header.setSortIndicatorShown(True)

    def hide_active_table_column(self) -> None:
        if not self._active_column_header or self._active_column_header not in self.visible_headers:
            return
        self.visible_headers = [header for header in self.visible_headers if header != self._active_column_header]
        self.pinned_headers = [header for header in self.pinned_headers if header != self._active_column_header]
        self.refresh_pinned_column_list()
        self.apply_pinned_columns()
        self._save_app_state()

    def pin_active_table_column(self) -> None:
        if self._active_column_header:
            self._set_header_pinned_state(self._active_column_header, True)

    def unpin_active_table_column(self) -> None:
        if self._active_column_header:
            self._set_header_pinned_state(self._active_column_header, False)

    def _apply_column_sort(self, logical_index: int) -> None:
        if logical_index < 0 or logical_index >= len(self.table_model.headers):
            return
        if self._sort_column == logical_index:
            if self._sort_order == Qt.AscendingOrder:
                self._sort_order = Qt.DescendingOrder
            else:
                self._clear_column_sort()
                return
        else:
            self._sort_column = logical_index
            self._sort_order = Qt.AscendingOrder
        self.proxy_model.sort(logical_index, self._sort_order)
        for view in (self.table_view, self.pinned_table_view):
            view.horizontalHeader().setSortIndicator(self._sort_column, self._sort_order)

    def _update_template_guide(self) -> None:
        profile = self.profiles.get(self.add_profile_combo.currentText())
        if not profile:
            self._clear_detail_tabs()
            return
        self.profile_title_label.setText(profile.id)
        self.profile_meta_label.setText(f"{profile.vendor} / {profile.model} / {profile.firmware}")
        self.profile_description_label.setText(profile.description_ko or profile.description or "설명 없음")
        increment_summary = ", ".join(profile_increment_fields(profile)) or "없음"
        self.entry_rules_label.setText(f"규칙: 프로파일 ID 필수 · 연속 값 {increment_summary}")
        blank_values = {"profile_id": profile.id, **{name: "" for name in profile.variables}}
        self._fill_profile_reference_table(profile, blank_values)

    def _clear_detail_tabs(self) -> None:
        self.profile_title_label.setText("선택 없음")
        self.profile_meta_label.setText("-")
        self.profile_description_label.setText("장비를 선택하면 프로파일 안내가 표시됩니다.")
        self.entry_rules_label.setText("")
        self.profile_reference_table.setRowCount(0)

    def _update_profile_reference(self, row: dict[str, str]) -> None:
        profile = _find_profile(self.profiles, row.get("profile_id", ""))
        if not profile:
            self.profile_title_label.setText("프로파일 확인 필요")
            self.profile_meta_label.setText(f"프로파일 ID: {row.get('profile_id', '-') or '-'}")
            self.profile_description_label.setText("해당 프로파일 ID의 프로파일을 찾을 수 없습니다.")
            self.entry_rules_label.setText("규칙: 올바른 프로파일 ID 필요")
            self.profile_reference_table.setRowCount(0)
            return
        self.profile_title_label.setText(profile.id)
        self.profile_meta_label.setText(f"{profile.vendor} / {profile.model} / {profile.firmware}")
        self.profile_description_label.setText(profile.description_ko or profile.description or "설명 없음")
        self.entry_rules_label.setText("규칙: 선택 장비 기준")
        self._fill_profile_reference_table(profile, row)

    def _fill_profile_reference_table(self, profile: Profile, row: dict[str, str]) -> None:
        guide_rows = build_profile_reference_rows(profile, row)
        self.profile_reference_table.setRowCount(len(guide_rows))
        for row_index, guide_row in enumerate(guide_rows):
            values = [guide_row["name"], guide_row["current"], guide_row["type"], guide_row["required"], guide_row["default"], guide_row["description"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if guide_row["missing_required"]:
                    item.setBackground(ERROR_BG)
                elif column == 1 and guide_row["current"] == "(비움 -> 기본값 사용)":
                    item.setBackground(DEFAULT_BG)
                self.profile_reference_table.setItem(row_index, column, item)
        self.profile_reference_table.resizeRowsToContents()

    def _update_issue_list(self, row_index: int) -> None:
        self.issue_list.clear()
        issues = self.current_row_issues.get(row_index, [])
        if not issues:
            self.issue_list.addItem("이슈 없음")
            return
        for issue in issues:
            item = QListWidgetItem(issue.message)
            if issue.level == "error":
                item.setBackground(ERROR_BG)
            elif issue.level == "warning":
                item.setBackground(WARNING_BG)
            self.issue_list.addItem(item)

    def _update_profile_effect_label(self, row: dict[str, str]) -> None:
        profile = _find_profile(self.profiles, row.get("profile_id", ""))
        if not profile:
            self.profile_effect_label.setText("요약 불가")
            return
        guide_rows = build_profile_reference_rows(profile, row)
        total = len(guide_rows)
        missing_required = sum(1 for guide_row in guide_rows if guide_row["missing_required"])
        default_used = sum(1 for guide_row in guide_rows if guide_row["current"] == "(비움 -> 기본값 사용)")
        entered = sum(1 for guide_row in guide_rows if guide_row["current"] and guide_row["current"] != "(비움 -> 기본값 사용)")
        increment_fields = profile_increment_fields(profile)
        increment_summary = ", ".join(increment_fields) if increment_fields else "없음"
        self.profile_effect_label.setText(
            f"입력 {entered} · 기본값 {default_used} · 확인 {missing_required} · 전체 {total} · 연속 값 {increment_summary}"
        )

    def _current_source_row(self) -> int | None:
        selected_rows = self._selected_source_rows()
        return selected_rows[0] if selected_rows else None

    def _select_source_row(self, row_index: int) -> bool:
        if row_index < 0 or row_index >= len(self.table_model.rows):
            return False
        source_index = self.table_model.index(row_index, 0)
        if not source_index.isValid():
            return False
        proxy_index = self.proxy_model.mapFromSource(source_index)
        if not proxy_index.isValid():
            return False
        self.table_view.setCurrentIndex(proxy_index)
        selection_model = self.table_view.selectionModel()
        if selection_model is not None:
            selection_model.setCurrentIndex(proxy_index, QItemSelectionModel.NoUpdate)
            selection_model.select(proxy_index, QItemSelectionModel.ClearAndSelect)
        self.table_view.scrollTo(proxy_index)
        self.refresh_selected_preview()
        return True

    def _select_relative_visible_row(self, offset: int) -> bool:
        current_source_row = self._current_source_row()
        if current_source_row is None:
            if self.proxy_model.rowCount() == 0:
                return False
            target_proxy_row = 0 if offset >= 0 else self.proxy_model.rowCount() - 1
        else:
            current_proxy_index = self.proxy_model.mapFromSource(self.table_model.index(current_source_row, 0))
            if not current_proxy_index.isValid():
                return False
            target_proxy_row = current_proxy_index.row() + offset
        if target_proxy_row < 0 or target_proxy_row >= self.proxy_model.rowCount():
            return False
        target_source_row = self.proxy_model.mapToSource(self.proxy_model.index(target_proxy_row, 0)).row()
        return self._select_source_row(target_source_row)

    def _update_navigation_buttons(self) -> None:
        if not hasattr(self, "previous_device_button"):
            return
        current_source_row = self._current_source_row()
        if current_source_row is None or self.proxy_model.rowCount() == 0:
            self.previous_device_button.setEnabled(False)
            self.next_device_button.setEnabled(self.proxy_model.rowCount() > 0)
            self.copy_cli_button.setEnabled(current_source_row is not None)
            self.copy_next_cli_button.setEnabled(current_source_row is not None)
            self.select_cli_button.setEnabled(bool(self.cli_preview.toPlainText().strip()))
            self.mark_done_button.setEnabled(current_source_row is not None)
            self.reset_work_state_button.setEnabled(current_source_row is not None)
            return
        current_proxy_index = self.proxy_model.mapFromSource(self.table_model.index(current_source_row, 0))
        has_previous = current_proxy_index.isValid() and current_proxy_index.row() > 0
        has_next = current_proxy_index.isValid() and current_proxy_index.row() < self.proxy_model.rowCount() - 1
        has_cli = bool(self.current_rendered.get(current_source_row))
        self.previous_device_button.setEnabled(has_previous)
        self.next_device_button.setEnabled(has_next)
        self.copy_cli_button.setEnabled(has_cli)
        self.copy_next_cli_button.setEnabled(has_cli)
        self.select_cli_button.setEnabled(bool(self.cli_preview.toPlainText().strip()))
        self.mark_done_button.setEnabled(True)
        self.reset_work_state_button.setEnabled(True)

    def _copy_row_cli(self, row_index: int) -> bool:
        rendered = self.current_rendered.get(row_index)
        if not rendered:
            QMessageBox.information(self, "CLI 복사", "현재 선택 장비의 CLI를 생성할 수 없습니다.")
            return False
        QApplication.clipboard().setText(rendered.text)
        self._set_row_work_state(row_index, ROW_STATE_COPIED)
        self.table_model.set_issue_map(self.current_row_issues)
        self.apply_filter()
        device_label = row_display_name(self.table_model.rows[row_index], row_index + 2)
        self.statusBar().showMessage(f"{device_label} CLI를 클립보드에 복사했습니다.", 3000)
        self._refresh_tutorial_dialog()
        return True

    def mark_selected_rows_done(self) -> None:
        self._set_selected_rows_work_state(ROW_STATE_DONE)

    def reset_selected_rows_work_state(self) -> None:
        self._set_selected_rows_work_state(ROW_STATE_PENDING)

    def copy_selected_cli(self) -> None:
        row_index = self._current_source_row()
        if row_index is None:
            QMessageBox.information(self, "CLI 복사", "복사할 장비 행을 먼저 선택하세요.")
            return
        self._copy_row_cli(row_index)

    def copy_selected_cli_and_advance(self) -> None:
        row_index = self._current_source_row()
        if row_index is None:
            QMessageBox.information(self, "CLI 복사", "복사할 장비 행을 먼저 선택하세요.")
            return
        if not self._copy_row_cli(row_index):
            return
        if not self._select_relative_visible_row(1):
            self.statusBar().showMessage("마지막 장비까지 복사했습니다.", 3000)

    def select_cli_preview_text(self) -> None:
        self.cli_preview.setFocus()
        self.cli_preview.selectAll()

    def save_selected_cli(self) -> None:
        source_rows = self._selected_source_rows()
        if not source_rows:
            return
        row_index = source_rows[0]
        rendered = self.current_rendered.get(row_index)
        if not rendered:
            QMessageBox.information(self, "CLI 저장", "현재 선택 장비의 CLI를 생성할 수 없습니다.")
            return
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        target_name = self._rendered_output_filename(row_index, rendered)
        target_path, _ = QFileDialog.getSaveFileName(self, "선택 CLI 저장", str(OUTPUT_DIR / target_name), "Text Files (*.txt)")
        if target_path:
            Path(target_path).write_text(rendered.text, encoding="utf-8")
            self._append_activity_log(f"선택 CLI 저장: {target_name} -> {target_path}")
            self.statusBar().showMessage(f"{Path(target_path).name} 저장 완료", 3000)

    def save_all_cli(self) -> None:
        if not self.current_rendered:
            QMessageBox.information(self, "CLI 저장", "저장할 CLI가 없습니다.")
            return
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        target_path, _ = QFileDialog.getSaveFileName(self, "전체 CLI 저장", str(OUTPUT_DIR / "all_devices.txt"), "Text Files (*.txt)")
        if target_path:
            Path(target_path).write_text(build_bundle_text(self.current_rendered.values()), encoding="utf-8")
            self._append_activity_log(f"전체 CLI 저장: {len(self.current_rendered)}건 -> {target_path}")
            self.statusBar().showMessage(f"{Path(target_path).name} 저장 완료", 3000)

    def save_each_cli(self) -> None:
        if not self.current_rendered:
            QMessageBox.information(self, "CLI 저장", "저장할 CLI가 없습니다.")
            return
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        target_dir = QFileDialog.getExistingDirectory(self, "장비별 TXT 저장", str(OUTPUT_DIR))
        if not target_dir:
            return
        saved_count = 0
        for row_index, rendered in self.current_rendered.items():
            target_path = Path(target_dir) / self._rendered_output_filename(row_index, rendered)
            target_path.write_text(rendered.text, encoding="utf-8")
            saved_count += 1
        self._append_activity_log(f"장비별 CLI 저장: {saved_count}건 -> {target_dir}")
        self.statusBar().showMessage(f"장비별 TXT {saved_count}건 저장 완료", 3000)

    def _rendered_output_filename(self, row_index: int, rendered: RenderedConfig) -> str:
        row_number = row_index + 2
        row = self.table_model.rows[row_index] if 0 <= row_index < len(self.table_model.rows) else {}
        display_name = str(rendered.display_name or "").strip() or row_display_name(row, row_number)
        if display_name == f"row-{row_number}":
            display_name = ""
        safe_name = re.sub(r"[\\\\/:*?\"<>|]+", "_", display_name).strip()
        safe_name = re.sub(r"\s+", "_", safe_name)
        safe_name = re.sub(r"_+", "_", safe_name).strip("_")
        if safe_name:
            return f"{safe_name}.txt"
        profile_part = str(row.get("profile_id") or rendered.profile_id or "profile").strip()
        profile_part = re.sub(r"[\\\\/:*?\"<>|]+", "_", profile_part)
        profile_part = re.sub(r"\s+", "_", profile_part)
        profile_part = re.sub(r"_+", "_", profile_part).strip("_") or "profile"
        return f"{profile_part}_row_{row_number}.txt"

    def open_new_profile_dialog(self) -> None:
        dialog = ProfileBuilderDialog(self.profile_dir, None, self)
        if dialog.exec():
            self._reload_after_profile_save(dialog.saved_profile_id)

    def clone_current_profile_dialog(self) -> None:
        profile = self._selected_template_profile()
        if not profile:
            QMessageBox.information(self, "프로파일 복사", "복사할 프로파일을 먼저 선택하세요.")
            return
        cloned = Profile(
            id=f"{profile.id}_COPY",
            vendor=profile.vendor,
            model=profile.model,
            firmware=profile.firmware,
            description=profile.description,
            description_ko=profile.description_ko,
            variables=dict(profile.variables),
            blocks=list(profile.blocks),
            source="",
        )
        dialog = ProfileBuilderDialog(self.profile_dir, cloned, self)
        dialog.setWindowTitle("프로파일 복사")
        if dialog.exec():
            self._reload_after_profile_save(dialog.saved_profile_id)

    def edit_current_profile_dialog(self) -> None:
        profile = self._selected_template_profile()
        if not profile:
            QMessageBox.information(self, "프로파일 편집", "편집할 프로파일을 먼저 선택하세요.")
            return
        dialog = ProfileBuilderDialog(self.profile_dir, profile, self)
        if dialog.exec():
            self._reload_after_profile_save(dialog.saved_profile_id or profile.id)

    def delete_current_profile_dialog(self) -> None:
        profile = self._selected_template_profile()
        if not profile:
            QMessageBox.information(self, "프로파일 삭제", "삭제할 프로파일을 먼저 선택하세요.")
            return
        profile_path = Path(profile.source).resolve() if profile.source else None
        profile_root = self.profile_dir.resolve()
        if profile_path is None or not profile_path.exists():
            QMessageBox.warning(self, "프로파일 삭제", "선택한 프로파일 파일을 찾을 수 없습니다.")
            return
        if profile_root not in profile_path.parents:
            QMessageBox.warning(self, "프로파일 삭제", "프로파일 폴더에 있는 프로파일만 삭제할 수 있습니다.")
            return
        referenced_rows = [
            row_display_name(row, row_index + 2)
            for row_index, row in enumerate(self.table_model.rows)
            if _normalize_profile_id(str(row.get("profile_id", ""))) == _normalize_profile_id(profile.id)
        ]
        reference_note = ""
        if referenced_rows:
            reference_note = (
                f"\n\n현재 장비 설정 정보에서 {len(referenced_rows)}개 행이 이 프로파일을 참조합니다."
                f"\n예: {', '.join(referenced_rows[:5])}"
            )
        confirmed = QMessageBox.question(
            self,
            "프로파일 삭제",
            f"{profile.id} 프로파일을 삭제하시겠습니까?\n파일: {profile_path.name}{reference_note}",
        )
        if confirmed != QMessageBox.Yes:
            return
        profile_path.unlink(missing_ok=False)
        self.reload_profiles()
        self.refresh_render_state()
        self.refresh_selected_preview()
        self._append_activity_log(f"프로파일 삭제: {profile.id}")
        self.statusBar().showMessage(f"{profile_path.name} 프로파일 삭제 완료", 3000)

    def _reload_after_profile_save(self, profile_id: str) -> None:
        self.reload_profiles()
        if profile_id and profile_id in self.profiles:
            self.add_profile_combo.setCurrentText(profile_id)
        self.refresh_render_state()
        self.refresh_selected_preview()
        self._refresh_tutorial_dialog()
        if profile_id:
            self._append_activity_log(f"프로파일 저장/갱신: {profile_id}")

    def _selected_template_profile(self) -> Profile | None:
        return self.profiles.get(self.add_profile_combo.currentText())

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.auto_save_check.isChecked() and self.current_file_path and self.is_dirty and self._save_to_path(self.current_file_path, autosave=True):
            self._save_app_state()
            event.accept()
            return
        if self._confirm_discard_changes():
            self._save_app_state()
            event.accept()
        else:
            event.ignore()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.schedule_auto_resize()

    def _confirm_discard_changes(self) -> bool:
        if not self.is_dirty:
            return True
        return QMessageBox.question(self, "저장되지 않은 변경", "저장되지 않은 변경 사항이 있습니다. 계속 진행하시겠습니까?") == QMessageBox.Yes

    def _current_proxy_index(self) -> QModelIndex:
        selection_model = self.table_view.selectionModel()
        if selection_model is None:
            return QModelIndex()
        current_index = selection_model.currentIndex()
        if current_index.isValid():
            return current_index
        selected_indexes = selection_model.selectedIndexes()
        return selected_indexes[0] if selected_indexes else QModelIndex()

    def _selected_proxy_indexes(self) -> list[QModelIndex]:
        selection_model = self.table_view.selectionModel()
        if selection_model is None:
            return []
        indexes = [index for index in selection_model.selectedIndexes() if index.isValid()]
        if not indexes:
            current_index = selection_model.currentIndex()
            if current_index.isValid():
                indexes = [current_index]
        return sorted(indexes, key=lambda index: (index.row(), index.column()))

    def _selection_bounds(self, indexes: list[QModelIndex]) -> tuple[int, int, int, int] | None:
        if not indexes:
            return None
        rows = [index.row() for index in indexes]
        columns = [index.column() for index in indexes]
        return min(rows), max(rows), min(columns), max(columns)

    def _proxy_row_to_source_row(self, proxy_row: int) -> int | None:
        proxy_index = self.proxy_model.index(proxy_row, 0)
        if not proxy_index.isValid():
            return None
        source_index = self.proxy_model.mapToSource(proxy_index)
        return source_index.row() if source_index.isValid() else None

    def _append_blank_rows(self, count: int) -> None:
        if count <= 0:
            return
        current_row_index = self._current_source_row()
        current_profile = None
        if current_row_index is not None and 0 <= current_row_index < len(self.table_model.rows):
            current_profile = _find_profile(self.profiles, self.table_model.rows[current_row_index].get("profile_id", ""))
        elif self.add_profile_combo.currentText():
            current_profile = self.profiles.get(self.add_profile_combo.currentText())
        rows = [make_blank_table_row(self.table_model.headers, current_profile) for _ in range(count)]
        self.table_model.append_rows(rows)

    def copy_selected_cells(self) -> None:
        indexes = self._selected_proxy_indexes()
        bounds = self._selection_bounds(indexes)
        if bounds is None:
            return
        min_row, max_row, min_col, max_col = bounds
        value_map = {(index.row(), index.column()): str(index.data(Qt.EditRole) or "") for index in indexes}
        lines: list[str] = []
        for row in range(min_row, max_row + 1):
            values = [value_map.get((row, column), "") for column in range(min_col, max_col + 1)]
            lines.append("\t".join(values))
        QApplication.clipboard().setText("\n".join(lines))
        self.statusBar().showMessage("선택 셀을 클립보드에 복사했습니다.", 3000)

    def clear_selected_cells(self) -> None:
        indexes = self._selected_proxy_indexes()
        changes: list[tuple[int, int, Any]] = []
        for proxy_index in indexes:
            source_index = self.proxy_model.mapToSource(proxy_index)
            if source_index.isValid():
                changes.append((source_index.row(), source_index.column(), ""))
        changed = self.table_model.apply_cell_changes(changes)
        if changed:
            self.statusBar().showMessage(f"선택 셀 {changed}개를 비웠습니다.", 3000)

    def cut_selected_cells(self) -> None:
        indexes = self._selected_proxy_indexes()
        if not indexes:
            return
        self.copy_selected_cells()
        self.clear_selected_cells()

    def paste_clipboard_cells(self) -> None:
        clipboard_text = QApplication.clipboard().text()
        if not clipboard_text:
            return
        rows = [line.split("\t") for line in clipboard_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        if rows and rows[-1] == [""]:
            rows = rows[:-1]
        if not rows:
            return

        selected_indexes = self._selected_proxy_indexes()
        current_index = self._current_proxy_index()
        if not current_index.isValid():
            return

        changes: list[tuple[int, int, Any]] = []
        if len(rows) == 1 and len(rows[0]) == 1 and len(selected_indexes) > 1:
            value = rows[0][0]
            for proxy_index in selected_indexes:
                source_index = self.proxy_model.mapToSource(proxy_index)
                if source_index.isValid():
                    changes.append((source_index.row(), source_index.column(), value))
        else:
            max_target_row = current_index.row() + len(rows) - 1
            append_count = max(0, max_target_row - (self.proxy_model.rowCount() - 1))
            if append_count:
                self._append_blank_rows(append_count)
            for row_offset, values in enumerate(rows):
                target_proxy_row = current_index.row() + row_offset
                source_row = self._proxy_row_to_source_row(target_proxy_row)
                if source_row is None:
                    continue
                for column_offset, value in enumerate(values):
                    target_column = current_index.column() + column_offset
                    if target_column >= self.proxy_model.columnCount():
                        break
                    source_index = self.proxy_model.mapToSource(self.proxy_model.index(target_proxy_row, target_column))
                    if source_index.isValid():
                        changes.append((source_index.row(), source_index.column(), value))
        changed = self.table_model.apply_cell_changes(changes)
        if changed:
            self.statusBar().showMessage(f"클립보드 내용을 셀 {changed}개에 붙여넣었습니다.", 3000)

    def fill_selection_to_target(self, target_proxy_index: QModelIndex) -> None:
        if not target_proxy_index.isValid():
            return
        indexes = self._selected_proxy_indexes()
        bounds = self._selection_bounds(indexes)
        if bounds is None:
            return
        min_row, max_row, min_col, max_col = bounds
        source_height = max_row - min_row + 1
        source_width = max_col - min_col + 1
        target_min_row = min(min_row, target_proxy_index.row())
        target_max_row = max(max_row, target_proxy_index.row())
        target_min_col = min(min_col, target_proxy_index.column())
        target_max_col = max(max_col, target_proxy_index.column())
        if target_min_row == min_row and target_max_row == max_row and target_min_col == min_col and target_max_col == max_col:
            return

        max_target_row = target_max_row
        append_count = max(0, max_target_row - (self.proxy_model.rowCount() - 1))
        if append_count:
            self._append_blank_rows(append_count)

        source_values = {
            (index.row(), index.column()): str(index.data(Qt.EditRole) or "")
            for index in indexes
        }
        single_cell = len(source_values) == 1
        base_value = next(iter(source_values.values())) if single_cell else ""
        changes: list[tuple[int, int, Any]] = []
        for target_row in range(target_min_row, target_max_row + 1):
            source_row = self._proxy_row_to_source_row(target_row)
            if source_row is None:
                continue
            for target_col in range(target_min_col, target_max_col + 1):
                if min_row <= target_row <= max_row and min_col <= target_col <= max_col:
                    continue
                source_index = self.proxy_model.mapToSource(self.proxy_model.index(target_row, target_col))
                if not source_index.isValid():
                    continue
                if single_cell:
                    if target_col != min_col:
                        continue
                    offset = target_row - min_row
                    changes.append((source_index.row(), source_index.column(), increment_spreadsheet_value(base_value, offset)))
                    continue
                source_row_offset = (target_row - min_row) % source_height
                source_col_offset = (target_col - min_col) % source_width
                pattern_row = min_row + source_row_offset
                pattern_col = min_col + source_col_offset
                value = source_values.get((pattern_row, pattern_col), "")
                changes.append((source_index.row(), source_index.column(), value))
        changed = self.table_model.apply_cell_changes(changes)
        if changed:
            self.statusBar().showMessage(f"연속 채우기를 셀 {changed}개에 적용했습니다.", 3000)

    def _selected_source_rows(self) -> list[int]:
        selection_model = self.table_view.selectionModel()
        if selection_model is None:
            return []
        proxy_rows = sorted({index.row() for index in selection_model.selectedRows()})
        if not proxy_rows:
            proxy_rows = sorted({index.row() for index in selection_model.selectedIndexes()})
        if not proxy_rows:
            current_index = selection_model.currentIndex()
            if current_index.isValid():
                proxy_rows = [current_index.row()]
        return [self.proxy_model.mapToSource(self.proxy_model.index(row, 0)).row() for row in proxy_rows]

    def _sync_column_width_from_main(self, section: int, _: int, new_size: int) -> None:
        if self._syncing_width or self.table_view.isColumnHidden(section):
            return
        self._syncing_width = True
        self.pinned_table_view.setColumnWidth(section, new_size)
        self._syncing_width = False
        self._update_pinned_view_width()

    def _sync_column_width_from_pinned(self, section: int, _: int, new_size: int) -> None:
        if self._syncing_width or self.pinned_table_view.isColumnHidden(section):
            return
        self._syncing_width = True
        self.table_view.setColumnWidth(section, new_size)
        self._syncing_width = False
        self._update_pinned_view_width()

    def _sync_column_move_from_main(self, logical_index: int, _: int, new_visual_index: int) -> None:
        if self._syncing_section_move:
            return
        pinned_count = len([header for header in self.column_order if header in self.visible_headers and header in self.pinned_headers])
        header_name = self.table_model.headers[logical_index] if 0 <= logical_index < len(self.table_model.headers) else ""
        self._syncing_section_move = True
        try:
            header = self.pinned_table_view.horizontalHeader()
            current_visual_index = header.visualIndex(logical_index)
            if current_visual_index != new_visual_index:
                header.moveSection(current_visual_index, new_visual_index)
        finally:
            self._syncing_section_move = False
        self._remember_current_column_order()
        if header_name and pinned_count > 0 and new_visual_index < pinned_count:
            self._set_header_pinned_state(header_name, True)
        else:
            self.refresh_pinned_column_list()

    def _sync_column_move_from_pinned(self, logical_index: int, _: int, new_visual_index: int) -> None:
        if self._syncing_section_move:
            return
        pinned_count = len([header for header in self.column_order if header in self.visible_headers and header in self.pinned_headers])
        header_name = self.table_model.headers[logical_index] if 0 <= logical_index < len(self.table_model.headers) else ""
        self._syncing_section_move = True
        try:
            header = self.table_view.horizontalHeader()
            current_visual_index = header.visualIndex(logical_index)
            if current_visual_index != new_visual_index:
                header.moveSection(current_visual_index, new_visual_index)
        finally:
            self._syncing_section_move = False
        self._remember_current_column_order()
        if header_name and new_visual_index >= pinned_count:
            self._set_header_pinned_state(header_name, False)
        else:
            self.refresh_pinned_column_list()

    def _update_save_status_label(self) -> None:
        if not hasattr(self, "save_status_label"):
            return
        issue_rows = sum(1 for issues in self.current_row_issues.values() if any(issue.level == "error" for issue in issues))
        if not self.current_file_path:
            text = "파일을 열면 저장 상태를 표시합니다."
            color = "#22303c"
            background = "#eef2f6"
        elif not self.auto_save_check.isChecked():
            text = "수동 저장 모드입니다."
            color = "#5a4314"
            background = "#fff8e4"
        elif issue_rows and self.allow_error_autosave_check.isChecked():
            text = f"오류 {issue_rows}행 포함 상태로 실시간 저장 중입니다."
            color = "#7a2e1c"
            background = "#fff1ed"
        elif issue_rows:
            text = f"오류 {issue_rows}행 때문에 자동 저장이 보류될 수 있습니다."
            color = "#7a2e1c"
            background = "#fff1ed"
        else:
            text = "오류 없이 실시간 저장 중입니다."
            color = "#204227"
            background = "#eef8eb"
        self.save_status_label.setText(text)
        self.save_status_label.setStyleSheet(
            f"padding: 4px 6px; border-radius: 6px; background: {background}; color: {color};"
        )

    def _update_summary(self) -> None:
        total_rows = len(self.table_model.rows)
        visible_rows = self.proxy_model.rowCount()
        ready_count = len(self.current_rendered)
        issue_rows = sum(1 for issues in self.current_row_issues.values() if any(issue.level == "error" for issue in issues))
        copied_count = sum(1 for state in self.row_work_state.values() if state == ROW_STATE_COPIED)
        done_count = sum(1 for state in self.row_work_state.values() if state == ROW_STATE_DONE)
        save_mode = "실시간 저장" if self.auto_save_check.isChecked() and self.current_file_path else "수동 저장"
        dirty = " / 저장 필요" if self.is_dirty else ""
        self.summary_label.setText(
            f"프로파일 {len(self.profiles)}개 / 전체 {total_rows}행 / 필터 결과 {visible_rows}행 / CLI 생성 가능 {ready_count}행 / 확인 필요 {issue_rows}행 / 복사 완료 {copied_count}행 / 적용 완료 {done_count}행 / {save_mode}{dirty}"
        )
        self._update_save_status_label()
        self._update_navigation_buttons()
        self.statusBar().showMessage(self.summary_label.text())


def main() -> None:
    set_windows_app_id()
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(build_app_icon())
    window = DesktopWindow()
    window.show()
    app.exec()
