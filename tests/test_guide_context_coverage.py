from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from app.app_state import AppState
from app.main_window import MainWindow
from app.ui.tabs.ai_chat_tab import AiChatTab


def _disable_external_startup(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_actual_ui_contexts_have_exact_registered_guide_coverage(
    qapp,
    tmp_path,
    monkeypatch,
):
    _disable_external_startup(monkeypatch)
    state = AppState(tmp_path)
    window = MainWindow(state)
    try:
        main_contexts = window.main_guide_context_ids()
        assert len(main_contexts) == window.tab_widget.count()
        assert main_contexts == window._MAIN_GUIDE_IDS
        assert len(set(main_contexts)) == window.tab_widget.count()
        for index, guide_id in enumerate(main_contexts):
            assert window.tab_widget.widget(index) is not None
            assert window.guide_catalog.get(guide_id) is not None

        diagnostic_contexts = window.diagnostic_guide_contexts()
        actual_tool_keys = set(window.diagnostics_tab._diagnostic_tool_keys)
        assert actual_tool_keys == set(window._DIAGNOSTIC_GUIDE_IDS)
        assert diagnostic_contexts == window._DIAGNOSTIC_GUIDE_IDS
        for guide_id in diagnostic_contexts.values():
            assert window.guide_catalog.get(guide_id) is not None

        transfer_contexts = window.transfer_guide_context_ids()
        registered_transfer_contexts = {
            entry.id
            for entry in window.guide_catalog.entries
            if entry.parent_id == "diagnostics.transfer"
        }
        assert transfer_contexts == registered_transfer_contexts
        assert window.guide_context_coverage_errors() == ()
    finally:
        window.close()
        state.shutdown()


def test_context_coverage_reports_new_unmapped_ui_entries(
    qapp,
    tmp_path,
    monkeypatch,
):
    _disable_external_startup(monkeypatch)
    state = AppState(tmp_path)
    window = MainWindow(state)
    extra_page = QWidget(window)
    try:
        window.tab_widget.addTab(extra_page, "Unmapped page")
        window.diagnostics_tab._diagnostic_tool_keys.append("future-tool")
        mode_combo = window.diagnostics_tab.file_transfer_mode_combo
        mode_combo.addItem("Future transfer", 99)

        errors = window.guide_context_coverage_errors()

        assert any("main tab count" in error for error in errors)
        assert any("future-tool" in error for error in errors)
        assert any("transfer mode" in error for error in errors)
    finally:
        window.diagnostics_tab._diagnostic_tool_keys.remove("future-tool")
        window.tab_widget.removeTab(window.tab_widget.indexOf(extra_page))
        extra_page.deleteLater()
        window.close()
        state.shutdown()
