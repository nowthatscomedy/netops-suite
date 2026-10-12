from __future__ import annotations

import pytest

from netops_suite.modules.inspector import InspectorService
from netops_suite.modules.inspector.extraction_suggest import (
    SelectionError,
    extract_value,
    suggest_extractions,
)

CISCO = """Cisco IOS XE Software, Version 17.09.04a
cisco C9300-24T (X86) processor with 1419044K/6147K bytes of memory.
Processor board ID FOC1234ABCD
Configuration register is 0x102
"""

AXGATE = """[System]
OS: AXGATE 2.1.3 build 220
Serial: AX12345678
Board: AXGATE-1000
"""

CPU = "CPU utilization for five seconds: 5%/0%; one minute: 7%; five minutes: 6%\n"

ALCATEL = "Software Version 6.6.5.170.R02 GA, January 22, 2016.\n"


def select(sample: str, value: str, occurrence: int = 0) -> tuple[int, int]:
    start = -1
    for _ in range(occurrence + 1):
        start = sample.index(value, start + 1)
    return start, start + len(value)


@pytest.mark.parametrize("sample,value,method", [
    (CISCO, "FOC1234ABCD", "keyword_after"),
    (CISCO, "17.09.04a", "keyword_after"),
    (AXGATE, "AX12345678", "keyword_after"),
    (AXGATE, "AXGATE 2.1.3 build 220", "keyword_after"),
    (CPU, "7%", "regex"),
    (ALCATEL, "6.6.5.170.R02", "regex"),
    (CISCO, "0x102", "keyword_after"),
    (CISCO, "1419044K", "regex"),
])
def test_best_suggestion_uses_a_label_and_extracts_the_selection(sample, value, method):
    suggestions = suggest_extractions(sample, *select(sample, value))
    best = suggestions[0]
    assert best.method == method
    assert not best.position_based
    assert extract_value(best.as_column("cmd"), sample) == value
    assert all(extract_value(item.as_column("cmd"), sample) == value for item in suggestions)


def test_label_rule_still_works_when_output_shifts():
    best = suggest_extractions(CISCO, *select(CISCO, "FOC1234ABCD"))[0]
    shifted = "Building configuration...\n\n" + CISCO.replace("FOC1234ABCD", "FOC9999ZZZZ")
    assert extract_value(best.as_column("cmd"), shifted) == "FOC9999ZZZZ"
    assert best.column_name == "Processor board ID"


def test_value_followed_by_label_at_line_start():
    sample = "  1419044K bytes of physical memory.\n  7341807K bytes of flash.\n"
    suggestions = suggest_extractions(sample, *select(sample, "7341807K"))
    assert extract_value(suggestions[0].as_column("cmd"), sample) == "7341807K"
    assert "flash" in suggestions[0].description


def test_selection_spaces_are_trimmed_and_position_fallback_exists():
    sample = "Port  Status  Vlan\nGi1/0/1  connected  10\n"
    start, end = select(sample, " connected ")
    suggestions = suggest_extractions(sample, start, end)
    assert suggestions[0].value == "connected"
    positional = [item for item in suggestions if item.position_based]
    assert positional and positional[0].line_number == 2 and positional[0].start_field == 2


@pytest.mark.parametrize("start,end,message", [
    (0, 0, "드래그"),
    (5, 60, "한 줄"),
])
def test_invalid_selections_explain_what_to_do(start, end, message):
    with pytest.raises(SelectionError, match=message):
        suggest_extractions(CISCO, start, end)


def test_suggested_rules_match_the_saved_runtime_parser(tmp_path):
    service = InspectorService(user_data_dir=tmp_path / "inspector")
    columns = [
        suggest_extractions(CISCO, *select(CISCO, value))[0].as_column("show version", name)
        for value, name in (("17.09.04a", "OS버전"), ("FOC1234ABCD", "시리얼번호"))
    ]
    rows = [
        {
            "command": column["command"],
            "output_column": column["name"],
            "parser_type": column["method"] if column["method"] != "regex" else "",
            "line_number": column["line_number"],
            "start_field": column["start_field"],
            "end_field": column["end_field"],
            "keyword": column["keyword"],
            "pattern": column["regex"] if column["method"] == "regex" else "",
        }
        for column in columns
    ]
    rules = service._build_parsing_rules_from_rows(rows)
    with service._runtime_import_path():
        from core.inspector import NetworkInspector
        from core.profile_resolver import ResolvedDeviceProfile

        inspector = NetworkInspector.__new__(NetworkInspector)
        inspector.inspection_excludes = {}
        import logging

        inspector.logger = logging.getLogger("test")
        inspector.column_aliases = {}
        profile = ResolvedDeviceProfile(
            vendor="v", os_name="o", model="", inspection_commands=("show version",),
            backup_command="", parsing_rules=rules, connection_overrides={}, handler_overrides={},
        )
        parsed = inspector._parse_command_output(
            "v", "o", "show version", CISCO, resolved_profile=profile
        )
    assert parsed == {"OS버전": "17.09.04a", "시리얼번호": "FOC1234ABCD"}


