from __future__ import annotations

import logging
import threading
import unicodedata
from pathlib import Path
from types import MethodType, SimpleNamespace

import pandas as pd
import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QMessageBox

from app.ui.tabs.inspector_tab import InspectorTab
from netops_suite.modules.inspector import InspectorRunRequest, InspectorService
from netops_suite.modules.inspector_runtime.core.command_patterns import (
    prepare_custom_commands,
)
from netops_suite.modules.inspector_runtime.core.custom_exceptions import ValidationError


def _device(ip: str = "192.0.2.10", **values: object) -> dict[str, object]:
    device: dict[str, object] = {
        "ip": ip,
        "vendor": "cisco",
        "os": "ios",
        "connection_type": "ssh",
        "port": 22,
        "username": "admin",
        "password": "secret",
        "enable_password": "enable-secret",
    }
    device.update(values)
    return device


def _write_inventory(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows).to_excel(path, index=False)


CONTROL_CHARACTERS = tuple(
    chr(codepoint)
    for codepoint in (*range(0x20), 0x7F, *range(0x80, 0xA0))
)


def test_custom_command_patterns_render_per_device_and_preserve_plain_commands():
    prepared = prepare_custom_commands(
        [
            "show interface {{ interface }}",
            "show vlan {{ vlan_id }} on {{ interface }}",
            "ping {{ gateway }}",
            "show version",
        ],
        [
            _device(
                interface=" GigabitEthernet1/0/1 ",
                vlan_id=100.0,
                gateway="192.0.2.1",
            ),
            _device(
                "192.0.2.20",
                interface="Ethernet1/1",
                vlan_id=200,
                gateway="192.0.2.254",
            ),
        ],
    )

    assert prepared.command_count == 4
    assert prepared.variable_names == ("interface", "vlan_id", "gateway")
    assert prepared.commands_by_device["192.0.2.10"] == [
        "show interface GigabitEthernet1/0/1",
        "show vlan 100 on GigabitEthernet1/0/1",
        "ping 192.0.2.1",
        "show version",
    ]
    assert prepared.commands_by_device["192.0.2.20"][1] == (
        "show vlan 200 on Ethernet1/1"
    )


def test_custom_command_pattern_values_are_not_rendered_recursively():
    prepared = prepare_custom_commands(
        ["show {{ interface }}"],
        [_device(interface="{{ gateway }}", gateway="192.0.2.1")],
    )

    assert prepared.commands_by_device["192.0.2.10"] == [
        "show {{ gateway }}"
    ]


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("show {{ Missing }}", "영문 소문자"),
        ("show {{ interface | upper }}", "영문 소문자"),
        ("show {{ interface", "닫는 '}}'"),
        ("show interface }}", "여는 '{{'"),
        ("{% if interface %}show{% endif %}", "Jinja 제어 문법"),
        ("show {{ password }}", "인증정보 변수"),
        ("show {{ enable_password }}", "인증정보 변수"),
    ],
)
def test_custom_command_pattern_rejects_unsupported_syntax(
    command: str,
    message: str,
):
    with pytest.raises(ValidationError, match=message):
        prepare_custom_commands([command], [_device(interface="Gi1/0/1")])


@pytest.mark.parametrize(
    ("device_values", "message"),
    [
        ({}, "장비 목록에 해당 열이 없습니다"),
        ({"interface": ""}, "값이 비어 있습니다"),
        ({"interface": float("nan")}, "값이 비어 있습니다"),
        ({"interface": "Gi1/0/1\nreload"}, "제어 문자"),
        ({"interface": "Gi1/0/1\x00reload"}, "제어 문자"),
        ({"interface": "Gi1/0/1|redirect tftp://192.0.2.50/x"}, "CLI 구분자"),
    ],
)
def test_custom_command_pattern_rejects_invalid_device_values(
    device_values: dict[str, object],
    message: str,
):
    with pytest.raises(ValidationError) as exc_info:
        prepare_custom_commands(
            ["show interface {{ interface }}"],
            [_device(**device_values)],
        )

    error = str(exc_info.value)
    assert "Excel 2행" in error
    assert "장비 192.0.2.10" in error
    assert "명령 1" in error
    assert "변수 interface" in error
    assert message in error


