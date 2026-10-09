from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


APP_STYLE_SHEET = """
QWidget {
    color: #24334b;
    font-size: 13px;
}
QMainWindow, QDialog, QWidget#appShell, QFrame#workspacePanel {
    background: #f3f6fb;
}
QDialog#subDialog { background: #f8fafc; }
QFrame#workspacePanel { border: 0; }
QFrame#sideNavigation {
    background: #101d32;
    border: 0;
    min-width: 192px;
    max-width: 208px;
}
QLabel#appTitle { color: #f8fafc; font-size: 17px; font-weight: 700; }
QLabel#appVersion { color: #8295b3; font-size: 11px; }
QLabel#appLogoTile { background: #e4eefc; border-radius: 8px; }
QLabel#navigationCaption { color: #8295b3; font-size: 11px; font-weight: 600; padding: 8px 8px 2px 8px; }
QLabel#pageTitle { color: #15243c; font-size: 23px; font-weight: 700; }
QLabel#pageDescription { color: #66758c; font-size: 12px; }
QLabel#diagnosticToolQuestion { color: #15243c; font-weight: 700; }
QLabel#diagnosticToolHint { color: #33507e; background: #eef4ff; border: 1px solid #d5e1fb;
    border-radius: 6px; padding: 6px 10px; }
QListWidget#mainNavigation { background: transparent; border: 0; padding: 0; outline: 0; }
QListWidget#mainNavigation::item {
    color: #b7c6dc;
    border: 2px solid transparent;
    border-radius: 8px;
    padding: 10px 8px;
    margin: 2px 0;
}
QListWidget#mainNavigation::item:hover { background: #1c2d48; color: #ffffff; }
QListWidget#mainNavigation::item:selected { background: #224477; color: #ffffff; font-weight: 600; }
QListWidget#mainNavigation::item:selected:focus { background: #224477; border: 2px solid #60a5fa; }
QToolButton#sideUtilityButton {
    background: transparent; color: #b7c6dc; border: 1px solid transparent;
    border-radius: 6px; padding: 6px; min-height: 22px; font-size: 12px;
}
QToolButton#sideUtilityButton:hover, QToolButton#sideUtilityButton:checked { background: #1c2d48; color: #ffffff; }
QToolButton#sideUtilityButton:focus { border: 1px solid #60a5fa; }
QToolButton#sideUtilityButton:disabled { color: #637894; }
QFrame#homeHero {
    background: #e8effc; border: 1px solid #d4e2fb; border-radius: 14px;
}
QLabel#heroEyebrow { color: #2563eb; font-size: 11px; font-weight: 700; }
QLabel#homeHeroTitle { color: #152b52; font-size: 26px; font-weight: 700; }
QLabel#homeHeroBody { color: #526b90; font-size: 13px; }
QFrame#homeFirstSteps { background: #ffffff; border: 1px solid #dfe6f1; border-radius: 12px; }
QLabel#homeFirstStepsTitle { color: #2457c5; font-size: 13px; font-weight: 700; }
QLabel#homeStepBadge { background: #2457c5; color: #ffffff; border-radius: 12px; font-weight: 700; }
QLabel#homeStepText { color: #4a5b74; font-size: 12px; }
QFrame#taskCard { background: #ffffff; border: 1px solid #dfe6f1; border-radius: 12px; }
QFrame#taskCard:hover { border: 1px solid #8baeee; background: #fcfdff; }
QLabel#taskCardTitle { color: #1b2d48; font-size: 16px; font-weight: 700; }
QLabel#taskCardDescription { color: #697990; font-size: 12px; }
QLabel#taskCategory { color: #7888a1; font-size: 11px; }
QLabel#taskIconTile { background: #edf3ff; border: 0; border-radius: 11px; padding: 10px; }
QFrame#taskCard QPushButton { border-radius: 7px; }
QWidget#pageHeader { background: transparent; }
QWidget#wirelessStatusCard { background: #ffffff; border: 1px solid #dfe6f1; border-radius: 10px; }
QLabel#wirelessCardTitle { color: #718198; font-size: 12px; }
QLabel#wirelessCardValue { color: #1a355c; font-size: 17px; font-weight: 600; }
QWidget#inspectorInventoryGuideCard { background: #f0f5fe; border: 1px solid #dce7f8; border-radius: 8px; }
QLabel#inventoryGuideStepTitle { color: #315c98; font-size: 13px; font-weight: 600; }
QFrame#inspectorSupportingTools {
    background: #f4f6f9; border: 1px solid #e1e6ee; border-radius: 9px;
}
QLabel#inspectorSupportingTitle { color: #6b778c; font-size: 11px; font-weight: 600; }
QFrame#inspectorSupportingTools QToolButton#disclosureToggle {
    background: #ffffff; color: #526176; border-color: #e3e7ed; font-weight: 500;
}
QFrame#inspectorSupportingTools QToolButton#disclosureToggle:hover {
    background: #f8fafc; border-color: #b6c3d5;
}
QFrame#inspectorSupportingTools QToolButton#disclosureToggle:checked {
    background: #ffffff; color: #334764; border-color: #b6c3d5;
}
QWidget#collapsibleSection { background: transparent; }
QToolButton#disclosureToggle {
    background: #edf2f9; color: #435a7b; border: 1px solid #e0e8f4;
    border-radius: 7px; min-height: 26px; padding: 4px 10px; font-size: 12px;
    text-align: left; font-weight: 600;
}
QToolButton#disclosureToggle:hover { background: #e4ecfa; border-color: #bfd1ef; }
QToolButton#disclosureToggle:checked { color: #2256a7; background: #e7effc; }
QToolButton#disclosureToggle:focus { border: 2px solid #60a5fa; padding: 3px 9px; }
QToolBar { background: #ffffff; border: 0; border-bottom: 1px solid #e1e8f3; spacing: 6px; padding: 6px 10px; }
QToolBar#mainUtilityBar { background: #f8fafc; }
QToolBar::separator { background: #dce4ef; width: 1px; margin: 5px 8px; }
QStatusBar { background: #ffffff; border-top: 1px solid #e1e8f3; color: #70819a; font-size: 11px; }
QMenu { background: #ffffff; border: 1px solid #d7e0ee; border-radius: 8px; padding: 5px; }
QMenu::item { padding: 8px 24px 8px 12px; border-radius: 5px; }
QMenu::item:selected { background: #eaf1ff; color: #1d4ed8; }
QTabWidget::pane { border: 0; background: transparent; top: 0; }
QTabBar::tab { background: transparent; color: #6b7b93; border: 0; border-bottom: 3px solid transparent; padding: 9px 15px; margin-right: 3px; min-width: 62px; }
QTabBar::tab:selected { background: #ffffff; color: #2563eb; border-bottom-color: #2563eb; font-weight: 600; }
QTabBar::tab:selected:focus { border: 2px solid #60a5fa; padding: 8px 14px; }
QTabBar::tab:hover:!selected { background: #eaf0fa; color: #254977; }
QGroupBox {
    background: #ffffff;
    border: 1px solid #e0e7f1;
    border-radius: 10px;
    margin-top: 0;
    padding: 32px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: padding; subcontrol-position: top left;
    left: 14px; top: 10px; padding: 0;
    color: #355073; background: transparent;
}
QLabel { background: transparent; }
QLabel#dialogIntro { color: #66758c; padding: 0 0 6px 0; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #ffffff; border: 1px solid #cbd7e8; border-radius: 6px;
    padding: 4px 8px; selection-background-color: #2563eb; selection-color: #ffffff;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { min-height: 20px; }
QPlainTextEdit, QTextEdit { padding: 7px 8px; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #3b82f6; }
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { background: #f0f4f9; color: #95a2b6; border-color: #dfe6f0; }
QComboBox::drop-down { border: 0; width: 26px; }
QComboBox[netopsChevron="true"]::down-arrow { image: none; }
QComboBox QAbstractItemView { background: #ffffff; border: 1px solid #cbd7e8; selection-background-color: #eaf1ff; selection-color: #1d4ed8; padding: 4px; }
QPushButton, QToolButton { background: #ffffff; color: #314766; border: 1px solid #cbd7e8; border-radius: 6px; padding: 4px 9px; min-height: 22px; font-size: 12px; font-weight: 500; }
QPushButton:hover:!disabled, QToolButton:hover:!disabled { background: #edf4ff; border-color: #9dbbe8; }
QPushButton:pressed, QToolButton:pressed { background: #dfeafe; }
QPushButton:focus,
QToolButton:focus { border: 2px solid #60a5fa; padding: 3px 8px; }
QPushButton:disabled, QToolButton:disabled { background: #f0f4f9; color: #95a2b6; border-color: #dfe6f0; }
QToolButton::menu-indicator { image: none; width: 0; }
QCheckBox, QRadioButton { spacing: 7px; }
QTableWidget, QTableView, QListWidget, QTreeView {
    background: #ffffff; alternate-background-color: #f7f9fd; border: 1px solid #dfe6f1;
    border-radius: 7px; gridline-color: #edf1f7; selection-background-color: #dceaff; selection-color: #173864; outline: 0;
}
QTableWidget::item, QTableView::item, QListWidget::item, QTreeView::item { padding: 5px 7px; }
QTableWidget::item:hover, QTableView::item:hover, QListWidget::item:hover, QTreeView::item:hover { background: #edf4ff; }
QTableWidget::item:selected, QTableView::item:selected, QListWidget::item:selected, QTreeView::item:selected { background: #dceaff; color: #173864; }
QHeaderView::section { background: #eef3fb; color: #5a6e8c; padding: 7px 8px; border: 0; border-bottom: 1px solid #dde6f2; font-size: 12px; font-weight: 600; }
QListWidget#diagnosticToolList { background: transparent; border: 0; padding: 2px 8px 2px 0; }
QListWidget#diagnosticToolList::item { border-radius: 6px; padding: 8px; margin: 2px 0; }
QListWidget#diagnosticToolList::item:selected { background: #e7effc; color: #2563eb; font-weight: 600; }
QSplitter::handle { background: #dce5f2; }
QSplitter::handle:horizontal { width: 5px; }
QSplitter::handle:vertical { height: 5px; }
QProgressBar { background: #eaf0f9; border: 0; border-radius: 5px; text-align: center; min-height: 10px; }
QProgressBar::chunk { background: #3b82f6; border-radius: 5px; }
QDockWidget::title { background: #eaf1fc; border: 1px solid #dce6f5; padding: 8px 10px; color: #355073; font-weight: 600; }
QScrollArea { background: transparent; border: 0; }
QDialogButtonBox { border-top: 1px solid #e1e8f3; padding-top: 10px; margin-top: 5px; }
QScrollBar:vertical, QScrollBar:horizontal { background: #eff3f9; border: 0; margin: 0; }
QScrollBar:vertical { width: 10px; }
QScrollBar:horizontal { height: 10px; }
QScrollBar::handle { background: #b8c8df; border-radius: 5px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: #8fa9cd; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QToolTip { background: #172b49; color: #ffffff; border: 0; border-radius: 5px; padding: 7px 9px; }
"""


def apply_app_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette(app.palette())
    for role, color in {
        QPalette.ColorRole.Window: "#f3f6fb", QPalette.ColorRole.Base: "#ffffff",
        QPalette.ColorRole.AlternateBase: "#f7f9fd", QPalette.ColorRole.Text: "#24334b",
        QPalette.ColorRole.WindowText: "#24334b", QPalette.ColorRole.Button: "#ffffff",
        QPalette.ColorRole.ButtonText: "#314766", QPalette.ColorRole.Highlight: "#2563eb",
        QPalette.ColorRole.HighlightedText: "#ffffff",
    }.items():
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLE_SHEET)
