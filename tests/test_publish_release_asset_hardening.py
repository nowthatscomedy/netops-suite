import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLISH_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "publish_release.ps1"


def _publish_script() -> str:
    return PUBLISH_SCRIPT_PATH.read_text(encoding="utf-8")


def test_additional_assets_are_preflighted_before_release_mutation():
    script = _publish_script()

    assert "[string[]]$AdditionalAssetPath = @()" in script
    assert "foreach ($additionalPath in @($AdditionalAssetPath))" in script
    assert "[string]::IsNullOrWhiteSpace([string]$additionalPath)" in script
    assert "Additional release asset was not found" in script
    assert "Test-Path -LiteralPath $additionalPath -PathType Leaf" in script
    assert "Release assets must have unique file names" in script
    assert "foreach ($additionalPath in $validatedAdditionalAssetPaths)" in script

    validation = script.index("$validatedAdditionalAssetPaths = @()")
    first_release_lookup = script.index("$release = Get-ReleaseByTag -Tag $TagName")
    assert validation < first_release_lookup


def test_asset_upload_has_bounded_retry_and_ambiguous_result_reconciliation():
    script = _publish_script()

    assert "function Invoke-ReleaseAssetUpload" in script
    assert "[int]$MaximumAttempts = 3" in script
    assert "for ($attempt = 1; $attempt -le $MaximumAttempts; $attempt++)" in script
    assert "Test-IsRetryableGitHubFailure -ErrorRecord $uploadError" in script
    assert "Get-DraftReleaseSnapshot -ReleaseId $ReleaseId" in script
    assert "Get-MatchingReleaseAsset" in script
    assert "[string]$_.name -ceq $AssetName" in script
    assert "[int64]$_.size -eq $Size" in script
    assert "Confirmed release asset after an ambiguous upload response" in script

    upload = script.index("$uploadedAsset = Invoke-RestMethod")
    reconcile = script.index(
        "$snapshot = Get-DraftReleaseSnapshot -ReleaseId $ReleaseId",
        upload,
    )
    retry = script.index("Start-Sleep -Seconds $retryDelaySeconds", reconcile)
    assert upload < reconcile < retry


def test_draft_asset_replacement_stages_then_promotes_then_removes_backup():
    script = _publish_script()

    stage = script.index("$stagedAsset = Invoke-ReleaseAssetUpload")
    preserve_old = script.index("$backupAsset = Set-ReleaseAssetNameConfirmed", stage)
    promote_new = script.index("$promotedAsset = Set-ReleaseAssetNameConfirmed", preserve_old)
    verify_new = script.index("$verifiedRelease = Get-DraftReleaseSnapshot", promote_new)
    remove_backup = script.index(
        "Remove-ReleaseAssetConfirmed -ReleaseId $Release.id -AssetId $backupAsset.id",
        verify_new,
    )

    assert stage < preserve_old < promote_new < verify_new < remove_backup
    assert "AssetId $existingAsset.id" not in script[
        script.index("function Publish-ReleaseAsset") :
    ].split("$stagedAsset = Invoke-ReleaseAssetUpload", maxsplit=1)[0]
    assert "Deleted existing release asset before replacement" not in script
    assert "the original asset rename could not be rolled back" in script
    assert "the original asset is preserved as $backupName" in script


def test_asset_hardening_keeps_release_immutability_and_commit_binding():
    script = _publish_script()

    assert "Release $TagName is already published and is immutable" in script
    assert "Refusing to upload assets to an already-published release" in script
    assert "Release $ReleaseId is no longer a draft" in script
    assert "target_commitish       = $TargetCommitish" in script
    assert "target_commitish = $TargetCommitish" in script
    assert "Published tag $TagName does not resolve to requested source commit" in script


