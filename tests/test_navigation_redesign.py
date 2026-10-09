from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from app.app_state import AppState
from app.main_window import MainWindow
from app.ui.common.disclosure import CollapsibleSection


@pytest.fixture
def workspace(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "_maybe_check_updates_on_startup", lambda *_args: None)
    state = AppState(tmp_path)
    window = MainWindow(state)
    window.show()
    qapp.processEvents()
    yield window
    window.close()


def test_new_user_starts_at_home_and_cards_only_navigate(workspace, monkeypatch):
    window = workspace
    assert window.tab_widget.currentWidget() is window.home_page
    starts = []
    monkeypatch.setattr(window.diagnostics_tab, "_start_worker", lambda *a, **kw: starts.append(a))
    for key, button in window.home_page.task_buttons.items():
        window.navigate_to("home")
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert window.tab_widget.currentWidget().property("mainPageKey") == key
    assert starts == []
    assert window._guide_dialog is None
    assert not window.help_dock.isVisible()


def test_menu_home_cards_and_page_titles_use_the_same_names(workspace):
    from PySide6.QtWidgets import QLabel

    window = workspace
    nav_titles = {
        window.nav_list.item(row).data(Qt.ItemDataRole.UserRole): window.nav_list.item(row).text()
        for row in range(window.nav_list.count())
    }
    home_titles = {key: heading for key, heading, _description in window.home_page.TASKS}
    for key, heading in home_titles.items():
        assert nav_titles[key] == heading
    assert nav_titles["inspector"] == "장비 점검·백업"
    assert window.inspector_tab.findChild(QLabel, "pageTitle").text() == nav_titles["inspector"]
    assert window.settings_tab.findChild(QLabel, "pageTitle").text() == "설정"


def test_transfer_server_pages_start_at_the_top(workspace, qapp):
    from PySide6.QtWidgets import QSplitter

    window = workspace
    window.resize(1280, 800)
    window.navigate_to("transfer")
    tab = window.diagnostics_tab
    tab.file_transfer_role_combo.setCurrentIndex(1)
    for mode in range(tab.file_transfer_mode_combo.count()):
        tab.file_transfer_mode_combo.setCurrentIndex(mode)
        qapp.processEvents()
        page = tab.file_transfer_page_stack.currentWidget()
        splitter = page.findChild(QSplitter)
        assert splitter.geometry().top() <= 8, mode


def test_target_hints_sit_directly_under_their_inputs(workspace, qapp):
    window = workspace
    window.resize(1280, 800)
    tab = window.diagnostics_tab
    for key in ("ping", "tcp"):
        window.navigate_to("diagnostics", key)
        qapp.processEvents()
        qapp.processEvents()
        edit = getattr(tab, f"{key}_targets_edit")
        hint = getattr(tab, f"{key}_targets_help_label")
        assert hint.height() == hint.heightForWidth(hint.width())
        assert hint.geometry().top() - edit.geometry().bottom() <= 8


def test_interface_empty_state_names_the_real_refresh_button(workspace):
    tab = workspace.interface_tab
    assert tab.refresh_button.text() in tab.adapter_empty_label.text()


def test_navigation_preserves_transfer_session_widgets_and_inputs(workspace):
    window = workspace
    tab = window.diagnostics_tab
    original = window.transfer_tab.transfer_page
    tab.ping_targets_edit.setPlainText("192.0.2.10")
    for _ in range(3):
        assert window.navigate_to("transfer")
        assert original.isVisibleTo(window)
        assert window.navigate_to("diagnostics", "ping")
    assert window.transfer_tab.transfer_page is original
    assert tab.ping_targets_edit.toPlainText() == "192.0.2.10"
    assert window.navigate_to("unknown") is False


def test_settings_section_change_does_not_navigate_when_focus_falls_back(workspace, qapp):
    window = workspace
    window.navigate_to("settings")
    window.settings_tab.show_section("storage")
    window.settings_tab.path_open_buttons["exports_dir"].setFocus()
    qapp.processEvents()
    window.settings_tab.show_section("maintenance")
    qapp.processEvents()
    assert window.tab_widget.currentWidget() is window.settings_tab
    assert window.settings_tab.reset_all_settings_button.isVisible()


@pytest.mark.parametrize("saved,expected", [
    ({"current_tab": 0}, "interface"),
    ({"current_tab": 1}, "diagnostics"),
    ({"current_tab": 2}, "wireless"),
    ({"current_tab": 3}, "inspector"),
    ({"current_tab": 4}, "config_builder"),
    ({"current_tab": 5}, "home"),
    ({"current_tab": 6}, "settings"),
    ({"current_tab": 7}, "settings"),
    ({"current_tab": "invalid"}, "home"),
    ({"current_page_key": "assistant", "current_tab": 5}, "home"),
    ({"current_page_key": "wireless", "current_tab": 0}, "wireless"),
])
def test_saved_workspace_migration(workspace, saved, expected):
    window = workspace
    window.state.app_config["ui_state"] = {"main_window": saved}
    window._restore_ui_state()
    assert window.tab_widget.currentWidget().property("mainPageKey") == expected


