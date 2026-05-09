from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from app.models.scp_models import ScpProfile
from app.utils.validators import ValidationError, parse_positive_int, validate_ftp_host, validate_ftp_username


class ScpProfileDialog(QDialog):
    def __init__(self, parent=None, profile: ScpProfile | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SCP 프로필 편집")
        self.resize(420, 220)

        self.name_edit = QLineEdit(profile.name if profile else "")
        self.name_edit.setPlaceholderText("예: 장비 백업 SCP")
        self.host_edit = QLineEdit(profile.host if profile else "")
        self.host_edit.setPlaceholderText("예: 192.168.0.10 또는 scp.example.com")
        self.port_edit = QLineEdit(str(profile.port if profile else 22))
        self.port_edit.setPlaceholderText("예: 22")
        self.username_edit = QLineEdit(profile.username if profile else "")
        self.username_edit.setPlaceholderText("예: operator")
        self.remote_path_edit = QLineEdit(profile.remote_path if profile else ".")
        self.remote_path_edit.setPlaceholderText("예: . 또는 /upload")
        self.timeout_edit = QLineEdit(str(profile.timeout_seconds if profile else 15))
        self.timeout_edit.setPlaceholderText("예: 15")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("프로필 이름", self.name_edit)
        form.addRow("호스트", self.host_edit)
        form.addRow("포트", self.port_edit)
        form.addRow("사용자명", self.username_edit)
        form.addRow("기본 원격 경로", self.remote_path_edit)
        form.addRow("타임아웃(초)", self.timeout_edit)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._handle_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _handle_accept(self) -> None:
        try:
            self.profile_data()
        except ValidationError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return
        self.accept()

    def profile_data(self) -> ScpProfile:
        name = self.name_edit.text().strip()
        if not name:
            raise ValidationError("프로필 이름을 입력해 주세요.")
        return ScpProfile(
            name=name,
            host=validate_ftp_host(self.host_edit.text()),
            port=parse_positive_int(self.port_edit.text().strip() or "22", "포트", minimum=1, maximum=65535),
            username=validate_ftp_username(self.username_edit.text(), "sftp"),
            remote_path=self.remote_path_edit.text().strip() or ".",
            timeout_seconds=parse_positive_int(self.timeout_edit.text().strip() or "15", "타임아웃", minimum=1, maximum=300),
        )
