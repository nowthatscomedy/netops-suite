from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .switch_configurator.engine import ConfigEngine, build_bundle_text
from .switch_configurator.io_utils import load_device_records_from_path, load_profiles_from_directory
from .switch_configurator.models import Profile, RenderedConfig, ValidationIssue
from .switch_configurator.table_data import make_blank_table_row, save_device_table_to_path


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
        self.user_device_values_dir = self.user_data_dir / "device_values" if self.user_data_dir else None
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

    def sample_device_values_for_profile(self, profile: Profile) -> Path | None:
        candidate_names = self._sample_candidate_names(profile)
        for name in candidate_names:
            candidate = self.device_values_dir / name
            if candidate.exists():
                return candidate

        candidate_keys = {self._sample_match_key(Path(name).stem) for name in candidate_names}
        for candidate in sorted(self.device_values_dir.glob("*.xlsx")) + sorted(self.device_values_dir.glob("*.csv")):
            if self._sample_match_key(candidate.stem) in candidate_keys:
                return candidate
        return None

    def prepare_sample_device_values_for_profile(self, profile: Profile) -> Path:
        package_sample = self.sample_device_values_for_profile(profile)
        if package_sample:
            if not self.user_device_values_dir:
                return package_sample
            target = self.user_device_values_dir / package_sample.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(package_sample, target)
            return target

        target_dir = self.user_device_values_dir or (self.module_dir / "outputs" / "device_values")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{self._safe_profile_file_stem(profile.id)}_devices.csv"
        if target.exists():
            return target

        headers = ["device_id", "profile_id", *profile.variables.keys()]
        row = make_blank_table_row(headers, profile)
        save_device_table_to_path(target, headers, [row])
        return target

    def _sample_candidate_names(self, profile: Profile) -> list[str]:
        stems: list[str] = []
        if profile.source:
            stems.append(Path(profile.source).stem)
        if profile.id:
            profile_stem = self._safe_profile_file_stem(profile.id)
            stems.extend([f"sample_{profile_stem}", profile_stem])

        names: list[str] = []
        seen: set[str] = set()
        for stem in stems:
            normalized = stem.strip()
            if not normalized:
                continue
            if normalized.endswith("_devices"):
                base_names = [normalized]
            else:
                base_names = [f"{normalized}_devices"]
            for base_name in base_names:
                for suffix in (".xlsx", ".csv"):
                    name = f"{base_name}{suffix}"
                    key = name.casefold()
                    if key not in seen:
                        names.append(name)
                        seen.add(key)
        return names

    @staticmethod
    def _sample_match_key(stem: str) -> str:
        normalized = stem.casefold()
        if normalized.endswith("_devices"):
            normalized = normalized[: -len("_devices")]
        if normalized.startswith("sample_"):
            normalized = normalized[len("sample_") :]
        return "".join(ch if ch.isalnum() else "_" for ch in normalized).strip("_")

    @staticmethod
    def _safe_profile_file_stem(profile_id: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in profile_id.casefold())
        safe = "_".join(part for part in safe.split("_") if part)
        return safe or "profile"
