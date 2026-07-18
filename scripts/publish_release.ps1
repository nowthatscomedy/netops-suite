param(
    [Parameter(Mandatory = $true)]
    [string]$Repository,
    [Parameter(Mandatory = $true)]
    [string]$TagName,
    [Parameter(Mandatory = $true)]
    [string]$ReleaseName,
    [Parameter(Mandatory = $true)]
    [string]$TargetCommitish,
    [Parameter(Mandatory = $true)]
    [string]$AssetPath,
    [string]$ChecksumPath = "",
    [string[]]$AdditionalAssetPath = @(),
    [switch]$IsPrerelease,
    [switch]$AllowAssetReplace
)

$ErrorActionPreference = "Stop"

if (-not $env:GITHUB_TOKEN) {
    throw "The GITHUB_TOKEN environment variable is required."
}

$semanticReleaseTagPattern = '^v\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$'
if ($TagName -notmatch $semanticReleaseTagPattern) {
    throw "TagName must be a semantic version such as v1.0.0."
}

if ($TargetCommitish -notmatch '^[0-9a-fA-F]{40}$') {
    throw "TargetCommitish must be the exact 40-character source commit SHA for this release."
}

if (-not (Test-Path -LiteralPath $AssetPath -PathType Leaf)) {
    throw "Release asset was not found: $AssetPath"
}

if (
    -not [string]::IsNullOrWhiteSpace($ChecksumPath) -and
    -not (Test-Path -LiteralPath $ChecksumPath -PathType Leaf)
) {
    throw "Checksum asset was not found: $ChecksumPath"
}

$validatedAdditionalAssetPaths = @()
foreach ($additionalPath in @($AdditionalAssetPath)) {
    if ([string]::IsNullOrWhiteSpace([string]$additionalPath)) {
        continue
    }
    if (-not (Test-Path -LiteralPath $additionalPath -PathType Leaf)) {
        throw "Additional release asset was not found: $additionalPath"
    }
    $validatedAdditionalAssetPaths += [string]$additionalPath
}

