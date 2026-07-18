from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.app_state import AppState
from app.main_window import MainWindow
from app.ui.common.theme import APP_STYLE_SHEET
from app.ui.tabs.ai_chat_tab import AiChatTab
from app.ui.tabs.interface_tab import InterfaceTab


def _disable_external_startup(monkeypatch) -> None:
    monkeypatch.setattr(
        AiChatTab,
        "refresh_provider_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        AiChatTab,
        "_ensure_model_catalog_fresh",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        MainWindow,
        "_maybe_check_updates_on_startup",
        lambda *_args, **_kwargs: None,
    )


def test_view_button_tab_focus_enters_each_current_page(
    qapp,
    tmp_path,
    monkeypatch,
):
    _disable_external_startup(monkeypatch)
    state = AppState(tmp_path)
    window = MainWindow(state)
    try:
        window.resize(1280, 800)
        window.show()
        qapp.processEvents()

        expected_targets = (
            window.interface_tab.refresh_button,
            window.diagnostics_tab.quick_target_edit,
            window.wireless_tab.refresh_button,
            window.inspector_tab.profile_editor_button,
            window.config_builder_tab.full_editor_button,
            window.ai_chat_tab.prompt_edit,
            window.settings_tab.section_tabs.tabBar(),
        )
        for index, expected in enumerate(expected_targets):
            window.tab_widget.setCurrentIndex(index)
            qapp.processEvents()
            window.view_button.setFocus(Qt.FocusReason.OtherFocusReason)
            QTest.keyClick(window.view_button, Qt.Key.Key_Tab)
            qapp.processEvents()

            assert QApplication.focusWidget() is expected
            assert window.tab_widget.currentWidget().isAncestorOf(expected)
    finally:
        window.close()


def test_keyboard_focus_styles_cover_navigation_tabs_and_buttons():
    assert "QListWidget#mainNavigation::item:selected:focus" in APP_STYLE_SHEET
    assert "QTabBar::tab:selected:focus" in APP_STYLE_SHEET
    assert "QPushButton:focus,\nQToolButton:focus" in APP_STYLE_SHEET
    assert "border: 2px solid #475467;" in APP_STYLE_SHEET


def test_main_window_stops_delayed_startup_update_check_on_quick_close(
    qapp,
    tmp_path,
    monkeypatch,
):
    _disable_external_startup(monkeypatch)
    state = AppState(tmp_path)
    window = MainWindow(state)

    assert window._startup_update_timer.isActive()

    window.shutdown()

    assert not window._startup_update_timer.isActive()
    window.close()


def test_interface_compact_layout_hides_auxiliary_columns_and_keeps_actions_visible(
    qapp,
    tmp_path,
):
    state = AppState(tmp_path)
    tab = InterfaceTab(state)
    try:
        tab.resize(816, 620)
        tab.show()
        qapp.processEvents()

        assert tab.adapter_table.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert {
            column
            for column in range(tab.adapter_table.columnCount())
            if tab.adapter_table.isColumnHidden(column)
        } == {1, 5, 6, 7}
        for button in (
            tab.profile_apply_button,
            tab.profile_add_button,
            tab.profile_edit_button,
            tab.profile_delete_button,
        ):
            assert button.isVisible()
            assert button.y() < tab.profile_list.y()

        tab.resize(1400, 800)
        qapp.processEvents()

        assert all(
            not tab.adapter_table.isColumnHidden(column)
            for column in range(tab.adapter_table.columnCount())
        )
    finally:
        tab.close()
        state.shutdown()
