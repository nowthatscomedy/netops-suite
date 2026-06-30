from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


APP_STYLE_SHEET = """
QWidget {
    color: #1f2933;
    background: #ffffff;
    font-size: 11px;
}
QMainWindow,
QDialog {
    background: #f7f7f5;
}
QDialog#subDialog {
    background: #fbfbfa;
}
QWidget#appShell {
    background: #ffffff;
}
QFrame#sideNavigation {
    background: #f2f1ee;
    border: 0;
    border-right: 1px solid #e4e2dd;
    min-width: 164px;
    max-width: 196px;
}
QFrame#workspacePanel {
    background: #ffffff;
    border: 0;
}
QLabel#appTitle {
    color: #111827;
    font-size: 16px;
    font-weight: 700;
}
QLabel#appVersion {
    color: #667085;
    font-size: 11px;
}
QListWidget#mainNavigation {
    background: transparent;
    border: 0;
    padding: 2px 0;
    outline: 0;
}
QListWidget#mainNavigation::item {
    color: #475467;
    border: 0;
    border-radius: 4px;
    padding: 7px 9px;
    margin: 1px 0;
}
QListWidget#mainNavigation::item:hover {
    background: #ebe9e4;
    color: #111827;
}
QListWidget#mainNavigation::item:selected {
    background: #e4e2dd;
    color: #111827;
    font-weight: 600;
}
QWidget#diagnosticToolStack,
QWidget#configBuilderEmbeddedBuilder {
    background: #ffffff;
}
QToolBar {
    background: #fbfbfa;
    border: 0;
    border-bottom: 1px solid #e4e7ec;
    spacing: 6px;
    padding: 6px 10px;
}
QToolBar#mainUtilityBar {
    background: #fbfbfa;
    border: 0;
    border-bottom: 1px solid #e7e5e4;
    spacing: 4px;
    padding: 3px 8px;
}
QToolBar#mainUtilityBar QToolButton {
    background: transparent;
    color: #475467;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 2px 7px;
    min-height: 22px;
    font-size: 11px;
    font-weight: 500;
}
QToolBar#mainUtilityBar QToolButton:hover {
    background: #f3f4f6;
    color: #111827;
    border-color: #e4e7ec;
}
QToolButton#sideUtilityButton {
    background: transparent;
    color: #475467;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 2px 7px;
    min-height: 22px;
    font-size: 11px;
    font-weight: 500;
}
QToolButton#sideUtilityButton:hover:!disabled {
    background: #ebe9e4;
    color: #111827;
    border-color: #e4e2dd;
}
QToolButton#sideUtilityButton:pressed {
    background: #e4e2dd;
}
QToolButton#sideUtilityButton:disabled {
    background: transparent;
    color: #98a2b3;
    border-color: transparent;
}
QToolBar::separator {
    background: #d9e2ec;
    width: 1px;
    margin: 4px 6px;
}
QStatusBar {
    background: #ffffff;
    border-top: 1px solid #e4e7ec;
    color: #475467;
}
QMenu {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 22px 6px 10px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #f3f4f6;
    color: #111827;
}
QTabWidget::pane {
    border: 0;
    background: #ffffff;
    top: 0;
}
QTabBar::tab {
    background: transparent;
    color: #475467;
    border: 0;
    border-bottom: 2px solid transparent;
    padding: 8px 13px;
    margin-right: 4px;
    min-width: 76px;
}
QTabBar::tab:selected {
    background: transparent;
    color: #111827;
    border-bottom-color: #111827;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background: transparent;
    color: #111827;
}
QGroupBox {
    background: transparent;
    border: 0;
    border-top: 1px solid #e4e7ec;
    border-radius: 0;
    margin-top: 12px;
    padding: 9px 2px 0 2px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0;
    padding: 0 8px 0 0;
    color: #111827;
    background: transparent;
}
QLabel {
    background: transparent;
}
QLabel#dialogIntro {
    color: #475467;
    background: transparent;
    padding: 0 0 2px 0;
    line-height: 140%;
}
QLineEdit,
QPlainTextEdit,
QTextEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    padding: 3px 7px;
    min-height: 20px;
    selection-background-color: #64748b;
    selection-color: #ffffff;
}
QPlainTextEdit,
QTextEdit {
    padding: 6px 7px;
}
QLineEdit:focus,
QPlainTextEdit:focus,
QTextEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus {
    border: 1px solid #64748b;
}
QLineEdit:disabled,
QPlainTextEdit:disabled,
QTextEdit:disabled,
QComboBox:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled {
    background: #eef2f6;
    color: #98a2b3;
    border-color: #d9e2ec;
}
QComboBox::drop-down {
    border: 0;
    width: 24px;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    selection-background-color: #f3f4f6;
    selection-color: #111827;
    padding: 3px;
}
QPushButton,
QToolButton {
    background: #ffffff;
    color: #182230;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    padding: 3px 8px;
    min-height: 22px;
    font-size: 11px;
    font-weight: 500;
}
QPushButton:hover:!disabled,
QToolButton:hover:!disabled {
    background: #f8fafc;
    border-color: #98a2b3;
}
QPushButton:pressed,
QToolButton:pressed {
    background: #eef2f6;
}
QPushButton:disabled,
QToolButton:disabled {
    background: #eef2f6;
    color: #98a2b3;
    border-color: #d9e2ec;
}
QToolButton::menu-indicator {
    image: none;
    width: 0;
}
QCheckBox,
QRadioButton {
    spacing: 7px;
}
QTableWidget,
QTableView,
QListWidget,
QTreeView {
    background: #ffffff;
    alternate-background-color: #fafafa;
    border: 1px solid #e4e7ec;
    border-radius: 4px;
    gridline-color: #e4e7ec;
    selection-background-color: #e5e7eb;
    selection-color: #182230;
    outline: 0;
}
QTableWidget::item,
QTableView::item,
QListWidget::item,
QTreeView::item {
    padding: 4px 6px;
}
QTableWidget::item:hover,
QTableView::item:hover,
QListWidget::item:hover,
QTreeView::item:hover {
    background: #f3f4f6;
}
QTableWidget::item:selected,
QTableView::item:selected,
QListWidget::item:selected,
QTreeView::item:selected {
    background: #e5e7eb;
    color: #182230;
}
QHeaderView::section {
    background: #f3f4f6;
    color: #344054;
    padding: 5px 7px;
    border: 0;
    border-right: 1px solid #e4e7ec;
    border-bottom: 1px solid #e4e7ec;
    font-weight: 600;
}
QListWidget#diagnosticToolList {
    background: transparent;
    border: 0;
    border-right: 1px solid #e4e7ec;
    border-radius: 0;
    padding: 2px 8px 2px 0;
}
QListWidget#diagnosticToolList::item {
    border-radius: 4px;
    padding: 7px 9px;
    margin: 1px 0;
}
QListWidget#diagnosticToolList::item:selected {
    background: #f3f4f6;
    color: #111827;
    font-weight: 600;
}
QSplitter::handle {
    background: #e4e7ec;
}
QSplitter::handle:horizontal {
    width: 6px;
}
QSplitter::handle:vertical {
    height: 6px;
}
QProgressBar {
    background: #eef2f6;
    border: 1px solid #e4e7ec;
    border-radius: 5px;
    text-align: center;
    min-height: 10px;
}
QProgressBar::chunk {
    background: #64748b;
    border-radius: 5px;
}
QDockWidget {
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}
QDockWidget::title {
    background: #ffffff;
    border: 1px solid #e4e7ec;
    padding: 6px 8px;
    color: #344054;
    font-weight: 600;
}
QScrollArea {
    background: transparent;
    border: 0;
}
QDialogButtonBox {
    border-top: 1px solid #e4e7ec;
    padding-top: 10px;
    margin-top: 4px;
}
QScrollBar:vertical,
QScrollBar:horizontal {
    background: #f4f6f8;
    border: 0;
    margin: 0;
}
QScrollBar:vertical {
    width: 10px;
}
QScrollBar:horizontal {
    height: 10px;
}
QScrollBar::handle {
    background: #cbd5e1;
    border-radius: 5px;
}
QScrollBar::handle:hover {
    background: #98a2b3;
}
QScrollBar::add-line,
QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QToolTip {
    background: #182230;
    color: #ffffff;
    border: 0;
    border-radius: 4px;
    padding: 5px 7px;
}
"""


def apply_app_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette(app.palette())
    palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8fafc"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#182230"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#182230"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#182230"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#64748b"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLE_SHEET)
