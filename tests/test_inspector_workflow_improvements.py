from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog
from app.ui.dialogs.inventory_editor_dialog import InventoryEditorDialog
from app.ui.tabs.inspector_tab import InspectorTab
from netops_suite.modules.config_builder.switch_configurator import desktop_impl
from netops_suite.modules.config_builder.switch_configurator.models import RenderedConfig
from netops_suite.modules.config_builder.switch_configurator.table_data import DeviceTable
from netops_suite.modules.inspector import (
    InspectorRunRequest,
    InspectorRunResult,
    InspectorService,
)
from netops_suite.modules.inspector.backup_compare import (
    backup_diff_lines,
    compare_backup_file,
)


def _device(ip: str = "192.0.2.10", **values: object) -> dict[str, object]:
    device: dict[str, object] = {
        "ip": ip,
        "vendor": "cisco",
        "os": "ios",
        "connection_type": "ssh",
        "port": 22,
        "username": "admin",
        "password": "secret",
    }
    device.update(values)
    return device


def _write_inventory(path: Path, rows: list[dict[str, object]]) -> Path:
    pd.DataFrame(rows).to_excel(path, index=False)
    return path


def _service(tmp_path: Path) -> InspectorService:
    return InspectorService(work_dir=tmp_path / "runs", user_data_dir=tmp_path / "inspector")


def _patch_custom_runs(service: InspectorService, monkeypatch) -> dict[str, list[str]]:
    service._ensure_runtime_modules_current()
    with service._runtime_import_path():
        from core.inspector import NetworkInspector

    visited: dict[str, list[str]] = {}

    def fake_run_device(self, device, commands, session_log_suffix=None):
        del self, session_log_suffix
        visited[str(device["ip"])] = list(commands)
        return {
            "ip": device["ip"],
            "vendor": device["vendor"],
            "os": device["os"],
            "status": "success",
            "error_message": "",
            "inspection_results": {},
        }

    monkeypatch.setattr(NetworkInspector, "_run_custom_commands_device", fake_run_device)
    return visited


# -- service -------------------------------------------------------------


def test_common_account_fills_only_blank_inventory_cells(tmp_path: Path):
    inventory = _write_inventory(
        tmp_path / "devices.xlsx",
        [
            {k: v for k, v in _device(password="own").items() if k != "username"},
            {k: v for k, v in _device("192.0.2.20", password="").items() if k != "username"},
        ],
    )
    devices = _service(tmp_path).load_inventory(
        str(inventory), None, {"username": "ops", "password": "common"}
    )
    by_ip = {str(device["ip"]): device for device in devices}
    assert by_ip["192.0.2.10"]["password"] == "own"
    assert by_ip["192.0.2.20"]["password"] == "common"
    assert {device["username"] for device in devices} == {"ops"}


def test_common_account_supplies_a_missing_password_column(tmp_path: Path):
    inventory = _write_inventory(
        tmp_path / "devices.xlsx",
        [{k: v for k, v in _device().items() if k != "password"}],
    )
    service = _service(tmp_path)
    with pytest.raises(Exception, match="password"):
        service.load_inventory(str(inventory))
    devices = service.load_inventory(str(inventory), None, {"password": "common"})
    assert devices[0]["password"] == "common"


def test_inventory_rows_open_lists_for_devices_without_a_profile(tmp_path: Path):
    inventory = _write_inventory(
        tmp_path / "devices.xlsx",
        [_device(), _device("192.0.2.20", vendor="acme", os="acmeos", port=2222.0)],
    )
    service = _service(tmp_path)
    rows = service.load_inventory_rows(str(inventory))
    assert rows[1]["vendor"] == "acme"
    assert rows[1]["port"] == "2222"
    assert service.unsupported_inventory_profiles(rows) == [("acme", "acmeos")]


def test_only_ips_reruns_just_the_failed_devices(tmp_path: Path, monkeypatch):
    inventory = _write_inventory(tmp_path / "devices.xlsx", [_device(), _device("192.0.2.20")])
    service = _service(tmp_path)
    visited = _patch_custom_runs(service, monkeypatch)
    events: list[dict] = []
    result = service.run(
        InspectorRunRequest(
            inventory_path=str(inventory),
            mode="custom_commands",
            commands=["show version"],
            only_ips=("192.0.2.20",),
        ),
        progress_callback=events.append,
    )
    assert list(visited) == ["192.0.2.20"]
    assert result.devices_total == 1
    assert any(event.get("total") == 1 for event in events)


