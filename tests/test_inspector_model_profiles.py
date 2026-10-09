from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import yaml
from PySide6.QtWidgets import QMessageBox

from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog
from netops_suite.modules.inspector import InspectorService


def _base_and_model_rules() -> str:
    return """
inspection_commands:
  Test Vendor:
    Test OS:
      - show base
backup_commands:
  Test Vendor:
    Test OS: show base backup
parsing_rules:
  Test Vendor:
    Test OS:
      show base:
        output_column: Base
        pattern: 'BASE: (\\S+)'
connection_overrides:
  Test Vendor:
    Test OS:
      ssh: base_ssh
      telnet: base_telnet
handler_overrides:
  Test Vendor:
    Test OS:
      command_delay: 2
      read_delay: 0.2
model_profiles:
  Test Vendor:
    Test OS:
      Core  Model-24T:
        os_version: '17.9'
        inspection_commands:
          - show model
        backup_command: ''
        parsing_rules:
          show model:
            output_column: ModelValue
            pattern: 'MODEL: (\\S+)'
        connection_overrides:
          ssh: model_ssh
        handler_overrides:
          command_delay: 9
        output_columns:
          - ModelValue
      Other-48P:
        os_version: ''
        inspection_commands:
          - show other
        backup_command: show other backup
        parsing_rules: {}
        connection_overrides: {}
        handler_overrides: {}
        output_columns: []
""".lstrip()


def test_model_profile_exact_match_replaces_work_and_inherits_connection(
    tmp_path: Path,
):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    service.save_custom_rules_text(_base_and_model_rules())

    with service._runtime_import_path():
        from core.inspector import NetworkInspector
        from core.profile_resolver import resolve_device_profile

        matched = resolve_device_profile(
            " TEST   VENDOR ", "test os", " core model-24t "
        )
        punctuation_mismatch = resolve_device_profile(
            "test vendor", "test os", "core model 24t"
        )
        inherited_handler_only = resolve_device_profile(
            "test vendor", "test os", "Other-48P"
        )
        inspector = object.__new__(NetworkInspector)
        inspector.logger = logging.getLogger("test.inspector.model-profile")
        inspector.inspection_excludes = {}
        inspector.column_aliases = {}
        commands = inspector._get_device_commands(
            "test vendor", "test os", "Core Model-24T"
        )
        parsed = inspector._parse_command_output(
            "test vendor",
            "test os",
            "show model",
            "MODEL: selected",
            "Core Model-24T",
        )

    assert matched.model_matched is True
    assert matched.inspection_commands == ("show model",)
    assert matched.backup_command == ""
    assert set(matched.parsing_rules) == {"show model"}
    assert matched.connection_overrides == {
        "ssh": "model_ssh",
        "telnet": "base_telnet",
    }
    assert matched.handler_overrides == {"command_delay": 9, "read_delay": 0.2}
    assert matched.model_handler_overridden is True
    assert matched.output_columns == ("ModelValue",)
    assert commands == ["show model"]
    assert parsed == {"ModelValue": "selected"}

    assert punctuation_mismatch.model_requested is True
    assert punctuation_mismatch.model_matched is False
    assert "show base" in punctuation_mismatch.inspection_commands
    assert punctuation_mismatch.backup_command == "show base backup"
    assert inherited_handler_only.handler_overrides == {
        "command_delay": 2,
        "read_delay": 0.2,
    }
    assert inherited_handler_only.model_handler_overridden is False


