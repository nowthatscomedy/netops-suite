from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from app.models.profile_models import IPProfile
from app.utils.validators import (
    ValidationError,
    parse_dns_servers,
    validate_ipv4,
    validate_optional_ipv4,
    validate_prefix,
)


class ProfileEditorDialog(QDialog):
    def __init__(self, parent=None, profile: IPProfile | None = None) -> None:
        super().__init__(parent)
        self._initial_profile = profile
        self.setWindowTitle("IP 프로필 편집")
        self.resize(420, 360)

        self.name_edit = QLineEdit(profile.name if profile else "")
        self.name_edit.setPlaceholderText("예: 현장 테스트 프로필")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("수동(static)", "static")
        self.mode_combo.addItem("자동(DHCP)", "dhcp")
        current_mode = profile.mode if profile else "static"
        self.mode_combo.setCurrentIndex(0 if current_mode == "static" else 1)
        self.interface_edit = QLineEdit(profile.interface_name if profile else "")
        self.interface_edit.setPlaceholderText("예: Wi-Fi")
        self.ip_edit = QLineEdit(profile.local_ip if profile else "")
        self.ip_edit.setPlaceholderText("예: 192.168.0.10")
        self.prefix_edit = QLineEdit(str(profile.prefix if profile else 24))
        self.prefix_edit.setPlaceholderText("예: 24 또는 255.255.255.0")
        self.gateway_edit = QLineEdit(profile.gateway if profile else "")
        self.gateway_edit.setPlaceholderText("예: 192.168.0.1")
        self.dns_edit = QPlainTextEdit(", ".join(profile.dns) if profile else "")
        self.dns_edit.setPlaceholderText("예: 8.8.8.8, 1.1.1.1")
        self.dns_edit.setMaximumHeight(68)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("프로필 이름", self.name_edit)
        form.addRow("모드", self.mode_combo)
        form.addRow("기본 인터페이스", self.interface_edit)
        form.addRow("로컬 IPv4", self.ip_edit)
        form.addRow("Prefix / 마스크", self.prefix_edit)
        form.addRow("게이트웨이", self.gateway_edit)
        form.addRow("DNS 서버", self.dns_edit)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._handle_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.mode_combo.currentTextChanged.connect(self._update_mode_state)
        self._update_mode_state(self.mode_combo.currentText())

    def _update_mode_state(self, mode: str) -> None:
        is_static = self.mode_combo.currentData() == "static"
        self.ip_edit.setEnabled(is_static)
        self.prefix_edit.setEnabled(is_static)
        self.gateway_edit.setEnabled(is_static)
        self.dns_edit.setEnabled(True)

    def _handle_accept(self) -> None:
        try:
            self.profile_data()
        except ValidationError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return
        self.accept()

    def profile_data(self) -> IPProfile:
        name = self.name_edit.text().strip()
        if not name:
            raise ValidationError("프로필 이름을 입력해 주세요.")

        mode = str(self.mode_combo.currentData())
        if mode == "static":
            local_ip = validate_ipv4(self.ip_edit.text(), "로컬 IPv4")
            prefix = validate_prefix(self.prefix_edit.text())
            gateway = validate_optional_ipv4(self.gateway_edit.text(), "게이트웨이")
        else:
            local_ip = ""
            prefix = 24
            gateway = ""

        return IPProfile(
            name=name,
            mode=mode,
            interface_name=self.interface_edit.text().strip(),
            local_ip=local_ip,
            prefix=prefix,
            gateway=gateway,
            dns=parse_dns_servers(self.dns_edit.toPlainText()),
            target_vendor=self._initial_profile.target_vendor if self._initial_profile else "",
            target_ip=self._initial_profile.target_ip if self._initial_profile else "",
            notes=self._initial_profile.notes if self._initial_profile else "",
        )
