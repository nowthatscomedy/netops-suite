from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QFileDialog, QMessageBox

from app.ui.tabs.inspector_tab import InspectorTab
from netops_suite.modules.config_builder.switch_configurator.engine import ConfigEngine
from netops_suite.modules.config_builder.switch_configurator.io_utils import parse_profile_yaml
from netops_suite.modules.config_builder.switch_configurator.models import DeviceRecord
from netops_suite.modules.config_builder.switch_configurator.table_data import (
    make_sample_table_row,
)
from netops_suite.modules.inspector.sample_inventory import (
    SAMPLE_BASE_COLUMNS,
    build_sample_inventory_rows,
    example_value_for_variable,
    sample_connection_type,
)


def test_sample_rows_follow_selected_device_and_skip_unused_columns():
    rows = build_sample_inventory_rows(
        vendor="juniper", os_name="junos", model="EX3400", mode="inspection"
    )

    assert len(rows) == 2
    assert list(rows[0]) == list(SAMPLE_BASE_COLUMNS)
    assert {row["vendor"] for row in rows} == {"juniper"}
    assert rows[0]["os"] == "junos"
    assert rows[0]["model"] == "EX3400"
    assert rows[0]["ip"] != rows[1]["ip"]
    assert "interface" not in rows[0]


def test_sample_rows_add_command_variables_only_in_custom_command_mode():
    custom = build_sample_inventory_rows(
        vendor="cisco",
        os_name="ios",
        mode="custom_commands",
        variable_names=("interface", "vlan_id", "ip"),
    )
    backup = build_sample_inventory_rows(
        vendor="cisco", os_name="ios", mode="backup", variable_names=("interface",)
    )

    assert custom[0]["interface"] == "GigabitEthernet1/0/1"
    assert custom[1]["interface"] == "GigabitEthernet1/0/2"
    assert custom[0]["vlan_id"] == 100
    assert custom[0]["ip"] == "192.0.2.10"  # base column is never overwritten
    assert "interface" not in backup[0]


def test_sample_rows_use_telnet_port_and_vendor_specific_enable():
    rows = build_sample_inventory_rows(
        vendor="axgate",
        os_name="axgate",
        connection_type=sample_connection_type(("telnet",)),
    )

    assert rows[0]["connection_type"] == "telnet"
    assert rows[0]["port"] == 23
    assert rows[0]["enable_password"] == ""
    assert sample_connection_type(("ssh", "telnet")) == "ssh"
    assert sample_connection_type(()) == "ssh"


def test_example_values_match_variable_names_and_vendor():
    assert example_value_for_variable("interface", "juniper") == "ge-0/0/1"
    assert example_value_for_variable("gateway") == "192.0.2.1"
    assert example_value_for_variable("hostname", index=1) == "SW-02"
    assert example_value_for_variable("mac_address").startswith("00:11:22")


def _inspector_tab(tmp_path: Path) -> InspectorTab:
    return InspectorTab(
        SimpleNamespace(
            thread_pool=QThreadPool.globalInstance(),
            paths=SimpleNamespace(data_root=tmp_path / "data", exports_dir=tmp_path / "exports"),
        )
    )


def test_inspector_sample_follows_selected_profile_mode_and_commands(
    qapp, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.Ok)
    tab = _inspector_tab(tmp_path)
    try:
        combo = tab.sample_profile_combo
        assert combo.count() > 0
        assert combo.currentText() == "cisco / ios"

        legacy_index = combo.findText("cisco / legacy")
        assert legacy_index >= 0
        tab.supported_table.selectRow(legacy_index)
        assert combo.currentIndex() == legacy_index

        tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("custom_commands"))
        tab.command_text_edit.setPlainText("show interface {{ interface }}\nshow vlan id {{ vlan_id }}")
        rows = tab.build_sample_inventory_rows()
        assert rows[0]["os"] == "legacy"
        assert rows[0]["connection_type"] == "telnet"
        assert rows[0]["interface"] == "GigabitEthernet1/0/1"
        assert rows[0]["vlan_id"] == 100

        chosen = tmp_path / "out" / "sample.xlsx"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(chosen), "")
        )
        assert tab._create_sample_inventory() == chosen
        devices = tab.service.load_inventory(str(chosen))
        assert [device["os"] for device in devices] == ["legacy", "legacy"]
        assert "interface" in tab.inventory_status_label.text()
    finally:
        tab.close()


def test_config_builder_generated_sample_row_renders(tmp_path: Path):
    profile = parse_profile_yaml(
        "\n".join(
            [
                "id: CUSTOM_PROFILE",
                "vendor: CISCO",
                "model: CUSTOM",
                "firmware: IOS-XE",
                "variables:",
                "  hostname: {required: true, type: string}",
                "  mgmt_ip: {required: true, type: ipv4}",
                "  mgmt_vlan: {required: true, type: int}",
                "  enable_ssh: {required: true, type: bool}",
                "blocks:",
                "  - name: base",
                "    lines:",
                '      - "hostname {{ hostname }}"',
                '      - "vlan {{ mgmt_vlan }}"',
                '      - "ip address {{ mgmt_ip }}"',
            ]
        ),
        "<test>",
    )
    headers = ["device_id", "profile_id", *profile.variables]
    rows = [make_sample_table_row(headers, profile, index) for index in range(2)]

    assert rows[0]["hostname"] == "SW-01"
    assert rows[1]["mgmt_ip"] == "192.0.2.12"
    assert rows[0]["mgmt_vlan"] == "100"
    assert rows[0]["enable_ssh"] == "true"
    engine = ConfigEngine({profile.id: profile})
    rendered = engine.render_device(DeviceRecord(row_number=2, values=rows[0])).text
    assert "hostname SW-01" in rendered
    assert "vlan 100" in rendered
