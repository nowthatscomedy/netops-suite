from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from app.models.ftp_models import FtpProfile
from app.utils.validators import (
    ValidationError,
    default_ftp_port,
    normalize_remote_path,
    parse_positive_int,
    validate_ftp_host,
    validate_ftp_protocol,
    validate_ftp_username,
)


class FtpProfileDialog(QDialog):
    def __init__(self, parent=None, profile: FtpProfile | None = None) -> None:
        super().__init__(parent)
        self._initial_profile = profile
        self.setWindowTitle("FTP 프로필 편집")
        self.resize(420, 280)

        self.name_edit = QLineEdit(profile.name if profile else "")
        self.name_edit.setPlaceholderText("예: 장비 백업 서버")

        self.protocol_combo = QComboBox()
        self.protocol_combo.addItem("FTP", "ftp")
        self.protocol_combo.addItem("FTPS", "ftps")
        self.protocol_combo.addItem("SFTP", "sftp")

        protocol = profile.protocol if profile else "ftp"
        index = self.protocol_combo.findData(protocol)
        self.protocol_combo.setCurrentIndex(index if index >= 0 else 0)

        self.host_edit = QLineEdit(profile.host if profile else "")
        self.host_edit.setPlaceholderText("예: 192.168.0.10 또는 ftp.example.com")

        self.port_edit = QLineEdit(str(profile.port if profile else default_ftp_port(protocol)))
        self.port_edit.setPlaceholderText(str(default_ftp_port(protocol)))

        self.username_edit = QLineEdit(profile.username if profile else "")
        self.username_edit.setPlaceholderText("예: operator")

        self.remote_path_edit = QLineEdit(profile.remote_path if profile else "/")
        self.remote_path_edit.setPlaceholderText("예: /upload")

        self.passive_check = QCheckBox("패시브 모드")
        self.passive_check.setChecked(profile.passive_mode if profile else True)

        self.timeout_edit = QLineEdit(str(profile.timeout_seconds if profile else 15))
        self.timeout_edit.setPlaceholderText("예: 15")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("프로필 이름", self.name_edit)
        form.addRow("프로토콜", self.protocol_combo)
        form.addRow("호스트", self.host_edit)
        form.addRow("포트", self.port_edit)
        form.addRow("사용자명", self.username_edit)
        form.addRow("기본 원격 경로", self.remote_path_edit)
        form.addRow("", self.passive_check)
        form.addRow("타임아웃(초)", self.timeout_edit)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._handle_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.protocol_combo.currentIndexChanged.connect(self._update_protocol_state)
        self._update_protocol_state()

    def _update_protocol_state(self) -> None:
        protocol = str(self.protocol_combo.currentData() or "ftp")
        is_passive_supported = protocol in {"ftp", "ftps"}
        self.passive_check.setEnabled(is_passive_supported)
        if not is_passive_supported:
            self.passive_check.setChecked(False)

        current_text = self.port_edit.text().strip()
        if not current_text:
            self.port_edit.setPlaceholderText(str(default_ftp_port(protocol)))
            return

        previous_protocol = self._initial_profile.protocol if self._initial_profile else "ftp"
        previous_default = default_ftp_port(previous_protocol)
        if current_text == str(previous_default):
            self.port_edit.setText(str(default_ftp_port(protocol)))
        self.port_edit.setPlaceholderText(str(default_ftp_port(protocol)))

    def _handle_accept(self) -> None:
        try:
            self.profile_data()
        except ValidationError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return
        self.accept()

    def profile_data(self) -> FtpProfile:
        name = self.name_edit.text().strip()
        if not name:
            raise ValidationError("프로필 이름을 입력해 주세요.")

        protocol = validate_ftp_protocol(str(self.protocol_combo.currentData() or "ftp"))
        host = validate_ftp_host(self.host_edit.text())
        username = validate_ftp_username(self.username_edit.text(), protocol)
        port = parse_positive_int(
            self.port_edit.text().strip() or str(default_ftp_port(protocol)),
            "포트",
            minimum=1,
            maximum=65535,
        )
        timeout_seconds = parse_positive_int(self.timeout_edit.text().strip() or "15", "타임아웃", minimum=1, maximum=300)

        return FtpProfile(
            name=name,
            protocol=protocol,
            host=host,
            port=port,
            username=username,
            remote_path=normalize_remote_path(self.remote_path_edit.text()),
            passive_mode=self.passive_check.isChecked() if protocol in {"ftp", "ftps"} else False,
            timeout_seconds=timeout_seconds,
        )
