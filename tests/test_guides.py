from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.app_state import AppState
from app.guides import GuideCatalog, GuideDialog, GuideEntry
from app.main_window import MainWindow
from scripts.generate_guides import build_bundle
from scripts.validate_guides import validate_repository


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _catalog_manifest(path: str, *, include_missing: bool = False) -> dict:
    guides = [
        {
            "id": "getting-started",
            "parent_id": None,
            "title": "Getting started",
            "locale": "ko",
            "path": path,
            "anchor": "overview",
            "route": "getting-started",
            "source_paths": ["app/main_window.py"],
            "keywords": ["welcome"],
            "risk": "low",
            "qa_capture_ids": [],
        }
    ]
    if include_missing:
        guides.append(
            {
                "id": "missing-guide",
                "parent_id": "getting-started",
                "title": "Missing guide",
                "locale": "ko",
                "path": "docs/user/ko/missing.md",
                "anchor": "",
                "route": "missing-guide",
                "source_paths": ["app/main_window.py"],
                "keywords": ["missing"],
                "risk": "low",
                "qa_capture_ids": [],
            }
        )
    return {
        "schema_version": 1,
        "default_locale": "ko",
        "supported_locales": ["ko"],
        "required_sections": ["Purpose"],
        "guides": guides,
    }


def _write_catalog_source(root: Path, *, include_missing: bool = False) -> None:
    document = root / "docs" / "user" / "ko" / "welcome.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(
        "# Welcome {#overview}\n\n## Purpose\n\nOffline source guide.\n",
        encoding="utf-8",
    )
    _write_json(
        root / "docs" / "guide_manifest.json",
        _catalog_manifest(
            "docs/user/ko/welcome.md",
            include_missing=include_missing,
        ),
    )


def _write_catalog_bundle(root: Path) -> None:
    bundle_root = root / "app" / "resources" / "guides"
    document = bundle_root / "content" / "ko" / "welcome.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(
        "# Welcome {#overview}\n\n## Purpose\n\nOffline bundled guide.\n",
        encoding="utf-8",
    )
    _write_json(
        bundle_root / "guide_manifest.json",
        _catalog_manifest("content/ko/welcome.md"),
    )


def _dialog_catalog(tmp_path: Path) -> GuideCatalog:
    tmp_path.mkdir(parents=True, exist_ok=True)
    welcome_path = tmp_path / "welcome.md"
    ping_path = tmp_path / "ping.md"
    welcome_path.write_text(
        "# Welcome\n\nChoose a diagnostic tool.\n",
        encoding="utf-8",
    )
    ping_path.write_text(
        "# Ping\n\nSearchable latency needle.\n\n"
        "## Latency details {#latency-details}\n\n"
        "The result confirms round-trip time.\n",
        encoding="utf-8",
    )
    return GuideCatalog(
        (
            GuideEntry(
                id="getting-started",
                title="Getting started",
                locale="ko",
                path="welcome.md",
                content_path=welcome_path,
                route="getting-started",
                keywords=("welcome",),
            ),
            GuideEntry(
                id="diagnostics.ping",
                parent_id="getting-started",
                title="Ping",
                locale="ko",
                path="ping.md",
                content_path=ping_path,
                anchor="latency-details",
                route="diagnostics.ping",
                keywords=("icmp", "latency"),
            ),
            GuideEntry(
                id="wireless",
                parent_id="getting-started",
                title="Wi-Fi",
                locale="ko",
                path="welcome.md",
                content_path=welcome_path,
                route="wireless",
                keywords=("wireless",),
            ),
            GuideEntry(
                id="interface",
                parent_id="getting-started",
                title="Network interface",
                locale="ko",
                path="welcome.md",
                content_path=welcome_path,
                route="interface",
                keywords=("adapter",),
            ),
        ),
        project_root=tmp_path,
        default_locale="ko",
        supported_locales=("ko",),
    )


def _disable_external_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        MainWindow,
        "_maybe_check_updates_on_startup",
        lambda *_args, **_kwargs: None,
    )


def _write_valid_guide_repository(root: Path) -> tuple[dict, Path]:
    source = root / "app" / "ui" / "example.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("FEATURE_ID = 'example'\n", encoding="utf-8")

    document = root / "docs" / "user" / "ko" / "example.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(
        "# Example {#example}\n\n## Purpose\n\nComplete user instructions.\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "default_locale": "ko",
        "supported_locales": ["ko"],
        "required_sections": ["Purpose"],
        "guides": [
            {
                "id": "example",
                "parent_id": None,
                "title": "Example",
                "locale": "ko",
                "path": "docs/user/ko/example.md",
                "anchor": "example",
                "route": "example",
                "source_paths": ["app/ui/example.py"],
                "keywords": ["example"],
                "risk": "low",
                "qa_capture_ids": [],
            }
        ],
    }
    _write_json(root / "docs" / "guide_manifest.json", manifest)
    return manifest, document


