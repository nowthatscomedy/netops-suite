from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import pandas as pd
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from app.ui.dialogs.inspector_vendor_template_dialog import InspectorVendorTemplateDialog, PythonParserDialog
from app.ui.tabs.inspector_tab import InspectorTab
from app.ui.tabs.config_builder_tab import ConfigBuilderTab
from app.utils.app_icon import load_app_icon
from app.utils.file_utils import DEFAULT_UPDATE_ASSET_PATTERN, resolve_asset_path
from netops_suite.modules.config_builder import ConfigBuilderRenderResult, ConfigBuilderService
from netops_suite.modules.config_builder.switch_configurator.models import RenderedConfig
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


def test_telnet_compat_uses_telnetlib3_not_deprecated_stdlib():
    from netops_suite.modules.inspector_runtime.core import telnet_compat

    assert telnet_compat.Telnet.__module__.startswith("telnetlib3.")


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


def test_config_builder_tab_shows_selected_profile_details_and_blocks(qt_app, tmp_path: Path):
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        _select_config_builder_profile(tab, "SAMPLE_COMPREHENSIVE_REFERENCE")

        blocks = [tab.block_list.item(index).text() for index in range(tab.block_list.count())]
        details = tab.profile_detail_view.toPlainText()
        profile = tab._selected_profile()

        assert profile is not None
        assert blocks == [block.name for block in profile.blocks]
        assert len(blocks) > 1
        assert "local_admin_secret" in details
        assert "sample_comprehensive_reference.yaml" in details
    finally:
        tab.close()


def test_config_builder_tab_action_buttons_follow_workflow_state(qt_app, tmp_path: Path):
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        button = lambda name: tab.findChild(QPushButton, name)

        assert button("configBuilderOpenDeviceValuesButton").text() == "장비값 열기"
        assert button("configBuilderOpenDeviceValuesButton").property("actionKind") == ActionKind.BROWSE.value
        assert button("configBuilderRenderButton").text() == "CLI 생성"
        assert button("configBuilderRenderButton").property("actionKind") == ActionKind.PRIMARY.value
        assert button("configBuilderEditProfileButton").text() == "선택 편집"
        assert button("configBuilderEditProfileButton").property("actionKind") == ActionKind.EDIT.value
        assert button("configBuilderFullEditorButton").text() == "고급 편집기"
        assert button("configBuilderCopyButton").text() == "선택 CLI 복사"
        assert button("configBuilderCopyButton").property("actionKind") == ActionKind.COPY.value
        assert button("configBuilderCopyNextButton").text() == "복사 후 다음"

        assert not button("configBuilderRenderButton").isEnabled()
        assert button("configBuilderEditProfileButton").isEnabled()
        assert not button("configBuilderCopyButton").isEnabled()
        assert not button("configBuilderSaveBundleButton").isEnabled()

        tab._device_values_path = str(tmp_path / "devices.csv")
        tab._update_action_states()
        assert button("configBuilderRenderButton").isEnabled()

        tab._last_result = ConfigBuilderRenderResult(
            profile_issues=[],
            device_issues=[],
            rendered=[
                RenderedConfig(
                    device_id="sw1",
                    profile_id="CISCO_IOS_L2_ACCESS_BASE",
                    text="hostname sw1",
                    values={},
                    display_name="sw1",
                )
            ],
            bundle_text="hostname sw1",
        )
        tab._fill_result_table()

        assert button("configBuilderCopyButton").isEnabled()
        assert button("configBuilderCopyNextButton").isEnabled()
        assert button("configBuilderSaveBundleButton").isEnabled()
        assert button("configBuilderSaveEachButton").isEnabled()
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

    from netops_suite.modules.config_builder.switch_configurator import profile_builder_dialog

    monkeypatch.setattr(profile_builder_dialog, "ProfileBuilderDialog", FakeProfileBuilderDialog)
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        _select_config_builder_profile(tab, "CISCO_IOS_L2_ACCESS_BASE")

        tab._open_selected_profile_editor()

        assert captured["profiles_dir"] == tmp_path / "data" / "config_builder" / "profiles"
        assert captured["profile_id"] == "CISCO_IOS_L2_ACCESS_BASE"
        assert captured["parent"] is tab
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

    from netops_suite.modules.config_builder.switch_configurator import desktop_impl

    monkeypatch.setattr(desktop_impl, "DesktopWindow", FakeDesktopWindow)
    tab = ConfigBuilderTab(_config_builder_state(tmp_path))
    try:
        tab._device_values_path = str(tmp_path / "devices.csv")
        _select_config_builder_profile(tab, "CISCO_IOS_L2_ACCESS_BASE")

        tab._open_full_editor()

        assert captured["profiles_dir"] == tmp_path / "data" / "config_builder" / "profiles"
        assert captured["profile_id"] == "CISCO_IOS_L2_ACCESS_BASE"
        assert captured["loaded"] == tmp_path / "devices.csv"
        assert captured["shown"] is True
        assert captured["raised"] is True
        assert captured["activated"] is True
    finally:
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
    assert "SHA256SUMS.txt" in workflow
    assert "netops_suite\\modules\\inspector_runtime" in build_script
    assert "netops_suite/modules/inspector_runtime" in build_script
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
        assert tab.validate_button.text() == "인벤토리 검증"
        assert tab.run_button.text() == "점검 실행"
        assert tab.template_editor_button.text() == "장비 템플릿 관리"
        assert "지원 벤더" in tab.template_editor_button.toolTip()
        assert "지원 벤더 목록 로드 실패" not in tab.supported_label.text()
    finally:
        tab.close()


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
