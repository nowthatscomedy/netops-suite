from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QFont, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QStatusBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.guides import GuideCatalog, GuideDialog, QuickHelpPanel
from app.models.update_models import DownloadedUpdate, UpdateCheckResult
from app.ui.common import JobRunner, confirm_risky_action, make_menu_button
from app.ui.home import HomePage
from app.ui.tabs.config_builder_tab import ConfigBuilderTab
from app.ui.tabs.diagnostics_tab import DiagnosticsTab
from app.ui.tabs.inspector_tab import InspectorTab
from app.ui.tabs.interface_tab import InterfaceTab
from app.ui.tabs.settings_tab import SettingsTab
from app.ui.tabs.transfer_tab import TransferTab
from app.ui.tabs.wireless_tab import WirelessTab
from app.utils.admin import relaunch_as_admin
from app.utils.app_icon import load_app_icon
from app.version import __version__
from netops_suite.ui.icons import PAGE_ICONS, icon


_GUIDE_CONTEXT_PROPERTY = "guideContextId"
_MAIN_PAGE_KEY_PROPERTY = "mainPageKey"
_MAIN_PAGE_CONTEXT_SPECS = (
    ("interface_tab", "내 PC 네트워크", "interface", "interface"),
    ("diagnostics_tab", "연결 진단", "diagnostics", "diagnostics"),
    ("wireless_tab", "Wi-Fi 확인", "wireless", "wireless"),
    ("inspector_tab", "장비 점검·백업", "inspector", "inspector"),
    ("config_builder_tab", "설정 명령 만들기", "config_builder", "config-builder"),
    ("settings_tab", "설정", "settings", "settings"),
    ("home_page", "시작", "home", "getting-started"),
    ("transfer_tab", "파일 전송", "transfer", "diagnostics.transfer"),
)