def test_device_cli_runs_each_device_own_lines_and_skips_others(tmp_path: Path, monkeypatch):
    inventory = _write_inventory(tmp_path / "devices.xlsx", [_device(), _device("192.0.2.20")])
    service = _service(tmp_path)
    visited = _patch_custom_runs(service, monkeypatch)
    result = service.run(
        InspectorRunRequest(
            inventory_path=str(inventory),
            mode="custom_commands",
            commands_by_ip={
                "192.0.2.10": ["!", "interface Gi1/0/1", "", " description uplink", "!!"],
                "198.51.100.9": ["hostname elsewhere"],
            },
        )
    )
    assert visited == {"192.0.2.10": ["interface Gi1/0/1", " description uplink"]}
    assert result.devices_total == 1


def test_validate_device_commands_reports_unmatched_ips(tmp_path: Path):
    summary = _service(tmp_path).validate_device_commands(
        {"192.0.2.10": ["hostname a"], "198.51.100.9": ["hostname b"]},
        [_device(), _device("192.0.2.20")],
    )
    assert summary.matched_ips == ("192.0.2.10",)
    assert summary.missing_ips == ("198.51.100.9",)
    assert summary.skipped_ips == ("192.0.2.20",)
    assert summary.preview_commands == ("hostname a",)
    with pytest.raises(ValueError, match="하나도 없습니다"):
        _service(tmp_path).validate_device_commands(
            {"198.51.100.9": ["hostname b"]}, [_device()]
        )


def test_backup_comparison_ignores_timestamps_and_finds_changes(tmp_path: Path):
    root = tmp_path / "backup"
    old = root / "20261001_090000"
    mid = root / "20261008_090000"
    new = root / "20261015_090000"
    for folder in (old, mid, new):
        folder.mkdir(parents=True)
    name = "192.0.2.10_cisco_ios.txt"
    (old / name).write_text("hostname OLD\n", encoding="utf-8")
    (mid / name).write_text(
        "! Last configuration change at 09:00\nhostname SW1\nvlan 10\n", encoding="utf-8"
    )
    (new / name).write_text(
        "! Last configuration change at 10:30\nhostname SW1\nvlan 10\nvlan 20\n",
        encoding="utf-8",
    )
    (new / "192.0.2.11_cisco_ios.txt").write_text("hostname SW2\n", encoding="utf-8")
    (mid / "192.0.2.12_cisco_ios.txt").write_text("hostname SW3\n", encoding="utf-8")
    (new / "192.0.2.12_cisco_ios.txt").write_text(
        "! NVRAM config last updated at 10:31\nhostname SW3\n", encoding="utf-8"
    )

    changed = compare_backup_file(new / name)
    assert changed.status == "changed"
    assert changed.previous_path == mid / name
    assert (changed.added, changed.removed) == (1, 0)
    assert "+vlan 20" in backup_diff_lines(changed)
    assert compare_backup_file(new / "192.0.2.11_cisco_ios.txt").status == "new"
    assert compare_backup_file(new / "192.0.2.12_cisco_ios.txt").status == "same"
    assert compare_backup_file(new / "missing.txt").status == "missing"


# -- inspector screen ----------------------------------------------------


class ControlledThreadPool:
    def __init__(self) -> None:
        self.pending: list[object] = []

    def start(self, worker) -> None:
        self.pending.append(worker)

    def release_next(self) -> None:
        self.pending.pop(0).run()


class RecordingService:
    """Stands in for InspectorService and records what the screen asks for."""

    validate_device_commands = InspectorService.validate_device_commands
    clean_device_commands = staticmethod(InspectorService.clean_device_commands)

    def __init__(self, results: list[dict], mode_backup_dir: Path | None = None) -> None:
        self.results = results
        self.requests: list[InspectorRunRequest] = []
        self.loads: list[tuple] = []
        self.backup_dir = mode_backup_dir
        self.work_dir = Path(".")

    def load_inventory(self, path, password=None, default_credentials=None):
        self.loads.append((path, password, default_credentials))
        return [_device(), _device("192.0.2.20")]

    def inventory_profile_warnings(self, _devices):
        return []

    def run(self, request, *, progress_callback=None, cancel_event=None):
        del cancel_event
        self.requests.append(request)
        emit = getattr(progress_callback, "emit", progress_callback)
        if emit is not None:
            emit({"type": "progress", "message": "대기열", "total": len(self.results)})
            for row in self.results:
                emit({"type": "device_complete", "ip": row["ip"], "success": row["status"] != "error"})
        return InspectorRunResult(
            mode=request.mode,
            devices_total=len(self.results),
            results_total=len(self.results),
            result_excel=None,
            backup_dir=str(self.backup_dir) if self.backup_dir else None,
            session_log_dir=None,
            results=list(self.results),
        )


