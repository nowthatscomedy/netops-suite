from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from app.ui.common.table_items import sortable_table_item


def make_table_item(
    text: str,
    sort_value=None,
    *,
    tooltip: bool | str = True,
    align: Qt.AlignmentFlag | Qt.Alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
) -> QTableWidgetItem:
    item = sortable_table_item(str(text), sort_value)
    if isinstance(tooltip, str):
        item.setToolTip(tooltip)
    elif tooltip and text:
        item.setToolTip(str(text))
    if align is not None:
        item.setTextAlignment(align)
    return item


def bind_empty_state(table: QTableWidget, label: QLabel, text: str | None = None) -> None:
    if text is not None:
        label.setText(text)
    label.setVisible(table.rowCount() == 0)


def configure_result_table(
    table: QTableWidget,
    columns: list[str] | tuple[str, ...],
    *,
    stretch_columns: tuple[int, ...] = (),
    selection_mode: QAbstractItemView.SelectionMode = QAbstractItemView.SelectionMode.SingleSelection,
) -> None:
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(selection_mode)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    stretch = set(stretch_columns)
    for column in range(table.columnCount()):
        mode = QHeaderView.ResizeMode.Stretch if column in stretch else QHeaderView.ResizeMode.ResizeToContents
        header.setSectionResizeMode(column, mode)


def set_table_minimums(
    table: QTableWidget,
    min_height: int,
    stretch_columns: tuple[int, ...] = (),
) -> None:
    table.setMinimumHeight(min_height)
    table.setMaximumHeight(16777215)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    if not stretch_columns:
        return

    header = table.horizontalHeader()
    stretch = set(stretch_columns)
    for column in range(table.columnCount()):
        mode = QHeaderView.ResizeMode.Stretch if column in stretch else QHeaderView.ResizeMode.ResizeToContents
        header.setSectionResizeMode(column, mode)


def make_table_log_splitter(
    table: QTableWidget,
    log_widget: QWidget,
    table_size: int = 420,
    log_size: int = 120,
) -> QSplitter:
    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(table)
    splitter.addWidget(log_widget)
    splitter.setSizes([table_size, log_size])
    return splitter