# Reject duplicate output names before creating a tag or draft. GitHub release
# assets share one namespace, even when the source files come from different
# directories.
$assetPathsForNameValidation = @($AssetPath)
if (-not [string]::IsNullOrWhiteSpace($ChecksumPath)) {
    $assetPathsForNameValidation += $ChecksumPath
}
$assetPathsForNameValidation += $validatedAdditionalAssetPaths
$assetNames = @{}
foreach ($candidatePath in $assetPathsForNameValidation) {
    $candidateName = Split-Path -Path $candidatePath -Leaf
    if ($assetNames.ContainsKey($candidateName)) {
        throw "Release assets must have unique file names: $candidateName"
    }
    $assetNames[$candidateName] = $true
}

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$releaseNotesRoot = Join-Path $projectRoot "release-notes"
$releaseNotesPath = Join-Path $releaseNotesRoot "$TagName.md"
if (-not (Test-Path -LiteralPath $releaseNotesRoot -PathType Container)) {
    throw "Release notes directory was not found: $releaseNotesRoot"
}
$releaseNotesRootItem = Get-Item -LiteralPath $releaseNotesRoot
if ($releaseNotesRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "Release notes directory must not be a reparse point: $releaseNotesRoot"
}
if (-not (Test-Path -LiteralPath $releaseNotesPath -PathType Leaf)) {
    throw "Versioned release notes were not found: $releaseNotesPath"
}
$releaseNotesValidatorPath = Join-Path $projectRoot "scripts\validate_release_notes.py"
if (-not (Test-Path -LiteralPath $releaseNotesValidatorPath -PathType Leaf)) {
    throw "Release notes validator was not found: $releaseNotesValidatorPath"
}
$releaseNotesValidatorItem = Get-Item -LiteralPath $releaseNotesValidatorPath
if ($releaseNotesValidatorItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "Release notes validator must not be a reparse point: $releaseNotesValidatorPath"
}
& python $releaseNotesValidatorPath --tag $TagName --project-root $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Versioned release notes validation failed for $TagName."
}
$releaseNotesItem = Get-Item -LiteralPath $releaseNotesPath
if ($releaseNotesItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "Release notes file must not be a reparse point: $releaseNotesPath"
}
$resolvedReleaseNotesRoot = [IO.Path]::GetFullPath($releaseNotesRootItem.FullName)
$resolvedReleaseNotesPath = [IO.Path]::GetFullPath($releaseNotesItem.FullName)
$expectedNotesPrefix = $resolvedReleaseNotesRoot.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
) + [IO.Path]::DirectorySeparatorChar
if (-not $resolvedReleaseNotesPath.StartsWith(
    $expectedNotesPrefix,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Release notes must stay inside $resolvedReleaseNotesRoot."
}

$releaseNotesBytes = [IO.File]::ReadAllBytes($resolvedReleaseNotesPath)
if ($releaseNotesBytes.Length -eq 0 -or $releaseNotesBytes.Length -gt 100KB) {
    throw "Release notes must be non-empty and no larger than 100 KiB."
}
if (
    $releaseNotesBytes.Length -ge 3 -and
    $releaseNotesBytes[0] -eq 0xEF -and
    $releaseNotesBytes[1] -eq 0xBB -and
    $releaseNotesBytes[2] -eq 0xBF
) {
    throw "Release notes must be UTF-8 without a byte-order mark."
}
try {
    $strictUtf8 = [Text.UTF8Encoding]::new($false, $true)
    $releaseBody = $strictUtf8.GetString($releaseNotesBytes)
}
catch {
    throw "Release notes must be valid UTF-8: $resolvedReleaseNotesPath"
}
if ($releaseBody.Contains([char]0)) {
    throw "Release notes must not contain NUL bytes."
}
$releaseBody = $releaseBody.Replace("`r`n", "`n").Replace("`r", "`n").Trim()
if ($releaseBody.Length -lt 500) {
    throw "Release notes are too short to be a detailed user-facing summary."
}
$firstReleaseNotesLine = @(
    $releaseBody -split "`n" |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
) | Select-Object -First 1
$expectedReleaseNotesTitle = "# NetOps Suite $TagName"
if ($firstReleaseNotesLine -ne $expectedReleaseNotesTitle) {
    throw "Release notes must start with: $expectedReleaseNotesTitle"
}
if ($releaseBody -match '(?im)\b(?:TODO|TBD)\b') {
    throw "Release notes still contain placeholder text."
}
$releaseNoteSections = [regex]::Matches($releaseBody, '(?m)^##\s+\S.*$')
if ($releaseNoteSections.Count -lt 7) {
    throw "Release notes must contain all required user-facing sections."
}
for ($sectionIndex = 0; $sectionIndex -lt $releaseNoteSections.Count; $sectionIndex++) {
    $contentStart = $releaseNoteSections[$sectionIndex].Index +
        $releaseNoteSections[$sectionIndex].Length
    $contentEnd = if ($sectionIndex + 1 -lt $releaseNoteSections.Count) {
        $releaseNoteSections[$sectionIndex + 1].Index
    }
    else {
        $releaseBody.Length
    }
    if ($contentEnd -le $contentStart) {
        throw "Release notes contain an empty section."
    }
    $sectionContent = $releaseBody.Substring(
        $contentStart,
        $contentEnd - $contentStart
    ).Trim()
    if ([string]::IsNullOrWhiteSpace($sectionContent)) {
        throw "Release notes contain an empty section."
    }
}

$apiHeaders = @{
    Authorization           = "Bearer $env:GITHUB_TOKEN"
    Accept                  = "application/vnd.github+json"
    "X-GitHub-Api-Version"  = "2022-11-28"
    "User-Agent"            = "NetOpsSuite-Release"
}

function Test-IsPrereleaseTag {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TagName
    )

    $normalized = $TagName.Trim()
    if ($normalized.StartsWith("v")) {
        $normalized = $normalized.Substring(1)
    }

    return $normalized -match "-"
}

$prereleaseFlag = $IsPrerelease.IsPresent -or (Test-IsPrereleaseTag -TagName $TagName)