def _tab(tmp_path: Path, service) -> tuple[InspectorTab, ControlledThreadPool]:
    pool = ControlledThreadPool()
    tab = InspectorTab(
        SimpleNamespace(thread_pool=pool, paths=SimpleNamespace(data_root=tmp_path / "data"))
    )
    tab.service = service
    inventory = tmp_path / "inventory.xlsx"
    inventory.write_bytes(b"")
    tab.inventory_path_edit.setText(str(inventory))
    return tab, pool


@pytest.fixture
def confirm_all(monkeypatch):
    confirmations: list[dict] = []

    def confirm(_parent, title, **kwargs):
        confirmations.append({"title": title, **kwargs})
        return True

    monkeypatch.setattr("app.ui.tabs.inspector_tab.confirm_risky_action", confirm)
    monkeypatch.setattr(
        "app.ui.tabs.inspector_tab.QMessageBox.warning", lambda *_args, **_kwargs: None
    )
    return confirmations


def test_mode_buttons_drive_the_selected_mode(qapp, tmp_path: Path):
    tab, _pool = _tab(tmp_path, RecordingService([]))
    try:
        assert [button.text() for button in tab.mode_buttons.values()] == [
            "점검",
            "백업",
            "점검+백업",
            "명령 일괄 입력",
        ]
        tab.mode_buttons["backup"].click()
        assert tab.mode_combo.currentData() == "backup"
        assert "이전 백업" in tab.mode_description_label.text()
        tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("custom_commands"))
        assert tab.mode_buttons["custom_commands"].isChecked()
        assert not tab.command_fields.isHidden()
    finally:
        tab.close()


def test_run_shows_progress_failed_devices_and_reruns_only_them(
    qapp, tmp_path: Path, confirm_all
):
    service = RecordingService(
        [
            {"ip": "192.0.2.10", "vendor": "cisco", "os": "ios", "status": "success"},
            {
                "ip": "192.0.2.20",
                "vendor": "cisco",
                "os": "ios",
                "status": "error",
                "error_message": "인증 실패\n조치: 계정을 확인하세요.",
            },
        ]
    )
    tab, pool = _tab(tmp_path, service)
    try:
        tab._last_request = InspectorRunRequest(inventory_path="x", mode="inspection")
        tab._run_progress = {"total": 0, "done": 0, "success": 0, "failed": 0}
        tab._handle_progress({"type": "progress", "message": "대기열", "total": 4})
        tab._handle_progress({"type": "device_complete", "ip": "192.0.2.10", "success": True})
        tab._handle_progress({"type": "device_complete", "ip": "192.0.2.20", "success": False})
        assert tab.summary_label.text() == "점검 실행 중 · 2/4대 완료 (성공 1 · 실패 1)"
        assert tab.progress_bar.value() == 2

        tab._run_inspector()
        pool.release_next()
        qapp.processEvents()

        assert tab.summary_label.text().startswith("점검 완료: 2대 중 1대 성공, 1대 실패")
        assert tab.result_table.rowCount() == 2
        assert tab.result_table.item(0, 0).text() == "192.0.2.20"
        assert tab.result_table.item(0, 2).text() == "실패"
        assert "조치: 계정을 확인하세요." in tab.result_table.item(0, 3).text()
        assert not tab.retry_failed_button.isHidden()
        assert tab.retry_failed_button.text() == "실패한 1대만 다시 실행"

        QTest.mouseClick(tab.retry_failed_button, Qt.MouseButton.LeftButton)
        assert confirm_all[-1]["title"] == "실패한 장비 다시 실행"
        pool.release_next()
        qapp.processEvents()
        assert service.requests[-1].only_ips == ("192.0.2.20",)
        assert service.requests[-1].mode == "inspection"
    finally:
        tab.close()


