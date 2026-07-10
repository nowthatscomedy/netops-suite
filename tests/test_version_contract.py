import re
from pathlib import Path

from app import __version__ as app_version
from app.version import __version__ as source_version


def test_version_contract():
    assert app_version == source_version


def test_release_workflow_defaults_to_current_version():
    workflow = open(".github/workflows/release.yml", encoding="utf-8").read()
    expected_tag = f"v{source_version}"

    assert f'default: "{expected_tag}"' in workflow
    assert f"example: {expected_tag}" in workflow


def test_release_workflow_gates_publish_and_binds_it_to_checked_out_commit():
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "python -m pip check" in workflow
    assert "python -m compileall -q main.py app netops_suite tests" in workflow
    assert "python -m pytest -q" in workflow
    assert "Gitleaks.Gitleaks" in workflow
    assert "gitleaks git --no-banner --redact --exit-code 1" in workflow
    assert "gitleaks dir --no-banner --redact --exit-code 1" in workflow
    assert '"pyinstaller==6.21.0"' in workflow
    assert "JRSoftware.InnoSetup" in workflow
    assert "--version 6.7.3" in workflow
    assert 'TargetCommitish = "${{ github.sha }}"' in workflow
    assert 'NetOpsSuite-setup-${{ steps.meta.outputs.version }}.exe' in workflow
    assert "already published and is immutable" in workflow
    assert "manual_replace_existing_draft" in workflow
    assert "WINDOWS_CODESIGN_CERT_BASE64" in workflow
    assert "allow_unsigned_release" in workflow
    assert "Set allow_unsigned_release=true only for an explicitly approved unsigned release" in workflow
    assert "Unsigned release artifact explicitly approved" in workflow
    assert "-RequireCodeSigning" in workflow
    assert "Get-AuthenticodeSignature" in workflow
    assert "SignatureStatus]::Valid" in workflow


def test_release_scripts_enforce_version_and_immutable_publish_contract():
    build_script = Path("scripts/build_release.ps1").read_text(encoding="utf-8")
    publish_script = Path("scripts/publish_release.ps1").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "$semanticVersionPattern" in build_script
    assert "does not match app/version.py version" in build_script
    assert 'ArgumentList "--release-smoke-test"' in build_script
    assert "-WindowStyle Hidden" in build_script
    assert "Packaged executable smoke test failed" in build_script
    assert '"--version-file", $versionInfoPath' in build_script
    assert "StringStruct('ProductVersion', '$normalizedVersion')" in build_script
    assert '"NetOpsSuite-setup-$normalizedVersion.exe"' in build_script
    assert '$env:LOCALAPPDATA "Programs\\Inno Setup 6\\ISCC.exe"' in build_script
    assert "[string]$TargetCommitish" in publish_script
    assert re.search(r"target_commitish\s*=\s*\$TargetCommitish", publish_script)
    assert "40-character source commit SHA" in publish_script
    assert "Resolve-TagCommitSha" in publish_script
    assert "Get-ReleaseByTag" in publish_script
    assert "releases?per_page=100" in publish_script
    assert "release-by-tag endpoint does not return unpublished drafts" in workflow
    assert "$releaseWasExisting = $null -ne $release" in publish_script
    existing_draft_guard = publish_script.index("if ($releaseWasExisting)")
    new_release_creation = publish_script.index("if (-not $release) {", existing_draft_guard)
    assert existing_draft_guard < new_release_creation
    assert "Existing tag $TagName points to" in publish_script
    assert 'ref = "refs/tags/$TagName"' in publish_script
    assert "Could not create tag $TagName at requested source commit" in publish_script
    assert "Published tag $TagName does not resolve" in publish_script
    assert "Refusing to upload assets to an already-published release" in publish_script
    assert "Use -AllowAssetReplace only to repair this unpublished draft" in publish_script


def test_installer_and_repository_release_safety_contract():
    installer = Path("installer/netops-suite.iss").read_text(encoding="utf-8")
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    integration_readme = Path("README_NETOPS_SUITE.md").read_text(encoding="utf-8")

    assert "MinVersion=10.0.17763" in installer
    for secret_pattern in ("*.pfx", "*.p12", "*.pvk", "*.key", "*.pem"):
        assert secret_pattern in gitignore
    assert f"-Version {source_version} -Clean" in readme
    assert "Windows 10 버전 1809(빌드 17763) 이상" in readme
    assert "공식 GitHub 릴리즈는 기본적으로 Windows 코드서명을 필수로 검증" in readme
    assert "allow_unsigned_release" in readme
    assert "75 passed" not in integration_readme


def test_release_build_bundles_only_existing_repository_data_paths():
    project_root = Path(__file__).resolve().parents[1]
    build_script = (project_root / "scripts" / "build_release.ps1").read_text(encoding="utf-8")
    relative_sources = re.findall(r"-Source \(Join-Path \$repoRoot '([^']+)'\)", build_script)

    assert relative_sources
    missing = [
        source
        for source in relative_sources
        if not (project_root / source.replace("\\", "/")).exists()
    ]
    assert missing == []
    assert "netops_suite/modules/inspector/vendor_profiles" in build_script.replace("\\", "/")