function Invoke-GitHubRest {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("GET", "POST", "PATCH", "DELETE")]
        [string]$Method,
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [object]$Body = $null
    )

    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 10
        $jsonBytes = [Text.Encoding]::UTF8.GetBytes($json)
        return Invoke-RestMethod `
            -Method $Method `
            -Uri $Uri `
            -Headers $apiHeaders `
            -Body $jsonBytes `
            -ContentType "application/json; charset=utf-8"
    }

    return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $apiHeaders
}

function Get-ReleaseByTag {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Tag
    )

    $escapedTag = [System.Uri]::EscapeDataString($Tag)
    try {
        return Invoke-GitHubRest -Method GET -Uri "https://api.github.com/repos/$Repository/releases/tags/$escapedTag"
    }
    catch {
        $statusCode = $null
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        if ($statusCode -ne 404) {
            throw
        }
    }

    # The tag endpoint omits unpublished drafts, so inspect the authenticated
    # release list before deciding that a release does not exist.
    $releases = @(Invoke-GitHubRest -Method GET -Uri "https://api.github.com/repos/$Repository/releases?per_page=100")
    return @($releases | Where-Object { $_.tag_name -eq $Tag }) | Select-Object -First 1
}

$release = Get-ReleaseByTag -Tag $TagName

function Resolve-TagCommitSha {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Tag
    )

    $escapedTag = [System.Uri]::EscapeDataString($Tag)
    try {
        $reference = Invoke-GitHubRest -Method GET -Uri "https://api.github.com/repos/$Repository/git/ref/tags/$escapedTag"
    }
    catch {
        $statusCode = $null
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        if ($statusCode -eq 404) {
            return ""
        }
        throw
    }

    $objectType = [string]$reference.object.type
    $objectSha = [string]$reference.object.sha
    for ($depth = 0; $depth -lt 5 -and $objectType -eq "tag"; $depth++) {
        $tagObject = Invoke-GitHubRest -Method GET -Uri "https://api.github.com/repos/$Repository/git/tags/$objectSha"
        $objectType = [string]$tagObject.object.type
        $objectSha = [string]$tagObject.object.sha
    }
    if ($objectType -ne "commit") {
        throw "Tag $Tag does not resolve to a commit."
    }
    return $objectSha
}

$releaseWasExisting = $null -ne $release
if ($releaseWasExisting) {
    if (-not $release.draft) {
        throw "Release $TagName is already published and is immutable. Create a new version instead."
    }
    if (-not $AllowAssetReplace.IsPresent) {
        throw "Draft release $TagName already exists. Use -AllowAssetReplace only to repair this unpublished draft."
    }
}

$existingTagCommit = Resolve-TagCommitSha -Tag $TagName
if ($existingTagCommit -and $existingTagCommit -ne $TargetCommitish) {
    throw "Existing tag $TagName points to $existingTagCommit, not requested source commit $TargetCommitish."
}
if (-not $existingTagCommit) {
    try {
        Invoke-GitHubRest -Method POST -Uri "https://api.github.com/repos/$Repository/git/refs" -Body @{
            ref = "refs/tags/$TagName"
            sha = $TargetCommitish
        } | Out-Null
    }
    catch {
        # A concurrent run may have created the tag after the initial read.
        # Resolve it below and accept it only when it points to the same commit.
        $statusCode = $null
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        if ($statusCode -ne 422) {
            throw
        }
    }
    # GitHub may briefly return 404 immediately after creating a ref. Retry
    # only the empty/not-yet-visible case; a visible mismatched SHA fails below.
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        $existingTagCommit = Resolve-TagCommitSha -Tag $TagName
        if ($existingTagCommit -or $attempt -eq 5) {
            break
        }
        Start-Sleep -Seconds 2
    }
    if ($existingTagCommit -ne $TargetCommitish) {
        throw "Could not create tag $TagName at requested source commit $TargetCommitish."
    }
}

if (-not $release) {
    $release = Invoke-GitHubRest -Method POST -Uri "https://api.github.com/repos/$Repository/releases" -Body @{
        tag_name               = $TagName
        target_commitish       = $TargetCommitish
        name                   = $ReleaseName
        body                   = $releaseBody
        draft                  = $true
        prerelease             = $prereleaseFlag
        generate_release_notes = $false
    }
}
else {
    $release = Invoke-GitHubRest -Method PATCH -Uri "https://api.github.com/repos/$Repository/releases/$($release.id)" -Body @{
        tag_name         = $TagName
        target_commitish = $TargetCommitish
        name             = $ReleaseName
        body             = $releaseBody
        draft            = $true
        prerelease       = $prereleaseFlag
    }
}

if (-not $release.id) {
    throw "GitHub release id was not returned. Cannot upload release asset."
}

if (-not $release.draft) {
    throw "Refusing to upload assets to an already-published release."
}
$draftBody = ([string]$release.body).Replace("`r`n", "`n").Replace("`r", "`n").Trim()
if ($draftBody -cne $releaseBody) {
    throw "Draft release notes do not match the validated versioned notes. The release was not published."
}

