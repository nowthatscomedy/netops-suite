from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.ui.dialogs.inspector_vendor_template_dialog import InspectorVendorTemplateDialog, PythonParserDialog
from app.utils.file_utils import DEFAULT_UPDATE_ASSET_PATTERN
from netops_suite.modules.config_builder import ConfigBuilderService
from netops_suite.modules.inspector import InspectorService


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
    ]

    for sample_name in sample_names:
        result = service.render_file(service.device_values_dir / sample_name)
        assert not result.profile_issues
        assert not result.device_issues
        assert len(result.rendered) == 2
        assert "hostname" in result.bundle_text


def test_packaging_names_match_suite_release_contract():
    build_script = Path("scripts/build_release.ps1").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    installer_script = Path("installer/netops-suite.iss").read_text(encoding="utf-8")

    assert "NetOpsSuite" in build_script
    assert "NetOpsSuite-setup-*.exe" in build_script
    assert "custom_parsers.example.py" in build_script
    assert "Invoke-CodeSignFile" in build_script
    assert "RequireCodeSigning" in workflow
    assert "WINDOWS_CODESIGN_PFX_BASE64" in workflow
    assert "NetOpsSuite-setup-{#AppVersion}" in installer_script
    assert "NetOpsSuite" in DEFAULT_UPDATE_ASSET_PATTERN


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app
