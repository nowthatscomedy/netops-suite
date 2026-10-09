from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, QThreadPool, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QLabel, QMessageBox, QPushButton, QWidget

from app.models.network_models import NetworkAdapterInfo
from app.models.profile_models import IPProfile
from app.models.result_models import OperationResult
from app.ui.tabs.interface_tab import InterfaceTab
from app.ui.tabs.config_builder_tab import ConfigBuilderTab
from app.ui.tabs.inspector_tab import InspectorTab
from app.ui.tabs.wireless_tab import WirelessTab
from netops_suite.modules.config_builder.switch_configurator import desktop_impl
from netops_suite.modules.config_builder.switch_configurator.models import RenderedConfig


def interface_state(*, admin=True):
    return SimpleNamespace(
        is_admin=admin,
        ip_profiles=[],
        config_reloaded=SimpleNamespace(connect=lambda *_: None),
        admin_status_changed=SimpleNamespace(connect=lambda *_: None),
        thread_pool=QThreadPool.globalInstance(),
    )


def test_ip_mode_disclosure_preserves_manual_values_and_confirmation(qapp, monkeypatch):
    tab = InterfaceTab(interface_state())
    tab._populate_adapter_table([
        NetworkAdapterInfo("Ethernet", "Test adapter", "", "Up", ipv4="192.0.2.10", prefix_length=24)
    ])
    tab.ip_edit.setText("192.0.2.20")
    tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("dhcp"))
    assert tab.static_fields.isHidden()
    assert "192.0.2.10" in tab.current_settings_label.text()
    tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("static"))
    assert not tab.static_fields.isHidden()
    assert tab.ip_edit.text() == "192.0.2.20"
    tab.profile_section.setExpanded(True)
    tab.profile_section.setExpanded(False)
    assert tab.ip_edit.text() == "192.0.2.20"
    calls = []
    monkeypatch.setattr("app.ui.tabs.interface_tab.confirm_risky_action", lambda *_a, **_k: False)
    monkeypatch.setattr(tab, "_start_worker", lambda *_a, **_k: calls.append(True))
    tab.apply_current_settings()
    assert calls == []


def test_ip_admin_action_emits_request_without_applying(qapp):
    tab = InterfaceTab(interface_state(admin=False))
    requests = []
    tab.admin_requested.connect(lambda: requests.append(True))
    tab.admin_restart_button.click()
    assert requests == [True]
    assert not tab.apply_button.isEnabled()


@pytest.mark.parametrize("apply_kind", ["dhcp", "static", "profile"])
@pytest.mark.parametrize("outcome", ["success", "failure", "exception"])
def test_ip_apply_locks_both_actions_and_recovers_after_completion(qapp, monkeypatch, apply_kind, outcome):
    pending, confirmations, warnings = [], [], []
    state = interface_state()
    state.ip_profiles = [IPProfile("Saved", mode="dhcp", interface_name="Ethernet")]
    state.thread_pool = SimpleNamespace(start=pending.append)
    def apply_settings(*_args):
        if outcome == "exception":
            raise RuntimeError("apply failed")
        return OperationResult(outcome == "success", "operation finished")
    state.network_interface_service = SimpleNamespace(
        set_dhcp=apply_settings, set_static=apply_settings, apply_profile=apply_settings)
    tab = InterfaceTab(state)
    tab._populate_adapter_table([
        NetworkAdapterInfo("Ethernet", "Test", "", "Up", ipv4="192.0.2.10", prefix_length=24)
    ])
    tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("dhcp" if apply_kind == "dhcp" else "static"))
    monkeypatch.setattr(tab, "refresh_adapters", lambda: None)
    monkeypatch.setattr(tab, "_confirm_apply", lambda *_a, **_k: confirmations.append(True) or True)
    monkeypatch.setattr("app.ui.tabs.interface_tab.QMessageBox.warning", lambda *_a: warnings.append(True))
    (tab.apply_selected_profile if apply_kind == "profile" else tab.apply_current_settings)()
    assert tab._network_change_running
    assert not tab.apply_button.isEnabled()
    assert not tab.profile_apply_button.isEnabled()
    tab.apply_current_settings()
    tab.apply_selected_profile()
    assert len(pending) == 1
    assert len(confirmations) == 1
    pending.pop().run()
    assert not tab._network_change_running
    assert tab.apply_button.isEnabled()
    assert tab.profile_apply_button.isEnabled()
    assert warnings == ([] if outcome == "success" else [True])


