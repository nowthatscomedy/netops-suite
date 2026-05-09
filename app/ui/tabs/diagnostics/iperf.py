from __future__ import annotations

from collections import Counter
from datetime import datetime
from threading import Event

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models.network_models import PublicIperfServer
from app.models.result_models import OperationResult
from app.utils.validators import ValidationError


class IperfDiagnosticsMixin:
    def _build_iperf_tab(self) -> QWidget:
        self._iperf_listen_addresses: list[str] = []
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        layout = QVBoxLayout(page)

        group = QGroupBox("iperf3")
        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(8, 8, 8, 8)
        group_layout.setSpacing(4)

        self.iperf_mode_combo = QComboBox()
        self.iperf_mode_combo.addItem("클라이언트", "client")
        self.iperf_mode_combo.addItem("서버", "server")
        self.iperf_use_public_server_check = QCheckBox("공개 서버 사용")
        self.iperf_public_refresh_button = QPushButton("목록 갱신")
        self.iperf_public_region_combo = QComboBox()
        self.iperf_public_region_combo.addItem("전체 지역", "")
        self.iperf_public_region_combo.setMinimumWidth(130)
        self.iperf_public_server_combo = QComboBox()
        self.iperf_public_server_combo.addItem("공개 서버 목록 확인 중...", "")
        self.iperf_public_info_label = QLabel("목록 상태 확인 중")
        self.iperf_public_info_label.setStyleSheet("color:#666;")
        self.iperf_server_edit = QLineEdit()
        self.iperf_server_edit.setPlaceholderText("예: 192.168.0.10")
        self.iperf_port_edit = QLineEdit()
        self.iperf_port_edit.setPlaceholderText("5201")
        self.iperf_port_edit.setMaximumWidth(90)
        self.iperf_streams_edit = QLineEdit()
        self.iperf_streams_edit.setPlaceholderText("1")
        self.iperf_streams_edit.setMaximumWidth(90)
        self.iperf_duration_edit = QLineEdit()
        self.iperf_duration_edit.setPlaceholderText("10")
        self.iperf_duration_edit.setMaximumWidth(90)
        self.iperf_reverse_check = QCheckBox("Reverse (-R)")
        self.iperf_udp_check = QCheckBox("UDP (-u)")
        self.iperf_ipv6_check = QCheckBox("IPv6 (-6)")

        self.iperf_run_button = QPushButton("실행")
        self.iperf_cancel_button = QPushButton("중지")
        self.iperf_cancel_button.setEnabled(False)

        self.iperf_status_label = QLabel()
        self.iperf_status_label.setWordWrap(False)
        self.iperf_status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.iperf_server_listen_label = QLabel()
        self.iperf_server_listen_label.setWordWrap(True)
        self.iperf_server_listen_label.setStyleSheet("color:#1565c0;")
        self.iperf_server_listen_label.hide()

        self.iperf_refresh_button = QPushButton("상태 새로고침")
        self.iperf_manage_button = QPushButton("winget 설치")

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("모드"))
        mode_row.addWidget(self.iperf_mode_combo)
        mode_row.addSpacing(10)
        mode_row.addWidget(self.iperf_use_public_server_check)
        mode_row.addWidget(self.iperf_public_refresh_button)
        mode_row.addStretch(1)
        group_layout.addLayout(mode_row)

        public_row = QHBoxLayout()
        public_row.addWidget(QLabel("지역"))
        public_row.addWidget(self.iperf_public_region_combo)
        public_row.addSpacing(6)
        public_row.addWidget(QLabel("공개 서버"))
        public_row.addWidget(self.iperf_public_server_combo, 1)
        self.iperf_public_row_widget = QWidget()
        self.iperf_public_row_widget.setLayout(public_row)
        group_layout.addWidget(self.iperf_public_row_widget)

        public_info_row = QHBoxLayout()
        public_info_row.addWidget(self.iperf_public_info_label, 1)
        self.iperf_public_info_row_widget = QWidget()
        self.iperf_public_info_row_widget.setLayout(public_info_row)
        group_layout.addWidget(self.iperf_public_info_row_widget)

        params_row = QHBoxLayout()
        params_row.addWidget(QLabel("서버"))
        params_row.addWidget(self.iperf_server_edit, 1)
        params_row.addSpacing(6)
        params_row.addWidget(QLabel("포트"))
        params_row.addWidget(self.iperf_port_edit)
        params_row.addSpacing(6)
        params_row.addWidget(QLabel("스트림"))
        params_row.addWidget(self.iperf_streams_edit)
        params_row.addSpacing(6)
        params_row.addWidget(QLabel("지속 초"))
        params_row.addWidget(self.iperf_duration_edit)
        params_row.addSpacing(6)
        params_row.addWidget(self.iperf_reverse_check)
        params_row.addWidget(self.iperf_udp_check)
        params_row.addWidget(self.iperf_ipv6_check)
        group_layout.addLayout(params_row)

        action_row = QHBoxLayout()
        action_row.addWidget(self.iperf_run_button)
        action_row.addWidget(self.iperf_cancel_button)
        action_row.addSpacing(8)
        action_row.addWidget(self.iperf_refresh_button)
        action_row.addWidget(self.iperf_manage_button)
        action_row.addSpacing(8)
        action_row.addWidget(self.iperf_status_label, 1)
        action_row.addStretch(1)
        group_layout.addLayout(action_row)
        group_layout.addWidget(self.iperf_server_listen_label)

        layout.addWidget(group, 0)

        self.iperf_output = self._output()
        self.iperf_output.setMinimumHeight(0)
        self.iperf_output.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        layout.addWidget(self.iperf_output, 1)

        self.iperf_mode_combo.currentIndexChanged.connect(self._update_iperf_mode_state)
        self.iperf_use_public_server_check.toggled.connect(self._toggle_public_iperf_mode)
        self.iperf_public_region_combo.currentIndexChanged.connect(self._handle_public_iperf_region_changed)
        self.iperf_public_server_combo.currentIndexChanged.connect(self._handle_public_iperf_selection_changed)
        self.iperf_public_refresh_button.clicked.connect(lambda: self.refresh_public_iperf_servers(force_refresh=True))
        self.iperf_port_edit.textChanged.connect(lambda _text: self._update_iperf_mode_state())
        self.iperf_ipv6_check.toggled.connect(lambda _checked: self._update_iperf_mode_state())
        self.iperf_run_button.clicked.connect(self.run_iperf_test)
        self.iperf_cancel_button.clicked.connect(self.cancel_iperf_test)
        self.iperf_refresh_button.clicked.connect(lambda: self.refresh_iperf_availability(deep_check=True))
        self.iperf_manage_button.clicked.connect(self.manage_iperf_install)
        self._reset_public_iperf_server_list()
        self._update_iperf_mode_state()
        return page

    def _reset_public_iperf_server_list(self) -> None:
        self.public_iperf_all_servers = []
        self.public_iperf_servers = []
        self._public_iperf_fetched_at = ""
        self._public_iperf_from_cache = False
        self._public_iperf_stale = True
        self.iperf_public_region_combo.blockSignals(True)
        self.iperf_public_region_combo.clear()
        self.iperf_public_region_combo.addItem("전체 지역", "")
        self.iperf_public_region_combo.blockSignals(False)
        self.iperf_public_server_combo.blockSignals(True)
        self.iperf_public_server_combo.clear()
        self.iperf_public_server_combo.addItem("목록 갱신을 눌러 공개 서버를 불러오세요.", "")
        self.iperf_public_server_combo.blockSignals(False)
        self.iperf_public_server_combo.setToolTip("")
        self._set_public_iperf_info("목록 갱신을 눌러 공개 서버 목록을 불러오세요.")

    def refresh_public_iperf_servers(self, force_refresh: bool = False) -> None:
        if self._public_iperf_refresh_in_progress:
            return
        self._public_iperf_refresh_in_progress = True
        self._set_public_iperf_info("공개 iperf 서버 목록을 갱신하는 중입니다...")
        self._update_iperf_mode_state()
        self._start_worker(
            self.state.public_iperf_service.fetch_public_servers,
            force_refresh=force_refresh,
            on_result=self._finish_public_iperf_refresh,
            on_finished=self._finish_public_iperf_refresh_state,
            error_title="공개 iperf 서버 목록 갱신 실패",
        )

    def _finish_public_iperf_refresh(self, result: OperationResult) -> None:
        payload = result.payload if isinstance(result.payload, dict) else {}
        servers = payload.get("servers", [])
        if isinstance(servers, list) and servers:
            self._apply_public_iperf_servers(
                servers,
                fetched_at=str(payload.get("fetched_at", "") or ""),
                from_cache=bool(payload.get("from_cache", False)),
                stale=bool(payload.get("stale", False)),
            )
        else:
            self._set_public_iperf_info(result.message)
            if not result.success:
                self.iperf_output.setPlainText(result.details or result.message)

    def _finish_public_iperf_refresh_state(self) -> None:
        self._public_iperf_refresh_in_progress = False
        self._update_iperf_mode_state()

    def _region_label(self, region: str) -> str:
        normalized = (region or "").strip()
        mapping = {
            "asia": "아시아",
            "europe": "유럽",
            "north america": "북미",
            "south america": "남미",
            "oceania": "오세아니아",
            "africa": "아프리카",
            "middle east": "중동",
        }
        return mapping.get(normalized.lower(), normalized or "기타")

    def _server_sort_key(self, server: PublicIperfServer) -> tuple[str, str, str]:
        region = (server.region or "ZZZ").lower()
        site = (server.site or server.name or server.host).lower()
        return (region, site, server.host.lower())

    def _public_server_item_text(self, server: PublicIperfServer, include_region: bool) -> str:
        location = server.site or server.name or server.host
        country = f" ({server.country_code})" if server.country_code and server.country_code not in location else ""
        parts: list[str] = []
        if include_region and server.region:
            parts.append(f"[{self._region_label(server.region)}]")
        parts.append(f"{location}{country}")
        parts.append(f"{server.host}:{server.port_spec}")
        if server.speed:
            parts.append(f"{server.speed} Gb/s")
        if server.options:
            parts.append(server.options)
        return " | ".join(parts)

    def _refresh_public_region_combo(self) -> None:
        previous_region = self._preferred_public_iperf_region or str(self.iperf_public_region_combo.currentData() or "")
        counts = Counter((server.region or "").strip() for server in self.public_iperf_all_servers if (server.region or "").strip())
        self.iperf_public_region_combo.blockSignals(True)
        self.iperf_public_region_combo.clear()
        total_count = len(self.public_iperf_all_servers)
        self.iperf_public_region_combo.addItem(f"전체 지역 ({total_count})", "")
        for region in sorted(counts, key=lambda item: self._region_label(item).lower()):
            self.iperf_public_region_combo.addItem(f"{self._region_label(region)} ({counts[region]})", region)
        index = self.iperf_public_region_combo.findData(previous_region)
        self.iperf_public_region_combo.setCurrentIndex(index if index >= 0 else 0)
        self.iperf_public_region_combo.blockSignals(False)
        self._preferred_public_iperf_region = str(self.iperf_public_region_combo.currentData() or "")

    def _ensure_public_iperf_state_placeholders(self, region: str, server_key: str) -> None:
        if region and self.iperf_public_region_combo.findData(region) < 0:
            self.iperf_public_region_combo.addItem(self._region_label(region), region)
        if server_key and self.iperf_public_server_combo.findData(server_key) < 0:
            host, separator, port = server_key.partition("|")
            label = f"{host}:{port}" if separator else server_key
            self.iperf_public_server_combo.addItem(label, server_key)

    def _rebuild_public_iperf_server_combo(self, previous_key: str = "") -> None:
        selected_region = str(self.iperf_public_region_combo.currentData() or "")
        self._preferred_public_iperf_region = selected_region
        if selected_region:
            self.public_iperf_servers = [
                server for server in self.public_iperf_all_servers if (server.region or "").strip() == selected_region
            ]
        else:
            self.public_iperf_servers = list(self.public_iperf_all_servers)

        include_region = not bool(selected_region)
        self.iperf_public_server_combo.blockSignals(True)
        self.iperf_public_server_combo.clear()
        for server in self.public_iperf_servers:
            self.iperf_public_server_combo.addItem(
                self._public_server_item_text(server, include_region=include_region),
                server.key,
            )
        self.iperf_public_server_combo.blockSignals(False)

        if previous_key:
            index = self.iperf_public_server_combo.findData(previous_key)
            if index >= 0:
                self.iperf_public_server_combo.setCurrentIndex(index)
                self._preferred_public_iperf_key = previous_key

        if self.iperf_public_server_combo.currentIndex() < 0 and self.iperf_public_server_combo.count() > 0:
            self.iperf_public_server_combo.setCurrentIndex(0)

    def _apply_public_iperf_servers(
        self,
        servers: list[PublicIperfServer],
        fetched_at: str = "",
        from_cache: bool = False,
        stale: bool = False,
    ) -> None:
        previous_key = self._preferred_public_iperf_key or str(self.iperf_public_server_combo.currentData() or "")
        self._public_iperf_fetched_at = fetched_at
        self._public_iperf_from_cache = from_cache
        self._public_iperf_stale = stale
        self.public_iperf_all_servers = sorted(list(servers), key=self._server_sort_key)
        self._refresh_public_region_combo()
        self._rebuild_public_iperf_server_combo(previous_key)
        selected = self._selected_public_iperf_server()
        if selected:
            self._preferred_public_iperf_key = selected.key
            self.iperf_public_server_combo.setToolTip(selected.summary_text or selected.display_name)
        else:
            self.iperf_public_server_combo.setToolTip("")

        self._refresh_public_iperf_info_message()
        self._sync_public_iperf_target(overwrite_port=False)
        self._update_iperf_mode_state()

    def _selected_public_iperf_server(self) -> PublicIperfServer | None:
        key = str(self.iperf_public_server_combo.currentData() or "")
        if not key:
            return None
        for server in self.public_iperf_servers:
            if server.key == key:
                return server
        return None

    def _current_public_iperf_state_key(self) -> str:
        selected = self._selected_public_iperf_server()
        if selected is not None:
            return selected.key
        server = self.iperf_server_edit.text().strip()
        port = self.iperf_port_edit.text().strip() or "5201"
        if not server:
            return ""
        return f"{server}|{port}"

    def _handle_public_iperf_selection_changed(self) -> None:
        selected = self._selected_public_iperf_server()
        if selected:
            self._preferred_public_iperf_key = selected.key
            self.iperf_public_server_combo.setToolTip(selected.summary_text or selected.display_name)
        else:
            self.iperf_public_server_combo.setToolTip("")
        self._sync_public_iperf_target(overwrite_port=True)
        self._update_iperf_mode_state()

    def _handle_public_iperf_region_changed(self) -> None:
        previous_key = self._preferred_public_iperf_key
        self._rebuild_public_iperf_server_combo(previous_key)
        selected = self._selected_public_iperf_server()
        if selected:
            self._preferred_public_iperf_key = selected.key
            self.iperf_public_server_combo.setToolTip(selected.summary_text or selected.display_name)
        else:
            self.iperf_public_server_combo.setToolTip("")
        self._refresh_public_iperf_info_message()
        self._sync_public_iperf_target(overwrite_port=False)
        self._update_iperf_mode_state()

    def _toggle_public_iperf_mode(self, checked: bool) -> None:
        if checked:
            self._sync_public_iperf_target(overwrite_port=False)
        self._update_iperf_mode_state()

    def _local_iperf_server_addresses(self) -> list[str]:
        try:
            adapters = self.state.network_interface_service.list_adapters()
        except Exception:
            return []

        addresses: list[str] = []
        for adapter in adapters:
            ip = (adapter.ipv4 or "").strip()
            if not ip or ip.startswith("127.") or ip.startswith("169.254."):
                continue
            if ip not in addresses:
                addresses.append(ip)
        return addresses

    def _format_iperf_server_listen_text(self, port: int, ipv6: bool, include_local_addresses: bool = False) -> str:
        wildcard = f"[::]:{port}" if ipv6 else f"0.0.0.0:{port}"
        addresses = self._iperf_listen_addresses if include_local_addresses else []
        if addresses:
            return f"서버 대기 주소 {wildcard} | 접속 가능한 로컬 IPv4: {', '.join(addresses)}"
        if ipv6:
            return f"서버 대기 주소 {wildcard} | 모든 IPv6 인터페이스에서 수신"
        return f"서버 대기 주소 {wildcard} | 모든 IPv4 인터페이스에서 수신"

    def _sync_public_iperf_target(self, overwrite_port: bool) -> None:
        if self.iperf_mode_combo.currentData() != "client":
            return
        if not self.iperf_use_public_server_check.isChecked():
            return
        selected = self._selected_public_iperf_server()
        if not selected:
            return
        self.iperf_server_edit.setText(selected.host)
        current_port = self.iperf_port_edit.text().strip()
        if overwrite_port or not current_port:
            self.iperf_port_edit.setText(str(selected.default_port))

    def _set_public_iperf_info(self, text: str) -> None:
        compact = " ".join(part.strip() for part in text.splitlines() if part.strip())
        self.iperf_public_info_label.setText(compact or "-")
        self.iperf_public_info_label.setToolTip(text)

    def _refresh_public_iperf_info_message(self) -> None:
        if not self.public_iperf_all_servers:
            return
        source_text = "캐시" if self._public_iperf_from_cache else "온라인"
        fetched_text = self._format_timestamp_text(self._public_iperf_fetched_at)
        total_count = len(self.public_iperf_all_servers)
        filtered_count = len(self.public_iperf_servers)
        region_text = ""
        if self._preferred_public_iperf_region:
            region_name = self._region_label(self._preferred_public_iperf_region)
            region_text = f" | {region_name} {filtered_count}/{total_count}개"
        message = f"{source_text} {total_count}개{region_text}"
        if fetched_text:
            message += f" | {fetched_text}"
        if self._public_iperf_stale:
            message += " | 오래된 캐시"
        self._set_public_iperf_info(message)

    def _format_timestamp_text(self, value: str) -> str:
        if not value:
            return ""
        normalized = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return value
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")

    def _set_iperf_option_enabled(
        self,
        checkbox: QCheckBox,
        enabled: bool,
        unsupported_message: str = "",
    ) -> None:
        checkbox.setEnabled(enabled)
        checkbox.setToolTip("" if enabled else unsupported_message)
        if not enabled and checkbox.isChecked():
            checkbox.setChecked(False)

    def _update_iperf_option_state(self, is_client: bool, use_public_requested: bool) -> None:
        if not is_client:
            self._set_iperf_option_enabled(self.iperf_reverse_check, False)
            self._set_iperf_option_enabled(self.iperf_udp_check, False)
            self._set_iperf_option_enabled(self.iperf_ipv6_check, True)
            return

        if not use_public_requested:
            self._set_iperf_option_enabled(self.iperf_reverse_check, True)
            self._set_iperf_option_enabled(self.iperf_udp_check, True)
            self._set_iperf_option_enabled(self.iperf_ipv6_check, True)
            return

        selected = self._selected_public_iperf_server()
        if selected is None:
            unavailable_text = "공개 서버를 먼저 선택해 주세요."
            self._set_iperf_option_enabled(self.iperf_reverse_check, False, unavailable_text)
            self._set_iperf_option_enabled(self.iperf_udp_check, False, unavailable_text)
            self._set_iperf_option_enabled(self.iperf_ipv6_check, False, unavailable_text)
            return

        self._set_iperf_option_enabled(
            self.iperf_reverse_check,
            selected.supports_option("-R"),
            "선택한 공개 서버는 Reverse(-R)를 지원하지 않습니다.",
        )
        self._set_iperf_option_enabled(
            self.iperf_udp_check,
            selected.supports_option("-u"),
            "선택한 공개 서버는 UDP(-u)를 지원하지 않습니다.",
        )
        self._set_iperf_option_enabled(
            self.iperf_ipv6_check,
            selected.supports_option("-6"),
            "선택한 공개 서버는 IPv6(-6)를 지원하지 않습니다.",
        )

    def _update_iperf_mode_state(self) -> None:
        is_client = self.iperf_mode_combo.currentData() == "client"
        use_public = is_client and self.iperf_use_public_server_check.isChecked() and bool(self.public_iperf_servers)
        show_public_section = is_client and self.iperf_use_public_server_check.isChecked()
        self.iperf_server_listen_label.setVisible(not is_client)
        if is_client:
            self.iperf_server_listen_label.clear()
        else:
            port_text = self.iperf_port_edit.text().strip() or "5201"
            try:
                port_value = int(port_text)
            except ValueError:
                port_value = 5201
            self.iperf_server_listen_label.setText(
                self._format_iperf_server_listen_text(port_value, self.iperf_ipv6_check.isChecked())
            )
        self.iperf_use_public_server_check.setEnabled(is_client)
        self.iperf_public_region_combo.setEnabled(is_client and show_public_section and bool(self.public_iperf_all_servers))
        self.iperf_public_server_combo.setEnabled(is_client and use_public and bool(self.public_iperf_servers))
        self.iperf_public_refresh_button.setEnabled(is_client and not self._public_iperf_refresh_in_progress)
        self.iperf_public_row_widget.setVisible(show_public_section)
        self.iperf_public_info_row_widget.setVisible(show_public_section)
        self.iperf_server_edit.setEnabled(is_client and not use_public)
        self.iperf_streams_edit.setEnabled(is_client)
        self.iperf_duration_edit.setEnabled(is_client)
        self._update_iperf_option_state(is_client, show_public_section)
        if is_client and not use_public:
            self.iperf_server_edit.setPlaceholderText("예: 192.168.0.10")
        elif is_client:
            self.iperf_server_edit.setPlaceholderText("공개 서버 선택 값이 자동으로 채워집니다.")
        else:
            self.iperf_server_edit.setPlaceholderText("서버 모드에서는 사용하지 않습니다.")

    def refresh_iperf_availability(self, deep_check: bool = True) -> None:
        executable_path, source = self.state.iperf_service.executable_details()
        self._iperf_available = executable_path is not None
        if not deep_check:
            self._iperf_manage_available = self.state.iperf_service.managed_install_supported()
            self._iperf_manage_enabled = self._iperf_manage_available and not self._iperf_available
            self.iperf_manage_button.setText("winget 설치" if self._iperf_manage_available else "winget 없음")
            if self._iperf_available:
                self.iperf_status_label.clear()
                self.iperf_status_label.setToolTip(executable_path or "")
                self.iperf_status_label.hide()
            else:
                parts = ["iperf3 없음"]
                if self._iperf_manage_available:
                    parts.append("winget 설치 가능")
                    tooltip = (
                        "현재 iperf3를 찾지 못했습니다.\n"
                        "1) 'winget 설치' 버튼으로 현재 사용자 범위에 설치\n"
                        "2) 시스템 PATH에서 iperf3를 찾을 수 있게 설치 후 '상태 새로고침' 실행"
                    )
                else:
                    parts.append("수동 설치 필요")
                    tooltip = (
                        "현재 iperf3를 찾지 못했습니다.\n"
                        "시스템 PATH에서 iperf3를 찾을 수 있게 설치한 뒤 '상태 새로고침'을 눌러 주세요."
                    )
                self.iperf_status_label.setText(" | ".join(parts))
                self.iperf_status_label.setToolTip(tooltip)
                self.iperf_status_label.setStyleSheet("color:#a33;")
                self.iperf_status_label.show()
            self._set_iperf_running(self.iperf_cancel_button.isEnabled())
            return
        manage_state = self.state.iperf_service.managed_install_state()
        self._iperf_manage_available = bool(manage_state["available"])
        self._iperf_manage_enabled = bool(manage_state["button_enabled"])
        self.iperf_manage_button.setText(str(manage_state["action_label"]))

        if self._iperf_available:
            version = self.state.iperf_service.executable_version(executable_path)
            if self._iperf_manage_available and self._iperf_manage_enabled:
                text = "업데이트 가능"
                if version:
                    text += f" (현재 {version})"
                self.iperf_status_label.setText(text)
                self.iperf_status_label.setToolTip(executable_path or "")
                self.iperf_status_label.setStyleSheet("color:#8d6e00;")
                self.iperf_status_label.show()
            else:
                self.iperf_status_label.clear()
                self.iperf_status_label.setToolTip(executable_path or "")
                self.iperf_status_label.hide()
        else:
            parts = ["iperf3 없음"]
            if self._iperf_manage_available:
                parts.append("winget 설치 가능")
                tooltip = (
                    "현재 iperf3를 찾지 못했습니다.\n"
                    f"1) '{manage_state['action_label']}' 버튼으로 현재 사용자에 설치/업데이트\n"
                    "2) 시스템 PATH에 iperf3를 설치하고 '상태 새로고침' 실행"
                )
            else:
                parts.append("수동 설치 필요")
                tooltip = (
                    "현재 iperf3를 찾지 못했습니다.\n"
                    "시스템 PATH에 iperf3를 설치한 뒤 '상태 새로고침'을 실행해 주세요."
                )
            self.iperf_status_label.setText(" | ".join(parts))
            self.iperf_status_label.setToolTip(tooltip)
            self.iperf_status_label.setStyleSheet("color:#a33;")
            self.iperf_status_label.show()

        self._set_iperf_running(self.iperf_cancel_button.isEnabled())

    def manage_iperf_install(self) -> None:
        manage_state = self.state.iperf_service.managed_install_state()
        if not bool(manage_state["available"]):
            QMessageBox.warning(
                self,
                "winget 사용 불가",
                "이 시스템에서는 winget을 찾지 못해 프로그램 내에서 설치를 진행할 수 없습니다.",
            )
            return
        if not bool(manage_state["button_enabled"]):
            QMessageBox.information(
                self,
                "최신 버전 사용 중",
                "현재 winget 기준 최신 iperf3가 이미 설치되어 있습니다.",
            )
            return

        action_label = "업데이트" if bool(manage_state["installed"]) else "설치"
        reply = QMessageBox.question(
            self,
            "iperf3 관리형 설치",
            (
                f"iperf3를 winget 패키지로 {action_label}하시겠습니까?\n\n"
                f"패키지 ID: {manage_state['package_id']}\n"
                f"패키지 페이지: {manage_state['package_url']}\n\n"
                "현재 사용자 범위로 설치되며, 실행 파일이 준비되면 바로 앱에서 사용할 수 있습니다."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        self.iperf_output.clear()
        self.iperf_manage_cancel_event = Event()
        self._set_iperf_running(True)

        self._start_worker(
            self.state.iperf_service.install_or_update_managed,
            cancel_event=self.iperf_manage_cancel_event,
            on_progress=self.iperf_output.appendPlainText,
            on_result=self._finish_iperf_manage,
            on_finished=self._finish_iperf_operation,
            error_title="iperf3 설치 실패",
        )

    def run_iperf_test(self) -> None:
        self.refresh_iperf_availability(deep_check=False)
        if not self._iperf_available:
            if self._iperf_manage_available:
                reply = QMessageBox.question(
                    self,
                    "iperf3 설치 필요",
                    "iperf3 실행 파일을 찾지 못했습니다.\n\n지금 winget으로 설치/업데이트하시겠습니까?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if reply == QMessageBox.Yes:
                    self.manage_iperf_install()
                return

            QMessageBox.information(
                self,
                "iperf3 설치 필요",
                "iperf3 실행 파일을 찾지 못했습니다.\n\n프로그램 폴더에 iperf3.exe를 두거나 시스템 PATH에 iperf3를 설치해 주세요.",
            )
            return

        mode = str(self.iperf_mode_combo.currentData())
        if mode == "client":
            self._sync_public_iperf_target(overwrite_port=False)
        try:
            port = self._positive_int_or_default(self.iperf_port_edit, "iperf 포트", 5201, minimum=1, maximum=65535)
            streams = self._positive_int_or_default(self.iperf_streams_edit, "스트림 수", 1) if mode == "client" else 1
            duration = self._positive_int_or_default(self.iperf_duration_edit, "지속 시간", 10) if mode == "client" else 0
        except ValidationError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return

        server = self.iperf_server_edit.text().strip()
        if mode == "client" and not server:
            if self.iperf_use_public_server_check.isChecked():
                QMessageBox.warning(self, "입력 확인", "공개 서버 목록을 먼저 불러오거나 직접 서버 주소를 입력해 주세요.")
            else:
                QMessageBox.warning(self, "입력 확인", "클라이언트 모드에서는 서버 주소를 입력해 주세요.")
            return

        self.iperf_output.clear()
        self.iperf_cancel_event = Event()
        self._set_iperf_running(True)
        if mode == "server":
            self._iperf_listen_addresses = self._local_iperf_server_addresses()
            listen_text = self._format_iperf_server_listen_text(
                port,
                self.iperf_ipv6_check.isChecked(),
                include_local_addresses=True,
            )
            self.iperf_server_listen_label.setText(listen_text)
            self.iperf_server_listen_label.show()
            self.iperf_output.appendPlainText(f"[서버] {listen_text}")

        self._start_worker(
            self.state.iperf_service.run_test,
            mode,
            server,
            port,
            streams,
            duration,
            self.iperf_reverse_check.isChecked(),
            self.iperf_udp_check.isChecked(),
            self.iperf_ipv6_check.isChecked(),
            cancel_event=self.iperf_cancel_event,
            on_progress=self.iperf_output.appendPlainText,
            on_result=self._finish_iperf,
            on_finished=self._finish_iperf_operation,
            error_title="iperf3 실행 실패",
        )

    def _finish_iperf(self, result: OperationResult) -> None:
        if self.iperf_output.toPlainText().strip():
            self.iperf_output.appendPlainText("")
            self.iperf_output.appendPlainText(f"[결과] {result.message}")
            if result.details and not result.success:
                self.iperf_output.appendPlainText(result.details)
        else:
            self.iperf_output.setPlainText(result.message + ("\n\n" + result.details if result.details else ""))

    def _finish_iperf_manage(self, result: OperationResult) -> None:
        streamed = self.iperf_output.toPlainText().strip()
        summary = f"[결과] {result.message}"
        if result.details and (not streamed or result.success):
            summary = f"{summary}\n{result.details}"

        if streamed:
            self.iperf_output.appendPlainText("")
            self.iperf_output.appendPlainText(summary)
            return

        self.iperf_output.setPlainText(summary)

    def _finish_iperf_operation(self) -> None:
        self.iperf_cancel_event = None
        self.iperf_manage_cancel_event = None
        self._set_iperf_running(False)
        self.refresh_iperf_availability()

    def _set_iperf_running(self, running: bool) -> None:
        self.iperf_run_button.setEnabled((not running) and self._iperf_available)
        self.iperf_cancel_button.setEnabled(running)
        self.iperf_refresh_button.setEnabled(not running)
        self.iperf_manage_button.setVisible(self._iperf_manage_enabled)
        self.iperf_manage_button.setEnabled((not running) and self._iperf_manage_enabled)
        self.iperf_public_refresh_button.setEnabled(
            (not running)
            and (self.iperf_mode_combo.currentData() == "client")
            and (not self._public_iperf_refresh_in_progress)
        )

    def cancel_iperf_test(self) -> None:
        if self.iperf_cancel_event:
            self.iperf_cancel_event.set()
        if self.iperf_manage_cancel_event:
            self.iperf_manage_cancel_event.set()
