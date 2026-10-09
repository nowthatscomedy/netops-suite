from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl

from app.guides import GuideCatalog, GuideDialog, GuideEntry, QuickHelpPanel
from app.guides.catalog import extract_heading_section
from scripts.generate_guides import _build_search_index
from scripts.validate_guides import validate_repository


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _catalog(tmp_path: Path) -> GuideCatalog:
    path = tmp_path / "transfer.md"
    path.write_text(
        "# Transfer\n\n## Overview {#overview}\nChoose a file task.\n\n"
        "## FTP {#ftp}\nFTP description.\n\n"
        "### More\nFTP-only detail.\n\n"
        "## SCP {#scp}\nSSH-copy needle.\n\n"
        "## FTP quick {#quick-ftp}\nSend FTP files.\n\n"
        "## SCP quick {#quick-scp}\nCheck SSH identity before copying.\n",
        encoding="utf-8",
    )
    root = GuideEntry("transfer", "File transfer", "ko", "transfer.md", path,
                      anchor="overview")
    ftp = replace(root, id="ftp", title="FTP", anchor="ftp", parent_id="transfer",
                  keywords=("file upload",), quick_help_anchor="quick-ftp")
    scp = replace(root, id="scp", title="SCP", anchor="scp", parent_id="transfer",
                  keywords=("SSH copy",), quick_help_anchor="quick-scp")
    return GuideCatalog((root, ftp, scp), project_root=tmp_path)


def test_heading_scope_includes_children_and_ignores_fenced_headings():
    markdown = (
        "# Document\n## Chosen {#chosen}\nIntro\n"
        "```text\n## Fake {#other}\n```\n"
        "### Child\nDetail\n## Next {#next}\nUnrelated\n"
    )
    section = extract_heading_section(markdown, "chosen")
    assert "Detail" in section
    assert "Fake" in section
    assert "Unrelated" not in section
    assert extract_heading_section(markdown, "missing") == ""
    assert extract_heading_section(markdown, "") == markdown


def test_catalog_search_is_topic_scoped_and_ranks_names_before_body(tmp_path):
    catalog = _catalog(tmp_path)
    assert [entry.id for entry in catalog.search("SSH")] == ["scp"]
    assert [entry.id for entry in catalog.search("FTP-only")] == ["ftp"]
    assert [entry.id for entry in catalog.search("FTP")] == ["ftp"]
    # An exact title outranks an earlier item that only mentions it in content.
    named = replace(catalog.get("scp"), id="identity", title="identity", anchor="scp")
    ranked = GuideCatalog((*catalog.entries, named))
    assert ranked.search("identity")[0].id == "identity"
    assert catalog.search("no-such-topic") == ()


def test_single_document_guide_searches_reference_sections_after_overview(tmp_path):
    path = tmp_path / "single.md"
    path.write_text(
        "# Single guide\n## Purpose {#single}\nOverview.\n"
        "## Advanced\nLegacy SSH recovery.\n"
        "## Quick {#quick-single}\nStart here.\n", encoding="utf-8"
    )
    entry = GuideEntry("single", "Single", "ko", "single.md", path,
                       anchor="single", quick_help_anchor="quick-single")
    catalog = GuideCatalog((entry,))
    assert [match.id for match in catalog.search("Legacy SSH")] == ["single"]
    quick, error = catalog.read_quick_help(entry)
    assert error is None
    assert "Legacy" not in quick


def test_generated_single_document_index_includes_reference_sections(tmp_path):
    path = tmp_path / "single.md"
    path.write_text(
        "# Single\n## Purpose {#single}\nOverview.\n"
        "## Saving\nLive file saving.\n"
        "## Quick {#quick-single}\nStart here.\n", encoding="utf-8"
    )
    guides = [{"id": "single", "title": "Single", "locale": "ko",
               "canonical_path": "single.md", "path": "single.md", "anchor": "single",
               "quick_help_anchor": "quick-single", "route": "single",
               "keywords": [], "risk": "low"}]
    index = _build_search_index(tmp_path, {"schema_version": 1, "default_locale": "ko"}, guides)
    assert "Live file saving" in index["entries"][0]["text"]
    assert "Live file saving" not in index["entries"][0]["summary"]


