"""Turn guide Markdown into short, visual HTML for Qt text browsers.

Qt's own Markdown import gives every guide the same plain look. This renderer
supports the small Markdown subset used in ``docs/user`` and adds visual
structure: numbered step badges, coloured result/caution boxes and a
"한눈에 보기" card built from each guide's quick-help section.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field

# (source, wanted width) -> (url to display, width, height); width 0 = unknown.
ImageResolver = Callable[[str, int], tuple[str, int, int]]
ZOOM_HINT = "그림을 누르면 원래 크기로 크게 볼 수 있습니다."

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_EXPLICIT_ANCHOR = re.compile(r"\s*\{#([^}]+)\}\s*$")
_ORDERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_IMAGE_ONLY = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)\s*$")
_QUICK_LABEL = re.compile(r"^\*\*(준비|성공 확인|안 되면)\*\*\s*[:：]?\s*(.*)$")

ACCENT = "#2457c5"
TEXT = "#1f2937"
MUTED = "#5b6474"

# Sections that get a coloured box so they stand out from the steps.
_SECTION_BOXES = {
    "성공 확인": ("#ecf8ef", "#1f7a3d"),
    "주의사항": ("#fff7e6", "#9a5b00"),
    "문제 해결": ("#f4f6fa", "#344054"),
}

STYLE_SHEET = f"""
body {{ color: {TEXT}; font-size: 10pt; }}
h1 {{ font-size: 15pt; color: #111827; margin-top: 4px; margin-bottom: 6px; }}
h2 {{ font-size: 12.5pt; color: {ACCENT}; margin-top: 18px; margin-bottom: 6px; }}
h3 {{ font-size: 11pt; color: #111827; margin-top: 12px; margin-bottom: 4px; }}
h4, h5, h6 {{ font-size: 10pt; color: #111827; margin-top: 8px; margin-bottom: 2px; }}
p {{ margin-top: 3px; margin-bottom: 6px; }}
li {{ margin-bottom: 3px; }}
code {{ background-color: #eef1f6; color: #1d3f8f; }}
pre {{ background-color: #f3f5f9; padding: 6px; }}
a {{ color: {ACCENT}; }}
.lead {{ font-size: 11.5pt; color: #111827; }}
.muted {{ color: {MUTED}; }}
.caption {{ color: {MUTED}; font-size: 9pt; }}
.label {{ font-weight: 700; }}
"""


@dataclass(slots=True)
class QuickHelp:
    """The fixed shape every ``quick-*`` guide section follows."""

    summary: str = ""
    prepare: str = ""
    steps: list[str] = field(default_factory=list)
    success: str = ""
    trouble: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.steps)


def parse_quick_help(markdown: str) -> QuickHelp:
    quick = QuickHelp()
    summary_lines: list[str] = []
    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        if not line or _HEADING.match(line):
            continue
        label = _QUICK_LABEL.match(line)
        if label is not None:
            key = {"준비": "prepare", "성공 확인": "success", "안 되면": "trouble"}[label.group(1)]
            setattr(quick, key, label.group(2).strip())
            continue
        ordered = _ORDERED.match(raw_line)
        if ordered is not None and not ordered.group(1):
            quick.steps.append(ordered.group(3).strip())
            continue
        if quick.steps and raw_line.startswith((" ", "\t")):
            quick.steps[-1] += " " + line
            continue
        if not quick.steps and not quick.prepare:
            summary_lines.append(line)
    quick.summary = " ".join(summary_lines)
    return quick


def first_image(markdown: str) -> str:
    """Path of the first stand-alone image in ``markdown`` or an empty string."""
    for line in _without_fences(markdown):
        match = _IMAGE_ONLY.match(line.strip())
        if match is not None:
            return match.group(2)
    return ""


def image_html(
    source: str,
    width: int,
    *,
    alt: str = "",
    resolver: ImageResolver | None = None,
    align: str = "",
) -> str:
    """An image that links to the full-size viewer, scaled without distortion."""
    url, shown_width, shown_height = (resolver or _plain_resolver)(source, width)
    size = f' width="{shown_width}"' if shown_width else f' width="{width}"'
    if shown_height:
        size += f' height="{shown_height}"'
    align_attr = f' align="{align}"' if align else ""
    return (
        f'<p{align_attr}><a href="zoom:{html.escape(source, quote=True)}">'
        f'<img src="{html.escape(url, quote=True)}"{size} alt="{html.escape(alt, quote=True)}"></a></p>'
        f'<p{align_attr} class="caption">{html.escape(alt + " · " if alt else "")}{ZOOM_HINT}</p>'
    )


def _plain_resolver(source: str, width: int) -> tuple[str, int, int]:
    return source, 0, 0


def quick_help_card_html(
    quick: QuickHelp,
    *,
    image_src: str = "",
    image_width: int = 0,
    heading: str = "",
    image_resolver: ImageResolver | None = None,
) -> str:
    """A compact visual card: picture, numbered steps, result and fallback."""
    parts: list[str] = [
        '<table width="100%" cellspacing="0" cellpadding="12" bgcolor="#f3f7ff" '
        'style="border: 1px solid #d5e1fb;"><tr><td>'
    ]
    if heading:
        parts.append(f'<p class="label" style="color: {ACCENT};">{html.escape(heading)}</p>')
    if quick.summary:
        parts.append(f'<p class="lead"><b>{inline_html(quick.summary)}</b></p>')
    if quick.prepare:
        parts.append(
            f'<p><span class="label">준비</span>&nbsp; {inline_html(quick.prepare)}</p>'
        )
    if quick.steps:
        parts.append(_steps_table(quick.steps))
    if quick.success:
        parts.append(_note_box("성공 확인", quick.success, "#ecf8ef", "#1f7a3d"))
    if quick.trouble:
        parts.append(_note_box("안 되면", quick.trouble, "#fff7e6", "#9a5b00"))
    # Steps come first; the screenshot below shows where each control is.
    if image_src and image_width > 0:
        parts.append('<p class="label" style="margin-top: 10px;">화면 위치</p>')
        parts.append(image_html(image_src, image_width, resolver=image_resolver))
    parts.append("</td></tr></table>")
    return "".join(parts)


def markdown_to_html(
    markdown: str,
    *,
    image_width: int = 560,
    skip_title: bool = False,
    image_resolver: ImageResolver | None = None,
) -> str:
    """Render guide Markdown with step badges and coloured section boxes."""
    renderer = _Renderer(
        image_width=image_width, skip_title=skip_title, image_resolver=image_resolver
    )
    return renderer.render(str(markdown or ""))


def heading_anchor_names(markdown: str) -> dict[str, str]:
    """Map every explicit ID and slug of a heading to the anchor name used in HTML."""
    names: dict[str, str] = {}
    seen: dict[str, int] = {}
    for line in _without_fences(markdown):
        match = _HEADING.match(line)
        if match is None:
            continue
        title, explicit = _split_heading(match.group(2))
        slug = _unique_slug(title, seen)
        primary = explicit or slug
        names[primary.casefold()] = primary
        names[slug.casefold()] = primary
    return names


def section_titles(markdown: str, level: int = 2) -> list[tuple[str, str]]:
    """``(anchor, title)`` for headings of ``level`` — used for jump links."""
    titles: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    for line in _without_fences(markdown):
        match = _HEADING.match(line)
        if match is None:
            continue
        title, explicit = _split_heading(match.group(2))
        slug = _unique_slug(title, seen)
        if len(match.group(1)) == level:
            titles.append((explicit or slug, _plain(title)))
    return titles


def inline_html(text: str) -> str:
    """Escape text and convert inline code, bold, links and images."""
    tokens: list[str] = []

    def stash(value: str) -> str:
        tokens.append(value)
        return f"\x00{len(tokens) - 1}\x00"

    text = re.sub(r"`([^`]+)`", lambda m: stash(f"<code>&nbsp;{html.escape(m.group(1))}&nbsp;</code>"), text)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)\)",
        lambda m: stash(f'<img src="{html.escape(m.group(2), quote=True)}" alt="{html.escape(m.group(1), quote=True)}">'),
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: stash(
            f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>'
        ),
        text,
    )
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: tokens[int(m.group(1))], text)


def _steps_table(steps: list[str], nested: list[str] | None = None) -> str:
    rows = []
    for number, step in enumerate(steps, start=1):
        extra = nested[number - 1] if nested and number - 1 < len(nested) else ""
        rows.append(
            "<tr>"
            '<td width="30" valign="top">'
            f'<table cellspacing="0" cellpadding="4" bgcolor="{ACCENT}"><tr>'
            f'<td width="22" align="center" style="color: #ffffff; font-weight: 700;">{number}</td>'
            "</tr></table></td>"
            f'<td valign="top" style="padding-left: 8px;">{inline_html(step)}{extra}</td>'
            "</tr>"
        )
    return (
        '<table cellspacing="5" cellpadding="4" width="100%" style="margin-top: 4px;">'
        + "".join(rows)
        + "</table>"
    )


def _note_box(label: str, text: str, background: str, color: str) -> str:
    return (
        f'<table width="100%" cellspacing="0" cellpadding="8" bgcolor="{background}" '
        'style="margin-top: 6px;"><tr><td>'
        f'<span class="label" style="color: {color};">{html.escape(label)}</span>&nbsp; '
        f"{inline_html(text)}</td></tr></table>"
    )


class _Renderer:
    def __init__(
        self,
        *,
        image_width: int,
        skip_title: bool,
        image_resolver: ImageResolver | None = None,
    ) -> None:
        self.image_width = image_width
        self.image_resolver = image_resolver
        self.skip_title = skip_title
        self.out: list[str] = []
        self.paragraph: list[str] = []
        self.seen_slugs: dict[str, int] = {}
        self.box_open = False

    def render(self, markdown: str) -> str:
        lines = markdown.replace("\r\n", "\n").split("\n")
        index = 0
        first_heading = True
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            fence = re.match(r"^\s*(`{3,}|~{3,})", line)
            if fence:
                self._flush_paragraph()
                marker = fence.group(1)
                body: list[str] = []
                index += 1
                while index < len(lines) and not lines[index].strip().startswith(marker):
                    body.append(lines[index])
                    index += 1
                self.out.append(f"<pre>{html.escape(chr(10).join(body))}</pre>")
                index += 1
                continue
            heading = _HEADING.match(line)
            if heading:
                self._flush_paragraph()
                level = len(heading.group(1))
                title, explicit = _split_heading(heading.group(2))
                slug = _unique_slug(title, self.seen_slugs)
                if level == 1 and first_heading and self.skip_title:
                    first_heading = False
                    index += 1
                    continue
                first_heading = False
                self._heading(level, title, explicit or slug)
                index += 1
                continue
            if not stripped:
                self._flush_paragraph()
                index += 1
                continue
            if stripped.startswith("|") and index + 1 < len(lines) and re.match(
                r"^\s*\|?\s*:?-{2,}", lines[index + 1]
            ):
                self._flush_paragraph()
                index = self._table(lines, index)
                continue
            if _ORDERED.match(line) or _BULLET.match(line):
                self._flush_paragraph()
                index = self._list(lines, index)
                continue
            image = _IMAGE_ONLY.match(stripped)
            if image:
                self._flush_paragraph()
                self._image(image.group(2), image.group(1))
                index += 1
                continue
            self.paragraph.append(stripped)
            index += 1
        self._flush_paragraph()
        self._close_box()
        return "".join(self.out)

    def _heading(self, level: int, title: str, anchor: str) -> None:
        if level <= 2:
            self._close_box()
        name = html.escape(anchor, quote=True)
        self.out.append(f'<h{level}><a name="{name}">{inline_html(title)}</a></h{level}>')
        plain = _plain(title)
        if level == 2 and plain in _SECTION_BOXES:
            background, _color = _SECTION_BOXES[plain]
            self.out.append(
                f'<table width="100%" cellspacing="0" cellpadding="10" bgcolor="{background}">'
                "<tr><td>"
            )
            self.box_open = True

    def _close_box(self) -> None:
        if self.box_open:
            self._flush_paragraph()
            self.out.append("</td></tr></table>")
            self.box_open = False

    def _flush_paragraph(self) -> None:
        if self.paragraph:
            self.out.append(f"<p>{inline_html(' '.join(self.paragraph))}</p>")
            self.paragraph = []

    def _image(self, src: str, alt: str) -> None:
        self.out.append(
            image_html(src, self.image_width, alt=alt, resolver=self.image_resolver, align="center")
        )

    def _table(self, lines: list[str], index: int) -> int:
        header = _table_cells(lines[index])
        index += 2
        rows: list[list[str]] = []
        while index < len(lines) and lines[index].strip().startswith("|"):
            rows.append(_table_cells(lines[index]))
            index += 1
        head_html = "".join(
            f'<th bgcolor="#eef2f8" align="left">{inline_html(cell)}</th>' for cell in header
        )
        body_html = "".join(
            "<tr>" + "".join(f"<td>{inline_html(cell)}</td>" for cell in row) + "</tr>"
            for row in rows
        )
        self.out.append(
            '<table cellspacing="0" cellpadding="5" border="1" '
            'style="border-color: #d9dee7; border-style: solid;">'
            f"<tr>{head_html}</tr>{body_html}</table>"
        )
        return index

    def _list(self, lines: list[str], index: int) -> int:
        items, index = _collect_list(lines, index)
        self.out.append(self._list_html(items, top_level=True))
        return index

    def _list_html(self, items: list[_ListItem], *, top_level: bool) -> str:
        if not items:
            return ""
        ordered = items[0].ordered
        if ordered and top_level:
            nested = [self._children_html(item) for item in items]
            return _steps_table([item.text for item in items], nested)
        tag = "ol" if ordered else "ul"
        body = "".join(
            f"<li>{inline_html(item.text)}{self._children_html(item)}</li>" for item in items
        )
        return f"<{tag}>{body}</{tag}>"

    def _children_html(self, item: _ListItem) -> str:
        return self._list_html(item.children, top_level=False) if item.children else ""


@dataclass(slots=True)
class _ListItem:
    text: str
    ordered: bool
    indent: int
    children: list[_ListItem] = field(default_factory=list)


def _collect_list(lines: list[str], index: int) -> tuple[list[_ListItem], int]:
    roots: list[_ListItem] = []
    stack: list[_ListItem] = []
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            # A blank line ends the list unless the next line continues it.
            nxt = lines[index + 1] if index + 1 < len(lines) else ""
            if not (_ORDERED.match(nxt) or _BULLET.match(nxt) or (nxt.startswith("  ") and stack)):
                index += 1
                break
            index += 1
            continue
        ordered = _ORDERED.match(line)
        bullet = _BULLET.match(line)
        if ordered or bullet:
            indent = len((ordered or bullet).group(1).expandtabs(4))
            text = ordered.group(3) if ordered else bullet.group(2)
            item = _ListItem(text=text.strip(), ordered=bool(ordered), indent=indent)
            while stack and stack[-1].indent >= indent:
                stack.pop()
            (stack[-1].children if stack else roots).append(item)
            stack.append(item)
            index += 1
            continue
        if line.startswith((" ", "\t")) and stack:
            stack[-1].text += " " + line.strip()
            index += 1
            continue
        break
    return roots, index


def _table_cells(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [cell.strip() for cell in cells]


def _split_heading(raw_title: str) -> tuple[str, str]:
    explicit = _EXPLICIT_ANCHOR.search(raw_title)
    if explicit is None:
        return raw_title.strip(), ""
    return raw_title[: explicit.start()].strip(), explicit.group(1).strip()


def _plain(value: str) -> str:
    value = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    return re.sub(r"[`*_~]", "", value).strip()


def _unique_slug(title: str, seen: dict[str, int]) -> str:
    slug = re.sub(r"[^\w\s-]", "", _plain(title).casefold())
    slug = re.sub(r"[\s-]+", "-", slug).strip("-")
    count = seen.get(slug, 0)
    seen[slug] = count + 1
    return f"{slug}-{count}" if count else slug


def _without_fences(markdown: str):
    fence = ""
    for line in str(markdown or "").splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            fence = "" if fence else marker.group(1)
            continue
        if not fence:
            yield line