class MainWindow(QMainWindow):
    _NAV_ITEMS = (
        ("home", "시작"),
        ("interface", "내 PC 네트워크"),
        ("diagnostics", "연결 진단"),
        ("wireless", "Wi-Fi 확인"),
        ("inspector", "장비 점검·백업"),
        ("config_builder", "설정 명령 만들기"),
        ("transfer", "파일 전송"),
    )
    _MAIN_PAGE_SPECS = _MAIN_PAGE_CONTEXT_SPECS
    _MAIN_PAGE_KEYS = tuple(spec[2] for spec in _MAIN_PAGE_CONTEXT_SPECS)
    _MAIN_GUIDE_IDS = tuple(spec[3] for spec in _MAIN_PAGE_CONTEXT_SPECS)
    _DIAGNOSTIC_GUIDE_IDS = {
        "ping": "diagnostics.ping",
        "tcp": "diagnostics.tcp",
        "dns": "diagnostics.dns",
        "trace": "diagnostics.trace",
        "iperf": "diagnostics.iperf",
        "arp": "diagnostics.arp",
        "subnet": "diagnostics.subnet",
        "oui": "diagnostics.oui",
        "transfer": "diagnostics.transfer",
        "commands": "diagnostics.commands",
    }

    def __init__(
        self,
        state: AppState,
        parent=None,
        startup_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.state = state
        self._shutdown_started = False
        self._startup_callback = startup_callback or (lambda _message, _detail="": None)
        self._report_startup("메인 창 준비", "윈도우 기본 속성과 작업 실행기를 준비합니다.")
        self._job_runner = JobRunner(self.state.thread_pool, self)
        self._active_workers = self._job_runner._active_workers
        self._update_busy = False
        self._startup_activated = False
        self.guide_catalog = GuideCatalog.load()
        self._guide_dialog: GuideDialog | None = None
        self._help_focus_return: QWidget | None = None
        self._last_workspace_focus: QWidget | None = None
        self._adapting_help = False
        self.setWindowTitle("NetOps Suite")
        self._apply_locale_font()
        self._apply_window_icon()
        self.resize(1280, 800)
        self.setMinimumSize(1024, 680)
        self.setDockOptions(
            QMainWindow.AnimatedDocks
            | QMainWindow.AllowNestedDocks
            | QMainWindow.AllowTabbedDocks
            | QMainWindow.GroupedDragging
        )

        self._build_ui()
        self._report_startup("이벤트 연결", "탭, 메뉴, 로그, 설정 변경 신호를 연결합니다.")
        self._connect_signals()
        self._report_startup("이전 화면 상태 복원", "마지막으로 열었던 탭과 도킹 패널 상태를 불러옵니다.")
        self._restore_ui_state()
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(500)
        self._activity_timer.timeout.connect(self._update_navigation_activity)
        self._activity_timer.start()
        self._startup_update_timer = QTimer(self)
        self._startup_update_timer.setSingleShot(True)
        self._startup_update_timer.timeout.connect(
            self._maybe_check_updates_on_startup
        )
        self._startup_update_timer.start(1200)

    def _report_startup(self, message: str, detail: str = "") -> None:
        self._startup_callback(message, detail)

    def _apply_window_icon(self) -> None:
        app = QApplication.instance()
        icon = app.windowIcon() if app and not app.windowIcon().isNull() else load_app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

    def _apply_locale_font(self) -> None:
        families = set(QFontDatabase.families())
        for family in ("Malgun Gothic", "맑은 고딕", "Segoe UI"):
            if family not in families:
                continue
            app = QApplication.instance()
            base_font = app.font() if app is not None else self.font()
            font = QFont(base_font)
            font.setFamily(family)
            self.setFont(font)
            if app is not None:
                app.setFont(font)
            return

    def _build_ui(self) -> None:
        self._report_startup("작업 영역 생성", "주요 기능 탭과 사이드 내비게이션을 구성합니다.")
        self.tab_widget = QTabWidget()
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.setElideMode(Qt.TextElideMode.ElideRight)
        self.tab_widget.setUsesScrollButtons(True)
        self.tab_widget.tabBar().hide()
        self._report_startup("네트워크 설정 화면 구성", "IP 프로파일과 어댑터 설정 화면을 준비합니다.")
        self.interface_tab = InterfaceTab(self.state)
        self._report_startup("연결 진단 화면 구성", "Ping, TCP, DNS, 파일 전송 도구 화면을 준비합니다.")
        self.diagnostics_tab = DiagnosticsTab(self.state)
        self._report_startup("Wi-Fi 분석 화면 구성", "무선 인터페이스와 주변 AP 분석 화면을 준비합니다.")
        self.wireless_tab = WirelessTab(self.state)
        self._report_startup("장비 작업 자동화 화면 구성", "대상 장비 목록 기반 점검과 백업 작업 화면을 준비합니다.")
        self.inspector_tab = InspectorTab(self.state)
        self._report_startup("장비 설정 생성 화면 구성", "장비 설정 생성 도구를 포함합니다.")
        self.config_builder_tab = ConfigBuilderTab(self.state)
        self._report_startup("설정 화면 구성", "프로그램, 저장 위치, 외부 도구와 설정 관리 화면을 준비합니다.")
        self.settings_tab = SettingsTab(self.state)
        self.home_page = HomePage()
        self.transfer_tab = TransferTab(self.diagnostics_tab)

        for attribute, title, page_key, guide_id in self._MAIN_PAGE_SPECS:
            page = getattr(self, attribute)
            page.setProperty(_MAIN_PAGE_KEY_PROPERTY, page_key)
            page.setProperty(_GUIDE_CONTEXT_PROPERTY, guide_id)
            self.tab_widget.addTab(page, title)
        self._bind_diagnostic_guide_contexts()

        self.view_menu = QMenu("보기", self)
        self.toggle_log_view_action = QAction("애플리케이션 로그", self)
        self.toggle_log_view_action.setCheckable(True)
        self.ping_result_view_action = QAction("Ping 결과 표", self)
        self.ping_result_view_action.setCheckable(True)
        self.tcp_result_view_action = QAction("포트 확인 결과 창 (TCPing)", self)
        self.tcp_result_view_action.setCheckable(True)
        self.view_menu.addAction(self.toggle_log_view_action)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.ping_result_view_action)
        self.view_menu.addAction(self.tcp_result_view_action)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("mainNavigation")
        self.nav_list.setAccessibleName("주요 화면")
        self.nav_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.nav_list.setIconSize(QSize(19, 19))
        for page_key, title in self._NAV_ITEMS:
            item = QListWidgetItem(title)
            item.setIcon(icon(PAGE_ICONS[page_key], "#b7cbea", 20))
            item.setData(Qt.ItemDataRole.UserRole, page_key)
            item.setData(Qt.ItemDataRole.UserRole + 1, title)
            self.nav_list.addItem(item)
        self.nav_list.setCurrentRow(0)

        nav_panel = QFrame()
        self.nav_panel = nav_panel
        nav_panel.setObjectName("sideNavigation")
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(14, 14, 14, 14)
        nav_layout.setSpacing(10)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(9)
        brand_icon = QLabel()
        brand_icon.setObjectName("appLogoTile")
        brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_icon.setPixmap(load_app_icon().pixmap(28, 28))
        brand_icon.setFixedSize(34, 34)
        brand_row.addWidget(brand_icon)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(3)
        title_label = QLabel("NetOps Suite")
        title_label.setObjectName("appTitle")
        version_label = QLabel(f"v{__version__}")
        version_label.setObjectName("appVersion")
        brand_text.addWidget(title_label)
        brand_text.addWidget(version_label)
        brand_row.addLayout(brand_text, 1)
        nav_layout.addLayout(brand_row)
        nav_layout.addSpacing(14)
        caption = QLabel("네트워크 작업")
        caption.setObjectName("navigationCaption")
        nav_layout.addWidget(caption)
        nav_layout.addWidget(self.nav_list, 1)
        self.settings_button = QToolButton()
        self.settings_button.setObjectName("sideUtilityButton")
        self.settings_button.setText("설정")
        self.settings_button.setIcon(icon("settings-2", "#b7cbea", 18))
        self.settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.settings_button.setCheckable(True)
        self.settings_button.clicked.connect(lambda: self.navigate_to("settings"))
        nav_layout.addWidget(self.settings_button)
        utility_row = QHBoxLayout()
        utility_row.setContentsMargins(0, 0, 0, 0)
        utility_row.setSpacing(6)
        self.restart_admin_action = QAction("관리자", self)
        self.restart_admin_action.setToolTip("관리자 권한으로 다시 실행")
        self.admin_button = QToolButton()
        self.admin_button.setObjectName("sideUtilityButton")
        self.admin_button.setDefaultAction(self.restart_admin_action)
        self.view_button = make_menu_button("보기", self.view_menu, "로그와 분리된 결과 표를 표시합니다.")
        self.view_button.setObjectName("sideUtilityButton")
        self.view_button.setMinimumHeight(28)
        self.view_button.setMaximumHeight(32)
        self.view_button.installEventFilter(self)
        self.guide_action = QAction("도움말", self)
        self.guide_action.setToolTip("현재 작업의 짧은 안내를 엽니다 (F1)")
        self.guide_action.setShortcut(QKeySequence("F1"))
        self.guide_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.addAction(self.guide_action)
        self.guide_button = QToolButton()
        self.guide_button.setObjectName("sideUtilityButton")
        self.guide_button.setAccessibleName("사용자 가이드")
        self.guide_button.setDefaultAction(self.guide_action)
        self.guide_button.setMinimumHeight(28)
        self.guide_button.setMaximumHeight(32)
        utility_row.addWidget(self.guide_button)
        utility_row.addWidget(self.admin_button)
        utility_row.addWidget(self.view_button)
        utility_row.addStretch(1)
        nav_layout.addLayout(utility_row)

        content_panel = QFrame()
        content_panel.setObjectName("workspacePanel")
        content_layout = QVBoxLayout(content_panel)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self.tab_widget)

        shell = QWidget()
        shell.setObjectName("appShell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(nav_panel)
        shell_layout.addWidget(content_panel, 1)
        self.setCentralWidget(shell)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_dock = QDockWidget("애플리케이션 로그", self)
        self.log_dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        self.log_dock.setFeatures(
            QDockWidget.DockWidgetClosable
            | QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
        )
        self.log_dock.setWidget(self.log_view)
        self.log_dock.setMinimumHeight(120)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)

        self.log_dock.hide()

        self.quick_help_panel = QuickHelpPanel(self.guide_catalog)
        self.help_dock = QDockWidget("현재 작업 도움말", self)
        self.help_dock.setObjectName("contextHelpDock")
        self.help_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.help_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.help_dock.setWidget(self.quick_help_panel)
        self.help_dock.setMinimumWidth(320)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.help_dock)
        self.help_dock.hide()
        self.quick_help_panel.full_guide_requested.connect(self.open_guide)
        self.help_dock.visibilityChanged.connect(self._help_visibility_changed)
        close_help = QAction(self.help_dock)
        close_help.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        close_help.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        close_help.triggered.connect(self.help_dock.hide)
        self.help_dock.addAction(close_help)

        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        self.admin_status_label = QLabel()
        self._update_admin_status()
        status_bar.addPermanentWidget(self.admin_status_label)
        status_bar.showMessage(f"준비 - v{__version__}")

    def _connect_signals(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._remember_workspace_focus)
        self.restart_admin_action.triggered.connect(self._restart_as_admin)
        self.guide_action.triggered.connect(lambda _checked=False: self.show_context_help())
        self.home_page.navigate_requested.connect(self.navigate_to)
        self.diagnostics_tab.transfer_requested.connect(lambda: self.navigate_to("transfer"))
        self.diagnostics_tab.wireless_requested.connect(lambda: self.navigate_to("wireless"))
        if hasattr(self.interface_tab, "admin_requested"):
            self.interface_tab.admin_requested.connect(self._restart_as_admin)
        self.tab_widget.currentChanged.connect(self._handle_main_tab_changed)
        self.tab_widget.currentChanged.connect(self._sync_nav_to_tab)
        self.nav_list.currentRowChanged.connect(self._handle_nav_changed)
        self.nav_list.itemClicked.connect(lambda item: self.navigate_to(str(item.data(Qt.ItemDataRole.UserRole))))
        self.nav_list.itemActivated.connect(lambda item: self.navigate_to(str(item.data(Qt.ItemDataRole.UserRole))))
        self.diagnostics_tab.diagnostic_stack.currentChanged.connect(self._refresh_context_help)
        for combo_name in ("file_transfer_role_combo", "file_transfer_mode_combo", "ftp_client_protocol_combo", "ftp_server_protocol_combo"):
            getattr(self.diagnostics_tab, combo_name).currentIndexChanged.connect(self._refresh_context_help)
        self.toggle_log_view_action.toggled.connect(self._set_log_dock_visible)
        self.ping_result_view_action.toggled.connect(
            lambda checked: self.diagnostics_tab.set_result_dock_visible("ping", checked)
        )
        self.tcp_result_view_action.toggled.connect(
            lambda checked: self.diagnostics_tab.set_result_dock_visible("tcp", checked)
        )
        self.settings_tab.check_updates_requested.connect(lambda config: self._check_for_updates(config, manual=True))
        self.settings_tab.integration_changed.connect(self._handle_integration_changed)
        self.diagnostics_tab.tool_settings_requested.connect(self._show_tool_settings)
        self.state.log_message.connect(self.log_view.appendPlainText)
        self.interface_tab.status_message.connect(self.statusBar().showMessage)
        self.state.config_reloaded.connect(self._update_admin_status)
        self.state.admin_status_changed.connect(lambda _is_admin: self._update_admin_status())
        self.diagnostics_tab.result_dock_visibility_changed.connect(self._sync_result_dock_action)
        self.log_dock.topLevelChanged.connect(self._sync_log_dock_state)
        self.log_dock.visibilityChanged.connect(self._sync_log_dock_state)

        self._sync_result_dock_action("ping", self.diagnostics_tab.is_result_dock_visible("ping"))
        self._sync_result_dock_action("tcp", self.diagnostics_tab.is_result_dock_visible("tcp"))
        self._sync_log_dock_state()
        self._sync_nav_to_tab(self.tab_widget.currentIndex())
        for diagnostic in self.guide_catalog.errors:
            self.state.logger.warning("User guide: %s", diagnostic)

    def _handle_nav_changed(self, row: int) -> None:
        item = self.nav_list.item(row)
        if item is not None:
            self.navigate_to(str(item.data(Qt.ItemDataRole.UserRole)))

    def navigate_to(self, page_key: str, tool_key: str | None = None) -> bool:
        """Select an existing workspace without starting a task or recreating it."""
        if page_key == "diagnostics" and tool_key == "transfer":
            page_key, tool_key = "transfer", None
        if page_key == "diagnostics" and tool_key == "wireless":
            page_key, tool_key = "wireless", None
        if page_key not in self._MAIN_PAGE_KEYS:
            return False
        if page_key == "diagnostics" and tool_key:
            if tool_key not in self.diagnostics_tab._diagnostic_tool_index_by_key:
                return False
            self.diagnostics_tab.select_tool(tool_key)
        elif page_key == "diagnostics" and self.diagnostics_tab._current_tool_key() == "transfer":
            self.diagnostics_tab.select_tool("ping")
        self.tab_widget.setCurrentIndex(self._MAIN_PAGE_KEYS.index(page_key))
        self._sync_nav_to_tab(self.tab_widget.currentIndex())
        self._refresh_context_help()
        return True

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is getattr(self, "view_button", None)
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Tab
            and not event.modifiers()
            & (
                Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
            and self._focus_current_page_first_control()
        ):
            return True
        return super().eventFilter(watched, event)

    def _focus_current_page_first_control(self) -> bool:
        page = self.tab_widget.currentWidget()
        if page is None:
            return False

        preferred = {
            self.interface_tab: self.interface_tab.refresh_button,
            self.diagnostics_tab: self.diagnostics_tab.diagnostic_tool_combo,
            self.wireless_tab: self.wireless_tab.refresh_button,
            self.inspector_tab: self.inspector_tab.mode_combo,
            self.config_builder_tab: self.config_builder_tab.full_editor_button,
            self.settings_tab: self.settings_tab.section_tabs.tabBar(),
            self.home_page: self.home_page.task_buttons["interface"],
            self.transfer_tab: self.diagnostics_tab.file_transfer_role_combo,
        }.get(page)
        if self._is_valid_page_focus_target(page, preferred):
            preferred.setFocus(Qt.FocusReason.TabFocusReason)
            return True

        candidate = page
        visited: set[int] = set()
        while candidate is not None:
            candidate = candidate.nextInFocusChain()
            if candidate is None or candidate is page or id(candidate) in visited:
                return False
            visited.add(id(candidate))
            if self._is_valid_page_focus_target(page, candidate):
                candidate.setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return False

    @staticmethod
    def _is_valid_page_focus_target(page: QWidget, candidate: QWidget | None) -> bool:
        if candidate is None or isinstance(candidate, (QScrollArea, QTabWidget)):
            return False
        if candidate is not page and not page.isAncestorOf(candidate):
            return False
        return (
            candidate.isEnabled()
            and candidate.isVisibleTo(page)
            and bool(candidate.focusPolicy() & Qt.FocusPolicy.TabFocus)
        )

    def _show_tool_settings(self, tool_key: str = "") -> None:
        self.navigate_to("settings")
        self.settings_tab.show_section("tools", tool_key)

    @staticmethod
    def _guide_id_for_widget(widget: QWidget | None) -> str:
        if widget is None:
            return ""
        return str(widget.property(_GUIDE_CONTEXT_PROPERTY) or "").strip()

    def _bind_diagnostic_guide_contexts(self) -> None:
        tool_keys = tuple(getattr(self.diagnostics_tab, "_diagnostic_tool_keys", ()))
        stack = getattr(self.diagnostics_tab, "diagnostic_stack", None)
        if stack is None:
            return
        for index, tool_key in enumerate(tool_keys):
            page = stack.widget(index)
            if page is not None:
                page.setProperty(
                    _GUIDE_CONTEXT_PROPERTY,
                    self._DIAGNOSTIC_GUIDE_IDS.get(str(tool_key), ""),
                )

    def main_guide_context_ids(self) -> tuple[str, ...]:
        """Return guide IDs attached to the actual main-tab widgets."""

        return tuple(
            self._guide_id_for_widget(self.tab_widget.widget(index))
            for index in range(self.tab_widget.count())
        )

    def diagnostic_guide_contexts(self) -> dict[str, str]:
        """Return actual diagnostic tool keys and their attached guide IDs."""

        tool_keys = tuple(getattr(self.diagnostics_tab, "_diagnostic_tool_keys", ()))
        stack = getattr(self.diagnostics_tab, "diagnostic_stack", None)
        return {
            str(tool_key): self._guide_id_for_widget(
                stack.widget(index) if stack is not None else None
            )
            for index, tool_key in enumerate(tool_keys)
        }

    def transfer_guide_context_ids(self) -> frozenset[str]:
        """Enumerate context IDs reachable from the actual transfer selectors."""

        role_combo = getattr(self.diagnostics_tab, "file_transfer_role_combo", None)
        mode_combo = getattr(self.diagnostics_tab, "file_transfer_mode_combo", None)
        if role_combo is None or mode_combo is None:
            return frozenset()

        contexts: set[str] = set()
        for role_index in range(role_combo.count()):
            role_value = int(role_combo.itemData(role_index) or 0)
            role = "server" if role_value == 1 else "client"
            for mode_index in range(mode_combo.count()):
                mode_value = int(mode_combo.itemData(mode_index) or 0)
                if mode_value == 1:
                    protocols = ("scp",)
                elif mode_value == 2:
                    protocols = ("tftp",)
                elif mode_value == 0:
                    protocol_combo = getattr(
                        self.diagnostics_tab,
                        f"ftp_{role}_protocol_combo",
                        None,
                    )
                    protocols = (
                        tuple(
                            str(protocol_combo.itemData(index) or "").casefold()
                            for index in range(protocol_combo.count())
                        )
                        if protocol_combo is not None
                        else ()
                    )
                else:
                    protocols = ()
                contexts.update(
                    f"diagnostics.transfer.{protocol}.{role}"
                    for protocol in protocols
                    if protocol
                )
        return frozenset(contexts)

    def guide_context_coverage_errors(self) -> tuple[str, ...]:
        """Report structural or catalog drift in context-sensitive help coverage."""

        errors: list[str] = []
        main_contexts = self.main_guide_context_ids()
        if self.tab_widget.count() != len(self._MAIN_PAGE_SPECS):
            errors.append(
                "main tab count does not match the registered main-page guide specs"
            )
        if any(not guide_id for guide_id in main_contexts):
            errors.append("one or more main tabs have no guide context ID")
        if main_contexts != self._MAIN_GUIDE_IDS:
            errors.append(
                "main-tab guide contexts differ from the registered page order "
                f"(actual={main_contexts!r}, expected={self._MAIN_GUIDE_IDS!r})"
            )

        diagnostic_contexts = self.diagnostic_guide_contexts()
        actual_tool_keys = set(diagnostic_contexts)
        mapped_tool_keys = set(self._DIAGNOSTIC_GUIDE_IDS)
        if actual_tool_keys != mapped_tool_keys:
            missing = sorted(actual_tool_keys - mapped_tool_keys)
            stale = sorted(mapped_tool_keys - actual_tool_keys)
            errors.append(
                "diagnostic guide mapping differs from actual tools "
                f"(missing={missing}, stale={stale})"
            )
        for tool_key, guide_id in diagnostic_contexts.items():
            expected = self._DIAGNOSTIC_GUIDE_IDS.get(tool_key, "")
            if guide_id != expected:
                errors.append(
                    f"diagnostic tool {tool_key!r} has guide {guide_id!r}; "
                    f"expected {expected!r}"
                )

        role_combo = getattr(self.diagnostics_tab, "file_transfer_role_combo", None)
        mode_combo = getattr(self.diagnostics_tab, "file_transfer_mode_combo", None)
        if role_combo is None or mode_combo is None:
            errors.append("transfer guide selectors are unavailable")
        else:
            role_values = tuple(
                int(role_combo.itemData(index) or 0)
                for index in range(role_combo.count())
            )
            mode_values = tuple(
                int(mode_combo.itemData(index) or 0)
                for index in range(mode_combo.count())
            )
            if role_values != (0, 1):
                errors.append(
                    f"transfer role guide mapping is stale: {role_values!r}"
                )
            if mode_values != (0, 1, 2):
                errors.append(
                    f"transfer mode guide mapping is stale: {mode_values!r}"
                )
            for role in ("client", "server"):
                protocol_combo = getattr(
                    self.diagnostics_tab,
                    f"ftp_{role}_protocol_combo",
                    None,
                )
                protocol_values = (
                    tuple(
                        str(protocol_combo.itemData(index) or "").casefold()
                        for index in range(protocol_combo.count())
                    )
                    if protocol_combo is not None
                    else ()
                )
                if protocol_values != ("ftp", "ftps", "sftp"):
                    errors.append(
                        f"transfer {role} protocol guide mapping is stale: "
                        f"{protocol_values!r}"
                    )

        transfer_contexts = self.transfer_guide_context_ids()
        registered_transfer_contexts = {
            entry.id
            for entry in self.guide_catalog.entries
            if entry.parent_id == "diagnostics.transfer"
        }
        if transfer_contexts != registered_transfer_contexts:
            missing = sorted(transfer_contexts - registered_transfer_contexts)
            stale = sorted(registered_transfer_contexts - transfer_contexts)
            errors.append(
                "transfer guide contexts differ from actual selectors "
                f"(missing={missing}, stale={stale})"
            )

        context_ids = {
            *main_contexts,
            *diagnostic_contexts.values(),
            *transfer_contexts,
        }
        for guide_id in sorted(context_ids):
            if guide_id and self.guide_catalog.get(guide_id) is None:
                errors.append(f"guide context is not registered exactly: {guide_id}")
        return tuple(errors)

    def current_guide_id(self) -> str:
        builder_window = getattr(self.config_builder_tab, "_builder_window", None)
        if builder_window is not None and QApplication.activeWindow() is builder_window:
            return "config-builder"
        current_page = self.tab_widget.currentWidget()
        if current_page is self.transfer_tab:
            return self._current_transfer_guide_id()
        if current_page is self.diagnostics_tab:
            current_tool = getattr(self.diagnostics_tab, "_current_tool_key", None)
            if callable(current_tool):
                tool_key = str(current_tool() or "")
                if tool_key == "transfer":
                    return self._current_transfer_guide_id()
                diagnostic_page = self.diagnostics_tab.diagnostic_stack.currentWidget()
                return self._guide_id_for_widget(diagnostic_page) or "diagnostics"
        return self._guide_id_for_widget(current_page) or "getting-started"

    def _current_transfer_guide_id(self) -> str:
        role_combo = getattr(self.diagnostics_tab, "file_transfer_role_combo", None)
        mode_combo = getattr(self.diagnostics_tab, "file_transfer_mode_combo", None)
        if role_combo is None or mode_combo is None:
            return "diagnostics.transfer"
        role = "server" if int(role_combo.currentData() or 0) == 1 else "client"
        mode_index = int(mode_combo.currentData() or 0)
        if mode_index == 1:
            protocol = "scp"
        elif mode_index == 2:
            protocol = "tftp"
        else:
            protocol_combo = getattr(
                self.diagnostics_tab,
                f"ftp_{role}_protocol_combo",
                None,
            )
            protocol = (
                str(protocol_combo.currentData() or "ftp").casefold()
                if protocol_combo is not None
                else "ftp"
            )
            if protocol not in {"ftp", "ftps", "sftp"}:
                protocol = "ftp"
        return f"diagnostics.transfer.{protocol}.{role}"

    def open_guide(self, feature_id: str | None = None) -> bool:
        """Open help for a stable guide ID, or for the current app context."""

        if self._guide_dialog is None:
            self._guide_dialog = GuideDialog(
                self.guide_catalog,
                context_provider=self.current_guide_id,
                parent=self,
            )
        target = feature_id.strip() if isinstance(feature_id, str) else ""
        if not target:
            target = self.current_guide_id()
        return self._guide_dialog.open_guide(target)

    def show_context_help(self) -> bool:
        builder_window = getattr(self.config_builder_tab, "_builder_window", None)
        self._help_for_detached_builder = (
            builder_window is not None and QApplication.activeWindow() is builder_window
        )
        if not self.help_dock.isVisible():
            self._help_focus_return = self._last_workspace_focus or QApplication.focusWidget()
        available = self.quick_help_panel.show_guide(self.current_guide_id())
        self._adapt_help_layout()
        self.help_dock.show()
        self.help_dock.raise_()
        self.quick_help_panel.setFocus(Qt.FocusReason.ShortcutFocusReason)
        return available

    def _refresh_context_help(self, *_args) -> None:
        if hasattr(self, "help_dock") and self.help_dock.isVisible():
            self.quick_help_panel.show_guide(self.current_guide_id())

    def _adapt_help_layout(self) -> None:
        if self._adapting_help or not hasattr(self, "help_dock"):
            return
        self._adapting_help = True
        try:
            floating = getattr(self, "_help_for_detached_builder", False) or self.width() - self.nav_panel.width() - 344 < 720
            if floating != self.help_dock.isFloating():
                self.help_dock.setFloating(floating)
            if floating:
                self.help_dock.resize(360, min(620, self.height() - 60))
            else:
                self.resizeDocks([self.help_dock], [336], Qt.Orientation.Horizontal)
        finally:
            self._adapting_help = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "help_dock") and self.help_dock.isVisible():
            QTimer.singleShot(0, self._adapt_help_layout)

    def _help_visibility_changed(self, visible: bool) -> None:
        if visible or self._adapting_help:
            return
        target = self._help_focus_return
        self._help_focus_return = None
        self._help_for_detached_builder = False
        def restore_focus() -> None:
            if self._shutdown_started:
                return
            try:
                if target is not None and target.isVisible() and target.isEnabled():
                    target.window().activateWindow()
                    target.setFocus(Qt.FocusReason.OtherFocusReason)
                else:
                    self.activateWindow()
                    self._focus_current_page_first_control()
            except RuntimeError:
                self._focus_current_page_first_control()
        QTimer.singleShot(0, restore_focus)

    def _remember_workspace_focus(self, _old: QWidget | None, current: QWidget | None) -> None:
        page = self.tab_widget.currentWidget()
        builder = self.config_builder_tab.builder_widget
        if current is not None and (
            (page is not None and page.isAncestorOf(current)) or builder.isAncestorOf(current)
        ):
            self._last_workspace_focus = current

    def _maybe_show_first_run_guide(self) -> None:
        """Remember onboarding without interrupting the selected workspace."""
        guide_config = self.state.app_config.get("guide", {})
        if isinstance(guide_config, dict) and bool(guide_config.get("welcome_seen", False)):
            return
        config = dict(self.state.app_config)
        normalized_guide_config = dict(guide_config) if isinstance(guide_config, dict) else {}
        normalized_guide_config["welcome_seen"] = True
        config["guide"] = normalized_guide_config
        try:
            self.state.save_app_config(config)
        except Exception as exc:
            self.state.logger.warning("Failed to save guide welcome state: %s", exc)

    def _handle_integration_changed(self, integration: str) -> None:
        if integration == "iperf3":
            self.diagnostics_tab.refresh_iperf_availability(deep_check=False)

    def _sync_nav_to_tab(self, index: int) -> None:
        if not hasattr(self, "nav_list"):
            return
        page = self.tab_widget.widget(index)
        page_key = str(page.property(_MAIN_PAGE_KEY_PROPERTY) or "") if page else ""
        row = next((row for row in range(self.nav_list.count()) if self.nav_list.item(row).data(Qt.ItemDataRole.UserRole) == page_key), -1)
        self.nav_list.blockSignals(True)
        if row >= 0:
            self.nav_list.setCurrentRow(row)
        else:
            # Keep a valid keyboard cursor: clearing the current index makes Qt
            # select row zero when focus falls back here after a settings tab hides.
            self.nav_list.clearSelection()
        self.nav_list.blockSignals(False)
        self.settings_button.setChecked(page_key == "settings")
        self._refresh_context_help()

    def _update_navigation_activity(self) -> None:
        diagnostic = self.diagnostics_tab
        transfer_busy = diagnostic.is_transfer_running()
        busy = {
            "interface": bool(getattr(self.interface_tab, "_active_workers", ())),
            "diagnostics": bool(diagnostic.active_task_keys() - {"transfer"}),
            "wireless": bool(getattr(self.wireless_tab, "_active_workers", ())),
            "inspector": bool(getattr(self.inspector_tab, "_inspector_running", False)),
            "transfer": transfer_busy,
        }
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            title = str(item.data(Qt.ItemDataRole.UserRole + 1))
            running = busy.get(str(item.data(Qt.ItemDataRole.UserRole)), False)
            item.setText(title + (" · 실행 중" if running else ""))
            item.setToolTip(title.strip() + (" — 작업이 진행 중입니다." if running else ""))

    def _update_admin_status(self) -> None:
        text = "관리자 권한 사용 중" if self.state.is_admin else "관리자 권한 미사용"
        accent = "#16a34a" if self.state.is_admin else "#d97706"
        self.admin_status_label.setText(text)
        self.admin_status_label.setStyleSheet(
            f"background:#ffffff; color:#344054; border:1px solid #d0d5dd; border-left:3px solid {accent}; "
            "border-radius:4px; padding:3px 8px 3px 7px; font-weight:600;"
        )
        if hasattr(self, "restart_admin_action"):
            self.restart_admin_action.setEnabled(not self.state.is_admin)
            self.restart_admin_action.setToolTip(
                "이미 관리자 권한으로 실행 중입니다."
                if self.state.is_admin
                else "관리자 권한으로 다시 실행"
            )

    def _restart_as_admin(self) -> None:
        if self.state.is_admin:
            QMessageBox.information(self, "안내", "이미 관리자 권한으로 실행 중입니다.")
            return
        if relaunch_as_admin():
            self.close()
            return
        QMessageBox.warning(self, "실행 실패", "관리자 권한 요청이 취소되었거나 실행에 실패했습니다.")

    def _sync_log_dock_state(self) -> None:
        shown_state = not self.log_dock.isHidden()
        self.toggle_log_view_action.blockSignals(True)
        self.toggle_log_view_action.setChecked(shown_state)
        self.toggle_log_view_action.blockSignals(False)
        self.log_dock.setMaximumHeight(16777215 if self.log_dock.isFloating() else 180)

    def _set_log_dock_visible(self, visible: bool) -> None:
        self.log_dock.setVisible(visible)
        if visible:
            self.log_dock.show()
            self.log_dock.raise_()

    def _sync_result_dock_action(self, key: str, visible: bool) -> None:
        action = self.ping_result_view_action if key == "ping" else self.tcp_result_view_action
        action.blockSignals(True)
        action.setChecked(visible)
        action.blockSignals(False)

    def _restore_ui_state(self) -> None:
        ui_state = self.state.get_ui_state()
        window_state = ui_state.get("main_window", {})

        self.interface_tab.restore_ui_state(ui_state.get("interface_tab", {}))
        self.diagnostics_tab.restore_ui_state(ui_state.get("diagnostics_tab", {}))
        self.wireless_tab.restore_ui_state(ui_state.get("wireless_tab", {}))
        self.settings_tab.restore_ui_state(ui_state.get("settings_tab", {}))

        page_key = str(window_state.get("current_page_key", "") or "")
        if page_key not in self._MAIN_PAGE_KEYS:
            try:
                legacy_index = int(window_state.get("current_tab", -1))
            except (TypeError, ValueError):
                legacy_index = -1
            legacy_pages = {0: "interface", 1: "diagnostics", 2: "wireless", 3: "inspector", 4: "config_builder", 6: "settings", 7: "settings"}
            page_key = "home" if page_key else legacy_pages.get(legacy_index, "home")
        if page_key == "diagnostics" and self.diagnostics_tab._current_tool_key() == "transfer":
            page_key = "transfer"
        self.navigate_to(page_key)

        log_visible = bool(window_state.get("log_dock_visible", False))
        ping_result_visible = bool(window_state.get("ping_result_dock_visible", False))
        tcp_result_visible = bool(window_state.get("tcp_result_dock_visible", False))

        self._set_log_dock_visible(log_visible)
        self.diagnostics_tab.set_result_dock_visible("ping", ping_result_visible)
        self.diagnostics_tab.set_result_dock_visible("tcp", tcp_result_visible)
        self._sync_log_dock_state()
        self._sync_result_dock_action("ping", ping_result_visible)
        self._sync_result_dock_action("tcp", tcp_result_visible)

    def activate_startup_loading(self) -> None:
        if self._startup_activated:
            return
        self._startup_activated = True
        QTimer.singleShot(0, self._start_visible_tab_initial_load)
        QTimer.singleShot(0, self._maybe_show_first_run_guide)

    def _handle_main_tab_changed(self, index: int) -> None:
        self._refresh_context_help()
        if not self._startup_activated:
            return
        self._start_tab_initial_load(index)

    def _start_visible_tab_initial_load(self) -> None:
        self._start_tab_initial_load(self.tab_widget.currentIndex())

    def _start_tab_initial_load(self, index: int) -> None:
        page = self.tab_widget.widget(index)
        if page in (self.interface_tab, self.diagnostics_tab, self.wireless_tab):
            page.start_initial_refresh()

    def _save_ui_state(self) -> None:
        if bool(getattr(self.state, "settings_reset_pending_restart", False)):
            return
        config = dict(self.state.app_config)
        current_index = self.tab_widget.currentIndex()
        current_page_key = (
            self._MAIN_PAGE_KEYS[current_index]
            if 0 <= current_index < len(self._MAIN_PAGE_KEYS)
            else "home"
        )
        ui_state = dict(self.state.get_ui_state())
        ui_state.update({
            "main_window": {
                "current_page_key": current_page_key,
                "log_dock_visible": not self.log_dock.isHidden(),
                "ping_result_dock_visible": self.diagnostics_tab.is_result_dock_visible("ping"),
                "tcp_result_dock_visible": self.diagnostics_tab.is_result_dock_visible("tcp"),
            },
            "interface_tab": self.interface_tab.save_ui_state(),
            "diagnostics_tab": self.diagnostics_tab.save_ui_state(),
            "wireless_tab": self.wireless_tab.save_ui_state(),
            "settings_tab": self.settings_tab.save_ui_state(),
        })
        config["ui_state"] = ui_state
        self.state.save_app_config(config)

    def _maybe_check_updates_on_startup(self) -> None:
        update_config = dict(self.state.app_config.get("update", {}) or {})
        if not update_config.get("check_on_startup", False):
            return
        self._check_for_updates(update_config, manual=False)

    def _check_for_updates(self, update_config: dict, manual: bool) -> None:
        if self._update_busy:
            if manual:
                QMessageBox.information(self, "업데이트 확인", "이미 업데이트 작업이 진행 중입니다.")
            return

        self._update_busy = True
        self.settings_tab.set_update_busy(True)
        self.settings_tab.set_update_status("업데이트를 확인하는 중입니다...")
        self.statusBar().showMessage("GitHub 업데이트 확인 중...")

        self._start_worker(
            self.state.update_service.check_for_updates,
            __version__,
            dict(update_config),
            on_progress=self._handle_update_progress,
            on_result=lambda result, manual=manual: self._handle_update_check_result(result, manual),
            on_finished=self._finish_update_check,
            on_error=lambda text, manual=manual: self._handle_update_error(text, manual, "업데이트 확인 실패"),
        )

    def _finish_update_check(self) -> None:
        self._update_busy = False
        self.settings_tab.set_update_busy(False)

    def _handle_update_progress(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        message = str(event.get("message", "") or "")
        if message:
            self.settings_tab.set_update_status(message)
            self.statusBar().showMessage(message)

    def _handle_update_error(self, text: str, manual: bool, title: str) -> None:
        self.settings_tab.set_update_status(title, text)
        self.statusBar().showMessage(title)
        if manual:
            QMessageBox.warning(self, title, text)

    def _handle_update_check_result(self, result: UpdateCheckResult, manual: bool) -> None:
        details_lines = []
        if result.release_name:
            details_lines.append(f"릴리즈: {result.release_name}")
        if result.latest_version:
            details_lines.append(f"최신 버전: {result.latest_version}")
        if result.published_at:
            details_lines.append(f"게시일: {result.published_at}")
        if result.release_url:
            details_lines.append(f"링크: {result.release_url}")
        if result.details:
            details_lines.extend(["", result.details])
        if result.body:
            details_lines.extend(["", "[릴리즈 노트]", result.body.strip()])

        details_text = "\n".join(details_lines).strip()
        self.settings_tab.set_update_status(result.message, details_text)
        self.statusBar().showMessage(result.message)

        if result.requires_config:
            if manual:
                QMessageBox.information(self, "업데이트 설정 필요", result.message + "\n\n" + result.details)
            return
        if not result.update_available:
            if manual:
                QMessageBox.information(self, "업데이트 확인", result.message)
            return
        if not result.install_ready:
            if manual:
                QMessageBox.warning(self, "업데이트 파일 확인 필요", result.message + "\n\n" + result.details)
            return

        message_lines = [
            f"현재 버전: {result.current_version}",
            f"최신 버전: {result.latest_version}",
        ]
        if result.release_name:
            message_lines.append(f"릴리즈: {result.release_name}")
        if result.asset:
            message_lines.append(f"설치 파일: {result.asset.name}")
        if result.verification_source == "github_release_digest":
            message_lines.append("무결성 검증: GitHub Releases SHA-256 digest")
        elif result.verification_source == "checksum_asset":
            message_lines.append("무결성 검증: 릴리즈 체크섬 파일 SHA-256")
        message_lines.extend(
            [
                "게시자 신뢰: 설치 프로그램 실행 전 Windows 코드서명 정보를 확인하세요.",
                "",
                "다운로드 및 검증을 마치면 설치 프로그램을 실행할 수 있습니다.",
            ]
        )

        if QMessageBox.question(self, "업데이트 발견", "\n".join(message_lines)) != QMessageBox.Yes:
            return
        self._download_update(result)

    def _download_update(self, check_result: UpdateCheckResult) -> None:
        self._update_busy = True
        self.settings_tab.set_update_busy(True)
        self.settings_tab.set_update_status("업데이트 파일을 다운로드하는 중입니다...")
        self.statusBar().showMessage("업데이트 다운로드 중...")

        self._start_worker(
            self.state.update_service.download_update,
            check_result,
            on_progress=self._handle_update_progress,
            on_result=self._handle_downloaded_update,
            on_finished=self._finish_update_check,
            on_error=lambda text: self._handle_update_error(text, True, "업데이트 다운로드 실패"),
        )

    def _handle_downloaded_update(self, downloaded: DownloadedUpdate) -> None:
        details = [
            f"버전: {downloaded.version}",
            f"파일: {downloaded.asset_name}",
            f"위치: {downloaded.asset_path}",
            f"SHA-256: {downloaded.sha256}",
        ]
        if downloaded.verification_source:
            details.append(f"검증: {downloaded.verification_source}")
        details.append("게시자 신뢰: SHA-256은 파일 무결성 검증이며, 게시자 신원은 코드서명으로 별도 확인해야 합니다.")

        self.settings_tab.set_update_status("업데이트 파일 검증을 완료했습니다.", "\n".join(details))
        self.statusBar().showMessage("업데이트 파일 검증 완료")

        if not confirm_risky_action(
            self,
            "업데이트 설치",
            impact="현재 프로그램을 종료하고 검증된 설치 프로그램을 실행합니다. 설치 중에는 NetOps Suite를 사용할 수 없습니다.",
            reversibility="설치 전에는 취소할 수 있습니다. 설치 후 되돌리기는 Windows 앱 제거 또는 이전 버전 재설치가 필요할 수 있습니다.",
            output_location=f"업데이트 상태와 검증 정보는 설정 화면에 표시되고 설치 파일은 {downloaded.asset_path}에 남습니다.",
            question="검증한 설치 프로그램을 실행할까요?",
            confirm_text="설치 실행",
        ):
            return

        try:
            self.state.update_service.launch_installer(downloaded.asset_path, expected_sha256=downloaded.sha256)
        except Exception as exc:
            QMessageBox.warning(self, "설치 프로그램 실행 실패", str(exc))
            return

        self._save_ui_state()
        self.close()

    def _start_worker(
        self,
        fn: Callable,
        *args,
        on_started: Callable[[], None] | None = None,
        on_progress: Callable | None = None,
        on_result: Callable | None = None,
        on_finished: Callable | None = None,
        on_error: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        self._job_runner.start(
            fn,
            *args,
            on_started=on_started,
            on_progress=on_progress,
            on_result=on_result,
            on_finished=on_finished,
            on_error=on_error,
            **kwargs,
        )

    def _discard_worker(self, worker) -> None:
        self._job_runner._discard_worker(worker)

    def closeEvent(self, event) -> None:
        if hasattr(self, "config_builder_tab") and not self.config_builder_tab.prepare_close():
            event.ignore()
            return
        self._save_ui_state()
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        activity_timer = getattr(self, "_activity_timer", None)
        if activity_timer is not None:
            activity_timer.stop()
        if hasattr(self, "help_dock"):
            self.help_dock.hide()
        startup_update_timer = getattr(self, "_startup_update_timer", None)
        if startup_update_timer is not None:
            startup_update_timer.stop()
        guide_dialog = getattr(self, "_guide_dialog", None)
        if guide_dialog is not None:
            guide_dialog.close()
        for tab_name in ("diagnostics_tab", "wireless_tab", "inspector_tab", "settings_tab"):
            tab = getattr(self, tab_name, None)
            shutdown = getattr(tab, "shutdown", None)
            if callable(shutdown):
                shutdown()
        thread_pool = getattr(self.state, "thread_pool", None)
        if thread_pool is not None:
            thread_pool.waitForDone(5000)
        self.state.shutdown()