def test_ip_refresh_completion_does_not_release_apply_lock(qapp, monkeypatch):
    pending = []
    state = interface_state()
    state.thread_pool = SimpleNamespace(start=pending.append)
    adapter = NetworkAdapterInfo("Ethernet", "Test", "", "Up", dhcp_enabled=True)
    state.network_interface_service = SimpleNamespace(
        list_adapters=lambda: [adapter], set_dhcp=lambda *_a: OperationResult(True, "done"))
    tab = InterfaceTab(state)
    tab._populate_adapter_table([adapter])
    monkeypatch.setattr(tab, "_confirm_apply", lambda *_a, **_k: True)
    tab.refresh_adapters()
    assert not tab._network_change_running
    assert tab.apply_button.isEnabled()
    tab.apply_current_settings()
    assert tab._network_change_running
    pending.pop(0).run()
    assert tab._network_change_running
    assert not tab.apply_button.isEnabled()
    pending.pop(0).run()
    assert not tab._network_change_running
    assert tab.apply_button.isEnabled()


def test_ip_apply_pool_rejection_releases_lock(qapp, monkeypatch):
    state = interface_state()
    def reject_worker(_worker):
        raise RuntimeError("pool unavailable")
    state.thread_pool = SimpleNamespace(start=reject_worker)
    state.network_interface_service = SimpleNamespace(set_dhcp=lambda *_a: None)
    tab = InterfaceTab(state)
    tab._populate_adapter_table([NetworkAdapterInfo("Ethernet", "Test", "", "Up", dhcp_enabled=True)])
    monkeypatch.setattr(tab, "_confirm_apply", lambda *_a, **_k: True)
    monkeypatch.setattr("app.ui.tabs.interface_tab.QMessageBox.warning", lambda *_a: None)
    tab.apply_current_settings()
    assert not tab._network_change_running
    assert tab.apply_button.isEnabled()


@pytest.mark.parametrize("field,value", [("ip_edit", "invalid"), ("prefix_edit", "99"), ("gateway_edit", "invalid"), ("dns_edit", "invalid")])
def test_ip_invalid_input_stays_inline_and_focuses_field(qapp, monkeypatch, field, value):
    tab = InterfaceTab(interface_state())
    tab._populate_adapter_table([NetworkAdapterInfo("Ethernet", "Test", "", "Up", ipv4="192.0.2.10", prefix_length=24)])
    tab.show()
    qapp.processEvents()
    edit = getattr(tab, field)
    if field == "dns_edit":
        edit.setPlainText(value)
    else:
        edit.setText(value)
    confirmations, modals = [], []
    monkeypatch.setattr(tab, "_confirm_apply", lambda *_a, **_k: confirmations.append(True))
    monkeypatch.setattr("app.ui.tabs.interface_tab.QMessageBox.warning", lambda *_a: modals.append(True))
    tab.apply_current_settings()
    assert tab.form_status_label.isVisibleTo(tab)
    assert "수정한 뒤 다시 검토" in tab.form_status_label.text()
    assert edit.hasFocus()
    assert confirmations == []
    assert modals == []
    assert not tab._network_change_running