def test_quick_help_is_separate_from_reference_and_has_safe_fallback(tmp_path):
    catalog = _catalog(tmp_path)
    entry = catalog.get("scp")
    text, error = catalog.read_quick_help(entry)
    assert error is None
    assert "Check SSH identity" in text
    assert "FTP" not in text
    assert "SSH-copy needle" not in text
    assert "SSH-copy needle" in catalog.read_quick_help(replace(entry, quick_help_anchor=""))[0]
    assert catalog.read_quick_help(replace(entry, quick_help_anchor="missing"))[0] is None


def test_quick_panel_opens_reference_only_on_explicit_action(qapp, tmp_path):
    panel = QuickHelpPanel(_catalog(tmp_path))
    requested: list[str] = []
    panel.full_guide_requested.connect(requested.append)
    try:
        assert panel.show_guide("scp")
        assert "Check SSH identity" in panel.browser.toPlainText()
        assert "{#" not in panel.browser.toPlainText()
        assert requested == []
        panel.full_guide_button.click()
        assert requested == ["scp"]
        panel._open_link(QUrl("https://example.test/"))
        assert requested == ["scp"]
        assert not panel.show_guide("unknown")
        assert not panel.full_guide_button.isEnabled()
    finally:
        panel.close()


def test_guide_tree_expands_current_path_and_search_paths_only(qapp, tmp_path):
    catalog = _catalog(tmp_path)
    other = replace(catalog.get("transfer"), id="other", title="Other")
    child = replace(catalog.get("ftp"), id="other-child", parent_id="other")
    dialog = GuideDialog(GuideCatalog((*catalog.entries, other, child)))
    try:
        assert all(not item.isExpanded() for item in dialog._tree_items.values())
        dialog.open_guide("scp")
        assert dialog._tree_items["transfer"].isExpanded()
        assert not dialog._tree_items["other"].isExpanded()
        dialog.search_edit.setText("SSH")
        assert dialog._direct_search_matches == ("scp",)
        assert set(dialog._tree_items) == {"scp", "transfer"}
    finally:
        dialog.close()


def test_project_sources_have_complete_short_help_for_every_context():
    catalog = GuideCatalog._load_manifest(
        PROJECT_ROOT / "docs/guide_manifest.json", project_root=PROJECT_ROOT
    )
    for entry in catalog.entries:
        assert entry.quick_help_anchor, entry.id
        text, error = catalog.read_quick_help(entry)
        assert error is None, (entry.id, error)
        assert "**준비**" in text, entry.id
        assert "**성공 확인**" in text, entry.id
        assert "**안 되면**" in text, entry.id
        assert 3 <= len(re.findall(r"^\d+\. ", text, re.MULTILINE)) <= 5, entry.id