def test_versioned_release_notes_are_validated_before_mutation_and_published_verbatim():
    script = _publish_script()

    notes_validation = script.index("$releaseNotesBytes = [IO.File]::ReadAllBytes")
    contract_validation = script.index(
        "& python $releaseNotesValidatorPath --tag $TagName --project-root $projectRoot"
    )
    first_release_lookup = script.index("$release = Get-ReleaseByTag -Tag $TagName")
    first_tag_mutation = script.index(
        'Invoke-GitHubRest -Method POST -Uri "https://api.github.com/repos/$Repository/git/refs"'
    )

    assert contract_validation < notes_validation < first_release_lookup < first_tag_mutation
    assert "$semanticReleaseTagPattern" in script
    assert 'Join-Path $releaseNotesRoot "$TagName.md"' in script
    assert "Release notes must be valid UTF-8" in script
    assert "Release notes must contain all required user-facing sections" in script
    assert "Release notes contain an empty section" in script
    assert script.count("body") >= 2
    assert script.count("= $releaseBody") >= 2
    assert "generate_release_notes = $false" in script
    assert "Draft release notes do not match" in script
    assert "Published release notes do not match" in script
    assert "$draftBody -cne $releaseBody" in script
    assert "$publishedBody -cne $releaseBody" in script
    draft_body_check = script.index("$draftBody =")
    first_asset_upload = script.index("Publish-ReleaseAsset -Release $release")
    publish_request = script.index("draft            = $false")
    assert draft_body_check < first_asset_upload < publish_request


def test_github_json_requests_use_explicit_utf8_bytes():
    script = _publish_script()

    assert "$jsonBytes = [Text.Encoding]::UTF8.GetBytes($json)" in script
    assert "-Body $jsonBytes" in script
    assert '-ContentType "application/json; charset=utf-8"' in script
    assert "-Body $json -ContentType" not in script


@pytest.mark.skipif(
    shutil.which("powershell") is None,
    reason="Windows PowerShell 5.1 is required for the encoding regression test.",
)
def test_windows_powershell_sends_korean_release_json_as_utf8_bytes(tmp_path):
    received: dict[str, object] = {}

    class CaptureHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            received["content_type"] = self.headers["Content-Type"]
            received["body"] = self.rfile.read(length)
            response = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        script_path = PROJECT_ROOT / "scripts" / "publish_release.ps1"
        escaped_script_path = str(script_path).replace("'", "''")
        uri = f"http://127.0.0.1:{server.server_port}/capture"
        powershell_script = tmp_path / "capture-release-json.ps1"
        powershell_script.write_text(
            "\n".join(
                (
                    "$ErrorActionPreference = 'Stop'",
                    "$tokens = $null",
                    "$parseErrors = $null",
                    (
                        "$ast = [System.Management.Automation.Language.Parser]::"
                        f"ParseFile('{escaped_script_path}', "
                        "[ref]$tokens, [ref]$parseErrors)"
                    ),
                    "if ($parseErrors.Count -gt 0) { throw $parseErrors[0] }",
                    (
                        "$functionAst = $ast.Find({ param($node) "
                        "$node -is "
                        "[System.Management.Automation.Language.FunctionDefinitionAst] "
                        "-and $node.Name -eq 'Invoke-GitHubRest' }, $true)"
                    ),
                    "if ($null -eq $functionAst) { throw 'Function not found.' }",
                    "$apiHeaders = @{}",
                    "Invoke-Expression $functionAst.Extent.Text",
                    (
                        f"$null = Invoke-GitHubRest -Method POST -Uri '{uri}' "
                        "-Body @{ body = '한글 릴리즈 노트'; name = '새 기능' }"
                    ),
                )
            ),
            encoding="utf-8-sig",
        )

        completed = subprocess.run(
            [
                shutil.which("powershell") or "powershell",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(powershell_script),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    assert completed.returncode == 0, completed.stderr
    assert received["content_type"] == "application/json; charset=utf-8"
    payload = json.loads(received["body"].decode("utf-8"))
    assert payload == {"body": "한글 릴리즈 노트", "name": "새 기능"}