def test_wireless_details_and_filters_remain_available_and_restore_values(qapp):
    state = SimpleNamespace(app_config={}, thread_pool=QThreadPool.globalInstance(),
                            oui_service=SimpleNamespace(cache_summary=lambda: "OUI cache"))
    tab = WirelessTab(state)
    tab.resize(1024, 680)
    tab.show()
    qapp.processEvents()
    assert tab.status_grid.count() == 4
    assert tab.status_detail_grid.count() == 6
    assert tab.info_labels["ssid"].isVisibleTo(tab)
    assert not tab.info_labels["bssid"].isVisibleTo(tab)
    tab.status_details_section.setExpanded(True)
    assert tab.info_labels["bssid"].isVisibleTo(tab)
    tab.nearby_options_section.setExpanded(True)
    tab.nearby_band_filter.setCurrentIndex(tab.nearby_band_filter.findData("5"))
    tab.nearby_interval_spin.setValue(45)
    tab.nearby_options_section.setExpanded(False)
    assert "변경된 옵션 2개" in tab.nearby_options_section.toggle_button.text()
    saved = tab.save_ui_state()
    restored = WirelessTab(state)
    restored.restore_ui_state(saved)
    assert restored.nearby_band_filter.currentData() == "5"
    assert restored.nearby_interval_spin.value() == 45
    assert "변경된 옵션 2개" in restored.nearby_options_section.toggle_button.text()


class InventoryService:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.passwords = []

    def load_inventory(self, _path, password):
        self.passwords.append(password)
        if self.fail:
            raise ValueError("필수 컬럼이 없습니다.")
        return [{"ip": "192.0.2.1"}]

    def inventory_profile_warnings(self, _devices):
        return []


def inspector_tab(tmp_path):
    return InspectorTab(SimpleNamespace(
        thread_pool=QThreadPool.globalInstance(),
        paths=SimpleNamespace(data_root=tmp_path),
        app_config={},
    ))


def test_inspector_profile_creation_is_direct_and_revalidates_inventory(qapp, tmp_path, monkeypatch):
    calls = []
    class ProfileDialog:
        def __init__(self, service, parent, **kwargs):
            calls.append((service, parent))
        def exec(self):
            calls.append("opened")
    monkeypatch.setattr("app.ui.tabs.inspector_tab.InspectorProfileDialog", ProfileDialog)
    tab = inspector_tab(tmp_path)
    tab.show()
    qapp.processEvents()
    assert not tab.profile_section.isExpanded()
    assert tab.profile_editor_button.isVisibleTo(tab)
    assert tab.profile_editor_button.text() == "프로파일 만들기·관리"
    tab.inventory_path_edit.setText("devices.xlsx")
    tab._inventory_validated = True
    tab.profile_editor_button.click()
    assert calls == [(tab.service, tab), "opened"]
    assert not tab._inventory_validated
    assert tab.inventory_path_edit.text() == "devices.xlsx"
    tab._inspector_running = True
    tab._set_run_controls_locked(True)
    tab.profile_editor_button.click()
    tab._open_profile_editor()
    assert len(calls) == 2
    tab._inspector_running = False
    tab._set_run_controls_locked(False)
    assert tab.profile_editor_button.isEnabled()