@pytest.mark.parametrize(
    "control_character",
    CONTROL_CHARACTERS,
    ids=lambda character: f"U+{ord(character):04X}",
)
def test_custom_command_pattern_rejects_every_unicode_cc_control(
    control_character: str,
):
    assert unicodedata.category(control_character) == "Cc"

    with pytest.raises(ValidationError) as exc_info:
        prepare_custom_commands(
            ["show interface {{ interface }}"],
            [_device(interface=f"Gi1/0/1{control_character}reload")],
        )

    error = str(exc_info.value)
    assert "Excel 2행" in error
    assert "장비 192.0.2.10" in error
    assert "명령 1" in error
    assert "변수 interface" in error
    assert "제어 문자" in error


def test_custom_command_pattern_rejects_control_before_trimming():
    with pytest.raises(ValidationError, match="제어 문자"):
        prepare_custom_commands(
            ["show interface {{ interface }}"],
            [_device(interface="\tGi1/0/1")],
        )


@pytest.mark.parametrize("delimiter", list(";&|<>"))
def test_custom_command_pattern_rejects_cli_delimiters_in_values(
    delimiter: str,
):
    with pytest.raises(ValidationError) as exc_info:
        prepare_custom_commands(
            ["show interface {{ interface }}"],
            [_device(interface=f"Gi1/0/1{delimiter}reload")],
        )

    error = str(exc_info.value)
    assert "Excel 2행" in error
    assert "장비 192.0.2.10" in error
    assert "명령 1" in error
    assert "변수 interface" in error
    assert "CLI 구분자 또는 리디렉션 문자" in error


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("show interfaces | include {{ interface }}", "show interfaces | include up"),
        ("show {{ interface }}; show clock", "show up; show clock"),
        ("show {{ interface }} & show clock", "show up & show clock"),
        ("show {{ interface }} < bootflash:input", "show up < bootflash:input"),
        ("show {{ interface }} > bootflash:output", "show up > bootflash:output"),
    ],
)
def test_literal_cli_operators_remain_available_in_command_patterns(
    pattern: str,
    expected: str,
):
    prepared = prepare_custom_commands(
        [pattern],
        [_device(interface="up")],
    )

    assert prepared.commands_by_device["192.0.2.10"] == [expected]


def test_custom_command_validation_reports_only_first_twenty_errors():
    devices = [_device(f"192.0.2.{index}") for index in range(1, 23)]

    with pytest.raises(ValidationError) as exc_info:
        prepare_custom_commands(["show {{ interface }}"], devices)

    error = str(exc_info.value)
    assert error.count("장비 목록에 해당 열이 없습니다") == 20
    assert "나머지 2건은 생략했습니다. (전체 22건)" in error


def test_service_validates_normalized_excel_columns(tmp_path: Path):
    inventory_path = tmp_path / "inventory.xlsx"
    command_path = tmp_path / "commands.txt"
    _write_inventory(
        inventory_path,
        [_device(**{"Interface Name": "Gi1/0/1", "VLAN-ID": 100})],
    )
    command_path.write_text(
        "show interface {{ interface_name }}\nshow vlan {{ vlan_id }}\n",
        encoding="utf-8",
    )
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    devices = service.load_inventory(str(inventory_path))

    summary = service.validate_custom_command_file(str(command_path), devices)

    assert summary.command_count == 2
    assert summary.variable_names == ("interface_name", "vlan_id")


