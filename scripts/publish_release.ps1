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
    [switch]$IsPrerelease,
    [switch]$AllowAssetReplace
)

$ErrorActionPreference = "Stop"

if (-not $env:GITHUB_TOKEN) {
    throw "The GITHUB_TOKEN environment variable is required."
}

if ($TargetCommitish -notmatch '^[0-9a-fA-F]{40}$') {
    throw "TargetCommitish must be the exact 40-character source commit SHA for this release."
}

if (-not (Test-Path $AssetPath)) {
    throw "Release asset was not found: $AssetPath"
}

if (-not [string]::IsNullOrWhiteSpace($ChecksumPath) -and -not (Test-Path $ChecksumPath)) {
    throw "Checksum asset was not found: $ChecksumPath"
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
        return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $apiHeaders -Body $json -ContentType "application/json"
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
        draft                  = $true
        prerelease             = $prereleaseFlag
        generate_release_notes = $true
    }
}

if (-not $release.id) {
    throw "GitHub release id was not returned. Cannot upload release asset."
}

if (-not $release.draft) {
    throw "Refusing to upload assets to an already-published release."
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
    $existingAsset = @($Release.assets | Where-Object { $_.name -eq $assetName }) | Select-Object -First 1
    if ($existingAsset) {
        if (-not $AllowAssetReplace.IsPresent) {
            throw "Release asset already exists: $assetName. Re-run with -AllowAssetReplace only when intentionally replacing release assets."
        }
        Invoke-GitHubRest -Method DELETE -Uri "https://api.github.com/repos/$Repository/releases/assets/$($existingAsset.id)" | Out-Null
        Write-Host "Deleted existing release asset before replacement: $assetName"
    }

    $escapedAssetName = [System.Uri]::EscapeDataString($assetName)
    $uploadUri = "https://uploads.github.com/repos/$Repository/releases/$($Release.id)/assets?name=$escapedAssetName"

    Invoke-RestMethod `
        -Method POST `
        -Uri $uploadUri `
        -Headers $apiHeaders `
        -InFile $Path `
        -ContentType $ContentType | Out-Null

    Write-Host "Uploaded release asset: $assetName"
}

Publish-ReleaseAsset -Release $release -Path $AssetPath
if (-not [string]::IsNullOrWhiteSpace($ChecksumPath)) {
    Publish-ReleaseAsset -Release $release -Path $ChecksumPath -ContentType "text/plain"
}

if ($release.draft) {
    $release = Invoke-GitHubRest -Method PATCH -Uri "https://api.github.com/repos/$Repository/releases/$($release.id)" -Body @{
        tag_name         = $TagName
        target_commitish = $TargetCommitish
        name             = $ReleaseName
        draft            = $false
        prerelease       = $prereleaseFlag
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