def test_profile_dialog_creates_column_from_dragged_value(qapp, tmp_path):
    from PySide6.QtGui import QTextCursor

    from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog

    dialog = InspectorProfileDialog(InspectorService(user_data_dir=tmp_path / "inspector"))
    try:
        dialog.vendor_edit.setText("QA")
        dialog.os_edit.setText("QA-OS")
        dialog.state["commands"][0]["sample"] = CISCO
        dialog._load_command(0)
        dialog.tabs.setCurrentWidget(dialog.column_tab)
        dialog.column_command_combo.setCurrentText("show version")
        dialog._refresh_extraction_preview()
        before = len(dialog.state["columns"])

        view = dialog.column_output_view
        start, end = select(view.toPlainText(), "17.09.04a")
        cursor = view.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        view.setTextCursor(cursor)
        dialog.pick_value_button.click()

        column = dialog.state["columns"][-1]
        assert len(dialog.state["columns"]) == before + 1
        assert column["name"] == "Version"
        assert column["command"] == "show version"
        assert dialog._extract_column_value(column, CISCO) == "17.09.04a"
        assert dialog.preview_label.text() == "추출 결과: 17.09.04a"
        assert "17.09.04a" in dialog.pick_status_label.text()
        assert dialog.suggestion_combo.count() >= 2

        dialog.suggestion_combo.setCurrentIndex(dialog.suggestion_combo.count() - 1)
        alternative = dialog.state["columns"][-1]
        assert alternative["name"] == "Version"
        assert dialog._extract_column_value(alternative, CISCO) == "17.09.04a"

        dialog.refresh_preview()
        assert "Version" in dialog.summary_preview.toPlainText()
    finally:
        dialog._dirty = False
        dialog.close()


def test_profile_dialog_pick_without_selection_explains(qapp, tmp_path):
    from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog

    dialog = InspectorProfileDialog(InspectorService(user_data_dir=tmp_path / "inspector"))
    try:
        before = list(dialog.state["columns"])
        dialog.tabs.setCurrentWidget(dialog.column_tab)
        dialog.pick_value_button.click()
        assert "드래그" in dialog.pick_status_label.text()
        assert dialog.state["columns"] == before
    finally:
        dialog._dirty = False
        dialog.close()


def test_profile_dialog_next_step_walks_the_flow(qapp, tmp_path):
    from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog

    dialog = InspectorProfileDialog(InspectorService(user_data_dir=tmp_path / "inspector"))
    try:
        dialog.show()
        dialog.vendor_edit.setText("QA")
        dialog.os_edit.setText("QA-OS")
        visited = [dialog.tabs.tabText(dialog.tabs.currentIndex())]
        while dialog.next_step_button.isVisible():
            dialog.next_step_button.click()
            visited.append(dialog.tabs.tabText(dialog.tabs.currentIndex()))
        assert visited == [
            "1. 장비 정보", "2. 점검 명령", "3. 백업 명령",
            "4. 장비로 시험", "5. Excel 컬럼", "6. 확인·저장",
        ]
    finally:
        dialog._dirty = False
        dialog.close()


def test_profile_dialog_marks_required_fields_and_blocks_empty_step(qapp, tmp_path):
    from PySide6.QtWidgets import QLabel

    from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog

    dialog = InspectorProfileDialog(InspectorService(user_data_dir=tmp_path / "inspector"))
    try:
        dialog.show()
        required = {
            label.accessibleName()
            for label in dialog.findChildren(QLabel, "requiredFieldLabel")
        }
        assert {"벤더 (필수)", "OS (필수)", "명령어 (필수)", "백업 명령 (필수)",
                "Excel 컬럼명 (필수)", "장비 IP (필수)"} <= required
        assert "*" in dialog.required_legend.text()
        assert not dialog.vendor_edit.property("missingRequired")

        dialog.next_step_button.click()
        assert dialog.tabs.currentWidget() is dialog.device_tab
        assert dialog.vendor_edit.property("missingRequired")
        assert dialog.os_edit.property("missingRequired")
        assert "벤더, OS" in dialog.required_legend.text()

        dialog.vendor_edit.setText("QA")
        dialog.os_edit.setText("QA-OS")
        dialog.refresh_preview()
        assert not dialog.vendor_edit.property("missingRequired")
        assert "표시는 필수" in dialog.required_legend.text()

        dialog.profile_scope_combo.setCurrentIndex(dialog.profile_scope_combo.findData("model"))
        assert dialog.model_label.accessibleName() == "모델 (필수)"
        dialog.next_step_button.click()
        assert dialog.tabs.currentWidget() is dialog.device_tab
        assert dialog.model_edit.property("missingRequired")
        dialog.model_edit.setText("QA-1")
        dialog.next_step_button.click()
        assert dialog.tabs.currentWidget() is dialog.command_tab
    finally:
        dialog._dirty = False
        dialog.close()