def test_legacy_transfer_restores_to_independent_workspace(workspace):
    window = workspace
    window.state.app_config["ui_state"] = {
        "main_window": {"current_page_key": "diagnostics"},
        "diagnostics_tab": {"current_tool_key": "transfer"},
        "extension": {"keep": True},
    }
    window._restore_ui_state()
    assert window.tab_widget.currentWidget() is window.transfer_tab
    window._save_ui_state()
    saved = window.state.get_ui_state()
    assert saved["main_window"]["current_page_key"] == "transfer"
    assert "current_tab" not in saved["main_window"]
    assert saved["extension"] == {"keep": True}


def test_context_help_docks_or_floats_and_returns_focus(workspace, qapp):
    window = workspace
    window.navigate_to("diagnostics", "ping")
    target = window.diagnostics_tab.ping_targets_edit
    target.setFocus()
    window.resize(1600, 900)
    qapp.processEvents()
    assert window.show_context_help()
    qapp.processEvents()
    assert not window.help_dock.isFloating()
    assert window.quick_help_panel.current_entry.id == "diagnostics.ping"
    window.navigate_to("transfer")
    assert window.quick_help_panel.current_entry.id == window._current_transfer_guide_id()
    window.resize(1024, 680)
    qapp.processEvents()
    qapp.processEvents()
    assert window.help_dock.isFloating()
    window.help_dock.hide()
    window.navigate_to("diagnostics", "ping")
    target.setFocus()
    window.show_context_help()
    window.help_dock.hide()
    qapp.processEvents()
    assert QApplication.focusWidget() is target


def test_changed_hidden_options_remain_visible_in_summary(qapp):
    section = CollapsibleSection("실행 옵션")
    option = QLineEdit("4")
    section.content_layout.addWidget(option)
    section.watch(option)
    option.setText("8")
    assert not section.isExpanded()
    assert "변경된 옵션 1개" in section.toggle_button.text()
    section.setExpanded(True)
    section.setExpanded(False)
    assert option.text() == "8"
    option.setText("4")
    assert section.toggle_button.text() == "실행 옵션"
    section.close()


def test_detached_builder_help_uses_active_editor_and_restores_its_focus(workspace, qapp):
    window = workspace
    window.navigate_to("config_builder")
    tab = window.config_builder_tab
    tab._open_full_editor()
    editor = tab._builder_window
    window.navigate_to("interface")
    target = tab.builder_widget.add_profile_combo
    editor.activateWindow()
    target.setFocus()
    qapp.processEvents()
    assert window.current_guide_id() == "config-builder"
    assert window.show_context_help()
    qapp.processEvents()
    assert window.quick_help_panel.current_entry.id == "config-builder"
    assert window.help_dock.isFloating()
    window.help_dock.hide()
    qapp.processEvents()
    assert QApplication.focusWidget() is target
    editor.close()


def test_every_work_page_has_a_visible_help_button_that_opens_context_help(workspace, qapp):
    from PySide6.QtWidgets import QPushButton

    window = workspace
    window.resize(1280, 800)
    for key in ("interface", "diagnostics", "wireless", "inspector", "config_builder", "transfer", "settings"):
        window.navigate_to(key)
        qapp.processEvents()
        page = window.tab_widget.currentWidget()
        buttons = [
            button
            for button in page.findChildren(QPushButton, "pageHelpButton")
            if button.isVisible()
        ]
        assert len(buttons) == 1, key
        window.help_dock.hide()
        QTest.mouseClick(buttons[0], Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert window.help_dock.isVisible(), key
        assert window.quick_help_panel.current_entry is not None


def test_home_explains_the_first_three_steps(workspace):
    steps = workspace.home_page.first_step_labels
    assert len(steps) == 3
    assert "도움말" in steps[2].text() and "F1" in steps[2].text()


@pytest.mark.parametrize("size", [(1024, 680), (1270, 825), (1600, 900)])
def test_header_help_and_profile_buttons_line_up(workspace, qapp, size):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QPushButton

    window = workspace
    window.resize(*size)
    pairs = (
        ("inspector", window.inspector_tab, lambda tab: tab.profile_editor_button),
        ("config_builder", window.config_builder_tab, lambda tab: tab.profile_management_button),
    )
    for key, tab, action in pairs:
        window.navigate_to(key)
        qapp.processEvents()
        help_button = tab.findChild(QPushButton, "pageHelpButton")
        profile_button = action(tab)
        help_top = help_button.mapTo(tab, QPoint()).y()
        profile_top = profile_button.mapTo(tab, QPoint()).y()
        assert help_button.height() == profile_button.height(), key
        assert help_top == profile_top, key