def test_juniper_model_without_connection_overrides_keeps_netmiko_handler(
    tmp_path: Path,
    monkeypatch,
):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    service.save_custom_rules_text(
        """
model_profiles:
  juniper:
    junos:
      mx204:
        os_version: ''
        inspection_commands:
          - show version
        backup_command: ''
        parsing_rules: {}
        connection_overrides: {}
        handler_overrides: {}
        output_columns: []
""".lstrip()
    )
    monkeypatch.chdir(tmp_path)
    connection_params: list[dict[str, object]] = []

    class FakeNetmikoConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def enable(self):
            return None

        def check_enable_mode(self):
            return True

        def send_command_timing(self, _command):
            return ""

    def fake_connect_handler(**kwargs):
        connection_params.append(kwargs)
        return FakeNetmikoConnection()

    def fail_generic_handler(*_args, **_kwargs):
        raise AssertionError("empty model overrides must not select Paramiko")

    with service._runtime_import_path():
        from core import inspector as inspector_module
        from core.inspector import NetworkInspector
        from vendors import base as vendor_base

        monkeypatch.setattr(inspector_module, "ConnectHandler", fake_connect_handler)
        monkeypatch.setattr(inspector_module, "get_custom_handler", lambda *_args: None)
        monkeypatch.setattr(
            vendor_base,
            "GenericParamikoHandler",
            fail_generic_handler,
        )

        inspector = NetworkInspector(
            "inspection_results.xlsx",
            inspection_only=True,
            max_retries=1,
        )
        monkeypatch.setattr(inspector, "_test_tcping", lambda *_args: True)
        monkeypatch.setattr(inspector, "_print_cli_status", lambda *_args: None)
        device = {
            "ip": "192.0.2.20",
            "vendor": "Juniper",
            "os": "Junos",
            "model": "MX204",
            "connection_type": "ssh",
            "port": 22,
            "username": "operator",
            "password": "test-password",
        }

        returned_device, result = inspector._connect_to_device(
            device,
            inspection_mode=False,
            backup_mode=False,
        )

    assert returned_device is device
    assert result == {}
    assert len(connection_params) == 1
    assert connection_params[0]["device_type"] == "juniper_junos"


def test_model_profile_merge_preserves_legacy_and_other_models(tmp_path: Path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    service.save_custom_rules_text(_base_and_model_rules())
    replacement = service.build_simple_custom_rules_yaml(
        vendor="Test Vendor",
        os_name="Test OS",
        model="Core Model-24T",
        model_specific=True,
        os_version="18.1",
        inspection_commands=["show replacement"],
        backup_command="",
        parsing_rules={
            "show replacement": {
                "output_column": "Replacement",
                "pattern": r"VALUE: (\S+)",
            }
        },
        default_device_type="replacement_ssh",
        output_columns=["Replacement"],
    )

    assert service.custom_profile_exists(
        "test vendor", "test os", "core model-24t"
    )
    service.merge_custom_profile_rules_text(replacement)

    saved = yaml.safe_load(service.custom_rules_path.read_text(encoding="utf-8"))
    assert saved["inspection_commands"]["Test Vendor"]["Test OS"] == [
        "show base"
    ]
    model_profile = saved["model_profiles"]["test vendor"]["test os"][
        "core model-24t"
    ]
    assert model_profile["inspection_commands"] == ["show replacement"]
    assert model_profile["backup_command"] == ""
    assert model_profile["os_version"] == "18.1"
    assert model_profile["connection_overrides"]["ssh"] == "replacement_ssh"
    assert model_profile["handler_overrides"]["handler_type"] == "netmiko"
    assert (
        saved["model_profiles"]["test vendor"]["test os"]["Other-48P"][
            "inspection_commands"
        ]
        == ["show other"]
    )
    assert list(service.user_data_dir.glob("custom_rules.backup-*.yaml"))


def test_inventory_model_is_optional_and_unmatched_model_reports_warning(
    tmp_path: Path,
):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    service.save_custom_rules_text(_base_and_model_rules())
    inventory = tmp_path / "inventory.xlsx"
    pd.DataFrame(
        [
            {
                "ip": "192.0.2.10",
                "vendor": "test vendor",
                "os": "test os",
                "model": "Unknown-48P",
                "connection_type": "ssh",
                "port": 22,
                "username": "admin",
                "password": "test-password",
            },
            {
                "ip": "192.0.2.11",
                "vendor": "test vendor",
                "os": "test os",
                "model": "Core Model-24T",
                "connection_type": "ssh",
                "port": 22,
                "username": "admin",
                "password": "test-password",
            },
        ]
    ).to_excel(inventory, index=False)

    devices = service.load_inventory(str(inventory))
    warnings = service.inventory_profile_warnings(devices)

    assert [device["model"] for device in devices] == [
        "Unknown-48P",
        "Core Model-24T",
    ]
    assert len(warnings) == 1
    assert "unknown-48p" in warnings[0]
    assert "벤더/OS 프로파일" in warnings[0]


def test_profile_dialog_saves_selected_model_scope(
    qapp, tmp_path: Path, monkeypatch
):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    dialog = InspectorProfileDialog(service)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)
    try:
        dialog.profile_scope_combo.setCurrentIndex(
            dialog.profile_scope_combo.findData("model")
        )
        dialog.vendor_edit.setText("Dialog Vendor")
        dialog.os_edit.setText("Dialog OS")
        dialog.model_edit.setText("Dialog Model")
        dialog.os_version_edit.setText("1.0")
        dialog.backup_enabled_check.setChecked(False)
        dialog.refresh_preview()

        preview = yaml.safe_load(dialog.latest_yaml_text)
        assert "model_profiles" in preview
        draft = preview["model_profiles"]["dialog vendor"]["dialog os"][
            "dialog model"
        ]
        assert draft["inspection_commands"] == ["show version", "show inventory"]
        assert draft["backup_command"] == ""
        assert dialog.save_button.isEnabled()

        dialog._save_profile()
        saved = yaml.safe_load(service.custom_rules_path.read_text(encoding="utf-8"))
        assert (
            saved["model_profiles"]["dialog vendor"]["dialog os"][
                "dialog model"
            ]["os_version"]
            == "1.0"
        )

        dialog.backup_enabled_check.setChecked(True)
        dialog.backup_command_edit.setText("show model backup")
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        dialog._save_profile()
        overwritten = yaml.safe_load(
            service.custom_rules_path.read_text(encoding="utf-8")
        )
        assert (
            overwritten["model_profiles"]["dialog vendor"]["dialog os"][
                "dialog model"
            ]["backup_command"]
            == "show model backup"
        )
        assert list(service.user_data_dir.glob("custom_rules.backup-*.yaml"))
    finally:
        dialog._dirty = False
        dialog.close()


