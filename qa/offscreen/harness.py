from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QFont, QFontDatabase, QImage, QPalette
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
    QScrollArea,
    QStyle,
    QStyleOptionGroupBox,
    QWidget,
)

from app.app_state import AppState
from app.guides import GuideCatalog
from app.main_window import MainWindow
from app.ui.common.theme import apply_app_theme
from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog
from netops_suite.modules.config_builder.switch_configurator.profile_builder_dialog import (
    ProfileBuilderDialog,
)
from qa.offscreen.fakes import (
    ControlledThreadPool,
    DeterministicWirelessService,
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
        self._original_palette: QPalette | None = None
        self._original_style_name = ""
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
        self._original_palette = QPalette(self.app.palette())
        self._original_style_name = self.app.style().name()
        self._original_malgun_substitutions = list(
            QFont.substitutes("Malgun Gothic")
        )
        self._install_offscreen_fonts()
        # Same style, palette and sheet as main.py so captures match the real app.
        apply_app_theme(self.app)

        self._patchers = [
            patch.object(
                GuideCatalog,
                "load",
                classmethod(lambda cls, *_args, **_kwargs: cls._load_manifest(
                    self.project_root / "docs" / "guide_manifest.json",
                    project_root=self.project_root,
                )),
            ),
            patch(
                "netops_suite.modules.config_builder.switch_configurator."
                "desktop_impl.APP_STATE_PATH",
                self.runtime_root / "config_builder" / ".desktop_state.json",
            ),
            patch(
                "app.app_state.configure_logging",
                lambda *_args, **_kwargs: self._qa_logger,
            ),
            patch(
                "app.app_state.shutdown_logging",
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
        # The user-flow contract exercises the normal-permission experience.
        # GitHub-hosted Windows runners may themselves be elevated, so never
        # inherit the host process token for this deterministic QA scenario.
        self.state.is_admin = False
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
                if self._original_style_name:
                    self.app.setStyle(self._original_style_name)
                if self._original_palette is not None:
                    self.app.setPalette(self._original_palette)
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
        known_scenarios = {
            name.removeprefix("_scenario_")
            for name in dir(self)
            if name.startswith("_scenario_")
        }
        known_capture_handlers = {
            name.removeprefix("_capture_handler_")
            for name in dir(self)
            if name.startswith("_capture_handler_")
        }
        configured: list[str] = []
        unknown: list[str] = []
        for index, item in enumerate(scenarios):
            if not isinstance(item, dict):
                raise ValueError(f"오프스크린 QA 시나리오 {index}가 객체가 아닙니다.")
            scenario_id = str(item.get("id", "")).strip()
            if not scenario_id:
                raise ValueError(f"오프스크린 QA 시나리오 {index}의 ID가 비어 있습니다.")
            configured.append(scenario_id)
            capture_handler = str(item.get("capture_handler", "")).strip()
            if capture_handler:
                if capture_handler not in known_capture_handlers:
                    unknown.append(f"{scenario_id} (handler={capture_handler})")
            elif scenario_id not in known_scenarios:
                unknown.append(scenario_id)
            viewport = item.get("viewport", [1280, 800])
            if (
                not isinstance(viewport, list)
                or len(viewport) != 2
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value <= 0
                    for value in viewport
                )
            ):
                raise ValueError(
                    f"시나리오 {scenario_id}의 viewport는 양의 정수 2개여야 합니다."
                )
        if unknown:
            raise ValueError(f"알 수 없는 오프스크린 QA 시나리오: {unknown}")
        if len(configured) != len(set(configured)):
            raise ValueError("오프스크린 QA 시나리오 ID가 중복되었습니다.")
        guide_assets = [
            str(item.get("guide_asset", "")).strip()
            for item in scenarios
            if str(item.get("guide_asset", "")).strip()
        ]
        if len(guide_assets) != len(set(guide_assets)):
            raise ValueError("가이드 이미지 파일명이 중복되었습니다.")
        invalid_assets = [
            asset
            for asset in guide_assets
            if Path(asset).name != asset or Path(asset).suffix.casefold() != ".png"
        ]
        if invalid_assets:
            raise ValueError(
                f"가이드 이미지는 폴더 없는 PNG 파일명이어야 합니다: {invalid_assets}"
            )
        for item in scenarios:
            if not str(item.get("guide_asset", "")).strip():
                continue
            scenario_id = str(item["id"])
            if not str(item.get("capture_handler", "")).strip():
                raise ValueError(
                    f"가이드 이미지 시나리오에는 capture_handler가 필요합니다: {scenario_id}"
                )
            for key in ("expected_object_names", "source_paths"):
                values = item.get(key)
                if (
                    not isinstance(values, list)
                    or not values
                    or any(not isinstance(value, str) or not value.strip() for value in values)
                    or len(values) != len(set(values))
                ):
                    raise ValueError(
                        f"가이드 이미지 시나리오 {scenario_id}의 {key}는 "
                        "중복 없는 문자열 배열이어야 합니다."
                    )

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
            capture_handler = str(scenario.get("capture_handler", "")).strip()
            if capture_handler:
                method = getattr(self, f"_capture_handler_{capture_handler}")
                method(scenario)
            else:
                method = getattr(self, f"_scenario_{scenario_id}")
                method()
            self._flush()
            if (
                scenario.get("capture_target") == "page"
                and self._active_capture_widget is self.window
            ):
                # Guide pictures show only the work area so text stays readable.
                self._active_capture_widget = self.window.tab_widget
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
            for row in range(len(self.window._MAIN_PAGE_KEYS)):
                self._navigate_main(row)
                current = self.window.tab_widget.currentWidget()
                if current is None or not current.isVisibleTo(self.window):
                    raise AssertionError(
                        f"{width}x{height}의 메인 화면 {row}가 보이지 않습니다."
                    )
                hint = current.findChild(QWidget, "pageHeader") or current.findChild(QWidget, "stepHint")
                if hint is None or not hint.isVisibleTo(current):
                    raise AssertionError(
                        f"{width}x{height}의 메인 화면 {row} 작업 흐름 안내가 보이지 않습니다."
                    )
                for control in self._primary_controls(self.window._MAIN_PAGE_KEYS[row]):
                    self._assert_control_accessible(control)
                checks.append(
                    f"{width}×{height} / {self.window.tab_widget.tabText(self.window.tab_widget.currentIndex())} 주요 조작부 접근 및 표시 영역"
                )
        return checks

    def _primary_controls(self, page_key: str) -> tuple[QWidget, ...]:
        window = self._require_window()
        controls = {
            "interface": (window.interface_tab.refresh_button, window.interface_tab.apply_button),
            "diagnostics": (window.diagnostics_tab.diagnostic_tool_combo,),
            "wireless": (window.wireless_tab.refresh_button, window.wireless_tab.nearby_refresh_button),
            "inspector": (window.inspector_tab.inventory_button, window.inspector_tab.profile_editor_button, window.inspector_tab.run_button),
            "config_builder": (window.config_builder_tab.profile_management_button, window.config_builder_tab.full_editor_button, window.config_builder_tab.builder_widget.sample_start_button, window.config_builder_tab.builder_widget.save_cli_button),
            "settings": (window.settings_button,),
            "home": tuple(window.home_page.task_buttons.values()),
            "transfer": (window.diagnostics_tab.file_transfer_role_combo, window.diagnostics_tab.file_transfer_mode_combo),
        }
        return controls[page_key]

    def _capture_handler_card_borders(self, scenario: dict) -> None:
        window = self._require_window()
        count = 0
        for row in range(len(window._MAIN_PAGE_KEYS)):
            self._navigate_main(row)
            current = window.tab_widget.currentWidget()
            for group in current.findChildren(QGroupBox):
                if not group.title() or not group.isVisibleTo(current):
                    continue
                option = QStyleOptionGroupBox()
                group.initStyleOption(option)
                title = group.style().subControlRect(
                    QStyle.ComplexControl.CC_GroupBox, option,
                    QStyle.SubControl.SC_GroupBoxLabel, group,
                )
                self._check(title.top() >= 8 and group.rect().contains(title), f"카드 제목이 테두리 안쪽에 표시: {group.title()}")
                count += 1
        self._check(count >= 10, "주요 작업 화면의 카드 제목 검사")
        window.navigate_to("inspector")
        self._flush()

    def _capture_handler_wireless_signal(self, scenario: dict) -> None:
        window = self._require_window()
        window.navigate_to("wireless")
        tab = window.wireless_tab
        service = DeterministicWirelessService()
        info = replace(
            service.get_wireless_info(), signal_percent=int(scenario["signal_percent"]),
            rssi=str(scenario["rssi"]),
        )
        tab._update_wireless_view(info)
        tab._update_nearby_access_points(service.scan_nearby_access_points())
        tab.status_details_section.setExpanded(False)
        tab.change_log_section.setExpanded(False)
        tab.nearby_options_section.setExpanded(False)
        tab.wireless_scroll_area.verticalScrollBar().setValue(0)
        self._flush()
        label = tab.info_labels["signal"]
        self._check(label.palette().color(QPalette.ColorRole.WindowText).name() == scenario["expected_color"], "현재 신호의 실제 표시 색상")
        self._check(scenario["expected_strength"] in label.text(), "색상과 함께 신호 상태 문구 표시")
        self._assert_readable_card(tab.status_cards["signal"], "현재 Wi-Fi 신호")
        self._post_capture = lambda: tab._update_wireless_view(service.get_wireless_info())

    def _scenario_main_navigation(self) -> None:
        window = self._require_window()
        nav = window.nav_list
        self._click_list_row(nav, 0)
        for expected_row in range(1, nav.count()):
            QTest.keyClick(nav, Qt.Key.Key_Down)
            self._flush()
            self._check(
                nav.currentRow() == expected_row,
                f"키보드 아래 이동: {nav.item(expected_row).text()}",
            )
            self._check(
                window.tab_widget.currentWidget().property("mainPageKey") == nav.item(expected_row).data(Qt.ItemDataRole.UserRole),
                "내비게이션 선택과 현재 화면 동기화",
            )
        self._check(nav.focusPolicy() == Qt.FocusPolicy.StrongFocus, "키보드 포커스")
        self._check(nav.accessibleName() == "주요 화면", "내비게이션 접근성 이름")

    def _scenario_guide_interface_overview(self) -> None:
        self._show_guide_overview_page(0, "내 PC 네트워크")

    def _scenario_guide_diagnostics_overview(self) -> None:
        self._show_guide_overview_page(1, "연결 진단")

    def _scenario_guide_wireless_overview(self) -> None:
        self._show_guide_overview_page(2, "Wi-Fi 확인")

    def _scenario_guide_inspector_overview(self) -> None:
        self._show_guide_overview_page(3, "장비 점검·백업")

    def _scenario_guide_config_builder_overview(self) -> None:
        self._show_guide_overview_page(4, "설정 명령 만들기")

    def _scenario_guide_settings_overview(self) -> None:
        self._show_guide_overview_page(5, "설정")

    def _capture_handler_guide_overview(self, scenario: dict) -> None:
        """Show and verify a top-level page declared by a guide scenario."""

        window = self._require_window()
        scenario_id = str(scenario["id"])
        page_index = scenario.get("page_index")
        expected_title = str(scenario.get("expected_title", "")).strip()
        if isinstance(page_index, bool) or not isinstance(page_index, int):
            raise AssertionError(
                f"{scenario_id}: page_index는 정수여야 합니다."
            )
        if page_index < 0 or page_index >= window.tab_widget.count():
            raise AssertionError(
                f"{scenario_id}: page_index가 화면 범위를 벗어났습니다: {page_index}"
            )
        if not expected_title:
            raise AssertionError(f"{scenario_id}: expected_title이 비어 있습니다.")

        self._navigate_main(page_index)
        current = window.tab_widget.currentWidget()
        self._check(
            window.tab_widget.tabText(page_index) == expected_title,
            f"가이드 화면 제목: {expected_title}",
        )
        self._check(
            current is not None and current.isVisibleTo(window),
            f"가이드 화면 표시: {expected_title}",
        )

        viewport = scenario["viewport"]
        self._check(
            window.width() == int(viewport[0]) and window.height() == int(viewport[1]),
            f"가이드 캡처 뷰포트: {viewport[0]}×{viewport[1]}",
        )
        for object_name in scenario["expected_object_names"]:
            candidates = []
            if window.objectName() == object_name:
                candidates.append(window)
            candidates.extend(window.findChildren(QWidget, object_name))
            self._check(
                any(candidate.isVisibleTo(window) for candidate in candidates),
                f"가이드 캡처 objectName 표시: {object_name}",
            )

    def _capture_handler_inspector_results(self, scenario: dict) -> None:
        from datetime import datetime

        self._capture_handler_guide_overview(scenario)
        tab = self._require_window().inspector_tab
        self._check(tab.open_artifacts_button.isEnabled(), "실행 전 폴더 열기 활성")
        with patch("app.ui.tabs.inspector_tab.os.startfile") as opened:
            self._click(tab.open_artifacts_button)
            self._check(opened.call_count == 1, "실행 전 작업 폴더 열기")
        with patch("app.ui.tabs.inspector_tab.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 22, 14, 5, 9)
            tab._handle_progress({"type": "info", "message": "결과 폴더를 확인했습니다."})
        tab._set_result_log_visible(True)
        self._check("[14:05:09] [info]" in tab.log_view.toPlainText(), "결과 로그 시각 표시")

    def _capture_handler_inspector_expanded(self, scenario: dict) -> None:
        self._navigate_main(3)
        tab = self._require_window().inspector_tab
        sections = (tab.inventory_format_section, tab.profile_section)
        for section in sections:
            section.setExpanded(False)

        def reset_sections() -> None:
            for section in sections:
                section.setExpanded(False)
            tab.top_scroll.verticalScrollBar().setValue(0)

        self._post_capture = reset_sections
        self._check(tab.profile_editor_button.text() == "프로파일 만들기·관리", "프로파일 관리 진입점 명칭")
        self._assert_control_accessible(tab.profile_editor_button)
        self._check(tab.supported_toggle_button is tab.profile_section.toggle_button, "지원 장비 목록 펼치기 한 단계")
        for cycle in range(3):
            for section in sections:
                self._click(section.toggle_button)
                self._check(section.isExpanded(), f"장비 작업 안내 {cycle + 1}회 펼치기")
            cards = tuple(tab.inventory_guide_steps.findChildren(QWidget, "inspectorInventoryGuideCard"))
            self._check(len(cards) == 3, "장비 목록 작성 안내 세 단계")
            for index, card in enumerate(cards):
                self._assert_readable_card(card, f"장비 목록 안내 {index + 1}")
            self._assert_non_overlapping(cards, tab.inventory_guide_steps, "장비 목록 안내 카드")
            example = tab.inventory_example_table
            self._assert_viewport_height(example, 120, "장비 목록 입력 예시표")
            self._check(example.rowCount() == 6 and example.columnCount() == 2, "필수 열과 입력 예시 6행 2열")
            self._check(example.viewport().rect().contains(example.visualItemRect(example.item(5, 1))), "입력 예시표 마지막 행 전체 표시")
            self._assert_viewport_height(tab.supported_table, 100, "지원 장비 목록")
            for control in (tab.inventory_button, tab.sample_button, tab.validate_button, tab.run_button):
                self._assert_control_accessible(control)
            if cycle < 2:
                for section in reversed(sections):
                    self._click(section.toggle_button)
        self._check(tab.top_scroll.horizontalScrollBar().maximum() == 0, "장비 작업 안내 가로 잘림 없음")
        viewport = scenario["viewport"]
        self._check(self.window.width() == viewport[0] and self.window.height() == viewport[1], "장비 작업 안내 펼침 후 창 크기 유지")
        tab.top_scroll.verticalScrollBar().setValue(0)

    def _capture_handler_inspector_custom_commands(self, scenario: dict) -> None:
        import pandas as pd

        self._navigate_main(3)
        tab = self._require_window().inspector_tab
        if self.runtime_root is None:
            raise RuntimeError("오프스크린 런타임 폴더가 준비되지 않았습니다.")
        inventory_path = self.runtime_root / "qa_custom_command_inventory.xlsx"
        pd.DataFrame(
            [
                {"ip": "192.0.2.10", "vendor": "cisco", "os": "ios", "connection_type": "ssh",
                 "port": 22, "password": "CHANGE_ME_PASSWORD", "interface": "Gi1/0/1"},
                {"ip": "192.0.2.11", "vendor": "cisco", "os": "ios", "connection_type": "ssh",
                 "port": 22, "password": "CHANGE_ME_PASSWORD", "interface": "Gi1/0/2"},
            ]
        ).to_excel(inventory_path, index=False)

        def reset_custom_commands() -> None:
            tab.command_text_edit.clear()
            tab.command_inline_radio.setChecked(True)
            tab.inventory_path_edit.clear()
            tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("inspection"))
            tab.top_scroll.verticalScrollBar().setValue(0)

        self._post_capture = reset_custom_commands
        tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("custom_commands"))
        self._flush()
        self._check(tab.command_inline_radio.isChecked(), "사용자 명령 기본 입력 방식은 직접 입력")
        self._check(tab.command_file_row.isHidden(), "직접 입력에서는 명령 파일 선택 숨김")
        self._click(tab.command_file_radio)
        self._check(not tab.command_file_row.isHidden() and tab.command_text_edit.isHidden(), "명령 파일 방식 전환")
        self._click(tab.command_inline_radio)
        self._check(not tab.command_text_edit.isHidden(), "직접 입력 방식 복귀")
        self._paste(tab.command_text_edit, "show interface {{ interface")
        QTest.qWait(400)
        self._check("닫는" in tab.command_check_label.text(), "입력 즉시 변수 문법 오류 표시")
        self._paste(tab.command_text_edit, "show version\nshow interface {{ interface }}")
        QTest.qWait(400)
        self._check(
            "명령 2개 · 사용 변수 interface" in tab.command_check_label.text(),
            "입력 즉시 명령 수와 변수 표시",
        )
        self._paste(tab.inventory_path_edit, str(inventory_path))
        self._click(tab.validate_button)
        self._check(tab._inventory_validated, "직접 입력 명령과 장비 목록 검증")
        self._check(not tab.command_preview.isHidden(), "검증 후 실행 미리보기 표시")
        self._check(
            tab.command_preview_view.toPlainText() == "show version\nshow interface Gi1/0/1",
            "첫 장비 기준 변수 치환 미리보기",
        )
        self._check("192.0.2.10" in tab.command_preview_title.text(), "미리보기 대상 장비 표시")
        for control in (
            tab.command_inline_radio,
            tab.command_file_radio,
            tab.command_text_edit,
            tab.command_preview_view,
            tab.validate_button,
            tab.run_button,
        ):
            self._assert_control_accessible(control)
        self._check(tab.top_scroll.horizontalScrollBar().maximum() == 0, "사용자 명령 영역 가로 잘림 없음")
        viewport = scenario["viewport"]
        self._check(
            self.window.width() == viewport[0] and self.window.height() == viewport[1],
            "사용자 명령 검증 후 창 크기 유지",
        )
        for object_name in scenario.get("expected_object_names", []):
            candidates = self.window.findChildren(QWidget, object_name)
            self._check(
                any(candidate.isVisibleTo(self.window) for candidate in candidates),
                f"가이드 캡처 objectName 표시: {object_name}",
            )
        tab.top_scroll.verticalScrollBar().setValue(0)
        self._flush()

    def _capture_handler_inspector_profile_trial(self, scenario: dict) -> None:
        from netops_suite.modules.inspector.service import ProfileTrialResult

        window = self._require_window()
        self._navigate_main(3)
        dialog = InspectorProfileDialog(window.inspector_tab.service, window)
        width, height = scenario["viewport"]
        dialog.resize(width, height)
        dialog.show()
        self._active_capture_widget = dialog

        def close_dialog() -> None:
            dialog._dirty = False
            dialog.close()

        self._post_capture = close_dialog
        self._flush()
        self._paste(dialog.vendor_edit, "Cisco")
        self._paste(dialog.os_edit, "IOS-XE")
        dialog.tabs.setCurrentWidget(dialog.trial_tab)
        self._flush()
        self._paste(dialog.trial_ip_edit, "192.0.2.10")
        self._paste(dialog.trial_username_edit, "admin")
        self._paste(dialog.trial_password_edit, "CHANGE_ME_PASSWORD")
        # The trial result is injected: offscreen QA never logs in to a device.
        dialog._show_trial_result(
            ProfileTrialResult(
                connection_type="ssh",
                probe_mode="legacy",
                probe_summary=(
                    "오래된 SSH 방식(키 교환, 호스트 키)만 지원하는 장비입니다. "
                    "구형 장비 호환 모드로 자동 전환해 접속합니다."
                ),
                probe_details=(
                    "SSH 버전 정보: SSH-2.0-OpenSSH_5.2\n"
                    "키 교환: diffie-hellman-group14-sha1, diffie-hellman-group1-sha1\n"
                    "호스트 키: ssh-rsa\n"
                    "암호화: aes128-cbc, 3des-cbc\n"
                    "무결성(MAC): hmac-sha1"
                ),
                outputs=[
                    {
                        "command": "show version",
                        "output": (
                            "Cisco IOS XE Software, Version 17.09.04\n"
                            "cisco C9300-24T processor\n"
                            "Processor board ID FOC1234ABCD"
                        ),
                    },
                    {"command": "show inventory", "output": 'NAME: "Chassis", DESCR: "C9300-24T"'},
                ],
                session_log_dir=str(self.runtime_root or ""),
            )
        )
        self._flush()
        self._check(
            dialog.state["commands"][0]["sample"].startswith("Cisco IOS XE Software"),
            "장비 시험 출력으로 출력 예시 채움",
        )
        self._check(
            "- OS버전: 17.09.04" in dialog.trial_result_view.toPlainText(),
            "장비 시험 결과에 Excel 컬럼 값 표시",
        )
        self._check(
            dialog.trial_password_edit.echoMode() == QLineEdit.EchoMode.Password,
            "장비 시험 비밀번호 가림",
        )
        for control in (
            dialog.trial_ip_edit,
            dialog.trial_probe_button,
            dialog.trial_run_button,
            dialog.trial_result_view,
        ):
            self._assert_control_accessible(control)
        for object_name in scenario.get("expected_object_names", []):
            candidates = dialog.findChildren(QWidget, object_name)
            self._check(
                any(candidate.isVisibleTo(dialog) for candidate in candidates),
                f"가이드 캡처 objectName 표시: {object_name}",
            )

    def _capture_handler_context_help(self, scenario: dict) -> None:
        window = self._require_window()
        window.navigate_to("diagnostics", "ping")
        self._flush()
        self._click(window.guide_button)
        self._check(window.help_dock.isVisible(), "현재 작업 도움말 표시")
        self._check(window.quick_help_panel.current_entry.id == "diagnostics.ping", "Ping 전용 도움말 연결")
        viewport = scenario["viewport"]
        self._check(window.width() == int(viewport[0]) and window.height() == int(viewport[1]), "가이드 도움말 캡처 크기 유지")
        for object_name in scenario["expected_object_names"]:
            candidates = window.findChildren(QWidget, object_name)
            self._check(
                any(candidate.isVisibleTo(window) for candidate in candidates),
                f"가이드 캡처 objectName 표시: {object_name}",
            )
        self._active_capture_widget = window
        self._post_capture = window.help_dock.hide

    def _scenario_home_tasks(self) -> None:
        window = self._require_window()
        for key, button in window.home_page.task_buttons.items():
            window.navigate_to("home")
            self._flush()
            self._click(button)
            self._check(window.tab_widget.currentWidget().property("mainPageKey") == key, f"시작 카드에서 {key} 직접 진입")
        window.navigate_to("home")
        self._flush()
        self._check(window._guide_dialog is None, "도움말 창 자동 표시 없음")

    def _scenario_diagnostic_input_error(self) -> None:
        window = self._require_window()
        window.navigate_to("diagnostics", "ping")
        tab = window.diagnostics_tab
        self._flush()
        self._paste(tab.ping_targets_edit, "")
        self._click(tab.ping_start_button)
        self._check(not tab.active_task_keys(), "빈 입력으로 작업을 시작하지 않음")
        self._check(tab.ping_start_button.isEnabled(), "오류 후 입력을 수정하여 재실행 가능")

    def _scenario_diagnostic_options(self) -> None:
        window = self._require_window()
        window.navigate_to("diagnostics", "ping")
        tab = window.diagnostics_tab
        self._flush()
        if not tab.ping_options_section.isExpanded():
            self._click(tab.ping_options_section.toggle_button)
        self._paste(tab.ping_count_edit, "8")
        self._click(tab.ping_options_section.toggle_button)
        self._check("변경된 옵션" in tab.ping_options_section.toggle_button.text(), "접힌 실행 옵션 변경수 표시")
        self._check(tab.ping_count_edit.text() == "8", "옵션을 접어도 값 보존")
        self._post_capture = lambda: tab.ping_count_edit.setText("")

    def _scenario_diagnostic_running(self) -> None:
        window = self._require_window()
        window.navigate_to("diagnostics", "ping")
        tab = window.diagnostics_tab
        self._flush()
        self._paste(tab.ping_targets_edit, "Gateway,192.0.2.1")
        self._click(tab.ping_start_button)
        window._update_navigation_activity()
        self._check("ping" in tab.active_task_keys(), "진행 중 작업 추적")
        self._check(tab.ping_cancel_button.isEnabled(), "실행 중 중지 접근 가능")
        self._check(not tab.ping_start_button.isEnabled(), "중복 실행 차단")

    def _scenario_context_help_compact(self) -> None:
        window = self._require_window()
        window.navigate_to("transfer")
        self._flush()
        self._click(window.guide_button)
        self._check(window.help_dock.isFloating(), "좁은 창에서는 비모달 분리 도움말")
        self._check(window.tab_widget.currentWidget().width() >= 720, "작업 영역 최소 폭 보존")
        self._active_capture_widget = window.help_dock
        self._post_capture = window.help_dock.hide

    def _capture_handler_workspace_state(self, scenario: dict) -> None:
        """Exercise common task states at every supported logical viewport."""
        from app.ui.common import set_inline_status

        window = self._require_window()
        window.help_dock.hide()
        window.navigate_to("diagnostics", "ping")
        tab = window.diagnostics_tab
        tab.ping_options_section.setExpanded(False)
        tab.ping_splitter.hide()
        tab.ping_empty_label.show()
        set_inline_status(tab.ping_input_error, "error", "")
        set_inline_status(tab.ping_status_label, "info", "")
        self._flush()
        state = str(scenario["workspace_state"])
        if state == "error":
            self._scenario_diagnostic_input_error()
            self._ensure_control_visible(tab.ping_input_error)
            self._check(bool(tab.ping_input_error.text()), "입력 옆 오류와 수정 안내 표시")
        elif state == "running":
            self._scenario_diagnostic_running()
            self._ensure_control_visible(tab.ping_cancel_button)
        elif state == "complete":
            self._scenario_multi_ping()
            self._ensure_control_visible(tab.ping_table)
            self._check("완료" in tab.ping_status_label.text(), "완료 요약과 결과 표시")
        elif state == "help":
            self._ensure_control_visible(tab.ping_targets_edit)
            tab.ping_targets_edit.setFocus()
            QTest.keyClick(tab.ping_targets_edit, Qt.Key.Key_F1)
            self._flush()
            self._check(window.help_dock.isVisible(), "F1으로 현재 작업 도움말 열기")
            self._check(window.quick_help_panel.current_entry.id == "diagnostics.ping", "현재 작업과 도움말 일치")
            self._check(window.tab_widget.currentWidget().width() >= 720, "도움말 표시 중 작업 영역 최소 폭 보존")
            if window.help_dock.isFloating():
                self._active_capture_widget = window.help_dock

            def close_help() -> None:
                window.help_dock.hide()
                self._flush()
                self._check(self.app.focusWidget() is tab.ping_targets_edit, "도움말 닫기 후 원래 입력으로 포커스 복원")

            self._post_capture = close_help
        elif state == "default":
            self._paste(tab.ping_targets_edit, "")
            self._ensure_control_visible(tab.ping_empty_label)
            self._check(not tab.active_task_keys(), "화면 진입만으로 실행하지 않음")
        else:
            raise AssertionError(f"알 수 없는 작업 상태: {state}")
        self._check(window.size().width() == int(scenario["viewport"][0]), "요청한 창 너비 유지")
        self._check(window.size().height() == int(scenario["viewport"][1]), "요청한 창 높이 유지")

    def _show_guide_overview_page(self, row: int, expected_title: str) -> None:
        window = self._require_window()
        self._navigate_main(row)
        current = window.tab_widget.currentWidget()
        self._check(
            window.tab_widget.tabText(row) == expected_title,
            f"가이드 화면 제목: {expected_title}",
        )
        self._check(
            current is not None and current.isVisibleTo(window),
            f"가이드 화면 표시: {expected_title}",
        )
        hint = (current.findChild(QWidget, "pageHeader") or current.findChild(QWidget, "stepHint")) if current is not None else None
        self._check(
            hint is not None and hint.isVisibleTo(current),
            f"가이드 작업 흐름 표시: {expected_title}",
        )

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
        tab.select_tool("subnet")
        self._flush()
        self._paste(tab.subnet_calc_ip_edit, "192.168.10.42")
        self._paste(tab.subnet_calc_prefix_edit, "24")
        self._click(tab.subnet_calc_button)
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
        self._check(not tab.quick_target_edit.isVisibleTo(window), "중복 대상 입력 비표시")

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
        tab.select_tool("dns")
        self._flush()
        self._paste(tab.dns_query_edit, "example.com")
        self._click(tab.dns_run_button)
        self._check(not tab.dns_run_button.isEnabled(), "DNS 조회 중 실행 차단")
        self._release_next()
        self._check("203.0.113.10" in tab.dns_output.toPlainText(), "DNS 결과 표시")

        expected_outputs = (
            ("ipconfig", "192.168.10.42"),
            ("route", "0.0.0.0"),
            ("arp", "00-11-22-33-44-55"),
        )
        tab.select_tool("commands")
        self._flush()
        for command, expected in expected_outputs:
            tab.command_tool_combo.setCurrentIndex(tab.command_tool_combo.findData(command))
            self._click(tab.command_run_button)
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
        window.navigate_to("transfer")
        self._flush()
        tab = window.diagnostics_tab
        self._check(window.tab_widget.currentWidget() is window.transfer_tab, "독립 파일 전송 화면 이동")
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
                    tab.file_transfer_page_stack.currentWidget().isVisibleTo(window.transfer_tab),
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
        if not tab.nearby_options_section.isExpanded():
            self._click(tab.nearby_options_section.toggle_button)
        index = tab.nearby_band_filter.findData("5")
        tab.nearby_band_filter.setCurrentIndex(index)
        self._flush()
        self._check(tab.nearby_table.rowCount() == 1, "5 GHz 필터 적용")

        def reset_filters() -> None:
            tab.nearby_search_edit.clear()
            tab.nearby_band_filter.setCurrentIndex(0)
            tab.nearby_options_section.setExpanded(False)

        self._post_capture = reset_filters

    def _capture_handler_wireless_expanded(self, scenario: dict) -> None:
        """Exercise the expanded workspace, including the content below the fold."""
        window = self._require_window()
        self._navigate_main(2)
        tab = window.wireless_tab
        sections = (tab.status_details_section, tab.change_log_section, tab.nearby_options_section)
        tab.nearby_search_edit.clear()
        tab.nearby_band_filter.setCurrentIndex(0)
        tab.nearby_security_filter.setCurrentIndex(0)
        tab.nearby_connected_only_check.setChecked(False)
        self._click(tab.refresh_button)
        self._release_next()
        self._click(tab.nearby_refresh_button)
        self._release_next()
        self._check(tab.nearby_table.rowCount() == 3, "확대 검사에 주변 AP 3개 준비")
        tab.change_log.clear()
        for index in range(12):
            tab.change_log.addItem(f"14:05:{index:02d} · QA-Lab-5G · 연결 유지 · 신호 {85 - index}%")

        def reset_workspace() -> None:
            for section in sections:
                section.setExpanded(False)
            tab.change_log.clear()
            tab.wireless_scroll_area.verticalScrollBar().setValue(0)

        self._post_capture = reset_workspace
        for section in sections:
            section.setExpanded(False)
        self._flush()
        expected_values = {key: label.text() for key, label in tab.info_labels.items()}
        for cycle in range(3):
            for section in sections:
                self._click(section.toggle_button)
                self._check(section.isExpanded(), f"Wi-Fi {cycle + 1}회 펼치기: {section.toggle_button.text()}")
            for key, card in tab.status_cards.items():
                self._assert_readable_card(card, f"Wi-Fi {key}")
            self._assert_non_overlapping(tuple(tab.status_cards.values()), tab.wireless_content, "Wi-Fi 상태 카드")
            for control in (
                tab.auto_refresh_check, tab.interval_spin,
                tab.nearby_search_edit, tab.nearby_band_filter,
                tab.nearby_security_filter, tab.nearby_sort_combo,
                tab.nearby_connected_only_check, tab.nearby_column_button,
                tab.nearby_auto_refresh_check, tab.nearby_interval_spin,
                tab.nearby_refresh_oui_button,
            ):
                self._assert_control_accessible(control)
            self._assert_viewport_height(tab.change_log, 120, "Wi-Fi 연결 변화 로그")
            tab.change_log.scrollToItem(tab.change_log.item(tab.change_log.count() - 1))
            self._flush()
            self._check(
                tab.change_log.viewport().rect().contains(tab.change_log.visualItemRect(tab.change_log.item(tab.change_log.count() - 1))),
                "Wi-Fi 로그 마지막 항목까지 스크롤 접근",
            )
            self._assert_viewport_height(tab.nearby_table, 180, "Wi-Fi 주변 AP 표")
            last_item = tab.nearby_table.item(tab.nearby_table.rowCount() - 1, tab.nearby_table.columnCount() - 1)
            tab.nearby_table.scrollToItem(last_item)
            self._flush()
            self._check(
                tab.nearby_table.viewport().rect().contains(tab.nearby_table.visualItemRect(last_item).center()),
                "Wi-Fi AP 표 마지막 행·열까지 스크롤 접근",
            )
            self._check(
                expected_values == {key: label.text() for key, label in tab.info_labels.items()},
                "Wi-Fi 반복 펼치기 후 현재 연결 값 보존",
            )
            if cycle < 2:
                for section in reversed(sections):
                    self._click(section.toggle_button)
                    self._check(not section.isExpanded(), f"Wi-Fi {cycle + 1}회 접기")

        self._check(tab.wireless_scroll_area.horizontalScrollBar().maximum() == 0, "Wi-Fi 작업영역 가로 잘림 없음")
        for object_name in scenario.get("expected_object_names", []):
            self._check(
                any(widget.isVisibleTo(window) for widget in window.findChildren(QWidget, object_name)),
                f"가이드 캡처 objectName 표시: {object_name}",
            )
        viewport = scenario["viewport"]
        self._check(window.size().width() == viewport[0] and window.size().height() == viewport[1], "Wi-Fi 펼침 후 창 크기 유지")
        tab.change_log.scrollToTop()
        tab.nearby_table.scrollToTop()
        tab.nearby_table.horizontalScrollBar().setValue(0)
        tab.wireless_scroll_area.verticalScrollBar().setValue(0)
        self._flush()

    def _scenario_settings_save(self) -> None:
        window = self._require_window()
        self._navigate_main(5)
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
        self._navigate_main(5)
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
        self._navigate_main(5)
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

    def _capture_handler_settings_storage(self, scenario: dict) -> None:
        self._capture_handler_guide_overview(scenario)
        tab = self._require_window().settings_tab
        tab.show_section("storage")
        self._check(tab.applied_paths_group.isHidden(), "중복 경로는 기본 접힘")
        self._click(tab.path_details_button)
        self._check(not tab.applied_paths_group.isHidden(), "실제 적용 경로 상세 보기")
        self._click(tab.path_details_button)
        self._check(len(tab.path_edits) == 3, "저장 위치 세 항목 표시")

    def _scenario_inspector_profile(self) -> None:
        window = self._require_window()
        self._navigate_main(3)
        dialog = InspectorProfileDialog(
            window.inspector_tab.service,
            window,
        )
        dialog.resize(1120, 780)
        dialog.show()
        self._active_capture_widget = dialog
        self._post_capture = dialog.close
        self._flush()

        self._paste(dialog.vendor_edit, "QA-Vendor")
        self._paste(dialog.model_edit, "QA-9000")
        self._paste(dialog.os_edit, "QA-OS")
        self._paste(dialog.os_version_edit, "1.0")
        self._check(
            dialog.windowTitle() == "장비 작업 자동화 프로파일 만들기",
            "장비 작업 프로파일 창 명칭",
        )
        self._check(
            all("AI" not in button.text() for button in dialog.findChildren(QPushButton)),
            "장비 작업 프로파일에 AI 초안 버튼 없음",
        )
        self._check(
            dialog.profile_scope_combo.itemData(1) == "model",
            "모델 전용 프로파일 적용 범위 제공",
        )
        dialog.profile_scope_combo.setCurrentIndex(1)
        self._flush()
        self._check(
            dialog.profile_scope_combo.currentData() == "model",
            "모델 전용 프로파일 적용 범위 선택",
        )
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
        self._check(
            dialog.tabs.currentWidget() is dialog.trial_tab,
            "저장 전 장비로 시험 탭 제공",
        )
        self._check(
            dialog.trial_run_button.isEnabled()
            and dialog.trial_probe_button.isEnabled()
            and not dialog.trial_stop_button.isEnabled(),
            "장비 시험 실행·SSH 방식 확인 버튼 사용 가능",
        )
        self._check(
            dialog.trial_password_edit.echoMode() == QLineEdit.EchoMode.Password,
            "장비 시험 비밀번호 가림",
        )
        self._click_tab(dialog, 5)
        refresh_button = self._find_button(dialog, "갱신")
        self._click(refresh_button)
        self._check(dialog.save_button.isEnabled(), "유효한 프로파일 저장 가능")
        yaml_text = dialog.yaml_preview.toPlainText()
        self._check("model_profiles:" in yaml_text, "모델 프로파일 YAML 생성")
        self._check("inspection_commands:" in yaml_text, "점검 명령 YAML 생성")
        self._check("backup_command:" in yaml_text, "모델 백업 명령 YAML 생성")
        self._click(dialog.save_button)
        self._check(
            dialog.service.custom_rules_path.is_file(),
            "격리된 설정 폴더에 프로파일 저장",
        )

    def _scenario_config_builder_profile(self) -> None:
        window = self._require_window()
        self._navigate_main(4)
        dialog = ProfileBuilderDialog(
            window.config_builder_tab.service.profiles_dir,
            None,
            window,
        )
        dialog.resize(1080, 820)
        dialog.show()
        self._active_capture_widget = dialog
        self._post_capture = dialog.reject
        self._flush()

        self._check(dialog.windowTitle() == "프로파일 작성", "장비 설정 프로파일 작성 창 명칭")
        self._check(
            all("AI" not in button.text() for button in dialog.findChildren(QPushButton)),
            "장비 설정 프로파일에 AI 초안 버튼 없음",
        )
        self._check(dialog.saved_path is None, "명시 저장 전 프로파일 파일 미생성")

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
        page_key = window._MAIN_PAGE_KEYS[row]
        if page_key == "settings":
            self._click(window.settings_button)
        else:
            nav_row = next(index for index in range(window.nav_list.count()) if window.nav_list.item(index).data(Qt.ItemDataRole.UserRole) == page_key)
            self._click_list_row(window.nav_list, nav_row)
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
        self._ensure_control_visible(widget)
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
        self._ensure_control_visible(widget)
        self.app.clipboard().setText(text)
        widget.setFocus()
        QTest.keyClick(
            widget,
            Qt.Key.Key_A,
            Qt.KeyboardModifier.ControlModifier,
        )
        if text:
            QTest.keyClick(widget, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        else:
            QTest.keyClick(widget, Qt.Key.Key_Backspace)
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

    def _ensure_control_visible(self, widget: QWidget) -> None:
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 0, max(24, widget.height()))
            parent = parent.parentWidget()
        self._flush()
        if not widget.isVisible() or not widget.visibleRegion().contains(widget.rect().center()):
            raise AssertionError(f"보이지 않는 컨트롤을 조작할 수 없습니다: {widget.objectName() or type(widget).__name__}")

    def _assert_control_accessible(self, widget: QWidget) -> None:
        self._ensure_control_visible(widget)
        name = widget.accessibleName() or widget.objectName() or getattr(widget, "text", lambda: type(widget).__name__)()
        visible = widget.visibleRegion().boundingRect()
        self._check(
            widget.height() >= widget.fontMetrics().height() and visible.height() >= widget.height() - 2
            and visible.width() >= widget.width() - 2,
            f"조작부 전체 높이 및 클릭 영역: {name} (위젯 {widget.width()}×{widget.height()}, 가시 {visible.width()}×{visible.height()})",
        )

    def _assert_viewport_height(self, view, minimum: int, name: str) -> None:
        viewport = view.viewport()
        self._ensure_control_visible(viewport)
        self._check(
            viewport.height() >= minimum and viewport.visibleRegion().boundingRect().height() >= minimum,
            f"{name} viewport 최소 {minimum}px (실제 {viewport.height()}px)",
        )

    def _assert_readable_card(self, card: QWidget, name: str) -> None:
        labels = card.findChildren(QLabel)
        for label in labels:
            self._ensure_control_visible(label)
            required_height = label.heightForWidth(label.width()) if label.wordWrap() else label.fontMetrics().height()
            self._check(
                label.height() >= required_height and card.rect().contains(label.geometry()),
                f"{name} 텍스트 높이와 카드 내부 배치: {label.text()}",
            )
        self._assert_non_overlapping(tuple(labels), card, f"{name} 제목·값")

    def _assert_non_overlapping(self, widgets: tuple[QWidget, ...], parent: QWidget, name: str) -> None:
        rects = [QRect(widget.mapTo(parent, QPoint(0, 0)), widget.size()) for widget in widgets if widget.isVisible()]
        self._check(
            all(not left.intersects(right) for index, left in enumerate(rects) for right in rects[index + 1:]),
            f"{name} 영역 비중첩",
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
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)
        x_step = max(1, image.width() // 48)
        y_step = max(1, image.height() // 36)
        sampled_colors: set[int] = set()
        for y in range(0, image.height(), y_step):
            for x in range(0, image.width(), x_step):
                sampled_colors.add(image.pixel(x, y))
                if len(sampled_colors) >= 4:
                    break
            if len(sampled_colors) >= 4:
                break
        if len(sampled_colors) < 2:
            raise AssertionError(f"빈 화면으로 보이는 캡처입니다: {title}")
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
                "- 외부 네트워크, 운영 장비와 파일 전송 서버는 결정론적 테스트 대역으로 교체했습니다.",
                "- 화면 캡처와 Qt 속성은 확인했지만 실제 스크린 리더 인증을 대신하지 않습니다.",
                "- 동시성 경쟁은 별도 실제 QThreadPool 회귀 테스트에서 검증합니다.",
                "",
            ]
        )
        report.markdown_path.write_text("\n".join(lines), encoding="utf-8")