def test_guide_catalog_loads_source_and_prefers_offline_bundle(tmp_path):
    source_root = tmp_path / "source"
    _write_catalog_source(source_root)

    source_catalog = GuideCatalog.load(source_root)

    assert source_catalog.is_available
    assert source_catalog.manifest_path == source_root / "docs" / "guide_manifest.json"
    source_entry = source_catalog.get("getting-started")
    assert source_entry is not None
    assert source_catalog.read_markdown(source_entry) == (
        "# Welcome {#overview}\n\n## Purpose\n\nOffline source guide.\n",
        None,
    )

    _write_catalog_bundle(source_root)
    bundled_catalog = GuideCatalog.load(source_root)

    assert bundled_catalog.manifest_path == (
        source_root / "app" / "resources" / "guides" / "guide_manifest.json"
    )
    bundled_entry = bundled_catalog.get("getting-started")
    assert bundled_entry is not None
    markdown, error = bundled_catalog.read_markdown(bundled_entry)
    assert error is None
    assert markdown is not None and "Offline bundled guide" in markdown


def test_guide_catalog_fails_safely_for_missing_or_invalid_content(tmp_path):
    missing_root = tmp_path / "missing"
    missing_catalog = GuideCatalog.load(missing_root)

    assert not missing_catalog.is_available
    assert missing_catalog.errors

    partial_root = tmp_path / "partial"
    _write_catalog_source(partial_root, include_missing=True)
    partial_catalog = GuideCatalog.load(partial_root)
    missing_entry = partial_catalog.get("missing-guide")

    assert missing_entry is not None
    markdown, error = partial_catalog.read_markdown(missing_entry)
    assert markdown is None
    assert error

    invalid_root = tmp_path / "invalid"
    invalid_manifest = _catalog_manifest("../../outside.md")
    _write_json(invalid_root / "docs" / "guide_manifest.json", invalid_manifest)
    invalid_catalog = GuideCatalog.load(invalid_root)

    assert not invalid_catalog.is_available
    assert invalid_catalog.errors


def test_manifest_covers_every_main_window_guide_context():
    catalog = GuideCatalog.load(PROJECT_ROOT)
    transfer_contexts = {
        f"diagnostics.transfer.{protocol}.{role}"
        for protocol in ("ftp", "ftps", "sftp", "scp", "tftp")
        for role in ("client", "server")
    }
    expected_ids = (
        set(MainWindow._MAIN_GUIDE_IDS)
        | set(MainWindow._DIAGNOSTIC_GUIDE_IDS.values())
        | transfer_contexts
    )

    assert catalog.is_available, catalog.errors
    missing_ids = sorted(
        identifier for identifier in expected_ids if catalog.get(identifier) is None
    )
    assert missing_ids == []
    for identifier in expected_ids:
        entry = catalog.get(identifier)
        assert entry is not None
        assert entry.source_paths
        markdown, error = catalog.read_markdown(entry)
        assert error is None
        assert markdown


def test_guide_dialog_search_context_and_anchor_navigation(qapp, tmp_path):
    catalog = _dialog_catalog(tmp_path)
    dialog = GuideDialog(catalog, context_provider=lambda: "diagnostics.ping")
    try:
        dialog.context_button.click()
        qapp.processEvents()

        assert dialog.current_entry is not None
        assert dialog.current_entry.id == "diagnostics.ping"
        # The jump link list also names the section; the cursor lands on the heading.
        cursor = dialog.browser.textCursor()
        assert cursor.block().text() == "Latency details"
        assert cursor.position() == cursor.block().position()
        assert cursor.block().blockFormat().headingLevel() == 2

        dialog.search_edit.setText("The result confirms round-trip time")
        qapp.processEvents()
        assert set(dialog._tree_items) == {"getting-started", "diagnostics.ping"}
        dialog.search_edit.returnPressed.emit()
        qapp.processEvents()
        assert dialog.current_entry is not None
        assert dialog.current_entry.id == "diagnostics.ping"

        dialog.search_edit.setText("no-match-token")
        qapp.processEvents()
        assert dialog._tree_items == {}
        assert dialog.tree.topLevelItemCount() == 1
        assert dialog.tree.topLevelItem(0).isDisabled()
    finally:
        dialog.close()


def test_main_window_manual_help_and_f1_follow_current_context(
    qapp,
    tmp_path,
    monkeypatch,
):
    _disable_external_startup(monkeypatch)
    catalog = _dialog_catalog(tmp_path / "guides")
    monkeypatch.setattr(
        GuideCatalog,
        "load",
        classmethod(lambda cls, *_args, **_kwargs: catalog),
    )
    state = AppState(tmp_path / "state")
    state.app_config["guide"]["welcome_seen"] = True
    window = MainWindow(state)
    try:
        window.show()
        window.navigate_to("interface")
        qapp.processEvents()

        window.guide_button.click()
        qapp.processEvents()
        assert window.quick_help_panel.current_entry is not None
        assert window.quick_help_panel.current_entry.id == "interface"
        assert window.help_dock.isVisible()

        window.help_dock.hide()
        window.tab_widget.setCurrentIndex(2)
        window.activateWindow()
        window.setFocus(Qt.FocusReason.OtherFocusReason)
        qapp.processEvents()
        QTest.keyClick(window, Qt.Key.Key_F1)
        qapp.processEvents()

        assert window.quick_help_panel.current_entry is not None
        assert window.quick_help_panel.current_entry.id == "wireless"
        assert window.help_dock.isVisible()
        assert window._guide_dialog is None
    finally:
        window.close()
        state.shutdown()