def test_inspector_expanded_inventory_guide_aligns_and_scrolls_at_small_size(qapp, tmp_path):
    tab = inspector_tab(tmp_path)
    tab.resize(1024, 680)
    tab.show()
    tab.inventory_format_section.setExpanded(True)
    tab.profile_section.setExpanded(True)
    tab.execution_options_section.setExpanded(True)
    qapp.processEvents()
    assert tab.size().width() == 1024
    assert tab.top_scroll.horizontalScrollBar().maximum() == 0
    assert tab.top_scroll.verticalScrollBar().maximum() > 0
    assert tab.profile_editor_button.isVisibleTo(tab)
    assert tab.supported_table.isVisibleTo(tab)
    inventory = tab.inventory_path_edit.parentWidget()
    left_positions = {
        widget.mapTo(inventory, QPoint()).x()
        for widget in (tab.inventory_path_edit, tab.inventory_password_check,
                       tab.inventory_status_label)
    }
    assert len(left_positions) == 1
    disclosure_buttons = [section.toggle_button for section in (
        tab.inventory_format_section, tab.execution_options_section, tab.profile_section)]
    assert len({button.mapTo(tab, QPoint()).x() for button in disclosure_buttons}) == 1
    assert len({button.width() for button in disclosure_buttons}) == 1
    cards = tab.inventory_guide_steps.findChildren(QWidget, "inspectorInventoryGuideCard")
    assert len(cards) == 3
    assert len({card.geometry().top() for card in cards}) == 1
    titles = tab.inventory_guide_steps.findChildren(QLabel, "inventoryGuideStepTitle")
    assert len({title.mapTo(tab.inventory_guide_steps, QPoint()).y() for title in titles}) == 1
    for left, right in zip(cards, cards[1:]):
        assert not left.geometry().intersects(right.geometry())
    assert tab.inventory_example_table.rowCount() == 6
    assert [tab.inventory_example_table.item(row, 0).text() for row in range(6)] == [
        "ip", "vendor", "os", "connection_type", "port", "password"]
    for control in (tab.inventory_example_table, tab.validate_button):
        tab.top_scroll.ensureWidgetVisible(control)
        qapp.processEvents()
        viewport_rect = tab.top_scroll.viewport().rect()
        control_rect = QRect(control.mapTo(tab.top_scroll.viewport(), QPoint()), control.size())
        assert viewport_rect.contains(control_rect)
        assert not QRect(tab.profile_editor_button.mapTo(tab, QPoint()), tab.profile_editor_button.size()).intersects(
            QRect(control.mapTo(tab, QPoint()), control.size()))
    for section in (tab.inventory_format_section, tab.execution_options_section, tab.profile_section):
        section.setExpanded(False)
    qapp.processEvents()
    assert len({button.mapTo(tab, QPoint()).x() for button in disclosure_buttons}) == 1
    assert len({button.width() for button in disclosure_buttons}) == 1


def test_inspector_conditionals_preserve_values_and_invalidate_validation(qapp, tmp_path):
    tab = inspector_tab(tmp_path)
    assert tab.command_fields.isHidden()
    assert tab.inventory_password_fields.isHidden()
    tab.inventory_password_check.setChecked(True)
    tab.inventory_password_edit.setText("session-only")
    tab._inventory_validated = True
    tab.inventory_password_check.setChecked(False)
    assert not tab._inventory_validated
    assert tab.inventory_password_edit.text() == "session-only"
    assert tab._inventory_password() is None
    tab.inventory_password_check.setChecked(True)
    assert tab._inventory_password() == "session-only"
    tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("custom_commands"))
    tab.command_path_edit.setText("commands.txt")
    assert not tab.command_fields.isHidden()
    tab._inventory_validated = True
    tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("inspection"))
    assert not tab._inventory_validated
    assert tab.command_fields.isHidden()
    assert tab.command_path_edit.text() == "commands.txt"


def test_inspector_auto_validation_blocks_invalid_inventory(qapp, tmp_path, monkeypatch):
    tab = inspector_tab(tmp_path)
    tab.service = InventoryService(fail=True)
    tab.inventory_path_edit.setText("invalid.xlsx")
    confirmations, jobs = [], []
    monkeypatch.setattr("app.ui.tabs.inspector_tab.QMessageBox.warning", lambda *_: None)
    monkeypatch.setattr("app.ui.tabs.inspector_tab.confirm_risky_action", lambda *_a, **_k: confirmations.append(True))
    monkeypatch.setattr(tab.runner, "start", lambda *_a, **_k: jobs.append(True))
    tab._run_inspector()
    assert not tab._inventory_validated
    assert confirmations == []
    assert jobs == []
    assert not tab._inspector_running