@pytest.mark.parametrize("quick_anchor", ["missing", "../bad", 123])
def test_validator_rejects_broken_short_help_anchor(tmp_path, quick_anchor):
    doc = tmp_path / "docs/user/ko/test.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Test {#test}\n\n## Purpose\nUseful instructions.\n", encoding="utf-8")
    source = tmp_path / "app/test.py"
    source.parent.mkdir()
    source.write_text("FEATURE = True\n", encoding="utf-8")
    manifest = {
        "schema_version": 1, "default_locale": "ko", "supported_locales": ["ko"],
        "required_sections": ["Purpose"],
        "guides": [{"id": "test", "parent_id": None, "title": "Test", "locale": "ko",
                    "path": "docs/user/ko/test.md", "anchor": "test", "route": "test",
                    "quick_help_anchor": quick_anchor, "source_paths": ["app/test.py"],
                    "keywords": ["test"], "risk": "low", "qa_capture_ids": []}],
    }
    (tmp_path / "docs/guide_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = validate_repository(repo_root=tmp_path)
    assert any("quick" in error for error in result.errors)


def test_generated_search_index_uses_the_same_topic_scope(tmp_path):
    catalog = _catalog(tmp_path)
    entries = [{"id": entry.id, "title": entry.title, "locale": "ko",
                "canonical_path": entry.path, "path": entry.path, "anchor": entry.anchor,
                "quick_help_anchor": entry.quick_help_anchor, "route": entry.id,
                "keywords": list(entry.keywords), "risk": "low"} for entry in catalog.entries]
    index = _build_search_index(tmp_path, {"schema_version": 1, "default_locale": "ko"}, entries)
    ftp = next(item for item in index["entries"] if item["id"] == "ftp")
    assert "FTP-only" in ftp["text"]
    assert "SSH" not in ftp["text"]
    assert ftp["quick_help_anchor"] == "quick-ftp"


def test_quick_help_renders_as_numbered_visual_card(qapp, tmp_path):
    from app.guides.render import parse_quick_help, quick_help_card_html

    path = tmp_path / "card.md"
    path.write_text(
        "# Card\n\n## Purpose {#card}\n\n![Screen](shot.png)\n\n"
        "## Card quick {#quick-card}\n\nCheck a host.\n\n**준비**: An address.\n\n"
        "1. Type it.\n2. Press `Run`.\n3. Read the result.\n\n"
        "**성공 확인**: A reply appears.\n\n**안 되면**: Check the firewall.\n",
        encoding="utf-8",
    )
    quick = parse_quick_help(extract_heading_section(path.read_text(encoding="utf-8"), "quick-card"))
    assert quick.summary == "Check a host."
    assert quick.steps == ["Type it.", "Press `Run`.", "Read the result."]
    assert quick.success == "A reply appears." and quick.trouble == "Check the firewall."
    html = quick_help_card_html(quick, image_src="shot.png", image_width=300)
    assert '<img src="shot.png" width="300"' in html
    assert 'href="zoom:shot.png"' in html  # click opens the full-size picture
    assert html.index("Read the result.") < html.index("shot.png")  # steps before the picture
    assert "<code>" in html

    entry = GuideEntry("card", "Card", "ko", "card.md", path, anchor="card",
                       quick_help_anchor="quick-card")
    panel = QuickHelpPanel(GuideCatalog((entry,)))
    try:
        assert panel.show_guide("card")
        text = panel.browser.toPlainText()
        for expected in ("Check a host.", "1", "Type it.", "성공 확인", "안 되면"):
            assert expected in text
        assert "**" not in text and "{#" not in text
    finally:
        panel.close()


def test_full_guide_starts_with_card_and_hides_other_quick_sections(qapp, tmp_path):
    catalog = _catalog(tmp_path)
    dialog = GuideDialog(catalog)
    try:
        dialog.open_guide("transfer")
        text = dialog.browser.toPlainText()
        assert "FTP description." in text
        assert "Send FTP files." not in text  # quick cards belong to their own topic
        dialog.open_guide("scp")
        text = dialog.browser.toPlainText()
        assert "SSH-copy needle." in text
        assert "FTP description." not in text
    finally:
        dialog.close()


def test_guide_pictures_are_scaled_smoothly_without_distortion(qapp, tmp_path):
    from PySide6.QtGui import QImage, QTextDocument

    from app.guides.images import register_scaled_image

    source = QImage(1280, 800, QImage.Format.Format_RGB32)
    source.fill(0xFFFFFF)
    source.save(str(tmp_path / "shot.png"))
    document = QTextDocument()
    url, width, height = register_scaled_image(document, tmp_path, "shot.png", 640)
    assert (width, height) == (640, 400)  # aspect ratio kept
    assert url != "shot.png"
    assert register_scaled_image(document, tmp_path, "missing.png", 640) == ("missing.png", 0, 0)


def test_zoom_links_open_only_pictures_inside_the_guide_folder(tmp_path):
    from PySide6.QtCore import QUrl

    from app.guides.images import image_path_from_zoom_url

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.png").write_bytes(b"png")
    assert image_path_from_zoom_url(QUrl("zoom:assets/a.png"), tmp_path) == (tmp_path / "assets" / "a.png").resolve()
    assert image_path_from_zoom_url(QUrl("zoom:../outside.png"), tmp_path) is None
    assert image_path_from_zoom_url(QUrl("guide:other"), tmp_path) is None


def test_full_guide_opens_on_walkthrough_and_deep_links_on_details(qapp):
    catalog = GuideCatalog._load_manifest(
        PROJECT_ROOT / "docs/guide_manifest.json", project_root=PROJECT_ROOT
    )
    dialog = GuideDialog(catalog)
    try:
        dialog.open_guide("inspector")
        assert dialog.view_tabs.isVisibleTo(dialog)
        assert dialog.view_stack.currentWidget() is dialog.card_browser
        walkthrough = dialog.card_browser.toPlainText()
        assert "샘플 생성" in walkthrough and "성공 확인" in walkthrough
        assert len(walkthrough) < len(dialog.browser.toPlainText()) / 3
        dialog.open_guide("inspector", "quick-inspector")
        assert dialog.view_stack.currentWidget() is dialog.browser
    finally:
        dialog.close()