function Get-GitHubStatusCode {
    param(
        [Parameter(Mandatory = $true)]
        [object]$ErrorRecord
    )

    try {
        if ($ErrorRecord.Exception.Response) {
            return [int]$ErrorRecord.Exception.Response.StatusCode
        }
    }
    catch {
        return $null
    }
    return $null
}

function Test-IsRetryableGitHubFailure {
    param(
        [Parameter(Mandatory = $true)]
        [object]$ErrorRecord
    )

    $statusCode = Get-GitHubStatusCode -ErrorRecord $ErrorRecord
    if ($null -eq $statusCode) {
        # Connection resets and timeouts generally do not expose an HTTP
        # response. Their outcome is ambiguous, so callers reconcile state
        # before retrying.
        return $true
    }
    return @(408, 409, 429, 500, 502, 503, 504) -contains $statusCode
}

function Get-DraftReleaseSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [long]$ReleaseId
    )

    $snapshot = Invoke-GitHubRest `
        -Method GET `
        -Uri "https://api.github.com/repos/$Repository/releases/$ReleaseId"
    if (-not $snapshot.draft) {
        throw "Release $ReleaseId is no longer a draft. Refusing to modify its assets."
    }
    return $snapshot
}

function Get-ReleaseAssetById {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Release,
        [Parameter(Mandatory = $true)]
        [long]$AssetId
    )

    return @(
        $Release.assets |
            Where-Object { [int64]$_.id -eq $AssetId }
    ) | Select-Object -First 1
}

function Get-MatchingReleaseAsset {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Release,
        [Parameter(Mandatory = $true)]
        [string]$AssetName,
        [Parameter(Mandatory = $true)]
        [long]$Size
    )

    return @(
        $Release.assets |
            Where-Object {
                [string]$_.name -ceq $AssetName -and
                [int64]$_.size -eq $Size
            }
    ) | Select-Object -First 1
}

function Invoke-ReleaseAssetUpload {
    param(
        [Parameter(Mandatory = $true)]
        [long]$ReleaseId,
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$AssetName,
        [Parameter(Mandatory = $true)]
        [string]$ContentType,
        [ValidateRange(1, 10)]
        [int]$MaximumAttempts = 3
    )

    $fileSize = [int64](Get-Item -LiteralPath $Path).Length
    $escapedAssetName = [System.Uri]::EscapeDataString($AssetName)
    $uploadUri = "https://uploads.github.com/repos/$Repository/releases/$ReleaseId/assets?name=$escapedAssetName"

    for ($attempt = 1; $attempt -le $MaximumAttempts; $attempt++) {
        try {
            $uploadedAsset = Invoke-RestMethod `
                -Method POST `
                -Uri $uploadUri `
                -Headers $apiHeaders `
                -InFile $Path `
                -ContentType $ContentType
            if (
                [string]$uploadedAsset.name -cne $AssetName -or
                [int64]$uploadedAsset.size -ne $fileSize
            ) {
                throw "GitHub returned an unexpected asset after uploading $AssetName."
            }
            return $uploadedAsset
        }
        catch {
            $uploadError = $_

            # A connection can fail after GitHub persisted all bytes but before
            # the response reached the runner. Re-read the draft before any
            # retry so a matching name and byte size is accepted as success
            # instead of creating a duplicate or failing on a name collision.
            try {
                $snapshot = Get-DraftReleaseSnapshot -ReleaseId $ReleaseId
                $matchingAsset = Get-MatchingReleaseAsset `
                    -Release $snapshot `
                    -AssetName $AssetName `
                    -Size $fileSize
                if ($matchingAsset) {
                    Write-Host "Confirmed release asset after an ambiguous upload response: $AssetName"
                    return $matchingAsset
                }
            }
            catch {
                if ($_.Exception.Message -like "*is no longer a draft*") {
                    throw
                }
                Write-Warning "Could not reconcile release assets after upload attempt $attempt for $AssetName."
            }

            if (
                $attempt -ge $MaximumAttempts -or
                -not (Test-IsRetryableGitHubFailure -ErrorRecord $uploadError)
            ) {
                throw $uploadError
            }

            $retryDelaySeconds = [Math]::Min(8, [Math]::Pow(2, $attempt))
            Write-Warning "Release asset upload attempt $attempt failed for $AssetName. Retrying in $retryDelaySeconds seconds."
            Start-Sleep -Seconds $retryDelaySeconds
        }
    }

    throw "Release asset upload exhausted all attempts: $AssetName"
}

