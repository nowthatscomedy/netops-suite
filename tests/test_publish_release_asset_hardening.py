from pathlib import Path


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