def test_backup_results_show_changed_devices(qapp, tmp_path: Path, confirm_all):
    backup_root = tmp_path / "runs" / "backup"
    previous = backup_root / "20261001_090000"
    current = backup_root / "20261015_090000"
    previous.mkdir(parents=True)
    current.mkdir(parents=True)
    (previous / "192.0.2.10_cisco_ios.txt").write_text("hostname A\n", encoding="utf-8")
    (current / "192.0.2.10_cisco_ios.txt").write_text("hostname B\n", encoding="utf-8")
    (current / "192.0.2.20_cisco_ios.txt").write_text("hostname C\n", encoding="utf-8")
    service = RecordingService(
        [
            {"ip": "192.0.2.10", "vendor": "cisco", "os": "ios", "status": "success",
             "backup_file": str(current / "192.0.2.10_cisco_ios.txt")},
            {"ip": "192.0.2.20", "vendor": "cisco", "os": "ios", "status": "success"},
        ],
        mode_backup_dir=current,
    )
    tab, pool = _tab(tmp_path, service)
    try:
        tab.mode_buttons["backup"].click()
        tab._run_inspector()
        pool.release_next()
        qapp.processEvents()
        assert "변경 1대" in tab.summary_label.text()
        assert "첫 백업 1대" in tab.summary_label.text()
        details = {
            tab.result_table.item(row, 0).text(): tab.result_table.item(row, 3).text()
            for row in range(tab.result_table.rowCount())
        }
        assert details["192.0.2.10"].startswith("변경됨")
        assert details["192.0.2.20"] == "첫 백업"
        assert not tab.backup_diff_button.isHidden()
        tab.result_table.selectRow(0)
        assert tab.backup_diff_button.isEnabled()
    finally:
        tab.close()


def test_common_account_is_required_and_passed_to_validation(qapp, tmp_path: Path):
    service = RecordingService([])
    tab, _pool = _tab(tmp_path, service)
    try:
        tab.common_account_check.setChecked(True)
        assert not tab.common_account_fields.isHidden()
        tab._validate_inventory()
        assert "비밀번호를 입력하세요" in tab.validation_status_label.text()
        assert service.loads == []
        tab.common_username_edit.setText("ops")
        tab.common_password_edit.setText("pw")
        tab._validate_inventory()
        assert service.loads[-1][2] == {"username": "ops", "password": "pw"}
        assert tab._inventory_validated
    finally:
        tab.close()


def test_device_cli_from_config_builder_runs_by_ip(qapp, tmp_path: Path, confirm_all):
    service = RecordingService(
        [{"ip": "192.0.2.10", "vendor": "cisco", "os": "ios", "status": "success",
          "custom_commands_executed": 2}]
    )
    tab, pool = _tab(tmp_path, service)
    try:
        assert tab.receive_device_commands(
            {"192.0.2.10": ["hostname SW1", "!", "vlan 10"]}, "IP는 'mgmt_ip' 열"
        )
        assert tab.mode_combo.currentData() == "custom_commands"
        assert tab.command_device_radio.isChecked()
        assert not tab.device_commands_panel.isHidden()
        assert tab.first_device_only_check.isChecked()
        tab.first_device_only_check.setChecked(False)
        tab._run_inspector()
        assert "장비별 CLI를 받을 장비 1대" in tab.validation_status_label.text()
        assert "CLI가 없어 건너뛸 장비 1대" in tab.validation_status_label.text()
        assert "설정을 바꾸는 명령" in confirm_all[-1]["impact"]
        pool.release_next()
        qapp.processEvents()
        request = service.requests[-1]
        assert request.commands_by_ip == {"192.0.2.10": ["hostname SW1", "vlan 10"]}
        assert request.commands is None and request.command_path is None
        assert tab.result_table.item(0, 3).text() == "명령 2개 실행"

        tab.clear_device_commands()
        assert tab.command_inline_radio.isChecked()
        assert tab.command_device_radio.isHidden()
    finally:
        tab.close()


