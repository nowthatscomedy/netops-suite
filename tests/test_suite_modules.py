from __future__ import annotations

import os
import builtins
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import pandas as pd
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication, QCheckBox, QGroupBox, QLabel, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QWidget

from app.app_state import AppState
from app.main_window import MainWindow
from app.ui.common import confirm_risky_action
from app.ui.common.theme import APP_STYLE_SHEET
from app.ui.dialogs.inspector_vendor_template_dialog import InspectorVendorTemplateDialog, PythonParserDialog
from app.ui.tabs.artifacts_tab import ArtifactsTab
from app.ui.tabs.inspector_tab import InspectorTab
from app.ui.tabs.config_builder_tab import ConfigBuilderTab
from app.utils.app_icon import load_app_icon
from app.utils.file_utils import DEFAULT_UPDATE_ASSET_PATTERN, resolve_asset_path
from netops_suite.modules.config_builder import ConfigBuilderService
from netops_suite.modules.inspector import InspectorService
from netops_suite.ui.actions import ActionKind, make_action_button


def test_config_builder_service_renders_valid_csv(tmp_path: Path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "test_profile.yaml").write_text(
        "\n".join(
            [
                "id: TEST_PROFILE",
                "vendor: CISCO",
                "model: TEST",
                "firmware: IOS-XE",
                "variables:",
                "  hostname:",
                "    required: true",
                "    type: string",
                "  mgmt_ip:",
                "    required: true",
                "    type: ipv4",
                "blocks:",
                "  - name: base",
                "    lines:",
                "      - \"hostname {{ hostname }}\"",
                "      - \"ip address {{ mgmt_ip }}\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    device_values = tmp_path / "devices.csv"
    device_values.write_text(
        "profile_id,device_id,hostname,mgmt_ip\nTEST_PROFILE,SW01,SW01,192.168.10.10\n",
        encoding="utf-8",
    )

    result = ConfigBuilderService(profiles_dir).render_file(device_values)

    assert not result.profile_issues
    assert not result.device_issues
    assert len(result.rendered) == 1
    assert "hostname SW01" in result.bundle_text
    assert "ip address 192.168.10.10" in result.bundle_text


def test_inspector_service_loads_supported_vendor_profiles():
    profiles = InspectorService().supported_profiles()

    assert "cisco" in profiles
    assert "juniper" in profiles
    assert profiles["cisco"]


def test_inspector_profile_listing_does_not_require_telnetlib3(monkeypatch, tmp_path: Path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    service.reload_runtime_modules()
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if str(name).startswith("telnetlib3"):
            raise ImportError("blocked telnetlib3 for profile listing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    try:
        profiles = service.supported_profiles()
    finally:
        service.reload_runtime_modules()

    assert "cisco" in profiles
    assert profiles["cisco"]


def test_inspector_inventory_validation_uses_runtime_core_and_vendors(tmp_path: Path):
    inventory_path = tmp_path / "inventory.xlsx"
    pd.DataFrame(
        [
            {
                "ip": "192.0.2.10",
                "vendor": "cisco",
                "os": "ios",
                "connection_type": "ssh",
                "port": 22,
                "username": "admin",
                "password": "test-password",
            }
        ]
    ).to_excel(inventory_path, index=False)

    devices = InspectorService(user_data_dir=tmp_path / "inspector").load_inventory(str(inventory_path))

    assert len(devices) == 1
    assert devices[0]["vendor"] == "cisco"
    assert devices[0]["os"] == "ios"


def test_inspector_sample_inventory_validates_without_manual_edits(qt_app, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.Ok)
    state = SimpleNamespace(
        thread_pool=QThreadPool.globalInstance(),
        paths=SimpleNamespace(data_root=tmp_path / "data"),
    )
    tab = InspectorTab(state)
    try:
        tab._create_sample_inventory()

        sample_path = tab.inventory_path_edit.text()
        devices = tab.service.load_inventory(sample_path)

        assert len(devices) == 1
        assert devices[0]["password"] == "CHANGE_ME_PASSWORD"
        assert devices[0]["enable_password"] == "CHANGE_ME_ENABLE_PASSWORD"
    finally:
        tab.close()


def test_telnet_compat_uses_telnetlib3_not_deprecated_stdlib():
    from netops_suite.modules.inspector_runtime.core import telnet_compat

    assert telnet_compat._load_telnet_class().__module__.startswith("telnetlib3.")


def test_telnet_compat_reports_missing_dependency_when_telnet_is_used(monkeypatch):
    module_path = Path("netops_suite/modules/inspector_runtime/core/telnet_compat.py")
    spec = importlib.util.spec_from_file_location("telnet_compat_missing_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def missing_telnetlib3(name):
        if name == "telnetlib3.telnetlib":
            raise ImportError("blocked telnetlib3")
        raise AssertionError(name)

    monkeypatch.setattr(module.importlib, "import_module", missing_telnetlib3)

    with pytest.raises(RuntimeError, match="telnetlib3 is required"):
        module.Telnet("127.0.0.1")


def test_inspector_reference_templates_load():
    templates = InspectorService().supported_profile_templates()
    reference_templates = [template for template in templates if template.get("is_reference")]
    cisco_reference = next(
        template for template in reference_templates if template["vendor"] == "reference-cisco"
    )

    assert cisco_reference["display_name"] == "참고용 Cisco IOS-XE 기본 점검"
    assert "show version" in cisco_reference["commands"]
    assert "OS버전" in cisco_reference["output_columns"]
    assert cisco_reference["parsing_rules"]["show version"]["patterns"][0]["parser_type"] == "split_fields"


def test_inspector_custom_rules_use_user_data_root(tmp_path: Path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    path = service.save_custom_rules_text(
        """
inspection_commands:
  custom:
    os1:
      - show version
"""
    )

    assert path == tmp_path / "inspector" / "custom_rules.yaml"
    assert path.exists()


def test_inspector_discovers_user_python_parsers(tmp_path: Path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    parser_file = service.custom_parsers_dir / "my_parser.py"
    parser_file.write_text("def parsing_custom(output):\n    return {'value': output.strip()}\n", encoding="utf-8")

    assert service.discover_user_custom_parsers() == ["parsing_custom"]


def test_inspector_service_saves_and_tests_custom_parser(tmp_path: Path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    code = "def parsing_cpu_usage(output: str):\n    return output.split()[2]\n"

    assert service.test_custom_parser_code("parsing_cpu_usage", code, "CPU Usage 12 %") == "12"
    path = service.save_custom_parser("parsing_cpu_usage", code)

    assert path.exists()
    assert "parsing_cpu_usage" in service.discover_user_custom_parsers()


def test_vendor_template_dialog_uses_engineer_friendly_flow(qt_app, tmp_path: Path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    dialog = InspectorVendorTemplateDialog(service)
    try:
        tabs = [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())]
        assert dialog.windowTitle() == "장비 점검 템플릿 만들기"
        assert tabs[:4] == ["장비 정보", "점검 명령", "Excel 컬럼", "미리보기/저장"]
        assert "Netmiko" not in "\n".join(tabs[:4])

        dialog.vendor_edit.setText("Cisco")
        dialog.os_edit.setText("IOS-XE")
        dialog.refresh_preview()

        assert "parser_type: split_fields" in dialog.latest_yaml_text
        assert "patterns:" in dialog.latest_yaml_text
        assert "OS버전" in dialog.summary_preview.toPlainText()
    finally:
        dialog.close()


def test_vendor_template_dialog_opens_without_telnetlib3(monkeypatch, qt_app, tmp_path: Path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    service.reload_runtime_modules()
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if str(name).startswith("telnetlib3"):
            raise ImportError("blocked telnetlib3 for template management")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    dialog = InspectorVendorTemplateDialog(service)
    try:
        assert dialog.windowTitle() == "장비 점검 템플릿 만들기"
        assert dialog.templates
    finally:
        dialog.close()
        service.reload_runtime_modules()


def test_python_parser_dialog_saves_function(qt_app, tmp_path: Path, monkeypatch):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    dialog = PythonParserDialog(service)
    try:
        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.Ok)
        dialog.function_name_edit.setText("parsing_test_value")
        dialog.code_edit.setPlainText("def parsing_test_value(output: str):\n    return output.strip().upper()\n")
        dialog.sample_output_edit.setPlainText("ok")
        dialog._test_code()

        assert "'OK'" in dialog.result_view.toPlainText()
        dialog._save_code()
        assert (service.custom_parsers_dir / "parsing_test_value.py").exists()
    finally:
        dialog.close()


def test_python_parser_dialog_shows_trusted_code_warning(qt_app, tmp_path: Path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    dialog = PythonParserDialog(service)
    try:
        warning = dialog.findChild(QLabel, "pythonParserTrustWarning")

        assert warning is not None
        assert "신뢰할 수 있는 코드만" in warning.text()
    finally:
        dialog.close()


def test_inspector_logs_custom_parser_test_failure(tmp_path: Path, caplog):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    code = "def parsing_broken(output: str):\n    raise RuntimeError('boom')\n"

    with pytest.raises(RuntimeError, match="boom"):
        service.test_custom_parser_code("parsing_broken", code, "sample")

    assert "Python custom parser failed during test: parsing_broken" in caplog.text


def test_split_fields_parser_extracts_range():
    service = InspectorService()
    with service._runtime_import_path():
        from core.inspector import NetworkInspector

        output = "CPU Usage        12 %\nMemory Usage     40 %"
        assert NetworkInspector._parse_split_fields(
            output,
            {"line_number": 1, "start_field": 3, "end_field": 4, "delimiter": "whitespace"},
        ) == "12 %"


def test_multiple_excel_columns_from_same_command_are_preserved(tmp_path: Path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    yaml_text = service.build_simple_custom_rules_yaml(
        vendor="Cisco",
        os_name="IOS-XE",
        inspection_commands=["show version"],
        parser_rows=[
            {
                "command": "show version",
                "output_column": "OS버전",
                "parser_type": "split_fields",
                "line_number": 1,
                "start_field": 6,
                "end_field": 6,
            },
            {
                "command": "show version",
                "output_column": "시리얼번호",
                "parser_type": "keyword_after",
                "keyword": "Processor board ID",
            },
        ],
    )

    assert "patterns:" in yaml_text
    assert "OS버전" in yaml_text
    assert "시리얼번호" in yaml_text


def test_config_builder_reference_device_values_render():
    service = ConfigBuilderService()
    sample_names = [
        "sample_cisco_ios_l2_access_base_devices.csv",
        "sample_cisco_iosxe_edge_port_base_devices.csv",
        "sample_cisco_iosxe_l3_distribution_base_devices.csv",
        "sample_comprehensive_reference_devices.csv",
    ]

    for sample_name in sample_names:
        result = service.render_file(service.device_values_dir / sample_name)
        assert not result.profile_issues
        assert not result.device_issues
        assert result.rendered
        assert "hostname" in result.bundle_text


def test_config_builder_profile_samples_match_package_profiles():
    service = ConfigBuilderService()
    profiles, issues = service.load_profiles()

    assert not issues
    assert profiles
    for profile in profiles.values():
        sample = service.sample_device_values_for_profile(profile)
        assert sample is not None, profile.id
        assert sample.name.startswith("sample_")
        result = service.render_file(sample)
        assert not result.profile_issues
        assert not result.device_issues
        assert result.rendered


def test_config_builder_prepares_user_sample_without_overwrite(tmp_path: Path):
    service = ConfigBuilderService(user_data_dir=tmp_path / "config_builder")
    profiles, _ = service.load_profiles()
    profile = profiles["CISCO_IOS_L2_ACCESS_BASE"]

    sample = service.prepare_sample_device_values_for_profile(profile)
    original_text = sample.read_text(encoding="utf-8")
    sample.write_text("sentinel", encoding="utf-8")
    prepared_again = service.prepare_sample_device_values_for_profile(profile)

    assert sample == tmp_path / "config_builder" / "device_values" / "sample_cisco_ios_l2_access_base_devices.csv"
    assert "CISCO_IOS_L2_ACCESS_BASE" in original_text
    assert prepared_again.read_text(encoding="utf-8") == "sentinel"


def test_config_builder_generates_blank_sample_for_custom_profile(tmp_path: Path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "custom_profile.yaml").write_text(
        "\n".join(
            [
                "id: CUSTOM_PROFILE",
                "vendor: CISCO",
                "model: CUSTOM",
                "firmware: IOS-XE",
                "variables:",
                "  hostname:",
                "    required: true",
                "    type: string",
                "  mgmt_ip:",
                "    required: true",
                "    type: ipv4",
                "blocks:",
                "  - name: base",
                "    lines:",
                "      - \"hostname {{ hostname }}\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    service = ConfigBuilderService(profiles_dir=profiles_dir, user_data_dir=tmp_path / "config_builder")
    profiles, issues = service.load_profiles()

    sample = service.prepare_sample_device_values_for_profile(profiles["CUSTOM_PROFILE"])
    lines = sample.read_text(encoding="utf-8").splitlines()

    assert not issues
    assert sample == tmp_path / "config_builder" / "device_values" / "custom_profile_devices.csv"
    assert lines[0] == "device_id,profile_id,hostname,mgmt_ip"
    assert lines[1].startswith(",CUSTOM_PROFILE,,")


def test_config_builder_tab_embeds_full_builder_and_removes_legacy_shortcuts(qt_app, tmp_path: Path):
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        tab.show()
        QApplication.processEvents()
        button = lambda name: tab.findChild(QPushButton, name)
        builder = tab.builder_widget

        assert button("configBuilderGuideButton") is None
        assert button("configBuilderSampleDeviceValuesButton") is None
        assert button("configBuilderRequiredColumnsButton") is None
        assert tab.full_editor_button.text() == "전체 창"
        assert tab.full_editor_button.property("actionKind") == ActionKind.EDIT.value
        assert "별도 창" in tab.full_editor_button.toolTip()
        assert builder._embedded is True
        assert builder.objectName() == "configBuilderEmbeddedBuilder"
        assert builder.windowTitle() == "CLI 설정 생성"
        assert builder.findChild(QWidget, "configBuilderCompactCommandBar") is not None
        assert builder.findChild(QWidget, "configBuilderSummaryChips") is not None
        assert builder.findChild(QWidget, "configBuilderLeftPanel") is not None
        assert builder.findChild(QWidget, "configBuilderRightPanel") is not None
        assert builder.findChild(QWidget, "configBuilderAdvancedPanel").isHidden()
        assert "QWidget#configBuilderEmbeddedCentral QWidget" in builder.styleSheet()
        assert builder.findChild(QPushButton, "configBuilderSampleStartButton").text() == "샘플로 시작"
        assert builder.open_file_button.text() == "장비 변수 파일 열기"
        assert builder.add_row_button.text() == "빈 행 추가"
        assert builder.advanced_toggle_button.text() == "고급 작업"
        assert builder.copy_cli_button.text() == "복사"
        assert builder.copy_next_cli_button.text() == "복사+다음"
        assert "자동 적용하지 않고" in builder.copy_next_cli_button.toolTip()
        assert builder.table_model.headers == ["profile_id"]
        assert builder.table_model.rowCount() == 0
        assert builder.current_file_path is None
        assert builder.file_path_label.text() == "-"
        assert builder.main_splitter.sizes()[0] > 0
    finally:
        tab.close()


def test_config_builder_tab_uses_existing_builder_profile_blocks(qt_app, tmp_path: Path):
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        tab.show()
        QApplication.processEvents()
        builder = tab.builder_widget
        group_titles = {group.title(): group for group in builder.findChildren(QGroupBox)}
        for title in ("명령 블록 선택", "필터", "표시 컬럼", "파일 상태"):
            assert title in group_titles
            assert not group_titles[title].isVisible()

        builder.advanced_toggle_button.setChecked(True)
        builder.add_profile_combo.setCurrentText("SAMPLE_COMPREHENSIVE_REFERENCE")
        QApplication.processEvents()
        assert builder.findChild(QWidget, "configBuilderAdvancedPanel").isVisible()
        block_group = builder.findChild(QGroupBox, "configBuilderBlockToggleGroup")
        block_scroll = builder.findChild(QScrollArea, "configBuilderBlockToggleScroll")

        assert block_group is not None
        assert block_scroll is not None
        assert "QGroupBox#configBuilderBlockToggleGroup" in block_group.styleSheet()
        assert "QWidget#configBuilderBlockToggleContainer" in block_group.styleSheet()
        assert "#ffffff" in block_group.styleSheet()

        profile = builder.profiles["SAMPLE_COMPREHENSIVE_REFERENCE"]
        block_checks = []
        for index in range(builder.block_toggle_container_layout.count()):
            widget = builder.block_toggle_container_layout.itemAt(index).widget()
            if isinstance(widget, QCheckBox):
                block_checks.append(widget)
        block_names = [check.text() for check in block_checks]

        assert block_names == [block.name for block in profile.blocks]
        assert len(block_names) > 1
        assert builder.profile_summary_label.text().startswith("프로파일 ")
    finally:
        tab.close()


def test_config_builder_tab_initial_empty_state_is_actionable(qt_app, tmp_path: Path):
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        tab.show()
        QApplication.processEvents()
        builder = tab.builder_widget
        empty_state = builder.findChild(QWidget, "configBuilderEmptyState")
        empty_text = " ".join(label.text() for label in empty_state.findChildren(QLabel))

        assert empty_state is not None
        assert empty_state.isVisible()
        assert "아직 장비 목록이 없습니다." in empty_text
        assert "샘플로 시작" in [button.text() for button in empty_state.findChildren(QPushButton)]
        assert "장비 변수 파일 열기" in [button.text() for button in empty_state.findChildren(QPushButton)]
        assert "빈 행 추가" in [button.text() for button in empty_state.findChildren(QPushButton)]
        assert builder.table_model.rowCount() == 0
        assert builder.cli_preview.toPlainText() == ""
        assert builder.issue_list.item(0).text() == "선택한 장비 없음"
        assert not builder.copy_cli_button.isEnabled()
        assert not builder.copy_next_cli_button.isEnabled()
    finally:
        tab.close()


def test_config_builder_tab_empty_state_hides_after_first_row(qt_app, tmp_path: Path):
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        tab.show()
        QApplication.processEvents()
        builder = tab.builder_widget
        empty_state = builder.findChild(QWidget, "configBuilderEmptyState")

        assert empty_state.isVisible()
        builder.add_row()
        QApplication.processEvents()

        assert builder.table_model.rowCount() == 1
        assert not empty_state.isVisible()
        assert builder._current_source_row() == 0
        assert builder.findChild(QWidget, "configBuilderAdvancedPanel").isHidden()
    finally:
        tab.close()


def test_config_builder_tab_sample_start_shows_device_variable_columns(qt_app, tmp_path: Path):
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        tab.show()
        QApplication.processEvents()
        builder = tab.builder_widget
        builder.add_profile_combo.setCurrentText("CISCO_IOSXE_EDGE_PORT_BASE")
        QApplication.processEvents()

        builder._start_sample_for_current_profile()
        QApplication.processEvents()

        assert builder.table_model.rowCount() == 2
        assert "profile_id" in builder.table_model.headers
        assert "hostname" in builder.table_model.headers
        assert "access_interface" in builder.table_model.headers
        assert set(builder.visible_headers) == set(builder.table_model.headers)

        visible_columns = [
            header
            for column, header in enumerate(builder.table_model.headers)
            if not builder.table_view.isColumnHidden(column) or not builder.pinned_table_view.isColumnHidden(column)
        ]
        assert "profile_id" in visible_columns
        assert "hostname" in visible_columns
        assert "access_interface" in visible_columns
    finally:
        tab.close()


def test_config_builder_tab_loads_new_device_file_with_all_columns_visible(qt_app, tmp_path: Path):
    device_file = tmp_path / "devices.csv"
    device_file.write_text(
        "\n".join(
            [
                "profile_id,device_id,hostname,mgmt_ip,access_interface",
                "CISCO_IOSXE_EDGE_PORT_BASE,SW01,SW01,192.0.2.10,GigabitEthernet1/0/1",
            ]
        ),
        encoding="utf-8",
    )
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        tab.show()
        QApplication.processEvents()
        builder = tab.builder_widget

        builder.load_device_file(device_file)
        QApplication.processEvents()

        assert builder.table_model.rowCount() == 1
        assert "profile_id" in builder.visible_headers
        assert "device_id" in builder.visible_headers
        assert "hostname" in builder.visible_headers
        assert "access_interface" in builder.visible_headers
        assert set(builder.visible_headers) == set(builder.table_model.headers)
    finally:
        tab.close()


def test_config_builder_tab_profile_editor_uses_service_profiles_dir(qt_app, tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}

    class FakeProfileBuilderDialog:
        saved_profile_id = ""

        def __init__(self, profiles_dir, profile, parent=None):
            captured["profiles_dir"] = Path(profiles_dir)
            captured["profile_id"] = profile.id
            captured["parent"] = parent

        def exec(self):
            return False

    from netops_suite.modules.config_builder.switch_configurator import desktop_impl

    monkeypatch.setattr(desktop_impl, "ProfileBuilderDialog", FakeProfileBuilderDialog)
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        tab.builder_widget.add_profile_combo.setCurrentText("CISCO_IOS_L2_ACCESS_BASE")

        tab.builder_widget.edit_current_profile_dialog()

        assert captured["profiles_dir"] == tmp_path / "data" / "config_builder" / "profiles"
        assert captured["profile_id"] == "CISCO_IOS_L2_ACCESS_BASE"
        assert captured["parent"] is tab.builder_widget
    finally:
        tab.close()


def test_config_builder_tab_full_editor_receives_profiles_dir(qt_app, tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}

    class FakeDesktopWindow:
        def __init__(self, profiles_dir=None):
            captured["profiles_dir"] = Path(profiles_dir)
            self.loaded = None
            self.add_profile_combo = SimpleNamespace(setCurrentText=lambda text: captured.setdefault("profile_id", text))

        def load_device_file(self, path):
            self.loaded = Path(path)
            captured["loaded"] = self.loaded

        def show(self):
            captured["shown"] = True

        def raise_(self):
            captured["raised"] = True

        def activateWindow(self):
            captured["activated"] = True

    from app.ui.tabs import config_builder_tab

    monkeypatch.setattr(config_builder_tab, "DesktopWindow", FakeDesktopWindow)
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        tab.builder_widget.current_file_path = tmp_path / "devices.csv"
        tab.builder_widget.add_profile_combo.setCurrentText("CISCO_IOS_L2_ACCESS_BASE")

        tab._open_full_editor()

        assert captured["profiles_dir"] == tmp_path / "data" / "config_builder" / "profiles"
        assert captured["profile_id"] == "CISCO_IOS_L2_ACCESS_BASE"
        assert captured["loaded"] == tmp_path / "devices.csv"
        assert captured["shown"] is True
        assert captured["raised"] is True
        assert captured["activated"] is True
    finally:
        tab.close()


def test_config_builder_tab_no_longer_starts_external_helper_files():
    sources = "\n".join(
        [
            Path("app/ui/tabs/config_builder_tab.py").read_text(encoding="utf-8"),
            Path("netops_suite/modules/config_builder/switch_configurator/desktop_impl.py").read_text(encoding="utf-8"),
        ]
    )

    assert "os.startfile" not in sources
    assert "configBuilderGuideButton" not in sources
    assert "configBuilderSampleDeviceValuesButton" not in sources
    assert "configBuilderRequiredColumnsButton" not in sources
    assert "가이드 열기" not in sources
    assert "샘플 장비 변수 파일 열기" not in sources
    assert "필수 컬럼 보기" not in sources


def test_config_builder_full_editor_window_uses_netops_title(qt_app, tmp_path: Path):
    from netops_suite.modules.config_builder.switch_configurator.desktop_impl import DesktopWindow, SwitchConfigBuilderWidget

    window = DesktopWindow(profiles_dir=tmp_path / "profiles")
    try:
        assert window.windowTitle() == "CLI 설정 생성 - 전체 편집기"
        assert isinstance(window.builder, SwitchConfigBuilderWidget)
    finally:
        window.close()


def test_config_builder_full_editor_keeps_advanced_controls_available(qt_app, tmp_path: Path, monkeypatch):
    from netops_suite.modules.config_builder.switch_configurator import desktop_impl

    monkeypatch.setattr(desktop_impl, "APP_STATE_PATH", tmp_path / "missing_state.json")
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    window = desktop_impl.DesktopWindow(profiles_dir=tab.service.profiles_dir)
    try:
        window.show()
        QApplication.processEvents()
        builder = window.builder
        group_titles = {group.title(): group for group in builder.findChildren(QGroupBox)}

        assert builder._embedded is False
        assert not builder.main_toolbar.isHidden()
        assert builder.findChild(QWidget, "configBuilderFullEditorCentral") is not None
        assert "QWidget#configBuilderFullEditorCentral" in builder.styleSheet()
        assert "QGroupBox" in builder.styleSheet()
        assert "background: #ffffff" in builder.styleSheet()
        for title in ("프로파일 작업", "명령 블록 선택", "필터", "행 작업", "표시 컬럼", "파일 상태"):
            assert title in group_titles
            assert group_titles[title].isVisible()
        for widget in (
            builder.duplicate_row_button,
            builder.increment_duplicate_row_button,
            builder.delete_row_button,
            builder.auto_save_check,
            builder.allow_error_autosave_check,
            builder.select_cli_button,
            builder.mark_done_button,
            builder.reset_work_state_button,
            builder.detail_tabs,
        ):
            assert widget.isVisible()
    finally:
        window.close()
        tab.close()


def test_packaging_names_match_suite_release_contract():
    build_script = Path("scripts/build_release.ps1").read_text(encoding="utf-8")
    publish_script = Path("scripts/publish_release.ps1").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    installer_script = Path("installer/netops-suite.iss").read_text(encoding="utf-8")

    assert "NetOpsSuite" in build_script
    assert "NetOpsSuite-setup-*.exe" in build_script
    assert "SHA256SUMS.txt" in build_script
    assert "Get-FileHash" in build_script
    assert "ChecksumPath" in publish_script
    assert "AllowAssetReplace" in publish_script
    assert "Release asset already exists" in publish_script
    assert "SHA256SUMS.txt" in workflow
    assert "netops_suite\\modules\\inspector_runtime" in build_script
    assert "netops_suite/modules/inspector_runtime" in build_script
    assert "--collect-submodules=telnetlib3" in build_script
    assert "--collect-all=netmiko" in build_script
    assert "--collect-all=ntc_templates" in build_script
    assert "--hidden-import=msoffcrypto" in build_script
    assert "--hidden-import=xlrd" in build_script
    assert "allow_asset_replace" in workflow
    assert "manual_replace_existing_release" in workflow
    assert "Invoke-CodeSignFile" in build_script
    assert "RequireCodeSigning" in build_script
    assert "workflow_dispatch" in workflow
    assert "CodeSigningCertPath" not in workflow
    assert "NetOpsSuite-setup-{#AppVersion}" in installer_script
    assert "AppUserModelID" in installer_script
    assert 'IconFilename: "{app}\\NetOpsSuite.exe"' in installer_script
    assert "NetOpsSuite" in DEFAULT_UPDATE_ASSET_PATTERN


def test_main_app_icon_loads(qt_app):
    icon = load_app_icon()

    assert not icon.isNull()
    assert icon.availableSizes()


def test_inspector_tab_buttons_use_clear_workflow_labels(qt_app, tmp_path: Path):
    state = SimpleNamespace(
        thread_pool=QThreadPool.globalInstance(),
        paths=SimpleNamespace(data_root=tmp_path / "data"),
    )
    tab = InspectorTab(state)
    try:
        top_panel = tab.findChild(QWidget, "inspectorTopPanel")
        step_titles = []
        for index in range(top_panel.layout().count()):
            widget = top_panel.layout().itemAt(index).widget()
            if isinstance(widget, QGroupBox):
                step_titles.append(widget.title())

        template_group = tab.findChild(QGroupBox, "inspectorTemplateGroup")
        validation_group = tab.findChild(QGroupBox, "inspectorValidationGroup")
        result_group = tab.findChild(QGroupBox, "inspectorResultGroup")
        step_hint = tab.findChild(QLabel, "stepHint")

        def has_ancestor(widget: QWidget, ancestor: QWidget) -> bool:
            parent = widget.parentWidget()
            while parent is not None:
                if parent is ancestor:
                    return True
                parent = parent.parentWidget()
            return False

        assert step_titles == ["1. 장비 템플릿", "2. 인벤토리", "3. 실행 방식", "4. 검증 및 실행"]
        assert step_hint is not None
        assert "장비 템플릿 확인/관리" in step_hint.text()
        assert "실행 방식 선택" in step_hint.text()
        assert template_group is not None
        assert validation_group is not None
        assert result_group is not None
        assert result_group.title() == "5. 결과"
        assert tab.validate_button.text() == "먼저 검증"
        assert tab.run_button.text() == "실행"
        assert not tab.run_button.isEnabled()
        assert tab.template_editor_button.text() == "템플릿 관리"
        assert has_ancestor(tab.template_editor_button, template_group)
        assert not has_ancestor(tab.template_editor_button, validation_group)
        assert has_ancestor(tab.supported_toggle_button, template_group)
        assert has_ancestor(tab.supported_table, template_group)
        assert "지원 제조사(vendor)" in tab.template_editor_button.toolTip()
        assert "지원 제조사(vendor) 목록 로드 실패" not in tab.supported_label.text()
        assert tab.supported_table.minimumHeight() >= 160
        assert tab.log_view.minimumHeight() >= 140
        assert not tab.supported_toggle_button.isChecked()
        assert tab.supported_toggle_button.text() == "지원 보기"
        assert tab.supported_label.isHidden()
        assert tab.supported_table.isHidden()
        assert not tab.command_path_edit.isEnabled()
        assert not tab.command_button.isEnabled()
        assert "사용자 명령 모드" in tab.command_path_edit.placeholderText()
        tab.inventory_path_edit.setText(str(tmp_path / "inventory.xlsx"))
        assert tab.run_button.isEnabled()
        tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("custom_commands"))
        assert tab.command_path_edit.isEnabled()
        assert tab.command_button.isEnabled()
        assert "사용자 명령 파일" in tab.command_path_edit.placeholderText()
        assert not tab.run_button.isEnabled()
        tab.command_path_edit.setText(str(tmp_path / "commands.txt"))
        assert tab.run_button.isEnabled()
        tab.inventory_path_edit.clear()
        assert not tab.run_button.isEnabled()
        tab.inventory_path_edit.setText(str(tmp_path / "inventory.xlsx"))
        assert tab.run_button.isEnabled()
        tab._inspector_running = True
        tab._update_run_action_state()
        assert not tab.run_button.isEnabled()
        tab._inspector_running = False
        tab._update_run_action_state()
        assert tab.run_button.isEnabled()
        message = tab._inspector_error_message(ModuleNotFoundError("No module named 'vendors'"))
        assert "No module named" not in message
        assert "requirements.txt" in message
    finally:
        tab.close()


def test_inspector_tab_top_sections_use_white_background(qt_app, tmp_path: Path):
    state = SimpleNamespace(
        thread_pool=QThreadPool.globalInstance(),
        paths=SimpleNamespace(data_root=tmp_path / "data"),
    )
    tab = InspectorTab(state)
    try:
        template_group = tab.findChild(QGroupBox, "inspectorTemplateGroup")
        inventory_group = tab.findChild(QGroupBox, "inspectorInventoryGroup")
        execution_group = tab.findChild(QGroupBox, "inspectorExecutionGroup")
        validation_group = tab.findChild(QGroupBox, "inspectorValidationGroup")
        top_scroll = tab.findChild(QScrollArea, "inspectorTopScrollArea")

        assert template_group is not None
        assert inventory_group is not None
        assert execution_group is not None
        assert validation_group is not None
        assert top_scroll is not None
        style = top_scroll.styleSheet()
        assert "QGroupBox#inspectorTemplateGroup" in style
        assert "QGroupBox#inspectorInventoryGroup" in style
        assert "QGroupBox#inspectorExecutionGroup" in style
        assert "QGroupBox#inspectorValidationGroup" in style
        assert "QWidget#inspectorTopPanel" in style
        assert "#ffffff" in style
    finally:
        tab.close()


def test_artifacts_tab_shortens_path_column_and_keeps_table_readable(qt_app, tmp_path: Path):
    data_root = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    exports_dir = tmp_path / "exports"
    deep_path = logs_dir / "sessions" / "2026" / "run.log"
    deep_path.parent.mkdir(parents=True)
    deep_path.write_text("ok", encoding="utf-8")
    state = SimpleNamespace(
        paths=SimpleNamespace(logs_dir=logs_dir, exports_dir=exports_dir, data_root=data_root),
    )

    tab = ArtifactsTab(state)
    try:
        assert tab.table.minimumHeight() >= 220
        assert tab.table.rowCount() == 1
        path_item = tab.table.item(0, 3)
        assert path_item.toolTip() == str(deep_path)
        assert path_item.text() != str(deep_path)
        assert "..." in path_item.text()
    finally:
        tab.close()


def test_main_window_uses_purpose_based_tab_labels_and_step_hints(qt_app, tmp_path: Path):
    state = AppState(tmp_path)
    window = MainWindow(state)
    try:
        labels = [window.tab_widget.tabText(index) for index in range(window.tab_widget.count())]

        assert labels == [
            "네트워크 설정",
            "연결 진단",
            "Wi-Fi 분석",
            "장비 점검/백업",
            "CLI 설정 생성",
            "결과 파일",
            "프로그램 설정",
        ]
        diagnostic_labels = [
            window.diagnostics_tab.diagnostic_tool_list.item(index).text()
            for index in range(window.diagnostics_tab.diagnostic_tool_list.count())
        ]
        assert diagnostic_labels == [
            "Ping",
            "포트 확인 (TCPing)",
            "DNS 조회 (nslookup)",
            "경로 추적 (tracert/pathping)",
            "대역폭 측정 (iperf3)",
            "같은 대역 장비 찾기 (ARP 스캔)",
            "서브넷 계산기",
            "MAC 제조사 조회 (OUI)",
            "파일전송(FTP/SCP)",
            "명령 출력",
        ]
        window.state.is_admin = False
        window.interface_tab._update_admin_banner()
        window.interface_tab._update_action_states()
        assert not window.interface_tab.apply_button.isEnabled()
        assert not window.interface_tab.admin_banner.isHidden()
        assert "왼쪽 아래 '관리자'" in window.interface_tab.admin_label.text()
        assert window.restart_admin_action.isEnabled()
        for tab in (
            window.interface_tab,
            window.diagnostics_tab,
            window.wireless_tab,
            window.inspector_tab,
            window.config_builder_tab,
            window.artifacts_tab,
        ):
            hint = tab.findChild(QLabel, "stepHint")
            assert hint is not None
            assert hint.maximumHeight() <= 42
        assert window.diagnostics_tab.diagnostic_stack.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Ignored
    finally:
        window.close()


def test_main_workspace_uses_single_white_content_surface():
    assert "QWidget {\n    color: #1f2933;\n    background: #ffffff;" in APP_STYLE_SHEET
    assert "QWidget#appShell {\n    background: #ffffff;" in APP_STYLE_SHEET
    assert "QFrame#workspacePanel {\n    background: #ffffff;" in APP_STYLE_SHEET
    assert "QTabWidget::pane {\n    border: 0;\n    background: #ffffff;" in APP_STYLE_SHEET


def test_confirm_risky_action_message_contains_standard_sections(qt_app, monkeypatch):
    captured: dict[str, str] = {}

    def fake_exec(self):
        captured["title"] = self.windowTitle()
        captured["text"] = self.text()
        captured["confirm"] = self.button(QMessageBox.StandardButton.Yes).text()
        captured["cancel"] = self.button(QMessageBox.StandardButton.No).text()
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    assert confirm_risky_action(
        None,
        "위험 작업",
        "영향",
        "되돌리기 가능",
        "결과 위치",
        confirm_text="삭제",
        cancel_text="그만두기",
    )
    assert captured["title"] == "위험 작업"
    assert "영향 범위: 영향" in captured["text"]
    assert "되돌리기: 되돌리기 가능" in captured["text"]
    assert "기록 위치: 결과 위치" in captured["text"]
    assert captured["confirm"] == "삭제"
    assert captured["cancel"] == "그만두기"


def test_asset_path_resolves_pyinstaller_internal_assets(tmp_path: Path, monkeypatch):
    bundle_root = tmp_path / "_internal"
    icon_path = bundle_root / "assets" / "icons" / "netops_toolkit.ico"
    icon_path.parent.mkdir(parents=True)
    icon_path.write_bytes(b"icon")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "NetOpsSuite.exe"))

    assert resolve_asset_path("icons", "netops_toolkit.ico") == icon_path


def test_action_button_helper_sets_role_icon_and_state(qt_app):
    button = make_action_button(
        "테스트 실행",
        ActionKind.START,
        tooltip="작업을 시작합니다.",
        object_name="testActionButton",
        enabled=False,
    )

    assert button.text() == "테스트 실행"
    assert button.property("actionKind") == ActionKind.START.value
    assert button.objectName() == "testActionButton"
    assert button.toolTip() == "작업을 시작합니다."
    assert not button.isEnabled()
    assert not button.icon().isNull()


def test_ui_buttons_are_created_through_action_helper():
    roots = [Path("app"), Path("netops_suite/modules/config_builder/switch_configurator")]
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "QPushButton(" in text:
                offenders.append(str(path))

    assert offenders == []


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _config_builder_state(tmp_path: Path):
    return SimpleNamespace(paths=SimpleNamespace(data_root=tmp_path / "data"))


def _select_config_builder_profile(tab: ConfigBuilderTab, profile_id: str) -> None:
    for row in range(tab.profile_table.rowCount()):
        if tab.profile_table.item(row, 0).text() == profile_id:
            tab.profile_table.selectRow(row)
            QApplication.processEvents()
            return
    raise AssertionError(f"프로파일을 찾지 못했습니다: {profile_id}")
