from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .switch_configurator.engine import ConfigEngine, build_bundle_text
from .switch_configurator.io_utils import load_device_records_from_path, load_profiles_from_directory
from .switch_configurator.models import Profile, RenderedConfig, ValidationIssue


@dataclass(slots=True)
class ConfigBuilderRenderResult:
    profile_issues: list[ValidationIssue]
    device_issues: list[ValidationIssue]
    rendered: list[RenderedConfig]
    bundle_text: str


class ConfigBuilderService:
    def __init__(self, profiles_dir: str | Path | None = None, user_data_dir: str | Path | None = None) -> None:
        self.module_dir = Path(__file__).resolve().parent
        self.package_profiles_dir = self.module_dir / "profiles"
        self.device_values_dir = self.module_dir / "device_values"
        self.docs_dir = self.module_dir / "docs"
        self.user_data_dir = Path(user_data_dir) if user_data_dir else None
        if profiles_dir:
            self.profiles_dir = Path(profiles_dir)
        elif self.user_data_dir:
            self.profiles_dir = self.user_data_dir / "profiles"
            self._seed_user_profiles()
        else:
            self.profiles_dir = self.package_profiles_dir

    def _seed_user_profiles(self) -> None:
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        if any(self.profiles_dir.glob("*.yaml")) or any(self.profiles_dir.glob("*.yml")):
            return
        if not self.package_profiles_dir.exists():
            return
        for source in list(self.package_profiles_dir.glob("*.yaml")) + list(self.package_profiles_dir.glob("*.yml")):
            shutil.copyfile(source, self.profiles_dir / source.name)

    def load_profiles(self) -> tuple[dict[str, Profile], list[ValidationIssue]]:
        return load_profiles_from_directory(self.profiles_dir)

    def profile_summaries(self) -> list[dict[str, Any]]:
        profiles, issues = self.load_profiles()
        return [
            {
                "id": profile.id,
                "vendor": profile.vendor,
                "model": profile.model,
                "firmware": profile.firmware,
                "description": profile.description_ko or profile.description,
                "variables": list(profile.variables.keys()),
                "blocks": [block.name for block in profile.blocks],
                "source": profile.source,
                "issue_count": sum(1 for issue in issues if issue.profile_id == profile.id),
            }
            for profile in profiles.values()
        ]

    def render_file(
        self,
        device_values_path: str | Path,
        *,
        skip_blocks: set[str] | None = None,
    ) -> ConfigBuilderRenderResult:
        profiles, profile_issues = self.load_profiles()
        engine = ConfigEngine(profiles)
        records = load_device_records_from_path(device_values_path)
        profile_validation = engine.validate_profiles()
        device_issues = engine.validate_device_records(records)
        all_profile_issues = [*profile_issues, *profile_validation]
        if any(issue.level == "error" for issue in [*all_profile_issues, *device_issues]):
            return ConfigBuilderRenderResult(
                profile_issues=all_profile_issues,
                device_issues=device_issues,
                rendered=[],
                bundle_text="",
            )
        rendered = engine.render_all(records, skip_blocks=skip_blocks)
        return ConfigBuilderRenderResult(
            profile_issues=all_profile_issues,
            device_issues=device_issues,
            rendered=rendered,
            bundle_text=build_bundle_text(rendered),
        )

    def save_bundle(self, text: str, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def save_each(self, rendered: list[RenderedConfig], output_dir: str | Path) -> list[Path]:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for index, config in enumerate(rendered, start=1):
            label = config.display_name or config.device_id or f"device_{index}"
            safe_label = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in label)
            path = directory / f"{safe_label}.txt"
            path.write_text(config.text, encoding="utf-8")
            paths.append(path)
        return paths

    def sample_device_values(self) -> Path | None:
        candidates = sorted(self.device_values_dir.glob("*.xlsx")) + sorted(self.device_values_dir.glob("*.csv"))
        return candidates[0] if candidates else None
