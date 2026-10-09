from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from app.ui.common.disclosure import make_page_header


class TransferTab(QWidget):
    """Independent workspace backed by the existing transfer session owner."""

    def __init__(self, diagnostics, parent=None) -> None:
        super().__init__(parent)
        self.diagnostics = diagnostics
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(make_page_header("파일 전송", "작업과 프로토콜을 고르고 접속 정보를 입력하세요."))
        self.transfer_page = diagnostics.take_transfer_page()
        layout.addWidget(self.transfer_page, 1)
        self.transfer_page.show()

    def start_initial_refresh(self) -> None:
        # Entering this workspace never connects or starts a server.
        pass

    def select_role(self, role: str) -> None:
        self.diagnostics.file_transfer_role_combo.setCurrentIndex(1 if role == "server" else 0)
