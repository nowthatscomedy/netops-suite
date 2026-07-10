from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from app.models.update_models import DownloadedUpdate, ReleaseAsset, UpdateCheckResult
from app.utils.file_utils import DEFAULT_UPDATE_ASSET_PATTERN, DEFAULT_UPDATE_REPO
from app.utils.process_utils import no_window_creationflags


class UpdateService:
    API_TIMEOUT_SEC = 20
    DOWNLOAD_CHUNK_SIZE = 1024 * 256

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def check_for_updates(
        self,
        current_version: str,
        update_config: dict,
        progress_callback=None,
    ) -> UpdateCheckResult:
        repo = self._normalize_repo(DEFAULT_UPDATE_REPO)
        if not repo:
            return UpdateCheckResult(
                success=False,
                current_version=current_version,
                requires_config=True,
                message="공식 업데이트 저장소 설정이 올바르지 않습니다.",
                details="앱 내부 업데이트 저장소는 owner/repo 형식이어야 합니다.",
            )

        asset_pattern = DEFAULT_UPDATE_ASSET_PATTERN

        if progress_callback is not None:
            progress_callback.emit({"stage": "check", "message": f"{repo} 릴리즈 정보를 확인하는 중..."})

        self.logger.info("Checking updates from GitHub repo %s", repo)
        release = self._fetch_release(repo)
        latest_version = self._normalize_version(str(release.get("tag_name", "") or ""))
        if not latest_version:
            raise ValueError("릴리즈 태그에서 버전 정보를 읽지 못했습니다.")

        compare_result = self._compare_versions(latest_version, current_version)
        if compare_result <= 0:
            return UpdateCheckResult(
                success=True,
                current_version=current_version,
                latest_version=latest_version,
                is_prerelease=bool(release.get("prerelease")),
                update_available=False,
                install_ready=False,
                message=f"이미 최신 버전입니다. (현재 {current_version})",
                release_name=str(release.get("name", "") or ""),
                release_url=str(release.get("html_url", "") or ""),
                published_at=str(release.get("published_at", "") or ""),
                body=str(release.get("body", "") or ""),
            )

        assets = [self._asset_from_api(item) for item in release.get("assets", [])]
        asset = self._select_asset(assets, asset_pattern, expected_version=latest_version)

        verification_source = ""
        checksum_asset = None
        install_ready = False
        message = ""
        details = ""

        if not asset:
            message = f"새 버전 {latest_version}이 있지만 설치 파일을 찾지 못했습니다."
            details = f"릴리즈 자산 중 `{asset_pattern}` 패턴과 일치하는 `.exe` 파일이 필요합니다."
        elif asset.digest_sha256:
            install_ready = True
            verification_source = "github_release_digest"
            message = f"새 버전 {latest_version}을 설치할 수 있습니다."
            details = (
                "GitHub Releases 자산 SHA-256 digest로 다운로드 파일의 무결성을 검증합니다. "
                "게시자 신뢰는 설치 프로그램의 Windows 코드서명 정보로 별도 확인해야 합니다."
            )
        else:
            checksum_asset = self._select_checksum_asset(assets, asset.name)
            if checksum_asset:
                install_ready = True
                verification_source = "checksum_asset"
                message = f"새 버전 {latest_version}을 설치할 수 있습니다."
                details = (
                    "릴리즈에 포함된 체크섬 파일로 다운로드 파일의 무결성을 검증합니다. "
                    "게시자 신뢰는 설치 프로그램의 Windows 코드서명 정보로 별도 확인해야 합니다."
                )
            else:
                message = f"새 버전 {latest_version}이 있지만 검증 정보를 찾지 못했습니다."
                details = (
                    "보안을 위해 GitHub Releases 자산 digest 또는 "
                    f"`{asset.name}.sha256` / `SHA256SUMS.txt` 같은 체크섬 파일이 필요합니다."
                )

        return UpdateCheckResult(
            success=True,
            current_version=current_version,
            latest_version=latest_version,
            is_prerelease=bool(release.get("prerelease")),
            update_available=True,
            install_ready=install_ready,
            message=message,
            details=details,
            release_name=str(release.get("name", "") or ""),
            release_url=str(release.get("html_url", "") or ""),
            published_at=str(release.get("published_at", "") or ""),
            body=str(release.get("body", "") or ""),
            asset=asset,
            checksum_asset=checksum_asset,
            verification_source=verification_source,
        )

    def download_update(
        self,
        check_result: UpdateCheckResult,
        progress_callback=None,
    ) -> DownloadedUpdate:
        if not check_result.asset:
            raise ValueError("설치 파일 정보가 없습니다.")

        temp_dir = Path(tempfile.mkdtemp(prefix="netops_update_"))
        asset_path = temp_dir / check_result.asset.name
        checksum_path: Path | None = None

        self._download_file(check_result.asset, asset_path, progress_callback)

        if check_result.asset.digest_sha256:
            expected_sha256 = check_result.asset.digest_sha256
            verification_source = "GitHub Releases SHA-256 digest"
        elif check_result.checksum_asset:
            checksum_path = temp_dir / check_result.checksum_asset.name
            self._download_file(check_result.checksum_asset, checksum_path, progress_callback)
            expected_sha256 = self._parse_expected_sha256(checksum_path, check_result.asset.name)
            verification_source = "release checksum file SHA-256"
        else:
            raise ValueError("검증 정보가 없어 다운로드한 설치 파일을 신뢰할 수 없습니다.")

        actual_sha256 = self._sha256_file(asset_path)
        if actual_sha256.lower() != expected_sha256.lower():
            raise ValueError(
                "다운로드한 설치 파일의 SHA-256 검증에 실패했습니다. 파일이 변조되었거나 릴리즈 자산 정보가 잘못되었습니다."
            )

        self.logger.info(
            "Downloaded update %s and verified SHA-256 using %s",
            check_result.asset.name,
            verification_source,
        )
        return DownloadedUpdate(
            version=check_result.latest_version,
            asset_name=check_result.asset.name,
            asset_path=asset_path,
            checksum_path=checksum_path,
            sha256=actual_sha256,
            verification_source=verification_source,
        )

    def launch_installer(self, installer_path: Path, expected_sha256: str = "") -> None:
        if not installer_path.exists():
            raise FileNotFoundError(f"설치 파일을 찾을 수 없습니다: {installer_path}")

        if expected_sha256:
            actual_sha256 = self._sha256_file(installer_path)
            if actual_sha256.lower() != expected_sha256.lower():
                raise ValueError(
                    "설치 직전 SHA-256 재검증에 실패했습니다. "
                    "다운로드 이후 설치 파일이 변경되었을 수 있어 실행을 차단했습니다."
                )

        self.logger.info("Launching installer %s", installer_path)
        subprocess.Popen(
            [str(installer_path)],
            cwd=str(installer_path.parent),
            creationflags=no_window_creationflags(),
        )

    def _fetch_release(self, repo: str) -> dict:
        releases = self._request_json(f"https://api.github.com/repos/{repo}/releases")
        if not isinstance(releases, list):
            raise ValueError("GitHub Releases 응답 형식이 올바르지 않습니다.")

        candidates: list[dict] = []
        for release in releases:
            if release.get("draft"):
                continue
            if release.get("prerelease"):
                continue
            version = self._normalize_version(str(release.get("tag_name", "") or ""))
            if not version:
                continue
            candidates.append(release)

        if not candidates:
            raise ValueError("사용 가능한 정식 릴리즈를 찾지 못했습니다.")

        candidates.sort(
            key=lambda release: self._version_sort_key(
                self._normalize_version(str(release.get("tag_name", "") or ""))
            ),
            reverse=True,
        )
        return candidates[0]

    def _request_json(self, url: str) -> dict | list:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "NetOpsSuite-Updater",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.API_TIMEOUT_SEC) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                payload = response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ValueError("GitHub 릴리즈를 찾지 못했습니다. 저장소 이름과 공개 릴리즈 여부를 확인해 주세요.") from exc
            if exc.code == 403:
                raise ValueError("GitHub API 접근이 거부되었습니다. 잠시 후 다시 시도해 주세요.") from exc
            raise ValueError(f"GitHub API 요청이 실패했습니다. HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ValueError("인터넷 연결 또는 GitHub 접근 상태를 확인해 주세요.") from exc

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("GitHub 응답을 해석하지 못했습니다.") from exc

    def _download_file(self, asset: ReleaseAsset, destination: Path, progress_callback=None) -> None:
        request = urllib.request.Request(asset.download_url, headers={"User-Agent": "NetOpsSuite-Updater"})
        try:
            with urllib.request.urlopen(request, timeout=self.API_TIMEOUT_SEC) as response:
                total_bytes = int(response.headers.get("Content-Length") or asset.size_bytes or 0)
                downloaded = 0
                with destination.open("wb") as handle:
                    while True:
                        chunk = response.read(self.DOWNLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback is not None:
                            percent = int(downloaded * 100 / total_bytes) if total_bytes else 0
                            progress_callback.emit(
                                {
                                    "stage": "download",
                                    "message": f"{asset.name} 다운로드 중... {percent}%",
                                    "file": asset.name,
                                    "percent": percent,
                                }
                            )
        except urllib.error.URLError as exc:
            raise ValueError(f"{asset.name} 다운로드에 실패했습니다.") from exc

    def _asset_from_api(self, asset_data: dict) -> ReleaseAsset:
        digest = str(asset_data.get("digest", "") or "")
        digest_sha256 = ""
        if digest.lower().startswith("sha256:"):
            digest_sha256 = digest.split(":", 1)[1].strip()

        return ReleaseAsset(
            name=str(asset_data.get("name", "") or ""),
            download_url=str(asset_data.get("browser_download_url", "") or ""),
            size_bytes=int(asset_data.get("size", 0) or 0),
            digest_sha256=digest_sha256,
        )

    def _select_asset(
        self,
        assets: list[ReleaseAsset],
        pattern: str,
        *,
        expected_version: str = "",
    ) -> ReleaseAsset | None:
        regex = re.compile(pattern, re.IGNORECASE)
        expected_name = f"NetOpsSuite-setup-{expected_version}.exe" if expected_version else ""
        for asset in assets:
            if expected_name and asset.name.casefold() != expected_name.casefold():
                continue
            if regex.fullmatch(asset.name):
                return asset
        return None

    def _select_checksum_asset(self, assets: list[ReleaseAsset], asset_name: str) -> ReleaseAsset | None:
        if not asset_name:
            return None

        exact_names = {
            f"{asset_name}.sha256",
            f"{asset_name}.sha256.txt",
            "SHA256SUMS.txt",
            "sha256sums.txt",
        }
        normalized_names = {name.lower() for name in exact_names}
        for asset in assets:
            if asset.name.lower() in normalized_names:
                return asset
        return None

    def _parse_expected_sha256(self, checksum_path: Path, asset_name: str) -> str:
        content = checksum_path.read_text(encoding="utf-8", errors="replace")
        hash_pattern = re.compile(r"(?P<hash>[a-fA-F0-9]{64})(?:\s+[\*\s]?(?P<name>.+))?$")

        for raw_line in content.splitlines():
            line = raw_line.strip().lstrip("\ufeff")
            if not line:
                continue
            match = hash_pattern.match(line)
            if not match:
                continue
            found_name = (match.group("name") or "").strip()
            if not found_name or Path(found_name).name == asset_name:
                return match.group("hash")

        raise ValueError("체크섬 파일에서 대상 설치 파일의 SHA-256 값을 찾지 못했습니다.")

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(self.DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _normalize_repo(self, repo: str) -> str:
        text = repo.strip().strip("/")
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", text):
            return ""
        return text

    def _normalize_version(self, value: str) -> str:
        text = value.strip()
        if text.lower().startswith("v"):
            text = text[1:]
        match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?", text)
        if not match:
            return ""
        base = match.group(1)
        prerelease = match.group(2)
        return f"{base}-{prerelease}" if prerelease else base

    def _parse_version(self, value: str) -> tuple[tuple[int, ...], tuple[tuple[int, object], ...] | None]:
        normalized = self._normalize_version(value)
        if not normalized:
            return tuple(), None

        if "-" in normalized:
            base, prerelease_text = normalized.split("-", 1)
        else:
            base, prerelease_text = normalized, ""

        base_parts = tuple(int(part) for part in base.split(".") if part.strip())
        prerelease_parts: list[tuple[int, object]] = []
        if prerelease_text:
            for token in prerelease_text.split("."):
                token = token.strip()
                if token.isdigit():
                    prerelease_parts.append((0, int(token)))
                else:
                    prerelease_parts.append((1, token.lower()))
            return base_parts, tuple(prerelease_parts)
        return base_parts, None

    def _version_sort_key(self, value: str) -> tuple[tuple[int, ...], int, tuple[tuple[int, object], ...]]:
        base_parts, prerelease_parts = self._parse_version(value)
        stability_rank = 1 if prerelease_parts is None else 0
        return base_parts, stability_rank, prerelease_parts or tuple()

    def _compare_versions(self, left: str, right: str) -> int:
        left_parts, left_prerelease = self._parse_version(left)
        right_parts, right_prerelease = self._parse_version(right)
        max_length = max(len(left_parts), len(right_parts))
        left_parts = left_parts + (0,) * (max_length - len(left_parts))
        right_parts = right_parts + (0,) * (max_length - len(right_parts))
        if left_parts != right_parts:
            return 1 if left_parts > right_parts else -1

        if left_prerelease is None and right_prerelease is None:
            return 0
        if left_prerelease is None:
            return 1
        if right_prerelease is None:
            return -1

        max_length = max(len(left_prerelease), len(right_prerelease))
        for index in range(max_length):
            if index >= len(left_prerelease):
                return -1
            if index >= len(right_prerelease):
                return 1

            left_kind, left_value = left_prerelease[index]
            right_kind, right_value = right_prerelease[index]
            if left_kind != right_kind:
                return -1 if left_kind < right_kind else 1
            if left_value != right_value:
                return 1 if left_value > right_value else -1
        return 0