def test_inspector_hidden_execution_options_are_forwarded_and_locked(qapp, tmp_path, monkeypatch):
    tab = inspector_tab(tmp_path)
    service = InventoryService()
    service.run = lambda *_a, **_k: None
    tab.service = service
    tab.inventory_path_edit.setText("devices.xlsx")
    tab.inventory_password_check.setChecked(True)
    tab.inventory_password_edit.setText("secret")
    tab.execution_options_section.setExpanded(True)
    tab.max_workers_spin.setValue(7)
    tab.timeout_spin.setValue(25)
    tab.retry_spin.setValue(2)
    tab.execution_options_section.setExpanded(False)
    calls = []
    monkeypatch.setattr("app.ui.tabs.inspector_tab.confirm_risky_action", lambda *_a, **_k: True)
    monkeypatch.setattr(tab.runner, "start", lambda fn, request, **kwargs: calls.append((request, kwargs)))
    tab._run_inspector()
    request, kwargs = calls[0]
    assert service.passwords == ["secret"]
    assert (request.max_workers, request.timeout, request.max_retries) == (7, 25, 2)
    assert request.inventory_password == "secret"
    assert tab._inspector_running
    assert not tab.inventory_password_check.isEnabled()
    assert tab.cancel_button.isEnabled()
    tab._cancel_inspector()
    assert kwargs["cancel_event"].is_set()
    tab._finish_inspector_run()
    assert tab.inventory_password_check.isEnabled()


def test_inspector_disclosures_cannot_unlock_running_inputs(qapp, tmp_path, monkeypatch):
    tab = inspector_tab(tmp_path)
    service = InventoryService()
    service.run = lambda *_a, **_k: None
    tab.service = service
    tab.inventory_path_edit.setText("devices.xlsx")
    tab.inventory_password_check.setChecked(True)
    tab.inventory_password_edit.setText("original")
    monkeypatch.setattr("app.ui.tabs.inspector_tab.confirm_risky_action", lambda *_a, **_k: True)
    monkeypatch.setattr(tab.runner, "start", lambda *_a, **_k: None)
    tab._run_inspector()
    for section in (tab.profile_section, tab.execution_options_section):
        section.setExpanded(True)
        section.setExpanded(False)
        section.setExpanded(True)
    tab.inventory_password_check.click()
    tab.inventory_password_edit.selectAll()
    QTest.keyClicks(tab.inventory_password_edit, "replacement")
    tab.max_workers_spin.setFocus()
    QTest.keyClick(tab.max_workers_spin, Qt.Key.Key_Up)
    assert tab.inventory_password_check.isChecked()
    assert tab.inventory_password_edit.text() == "original"
    assert tab.max_workers_spin.value() == 10
    assert not tab.profile_editor_button.isEnabled()
    assert tab._inventory_validated
    assert tab._inspector_running
    tab._finish_inspector_run()


@pytest.mark.parametrize("mode", ["inspection", "backup", "inspection_backup", "custom_commands"])
def test_inspector_only_forwards_command_file_for_custom_mode(qapp, tmp_path, monkeypatch, mode):
    tab = inspector_tab(tmp_path)
    service = InventoryService()
    service.run = lambda *_a, **_k: None
    service.validate_custom_command_file = lambda *_a: SimpleNamespace(command_count=1, variable_names=())
    tab.service = service
    tab.inventory_path_edit.setText("devices.xlsx")
    tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("custom_commands"))
    tab.command_file_radio.setChecked(True)
    tab.command_path_edit.setText("commands.txt")
    tab.mode_combo.setCurrentIndex(tab.mode_combo.findData(mode))
    calls = []
    monkeypatch.setattr("app.ui.tabs.inspector_tab.confirm_risky_action", lambda *_a, **_k: True)
    monkeypatch.setattr(tab.runner, "start", lambda fn, request, **_kwargs: calls.append(request))
    tab._run_inspector()
    assert calls[0].mode == mode
    assert calls[0].command_path == ("commands.txt" if mode == "custom_commands" else None)
    assert tab.command_path_edit.text() == "commands.txt"
    tab._finish_inspector_run()


