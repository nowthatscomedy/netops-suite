from __future__ import annotations

import ast
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea, QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget

from app.ui.common.theme import APP_STYLE_SHEET
from netops_suite.ui.selection_inputs import NoWheelComboBox


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _PopupTrackingCombo(NoWheelComboBox):
    def __init__(self) -> None:
        super().__init__()
        self.popup_calls = 0

    def showPopup(self) -> None:
        self.popup_calls += 1


def _wheel_event(delta: int) -> QWheelEvent:
    return QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )


def test_combo_ignores_wheel_but_keeps_keyboard_and_popup_click(qapp):
    combo = _PopupTrackingCombo()
    combo.addItems(["첫 번째", "두 번째", "세 번째"])
    combo.setCurrentIndex(1)
    combo.resize(180, 32)
    combo.show()
    combo.setFocus()
    qapp.processEvents()
    try:
        for delta in (120, -120):
            event = _wheel_event(delta)
            QCoreApplication.sendEvent(combo, event)
            assert combo.currentIndex() == 1
            assert not event.isAccepted()

        QTest.keyClick(combo, Qt.Key.Key_Down)
        assert combo.currentIndex() == 2
        QTest.keyClick(combo, Qt.Key.Key_Up)
        assert combo.currentIndex() == 1

        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        arrow_rect = combo.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxArrow,
            combo,
        )
        assert arrow_rect.isValid()
        QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=arrow_rect.center())
        assert combo.popup_calls == 1
    finally:
        combo.close()


def test_combo_wheel_continues_scrolling_parent(qapp):
    scroll = QScrollArea()
    scroll.resize(240, 120)
    content = QWidget()
    layout = QVBoxLayout(content)
    combos = []
    for _ in range(20):
        combo = NoWheelComboBox()
        combo.addItems(["전체", "2.4GHz", "5GHz"])
        combo.setCurrentIndex(1)
        layout.addWidget(combo)
        combos.append(combo)
    scroll.setWidget(content)
    scroll.setWidgetResizable(True)
    scroll.show()
    qapp.processEvents()
    try:
        visible_combo = combos[2]
        position = scroll.window().mapFromGlobal(visible_combo.mapToGlobal(visible_combo.rect().center()))
        before_index = visible_combo.currentIndex()
        before_scroll = scroll.verticalScrollBar().value()

        QTest.wheelEvent(scroll.windowHandle(), position, QPoint(0, -120))
        qapp.processEvents()

        assert visible_combo.currentIndex() == before_index
        assert scroll.verticalScrollBar().value() > before_scroll
    finally:
        scroll.close()


def test_open_combo_popup_keeps_its_own_wheel_scrolling(qapp):
    combo = NoWheelComboBox()
    combo.addItems([f"항목 {index}" for index in range(100)])
    combo.resize(180, 32)
    combo.show()
    qapp.processEvents()
    combo.showPopup()
    qapp.processEvents()
    try:
        popup_view = combo.view()
        popup_scroll = popup_view.verticalScrollBar()
        popup_window = popup_view.window()
        popup_handle = popup_window.windowHandle()
        position = popup_window.mapFromGlobal(
            popup_view.viewport().mapToGlobal(popup_view.viewport().rect().center())
        )
        before_index = combo.currentIndex()

        QTest.wheelEvent(popup_handle, position, QPoint(0, -120))
        qapp.processEvents()

        assert popup_scroll.value() > 0
        assert combo.currentIndex() == before_index
    finally:
        combo.hidePopup()
        combo.close()


def test_combo_chevron_is_visible_with_app_theme(qapp):
    combo = NoWheelComboBox()
    combo.addItem("전체")
    combo.resize(180, 32)
    try:
        combo.setStyleSheet(APP_STYLE_SHEET)
        combo.show()
        qapp.processEvents()

        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        arrow_rect = combo.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxArrow,
            combo,
        )
        assert arrow_rect.width() == 24
        pixmap = combo.grab()
        image = pixmap.toImage()
        scale = pixmap.devicePixelRatio()
        center_x = round(arrow_rect.center().x() * scale)
        center_y = round(arrow_rect.center().y() * scale)
        dark_pixels = 0
        for x in range(center_x - 5, center_x + 6):
            for y in range(center_y - 4, center_y + 5):
                if image.pixelColor(x, y).lightness() < 180:
                    dark_pixels += 1

        assert combo.property("netopsChevron") is True
        assert 'QComboBox[netopsChevron="true"]::down-arrow' in APP_STYLE_SHEET
        assert dark_pixels >= 6
    finally:
        combo.close()


def test_ui_does_not_construct_raw_qt_combo_boxes():
    shared_widget_path = PROJECT_ROOT / "netops_suite" / "ui" / "selection_inputs.py"
    offenders: list[str] = []

    for root in (PROJECT_ROOT / "app", PROJECT_ROOT / "netops_suite"):
        for path in root.rglob("*.py"):
            if path == shared_widget_path:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            imported_names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "PySide6.QtWidgets":
                    imported_names.update(
                        alias.asname or alias.name for alias in node.names if alias.name == "QComboBox"
                    )

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                calls_raw_name = isinstance(function, ast.Name) and function.id in imported_names
                calls_raw_attribute = isinstance(function, ast.Attribute) and function.attr == "QComboBox"
                if calls_raw_name or calls_raw_attribute:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")

    assert offenders == []
