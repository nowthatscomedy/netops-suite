from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSpinBox, QScrollArea, QVBoxLayout, QWidget

from netops_suite.ui.numeric_inputs import NoWheelDoubleSpinBox, NoWheelSpinBox


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


@pytest.mark.parametrize("spin_type", [NoWheelSpinBox, NoWheelDoubleSpinBox])
def test_numeric_input_ignores_wheel_hides_steppers_and_keeps_keyboard(spin_type, qapp):
    spin = spin_type()
    try:
        spin.setRange(0, 10)
        spin.setValue(5)
        spin.show()
        spin.setFocus()

        QTest.keyClick(spin, Qt.Key.Key_Up)
        assert spin.value() == 6

        for delta in (120, -120):
            event = _wheel_event(delta)
            QCoreApplication.sendEvent(spin, event)
            assert spin.value() == 6
            assert not event.isAccepted()

        assert spin.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
    finally:
        spin.close()


def test_numeric_input_wheel_continues_scrolling_parent(qapp):
    scroll = QScrollArea()
    scroll.resize(240, 120)
    content = QWidget()
    layout = QVBoxLayout(content)
    spins = []
    for _ in range(20):
        spin = NoWheelSpinBox()
        spin.setValue(10)
        layout.addWidget(spin)
        spins.append(spin)
    scroll.setWidget(content)
    scroll.setWidgetResizable(True)
    scroll.show()
    qapp.processEvents()

    try:
        visible_spin = spins[2]
        position = scroll.window().mapFromGlobal(visible_spin.mapToGlobal(visible_spin.rect().center()))
        before_value = visible_spin.value()
        before_scroll = scroll.verticalScrollBar().value()

        QTest.wheelEvent(scroll.windowHandle(), position, QPoint(0, -120))
        qapp.processEvents()

        assert visible_spin.value() == before_value
        assert scroll.verticalScrollBar().value() > before_scroll
    finally:
        scroll.close()


def test_ui_does_not_construct_raw_qt_spin_boxes():
    raw_types = {"QSpinBox", "QDoubleSpinBox"}
    shared_widget_path = PROJECT_ROOT / "netops_suite" / "ui" / "numeric_inputs.py"
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
                        alias.asname or alias.name for alias in node.names if alias.name in raw_types
                    )

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                calls_raw_name = isinstance(function, ast.Name) and function.id in imported_names
                calls_raw_attribute = isinstance(function, ast.Attribute) and function.attr in raw_types
                if calls_raw_name or calls_raw_attribute:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")

    assert offenders == []
