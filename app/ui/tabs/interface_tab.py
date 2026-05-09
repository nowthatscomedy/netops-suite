from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.models.network_models import NetworkAdapterInfo
from app.models.profile_models import IPProfile
from app.ui.dialogs.profile_editor_dialog import ProfileEditorDialog
from app.utils.threading_utils import FunctionWorker
from app.utils.validators import (
    ValidationError,
    format_prefix,
    parse_dns_servers,
    validate_ipv4,
    validate_optional_ipv4,
    validate_prefix,
)


class InterfaceTab(QWidget):
    status_message = Signal(str)

    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.adapters: list[NetworkAdapterInfo] = []
        self._active_workers: list[FunctionWorker] = []
        self._pending_ui_state: dict = {}
        self._startup_refresh_requested = False

        self._build_ui()
        self.state.config_reloaded.connect(self._reload_lists)
        self._reload_lists()

    def start_initial_refresh(self) -> None:
        if self._startup_refresh_requested:
            return
        self._startup_refresh_requested = True
        self.refresh_adapters()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.admin_label = QLabel()
        self.admin_label.setWordWrap(True)
        self._update_admin_banner()
        layout.addWidget(self.admin_label)

        top_row = QHBoxLayout()
        self.refresh_button = QPushButton("인터페이스 새로고침")
        self.loading_label = QLabel("인터페이스 정보를 불러오는 중입니다...")
        self.loading_label.hide()
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setMaximumWidth(220)
        self.loading_bar.hide()
        top_row.addWidget(self.refresh_button)
        top_row.addWidget(self.loading_label)
        top_row.addWidget(self.loading_bar)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        adapter_group = QGroupBox("네트워크 인터페이스")
        adapter_layout = QVBoxLayout(adapter_group)
        self.adapter_table = QTableWidget(0, 8)
        self.adapter_table.setHorizontalHeaderLabels(
            ["이름", "설명", "상태", "DHCP", "IPv4", "Prefix", "Gateway", "DNS"]
        )
        self.adapter_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.adapter_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.adapter_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.adapter_table.verticalHeader().setVisible(False)
        self.adapter_table.horizontalHeader().setStretchLastSection(True)
        adapter_layout.addWidget(self.adapter_table)
        splitter.addWidget(adapter_group)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        form_group = QGroupBox("선택 인터페이스 설정")
        form_layout = QFormLayout(form_group)
        self.selected_interface_label = QLabel("-")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("자동 (DHCP)", "dhcp")
        self.mode_combo.addItem("수동 IP", "static")
        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("예: 192.168.0.10")
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setPlaceholderText("예: 24 또는 255.255.255.0")
        self.gateway_edit = QLineEdit()
        self.gateway_edit.setPlaceholderText("예: 192.168.0.1")
        self.dns_edit = QPlainTextEdit()
        self.dns_edit.setMaximumHeight(72)
        self.dns_edit.setPlaceholderText("예: 8.8.8.8, 1.1.1.1")

        apply_row = QHBoxLayout()
        self.apply_button = QPushButton("적용")
        self.save_current_button = QPushButton("현재값 저장")
        apply_row.addWidget(self.apply_button)
        apply_row.addWidget(self.save_current_button)

        form_layout.addRow("인터페이스", self.selected_interface_label)
        form_layout.addRow("적용 모드", self.mode_combo)
        form_layout.addRow("로컬 IPv4", self.ip_edit)
        form_layout.addRow("Prefix / 마스크", self.prefix_edit)
        form_layout.addRow("게이트웨이", self.gateway_edit)
        form_layout.addRow("DNS", self.dns_edit)
        form_layout.addRow("", apply_row)
        right_layout.addWidget(form_group)

        profile_group = QGroupBox("저장된 IP 프로필")
        profile_layout = QVBoxLayout(profile_group)
        self.profile_list = QListWidget()
        profile_layout.addWidget(self.profile_list)

        detail_form = QFormLayout()
        self.profile_mode_label = QLabel("-")
        self.profile_summary_label = QLabel("-")
        self.profile_summary_label.setWordWrap(True)
        detail_form.addRow("모드", self.profile_mode_label)
        detail_form.addRow("설정", self.profile_summary_label)
        profile_layout.addLayout(detail_form)

        button_row = QHBoxLayout()
        self.profile_apply_button = QPushButton("프로필 적용")
        self.profile_add_button = QPushButton("추가")
        self.profile_edit_button = QPushButton("수정")
        self.profile_delete_button = QPushButton("삭제")
        button_row.addWidget(self.profile_apply_button)
        button_row.addWidget(self.profile_add_button)
        button_row.addWidget(self.profile_edit_button)
        button_row.addWidget(self.profile_delete_button)
        profile_layout.addLayout(button_row)

        right_layout.addWidget(profile_group)
        right_layout.addStretch(1)
        splitter.addWidget(right_panel)
        splitter.setSizes([760, 360])

        self.refresh_button.clicked.connect(self.refresh_adapters)
        self.adapter_table.itemSelectionChanged.connect(self._load_selected_adapter_into_form)
        self.mode_combo.currentIndexChanged.connect(self._update_mode_state)
        self.apply_button.clicked.connect(self.apply_current_settings)
        self.save_current_button.clicked.connect(self.save_current_as_profile)
        self.profile_list.itemSelectionChanged.connect(self._show_selected_profile_details)
        self.profile_apply_button.clicked.connect(self.apply_selected_profile)
        self.profile_add_button.clicked.connect(self.add_profile)
        self.profile_edit_button.clicked.connect(self.edit_selected_profile)
        self.profile_delete_button.clicked.connect(self.delete_selected_profile)

        self._update_mode_state()

    def _update_admin_banner(self) -> None:
        if self.state.is_admin:
            self.admin_label.setText(
                "관리자 권한으로 실행 중입니다. IP, DNS, 어댑터 변경 작업을 바로 적용할 수 있습니다."
            )
            self.admin_label.setStyleSheet(
                "background:#e8f5e9; color:#1b5e20; padding:8px; border:1px solid #a5d6a7;"
            )
        else:
            self.admin_label.setText(
                "일반 권한으로 실행 중입니다. 네트워크 설정 변경 작업은 관리자 권한이 필요합니다."
            )
            self.admin_label.setStyleSheet(
                "background:#fff8e1; color:#8d6e00; padding:8px; border:1px solid #ffe082;"
            )

    def _reload_lists(self) -> None:
        selected_name = self._selected_profile().name if self._selected_profile() else ""
        self.profile_list.clear()
        for profile in self.state.ip_profiles:
            self.profile_list.addItem(profile.name)
        if selected_name:
            self._select_profile_by_name(selected_name)
        elif self.profile_list.count():
            self.profile_list.setCurrentRow(0)
        self._show_selected_profile_details()

    def refresh_adapters(self) -> None:
        previous_name = self._selected_adapter().name if self._selected_adapter() else ""
        self._start_worker(
            self.state.network_interface_service.list_adapters,
            on_started=lambda: self._set_loading(True),
            on_result=lambda adapters: self._populate_adapter_table(adapters, previous_name),
            on_finished=lambda: self._set_loading(False),
            error_title="인터페이스 조회 실패",
        )

    def _set_loading(self, loading: bool) -> None:
        self.refresh_button.setEnabled(not loading)
        self.adapter_table.setEnabled(not loading)
        self.loading_label.setVisible(loading)
        self.loading_bar.setVisible(loading)
        if loading:
            self.status_message.emit("인터페이스 정보를 불러오는 중입니다...")

    def _populate_adapter_table(
        self,
        adapters: list[NetworkAdapterInfo],
        preferred_name: str = "",
    ) -> None:
        self.adapters = adapters
        self.adapter_table.setRowCount(0)
        selected_row = 0

        for row, adapter in enumerate(adapters):
            self.adapter_table.insertRow(row)
            values = [
                adapter.name,
                adapter.interface_description,
                "연결됨" if adapter.status.lower() == "up" else adapter.status,
                "사용" if adapter.dhcp_enabled else "사용 안 함",
                adapter.ipv4 or "-",
                format_prefix(adapter.prefix_length) if adapter.prefix_length else "-",
                adapter.gateway or "-",
                adapter.dns_text() or "-",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 2:
                    item.setForeground(
                        QColor("#1b5e20") if adapter.status.lower() == "up" else QColor("#b71c1c")
                    )
                self.adapter_table.setItem(row, column, item)
            if preferred_name and adapter.name == preferred_name:
                selected_row = row

        if adapters:
            self.adapter_table.selectRow(selected_row)
            if self._pending_ui_state:
                self._apply_saved_state(self._pending_ui_state)
                self._pending_ui_state = {}
            self.status_message.emit(f"네트워크 인터페이스 {len(adapters)}개를 불러왔습니다.")
        else:
            self.selected_interface_label.setText("-")
            self.status_message.emit("네트워크 인터페이스를 찾지 못했습니다.")

    def _selected_adapter(self) -> NetworkAdapterInfo | None:
        selection = self.adapter_table.selectionModel().selectedRows()
        if not selection:
            return None
        row = selection[0].row()
        if 0 <= row < len(self.adapters):
            return self.adapters[row]
        return None

    def _selected_profile(self) -> IPProfile | None:
        row = self.profile_list.currentRow()
        if row < 0 or row >= len(self.state.ip_profiles):
            return None
        return self.state.ip_profiles[row]

    def _load_selected_adapter_into_form(self) -> None:
        adapter = self._selected_adapter()
        if not adapter:
            self.selected_interface_label.setText("-")
            return
        self.selected_interface_label.setText(adapter.name)
        self.mode_combo.setCurrentIndex(0 if adapter.dhcp_enabled else 1)
        self.ip_edit.setText(adapter.ipv4 or "")
        self.prefix_edit.setText(str(adapter.prefix_length or 24))
        self.gateway_edit.setText(adapter.gateway or "")
        self.dns_edit.setPlainText(", ".join(adapter.dns_servers))
        self._update_mode_state()

    def _update_mode_state(self) -> None:
        is_static = self.mode_combo.currentData() == "static"
        self.ip_edit.setEnabled(is_static)
        self.prefix_edit.setEnabled(is_static)
        self.gateway_edit.setEnabled(is_static)
        self.dns_edit.setEnabled(is_static)

    def _show_selected_profile_details(self) -> None:
        profile = self._selected_profile()
        if not profile:
            self.profile_mode_label.setText("-")
            self.profile_summary_label.setText("-")
            return

        self.profile_mode_label.setText("자동 (DHCP)" if profile.mode == "dhcp" else "수동 IP")
        if profile.mode == "dhcp":
            self.profile_summary_label.setText("선택한 인터페이스를 DHCP와 자동 DNS로 전환합니다.")
        else:
            dns_text = ", ".join(profile.dns) if profile.dns else "-"
            self.profile_summary_label.setText(
                f"IP {profile.local_ip}/{profile.prefix}, GW {profile.gateway or '-'}, DNS {dns_text}"
            )

    def apply_current_settings(self) -> None:
        adapter = self._selected_adapter()
        if not adapter:
            QMessageBox.warning(self, "선택 필요", "먼저 인터페이스를 선택해 주세요.")
            return
        if not self._ensure_admin():
            return

        mode = str(self.mode_combo.currentData())
        if mode == "dhcp":
            if not self._confirm_apply(
                title="DHCP 적용 확인",
                interface_name=adapter.name,
                current_lines=self._adapter_summary_lines(adapter),
                target_lines=[
                    "모드: 자동 (DHCP)",
                    "IPv4: DHCP 할당",
                    "DNS: 자동",
                ],
            ):
                return
            self._start_worker(
                self.state.network_interface_service.set_dhcp,
                adapter.name,
                on_result=lambda result: self._handle_operation_result(result, refresh_after=True),
                error_title="DHCP 적용 실패",
            )
            return

        try:
            ip_value = validate_ipv4(self.ip_edit.text(), "로컬 IPv4")
            prefix_value = validate_prefix(self.prefix_edit.text())
            gateway_value = validate_optional_ipv4(self.gateway_edit.text(), "게이트웨이")
            dns_servers = (
                parse_dns_servers(self.dns_edit.toPlainText()) if self.dns_edit.toPlainText().strip() else []
            )
        except ValidationError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return

        if not self._confirm_apply(
            title="수동 IP 적용 확인",
            interface_name=adapter.name,
            current_lines=self._adapter_summary_lines(adapter),
            target_lines=self._static_target_summary_lines(ip_value, prefix_value, gateway_value, dns_servers),
        ):
            return

        self._start_worker(
            self.state.network_interface_service.set_static,
            adapter.name,
            ip_value,
            prefix_value,
            gateway_value,
            dns_servers,
            on_result=lambda result: self._handle_operation_result(result, refresh_after=True),
            error_title="수동 IP 적용 실패",
        )

    def save_current_as_profile(self) -> None:
        try:
            seed = self._build_profile_seed_from_form()
        except ValidationError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return

        dialog = ProfileEditorDialog(self, seed)
        if dialog.exec():
            profile = dialog.profile_data()
            self._save_profile(profile)
            self.status_message.emit(f"프로필을 저장했습니다: {profile.name}")

    def add_profile(self) -> None:
        try:
            seed = self._build_profile_seed_from_form()
        except ValidationError:
            seed = IPProfile(name="", mode="static", interface_name=self.selected_interface_label.text().strip())

        dialog = ProfileEditorDialog(self, seed)
        if dialog.exec():
            profile = dialog.profile_data()
            self._save_profile(profile)
            self.status_message.emit(f"프로필을 추가했습니다: {profile.name}")

    def edit_selected_profile(self) -> None:
        profile = self._selected_profile()
        if not profile:
            QMessageBox.warning(self, "선택 필요", "먼저 IP 프로필을 선택해 주세요.")
            return

        dialog = ProfileEditorDialog(self, profile)
        if dialog.exec():
            updated = dialog.profile_data()
            profiles = list(self.state.ip_profiles)
            profiles[self.profile_list.currentRow()] = updated
            self.state.save_ip_profiles(profiles)
            self._select_profile_by_name(updated.name)
            self.status_message.emit(f"프로필을 수정했습니다: {updated.name}")

    def apply_selected_profile(self) -> None:
        profile = self._selected_profile()
        adapter = self._selected_adapter()
        if not profile:
            QMessageBox.warning(self, "선택 필요", "먼저 IP 프로필을 선택해 주세요.")
            return
        if not self._ensure_admin():
            return

        interface_name = adapter.name if adapter else profile.interface_name
        if not interface_name:
            QMessageBox.warning(self, "선택 필요", "프로필을 적용할 인터페이스를 선택해 주세요.")
            return

        current_lines = self._adapter_summary_lines(adapter) if adapter else ["현재 정보: 인터페이스 선택 필요"]
        target_lines = (
            ["모드: 자동 (DHCP)", "IPv4: DHCP 할당", "DNS: 자동"]
            if profile.mode == "dhcp"
            else self._static_target_summary_lines(profile.local_ip, profile.prefix, profile.gateway, profile.dns)
        )
        if not self._confirm_apply("저장된 프로필 적용 확인", interface_name, current_lines, target_lines):
            return

        self._start_worker(
            self.state.network_interface_service.apply_profile,
            interface_name,
            profile,
            on_result=lambda result: self._handle_operation_result(result, refresh_after=True),
            error_title="프로필 적용 실패",
        )

    def delete_selected_profile(self) -> None:
        profile = self._selected_profile()
        if not profile:
            QMessageBox.warning(self, "선택 필요", "먼저 IP 프로필을 선택해 주세요.")
            return
        if QMessageBox.question(self, "프로필 삭제", f"'{profile.name}' 프로필을 삭제할까요?") != QMessageBox.Yes:
            return

        profiles = list(self.state.ip_profiles)
        profiles.pop(self.profile_list.currentRow())
        self.state.save_ip_profiles(profiles)
        self.status_message.emit(f"프로필을 삭제했습니다: {profile.name}")

    def _build_profile_seed_from_form(self) -> IPProfile:
        adapter = self._selected_adapter()
        mode = str(self.mode_combo.currentData())
        if mode == "static":
            local_ip = validate_ipv4(self.ip_edit.text(), "로컬 IPv4")
            prefix = validate_prefix(self.prefix_edit.text())
            gateway = validate_optional_ipv4(self.gateway_edit.text(), "게이트웨이")
            dns_servers = (
                parse_dns_servers(self.dns_edit.toPlainText()) if self.dns_edit.toPlainText().strip() else []
            )
        else:
            local_ip = ""
            prefix = 24
            gateway = ""
            dns_servers = []

        default_name = f"{adapter.name} 프로필" if adapter else ""
        return IPProfile(
            name=default_name,
            mode=mode,
            interface_name=adapter.name if adapter else self.selected_interface_label.text().strip(),
            local_ip=local_ip,
            prefix=prefix,
            gateway=gateway,
            dns=dns_servers,
        )

    def _save_profile(self, profile: IPProfile) -> None:
        profiles = list(self.state.ip_profiles)
        existing_index = next((index for index, item in enumerate(profiles) if item.name == profile.name), None)
        if existing_index is not None:
            profiles[existing_index] = profile
        else:
            profiles.append(profile)
        self.state.save_ip_profiles(profiles)
        self._select_profile_by_name(profile.name)

    def _adapter_summary_lines(self, adapter: NetworkAdapterInfo | None) -> list[str]:
        if not adapter:
            return ["현재 정보: 없음"]
        return [
            f"모드: {'자동 (DHCP)' if adapter.dhcp_enabled else '수동 IP'}",
            f"IPv4: {adapter.ipv4 or '-'}",
            f"Prefix / 마스크: {format_prefix(adapter.prefix_length) if adapter.prefix_length else '-'}",
            f"게이트웨이: {adapter.gateway or '-'}",
            f"DNS: {adapter.dns_text() or '-'}",
        ]

    def _static_target_summary_lines(
        self,
        ip_value: str,
        prefix_value: int,
        gateway_value: str,
        dns_servers: list[str],
    ) -> list[str]:
        return [
            "모드: 수동 IP",
            f"IPv4: {ip_value}",
            f"Prefix / 마스크: {format_prefix(prefix_value)}",
            f"게이트웨이: {gateway_value or '-'}",
            f"DNS: {', '.join(dns_servers) if dns_servers else '-'}",
        ]

    def _confirm_apply(
        self,
        title: str,
        interface_name: str,
        current_lines: list[str],
        target_lines: list[str],
    ) -> bool:
        message = "\n".join(
            [
                f"대상 인터페이스: {interface_name}",
                "",
                "[현재 설정]",
                *current_lines,
                "",
                "[적용 후 설정]",
                *target_lines,
                "",
                "이대로 적용할까요?",
            ]
        )
        return QMessageBox.question(self, title, message) == QMessageBox.Yes

    def _handle_operation_result(self, result, refresh_after: bool = False) -> None:
        if result.success:
            self.status_message.emit(result.message)
            if refresh_after:
                self.refresh_adapters()
            return

        QMessageBox.warning(self, "작업 실패", result.message + ("\n\n" + result.details if result.details else ""))

    def _ensure_admin(self) -> bool:
        if self.state.is_admin:
            return True
        QMessageBox.warning(self, "관리자 권한 필요", "이 작업은 관리자 권한이 필요합니다.")
        return False

    def _select_profile_by_name(self, profile_name: str) -> None:
        for index, profile in enumerate(self.state.ip_profiles):
            if profile.name == profile_name:
                self.profile_list.setCurrentRow(index)
                return

    def save_ui_state(self) -> dict:
        adapter = self._selected_adapter()
        profile = self._selected_profile()
        return {
            "selected_adapter": adapter.name if adapter else "",
            "selected_profile": profile.name if profile else "",
            "mode": str(self.mode_combo.currentData() or ""),
            "ip": self.ip_edit.text().strip(),
            "prefix": self.prefix_edit.text().strip(),
            "gateway": self.gateway_edit.text().strip(),
            "dns": self.dns_edit.toPlainText().strip(),
        }

    def restore_ui_state(self, ui_state: dict | None) -> None:
        state = dict(ui_state or {})
        if not state:
            return
        self._pending_ui_state = state
        self._apply_saved_state(state)

    def _apply_saved_state(self, state: dict) -> None:
        adapter_name = str(state.get("selected_adapter", "") or "").strip()
        if adapter_name:
            for row, adapter in enumerate(self.adapters):
                if adapter.name == adapter_name:
                    self.adapter_table.selectRow(row)
                    break

        profile_name = str(state.get("selected_profile", "") or "").strip()
        if profile_name:
            self._select_profile_by_name(profile_name)

        mode = str(state.get("mode", "") or "")
        if mode:
            mode_index = self.mode_combo.findData(mode)
            if mode_index >= 0:
                self.mode_combo.setCurrentIndex(mode_index)

        self.ip_edit.setText(str(state.get("ip", "") or ""))
        self.prefix_edit.setText(str(state.get("prefix", "") or ""))
        self.gateway_edit.setText(str(state.get("gateway", "") or ""))
        self.dns_edit.setPlainText(str(state.get("dns", "") or ""))
        self._update_mode_state()

    def _start_worker(
        self,
        fn: Callable,
        *args,
        on_started: Callable | None = None,
        on_result: Callable | None = None,
        on_progress: Callable | None = None,
        on_finished: Callable | None = None,
        error_title: str = "작업 실패",
        **kwargs,
    ) -> None:
        worker = FunctionWorker(fn, *args, **kwargs)
        self._active_workers.append(worker)
        if on_started:
            worker.signals.started.connect(on_started)
        if on_result:
            worker.signals.result.connect(on_result)
        if on_progress:
            worker.signals.progress.connect(on_progress)
        if on_finished:
            worker.signals.finished.connect(on_finished)
        worker.signals.error.connect(lambda text: QMessageBox.warning(self, error_title, text))
        worker.signals.finished.connect(lambda worker=worker: self._discard_worker(worker))
        self.state.thread_pool.start(worker)

    def _discard_worker(self, worker: FunctionWorker) -> None:
        if worker in self._active_workers:
            self._active_workers.remove(worker)
