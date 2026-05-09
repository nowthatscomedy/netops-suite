param(
    [Parameter(Mandatory = $true)]
    [string]$Repository,
    [Parameter(Mandatory = $true)]
    [string]$TagName,
    [Parameter(Mandatory = $true)]
    [string]$ReleaseName,
    [Parameter(Mandatory = $true)]
    [string]$AssetPath,
    [string]$ChecksumPath = "",
    [switch]$IsPrerelease
)

$ErrorActionPreference = "Stop"

if (-not $env:GITHUB_TOKEN) {
    throw "The GITHUB_TOKEN environment variable is required."
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

$releaseApi = "https://api.github.com/repos/$Repository/releases/tags/$TagName"
$release = $null

try {
    $release = Invoke-GitHubRest -Method GET -Uri $releaseApi
}
catch {
    $response = $_.Exception.Response
    $statusCode = $null
    if ($response) {
        $statusCode = [int]$response.StatusCode
    }

    if ($statusCode -ne 404) {
        throw
    }
}

if (-not $release) {
    $release = Invoke-GitHubRest -Method POST -Uri "https://api.github.com/repos/$Repository/releases" -Body @{
        tag_name              = $TagName
        name                  = $ReleaseName
        draft                 = $true
        prerelease            = $prereleaseFlag
        generate_release_notes = $true
    }
}

if (-not $release.id) {
    throw "GitHub release id was not returned. Cannot upload release asset."
}

function Publish-ReleaseAsset {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Release,
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string]$ContentType = "application/octet-stream"
    )

    $assetName = Split-Path -Path $Path -Leaf
    $existingAsset = @($Release.assets | Where-Object { $_.name -eq $assetName }) | Select-Object -First 1
    if ($existingAsset) {
        Invoke-GitHubRest -Method DELETE -Uri "https://api.github.com/repos/$Repository/releases/assets/$($existingAsset.id)" | Out-Null
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
        tag_name   = $TagName
        name       = $ReleaseName
        draft      = $false
        prerelease = $prereleaseFlag
    }
    if ($prereleaseFlag) {
        Write-Host "Published prerelease: $TagName"
    }
    else {
        Write-Host "Published release: $TagName"
    }
}
