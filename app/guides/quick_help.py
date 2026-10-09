from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QLabel, QTextBrowser, QVBoxLayout, QWidget

from app.guides.catalog import GuideCatalog, GuideEntry
from app.guides.images import ImageZoomDialog, image_path_from_zoom_url, register_scaled_image
from app.guides.render import (
    STYLE_SHEET,
    first_image,
    markdown_to_html,
    parse_quick_help,
    quick_help_card_html,
)
from netops_suite.ui.actions import make_action_button


class QuickHelpPanel(QWidget):
    """A compact, offline explanation for the current task."""

    full_guide_requested = Signal(str)

    def __init__(self, catalog: GuideCatalog, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self._current_entry: GuideEntry | None = None
        self._rendered_width = 0
        self.setObjectName("quickHelpPanel")
        layout = QVBoxLayout(self)
        self.title_label = QLabel("이 화면 도움말")
        self.title_label.setObjectName("quickHelpTitle")
        self.title_label.setWordWrap(True)
        self.browser = QTextBrowser()
        self.browser.setObjectName("quickHelpBrowser")
        self.browser.setAccessibleName("현재 작업의 짧은 도움말")
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.document().setDefaultStyleSheet(STYLE_SHEET)
        self.browser.anchorClicked.connect(self._open_link)
        self.full_guide_button = make_action_button("자세한 설명 보기", enabled=False)
        self.full_guide_button.setObjectName("quickHelpFullGuideButton")
        self.full_guide_button.setToolTip("이 작업의 전체 설명과 문제 해결을 엽니다.")
        self.full_guide_button.clicked.connect(self._open_full_guide)
        layout.addWidget(self.title_label)
        layout.addWidget(self.browser, 1)
        layout.addWidget(self.full_guide_button)

    @property
    def current_entry(self) -> GuideEntry | None:
        return self._current_entry

    def show_guide(self, guide_id: str) -> bool:
        entry = self.catalog.resolve(guide_id)
        self._current_entry = entry
        self.full_guide_button.setEnabled(entry is not None)
        if entry is None:
            self.title_label.setText("이 화면 도움말")
            self.browser.setPlainText("이 화면의 도움말을 찾을 수 없습니다.")
            return False
        self.title_label.setText(entry.title)
        return self._render(entry)

    def _render(self, entry: GuideEntry) -> bool:
        markdown, error = self.catalog.read_quick_help(entry)
        if markdown is None:
            self.browser.setPlainText(error or "도움말을 불러올 수 없습니다.")
            return False
        base_dir = entry.content_path.parent
        self.browser.document().setBaseUrl(
            QUrl.fromLocalFile(str(base_dir.resolve(strict=False)) + "/")
        )
        width = self._image_width()
        self._rendered_width = width
        ratio = self.devicePixelRatioF()

        def resolve(source: str, wanted: int) -> tuple[str, int, int]:
            return register_scaled_image(self.browser.document(), base_dir, source, wanted, ratio)

        quick = parse_quick_help(markdown)
        if quick.is_complete:
            topic, _error = self.catalog.read_topic(entry)
            image = first_image(topic or "")
            self.browser.setHtml(
                quick_help_card_html(
                    quick, image_src=image, image_width=width, image_resolver=resolve
                )
            )
        else:
            self.browser.setHtml(
                markdown_to_html(
                    markdown, image_width=width, skip_title=True, image_resolver=resolve
                )
            )
        self.browser.verticalScrollBar().setValue(0)
        return True

    def _image_width(self) -> int:
        viewport = self.browser.viewport().width()
        return max(220, min(520, viewport - 48))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if self._current_entry is not None and abs(self._image_width() - self._rendered_width) > 24:
            self._render(self._current_entry)

    def _open_full_guide(self) -> None:
        if self._current_entry is not None:
            self.full_guide_requested.emit(self._current_entry.id)

    def _open_link(self, url: QUrl) -> None:
        if self._current_entry is not None:
            image = image_path_from_zoom_url(url, self._current_entry.content_path.parent)
            if image is not None:
                ImageZoomDialog(image, self._current_entry.title, self).exec()
                return
        if url.scheme() == "guide":
            target = url.path().lstrip("/") or url.host()
            entry = self.catalog.resolve(target)
            if entry is not None:
                self.full_guide_requested.emit(entry.id)
