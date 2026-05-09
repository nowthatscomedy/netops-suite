param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [switch]$Clean,
    [string]$SignToolPath = "",
    [string]$CodeSigningCertPath = "",
    [string]$CodeSigningCertPassword = "",
    [string]$CodeSigningCertSha1 = "",
    [string]$TimestampUrl = "",
    [switch]$RequireCodeSigning
)

$ErrorActionPreference = "Stop"

function Format-PyInstallerBundleArg {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    $normalizedSource = $Source -replace "\\", "/"
    $normalizedDestination = $Destination -replace "\\", "/"
    return "$normalizedSource;$normalizedDestination"
}

function Resolve-IsccPath {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles} "Inno Setup 6\ISCC.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    throw "ISCC.exe from Inno Setup 6 was not found. Please install Inno Setup first."
}

function Test-IsTruthy {
    param([string]$Value)

    return $Value -match "^(1|true|yes|on)$"
}

function Resolve-SignToolPath {
    param([string]$ExplicitPath)

    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        if (Test-Path $ExplicitPath) {
            return (Resolve-Path $ExplicitPath).Path
        }
        throw "SignTool was not found: $ExplicitPath"
    }

    $command = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $windowsKitsBin = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path $windowsKitsBin) {
        $candidate = Get-ChildItem -LiteralPath $windowsKitsBin -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1

        if ($candidate) {
            return $candidate.FullName
        }
    }

    return $null
}

function New-CodeSigningConfig {
    $certPath = $CodeSigningCertPath
    if ([string]::IsNullOrWhiteSpace($certPath)) {
        $certPath = $env:WINDOWS_CODESIGN_CERT_PATH
    }

    $certPassword = $CodeSigningCertPassword
    if ([string]::IsNullOrWhiteSpace($certPassword)) {
        $certPassword = $env:WINDOWS_CODESIGN_CERT_PASSWORD
    }

    $certSha1 = $CodeSigningCertSha1
    if ([string]::IsNullOrWhiteSpace($certSha1)) {
        $certSha1 = $env:WINDOWS_CODESIGN_CERT_SHA1
    }

    $timestamp = $TimestampUrl
    if ([string]::IsNullOrWhiteSpace($timestamp)) {
        $timestamp = $env:WINDOWS_CODESIGN_TIMESTAMP_URL
    }
    if ([string]::IsNullOrWhiteSpace($timestamp)) {
        $timestamp = "http://timestamp.digicert.com"
    }

    $signingRequired = $RequireCodeSigning.IsPresent -or (Test-IsTruthy -Value $env:WINDOWS_CODESIGN_REQUIRED)
    $hasSigningIdentity = -not [string]::IsNullOrWhiteSpace($certPath) -or -not [string]::IsNullOrWhiteSpace($certSha1)

    if (-not $hasSigningIdentity) {
        if ($signingRequired) {
            throw "Code signing is required, but no signing certificate was provided."
        }
        Write-Host "Code signing certificate was not provided. Release binaries will be unsigned."
        return $null
    }

    if (-not [string]::IsNullOrWhiteSpace($certPath) -and -not (Test-Path $certPath)) {
        throw "Code signing certificate was not found: $certPath"
    }

    $requestedSignToolPath = $SignToolPath
    if ([string]::IsNullOrWhiteSpace($requestedSignToolPath)) {
        $requestedSignToolPath = $env:WINDOWS_SIGNTOOL_PATH
    }

    $resolvedSignTool = Resolve-SignToolPath -ExplicitPath $requestedSignToolPath
    if (-not $resolvedSignTool) {
        throw "signtool.exe was not found. Install Windows SDK or set -SignToolPath."
    }

    return [pscustomobject]@{
        SignToolPath = $resolvedSignTool
        CertPath     = $certPath
        CertPassword = $certPassword
        CertSha1     = $certSha1
        TimestampUrl = $timestamp
    }
}

function Invoke-CodeSignFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [object]$Config
    )

    if (-not $Config) {
        return
    }

    if (-not (Test-Path $Path)) {
        throw "File to sign was not found: $Path"
    }

    $signArgs = @("sign", "/fd", "SHA256", "/tr", $Config.TimestampUrl, "/td", "SHA256")

    if (-not [string]::IsNullOrWhiteSpace($Config.CertPath)) {
        $signArgs += @("/f", $Config.CertPath)
        if (-not [string]::IsNullOrWhiteSpace($Config.CertPassword)) {
            $signArgs += @("/p", $Config.CertPassword)
        }
    }
    elseif (-not [string]::IsNullOrWhiteSpace($Config.CertSha1)) {
        $signArgs += @("/sha1", $Config.CertSha1)
    }

    $signArgs += $Path

    Write-Host "Signing $Path..."
    & $Config.SignToolPath @signArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Code signing failed for $Path."
    }
}

