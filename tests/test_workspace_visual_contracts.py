from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QScrollArea

from app.models.network_models import NetworkAdapterInfo
from app.ui.common.theme import apply_app_theme
from app.ui.home import HomePage
from app.ui.tabs.interface_tab import InterfaceTab
from netops_suite.ui.icons import PAGE_ICONS, icon


def test_navigation_icons_are_available_offline_at_scaled_sizes(qapp):
    for name in PAGE_ICONS.values():
        asset = icon(name, "#2563eb", 24)
        assert not asset.isNull(), name
        for pixels in (24, 30, 36, 48):
            assert not asset.pixmap(pixels, pixels).isNull(), name


@pytest.mark.parametrize("height", [550, 700])
def test_manual_ip_fields_fit_their_container_after_reopening(qapp, height):
    apply_app_theme(qapp)
    state = SimpleNamespace(
        is_admin=True, ip_profiles=[], thread_pool=QThreadPool.globalInstance(),
        config_reloaded=SimpleNamespace(connect=lambda *_: None),
        admin_status_changed=SimpleNamespace(connect=lambda *_: None),
    )
    tab = InterfaceTab(state)
    tab.resize(800, height)
    tab.show()
    tab._populate_adapter_table([
        NetworkAdapterInfo("Ethernet", "Test adapter", "", "Up", ipv4="192.0.2.10", prefix_length=24)
    ])
    for _ in range(3):
        tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("dhcp"))
        tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("static"))
        qapp.processEvents()
        for field in (tab.ip_edit, tab.prefix_edit, tab.gateway_edit, tab.dns_edit):
            assert tab.static_fields.rect().contains(field.geometry()), field.objectName()
        assert tab.dns_edit.viewport().height() >= tab.dns_edit.fontMetrics().lineSpacing() * 2 + 4
        tab.profile_section.setExpanded(True)
        qapp.processEvents()
        for area in tab.findChildren(QScrollArea):
            if area.isAncestorOf(tab.dns_edit):
                area.ensureWidgetVisible(tab.dns_edit)
        qapp.processEvents()
        assert not tab.dns_edit.visibleRegion().isEmpty()
        tab.profile_section.setExpanded(False)
    tab.close()


def test_home_cards_fit_compact_window_without_overlap(qapp):
    apply_app_theme(qapp)
    page = HomePage()
    page.resize(800, 620)
    page.show()
    qapp.processEvents()
    for index, card in enumerate(page.cards):
        for sibling in page.cards[index + 1:]:
            assert not card.geometry().intersects(sibling.geometry())
        button = page.task_buttons[page.TASKS[index][0]]
        page.scroll_area.ensureWidgetVisible(button)
        qapp.processEvents()
        assert not button.visibleRegion().isEmpty()
        assert card.rect().contains(button.geometry())
    page.close()
