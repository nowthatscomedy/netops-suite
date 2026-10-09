from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from netops_suite.ui.actions import ActionKind, make_action_button
from netops_suite.ui.icons import PAGE_ICONS, icon


class HomePage(QWidget):
    navigate_requested = Signal(str)
    TASKS = (
        ("interface", "내 PC 네트워크", "어댑터의 현재 주소를 확인하고 IP를 변경합니다."),
        ("diagnostics", "연결 진단", "Ping·포트·DNS·경로로 연결 상태를 확인합니다."),
        ("wireless", "Wi-Fi 확인", "연결 신호와 주변 AP·채널을 살펴봅니다."),
        ("inspector", "장비 점검·백업", "내 프로파일로 장비를 점검하고 설정을 백업합니다."),
        ("config_builder", "설정 명령 만들기", "변수 입력으로 CLI를 만들고 복사·저장합니다."),
        ("transfer", "파일 전송", "장비와 파일을 주고받거나 파일 서버를 엽니다."),
    )
    ACCENTS = ("#2563eb", "#7554cc", "#0d9488", "#2563eb", "#7554cc", "#c47b11")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("homePage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(16)
        hero = QFrame()
        hero.setObjectName("homeHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(22, 18, 22, 18)
        hero_layout.setSpacing(5)
        eyebrow = QLabel("NETOPS WORKSPACE")
        eyebrow.setObjectName("heroEyebrow")
        title = QLabel("네트워크 작업, 한곳에서.")
        title.setObjectName("homeHeroTitle")
        title.setWordWrap(True)
        description = QLabel("필요한 도구를 선택하고 바로 시작하세요.")
        description.setObjectName("homeHeroBody")
        hero_layout.addWidget(eyebrow)
        hero_layout.addWidget(title)
        hero_layout.addWidget(description)
        # Shared page-header contract used by the keyboard/layout harness.
        header = QWidget()
        header.setObjectName("pageHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(hero)
        layout.addWidget(header)
        layout.addWidget(self._build_first_steps())
        self.grid = QGridLayout()
        self.grid.setSpacing(14)
        self.cards: list[QFrame] = []
        self.task_buttons: dict[str, QPushButton] = {}
        for index, (key, heading, description) in enumerate(self.TASKS):
            card = QFrame()
            card.setObjectName("taskCard")
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 15, 16, 15)
            card_layout.setSpacing(10)
            title_row = QHBoxLayout()
            title_row.setSpacing(11)
            tile = QLabel()
            tile.setObjectName("taskIconTile")
            tile.setPixmap(icon(PAGE_ICONS[key], self.ACCENTS[index], 24).pixmap(24, 24))
            tile.setFixedSize(44, 44)
            tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label = QLabel(heading)
            label.setObjectName("taskCardTitle")
            label.setWordWrap(True)
            title_row.addWidget(tile)
            title_row.addWidget(label, 1)
            card_layout.addLayout(title_row)
            body = QLabel(description)
            body.setObjectName("taskCardDescription")
            body.setWordWrap(True)
            body.setMinimumHeight(38)
            card_layout.addWidget(body)
            button = make_action_button("열기", ActionKind.SECONDARY, object_name=f"homeTask_{key}")
            button.setIcon(icon("arrow-up-right", self.ACCENTS[index], 16))
            button.setAccessibleName(f"{heading} 열기")
            button.clicked.connect(lambda _checked=False, page_key=key: self.navigate_requested.emit(page_key))
            card_layout.addWidget(button)
            self.cards.append(card)
            self.task_buttons[key] = button
        layout.addLayout(self.grid)
        layout.addStretch(1)
        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area)
        self._columns = 0
        self._reflow(3)

    FIRST_STEPS = (
        ("할 일 고르기", "아래 카드에서 하려는 작업의 열기를 누릅니다."),
        ("위에서부터 차례로", "각 화면은 1 → 2 → 3 순서로 입력하고 실행합니다."),
        ("막히면 도움말", "화면 오른쪽 위 도움말이나 F1 키로 단계별 안내를 봅니다."),
    )

    def _build_first_steps(self) -> QFrame:
        """A three-step orientation strip for first-time users."""
        strip = QFrame()
        strip.setObjectName("homeFirstSteps")
        row = QHBoxLayout(strip)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(18)
        intro = QLabel("처음이라면")
        intro.setObjectName("homeFirstStepsTitle")
        row.addWidget(intro, 0, Qt.AlignmentFlag.AlignVCenter)
        self.first_step_labels: list[QLabel] = []
        for number, (heading, description) in enumerate(self.FIRST_STEPS, start=1):
            step = QHBoxLayout()
            step.setSpacing(8)
            badge = QLabel(str(number))
            badge.setObjectName("homeStepBadge")
            badge.setFixedSize(24, 24)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text = QLabel(f"<b>{heading}</b><br>{description}")
            text.setObjectName("homeStepText")
            text.setWordWrap(True)
            step.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            step.addWidget(text, 1)
            row.addLayout(step, 1)
            self.first_step_labels.append(text)
        return strip

    def _reflow(self, columns: int) -> None:
        if self._columns == columns:
            return
        self._columns = columns
        for card in self.cards:
            self.grid.removeWidget(card)
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)
        for column in range(3):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow(3 if self.width() >= 760 else 2)