function Test-FileTransferRuntimeDependencies {
    Write-Host "Verifying file transfer runtime dependencies..."
    & python -c "import pyftpdlib, OpenSSL, paramiko, tftpy; print('File transfer runtime dependencies OK')"
    if ($LASTEXITCODE -ne 0) {
        throw "File transfer runtime dependency smoke check failed."
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$normalizedVersion = $Version.Trim()
if ($normalizedVersion.StartsWith("v")) {
    $normalizedVersion = $normalizedVersion.Substring(1)
}

if ([string]::IsNullOrWhiteSpace($normalizedVersion)) {
    throw "A valid version is required, for example 1.0.0 or v1.0.0."
}

$codeSigningConfig = New-CodeSigningConfig

$buildDir = Join-Path $repoRoot "build"
$distDir = Join-Path $repoRoot "dist"
$stagingDir = Join-Path $buildDir "staging"
$stagingConfigDir = Join-Path $stagingDir "config"
$stagingLogsDir = Join-Path $stagingDir "logs"
$stagingLogsExportsDir = Join-Path $stagingLogsDir "exports"
$releaseDir = Join-Path $distDir "release"

if ($Clean) {
    foreach ($path in @($buildDir, $distDir)) {
        if (Test-Path $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}

New-Item -ItemType Directory -Force -Path $stagingConfigDir | Out-Null
New-Item -ItemType Directory -Force -Path $stagingLogsExportsDir | Out-Null
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

Copy-Item -LiteralPath (Join-Path $repoRoot "config\ip_profiles.json") -Destination $stagingConfigDir -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "config\ftp_profiles.json") -Destination $stagingConfigDir -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "config\scp_profiles.json") -Destination $stagingConfigDir -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "config\vendor_presets.json") -Destination $stagingConfigDir -Force

Set-Content -LiteralPath (Join-Path $stagingLogsDir ".gitkeep") -Value "" -Encoding UTF8
Set-Content -LiteralPath (Join-Path $stagingLogsExportsDir ".gitkeep") -Value "" -Encoding UTF8

$pyInstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", "NetOpsSuite",
    "--icon", (Join-Path $repoRoot "assets\icons\netops_toolkit.ico"),
    "--add-data=$(Format-PyInstallerBundleArg -Source $stagingConfigDir -Destination 'config')",
    "--add-data=$(Format-PyInstallerBundleArg -Source $stagingLogsDir -Destination 'logs')",
    "--add-data=$(Format-PyInstallerBundleArg -Source (Join-Path $repoRoot 'assets\icons') -Destination 'assets/icons')",
    "--add-data=$(Format-PyInstallerBundleArg -Source (Join-Path $repoRoot 'netops_suite\modules\inspector\vendor_templates') -Destination 'netops_suite/modules/inspector/vendor_templates')",
    "--add-data=$(Format-PyInstallerBundleArg -Source (Join-Path $repoRoot 'netops_suite\modules\inspector_runtime') -Destination 'netops_suite/modules/inspector_runtime')",
    "--add-data=$(Format-PyInstallerBundleArg -Source (Join-Path $repoRoot 'netops_suite\modules\config_builder\profiles') -Destination 'netops_suite/modules/config_builder/profiles')",
    "--add-data=$(Format-PyInstallerBundleArg -Source (Join-Path $repoRoot 'netops_suite\modules\config_builder\device_values') -Destination 'netops_suite/modules/config_builder/device_values')",
    "--add-data=$(Format-PyInstallerBundleArg -Source (Join-Path $repoRoot 'netops_suite\modules\config_builder\docs') -Destination 'netops_suite/modules/config_builder/docs')",
    "main.py"
)

$optionalBinaries = @(
    "iperf3.exe",
    "cygcrypto-3.dll",
    "cygwin1.dll",
    "cygz.dll"
)

foreach ($binaryName in $optionalBinaries) {
    $binaryPath = Join-Path $repoRoot $binaryName
    if (Test-Path $binaryPath) {
        $pyInstallerArgs += @("--add-binary=$(Format-PyInstallerBundleArg -Source $binaryPath -Destination '.')")
    }
}

Write-Host "Building PyInstaller bundle for version $normalizedVersion..."
Test-FileTransferRuntimeDependencies
& python @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

$isccPath = Resolve-IsccPath
$sourceDir = Join-Path $distDir "NetOpsSuite"
$installerScript = Join-Path $repoRoot "installer\netops-suite.iss"

if (-not (Test-Path $sourceDir)) {
    throw "PyInstaller output folder was not found: $sourceDir"
}

$appExePath = Join-Path $sourceDir "NetOpsSuite.exe"
Invoke-CodeSignFile -Path $appExePath -Config $codeSigningConfig

Copy-Item -LiteralPath (Join-Path $repoRoot "LICENSE") -Destination $sourceDir -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "THIRD_PARTY_NOTICES.md") -Destination $sourceDir -Force

Write-Host "Building installer..."
& $isccPath `
    "/DAppVersion=$normalizedVersion" `
    "/DSourceDir=$sourceDir" `
    "/DOutputDir=$releaseDir" `
    $installerScript

if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup build failed."
}

$installer = Get-ChildItem -LiteralPath $releaseDir -Filter "NetOpsSuite-setup-*.exe" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $installer) {
    throw "Installer was not found in release directory: $releaseDir"
}

Invoke-CodeSignFile -Path $installer.FullName -Config $codeSigningConfig

$checksumPath = Join-Path $releaseDir "SHA256SUMS.txt"
$installerHash = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$installerName = Split-Path -Path $installer.FullName -Leaf
"$installerHash *$installerName" | Set-Content -LiteralPath $checksumPath -Encoding ASCII
Write-Host "Wrote checksum manifest: $checksumPath"

Get-ChildItem -LiteralPath $releaseDir -Filter "NetOpsSuite-setup-*.exe" |
    Select-Object FullName, Length, LastWriteTime
Get-Item -LiteralPath $checksumPath |
    Select-Object FullName, Length, LastWriteTime