def test_inspector_successful_validation_still_requires_risk_confirmation(qapp, tmp_path, monkeypatch):
    tab = inspector_tab(tmp_path)
    tab.service = InventoryService()
    tab.inventory_path_edit.setText("devices.xlsx")
    confirmations, jobs = [], []
    def reject_confirmation(*_args, **kwargs):
        confirmations.append(kwargs)
        return False
    monkeypatch.setattr("app.ui.tabs.inspector_tab.confirm_risky_action", reject_confirmation)
    monkeypatch.setattr(tab.runner, "start", lambda *_a, **_k: jobs.append(True))
    tab._run_inspector()
    assert tab._inventory_validated
    assert len(confirmations) == 1
    assert "SSH/Telnet" in confirmations[0]["impact"]
    assert "사용자 명령" in confirmations[0]["reversibility"]
    assert not tab._inspector_running
    assert jobs == []


@pytest.mark.parametrize("embedded", [False, True])
def test_config_builder_uses_same_progressive_controls_without_duplicate_start(qapp, tmp_path, monkeypatch, embedded):
    state_path = tmp_path / "ui_state.json"
    monkeypatch.setattr(desktop_impl, "APP_STATE_PATH", state_path)
    monkeypatch.setattr(desktop_impl, "_app_state_read_paths", lambda: [state_path])
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    widget = desktop_impl.SwitchConfigBuilderWidget(profiles_dir=profiles_dir, embedded=embedded)
    widget.resize(1024, 680)
    widget.show()
    qapp.processEvents()
    for label in ("샘플로 시작", "장비 변수 파일 열기", "빈 행 추가"):
        assert sum(button.text() == label for button in widget.findChildren(QPushButton)) == 1
    assert widget.summary_label.isHidden()
    assert not hasattr(widget, "tutorial_button")
    assert not hasattr(widget, "start_in_app_tutorial")
    assert not hasattr(desktop_impl, "InAppTutorialDialog")
    assert all("튜토리얼" not in action.text() for action in widget.main_toolbar.actions())
    assert widget.detail_tabs.tabText(widget.detail_tabs.currentIndex()) == "CLI"
    assert widget.advanced_panel.isHidden()
    widget.advanced_toggle_button.click()
    assert not widget.advanced_panel.isHidden()
    assert all(not section.isExpanded() for section in widget.advanced_sections.values())
    widget.advanced_sections["filter"].setExpanded(True)
    widget.filter_value_edit.setText("192.0.2")
    widget.advanced_sections["filter"].setExpanded(False)
    assert widget.filter_value_edit.text() == "192.0.2"
    assert "변경된 옵션 1개" in widget.advanced_sections["filter"].toggle_button.text()
    widget.work_state_section.setExpanded(True)
    assert widget.mark_done_button.isVisibleTo(widget)
    assert widget.mark_done_button.text() == "적용 완료로 표시"
    assert "명령을 전송하지 않습니다" in widget.mark_done_button.toolTip()
    for section in widget.advanced_sections.values():
        section.setExpanded(True)
    qapp.processEvents()
    assert widget.width() == 1024
    assert widget.height() == 680
    assert widget.advanced_panel.verticalScrollBar().maximum() > 0
    for control in (widget.open_file_button, widget.add_row_button, widget.sample_start_button):
        assert widget.rect().contains(QRect(control.mapTo(widget, QPoint()), control.size()))
    widget.right_scroll.ensureWidgetVisible(widget.copy_cli_button)
    qapp.processEvents()
    assert widget.right_scroll.viewport().rect().contains(
        QRect(widget.copy_cli_button.mapTo(widget.right_scroll.viewport(), QPoint()), widget.copy_cli_button.size()))


