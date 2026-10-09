from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, QThreadPool, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QWidget

from app.ui.common.theme import apply_app_theme
from app.ui.tabs.inspector_tab import InspectorTab


def _settle(qapp):
    for _ in range(4):
        qapp.processEvents()


def _rect_in(widget, parent):
    return QRect(widget.mapTo(parent, QPoint()), widget.size())


@pytest.fixture
def inspector_workspace(qapp, tmp_path, request):
    original_style = qapp.styleSheet()
    original_font = QFont(qapp.font())
    apply_app_theme(qapp)
    window = QMainWindow()
    central = QWidget()
    body = QHBoxLayout(central)
    body.setContentsMargins(0, 0, 0, 0)
    navigation = QWidget()
    navigation.setFixedWidth(220)
    body.addWidget(navigation)
    tab = InspectorTab(SimpleNamespace(
        thread_pool=QThreadPool.globalInstance(),
        paths=SimpleNamespace(data_root=tmp_path),
        app_config={},
    ))
    body.addWidget(tab, 1)
    window.setCentralWidget(central)
    window.resize(*getattr(request, "param", (1024, 680)))
    window.show()
    _settle(qapp)
    yield tab
    window.close()
    qapp.setStyleSheet(original_style)
    qapp.setFont(original_font)


def _click_section(qapp, tab, section):
    button = section.toggle_button
    tab.top_scroll.ensureWidgetVisible(button, 0, button.height())
    _settle(qapp)
    assert tab.top_scroll.viewport().rect().contains(_rect_in(button, tab.top_scroll.viewport()))
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    _settle(qapp)


@pytest.mark.parametrize("inspector_workspace", [(1024, 680), (1280, 800)], indirect=True)
def test_supporting_tools_stay_grouped_and_separate_from_validation(qapp, inspector_workspace):
    tab = inspector_workspace
    tools = tab.supporting_tools_group
    sections = (tab.inventory_format_section, tab.execution_options_section, tab.profile_section)
    assert tab.supporting_tools_title.text() == "보조 도구"
    assert all(not section.isExpanded() for section in sections)
    assert all(tools.isAncestorOf(section) for section in sections)
    assert not tools.isAncestorOf(tab.validation_group)
    assert tab.validation_group.isAncestorOf(tab.validate_button)
    assert tab.validation_group.isAncestorOf(tab.run_button)
    for button in (tab.validate_button, tab.run_button):
        assert tab.top_scroll.viewport().rect().contains(_rect_in(button, tab.top_scroll.viewport()))
    icons = tools.findChildren(QLabel, "inspectorSupportingIcon")
    assert len(icons) == 3
    assert all(not label.pixmap().isNull() for label in icons)

    for _ in range(3):
        for section in sections:
            _click_section(qapp, tab, section)
        for section in sections:
            assert section.isExpanded()
        buttons = [section.toggle_button for section in sections]
        assert len({button.mapTo(tools, QPoint()).x() for button in buttons}) == 1
        assert len({button.width() for button in buttons}) == 1
        for section, button in zip(sections, buttons):
            assert tools.rect().contains(_rect_in(button, tools))
            assert section.toggle_button.arrowType() == Qt.ArrowType.DownArrow
        panel = tab.top_scroll.widget()
        assert _rect_in(tools, panel).top() - _rect_in(tab.validation_group, panel).bottom() >= 12
        assert tab.top_scroll.horizontalScrollBar().maximum() == 0
        for button in (tab.validate_button, tab.run_button):
            tab.top_scroll.ensureWidgetVisible(button, 0, button.height())
            _settle(qapp)
            assert tab.top_scroll.viewport().rect().contains(_rect_in(button, tab.top_scroll.viewport()))
        for section in reversed(sections):
            _click_section(qapp, tab, section)
            assert not section.isExpanded()
        assert len({button.mapTo(tools, QPoint()).x() for button in buttons}) == 1
        assert len({button.width() for button in buttons}) == 1


def test_supporting_tools_preserve_inputs_and_report_changed_options(qapp, inspector_workspace):
    tab = inspector_workspace
    tab.inventory_path_edit.setText("approved-devices.xlsx")
    tab.inventory_password_check.setChecked(True)
    tab.inventory_password_edit.setText("session-only")
    _click_section(qapp, tab, tab.execution_options_section)
    tab.max_workers_spin.setValue(7)
    tab.timeout_spin.setValue(25)
    tab.retry_spin.setValue(2)
    tab.output_name_edit.setText("verified-results.xlsx")
    tab._inventory_validated = True

    for _ in range(3):
        _click_section(qapp, tab, tab.execution_options_section)
        _click_section(qapp, tab, tab.inventory_format_section)
        _click_section(qapp, tab, tab.profile_section)
    assert not tab.execution_options_section.isExpanded()
    assert "변경된 옵션 4개" in tab.execution_options_section.toggle_button.text()
    assert tab.inventory_path_edit.text() == "approved-devices.xlsx"
    assert tab.inventory_password_edit.text() == "session-only"
    assert (tab.max_workers_spin.value(), tab.timeout_spin.value(), tab.retry_spin.value()) == (7, 25, 2)
    assert tab.output_name_edit.text() == "verified-results.xlsx"
    assert tab._inventory_validated
    assert not tab._inspector_running
