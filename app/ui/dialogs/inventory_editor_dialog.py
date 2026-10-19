from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any

import pandas as pd
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.ui.common import (
    make_dialog_intro,
    make_inline_status,
    polish_dialog,
    required_legend_text,
    set_inline_status,
)
from netops_suite.ui.actions import ActionKind, make_action_button
from netops_suite.ui.selection_inputs import NoWheelComboBox

DEVICE_COLUMN = "device"
# (key, header, required)
BASE_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("ip", "IP *", True),
    (DEVICE_COLUMN, "장비 종류 *", True),
    ("model", "모델", False),
    ("connection_type", "접속 *", True),
    ("port", "포트 *", True),
    ("username", "계정", False),
    ("password", "비밀번호", False),
    ("enable_password", "enable 비밀번호", False),
)
SECRET_COLUMNS = frozenset({"password", "enable_password"})
_KNOWN_KEYS = {key for key, _header, _required in BASE_COLUMNS} | {"vendor", "os"}
_DEFAULT_PORTS = {"ssh": 22, "telnet": 23}
_INVALID_BRUSH = QBrush(QColor("#fde8e4"))
_MASK = "●●●●●●"


def device_label(vendor: str, os_name: str) -> str:
    return f"{vendor} / {os_name}"


class _SecretDelegate(QStyledItemDelegate):
    """Show passwords as dots, and edit them in a password field."""

    def displayText(self, value, locale) -> str:  # noqa: N802 - Qt API
        return _MASK if str(value or "") else ""

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt API
        editor = QLineEdit(parent)
        editor.setEchoMode(QLineEdit.EchoMode.Password)
        return editor


class _InventoryTable(QTableWidget):
    def __init__(self, dialog: "InventoryEditorDialog") -> None:
        super().__init__(dialog)
        self._dialog = dialog

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.matches(QKeySequence.StandardKey.Paste):
            self._dialog.paste_clipboard()
            return
        super().keyPressEvent(event)


