import re
from pathlib import Path

import yaml

from app import __version__ as app_version
from app.version import __version__ as source_version


def test_version_contract():
    assert app_version == source_version


def test_release_workflow_defaults_to_current_version():
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    expected_tag = f"v{source_version}"

    assert f'default: "{expected_tag}"' in workflow
    assert f"example: {expected_tag}" in workflow


def test_release_workflow_gates_publish_and_binds_it_to_checked_out_commit():
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "python -m pip check" in workflow
    assert "python scripts/audit_dependencies.py" in workflow
    assert "python -m ruff check ." in workflow
    assert "python -m compileall -q main.py app netops_suite qa scripts tests" in workflow
    assert "python -m pytest -q" in workflow
    assert "Gitleaks.Gitleaks" in workflow
    assert "gitleaks git --no-banner --redact --exit-code 1" in workflow
    assert "gitleaks dir --no-banner --redact --exit-code 1" in workflow
    assert '"pyinstaller==6.21.0"' in workflow
    assert '"pyinstaller-hooks-contrib==2026.6"' in workflow
    assert 'python-version: "3.11.15"' in workflow
    assert "refs/heads/main" in workflow
    assert "python-dependencies.cdx.json" in workflow
    assert "--requirements requirements-lock.txt" in workflow
    assert "AdditionalAssetPath = @($dependencySbom)" in workflow
    assert "JRSoftware.InnoSetup" in workflow
    assert "--version 6.7.3" in workflow
    assert "TargetCommitish = $env:TARGET_COMMITISH" in workflow
    assert "NetOpsSuite-setup-$($env:RELEASE_VERSION).exe" in workflow
    assert "already published and is immutable" in workflow
    assert "manual_replace_existing_draft" in workflow
    assert "WINDOWS_CODESIGN_CERT_BASE64" in workflow
    assert "allow_unsigned_release" in workflow
    assert "Set allow_unsigned_release=true only for an explicitly approved unsigned release" in workflow
    assert "Unsigned release artifact explicitly approved" in workflow
    assert "-RequireCodeSigning" in workflow
    assert "Get-AuthenticodeSignature" in workflow
    assert "SignatureStatus]::Valid" in workflow


def test_release_workflow_does_not_interpolate_contexts_inside_shell_scripts():
    workflow_text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    shell_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "run" in step
    ]

    assert shell_steps
    assert [
        step.get("name", "<unnamed>")
        for step in shell_steps
        if "${{" in step["run"]
    ] == []


def test_ci_workflow_runs_full_quality_gates():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    runtime_requirements = Path("requirements.txt").read_text(encoding="utf-8")
    dev_requirements = Path("requirements-dev.txt").read_text(encoding="utf-8")
    runtime_lock = Path("requirements-lock.txt").read_text(encoding="utf-8")
    dev_lock = Path("requirements-dev-lock.txt").read_text(encoding="utf-8")
    install_script = Path("scripts/install_dev.ps1").read_text(encoding="utf-8")
    test_script = Path("test.bat").read_text(encoding="utf-8")

    assert "pytest" not in runtime_requirements
    assert "ruff" not in runtime_requirements
    assert "-r requirements.txt" in dev_requirements
    assert "pytest>=9.0.3,<10" in dev_requirements
    assert "ruff>=0.15,<1" in dev_requirements
    assert "pip-audit>=2.9,<3" in dev_requirements
    assert "--hash=sha256:" in runtime_lock
    assert "--hash=sha256:" in dev_lock
    assert "cryptography==49.0.0" in runtime_lock
    assert "pyopenssl==26.3.0" in runtime_lock
    assert "pygments==2.20.0" in runtime_lock
    assert "requirements-dev-lock.txt" in install_script
    assert "requirements-dev-lock.txt" in workflow
    assert "python -m pip check" in workflow
    assert "python scripts/audit_dependencies.py" in workflow
    assert "python -m ruff check ." in workflow
    assert "python -m compileall -q main.py app netops_suite qa scripts tests" in workflow
    assert "python -m pytest -q" in workflow
    assert "-m pip check" in test_script
    assert "scripts\\audit_dependencies.py" in test_script
    assert "-m ruff check ." in test_script
    assert "-m compileall -q main.py app netops_suite qa scripts tests" in test_script
    assert "-m pytest -q" in test_script


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
    assert "function Assert-PathInsideRepository" in build_script
    assert "function Copy-InspectorRuntimePayload" in build_script
    assert "-Source $stagingInspectorRuntimeDir" in build_script
    assert "Inspector runtime cache file leaked into the packaged application" in build_script
    assert (
        "-Source (Join-Path $repoRoot 'netops_suite\\modules\\inspector_runtime')"
        not in build_script
    )
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
    assert "for ($attempt = 1; $attempt -le 5; $attempt++)" in publish_script
    assert "Start-Sleep -Seconds 2" in publish_script
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


def test_release_build_generates_pyinstaller_spec_outside_the_source_tree():
    build_script = Path("scripts/build_release.ps1").read_text(encoding="utf-8")
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "*.spec" in gitignore
    assert '"--specpath", $stagingDir' in build_script
    assert "C:/Users/" not in build_script
    assert "C:\\Users\\" not in build_script
