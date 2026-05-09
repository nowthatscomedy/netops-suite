from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem


class SortableTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(Qt.ItemDataRole.UserRole)
        right = other.data(Qt.ItemDataRole.UserRole)
        if left is not None and right is not None:
            try:
                return left < right
            except TypeError:
                return str(left) < str(right)
        return super().__lt__(other)


def sortable_table_item(text: str, sort_value=None) -> QTableWidgetItem:
    item = SortableTableWidgetItem(text)
    if sort_value is not None:
        item.setData(Qt.ItemDataRole.UserRole, sort_value)
    return item


def nullable_number_sort_value(value: float | int | None) -> tuple[int, float]:
    if value is None:
        return (1, 0.0)
    return (0, float(value))
