from __future__ import annotations

import pytest
from PySide6.QtWidgets import QGroupBox, QLineEdit, QStyle, QStyleOptionGroupBox, QVBoxLayout

from app.ui.common.theme import APP_STYLE_SHEET, apply_app_theme
from netops_suite.modules.config_builder.switch_configurator.desktop_impl import COMPACT_UI_STYLE


@pytest.mark.parametrize("style", [APP_STYLE_SHEET, COMPACT_UI_STYLE], ids=["app", "builder"])
@pytest.mark.parametrize("title", ["현재 Wi-Fi 상태", "3. 검증 및 실행", "CLI", "선택"])
def test_card_title_sits_inside_an_unbroken_border(qapp, style, title):
    apply_app_theme(qapp)
    group = QGroupBox(title)
    group.setStyleSheet(style)
    layout = QVBoxLayout(group)
    field = QLineEdit("입력값")
    layout.addWidget(field)
    group.resize(400, 120)
    group.show()
    qapp.processEvents()
    try:
        option = QStyleOptionGroupBox()
        group.initStyleOption(option)
        label = group.style().subControlRect(
            QStyle.ComplexControl.CC_GroupBox, option,
            QStyle.SubControl.SC_GroupBoxLabel, group,
        )
        assert label.top() >= 8
        assert group.rect().contains(label)
        assert field.geometry().top() > label.bottom() + 4
        # The title must not erase a segment of the top border.
        pixmap = group.grab()
        pixels = pixmap.toImage()
        scale = pixmap.devicePixelRatio()
        edge_y = round(scale / 2)
        reference = pixels.pixelColor(round(200 * scale), edge_y)
        for logical_x in range(20, 180):
            color = pixels.pixelColor(round(logical_x * scale), edge_y)
            assert color == reference, (logical_x, color.name(), reference.name())
    finally:
        group.close()