def test_profile_dialog_requires_regex_capture_group(qapp, tmp_path: Path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    dialog = InspectorProfileDialog(service)
    try:
        dialog.state["vendor"] = "Regex Vendor"
        dialog.state["os"] = "Regex OS"
        dialog.state["columns"][0]["method"] = "regex"
        dialog.state["columns"][0]["regex"] = r"Version\s+\S+"
        dialog._load_state()
        dialog.refresh_preview()

        assert not dialog.save_button.isEnabled()
        assert any(
            "캡처 그룹" in dialog.issue_list.item(index).text()
            for index in range(dialog.issue_list.count())
        )

        dialog.state["columns"][0]["regex"] = r"Version\s+(\S+)"
        dialog._load_state()
        dialog.refresh_preview()
        assert dialog.save_button.isEnabled(), [
            dialog.issue_list.item(index).text()
            for index in range(dialog.issue_list.count())
        ]
    finally:
        dialog._dirty = False
        dialog.close()


def test_profile_dialog_rejects_duplicate_commands_unknown_references_and_device_type(
    qapp,
    tmp_path: Path,
):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    dialog = InspectorProfileDialog(service)
    try:
        dialog.state["commands"].append(
            {"command": "SHOW VERSION", "sample": ""}
        )
        dialog.state["ssh_device_type"] = "invented_platform"
        dialog._load_state()
        dialog.refresh_preview()
        dialog.state["columns"][0]["command"] = "show missing"

        messages = [
            dialog.issue_list.item(index).text()
            for index in range(dialog.issue_list.count())
        ]
        messages.extend(dialog._validation_issues())
        assert any("중복" in message for message in messages)
        assert any("없는 명령" in message for message in messages)
        assert any("지원되지 않는 SSH" in message for message in messages)
        assert not dialog.save_button.isEnabled()
    finally:
        dialog._dirty = False
        dialog.close()


def test_inspector_profile_dialog_has_no_ai_draft_controls(qapp, tmp_path):
    from PySide6.QtWidgets import QPushButton

    from netops_suite.modules.inspector import InspectorService

    dialog = InspectorProfileDialog(InspectorService(user_data_dir=tmp_path))
    try:
        texts = [button.text() for button in dialog.findChildren(QPushButton)]
        assert all("AI" not in text for text in texts), texts
        assert not hasattr(dialog, "ai_draft_button")
    finally:
        dialog._dirty = False
        dialog.close()