def test_validation_failure_offers_a_profile_for_unknown_devices(qapp, tmp_path: Path, monkeypatch):
    class FailingService(RecordingService):
        def load_inventory(self, *_args, **_kwargs):
            raise ValueError("지원하지 않는 벤더: acme")

        def load_inventory_rows(self, *_args, **_kwargs):
            return [{"ip": "192.0.2.10", "vendor": "acme", "os": "acmeos"}]

        def unsupported_inventory_profiles(self, rows):
            return [("acme", "acmeos")]

    monkeypatch.setattr("app.ui.tabs.inspector_tab.QMessageBox.warning", lambda *_a, **_k: None)
    tab, _pool = _tab(tmp_path, FailingService([]))
    opened: list[object] = []
    monkeypatch.setattr(tab, "_open_profile_editor", lambda initial_device=None: opened.append(initial_device))
    try:
        tab._validate_inventory()
        assert "acme / acmeos" in tab.validation_status_label.text()
        assert not tab.create_missing_profile_button.isHidden()
        assert tab.create_missing_profile_button.text() == "acme / acmeos 점검 프로파일 만들기"
        tab.create_missing_profile_button.click()
        assert opened == [("acme", "acmeos")]
        tab.inventory_path_edit.setText(str(tmp_path / "other.xlsx"))
        assert tab.create_missing_profile_button.isHidden()
    finally:
        tab.close()


# -- dialogs -------------------------------------------------------------


def test_inventory_editor_pastes_ip_lists_and_saves_excel(qapp, tmp_path: Path):
    dialog = InventoryEditorDialog(
        [("cisco", "ios"), ("axgate", "axgate")],
        default_device=("axgate", "axgate"),
        default_connection="telnet",
        extra_columns=("interface",),
        save_dir=tmp_path,
    )
    try:
        assert dialog.row_values(0)["connection_type"] == "telnet"
        assert dialog.row_values(0)["port"] == "23"
        dialog.table.setCurrentCell(0, dialog.column_index["ip"])
        dialog.paste_clipboard("192.0.2.10\n192.0.2.11\nnot-an-ip\n")
        assert dialog.table.rowCount() == 3
        assert dialog.row_values(2)["vendor"] == "axgate"
        messages = [message for _row, _key, message in dialog.problems()]
        assert any("3행: IP 형식" in message for message in messages)
        assert any("interface" in message for message in messages)
        dialog.table.setCurrentCell(2, 0)
        dialog.delete_selected_rows()
        dialog.table.setCurrentCell(0, dialog.column_index["interface"])
        dialog.paste_clipboard("Gi1/0/1\nGi1/0/2")
        dialog.table.item(0, dialog.column_index["password"]).setText("pw")
        assert dialog.table.itemDelegateForColumn(dialog.column_index["password"]).displayText("pw", None) == "●●●●●●"
        assert dialog.problems() == []

        target = dialog.save_to(tmp_path / "devices.xlsx")
        assert target is not None
        saved = pd.read_excel(target, dtype=str).fillna("")
        assert list(saved.columns[:6]) == ["ip", "vendor", "os", "model", "connection_type", "port"]
        assert saved["ip"].tolist() == ["192.0.2.10", "192.0.2.11"]
        assert saved["password"].tolist() == ["pw", ""]
        assert saved["interface"].tolist() == ["Gi1/0/1", "Gi1/0/2"]
    finally:
        dialog.close()


def test_inventory_editor_keeps_unknown_devices_and_columns(qapp, tmp_path: Path):
    dialog = InventoryEditorDialog(
        [("cisco", "ios")],
        rows=[{"ip": "192.0.2.10", "vendor": "acme", "os": "acmeos", "port": "22",
               "connection_type": "ssh", "legacy_ssh": "TRUE"}],
        source_path=tmp_path / "devices.xlsx",
    )
    try:
        assert dialog.windowTitle() == "장비 목록 편집"
        assert dialog.row_values(0)["vendor"] == "acme"
        assert dialog.row_values(0)["legacy_ssh"] == "TRUE"
        dialog.duplicate_selected_rows()
        assert dialog.table.rowCount() == 1
        dialog.table.setCurrentCell(0, 0)
        dialog.duplicate_selected_rows()
        assert dialog.row_values(1)["ip"] == "192.0.2.11"
    finally:
        dialog.close()


