from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QTimer, Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)


class StartupLoadingWindow(QWidget):
    """Visible startup progress window shown before the main shell is ready."""

    def __init__(self, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._steps = [
            "실행 환경 확인",
            "테마와 아이콘 준비",
            "설정 파일 준비",
            "서비스 초기화",
            "메인 화면 구성",
            "시작 데이터 갱신",
        ]
        self._current_step = 0
        self._elapsed = QElapsedTimer()
        self._elapsed.start()

        self.setObjectName("startupLoadingWindow")
        self.setWindowTitle("NetOps Suite 시작 준비")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(640, 460)
        self.setMinimumSize(560, 420)

        self._build_ui(version)
        self._apply_styles()
        self._sync_step_items()

    def _build_ui(self, version: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("startupHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 18, 22, 16)
        header_layout.setSpacing(14)

        title_stack = QVBoxLayout()
        title_stack.setContentsMargins(0, 0, 0, 0)
        title_stack.setSpacing(4)
        self.title_label = QLabel("NetOps Suite")
        self.title_label.setObjectName("startupTitle")
        self.subtitle_label = QLabel("네트워크 도구와 작업 환경을 준비하고 있습니다.")
        self.subtitle_label.setObjectName("startupSubtitle")
        self.subtitle_label.setWordWrap(True)
        title_stack.addWidget(self.title_label)
        title_stack.addWidget(self.subtitle_label)

        version_label = QLabel(f"v{version}")
        version_label.setObjectName("startupVersion")
        version_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        header_layout.addLayout(title_stack, 1)
        header_layout.addWidget(version_label)
        root.addWidget(header)

        body = QFrame()
        body.setObjectName("startupBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(22, 18, 22, 20)
        body_layout.setSpacing(12)

        self.stage_label = QLabel("시작 준비 중")
        self.stage_label.setObjectName("startupStage")
        self.stage_label.setWordWrap(True)
        self.detail_label = QLabel("잠시만 기다려 주세요.")
        self.detail_label.setObjectName("startupDetail")
        self.detail_label.setWordWrap(True)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("startupProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        self.step_list = QListWidget()
        self.step_list.setObjectName("startupStepList")
        self.step_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.step_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.step_list.setMinimumHeight(150)
        self.step_list.setMaximumHeight(180)
        for step in self._steps:
            item = QListWidgetItem(step)
            item.setData(Qt.ItemDataRole.UserRole, step)
            self.step_list.addItem(item)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("startupLog")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(30)
        self.log_view.setMinimumHeight(96)
        self.log_view.setMaximumHeight(118)

        body_layout.addWidget(self.stage_label)
        body_layout.addWidget(self.detail_label)
        body_layout.addWidget(self.progress_bar)
        body_layout.addWidget(self.step_list)
        body_layout.addWidget(self.log_view)
        root.addWidget(body, 1)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget#startupLoadingWindow {
                background: #fbfbfa;
            }
            QFrame#startupHeader {
                background: #f2f1ee;
                border-bottom: 1px solid #e4e2dd;
            }
            QFrame#startupBody {
                background: #fbfbfa;
            }
            QLabel#startupTitle {
                color: #111827;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#startupSubtitle,
            QLabel#startupVersion,
            QLabel#startupDetail {
                color: #667085;
            }
            QLabel#startupStage {
                color: #182230;
                font-size: 14px;
                font-weight: 700;
            }
            QProgressBar#startupProgress {
                background: #eef2f6;
                border: 1px solid #d9e2ec;
                border-radius: 5px;
                min-height: 12px;
                max-height: 12px;
                text-align: center;
            }
            QProgressBar#startupProgress::chunk {
                background: #2563eb;
                border-radius: 5px;
            }
            QListWidget#startupStepList,
            QPlainTextEdit#startupLog {
                background: #ffffff;
                border: 1px solid #e4e7ec;
                border-radius: 4px;
            }
            QListWidget#startupStepList::item {
                border-radius: 4px;
                padding: 6px 8px;
                margin: 1px 2px;
            }
            QPlainTextEdit#startupLog {
                color: #475467;
                padding: 7px;
            }
            """
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.move(available.center() - self.rect().center())

    def set_step(self, index: int, message: str, detail: str = "", progress: int | None = None) -> None:
        self._current_step = max(0, min(index, len(self._steps) - 1))
        self.stage_label.setText(message)
        self.detail_label.setText(detail or "초기화 작업을 진행하고 있습니다.")
        if progress is None:
            progress = int((self._current_step / max(1, len(self._steps) - 1)) * 100)
        self.progress_bar.setValue(max(0, min(progress, 100)))
        self._append_log(message, detail)
        self._sync_step_items()
        QApplication.processEvents()

    def complete(self, message: str = "시작 준비가 완료되었습니다.") -> None:
        self._current_step = len(self._steps) - 1
        self.stage_label.setText(message)
        self.detail_label.setText("메인 화면을 여는 중입니다.")
        self.progress_bar.setValue(100)
        self._append_log(message, "메인 화면을 표시합니다.")
        self._sync_step_items(complete_all=True)
        QApplication.processEvents()

    def fail(self, message: str, detail: str = "") -> None:
        self.stage_label.setText(message)
        self.detail_label.setText(detail or "시작 중 오류가 발생했습니다.")
        self.progress_bar.setValue(100)
        self._append_log(message, detail)
        QApplication.processEvents()

    def finish_after_minimum(self, minimum_msec: int = 900) -> None:
        remaining = max(0, minimum_msec - self._elapsed.elapsed())
        if remaining:
            QTimer.singleShot(remaining, self.close)
            return
        self.close()

    def _append_log(self, message: str, detail: str = "") -> None:
        elapsed = self._elapsed.elapsed() / 1000
        line = f"{elapsed:0.1f}s  {message}"
        if detail:
            line = f"{line} - {detail}"
        self.log_view.appendPlainText(line)

    def _sync_step_items(self, *, complete_all: bool = False) -> None:
        for row in range(self.step_list.count()):
            item = self.step_list.item(row)
            label = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if complete_all or row < self._current_step:
                status = "완료"
                color = QColor("#166534")
                weight = QFont.Weight.DemiBold
            elif row == self._current_step:
                status = "진행"
                color = QColor("#1d4ed8")
                weight = QFont.Weight.Bold
            else:
                status = "대기"
                color = QColor("#667085")
                weight = QFont.Weight.Normal

            item.setText(f"[{status}] {label}")
            item.setForeground(QBrush(color))
            font = item.font()
            font.setWeight(weight)
            item.setFont(font)
