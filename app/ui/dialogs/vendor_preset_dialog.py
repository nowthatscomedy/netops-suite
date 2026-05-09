from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from app.models.profile_models import VendorPreset
from app.utils.validators import ValidationError, parse_dns_servers, validate_ipv4, validate_optional_ipv4, validate_prefix


class VendorPresetDialog(QDialog):
    def __init__(self, parent=None, preset: VendorPreset | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("벤더 프리셋 편집")
        self.resize(420, 360)

        self.name_edit = QLineEdit(preset.name if preset else "")
        self.vendor_edit = QLineEdit(preset.target_vendor if preset else "")
        self.local_ip_edit = QLineEdit(preset.local_ip if preset else "")
        self.prefix_spin = QSpinBox()
        self.prefix_spin.setRange(1, 32)
        self.prefix_spin.setValue(preset.prefix if preset else 24)
        self.gateway_edit = QLineEdit(preset.gateway if preset else "")
        self.dns_edit = QPlainTextEdit(", ".join(preset.dns) if preset else "")
        self.target_ip_edit = QLineEdit(preset.default_target_ip if preset else "")
        self.notes_edit = QPlainTextEdit(preset.notes if preset else "")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("프리셋 이름", self.name_edit)
        form.addRow("벤더", self.vendor_edit)
        form.addRow("로컬 IPv4", self.local_ip_edit)
        form.addRow("Prefix", self.prefix_spin)
        form.addRow("게이트웨이", self.gateway_edit)
        form.addRow("DNS 서버", self.dns_edit)
        form.addRow("기본 대상 IP", self.target_ip_edit)
        form.addRow("메모", self.notes_edit)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._handle_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _handle_accept(self) -> None:
        try:
            self.preset_data()
        except ValidationError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return
        self.accept()

    def preset_data(self) -> VendorPreset:
        name = self.name_edit.text().strip()
        if not name:
            raise ValidationError("프리셋 이름을 입력해 주세요.")

        return VendorPreset(
            name=name,
            target_vendor=self.vendor_edit.text().strip(),
            local_ip=validate_ipv4(self.local_ip_edit.text(), "로컬 IPv4"),
            prefix=validate_prefix(self.prefix_spin.value()),
            gateway=validate_optional_ipv4(self.gateway_edit.text(), "게이트웨이"),
            dns=parse_dns_servers(self.dns_edit.toPlainText()),
            default_target_ip=validate_optional_ipv4(self.target_ip_edit.text(), "기본 대상 IP"),
            notes=self.notes_edit.toPlainText().strip(),
        )