function Set-ReleaseAssetNameConfirmed {
    param(
        [Parameter(Mandatory = $true)]
        [long]$ReleaseId,
        [Parameter(Mandatory = $true)]
        [long]$AssetId,
        [Parameter(Mandatory = $true)]
        [string]$AssetName,
        [ValidateRange(1, 10)]
        [int]$MaximumAttempts = 3
    )

    for ($attempt = 1; $attempt -le $MaximumAttempts; $attempt++) {
        try {
            $renamedAsset = Invoke-GitHubRest `
                -Method PATCH `
                -Uri "https://api.github.com/repos/$Repository/releases/assets/$AssetId" `
                -Body @{ name = $AssetName }
            if ([string]$renamedAsset.name -ceq $AssetName) {
                return $renamedAsset
            }
            throw "GitHub returned an unexpected name while renaming release asset $AssetId."
        }
        catch {
            $renameError = $_
            $snapshot = Get-DraftReleaseSnapshot -ReleaseId $ReleaseId
            $currentAsset = Get-ReleaseAssetById -Release $snapshot -AssetId $AssetId
            if ($currentAsset -and [string]$currentAsset.name -ceq $AssetName) {
                return $currentAsset
            }
            if (-not $currentAsset) {
                throw "Release asset $AssetId disappeared while renaming it to $AssetName."
            }
            if (
                $attempt -ge $MaximumAttempts -or
                -not (Test-IsRetryableGitHubFailure -ErrorRecord $renameError)
            ) {
                throw $renameError
            }
            Start-Sleep -Seconds ([Math]::Min(8, [Math]::Pow(2, $attempt)))
        }
    }

    throw "Release asset rename exhausted all attempts: $AssetId"
}

function Remove-ReleaseAssetConfirmed {
    param(
        [Parameter(Mandatory = $true)]
        [long]$ReleaseId,
        [Parameter(Mandatory = $true)]
        [long]$AssetId,
        [ValidateRange(1, 10)]
        [int]$MaximumAttempts = 3
    )

    for ($attempt = 1; $attempt -le $MaximumAttempts; $attempt++) {
        try {
            Invoke-GitHubRest `
                -Method DELETE `
                -Uri "https://api.github.com/repos/$Repository/releases/assets/$AssetId" |
                Out-Null
            return
        }
        catch {
            $deleteError = $_
            $snapshot = Get-DraftReleaseSnapshot -ReleaseId $ReleaseId
            $currentAsset = Get-ReleaseAssetById -Release $snapshot -AssetId $AssetId
            if (-not $currentAsset) {
                return
            }
            if (
                $attempt -ge $MaximumAttempts -or
                -not (Test-IsRetryableGitHubFailure -ErrorRecord $deleteError)
            ) {
                throw $deleteError
            }
            Start-Sleep -Seconds ([Math]::Min(8, [Math]::Pow(2, $attempt)))
        }
    }

    throw "Release asset deletion exhausted all attempts: $AssetId"
}

function Publish-ReleaseAsset {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Release,
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string]$ContentType = "application/octet-stream"
    )

    if (-not $Release.draft) {
        throw "Refusing to replace assets on an already-published release."
    }

    $assetName = Split-Path -Path $Path -Leaf
    $fileSize = [int64](Get-Item -LiteralPath $Path).Length
    $currentRelease = Get-DraftReleaseSnapshot -ReleaseId $Release.id
    $existingAsset = @(
        $currentRelease.assets |
            Where-Object { [string]$_.name -ceq $assetName }
    ) | Select-Object -First 1

    if (-not $existingAsset) {
        $uploadedAsset = Invoke-ReleaseAssetUpload `
            -ReleaseId $Release.id `
            -Path $Path `
            -AssetName $assetName `
            -ContentType $ContentType
        Write-Host "Uploaded release asset: $($uploadedAsset.name)"
        return
    }

    if (-not $AllowAssetReplace.IsPresent) {
        throw "Release asset already exists: $assetName. Re-run with -AllowAssetReplace only when intentionally replacing release assets."
    }

    # Keep the existing good asset available until a complete replacement has
    # been uploaded and verified. Promotion uses reversible renames; the old
    # asset is deleted only after the staged asset owns the final name.
    $replacementId = [Guid]::NewGuid().ToString("N")
    $extension = [System.IO.Path]::GetExtension($assetName)
    $stagingName = ".netops-stage-$replacementId$extension"
    $backupName = ".netops-backup-$replacementId$extension"
    $stagedAsset = Invoke-ReleaseAssetUpload `
        -ReleaseId $Release.id `
        -Path $Path `
        -AssetName $stagingName `
        -ContentType $ContentType

    try {
        $backupAsset = Set-ReleaseAssetNameConfirmed `
            -ReleaseId $Release.id `
            -AssetId $existingAsset.id `
            -AssetName $backupName
    }
    catch {
        $backupError = $_
        try {
            Remove-ReleaseAssetConfirmed -ReleaseId $Release.id -AssetId $stagedAsset.id
        }
        catch {
            Write-Warning "Could not remove staged asset $stagingName after the backup rename failed. The draft was not published."
        }
        throw $backupError
    }

    try {
        $promotedAsset = Set-ReleaseAssetNameConfirmed `
            -ReleaseId $Release.id `
            -AssetId $stagedAsset.id `
            -AssetName $assetName
        $verifiedRelease = Get-DraftReleaseSnapshot -ReleaseId $Release.id
        $verifiedAsset = @(
            $verifiedRelease.assets |
                Where-Object {
                    [int64]$_.id -eq [int64]$promotedAsset.id -and
                    [string]$_.name -ceq $assetName -and
                    [int64]$_.size -eq $fileSize
                }
        ) | Select-Object -First 1
        if (-not $verifiedAsset) {
            throw "Replacement asset could not be verified after promotion: $assetName"
        }
    }
    catch {
        $promotionError = $_
        try {
            Set-ReleaseAssetNameConfirmed `
                -ReleaseId $Release.id `
                -AssetId $backupAsset.id `
                -AssetName $assetName | Out-Null
        }
        catch {
            throw "Replacement of $assetName failed and the original asset rename could not be rolled back. The release remains a draft; the original asset is preserved as $backupName. Promotion error: $($promotionError.Exception.Message) Rollback error: $($_.Exception.Message)"
        }

        try {
            Remove-ReleaseAssetConfirmed -ReleaseId $Release.id -AssetId $stagedAsset.id
        }
        catch {
            Write-Warning "Could not remove staged asset $stagingName after rollback. The original asset was restored and the draft was not published."
        }
        throw $promotionError
    }

    # At this point the replacement is independently verified under the final
    # name. It is now safe to remove the backup.
    Remove-ReleaseAssetConfirmed -ReleaseId $Release.id -AssetId $backupAsset.id
    Write-Host "Replaced release asset safely: $assetName"
}

