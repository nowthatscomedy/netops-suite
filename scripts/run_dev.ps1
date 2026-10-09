param(
    [switch]$UseProjectData,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Python virtual environment was not found. Run .\scripts\install_dev.ps1 first."
}

if ($SkipInstall) {
    Write-Host "-SkipInstall is no longer needed; run_dev.ps1 only starts the app."
}

if ($UseProjectData) {
    Write-Host "-UseProjectData is no longer needed; source runs always store data in .\data."
}

& $Python main.py
