from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.ui.common import make_selectable_wrapped_label
from app.utils.file_utils import (
    default_effective_path_settings,
    default_update_config,
    effective_path_settings,
    load_json,
    normalize_path_settings,
    open_in_explorer,
)
from app.version import __version__
from netops_suite.ui.actions import ActionKind, make_action_button


class SettingsTab(QWidget):
    check_updates_requested = Signal(dict)

    _PATH_FIELDS = (
        ("config_dir", "설정 파일 폴더", "프로파일과 기능별 JSON 설정 파일을 저장합니다."),
        ("logs_dir", "로그 폴더", "프로그램 로그와 AI 감사 로그를 저장합니다."),
        ("exports_dir", "결과/내보내기 폴더", "진단 결과와 AI 대화 내보내기를 저장합니다."),
    )

    _CONFIG_FILE_NAMES = (
        ("주 설정", "app_config.json"),
        ("IP 프로파일", "ip_profiles.json"),
        ("FTP 프로파일", "ftp_profiles.json"),
        ("FTP 화면 상태", "ftp_runtime.json"),
        ("SCP 프로파일", "scp_profiles.json"),
        ("SCP 화면 상태", "scp_runtime.json"),
        ("TFTP 화면 상태", "tftp_runtime.json"),
        ("공개 iperf 서버 캐시", "public_iperf_servers_cache.json"),
        ("AI 모델 목록 캐시", "ai_model_catalog_cache.json"),
        ("OUI 캐시", "oui_cache.json"),
        ("FTP/SCP 키 폴더", "ftp_keys"),
    )

    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._path_dirty = False
        self._saved_path_values: dict[str, str] = {}
        self._build_ui()
        self.state.config_reloaded.connect(self.reload_view)
        self.reload_view()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setObjectName("settingsScrollArea")
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer_layout.addWidget(self.settings_scroll)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.settings_scroll.setWidget(content)

        update_group = QGroupBox("프로그램 업데이트")
        update_layout = QVBoxLayout(update_group)

        summary_label = QLabel(
            "공식 배포 채널에서 새 버전을 확인합니다. 설치 파일은 다운로드 후 SHA-256 무결성과 "
            "게시자 정보를 확인하며, 사용자가 승인한 경우에만 설치 프로그램을 실행합니다."
        )
        summary_label.setWordWrap(True)
        update_layout.addWidget(summary_label)

        form = QFormLayout()
        self.version_label = QLabel(__version__)
        self.check_on_startup_check = QCheckBox("프로그램 시작 시 업데이트 확인")
        form.addRow("현재 버전", self.version_label)
        form.addRow("", self.check_on_startup_check)
        update_layout.addLayout(form)

        button_row = QHBoxLayout()
        self.check_update_button = make_action_button(
            "확인",
            ActionKind.START,
            tooltip="새 버전 업데이트를 확인합니다.",
        )
        button_row.addWidget(self.check_update_button)
        button_row.addStretch(1)
        update_layout.addLayout(button_row)

        self.update_status_label = QLabel("업데이트는 프로그램 이름에 고정된 공식 배포 채널을 사용합니다.")
        self.update_status_label.setWordWrap(True)
        self.update_details = QPlainTextEdit()
        self.update_details.setReadOnly(True)
        self.update_details.setMaximumHeight(150)
        update_layout.addWidget(self.update_status_label)
        update_layout.addWidget(self.update_details)
        layout.addWidget(update_group)

        storage_group = QGroupBox("저장 위치")
        storage_layout = QVBoxLayout(storage_group)
        storage_help = QLabel(
            "결과/내보내기 폴더는 저장 즉시 이후 작업에 적용됩니다. 설정 파일 폴더와 로그 폴더는 "
            "열려 있는 파일과 서비스 경로가 섞이지 않도록 프로그램을 다시 시작한 뒤 적용됩니다. "
            "설정 폴더를 바꾸면 기존 파일은 새 폴더에 복사하되 원본과 기존 대상 파일은 덮어쓰거나 삭제하지 않습니다."
        )
        storage_help.setWordWrap(True)
        storage_layout.addWidget(storage_help)

        path_form = QFormLayout()
        self.path_edits: dict[str, QLineEdit] = {}
        self.path_browse_buttons = {}
        for key, label, tooltip in self._PATH_FIELDS:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            edit = QLineEdit()
            edit.setObjectName(f"{key}Edit")
            edit.setClearButtonEnabled(True)
            edit.setToolTip(tooltip)
            browse_button = make_action_button("찾아보기", ActionKind.OPEN, tooltip=f"{label}를 선택합니다.")
            browse_button.clicked.connect(lambda _checked=False, field=key: self._browse_directory(field))
            edit.textChanged.connect(self._path_fields_changed)
            row_layout.addWidget(edit, 1)
            row_layout.addWidget(browse_button)
            path_form.addRow(label, row_widget)
            self.path_edits[key] = edit
            self.path_browse_buttons[key] = browse_button

        self.config_dir_edit = self.path_edits["config_dir"]
        self.logs_dir_edit = self.path_edits["logs_dir"]
        self.exports_dir_edit = self.path_edits["exports_dir"]
        storage_layout.addLayout(path_form)

        path_action_row = QHBoxLayout()
        self.save_paths_button = make_action_button("경로 설정 저장", ActionKind.SAVE)
        self.reset_paths_button = make_action_button("기본 경로로 되돌리기", ActionKind.UTILITY)
        path_action_row.addWidget(self.save_paths_button)
        path_action_row.addWidget(self.reset_paths_button)
        path_action_row.addStretch(1)
        storage_layout.addLayout(path_action_row)

        self.path_status_label = make_selectable_wrapped_label()
        storage_layout.addWidget(self.path_status_label)
        layout.insertWidget(0, storage_group)

        files_group = QGroupBox("현재 적용 중인 폴더와 설정 파일")
        files_layout = QVBoxLayout(files_group)
        self.config_dir_label = make_selectable_wrapped_label()
        self.ip_profile_label = make_selectable_wrapped_label()
        self.log_dir_label = make_selectable_wrapped_label()
        self.export_dir_label = make_selectable_wrapped_label()
        files_layout.addWidget(self.config_dir_label)
        files_layout.addWidget(self.ip_profile_label)
        files_layout.addWidget(self.log_dir_label)
        files_layout.addWidget(self.export_dir_label)

        self.settings_files_view = QPlainTextEdit()
        self.settings_files_view.setObjectName("settingsFilesView")
        self.settings_files_view.setReadOnly(True)
        self.settings_files_view.setMaximumHeight(190)
        files_layout.addWidget(self.settings_files_view)

        folder_button_row = QHBoxLayout()
        self.open_config_button = make_action_button("설정 폴더", ActionKind.OPEN)
        self.open_logs_button = make_action_button("로그 폴더", ActionKind.OPEN)
        self.open_exports_button = make_action_button("결과 폴더", ActionKind.OPEN)
        self.reload_button = make_action_button("설정 파일 다시 불러오기", ActionKind.REFRESH)
        folder_button_row.addWidget(self.open_config_button)
        folder_button_row.addWidget(self.open_logs_button)
        folder_button_row.addWidget(self.open_exports_button)
        folder_button_row.addWidget(self.reload_button)
        folder_button_row.addStretch(1)
        files_layout.addLayout(folder_button_row)
        layout.insertWidget(1, files_group)
        layout.addStretch(1)

        self.open_config_button.clicked.connect(lambda: open_in_explorer(self.state.paths.config_dir))
        self.open_logs_button.clicked.connect(lambda: open_in_explorer(self.state.paths.logs_dir))
        self.open_exports_button.clicked.connect(lambda: open_in_explorer(self.state.paths.exports_dir))
        self.reload_button.clicked.connect(self._reload_config_files)
        self.save_paths_button.clicked.connect(self._save_path_settings)
        self.reset_paths_button.clicked.connect(self._reset_path_fields)
        self.check_on_startup_check.toggled.connect(self._save_startup_update_preference)
        self.check_update_button.clicked.connect(self._request_update_check)

    def current_update_config(self) -> dict:
        config = default_update_config()
        config["check_on_startup"] = self.check_on_startup_check.isChecked()
        return config

    def _save_startup_update_preference(self, checked: bool) -> None:
        config = dict(self.state.app_config)
        update_config = default_update_config()
        update_config["check_on_startup"] = checked
        config["update"] = update_config
        self.state.save_app_config(config)

    def set_update_status(self, message: str, details: str = "") -> None:
        self.update_status_label.setText(message)
        if details:
            self.update_details.setPlainText(details)
        elif message:
            self.update_details.clear()

    def set_update_busy(self, busy: bool) -> None:
        self.check_update_button.setEnabled(not busy)

    def reload_view(self) -> None:
        update_config = self.state.app_config.get("update", {})
        was_blocked = self.check_on_startup_check.blockSignals(True)
        self.check_on_startup_check.setChecked(bool(update_config.get("check_on_startup", False)))
        self.check_on_startup_check.blockSignals(was_blocked)

        self._refresh_effective_path_labels()
        if not self._path_dirty:
            self._load_saved_path_fields()
        else:
            self._refresh_settings_file_preview(self._current_path_values())
        self.version_label.setText(__version__)
        self.set_update_status("업데이트는 프로그램 이름에 고정된 공식 배포 채널을 사용합니다.")

    def _path_settings_file(self) -> Path:
        explicit = getattr(self.state.paths, "path_settings", None)
        if explicit:
            return Path(explicit)
        return Path(self.state.paths.data_root) / "path_settings.json"

    def _load_saved_path_fields(self) -> None:
        effective = effective_path_settings(self.state.paths)
        stored = normalize_path_settings(load_json(self._path_settings_file(), {}))
        values = {
            key: str(stored.get(key, "") or effective[key])
            for key, _label, _tooltip in self._PATH_FIELDS
        }
        self._set_path_fields(values)
        self._saved_path_values = dict(values)
        self._path_dirty = False
        self._refresh_settings_file_preview(values)
        self._update_path_status()

    def _set_path_fields(self, values: dict[str, str]) -> None:
        for key, edit in self.path_edits.items():
            blocked = edit.blockSignals(True)
            edit.setText(str(values.get(key, "")))
            edit.blockSignals(blocked)

    def _current_path_values(self) -> dict[str, str]:
        return {key: edit.text().strip() for key, edit in self.path_edits.items()}

    def _path_fields_changed(self, _text: str = "") -> None:
        self._path_dirty = self._current_path_values() != self._saved_path_values
        self._refresh_settings_file_preview(self._current_path_values())
        if self._path_dirty:
            self.path_status_label.setText("저장되지 않은 경로 변경이 있습니다.")
        else:
            self._update_path_status()

    def _browse_directory(self, key: str) -> None:
        edit = self.path_edits[key]
        initial = edit.text().strip() or str(self.state.paths.data_root)
        label = next(label for field, label, _tooltip in self._PATH_FIELDS if field == key)
        selected = QFileDialog.getExistingDirectory(self, f"{label} 선택", initial)
        if selected:
            edit.setText(str(Path(selected)))

    def _reset_path_fields(self) -> None:
        self._set_path_fields(default_effective_path_settings(self.state.paths))
        self._path_fields_changed()

    def _save_path_settings(self) -> None:
        try:
            result = self.state.save_path_settings(self._current_path_values())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "경로 설정 저장 실패", str(exc))
            self.path_status_label.setText(f"경로 설정을 저장하지 못했습니다: {exc}")
            return

        target_paths = result.get("target_paths")
        if target_paths is not None:
            effective = effective_path_settings(target_paths)
            self._saved_path_values = {
                key: str(effective[key])
                for key, _label, _tooltip in self._PATH_FIELDS
            }
            self._set_path_fields(self._saved_path_values)
        else:
            self._saved_path_values = self._current_path_values()
        self._path_dirty = False
        self._refresh_effective_path_labels()
        self._refresh_settings_file_preview(self._saved_path_values)

        copied = len(result.get("copied_files", ()))
        skipped = len(result.get("skipped_files", ()))
        details = ["경로 설정을 저장했습니다."]
        if copied:
            details.append(f"기존 설정 파일 {copied}개를 새 폴더에 복사했습니다.")
        if skipped:
            details.append(f"대상에 이미 있던 파일 {skipped}개는 덮어쓰지 않았습니다.")
        if result.get("restart_required", False):
            details.append("설정 파일 폴더는 즉시 적용했고, 로그 폴더 변경은 프로그램을 다시 시작하면 적용됩니다.")
        else:
            details.append("결과/내보내기 폴더 변경을 적용했습니다.")
        self.path_status_label.setText(" ".join(details))

    def _update_path_status(self) -> None:
        if not self._saved_path_values:
            self.path_status_label.clear()
            return
        current = effective_path_settings(self.state.paths)
        restart_required = any(
            self._normalized_path_text(self._saved_path_values.get(key, ""))
            != self._normalized_path_text(current.get(key, ""))
            for key in ("config_dir", "logs_dir")
        )
        if restart_required:
            self.path_status_label.setText("경로 설정이 저장되었습니다. 로그 폴더 변경은 프로그램 재시작 후 적용됩니다.")
        else:
            self.path_status_label.setText("표시된 저장 위치가 현재 적용 중입니다.")

    @staticmethod
    def _normalized_path_text(value: str) -> str:
        try:
            return str(Path(value).resolve(strict=False)).casefold()
        except (OSError, ValueError):
            return value.strip().casefold()

    def _refresh_effective_path_labels(self) -> None:
        paths = self.state.paths
        self.config_dir_label.setText(f"설정 파일 폴더: {paths.config_dir}")
        self.ip_profile_label.setText(f"주 설정 파일: {paths.app_config}\nIP 프로파일: {paths.ip_profiles}")
        self.log_dir_label.setText(f"로그 폴더: {paths.logs_dir}")
        self.export_dir_label.setText(f"결과/내보내기 폴더: {paths.exports_dir}")

    def _refresh_settings_file_preview(self, values: dict[str, str]) -> None:
        config_dir_text = str(values.get("config_dir", "") or "").strip()
        config_dir = Path(config_dir_text) if config_dir_text else Path(self.state.paths.config_dir)
        lines = [f"경로 설정(고정 위치): {self._path_settings_file()}"]
        lines.extend(f"{label}: {config_dir / filename}" for label, filename in self._CONFIG_FILE_NAMES)
        self.settings_files_view.setPlainText("\n".join(lines))

    def _reload_config_files(self) -> None:
        self._path_dirty = False
        self.state.reload_config_files()

    def _request_update_check(self) -> None:
        self.check_updates_requested.emit(self.current_update_config())