class InventoryEditorDialog(QDialog):
    """Make or edit a device list without opening Excel."""

    def __init__(
        self,
        device_types: list[tuple[str, str]],
        parent=None,
        *,
        rows: list[dict[str, Any]] | None = None,
        extra_columns: tuple[str, ...] = (),
        default_device: tuple[str, str] | None = None,
        default_connection: str = "ssh",
        save_dir: Path | None = None,
        source_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.saved_path: Path | None = None
        self.save_dir = save_dir
        self.source_path = source_path
        self._device_types: list[tuple[str, str]] = []
        for vendor, os_name in device_types:
            if vendor and os_name and (vendor, os_name) not in self._device_types:
                self._device_types.append((vendor, os_name))
        self._default_device = default_device or (
            self._device_types[0] if self._device_types else ("", "")
        )
        self._default_connection = (
            default_connection if default_connection in _DEFAULT_PORTS else "ssh"
        )
        rows = list(rows or [])
        for row in rows:
            pair = (str(row.get("vendor", "")).strip(), str(row.get("os", "")).strip())
            if all(pair) and pair not in self._device_types:
                self._device_types.append(pair)
        extras: list[str] = []
        for name in list(extra_columns) + [
            key for row in rows for key in row if key not in _KNOWN_KEYS
        ]:
            if name and name not in extras and name not in _KNOWN_KEYS:
                extras.append(name)
        self.columns: list[tuple[str, str, bool]] = list(BASE_COLUMNS) + [
            (name, f"{name} *" if name in extra_columns else name, name in extra_columns)
            for name in extras
        ]
        self.column_index = {key: index for index, (key, _h, _r) in enumerate(self.columns)}

        self.setWindowTitle("장비 목록 편집" if source_path else "장비 목록 만들기")
        self.resize(1080, 560)
        self._bulk_loading = True
        self._build_ui()
        for row in rows:
            self.add_row(row)
        if not rows:
            self.add_row()
        self._bulk_loading = False
        self._refresh_status()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        polish_dialog(self, layout)
        layout.addWidget(
            make_dialog_intro(
                "한 행에 장비 한 대를 적습니다. Excel에서 IP 목록을 복사해 IP 칸에 붙여넣으면(Ctrl+V) "
                "행이 자동으로 늘어나고 장비 종류·접속 방식은 바로 위 행을 따릅니다. "
                "비밀번호를 파일에 남기기 싫으면 비워 두고 점검 화면의 '공통 계정'을 쓰세요."
            )
        )
        self.table = _InventoryTable(self)
        self.table.setObjectName("inventoryEditorTable")
        self.table.setColumnCount(len(self.columns))
        self.table.setHorizontalHeaderLabels([header for _k, header, _r in self.columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setDefaultSectionSize(30)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for key, width in (("ip", 130), (DEVICE_COLUMN, 190), ("model", 110), ("connection_type", 90), ("port", 60)):
            self.table.setColumnWidth(self.column_index[key], width)
        secret_delegate = _SecretDelegate(self.table)
        for key in SECRET_COLUMNS:
            self.table.setItemDelegateForColumn(self.column_index[key], secret_delegate)
        self.table.itemChanged.connect(self._refresh_status)
        layout.addWidget(self.table, 1)

        row_buttons = QHBoxLayout()
        self.add_row_button = make_action_button("행 추가", ActionKind.ADD)
        self.add_row_button.clicked.connect(lambda: self.add_row(self._row_defaults()))
        self.duplicate_row_button = make_action_button(
            "선택 행 복제",
            ActionKind.COPY,
            tooltip="선택한 행을 복사하고 IP 마지막 자리를 1 올립니다.",
        )
        self.duplicate_row_button.clicked.connect(self.duplicate_selected_rows)
        self.delete_row_button = make_action_button("선택 행 삭제", ActionKind.DELETE)
        self.delete_row_button.clicked.connect(self.delete_selected_rows)
        row_buttons.addWidget(self.add_row_button)
        row_buttons.addWidget(self.duplicate_row_button)
        row_buttons.addWidget(self.delete_row_button)
        row_buttons.addStretch(1)
        layout.addLayout(row_buttons)

        self.status_label = make_inline_status("info", "")
        self.status_label.setObjectName("inventoryEditorStatus")
        layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        legend = QLabel(required_legend_text())
        legend.setWordWrap(True)
        actions.addWidget(legend, 1)
        self.save_button = make_action_button(
            "저장하고 사용",
            ActionKind.PRIMARY,
            tooltip="Excel로 저장하고 대상 장비 목록으로 선택합니다.",
            object_name="inventoryEditorSaveButton",
        )
        self.save_button.clicked.connect(self.save)
        cancel = make_action_button("취소", ActionKind.CANCEL)
        cancel.clicked.connect(self.reject)
        actions.addWidget(self.save_button)
        actions.addWidget(cancel)
        layout.addLayout(actions)

    # -- rows -------------------------------------------------------------
    def _row_defaults(self) -> dict[str, str]:
        if self.table.rowCount():
            last = self.row_values(self.table.rowCount() - 1)
            return {
                "vendor": last["vendor"],
                "os": last["os"],
                "model": last.get("model", ""),
                "connection_type": last["connection_type"],
                "port": last["port"],
                "username": last.get("username", ""),
            }
        return {}

    def add_row(self, values: dict[str, Any] | None = None) -> int:
        values = dict(values or {})
        row = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(row)
        device_combo = NoWheelComboBox()
        device_combo.setObjectName("inventoryEditorDeviceCombo")
        for vendor, os_name in self._device_types:
            device_combo.addItem(device_label(vendor, os_name), (vendor, os_name))
        vendor = str(values.get("vendor") or self._default_device[0]).strip()
        os_name = str(values.get("os") or self._default_device[1]).strip()
        index = device_combo.findData((vendor, os_name))
        if index < 0 and vendor and os_name:
            device_combo.addItem(device_label(vendor, os_name), (vendor, os_name))
            index = device_combo.count() - 1
        device_combo.setCurrentIndex(max(index, 0))
        device_combo.currentIndexChanged.connect(self._refresh_status)
        self.table.setCellWidget(row, self.column_index[DEVICE_COLUMN], device_combo)

        connection_combo = NoWheelComboBox()
        connection_combo.setObjectName("inventoryEditorConnectionCombo")
        connection_combo.addItem("SSH", "ssh")
        connection_combo.addItem("Telnet", "telnet")
        connection = str(values.get("connection_type") or self._default_connection).strip().lower()
        connection_combo.setCurrentIndex(max(connection_combo.findData(connection), 0))
        connection_combo.currentIndexChanged.connect(
            lambda _index, combo=connection_combo: self._sync_default_port(combo)
        )
        self.table.setCellWidget(row, self.column_index["connection_type"], connection_combo)

        for key, _header, _required in self.columns:
            if key in (DEVICE_COLUMN, "connection_type"):
                continue
            text = str(values.get(key, "") or "").strip()
            if key == "port" and not text:
                text = str(_DEFAULT_PORTS.get(connection_combo.currentData(), 22))
            item = QTableWidgetItem(text)
            if key in SECRET_COLUMNS:
                item.setToolTip("")
            self.table.setItem(row, self.column_index[key], item)
        self.table.blockSignals(False)
        if not self._bulk_loading:
            self._refresh_status()
        return row

    def _sync_default_port(self, combo: QComboBox) -> None:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, self.column_index["connection_type"]) is combo:
                item = self.table.item(row, self.column_index["port"])
                if item is not None and item.text().strip() in {"", "22", "23"}:
                    item.setText(str(_DEFAULT_PORTS.get(combo.currentData(), 22)))
                break
        self._refresh_status()

    def _selected_rows(self) -> list[int]:
        rows = {index.row() for index in self.table.selectedIndexes()}
        current = self.table.currentRow()
        if not rows and current >= 0:
            rows.add(current)
        return sorted(rows)

    def duplicate_selected_rows(self) -> None:
        for row in self._selected_rows():
            values = self.row_values(row)
            values["ip"] = _next_ip(values.get("ip", ""))
            self.add_row(values)

    def delete_selected_rows(self) -> None:
        for row in reversed(self._selected_rows()):
            self.table.removeRow(row)
        if self.table.rowCount() == 0:
            self.add_row()
        self._refresh_status()

    def row_values(self, row: int) -> dict[str, str]:
        values: dict[str, str] = {}
        for key, _header, _required in self.columns:
            column = self.column_index[key]
            if key == DEVICE_COLUMN:
                combo = self.table.cellWidget(row, column)
                vendor, os_name = (combo.currentData() if combo else None) or ("", "")
                values["vendor"], values["os"] = vendor, os_name
            elif key == "connection_type":
                combo = self.table.cellWidget(row, column)
                values[key] = str(combo.currentData() if combo else "ssh")
            else:
                item = self.table.item(row, column)
                values[key] = item.text().strip() if item else ""
        return values

    def all_rows(self) -> list[dict[str, str]]:
        return [self.row_values(row) for row in range(self.table.rowCount())]

    # -- paste ------------------------------------------------------------
    def paste_clipboard(self, text: str | None = None) -> None:
        if text is None:
            text = QGuiApplication.clipboard().text()
        lines = [line for line in text.replace("\r\n", "\n").split("\n")]
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return
        start_row = max(self.table.currentRow(), 0)
        start_column = max(self.table.currentColumn(), 0)
        self._bulk_loading = True
        for offset, line in enumerate(lines):
            row = start_row + offset
            while row >= self.table.rowCount():
                self.add_row(self._row_defaults())
            for column_offset, value in enumerate(line.split("\t")):
                column = start_column + column_offset
                if column >= len(self.columns):
                    break
                self._set_cell_text(row, column, value.strip())
        self._bulk_loading = False
        self._refresh_status()

    def _set_cell_text(self, row: int, column: int, value: str) -> None:
        widget = self.table.cellWidget(row, column)
        if isinstance(widget, QComboBox):
            lowered = value.casefold()
            for index in range(widget.count()):
                data = widget.itemData(index)
                candidates = {widget.itemText(index).casefold()}
                if isinstance(data, tuple):
                    candidates.add(data[0].casefold())
                else:
                    candidates.add(str(data).casefold())
                if lowered in candidates:
                    widget.setCurrentIndex(index)
                    return
            return
        item = self.table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self.table.setItem(row, column, item)
        item.setText(value)

    # -- validation -------------------------------------------------------
    def problems(self) -> list[tuple[int, str, str]]:
        """(row, column key, message) for every cell that blocks saving."""
        found: list[tuple[int, str, str]] = []
        seen_ips: dict[str, int] = {}
        for row in range(self.table.rowCount()):
            values = self.row_values(row)
            number = row + 1
            ip = values["ip"]
            if not ip:
                found.append((row, "ip", f"{number}행: IP를 입력하세요."))
            else:
                try:
                    ipaddress.ip_address(ip)
                except ValueError:
                    found.append((row, "ip", f"{number}행: IP 형식이 아닙니다 ({ip})."))
                else:
                    if ip in seen_ips:
                        found.append((row, "ip", f"{number}행: {seen_ips[ip] + 1}행과 IP가 같습니다."))
                    seen_ips.setdefault(ip, row)
            if not values["vendor"] or not values["os"]:
                found.append((row, DEVICE_COLUMN, f"{number}행: 장비 종류를 고르세요."))
            try:
                port = int(values["port"])
            except ValueError:
                port = 0
            if not 1 <= port <= 65535:
                found.append((row, "port", f"{number}행: 포트는 1~65535 숫자입니다."))
            for key, _header, required in self.columns[len(BASE_COLUMNS):]:
                if required and not values.get(key):
                    found.append((row, key, f"{number}행: 명령 변수 {key} 값을 입력하세요."))
        return found

    def _refresh_status(self, *_args) -> None:
        if not hasattr(self, "status_label") or getattr(self, "_bulk_loading", False):
            return
        problems = self.problems()
        flagged = {(row, key) for row, key, _message in problems}
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            for key, _header, _required in self.columns:
                item = self.table.item(row, self.column_index[key])
                if item is not None:
                    item.setBackground(_INVALID_BRUSH if (row, key) in flagged else QBrush())
        self.table.blockSignals(False)
        count = self.table.rowCount()
        if problems:
            more = f" 외 {len(problems) - 3}건" if len(problems) > 3 else ""
            set_inline_status(
                self.status_label,
                "warning",
                f"장비 {count}대 · 고칠 칸 {len(problems)}개: "
                + " ".join(message for _r, _k, message in problems[:3])
                + more,
            )
        else:
            set_inline_status(
                self.status_label, "success", f"장비 {count}대 · 저장할 수 있습니다."
            )

    # -- save -------------------------------------------------------------
    def dataframe(self) -> pd.DataFrame:
        order = ["ip", "vendor", "os", "model", "connection_type", "port", "username", "password", "enable_password"]
        order += [key for key, _h, _r in self.columns[len(BASE_COLUMNS):]]
        records = []
        for values in self.all_rows():
            record = {key: values.get(key, "") for key in order}
            record["port"] = int(values["port"]) if values["port"].isdigit() else values["port"]
            records.append(record)
        return pd.DataFrame(records, columns=order)

    def save(self) -> Path | None:
        problems = self.problems()
        if problems:
            self._refresh_status()
            row, key, message = problems[0]
            self.table.setCurrentCell(row, self.column_index[key])
            QMessageBox.warning(
                self,
                "장비 목록 저장",
                "빨간 칸을 먼저 고치세요.\n\n" + "\n".join(m for _r, _k, m in problems[:8]),
            )
            return None
        if self.source_path is not None:
            suggested = self.source_path
        else:
            from app.utils.file_utils import timestamped_export_path

            base = self.save_dir or Path.home()
            suggested = timestamped_export_path(base, "device_list", "xlsx")
        selected, _ = QFileDialog.getSaveFileName(
            self, "장비 목록 저장", str(suggested), "Excel Files (*.xlsx)"
        )
        if not selected:
            return None
        target = Path(selected)
        if target.suffix.casefold() != ".xlsx":
            target = target.with_suffix(".xlsx")
        return self.save_to(target)

    def save_to(self, target: Path) -> Path | None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self.dataframe().to_excel(target, index=False)
        except Exception as exc:
            QMessageBox.warning(self, "장비 목록 저장 실패", f"파일을 저장하지 못했습니다.\n{exc}")
            return None
        self.saved_path = target
        self.accept()
        return target


def _next_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()) + 1)
    except (ValueError, ipaddress.AddressValueError):
        return ""
