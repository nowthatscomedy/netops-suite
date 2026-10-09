from __future__ import annotations

import html
import re
from collections.abc import Callable

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.guides.catalog import GuideCatalog, GuideEntry, extract_heading_section
from app.guides.images import ImageZoomDialog, image_path_from_zoom_url, register_scaled_image
from app.guides.render import (
    STYLE_SHEET,
    first_image,
    heading_anchor_names,
    markdown_to_html,
    parse_quick_help,
    quick_help_card_html,
)
from netops_suite.ui.actions import make_action_button


_GUIDE_ROLE = int(Qt.ItemDataRole.UserRole)
_HEADING_PATTERN = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_EXPLICIT_ANCHOR_PATTERN = re.compile(r"\s*\{#([^}]+)\}\s*$")


class GuideDialog(QDialog):
    """Non-modal, offline help browser backed by :class:`GuideCatalog`."""

    def __init__(
        self,
        catalog: GuideCatalog,
        *,
        context_provider: Callable[[], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self._context_provider = context_provider
        self._current_entry: GuideEntry | None = None
        self._current_markdown = ""
        self._heading_targets: dict[str, str] = {}
        self._anchor_names: dict[str, str] = {}
        self._tree_items: dict[str, QTreeWidgetItem] = {}
        self._direct_search_matches: tuple[str, ...] = ()
        self._rebuilding_tree = False

        self.setObjectName("guideDialog")
        self.setWindowTitle("NetOps Suite 사용자 가이드")
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumSize(760, 520)
        self.resize(*_initial_size())
        self._build_ui()
        self._connect_signals()
        self._rebuild_tree("")

    @property
    def current_entry(self) -> GuideEntry | None:
        return self._current_entry

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        title = QLabel("사용자 가이드")
        title.setObjectName("guideDialogTitle")
        self.welcome_button = make_action_button(
            "처음 시작", object_name="guideWelcomeButton"
        )
        self.context_button = make_action_button(
            "현재 화면",
            object_name="guideContextButton",
            enabled=self._context_provider is not None,
        )
        toolbar.addWidget(title)
        toolbar.addStretch(1)
        toolbar.addWidget(self.welcome_button)
        toolbar.addWidget(self.context_button)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("guideSplitter")

        index_panel = QFrame()
        index_panel.setObjectName("guideIndexPanel")
        index_layout = QVBoxLayout(index_panel)
        index_layout.setContentsMargins(8, 8, 8, 8)
        index_layout.setSpacing(8)
        search_label = QLabel("가이드 검색")
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("guideSearchEdit")
        self.search_edit.setAccessibleName("사용자 가이드 검색")
        self.search_edit.setPlaceholderText("기능명 또는 작업 검색")
        self.search_edit.setClearButtonEnabled(True)
        self.tree = QTreeWidget()
        self.tree.setObjectName("guideTree")
        self.tree.setAccessibleName("사용자 가이드 목차")
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        index_layout.addWidget(search_label)
        index_layout.addWidget(self.search_edit)
        index_layout.addWidget(self.tree, 1)

        content_panel = QFrame()
        content_panel.setObjectName("guideContentPanel")
        content_layout = QVBoxLayout(content_panel)
        content_layout.setContentsMargins(10, 8, 8, 8)
        content_layout.setSpacing(6)
        self.document_title = QLabel()
        self.document_title.setObjectName("guideDocumentTitle")
        self.document_title.setWordWrap(True)
        # Two views: a short, picture-first walkthrough and the full reference.
        self.view_tabs = QTabBar()
        self.view_tabs.setObjectName("guideViewTabs")
        self.view_tabs.setDrawBase(False)
        self.view_tabs.setExpanding(False)
        self.view_tabs.addTab("따라 하기")
        self.view_tabs.addTab("자세한 설명")
        self.view_tabs.setAccessibleName("도움말 보기 방식")
        self.card_browser = _make_browser("guideCardBrowser", "따라 하기 안내")
        self.browser = _make_browser("guideBrowser", "사용자 가이드 내용")
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.card_browser)
        self.view_stack.addWidget(self.browser)
        self.status_label = QLabel()
        self.status_label.setObjectName("guideStatusLabel")
        self.status_label.setWordWrap(True)
        content_layout.addWidget(self.document_title)
        content_layout.addWidget(self.view_tabs)
        content_layout.addWidget(self.view_stack, 1)
        content_layout.addWidget(self.status_label)

        splitter.addWidget(index_panel)
        splitter.addWidget(content_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, max(560, self.width() - 270)])
        layout.addWidget(splitter, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        layout.addWidget(buttons)
        self._close_buttons = buttons

        self.setStyleSheet(
            """
            QDialog#guideDialog { background: #f7f7f5; }
            QLabel#guideDialogTitle { color: #111827; font-size: 16px; font-weight: 700; }
            QLabel#guideDocumentTitle { color: #111827; font-size: 15px; font-weight: 700;
                                        padding: 3px 2px 7px 2px; }
            QFrame#guideIndexPanel, QFrame#guideContentPanel { background: #ffffff;
                border: 1px solid #e4e7ec; border-radius: 5px; }
            QTreeWidget#guideTree { border: 0; background: #ffffff; }
            QTreeWidget#guideTree::item { padding: 4px; }
            QTreeWidget#guideTree::item:selected { background: #e4e2dd; color: #111827; }
            QTextBrowser#guideBrowser, QTextBrowser#guideCardBrowser {
                background: #ffffff; border: 0; padding: 8px; }
            QTabBar#guideViewTabs::tab { padding: 6px 16px; margin-right: 4px; color: #475467;
                border: 1px solid #d5dbe6; border-radius: 6px; background: #ffffff; }
            QTabBar#guideViewTabs::tab:selected { color: #ffffff; background: #2457c5;
                border-color: #2457c5; font-weight: 700; }
            QLabel#guideStatusLabel { color: #667085; padding: 3px; }
            """
        )

    def _connect_signals(self) -> None:
        self.search_edit.textChanged.connect(self._rebuild_tree)
        self.search_edit.returnPressed.connect(self._open_first_search_result)
        self.tree.currentItemChanged.connect(self._handle_tree_selection)
        self.browser.anchorClicked.connect(self._handle_link)
        self.card_browser.anchorClicked.connect(self._handle_link)
        self.view_tabs.currentChanged.connect(self.view_stack.setCurrentIndex)
        self.welcome_button.clicked.connect(self.open_welcome)
        self.context_button.clicked.connect(self.open_current_context)
        self._close_buttons.rejected.connect(self.close)

    def open_guide(self, guide_id: str, anchor: str = "") -> bool:
        entry = self.catalog.resolve(guide_id)
        if entry is None:
            self._show_unavailable(
                f"요청한 가이드 '{guide_id}'를 찾을 수 없습니다."
            )
            self._show_non_modal()
            return False
        self._select_tree_entry(entry.id)
        self._render_entry(entry, anchor=anchor or entry.anchor)
        self._show_non_modal()
        return True

    def open_welcome(self) -> bool:
        entry = self.catalog.welcome_entry()
        if entry is None:
            self._show_unavailable()
            self._show_non_modal()
            return False
        return self.open_guide(entry.id, entry.anchor)

    def open_current_context(self) -> bool:
        if self._context_provider is None:
            return self.open_welcome()
        return self.open_guide(self._context_provider())

    def _show_non_modal(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _rebuild_tree(self, query: str) -> None:
        query = str(query or "").strip()
        current_id = self._current_entry.id if self._current_entry is not None else ""
        direct_matches = tuple(entry.id for entry in self.catalog.search(query))
        self._direct_search_matches = direct_matches if query else ()
        matches = set(direct_matches)
        if query:
            by_id = {entry.id: entry for entry in self.catalog.entries}
            for guide_id in tuple(matches):
                parent_id = by_id.get(guide_id).parent_id if guide_id in by_id else ""
                visited: set[str] = set()
                while parent_id and parent_id not in visited:
                    visited.add(parent_id)
                    parent = by_id.get(parent_id)
                    if parent is None:
                        break
                    matches.add(parent_id)
                    parent_id = parent.parent_id

        self._rebuilding_tree = True
        self.tree.clear()
        self._tree_items = {}
        display_entries = self.catalog.search(query) if query else self.catalog.entries
        # Include ancestors for orientation without ranking them as direct hits.
        display_entries = (*display_entries, *(entry for entry in self.catalog.entries
                            if entry.id in matches and entry.id not in direct_matches))
        for entry in display_entries:
            if entry.id not in matches:
                continue
            item = QTreeWidgetItem([entry.title])
            item.setData(0, _GUIDE_ROLE, entry.id)
            item.setToolTip(0, entry.title)
            self._tree_items[entry.id] = item

        for entry in display_entries:
            item = self._tree_items.get(entry.id)
            if item is None:
                continue
            parent_item = self._tree_items.get(entry.parent_id)
            if parent_item is not None and parent_item is not item:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)

        if not self._tree_items and query:
            empty_item = QTreeWidgetItem(["검색 결과가 없습니다."])
            empty_item.setDisabled(True)
            self.tree.addTopLevelItem(empty_item)
        if query:
            self.tree.expandAll()
        else:
            self.tree.collapseAll()
        self._rebuilding_tree = False
        if current_id in self._tree_items:
            self._select_tree_entry(current_id)

    def _open_first_search_result(self) -> None:
        for guide_id in self._direct_search_matches:
            item = self._tree_items.get(guide_id)
            if item is not None:
                self.tree.setCurrentItem(item)
                return
        item = self.tree.topLevelItem(0)
        while item is not None and not item.data(0, _GUIDE_ROLE) and item.childCount():
            item = item.child(0)
        if item is not None and item.data(0, _GUIDE_ROLE):
            self.tree.setCurrentItem(item)

    def _select_tree_entry(self, guide_id: str) -> None:
        item = self._tree_items.get(guide_id)
        if item is None:
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
            self._rebuild_tree("")
            item = self._tree_items.get(guide_id)
        if item is None:
            return
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(item)
        if not self.search_edit.text().strip():
            self.tree.collapseAll()
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        if item.childCount():
            item.setExpanded(True)
        self.tree.scrollToItem(item)
        self.tree.blockSignals(False)

    def _handle_tree_selection(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if self._rebuilding_tree or current is None:
            return
        guide_id = str(current.data(0, _GUIDE_ROLE) or "")
        entry = self.catalog.get(guide_id)
        if entry is not None:
            self._select_tree_entry(entry.id)
            self._render_entry(entry, anchor=entry.anchor)

    def _render_entry(self, entry: GuideEntry, *, anchor: str = "") -> None:
        markdown, error = self.catalog.read_markdown(entry)
        self._current_entry = entry
        self.document_title.setText(entry.title)
        if markdown is None:
            self._current_markdown = ""
            self._heading_targets = {}
            self._anchor_names = {}
            self._show_unavailable(error or "가이드 문서를 읽을 수 없습니다.", entry=entry)
            return

        detail = self._detail_markdown(entry, markdown)
        self._current_markdown = detail
        self._heading_targets = _heading_targets(detail)
        self._anchor_names = heading_anchor_names(detail)
        base_dir = entry.content_path.parent
        base_url = QUrl.fromLocalFile(str(base_dir.resolve(strict=False)) + "/")
        image_width = max(320, min(900, self.view_stack.width() - 60))
        ratio = self.devicePixelRatioF()

        def resolver(browser: QTextBrowser):
            def resolve(source: str, wanted: int) -> tuple[str, int, int]:
                return register_scaled_image(browser.document(), base_dir, source, wanted, ratio)
            return resolve

        self.browser.document().setBaseUrl(base_url)
        self.browser.setHtml(
            markdown_to_html(
                detail,
                image_width=image_width,
                skip_title=True,
                image_resolver=resolver(self.browser),
            )
        )
        self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        self.card_browser.document().setBaseUrl(base_url)
        # The card adds its own padding and border around the picture.
        card = self._quick_card(
            entry, markdown, detail, image_width - 48, resolver(self.card_browser)
        )
        self.card_browser.setHtml(card)
        self.card_browser.moveCursor(QTextCursor.MoveOperation.Start)
        self.view_tabs.setVisible(bool(card))
        self.status_label.setText("")
        deep_link = bool(anchor) and anchor != entry.anchor
        self._show_view(1 if deep_link or not card else 0)
        if deep_link or (anchor and not card):
            self._scroll_to_anchor(anchor)

    def _show_view(self, index: int) -> None:
        self.view_tabs.setCurrentIndex(index)
        self.view_stack.setCurrentIndex(index)

    def _is_topic(self, entry: GuideEntry) -> bool:
        """True when the entry is one topic inside a document shared with others."""
        owners = [
            other
            for other in self.catalog.entries
            if other.content_path == entry.content_path
        ]
        return len(owners) > 1 and entry.parent_id in {other.id for other in owners}

    def _detail_markdown(self, entry: GuideEntry, markdown: str) -> str:
        """The reference text for one task, without the quick-help cards."""
        if self._is_topic(entry):
            topic = extract_heading_section(markdown, entry.anchor)
            if topic:
                return topic
        detail = markdown
        for other in self.catalog.entries:
            if other.content_path != entry.content_path or not other.quick_help_anchor:
                continue
            section = extract_heading_section(detail, other.quick_help_anchor)
            if section:
                detail = detail.replace(section, "")
        return detail.strip() + "\n"

    def _quick_card(
        self,
        entry: GuideEntry,
        markdown: str,
        detail: str,
        image_width: int,
        image_resolver,
    ) -> str:
        """Picture first, then numbered steps: the default view of every task."""
        if not entry.quick_help_anchor:
            return ""
        quick_markdown, _error = self.catalog.read_quick_help(entry)
        quick = parse_quick_help(quick_markdown or "")
        if not quick.is_complete:
            return ""
        image = first_image(detail) or first_image(markdown)
        return quick_help_card_html(
            quick,
            image_src=image,
            image_width=image_width,
            image_resolver=image_resolver,
        ) + (
            '<p class="muted">더 알고 싶으면 위의 <b>자세한 설명</b>을 누르세요. '
            "입력 항목, 저장 위치, 주의사항과 문제 해결이 있습니다.</p>"
        )

    def _show_unavailable(
        self,
        message: str = "가이드 파일을 불러올 수 없습니다.",
        *,
        entry: GuideEntry | None = None,
    ) -> None:
        self._current_entry = entry
        if entry is None:
            self.document_title.setText("사용자 가이드를 사용할 수 없습니다")
        details = [message, *self.catalog.errors]
        detail_html = "".join(
            f"<li>{html.escape(detail)}</li>" for detail in dict.fromkeys(details) if detail
        )
        self.browser.setHtml(
            "<h2>가이드 파일을 불러올 수 없습니다.</h2>"
            "<p>NetOps Suite의 다른 기능은 계속 사용할 수 있습니다.</p>"
            f"<ul>{detail_html}</ul>"
            "<p>설치본은 복구 또는 재설치하고, 소스 실행 환경은 가이드 번들을 "
            "다시 생성한 뒤 도움말을 열어 주세요.</p>"
        )
        self.status_label.setText("가이드 로드 실패 — 애플리케이션 기능에는 영향을 주지 않습니다.")

    def _handle_link(self, url: QUrl) -> None:
        if self._current_entry is not None:
            image = image_path_from_zoom_url(url, self._current_entry.content_path.parent)
            if image is not None:
                ImageZoomDialog(image, self._current_entry.title, self).exec()
                return
        scheme = url.scheme().casefold()
        fragment = url.fragment()
        if scheme == "guide":
            target = url.path().lstrip("/") or url.host()
            if not target:
                target = url.toString().split(":", 1)[-1].split("#", 1)[0]
            self.open_guide(target, fragment)
            return
        if scheme:
            self.status_label.setText(
                f"외부 링크는 앱에서 자동으로 열지 않습니다: {url.toString()}"
            )
            return
        if self._current_entry is None:
            return
        if not url.path():
            self._show_view(1)
            self._scroll_to_anchor(fragment)
            return

        linked_entry = self.catalog.entry_for_link(
            self._current_entry, url.path(), fragment
        )
        if linked_entry is not None:
            self._select_tree_entry(linked_entry.id)
            self._render_entry(linked_entry, anchor=fragment or linked_entry.anchor)
            return

        route_entry = self.catalog.resolve(url.path().strip("/"))
        if route_entry is not None:
            self._select_tree_entry(route_entry.id)
            self._render_entry(route_entry, anchor=fragment or route_entry.anchor)
            return
        self.status_label.setText(f"연결된 가이드를 찾을 수 없습니다: {url.toString()}")

    def _scroll_to_anchor(self, anchor: str) -> None:
        anchor = str(anchor or "").strip()
        if not anchor:
            return
        name = self._anchor_names.get(anchor.casefold()) or self._anchor_names.get(
            _slugify(self._heading_targets.get(anchor.casefold(), "") or anchor)
        )
        position = _anchor_position(self.browser.document(), name) if name else -1
        if position < 0:
            heading_text = self._heading_targets.get(anchor.casefold())
            if not heading_text:
                return
            found = self.browser.document().find(heading_text)
            if found.isNull():
                return
            position = found.selectionStart()
        cursor = self.browser.textCursor()
        cursor.setPosition(position)
        self.browser.setTextCursor(cursor)
        self.browser.ensureCursorVisible()
        self.browser.scrollToAnchor(name or anchor)


def _make_browser(object_name: str, accessible_name: str) -> QTextBrowser:
    browser = QTextBrowser()
    browser.setObjectName(object_name)
    browser.setAccessibleName(accessible_name)
    browser.setOpenLinks(False)
    browser.setOpenExternalLinks(False)
    browser.setReadOnly(True)
    browser.document().setDefaultStyleSheet(STYLE_SHEET)
    return browser


def _initial_size() -> tuple[int, int]:
    """Large enough for readable screenshots, never larger than the screen."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 1180, 820
    available = screen.availableGeometry()
    return (
        max(760, min(1240, int(available.width() * 0.9))),
        max(520, min(860, int(available.height() * 0.9))),
    )


def _anchor_position(document: QTextDocument, name: str) -> int:
    target = name.casefold()
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and any(
                value.casefold() == target for value in fragment.charFormat().anchorNames()
            ):
                return fragment.position()
            iterator += 1
        block = block.next()
    return -1


def _heading_targets(markdown: str) -> dict[str, str]:
    targets: dict[str, str] = {}
    for line in markdown.splitlines():
        match = _HEADING_PATTERN.match(line)
        if match is None:
            continue
        heading = match.group(2).strip()
        explicit = _EXPLICIT_ANCHOR_PATTERN.search(heading)
        if explicit is not None:
            heading = heading[: explicit.start()].strip()
            targets[explicit.group(1).casefold()] = _plain_heading(heading)
        plain = _plain_heading(heading)
        targets[_slugify(plain)] = plain
    return targets


def _display_markdown(markdown: str) -> str:
    lines: list[str] = []
    for line in markdown.splitlines(keepends=True):
        heading = _HEADING_PATTERN.match(line.rstrip("\r\n"))
        if heading is None:
            lines.append(line)
            continue
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        visible_heading = _EXPLICIT_ANCHOR_PATTERN.sub("", heading.group(2)).rstrip()
        lines.append(f"{heading.group(1)} {visible_heading}{newline}")
    return "".join(lines)


def _plain_heading(value: str) -> str:
    value = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"[`*_~]", "", value)
    return value.strip()


def _slugify(value: str) -> str:
    value = _plain_heading(value).casefold()
    value = re.sub(r"[^\w\s가-힣-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s-]+", "-", value).strip("-")
    return value
