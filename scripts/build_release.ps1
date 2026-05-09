param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [switch]$Clean
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
    "--add-data=$(Format-PyInstallerBundleArg -Source (Join-Path $repoRoot 'netops_suite\modules\inspector_runtime\custom_rules.example.yaml') -Destination 'netops_suite/modules/inspector_runtime')",
    "--add-data=$(Format-PyInstallerBundleArg -Source (Join-Path $repoRoot 'netops_suite\modules\inspector_runtime\custom_parsers.example.py') -Destination 'netops_suite/modules/inspector_runtime')",
    "--add-data=$(Format-PyInstallerBundleArg -Source (Join-Path $repoRoot 'netops_suite\modules\inspector_runtime\locales') -Destination 'netops_suite/modules/inspector_runtime/locales')",
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

Write-Host "Building installer..."
& $isccPath `
    "/DAppVersion=$normalizedVersion" `
    "/DSourceDir=$sourceDir" `
    "/DOutputDir=$releaseDir" `
    $installerScript

if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup build failed."
}

Get-ChildItem -LiteralPath $releaseDir -Filter "NetOpsSuite-setup-*.exe" |
    Select-Object FullName, Length, LastWriteTime