@pytest.mark.parametrize("action_name, dialog_title", [
    ("save_selected_cli_action", "선택 CLI 저장"),
    ("save_all_cli_action", "전체 CLI 저장"),
    ("save_each_cli_action", "장비별 CLI ZIP 저장"),
])
def test_cli_save_menu_routes_all_variants_and_cancel_creates_nothing(qapp, tmp_path, monkeypatch, action_name, dialog_title):
    state_path = tmp_path / "ui_state.json"
    monkeypatch.setattr(desktop_impl, "APP_STATE_PATH", state_path)
    monkeypatch.setattr(desktop_impl, "_app_state_read_paths", lambda: [state_path])
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    exports_dir = tmp_path / "exports"
    widget = desktop_impl.SwitchConfigBuilderWidget(profiles_dir=profiles_dir, exports_dir=exports_dir, embedded=True)
    assert not widget.save_cli_button.isEnabled()
    widget.current_rendered = {0: RenderedConfig("SW01", "TEST", "hostname SW01\n", {}, "SW01")}
    monkeypatch.setattr(widget, "_selected_source_rows", lambda: [0])
    widget._update_navigation_buttons()
    assert widget.save_cli_button.isEnabled()
    action = getattr(widget, action_name)
    assert action in widget.save_cli_button.menu().actions()
    assert action.isEnabled()
    dialogs, success_messages, activity = [], [], []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda _parent, title, *_args: (dialogs.append(title) or ("", "")))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: success_messages.append(True))
    monkeypatch.setattr(widget, "_append_activity_log", lambda _message: activity.append(True))
    previous_status = widget.statusBar().currentMessage()
    action.trigger()
    assert dialogs == [dialog_title]
    assert not exports_dir.exists()
    assert activity == []
    assert success_messages == []
    assert widget.statusBar().currentMessage() == previous_status