Publish-ReleaseAsset -Release $release -Path $AssetPath
if (-not [string]::IsNullOrWhiteSpace($ChecksumPath)) {
    Publish-ReleaseAsset -Release $release -Path $ChecksumPath -ContentType "text/plain"
}
foreach ($additionalPath in $validatedAdditionalAssetPaths) {
    Publish-ReleaseAsset -Release $release -Path $additionalPath
}

if ($release.draft) {
    $release = Invoke-GitHubRest -Method PATCH -Uri "https://api.github.com/repos/$Repository/releases/$($release.id)" -Body @{
        tag_name         = $TagName
        target_commitish = $TargetCommitish
        name             = $ReleaseName
        body             = $releaseBody
        draft            = $false
        prerelease       = $prereleaseFlag
    }
    if ($release.draft) {
        throw "GitHub returned a draft release after the publish request: $TagName"
    }
    $publishedBody = ([string]$release.body).Replace("`r`n", "`n").Replace("`r", "`n").Trim()
    if ($publishedBody -cne $releaseBody) {
        throw "Published release notes do not match the validated versioned notes."
    }
    if ($prereleaseFlag) {
        Write-Host "Published prerelease: $TagName"
    }
    else {
        Write-Host "Published release: $TagName"
    }
}

$publishedTagCommit = Resolve-TagCommitSha -Tag $TagName
if ($publishedTagCommit -ne $TargetCommitish) {
    throw "Published tag $TagName does not resolve to requested source commit $TargetCommitish."
}
