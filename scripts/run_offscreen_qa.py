from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qa.offscreen import OffscreenQaHarness  # noqa: E402


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = OffscreenQaHarness(
        project_root=PROJECT_ROOT,
        config_path=args.config.resolve(),
        output_dir=args.output.resolve(),
        keep_runtime=args.keep_runtime,
    ).run()
    print(report.summary_text())
    print(f"Report: {report.markdown_path}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