def test_profile_trial_fills_a_device_from_the_inventory(qapp, tmp_path: Path):
    service = _service(tmp_path)
    dialog = InspectorProfileDialog(
        service,
        exports_dir=tmp_path / "exports",
        inventory_rows=[
            {"ip": "192.0.2.10", "vendor": "cisco", "os": "ios", "port": "22",
             "connection_type": "ssh", "username": "a", "password": "p1"},
            {"ip": "192.0.2.20", "vendor": "acme", "os": "acmeos", "port": "2323",
             "connection_type": "telnet", "username": "b", "password": "p2",
             "enable_password": "e2"},
        ],
        initial_device=("acme", "acmeos"),
    )
    try:
        assert dialog.windowTitle() == "점검 프로파일 만들기"
        assert dialog.vendor_edit.text() == "acme"
        assert dialog.os_edit.text() == "acmeos"
        combo = dialog.trial_device_combo
        assert combo.count() == 3
        assert combo.itemText(1).startswith("192.0.2.20")
        combo.setCurrentIndex(1)
        assert dialog.trial_ip_edit.text() == "192.0.2.20"
        assert dialog.trial_port_spin.value() == 2323
        assert dialog.connection_combo.currentData() == "telnet"
        assert dialog.trial_username_edit.text() == "b"
        assert dialog.trial_password_edit.text() == "p2"
        assert dialog.trial_enable_edit.text() == "e2"
    finally:
        dialog._dirty = False
        dialog.close()


def test_profile_trial_hides_the_picker_without_an_inventory(qapp, tmp_path: Path):
    dialog = InspectorProfileDialog(_service(tmp_path), exports_dir=tmp_path / "exports")
    try:
        assert dialog.trial_device_combo.isHidden()
    finally:
        dialog._dirty = False
        dialog.close()


# -- config builder hand-off ----------------------------------------------


@pytest.fixture
def builder(qapp, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(desktop_impl, "APP_STATE_PATH", tmp_path / "desktop_state.json")
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    widget = desktop_impl.SwitchConfigBuilderWidget(
        profiles_dir=profiles_dir, exports_dir=tmp_path / "exports"
    )
    monkeypatch.setattr(widget, "_append_activity_log", lambda _message: None)
    monkeypatch.setattr(widget, "_confirm_discard_changes", lambda: True)
    widget.table_model.set_table(
        DeviceTable(
            path=None,
            headers=["profile_id", "hostname", "gateway_ip", "mgmt_ip"],
            rows=[
                {"profile_id": "P", "hostname": "SW1", "gateway_ip": "192.0.2.1", "mgmt_ip": "192.0.2.10"},
                {"profile_id": "P", "hostname": "SW2", "gateway_ip": "192.0.2.1", "mgmt_ip": "192.0.2.11"},
            ],
        )
    )
    widget.current_rendered = {
        0: RenderedConfig("SW1", "P", "hostname SW1\n!\n", {}, "SW1"),
        1: RenderedConfig("SW2", "P", "hostname SW2\n", {}, "SW2"),
    }
    yield widget
    widget.close()


def test_config_builder_sends_per_device_cli_by_management_ip(builder, monkeypatch):
    assert builder.device_ip_headers() == ["mgmt_ip", "gateway_ip"]
    monkeypatch.setattr(
        desktop_impl.QInputDialog,
        "getItem",
        lambda *_args, **_kwargs: ("mgmt_ip", True),
    )
    received: list[tuple[dict, str]] = []
    builder.send_to_inspector_requested.connect(lambda mapping, note: received.append((mapping, note)))
    sent = builder.send_rendered_cli_to_inspector()
    assert sent == {"192.0.2.10": ["hostname SW1", "!"], "192.0.2.11": ["hostname SW2"]}
    assert received[0][1].startswith("IP는 'mgmt_ip' 열")


def test_config_builder_refuses_a_column_with_shared_ips(builder, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(
        desktop_impl.QInputDialog,
        "getItem",
        lambda *_args, **_kwargs: ("gateway_ip", True),
    )
    monkeypatch.setattr(
        desktop_impl.QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    assert builder.send_rendered_cli_to_inspector() is None
    assert "같은 IP" in warnings[0]


def test_main_window_opens_inspector_with_sent_cli(qapp, tmp_path: Path, monkeypatch):
    from app.app_state import AppState
    from app.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_maybe_check_updates_on_startup", lambda *_a, **_k: None)
    window = MainWindow(AppState(tmp_path))
    try:
        window.navigate_to("config_builder")
        window.config_builder_tab.builder_widget.send_to_inspector_requested.emit(
            {"192.0.2.10": ["hostname SW1"]}, "IP는 'mgmt_ip' 열"
        )
        qapp.processEvents()
        assert window.tab_widget.currentWidget() is window.inspector_tab
        assert window.inspector_tab.mode_combo.currentData() == "custom_commands"
        assert window.inspector_tab.command_device_radio.isChecked()
    finally:
        window.close()
