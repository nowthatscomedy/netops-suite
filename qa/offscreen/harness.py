from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QWidget,
)

import app.ui.tabs.ai_chat_tab as ai_chat_module
from app.app_state import AppState
from app.main_window import MainWindow
from app.ui.common.theme import APP_STYLE_SHEET
from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog
from app.ui.tabs.ai_chat_tab import AiChatTab
from qa.offscreen.fakes import (
    ControlledThreadPool,
    install_deterministic_services,
)


@dataclass(slots=True)
class ScenarioResult:
    scenario_id: str
    title: str
    status: str
    duration_ms: int
    screenshot: str = ""
    checks: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "passed"


@dataclass(slots=True)
class OffscreenQaReport:
    output_dir: Path
    results: list[ScenarioResult]
    layout_checks: list[str]
    runtime_root: str
    started_at: str
    finished_at: str
    markdown_path: Path
    json_path: Path

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)

    def summary_text(self) -> str:
        passed = sum(result.ok for result in self.results)
        failed = len(self.results) - passed
        return (
            f"Offscreen QA: {passed} passed, {failed} failed, "
            f"{len(self.layout_checks)} layout checks"
        )


class OffscreenQaHarness:
    """Runs deterministic, user-like Qt interactions without controlling Windows."""

    def __init__(
        self,
        *,
        project_root: Path,
        config_path: Path,
        output_dir: Path,
        keep_runtime: bool = False,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config_path = config_path.resolve()
        self.output_dir = output_dir.resolve()
        self.keep_runtime = keep_runtime
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.app: QApplication | None = None
        self.state: AppState | None = None
        self.window: MainWindow | None = None
        self.pool: ControlledThreadPool | None = None
        self.runtime_root: Path | None = None
        self._temporary_runtime: tempfile.TemporaryDirectory | None = None
        self._patchers: list[object] = []
        self._message_log: list[tuple[str, str, str]] = []
        self._active_capture_widget: QWidget | None = None
        self._post_capture: Callable[[], None] | None = None
        self._current_checks: list[str] = []
        self._capture_index = 0
        self._original_app_font: QFont | None = None
        self._original_style_sheet = ""
        self._original_malgun_substitutions: list[str] = []
        self._application_font_ids: list[int] = []
        self._qa_logger = logging.Logger("netops_suite.offscreen_qa")
        self._qa_logger.addHandler(logging.NullHandler())

    def run(self) -> OffscreenQaReport:
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        results: list[ScenarioResult] = []
        layout_checks: list[str] = []
        try:
            self._setup()
            for scenario in self.config.get("scenarios", []):
                results.append(self._run_scenario(dict(scenario)))
            layout_checks = self._run_layout_sweep()
        finally:
            self._teardown()

        finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
        json_path = self.output_dir / "report.json"
        markdown_path = self.output_dir / "report.md"
        report = OffscreenQaReport(
            output_dir=self.output_dir,
            results=results,
            layout_checks=layout_checks,
            runtime_root=str(self.runtime_root or ""),
            started_at=started_at,
            finished_at=finished_at,
            markdown_path=markdown_path,
            json_path=json_path,
        )
        self._write_report(report)
        return report

    def _setup(self) -> None:
        self._validate_config()
        self._temporary_runtime = tempfile.TemporaryDirectory(
            prefix="netops-offscreen-qa-"
        )
        self.runtime_root = Path(self._temporary_runtime.name).resolve()
        self.app = QApplication.instance() or QApplication([])
        self._original_app_font = QFont(self.app.font())
        self._original_style_sheet = self.app.styleSheet()
        self._original_malgun_substitutions = list(
            QFont.substitutes("Malgun Gothic")
        )
        self._install_offscreen_fonts()
        self.app.setStyleSheet(APP_STYLE_SHEET)

        self._patchers = [
            patch(
                "app.app_state.configure_logging",
                lambda *_args, **_kwargs: self._qa_logger,
            ),
            patch(
                "app.app_state.shutdown_logging",
                lambda *_args, **_kwargs: None,
            ),
            patch.object(
                AiChatTab,
                "refresh_provider_status",
                lambda tab, allow_repair=False: tab._set_status(
                    "QA 대역", "외부 AI CLI를 실행하지 않습니다."
                ),
            ),
            patch.object(
                AiChatTab,
                "_ensure_model_catalog_fresh",
                lambda *_args, **_kwargs: None,
            ),
            patch.object(
                MainWindow,
                "_maybe_check_updates_on_startup",
                lambda *_args, **_kwargs: None,
            ),
            patch.object(QMessageBox, "warning", self._message_handler("warning")),
            patch.object(
                QMessageBox, "information", self._message_handler("information")
            ),
            patch.object(
                QMessageBox,
                "question",
                self._question_handler,
            ),
        ]
        for patcher in self._patchers:
            patcher.start()

        self.state = AppState(self.runtime_root)
        self.state.app_config["update"]["check_on_startup"] = False
        self.state.app_config["ui_state"] = {}
        self.pool = ControlledThreadPool()
        self.state.thread_pool = self.pool
        install_deterministic_services(self.state)

        self.window = MainWindow(self.state)
        self.window.resize(1280, 800)
        self.window.show()
        self.window.log_dock.hide()
        self.window.diagnostics_tab.set_result_dock_visible("ping", False)
        self.window.diagnostics_tab.set_result_dock_visible("tcp", False)
        self.window.settings_tab._tools_loaded = True
        self._flush()

    def _teardown(self) -> None:
        try:
            if self._post_capture is not None:
                self._post_capture()
                self._post_capture = None
            if self.pool is not None:
                self.pool.release_all()
            if self.window is not None:
                self.window.shutdown()
                self.window.close()
                self.window.deleteLater()
            if self.app is not None:
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.app.processEvents()
                self.app.setStyleSheet(self._original_style_sheet)
                if self._original_app_font is not None:
                    self.app.setFont(self._original_app_font)
                QFont.removeSubstitutions("Malgun Gothic")
                if self._original_malgun_substitutions:
                    QFont.insertSubstitutions(
                        "Malgun Gothic",
                        self._original_malgun_substitutions,
                    )
                for font_id in self._application_font_ids:
                    QFontDatabase.removeApplicationFont(font_id)
                self._application_font_ids.clear()
                self.app.processEvents()
        finally:
            for patcher in reversed(self._patchers):
                patcher.stop()
            self._patchers.clear()
            if self.keep_runtime and self.runtime_root is not None:
                preserved = self.output_dir / "runtime"
                suffix = 1
                while preserved.exists():
                    preserved = self.output_dir / f"runtime-{suffix:02d}"
                    suffix += 1
                shutil.copytree(self.runtime_root, preserved)
                self.runtime_root = preserved
            if self._temporary_runtime is not None:
                self._temporary_runtime.cleanup()
                self._temporary_runtime = None

    def _validate_config(self) -> None:
        if self.config.get("schema_version") != 1:
            raise ValueError("지원하지 않는 오프스크린 QA 구성 버전입니다.")
        scenarios = self.config.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios:
            raise ValueError("오프스크린 QA 시나리오가 비어 있습니다.")
        known = {
            name.removeprefix("_scenario_")
            for name in dir(self)
            if name.startswith("_scenario_")
        }
        configured = [str(item.get("id", "")) for item in scenarios]
        unknown = [scenario_id for scenario_id in configured if scenario_id not in known]
        if unknown:
            raise ValueError(f"알 수 없는 오프스크린 QA 시나리오: {unknown}")
        if len(configured) != len(set(configured)):
            raise ValueError("오프스크린 QA 시나리오 ID가 중복되었습니다.")

    def _install_offscreen_fonts(self) -> None:
        candidates = (
            Path(r"C:\Windows\Fonts\malgun.ttf"),
            Path(r"C:\Windows\Fonts\malgunbd.ttf"),
            Path(r"C:\Windows\Fonts\NotoSansKR-VF.ttf"),
            Path(r"C:\Windows\Fonts\gulim.ttc"),
        )
        for candidate in candidates:
            if candidate.is_file():
                font_id = QFontDatabase.addApplicationFont(str(candidate))
                if font_id >= 0:
                    self._application_font_ids.append(font_id)
        QFont.insertSubstitution("Malgun Gothic", "Noto Sans KR")
        families = set(QFontDatabase.families())
        family = next(
            (
                name
                for name in ("Malgun Gothic", "맑은 고딕", "Noto Sans KR", "Gulim")
                if name in families
            ),
            self.app.font().family() if self.app is not None else "",
        )
        if self.app is not None and family:
            font = QFont(self.app.font())
            font.setFamily(family)
            self.app.setFont(font)

    def _message_handler(self, kind: str):
        def handler(_parent, title: str, text: str, *args, **kwargs):
            del args, kwargs
            self._message_log.append((kind, str(title), str(text)))
            return QMessageBox.StandardButton.Ok

        return handler

    def _question_handler(self, _parent, title: str, text: str, *args, **kwargs):
        del args, kwargs
        self._message_log.append(("question", str(title), str(text)))
        return QMessageBox.StandardButton.Yes

    def _run_scenario(self, scenario: dict) -> ScenarioResult:
        if self.window is None:
            raise RuntimeError("오프스크린 메인 창이 준비되지 않았습니다.")
        scenario_id = str(scenario["id"])
        title = str(scenario.get("title", scenario_id))
        viewport = scenario.get("viewport", [1280, 800])
        width, height = int(viewport[0]), int(viewport[1])
        self.window.resize(width, height)
        self._active_capture_widget = self.window
        self._post_capture = None
        self._current_checks = []
        started = time.perf_counter()
        status = "passed"
        error = ""
        screenshot_path = ""
        try:
            method = getattr(self, f"_scenario_{scenario_id}")
            method()
            self._flush()
            screenshot_path = self._capture(title, self._active_capture_widget)
        except Exception:
            status = "failed"
            error = traceback.format_exc()
            try:
                screenshot_path = self._capture(
                    f"{title}-failed", self._active_capture_widget or self.window
                )
            except Exception:
                error += "\n스크린샷 저장도 실패했습니다:\n" + traceback.format_exc()
        finally:
            if self._post_capture is not None:
                self._post_capture()
                self._post_capture = None
            if self.pool is not None:
                self.pool.release_all()
            self._flush()
        duration_ms = round((time.perf_counter() - started) * 1000)
        return ScenarioResult(
            scenario_id=scenario_id,
            title=title,
            status=status,
            duration_ms=duration_ms,
            screenshot=screenshot_path,
            checks=list(self._current_checks),
            error=error,
        )

    def _run_layout_sweep(self) -> list[str]:
        if self.window is None:
            return []
        checks: list[str] = []
        for viewport in self.config.get("layout_sweep_viewports", []):
            width, height = int(viewport[0]), int(viewport[1])
            self.window.resize(width, height)
            self._flush()
            for row in range(self.window.nav_list.count()):
                self._click_list_row(self.window.nav_list, row)
                current = self.window.tab_widget.currentWidget()
                if current is None or not current.isVisibleTo(self.window):
                    raise AssertionError(
                        f"{width}x{height}의 메인 화면 {row}가 보이지 않습니다."
                    )
                hint = current.findChild(QWidget, "stepHint")
                if hint is None or not hint.isVisibleTo(current):
                    raise AssertionError(
                        f"{width}x{height}의 메인 화면 {row} 작업 흐름 안내가 보이지 않습니다."
                    )
                checks.append(
                    f"{width}×{height} / {self.window.tab_widget.tabText(row)} 표시"
                )
        return checks

    def _scenario_main_navigation(self) -> None:
        window = self._require_window()
        nav = window.nav_list
        self._click_list_row(nav, 0)
        for expected_row in range(1, nav.count()):
            QTest.keyClick(nav, Qt.Key.Key_Down)
            self._flush()
            self._check(
                nav.currentRow() == expected_row,
                f"키보드 아래 이동: {window.tab_widget.tabText(expected_row)}",
            )
            self._check(
                window.tab_widget.currentIndex() == expected_row,
                "내비게이션 선택과 현재 화면 동기화",
            )
        self._check(nav.focusPolicy() == Qt.FocusPolicy.StrongFocus, "키보드 포커스")
        self._check(nav.accessibleName() == "주요 화면", "내비게이션 접근성 이름")

    def _scenario_interface_refresh(self) -> None:
        window = self._require_window()
        self._navigate_main(0)
        tab = window.interface_tab
        self._click(tab.refresh_button)
        self._check(not tab.refresh_button.isEnabled(), "조회 중 새로고침 비활성")
        self._check(tab.loading_bar.isVisibleTo(tab), "조회 중 진행 표시")
        self._release_next()
        self._check(tab.adapter_table.rowCount() == 1, "인터페이스 1개 표시")
        self._check(
            tab.selected_interface_label.text() == "Ethernet QA",
            "선택 인터페이스 폼 반영",
        )
        self._check(not tab.apply_button.isEnabled(), "일반 권한 적용 차단")
        self._check(tab.save_current_button.isEnabled(), "읽기 결과 프로파일 저장 가능")

    def _scenario_quick_subnet(self) -> None:
        window = self._require_window()
        self._navigate_main(1)
        tab = window.diagnostics_tab
        self._paste(tab.quick_target_edit, "192.168.10.42/24")
        self._click(tab.quick_subnet_button)
        self._check(tab._current_tool_key() == "subnet", "서브넷 계산기로 이동")
        self._check(
            tab.subnet_calc_summary_labels["network_address"].text()
            == "192.168.10.0",
            "네트워크 주소 계산",
        )
        self._check(
            tab.subnet_calc_detail_table.rowCount() >= 8,
            "서브넷 상세 결과 표시",
        )
        widths = [button.width() for button in tab.quick_action_buttons]
        self._check(max(widths) - min(widths) <= 1, "빠른 진단 버튼 동일 폭")

    def _scenario_multi_ping(self) -> None:
        window = self._require_window()
        self._navigate_main(1)
        tab = window.diagnostics_tab
        tab.select_diagnostic_tab("ping")
        self._paste(
            tab.ping_targets_edit,
            "Gateway,192.0.2.1\nDNS,198.51.100.53",
        )
        self._click(tab.ping_start_button)
        self._check(not tab.ping_start_button.isEnabled(), "Ping 실행 중 시작 차단")
        self._check(tab.ping_cancel_button.isEnabled(), "Ping 실행 중 중지 활성")
        self._release_next()
        targets = {
            tab.ping_table.item(row, 1).text()
            for row in range(tab.ping_table.rowCount())
        }
        self._check(
            targets == {"192.0.2.1", "198.51.100.53"},
            "요청한 Ping 대상 2개 모두 표시",
        )
        self._check(len(tab.ping_results) == 2, "Ping 최종 결과 2개 보존")
        self._check(tab.ping_start_button.isEnabled(), "Ping 완료 후 다시 실행 가능")
        self._check(not tab.ping_cancel_button.isEnabled(), "Ping 완료 후 중지 비활성")

    def _scenario_multi_tcp(self) -> None:
        window = self._require_window()
        self._navigate_main(1)
        tab = window.diagnostics_tab
        tab.select_diagnostic_tab("tcp")
        self._paste(
            tab.tcp_targets_edit,
            "Web-A,192.0.2.10\nWeb-B,192.0.2.11",
        )
        self._paste(tab.tcp_ports_edit, "22,443")
        self._click(tab.tcp_start_button)
        self._check(not tab.tcp_start_button.isEnabled(), "TCPing 실행 중 시작 차단")
        self._check(tab.tcp_cancel_button.isEnabled(), "TCPing 실행 중 중지 활성")
        self._release_next()
        endpoints = {
            (
                tab.tcp_table.item(row, 1).text(),
                int(tab.tcp_table.item(row, 2).text()),
            )
            for row in range(tab.tcp_table.rowCount())
        }
        self._check(
            endpoints
            == {
                ("192.0.2.10", 22),
                ("192.0.2.10", 443),
                ("192.0.2.11", 22),
                ("192.0.2.11", 443),
            },
            "2개 대상 × 2개 포트 결과 4개",
        )
        self._check(len(tab.tcp_results) == 4, "TCPing 최종 결과 4개 보존")

    def _scenario_dns_and_commands(self) -> None:
        window = self._require_window()
        self._navigate_main(1)
        tab = window.diagnostics_tab
        self._paste(tab.quick_target_edit, "example.com")
        self._click(tab.quick_dns_button)
        self._check(not tab.dns_run_button.isEnabled(), "DNS 조회 중 실행 차단")
        self._release_next()
        self._check("203.0.113.10" in tab.dns_output.toPlainText(), "DNS 결과 표시")

        expected_outputs = (
            (tab.quick_ipconfig_button, "192.168.10.42"),
            (tab.quick_route_button, "0.0.0.0"),
            (tab.quick_arp_table_button, "00-11-22-33-44-55"),
        )
        for button, expected in expected_outputs:
            self._click(button)
            self._release_next()
            self._check(expected in tab.tools_output.toPlainText(), f"명령 출력: {expected}")

    def _scenario_oui_lookup(self) -> None:
        window = self._require_window()
        self._navigate_main(1)
        tab = window.diagnostics_tab
        tab.select_diagnostic_tab("oui")
        self._paste(
            tab.oui_mac_edit,
            "Core,00:11:22:33:44:55\nAP,66-77-88-99-AA-BB",
        )
        self._click(tab.oui_lookup_button)
        self._check(tab.oui_table.rowCount() == 2, "OUI 입력 2개 결과 2개")
        vendors = {
            tab.oui_table.item(row, 3).text()
            for row in range(tab.oui_table.rowCount())
        }
        self._check(
            vendors == {"QA Network Devices", "QA Wireless Labs"},
            "OUI 제조사 매핑",
        )

    def _scenario_file_transfer_routing(self) -> None:
        window = self._require_window()
        self._navigate_main(1)
        tab = window.diagnostics_tab
        self._paste(tab.quick_target_edit, "192.0.2.20")
        self._click(tab.quick_transfer_button)
        self._check(tab._current_tool_key() == "transfer", "파일 전송 화면 이동")
        for role in range(2):
            tab.file_transfer_role_combo.setCurrentIndex(role)
            self._flush()
            for mode in range(3):
                tab.file_transfer_mode_combo.setCurrentIndex(mode)
                self._flush()
                expected_page = role * 3 + mode
                self._check(
                    tab.file_transfer_page_stack.currentIndex() == expected_page,
                    f"파일 전송 역할 {role}, 방식 {mode} 페이지",
                )
                self._check(
                    tab.file_transfer_page_stack.currentWidget().isVisibleTo(tab),
                    "현재 파일 전송 페이지 표시",
                )
        self._check(
            "TFTP 서버" in tab.file_transfer_hint_label.text(),
            "선택 상태 설명 갱신",
        )

    def _scenario_wireless_scan_filter(self) -> None:
        window = self._require_window()
        self._navigate_main(2)
        tab = window.wireless_tab
        self._click(tab.refresh_button)
        self._check(not tab.refresh_button.isEnabled(), "Wi-Fi 조회 중 새로고침 비활성")
        self._release_next()
        self._check(tab.info_labels["ssid"].text() == "QA-Lab-5G", "현재 SSID 표시")
        self._check(tab.refresh_button.isEnabled(), "Wi-Fi 조회 완료 후 새로고침 활성")

        self._click(tab.nearby_refresh_button)
        self._check(not tab.nearby_refresh_button.isEnabled(), "AP 스캔 중 버튼 비활성")
        self._release_next()
        self._check(tab.nearby_table.rowCount() == 3, "주변 AP 3개 표시")
        self._paste(tab.nearby_search_edit, "QA")
        self._check(tab.nearby_table.rowCount() == 2, "검색 필터 적용")
        index = tab.nearby_band_filter.findData("5")
        tab.nearby_band_filter.setCurrentIndex(index)
        self._flush()
        self._check(tab.nearby_table.rowCount() == 1, "5 GHz 필터 적용")

    def _scenario_settings_save(self) -> None:
        window = self._require_window()
        self._navigate_main(6)
        tab = window.settings_tab
        tab.show_section("program")
        self._check(
            "진단 기본값"
            not in {
                group.title()
                for group in tab.program_scroll.findChildren(QGroupBox)
            },
            "중복 진단 기본값 섹션 없음",
        )

        tab.show_section("storage")
        exports = self.runtime_root / "qa-exports"
        self._paste(tab.exports_dir_edit, str(exports))
        self._click(tab.save_paths_button)
        self._check(exports.is_dir(), "결과 폴더 생성")
        self._check(
            self.state.paths.exports_dir == exports.resolve(),
            "결과 폴더 즉시 적용",
        )
        opened: list[Path] = []
        with patch(
            "app.ui.tabs.settings_tab.open_in_explorer",
            lambda path: opened.append(Path(path)),
        ):
            self._click(tab.path_open_buttons["exports_dir"])
        self._check(
            opened == [exports.resolve()],
            "저장 위치 행에서 현재 결과 폴더 열기",
        )
        self._check(
            tab.path_change_buttons["exports_dir"].text() == "변경",
            "저장 위치 변경 용어 표시",
        )
        self._check("적용" in tab.path_status_label.text(), "저장 결과 상태 표시")
        tab.show_section("maintenance")
        self._check(
            tab.section_tabs.tabText(tab.section_tabs.currentIndex()) == "설정 관리",
            "유지 관리 대신 설정 관리 용어 표시",
        )
        self._check(
            tab.reset_all_settings_button.isVisible()
            and tab.reset_all_settings_button.isEnabled(),
            "모든 설정 초기화 동작 표시",
        )
        self._check(
            "내장 도구"
            not in {
                group.title()
                for group in tab.tools_scroll.findChildren(QGroupBox)
            },
            "불필요한 TCPing 내장 도구 설명 없음",
        )
        tab.show_section("storage")

    def _scenario_settings_oui_updates(self) -> None:
        window = self._require_window()
        self._navigate_main(6)
        tab = window.settings_tab
        tab.show_section("tools", "oui")

        self._click(tab.tool_refresh_button)
        self._check(
            not tab.oui_check_updates_button.isEnabled(),
            "도구 상태 조회 중 OUI 작업 중복 차단",
        )
        self._release_next()
        self._check("로컬 데이터 2건" in tab.oui_tool_status_label.text(), "OUI 로컬 건수 표시")
        self._check(
            "최신 여부 확인 권장" in tab.oui_tool_status_label.text(),
            "오래된 OUI 데이터 안내",
        )
        self._check(
            "IEEE Registration Authority" in tab.oui_tool_source_label.text(),
            "OUI 공식 원본 표시",
        )

        self._click(tab.oui_check_updates_button)
        self._check(
            not tab.oui_update_button.isEnabled(),
            "최신 여부 확인 중 업데이트 중복 차단",
        )
        self._release_next()
        self._check(
            "최신 IEEE OUI 데이터가 있습니다." == tab.oui_tool_result_label.text(),
            "OUI 업데이트 가능 상태 표시",
        )

        self._click(tab.oui_update_button)
        self._release_next()
        self._check(
            "최신 상태로 업데이트" in tab.oui_tool_result_label.text(),
            "OUI 업데이트 완료 표시",
        )
        self._check(
            "SHA-256 qaoui0000002" in tab.oui_tool_version_label.text(),
            "업데이트된 OUI 내용 버전 표시",
        )
        self._check(
            "최신 여부 확인 권장" not in tab.oui_tool_status_label.text(),
            "업데이트 후 오래됨 안내 제거",
        )

    def _scenario_settings_management(self) -> None:
        window = self._require_window()
        self._navigate_main(6)
        tab = window.settings_tab
        tab.show_section("maintenance")
        tab.maintenance_scroll.ensureWidgetVisible(tab.reset_all_settings_button)
        self._flush()

        self._check(
            tab.section_tabs.tabText(tab.section_tabs.currentIndex()) == "설정 관리",
            "설정 관리 탭 명칭",
        )
        self._check(tab.reset_all_settings_button.isVisible(), "모든 설정 초기화 버튼 표시")
        self._check(
            tab.reset_all_settings_button.accessibleName() == "모든 사용자 설정 초기화",
            "설정 초기화 접근성 이름",
        )
        reset_group = tab.reset_all_settings_button.parentWidget()
        reset_text = " ".join(
            label.text() for label in reset_group.findChildren(QLabel)
        )
        self._check("로그" in reset_text and "삭제하지 않습니다" in reset_text, "보존 범위 안내")
        self._check(
            tab.maintenance_scroll.horizontalScrollBar().maximum() == 0,
            "설정 관리 가로 잘림 없음",
        )

    def _scenario_ai_tool_ordering(self) -> None:
        window = self._require_window()
        self._navigate_main(5)
        tab = window.ai_chat_tab
        tab.ai_chat_tabs.setCurrentWidget(tab.chat_page)
        tab._messages.clear()
        tab._render_transcript()
        tab._confirm_netops_chat_action = lambda _action: True
        tab._run_netops_chat_action = lambda action, approved, cancel_event: (
            "NetOps Suite tool result\n"
            f"Ping targets: {', '.join(action.targets)}\n"
            "8.8.8.8: 정상, 손실 0%\n"
            "1.1.1.1: 정상, 손실 0%"
        )

        with patch.object(
            ai_chat_module,
            "inspect_provider",
            lambda _config: SimpleNamespace(
                installed=False, detail="QA에서는 외부 AI CLI를 실행하지 않습니다."
            ),
        ):
            self._paste(tab.prompt_edit, "ping 8.8.8.8 1.1.1.1")
            self._click(tab.send_button)
            self._check(self.pool is not None and len(self.pool.pending) == 1, "AI 도구 작업 대기")
            self._check(
                [item["title"] for item in tab._messages[:2]]
                == ["사용자", "시스템"],
                "사용자 메시지가 도구 안내보다 먼저 표시",
            )
            self._check(bool(tab._working_status_text), "AI 작업 중 표시")
            self._check(not tab.send_button.isEnabled(), "AI 작업 중 보내기 비활성")
            self._check(tab.stop_button.isEnabled(), "AI 작업 중 중지 활성")
            self._release_next()

        titles = [item["title"] for item in tab._messages]
        self._check(titles[:3] == ["사용자", "시스템", "NetOps"], "대화/도구 결과 순서")
        netops_body = next(
            item["body"] for item in tab._messages if item["title"] == "NetOps"
        )
        self._check(
            "8.8.8.8" in netops_body and "1.1.1.1" in netops_body,
            "AI 도구 결과에 요청한 대상 2개 포함",
        )
        self._check(not tab._working_status_text, "AI 도구 완료 후 작업 표시 제거")

    def _scenario_ai_image_paste(self) -> None:
        window = self._require_window()
        self._navigate_main(5)
        tab = window.ai_chat_tab
        tab.ai_chat_tabs.setCurrentWidget(tab.chat_page)
        self._paste_clipboard_image(tab)
        self._check(len(tab._attachments) == 1, "클립보드 이미지 첨부")
        card = tab.attachment_list.itemWidget(tab.attachment_list.item(0))
        remove_button = card.findChild(QToolButton, "attachmentRemoveButton")
        self._check(remove_button is not None, "첨부 카드 제거 버튼")
        button_center = remove_button.mapTo(card, remove_button.rect().center())
        self._check(card.rect().contains(button_center), "제거 버튼이 카드 경계 안에 표시")
        self._click(remove_button)
        self._check(not tab._attachments, "첨부 카드 X로 제거")
        self._paste_clipboard_image(tab)
        self._check(len(tab._attachments) == 1, "제거 후 이미지 재첨부")

    def _scenario_inspector_profile(self) -> None:
        window = self._require_window()
        self._navigate_main(3)
        dialog = InspectorProfileDialog(window.inspector_tab.service, window)
        dialog.resize(1120, 780)
        dialog.show()
        self._active_capture_widget = dialog
        self._post_capture = dialog.close
        self._flush()

        self._paste(dialog.vendor_edit, "QA-Vendor")
        self._paste(dialog.model_edit, "QA-9000")
        self._paste(dialog.os_edit, "QA-OS")
        self._paste(dialog.os_version_edit, "1.0")
        self._click_tab(dialog, 1)
        self._check(
            dialog.sample_output_edit.height()
            >= max(120, dialog.sample_output_edit.sizeHint().height()),
            "점검 출력 예시가 남는 세로 공간 사용",
        )
        self._click_tab(dialog, 2)
        if not dialog.backup_enabled_check.isChecked():
            self._click(dialog.backup_enabled_check)
        self._paste(dialog.backup_command_edit, "show running-config qa")
        self._check(
            dialog.backup_command_edit.isEnabled(),
            "백업 명령을 점검 명령과 분리해 편집",
        )

        self._click_tab(dialog, 4)
        refresh_button = self._find_button(dialog, "갱신")
        self._click(refresh_button)
        self._check(dialog.save_button.isEnabled(), "유효한 프로파일 저장 가능")
        yaml_text = dialog.yaml_preview.toPlainText()
        self._check("inspection_commands:" in yaml_text, "점검 명령 YAML 생성")
        self._check("backup_commands:" in yaml_text, "백업 명령 YAML 생성")
        self._click(dialog.save_button)
        self._check(
            dialog.service.custom_rules_path.is_file(),
            "격리된 설정 폴더에 프로파일 저장",
        )

    def _paste_clipboard_image(self, tab: AiChatTab) -> None:
        image = QImage(96, 64, QImage.Format.Format_ARGB32)
        image.fill(QColor("#5b8def"))
        self.app.clipboard().setImage(image)
        tab.prompt_edit.setFocus()
        QTest.keyClick(
            tab.prompt_edit,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier,
        )
        self._flush()

    def _click_tab(self, dialog: InspectorProfileDialog, index: int) -> None:
        rect = dialog.tabs.tabBar().tabRect(index)
        QTest.mouseClick(
            dialog.tabs.tabBar(),
            Qt.MouseButton.LeftButton,
            pos=rect.center(),
        )
        self._flush()
        self._check(dialog.tabs.currentIndex() == index, f"프로파일 탭 {index} 이동")

    @staticmethod
    def _find_button(parent: QWidget, text: str) -> QPushButton:
        for button in parent.findChildren(QPushButton):
            if button.text() == text:
                return button
        raise AssertionError(f"버튼을 찾지 못했습니다: {text}")

    def _navigate_main(self, row: int) -> None:
        window = self._require_window()
        self._click_list_row(window.nav_list, row)
        self._check(window.tab_widget.currentIndex() == row, f"메인 화면 {row} 이동")

    def _click_list_row(self, widget: QListWidget, row: int) -> None:
        item = widget.item(row)
        if item is None:
            raise AssertionError(f"목록 행이 없습니다: {row}")
        rect = widget.visualItemRect(item)
        QTest.mouseClick(
            widget.viewport(),
            Qt.MouseButton.LeftButton,
            pos=rect.center(),
        )
        self._flush()

    def _click(self, widget: QWidget) -> None:
        if not widget.isEnabled():
            raise AssertionError(
                f"비활성 컨트롤을 클릭할 수 없습니다: {widget.objectName() or type(widget).__name__}"
            )
        QTest.mouseClick(
            widget,
            Qt.MouseButton.LeftButton,
            pos=widget.rect().center(),
        )
        self._flush()

    def _paste(self, widget: QWidget, text: str) -> None:
        self.app.clipboard().setText(text)
        widget.setFocus()
        QTest.keyClick(
            widget,
            Qt.Key.Key_A,
            Qt.KeyboardModifier.ControlModifier,
        )
        QTest.keyClick(
            widget,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier,
        )
        self._flush()
        if isinstance(widget, QLineEdit):
            actual = widget.text()
        elif isinstance(widget, QPlainTextEdit):
            actual = widget.toPlainText()
        else:
            actual = getattr(widget, "text", lambda: "")()
        if actual != text:
            raise AssertionError(
                f"붙여넣기 결과가 다릅니다: expected={text!r}, actual={actual!r}"
            )

    def _release_next(self) -> None:
        if self.pool is None:
            raise RuntimeError("제어형 작업 큐가 준비되지 않았습니다.")
        self.pool.release_next()
        self._flush()

    def _flush(self) -> None:
        if self.app is None:
            return
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        delay = int(self.config.get("capture_delay_ms", 20) or 20)
        if delay > 0:
            QTest.qWait(min(delay, 100))
        self.app.processEvents()

    def _capture(self, title: str, widget: QWidget) -> str:
        self._flush()
        pixmap = widget.grab()
        if pixmap.isNull() or pixmap.width() < 100 or pixmap.height() < 100:
            raise AssertionError(f"유효하지 않은 캡처입니다: {title}")
        self._capture_index += 1
        slug = "".join(
            character.lower() if character.isalnum() else "-"
            for character in title
        ).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        path = self.output_dir / f"{self._capture_index:02d}-{slug[:60]}.png"
        if not pixmap.save(str(path), "PNG"):
            raise OSError(f"스크린샷을 저장하지 못했습니다: {path}")
        return path.name

    def _check(self, condition: bool, description: str) -> None:
        if not condition:
            raise AssertionError(description)
        self._current_checks.append(description)

    def _require_window(self) -> MainWindow:
        if self.window is None:
            raise RuntimeError("메인 창이 준비되지 않았습니다.")
        return self.window

    def _write_report(self, report: OffscreenQaReport) -> None:
        payload = {
            "application": self.config.get("application", "NetOps Suite"),
            "config": str(self.config_path),
            "started_at": report.started_at,
            "finished_at": report.finished_at,
            "ok": report.ok,
            "summary": report.summary_text(),
            "runtime_root": report.runtime_root,
            "layout_checks": report.layout_checks,
            "results": [asdict(item) for item in report.results],
            "messages": [
                {"kind": kind, "title": title, "text": text}
                for kind, title, text in self._message_log
            ],
        }
        report.json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        lines = [
            "# NetOps Suite Qt 오프스크린 QA",
            "",
            f"- 실행: {report.started_at} ~ {report.finished_at}",
            f"- 결과: **{'PASS' if report.ok else 'FAIL'}**",
            f"- 요약: `{report.summary_text()}`",
            "- 방식: Windows 화면 제어 없이 실제 Qt 위젯 클릭·키보드·붙여넣기·작업 완료",
            "",
            "## 시나리오",
            "",
        ]
        for index, result in enumerate(report.results, start=1):
            health = "양호" if result.ok else "실패"
            lines.extend(
                [
                    f"### {index}. {result.title} — {health}",
                    "",
                    f"- 시간: {result.duration_ms} ms",
                    *[f"- 확인: {check}" for check in result.checks],
                ]
            )
            if result.error:
                lines.extend(
                    [
                        "- 오류:",
                        "",
                        "```text",
                        result.error.rstrip(),
                        "```",
                    ]
                )
            if result.screenshot:
                lines.extend(
                    [
                        "",
                        f"![{result.title}]({result.screenshot})",
                    ]
                )
            lines.append("")
        lines.extend(
            [
                "## 레이아웃 스윕",
                "",
                *[f"- {check}" for check in report.layout_checks],
                "",
                "## 증거 한계",
                "",
                "- 외부 네트워크, 운영 장비, AI CLI, 파일 전송 서버는 결정론적 테스트 대역으로 교체했습니다.",
                "- 화면 캡처와 Qt 속성은 확인했지만 실제 스크린 리더 인증을 대신하지 않습니다.",
                "- 동시성 경쟁은 별도 실제 QThreadPool 회귀 테스트에서 검증합니다.",
                "",
            ]
        )
        report.markdown_path.write_text("\n".join(lines), encoding="utf-8")