def test_service_blocks_unsafe_substitution_before_creating_run_artifacts(
    tmp_path: Path,
):
    inventory_path = tmp_path / "inventory.xlsx"
    work_dir = tmp_path / "runs"
    work_dir.mkdir()
    _write_inventory(
        inventory_path,
        [_device(interface="Gi1/0/1|redirect tftp://192.0.2.50/x")],
    )
    service = InspectorService(
        work_dir=work_dir,
        user_data_dir=tmp_path / "inspector",
    )

    # InspectorService loads the migrated runtime through its isolated ``core``
    # import path, so the runtime exception class has a distinct module identity.
    with pytest.raises(Exception, match="CLI 구분자"):
        service.run(
            InspectorRunRequest(
                inventory_path=str(inventory_path),
                mode="custom_commands",
                commands=["show interface {{ interface }}"],
            )
        )

    assert not (work_dir / "results").exists()
    assert not (work_dir / "session_logs").exists()
    assert not (work_dir / "backup").exists()


def test_network_inspector_dispatches_rendered_commands_per_device(tmp_path: Path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    service._ensure_runtime_modules_current()
    with service._runtime_import_path():
        from core.inspector import NetworkInspector

        inspector = NetworkInspector.__new__(NetworkInspector)
        inspector.devices = [_device(), _device("192.0.2.20")]
        inspector.max_workers = 2
        inspector.results = []
        inspector.results_lock = threading.Lock()
        inspector.logger = logging.getLogger("test.custom-command-dispatch")
        inspector.status_callback = lambda _event: None
        inspector.cancel_event = None
        dispatched: dict[str, list[str]] = {}

        def fake_run_device(self, device, commands, session_log_suffix=None):
            del self, session_log_suffix
            device_ip = str(device["ip"])
            dispatched[device_ip] = list(commands)
            return {
                "ip": device_ip,
                "vendor": device["vendor"],
                "os": device["os"],
                "status": "success",
                "error_message": "",
                "inspection_results": {"command_1": commands[0]},
            }

        inspector._run_custom_commands_device = MethodType(
            fake_run_device,
            inspector,
        )
        inspector.run_custom_commands(
            {
                "192.0.2.10": ["show interface Gi1/0/1"],
                "192.0.2.20": ["show interface Eth1/1"],
            }
        )

    assert dispatched == {
        "192.0.2.10": ["show interface Gi1/0/1"],
        "192.0.2.20": ["show interface Eth1/1"],
    }
    executed_commands = [
        result["inspection_results"]["command_1"] for result in inspector.results
    ]
    assert executed_commands == [
        "show interface Gi1/0/1",
        "show interface Eth1/1",
    ]


def test_inspector_ui_validates_commands_and_invalidates_changed_file(
    qapp,
    tmp_path: Path,
    monkeypatch,
):
    inventory_path = tmp_path / "inventory.xlsx"
    command_path = tmp_path / "commands.txt"
    _write_inventory(
        inventory_path,
        [_device(interface="Gi1/0/1", vlan_id=100)],
    )
    command_path.write_text(
        "show interface {{ interface }}\nshow vlan {{ vlan_id }}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.Ok,
    )
    state = SimpleNamespace(
        thread_pool=QThreadPool.globalInstance(),
        paths=SimpleNamespace(
            data_root=tmp_path / "data",
            exports_dir=tmp_path / "exports",
        ),
    )
    tab = InspectorTab(state)
    try:
        assert tab.command_variable_hint.isHidden()
        tab.mode_combo.setCurrentIndex(
            tab.mode_combo.findData("custom_commands")
        )
        assert not tab.command_variable_hint.isHidden()

        tab.inventory_path_edit.setText(str(inventory_path))
        tab.command_path_edit.setText(str(command_path))
        tab._validate_inventory()

        assert tab._inventory_validated
        assert tab._custom_command_validation is not None
        assert "명령 2개" in tab.validation_status_label.text()
        assert "사용 변수 interface, vlan_id" in tab.validation_status_label.text()

        tab.command_path_edit.setText(str(tmp_path / "changed.txt"))
        assert not tab._inventory_validated
        assert tab._custom_command_validation is None
        assert "다시 검증" in tab.validation_status_label.text()
    finally:
        tab.close()
