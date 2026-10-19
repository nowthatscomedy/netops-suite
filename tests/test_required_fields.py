from __future__ import annotations

from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox, QWidget

from app.ui.common.required import (
    check_required,
    make_required_legend,
    mark_missing,
    required_label,
)


def _required_names(widget: QWidget) -> set[str]:
    return {
        label.accessibleName()
        for label in widget.findChildren(QLabel, "requiredFieldLabel")
    }


def test_required_label_and_outline_clear_on_edit(qapp):
    label = required_label("호스트")
    assert "*" in label.text()
    assert label.accessibleName() == "호스트 (필수)"

    edit = QLineEdit()
    mark_missing(edit)
    assert edit.property("missingRequired") is True
    edit.setText("10.0.0.1")
    assert edit.property("missingRequired") is False


def test_check_required_names_empty_fields_in_legend(qapp):
    parent = QWidget()
    name, host = QLineEdit(parent), QLineEdit(parent)
    host.setText("ftp.example.com")
    legend = make_required_legend()
    assert "표시는 필수" in legend.text()

    assert not check_required(parent, [(name, "프로파일 이름"), (host, "호스트")], legend)
    assert "프로파일 이름" in legend.text() and "호스트" not in legend.text()
    assert name.property("missingRequired") and not host.property("missingRequired")

    name.setText("lab")
    assert check_required(parent, [(name, "프로파일 이름"), (host, "호스트")], legend)
    assert "표시는 필수" in legend.text()


def test_ftp_profile_dialog_requires_user_only_for_sftp(qapp, monkeypatch):
    from app.ui.dialogs.ftp_profile_dialog import FtpProfileDialog

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args))
    dialog = FtpProfileDialog()
    try:
        assert {"프로파일 이름 (필수)", "호스트 (필수)"} <= _required_names(dialog)
        assert "사용자명 (필수)" not in _required_names(dialog)
        dialog.protocol_combo.setCurrentIndex(dialog.protocol_combo.findData("sftp"))
        assert "사용자명 (필수)" in _required_names(dialog)

        dialog._handle_accept()
        assert dialog.result() == 0
        assert dialog.name_edit.property("missingRequired")
        assert dialog.username_edit.property("missingRequired")
        assert "필수 칸을 채워 주세요" in dialog.required_legend.text()
        assert warnings == []
    finally:
        dialog.close()


def test_ip_profile_requires_address_only_in_static_mode(qapp):
    from app.ui.dialogs.profile_editor_dialog import ProfileEditorDialog

    dialog = ProfileEditorDialog()
    try:
        dialog.name_edit.setText("현장")
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("static"))
        dialog.ip_edit.clear()
        assert "로컬 IPv4 (필수)" in _required_names(dialog)
        dialog._handle_accept()
        assert dialog.ip_edit.property("missingRequired")

        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("dhcp"))
        assert "로컬 IPv4 (필수)" not in _required_names(dialog)
    finally:
        dialog.close()


def test_scp_profile_dialog_marks_required(qapp):
    from app.ui.dialogs.scp_profile_dialog import ScpProfileDialog

    dialog = ScpProfileDialog()
    try:
        assert {"프로파일 이름 (필수)", "호스트 (필수)", "사용자명 (필수)"} <= _required_names(dialog)
        dialog._handle_accept()
        assert dialog.host_edit.property("missingRequired")
    finally:
        dialog.close()


def test_config_profile_builder_marks_required_basics(qapp, tmp_path, monkeypatch):
    from netops_suite.modules.config_builder.switch_configurator.profile_builder_dialog import (
        ProfileBuilderDialog,
    )

    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    dialog = ProfileBuilderDialog(tmp_path, None)
    try:
        assert {"프로파일 ID (필수)", "벤더 (필수)", "모델 (필수)", "펌웨어 (필수)",
                "변수명 (필수)", "블록 이름 (필수)"} <= _required_names(dialog)
        dialog.profile_id_edit.clear()
        dialog.save_profile()
        assert dialog.profile_id_edit.property("missingRequired")
        assert dialog.saved_path is None
    finally:
        dialog.close()


def test_diagnostic_input_error_outlines_the_field(qapp):
    from app.ui.tabs.diagnostics.core import DiagnosticsTab

    label, field = QLabel(), QLineEdit()
    DiagnosticsTab._show_diagnostic_input_error(label, field, "대상을 입력해 주세요.")
    assert field.property("missingRequired")


def test_inspector_inventory_label_appears_once(qapp, tmp_path):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QGroupBox

    from app.ui.tabs.inspector_tab import InspectorTab

    state = SimpleNamespace(thread_pool=None, paths=SimpleNamespace(data_root=tmp_path))
    tab = InspectorTab(state)
    try:
        group = tab.findChild(QGroupBox, "inspectorInventoryGroup")
        labels = [
            label for label in group.findChildren(QLabel)
            if label.text().startswith("장비 목록 Excel")
        ]
        assert labels == [tab.inventory_path_label]
        assert "장비 목록 Excel (필수)" in _required_names(group)
    finally:
        tab.close()