def test_full_editor_moves_live_document_and_returns_edits_without_save(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_impl, "APP_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(desktop_impl, "_app_state_read_paths", lambda: [])
    state = SimpleNamespace(paths=SimpleNamespace(data_root=tmp_path / "data", exports_dir=tmp_path / "exports"), app_config={})
    tab = ConfigBuilderTab(state)
    tab.show()
    builder = tab.builder_widget
    builder.add_profile_combo.setCurrentText("CISCO_IOSXE_EDGE_PORT_BASE")
    builder._start_sample_for_current_profile()
    column = builder.table_model.headers.index("hostname")
    builder.table_model.setData(builder.table_model.index(0, column), "BEFORE-FULL", Qt.ItemDataRole.EditRole)
    builder.refresh_timer.stop()
    builder.refresh_render_state()
    calls = []
    monkeypatch.setattr(builder, "_save_to_path", lambda *_a, **_k: calls.append("save"))
    monkeypatch.setattr(builder, "prepare_close", lambda: calls.append("prepare") or True)
    monkeypatch.setattr(builder, "load_device_file", lambda *_a: calls.append("load"))
    tab._open_full_editor()
    window = tab._builder_window
    assert window.builder is builder
    assert window.centralWidget() is builder
    assert builder.table_model.rows[0]["hostname"] == "BEFORE-FULL"
    assert calls == []
    assert not builder.auto_save_timer.isActive()
    builder.table_model.setData(builder.table_model.index(0, column), "EDITED-IN-FULL", Qt.ItemDataRole.EditRole)
    tab._open_full_editor()
    assert tab._builder_window is window
    assert window.close()
    qapp.processEvents()
    assert tab.isAncestorOf(builder)
    assert builder.table_model.rows[0]["hostname"] == "EDITED-IN-FULL"
    assert not builder._close_prepared
    assert calls == []
    assert tab.full_editor_placeholder.isHidden()
    tab._open_full_editor()
    assert tab.prepare_close()
    assert calls == ["prepare"]
    assert tab.isAncestorOf(builder)
    builder.is_dirty = False


@pytest.mark.parametrize("detached", [False, True])
def test_builder_header_profile_actions_reuse_editor_and_keep_cancelled_changes(qapp, tmp_path, monkeypatch, detached):
    monkeypatch.setattr(desktop_impl, "APP_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(desktop_impl, "_app_state_read_paths", lambda: [])
    state = SimpleNamespace(paths=SimpleNamespace(data_root=tmp_path / "data", exports_dir=tmp_path / "exports"), app_config={})
    tab = ConfigBuilderTab(state)
    tab.resize(1024, 680)
    tab.show()
    builder = tab.builder_widget
    builder.add_profile_combo.setCurrentText("CISCO_IOSXE_EDGE_PORT_BASE")
    builder._start_sample_for_current_profile()
    column = builder.table_model.headers.index("hostname")
    builder.table_model.setData(builder.table_model.index(0, column), "KEEP-EDIT", Qt.ItemDataRole.EditRole)
    builder.refresh_timer.stop()
    rows_before = deepcopy(builder.table_model.rows)
    profiles_before = {path: path.read_bytes() for path in builder.profile_dir.glob("*.yaml")}
    dialogs, confirmations, reloads = [], [], []
    class CancelledProfileDialog:
        def __init__(self, _directory, profile, parent, **_kwargs):
            dialogs.append((profile.id if profile else None, parent))
        def setWindowTitle(self, _title):
            pass
        def exec(self):
            return False
    monkeypatch.setattr(desktop_impl, "ProfileBuilderDialog", CancelledProfileDialog)
    monkeypatch.setattr(desktop_impl.QMessageBox, "question", lambda *args: confirmations.append(args[2]) or QMessageBox.StandardButton.No)
    monkeypatch.setattr(builder, "_reload_after_profile_save", lambda *_args: reloads.append(True))
    surface = tab
    if detached:
        tab._open_full_editor()
        surface = tab._builder_window
        surface.resize(1024, 680)
    qapp.processEvents()
    button = surface.profile_management_button
    assert button.isVisibleTo(surface)
    assert button.text() == "프로파일 만들기·관리"
    assert button.property("actionKind") == "primary"
    assert surface.rect().contains(QRect(button.mapTo(surface, QPoint()), button.size()))
    assert "profiles" not in builder.advanced_sections
    menu = button.menu()
    menu.aboutToShow.emit()
    assert any("CISCO_IOSXE_EDGE_PORT_BASE" in action.text() for action in menu.actions())
    actions = {action.objectName(): action for action in menu.actions() if action.objectName()}
    for key in ("new", "edit", "copy", "delete"):
        action = actions[f"configBuilderProfileAction_{key}"]
        assert action.isEnabled()
        action.trigger()
    assert dialogs == [(None, builder), ("CISCO_IOSXE_EDGE_PORT_BASE", builder), ("CISCO_IOSXE_EDGE_PORT_BASE_COPY", builder)]
    assert len(confirmations) == 1
    assert "CISCO_IOSXE_EDGE_PORT_BASE" in confirmations[0]
    assert "행이 이 프로파일을 참조" in confirmations[0]
    assert reloads == []
    assert builder.table_model.rows == rows_before
    assert builder.is_dirty
    assert {path: path.read_bytes() for path in builder.profile_dir.glob("*.yaml")} == profiles_before
    monkeypatch.setattr(builder, "reload_profiles", lambda: reloads.append(True))
    actions["configBuilderProfileAction_reload"].trigger()
    assert reloads == [True]
    builder.is_dirty = False
    if detached:
        assert surface.close()
        assert tab.isAncestorOf(builder)


def test_builder_profile_menu_allows_creation_without_existing_profile(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_impl, "APP_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(desktop_impl, "_app_state_read_paths", lambda: [])
    builder = desktop_impl.SwitchConfigBuilderWidget(profiles_dir=tmp_path / "profiles", embedded=True)
    button = builder.create_profile_management_button(builder)
    assert not builder.profiles
    menu = button.menu()
    menu.aboutToShow.emit()
    actions = {action.objectName(): action for action in menu.actions() if action.objectName()}
    assert actions["configBuilderProfileAction_new"].isEnabled()
    assert actions["configBuilderProfileAction_reload"].isEnabled()
    for key in ("edit", "copy", "delete"):
        assert not actions[f"configBuilderProfileAction_{key}"].isEnabled()
