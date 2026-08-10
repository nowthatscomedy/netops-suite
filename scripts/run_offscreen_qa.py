from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qa.offscreen import OffscreenQaHarness  # noqa: E402
from scripts.guide_capture_fingerprint import (  # noqa: E402
    CAPTURE_MANIFEST_SCHEMA_VERSION,
    build_capture_fingerprint,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NetOps Suite Qt 오프스크린 사용자 흐름 QA"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "qa" / "offscreen" / "scenarios.json",
        help="시나리오 JSON 경로",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "qa"
        / "evidence"
        / date.today().isoformat()
        / "offscreen",
        help="스크린샷과 보고서 출력 폴더",
    )
    parser.add_argument(
        "--keep-runtime",
        action="store_true",
        help="격리된 임시 앱 데이터 폴더를 결과 폴더에 보존",
    )
    parser.add_argument(
        "--guide-assets-output",
        type=Path,
        default=None,
        help="guide_asset이 지정된 시나리오 이미지를 안정된 파일명으로 내보낼 폴더",
    )
    return parser.parse_args()


def export_guide_assets(
    config_path: Path,
    report,
    output_dir: Path,
) -> Path:
    """Export selected QA screenshots as deterministic user-guide assets."""

    config = json.loads(config_path.read_text(encoding="utf-8"))
    configured = {
        str(item.get("id", "")): (str(item.get("guide_asset", "")).strip(), item)
        for item in config.get("scenarios", [])
        if isinstance(item, dict) and str(item.get("guide_asset", "")).strip()
    }
    results = {result.scenario_id: result for result in report.results}
    destination_root = output_dir.resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    exported: list[dict[str, object]] = []

    for scenario_id, (asset_name, scenario) in configured.items():
        relative_asset = Path(asset_name)
        if (
            relative_asset.is_absolute()
            or relative_asset.name != asset_name
            or relative_asset.suffix.casefold() != ".png"
        ):
            raise ValueError(
                f"guide_asset은 폴더 없는 PNG 파일명이어야 합니다: {asset_name}"
            )
        result = results.get(scenario_id)
        if result is None or not result.ok or not result.screenshot:
            raise RuntimeError(
                f"가이드 이미지 시나리오가 성공하지 않았습니다: {scenario_id}"
            )
        source = (report.output_dir / result.screenshot).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        target = (destination_root / relative_asset).resolve()
        target.relative_to(destination_root)
        shutil.copy2(source, target)
        viewport = scenario.get("viewport", [])
        fingerprint = build_capture_fingerprint(PROJECT_ROOT, config, scenario)
        exported.append(
            {
                "scenario_id": scenario_id,
                "file": relative_asset.as_posix(),
                "viewport": viewport,
                "capture_handler": scenario.get("capture_handler", ""),
                "expected_object_names": list(
                    scenario.get("expected_object_names", [])
                ),
                "source_paths": list(scenario.get("source_paths", [])),
                "png_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "fingerprint": fingerprint,
                "checks": list(result.checks),
            }
        )

    manifest_path = destination_root / "capture-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": CAPTURE_MANIFEST_SCHEMA_VERSION,
                "fingerprint_algorithm": "sha256",
                "assets": exported,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    args = parse_args()
    report = OffscreenQaHarness(
        project_root=PROJECT_ROOT,
        config_path=args.config.resolve(),
        output_dir=args.output.resolve(),
        keep_runtime=args.keep_runtime,
    ).run()
    if args.guide_assets_output is not None and report.ok:
        asset_manifest = export_guide_assets(
            args.config.resolve(),
            report,
            args.guide_assets_output.resolve(),
        )
        print(f"Guide assets: {asset_manifest}")
    print(report.summary_text())
    print(f"Report: {report.markdown_path}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
