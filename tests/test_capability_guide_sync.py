from __future__ import annotations

import json
from pathlib import Path

from app.assistant.capabilities import all_feature_capabilities
from app.guides import GuideCatalog
from scripts.generate_guides import sync_capability_guides


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_project_capability_contract_is_synced_with_guides():
    assert sync_capability_guides(repo_root=PROJECT_ROOT, check=True) == 0
    catalog = GuideCatalog.load(PROJECT_ROOT)
    for capability in all_feature_capabilities():
        entry = catalog.resolve(capability.guide_id)
        assert entry is not None
        assert capability.feature_id in entry.capability_ids


def test_new_capability_requires_sync_and_creates_non_overwriting_draft(tmp_path: Path):
    capability_source = tmp_path / "app" / "assistant" / "capabilities.py"
    capability_source.parent.mkdir(parents=True)
    capability_source.write_text(
        """
class Capability:
    feature_id = "new-feature"
    public_name = "새 기능"
    guide_id = "new-feature"
    route = "new-feature"
    aliases = ("새 기능 별칭",)
    intents = ("새 기능 사용",)
    support_level = "ui_guidance"
    operations = ("새 작업",)
    inputs = ("입력값",)
    constraints = ()
    risk = "medium"
    source_paths = ("app/new_feature.py",)
    steps = ()
    success_checks = ()
    stop_conditions = ()

def all_feature_capabilities():
    return (Capability(),)
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "app" / "new_feature.py").write_text("# feature\n", encoding="utf-8")
    getting_started = tmp_path / "docs" / "user" / "ko" / "getting-started.md"
    getting_started.parent.mkdir(parents=True)
    getting_started.write_text("# 시작\n\n## Purpose\n\n설명\n", encoding="utf-8")
    _write_json(
        tmp_path / "docs" / "guide_manifest.json",
        {
            "schema_version": 1,
            "default_locale": "ko",
            "supported_locales": ["ko"],
            "required_sections": ["Purpose"],
            "guides": [
                {
                    "id": "getting-started",
                    "parent_id": None,
                    "title": "시작",
                    "locale": "ko",
                    "path": "docs/user/ko/getting-started.md",
                    "anchor": "",
                    "route": "getting-started",
                    "source_paths": ["app/new_feature.py"],
                    "keywords": ["시작"],
                    "risk": "low",
                    "qa_capture_ids": [],
                }
            ],
        },
    )

    assert sync_capability_guides(repo_root=tmp_path, check=True) == 1
    draft = tmp_path / "docs" / "user" / "ko" / "new-feature.md"
    assert not draft.exists()

    assert sync_capability_guides(repo_root=tmp_path) == 0
    assert draft.exists()
    assert "TODO" in draft.read_text(encoding="utf-8")
    manifest = json.loads(
        (tmp_path / "docs" / "guide_manifest.json").read_text(encoding="utf-8")
    )
    guide = next(item for item in manifest["guides"] if item["id"] == "new-feature")
    assert guide["capability_ids"] == ["new-feature"]
    assert guide["keywords"] == ["새 기능 별칭", "새 기능 사용", "새 기능"]