def test_first_run_guide_is_persisted_once_without_startup_side_effects(
    qapp,
    tmp_path,
    monkeypatch,
):
    _disable_external_startup(monkeypatch)
    catalog = _dialog_catalog(tmp_path / "guides")
    monkeypatch.setattr(
        GuideCatalog,
        "load",
        classmethod(lambda cls, *_args, **_kwargs: catalog),
    )
    state = AppState(tmp_path / "state")
    window = MainWindow(state)
    opened: list[str | None] = []
    monkeypatch.setattr(
        window,
        "open_guide",
        lambda feature_id=None: opened.append(feature_id) or True,
    )
    try:
        assert not window._startup_activated
        window._maybe_show_first_run_guide()
        qapp.processEvents()

        assert opened == []
        assert window.tab_widget.currentWidget() is window.home_page
        persisted = json.loads(state.paths.app_config.read_text(encoding="utf-8"))
        assert persisted["guide"]["welcome_seen"] is True

        window._maybe_show_first_run_guide()
        assert opened == []
    finally:
        window.close()
        state.shutdown()


@pytest.mark.parametrize(
    ("case", "expected_error"),
    (
        ("duplicate-id", "duplicate guide ID"),
        ("placeholder", "unresolved placeholder"),
        ("broken-link", "broken local link or asset"),
        ("orphan-document", "orphan user-guide Markdown"),
    ),
)
def test_guide_validator_rejects_invalid_repository_cases(
    tmp_path,
    case,
    expected_error,
):
    manifest, document = _write_valid_guide_repository(tmp_path)
    if case == "duplicate-id":
        duplicate = copy.deepcopy(manifest["guides"][0])
        duplicate["route"] = "duplicate"
        duplicate["anchor"] = ""
        manifest["guides"].append(duplicate)
        _write_json(tmp_path / "docs" / "guide_manifest.json", manifest)
    elif case == "placeholder":
        document.write_text(
            document.read_text(encoding="utf-8") + "\nTODO: finish this guide.\n",
            encoding="utf-8",
        )
    elif case == "broken-link":
        document.write_text(
            document.read_text(encoding="utf-8") + "\n[Missing](missing.md)\n",
            encoding="utf-8",
        )
    else:
        (document.parent / "orphan.md").write_text(
            "# Orphan\n\nUnregistered guide.\n",
            encoding="utf-8",
        )

    result = validate_repository(tmp_path)

    assert not result.ok
    assert any(expected_error in error for error in result.errors), result.errors


def test_guide_generator_build_check_detects_current_and_stale_bundle(tmp_path):
    _write_valid_guide_repository(tmp_path)

    assert build_bundle(repo_root=tmp_path) == 0
    assert build_bundle(repo_root=tmp_path, check=True) == 0

    document = tmp_path / "docs" / "user" / "ko" / "example.md"
    document.write_text(
        document.read_text(encoding="utf-8") + "\nAdditional verified guidance.\n",
        encoding="utf-8",
    )

    assert build_bundle(repo_root=tmp_path, check=True) == 1


def test_committed_offline_guide_bundle_is_current_and_loadable():
    assert build_bundle(repo_root=PROJECT_ROOT, check=True) == 0

    catalog = GuideCatalog.load(PROJECT_ROOT)
    assert catalog.is_available, catalog.errors
    assert catalog.manifest_path == (
        PROJECT_ROOT / "app" / "resources" / "guides" / "guide_manifest.json"
    )
    assert catalog.get("getting-started") is not None


def test_ci_release_and_build_scripts_enforce_guide_contracts():
    ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    release = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    build = (PROJECT_ROOT / "scripts" / "build_release.ps1").read_text(
        encoding="utf-8"
    )
    local_test = (PROJECT_ROOT / "test.bat").read_text(encoding="utf-8")

    for contract_file in (ci, release, build, local_test):
        normalized = contract_file.replace("\\", "/")
        assert "scripts/validate_guides.py" in normalized
        assert "scripts/generate_guides.py build --check" in normalized

    assert "guide-not-required" in ci
    assert "--base" in ci
    assert "--allow-guide-not-required" in ci
    assert "GUIDE_EXCEPTION_REASON" in ci
    normalized_build = build.replace("\\", "/")
    assert "app/resources/guides" in normalized_build
    assert "--add-data" in build
