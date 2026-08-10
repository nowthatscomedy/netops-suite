param(
    [string]$IsccPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$installerScriptPath = Join-Path $repositoryRoot "installer\netops-suite.iss"
$setupIconPath = Join-Path $repositoryRoot "assets\icons\netops_toolkit.ico"
$licensePath = Join-Path $repositoryRoot "LICENSE"

function Resolve-TestIsccPath {
    if ($IsccPath) {
        $resolved = (Resolve-Path -LiteralPath $IsccPath -ErrorAction Stop).Path
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "ISCC path is not a file: $resolved"
        }
        return $resolved
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Inno Setup 6 command-line compiler was not found."
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    [IO.File]::WriteAllText($Path, $Value, [Text.UTF8Encoding]::new($false))
}

function New-TestPayload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$KeepValue,
        [string[]]$AdditionalFiles = @()
    )

    $payload = Join-Path $script:sourceRoot $Name
    $internal = Join-Path $payload "_internal"
    New-Item -ItemType Directory -Path $internal -Force | Out-Null
    $executablePath = Join-Path $payload "NetOpsSuite.exe"
    Copy-Item `
        -LiteralPath (Join-Path $env:SystemRoot "System32\where.exe") `
        -Destination $executablePath
    $payloadMarker = [Text.Encoding]::UTF8.GetBytes("payload:$KeepValue")
    $payloadStream = [IO.File]::Open(
        $executablePath,
        [IO.FileMode]::Append,
        [IO.FileAccess]::Write,
        [IO.FileShare]::Read
    )
    try {
        $payloadStream.Write($payloadMarker, 0, $payloadMarker.Length)
    }
    finally {
        $payloadStream.Dispose()
    }
    Copy-Item -LiteralPath $licensePath -Destination (Join-Path $payload "LICENSE")
    Write-Utf8NoBom -Path (Join-Path $internal "keep.txt") -Value $KeepValue
    foreach ($fileName in $AdditionalFiles) {
        Write-Utf8NoBom `
            -Path (Join-Path $internal $fileName) `
            -Value $fileName
    }
    return $payload
}

function Build-TestInstaller {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Version,
        [Parameter(Mandatory = $true)]
        [string]$Payload,
        [switch]$FailAfterFirstFile,
        [switch]$LockBackupCleanup
    )

    $outputDirectory = Join-Path $script:outputRoot $Version
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
    $scriptText = $script:originalInstallerScript.Replace(
        "E5B8B0F9-5B63-4A5F-BB0A-89F14E37E7B8",
        $script:testAppId
    )
    $scriptText = $scriptText.Replace(
        "AppName=NetOps Suite",
        "AppName=NetOps Suite Installer Transaction Test"
    )
    $scriptText = $scriptText.Replace(
        "DefaultDirName={autopf}\NetOps Suite",
        "DefaultDirName=$($script:installDirectory)"
    )
    $scriptText = $scriptText.Replace(
        "PrivilegesRequired=admin",
        "PrivilegesRequired=lowest"
    )
    $scriptText = $scriptText.Replace("HKLM64", "HKCU64")
    $scriptText = $scriptText.Replace(
        "SetupIconFile=..\assets\icons\netops_toolkit.ico",
        "SetupIconFile=$setupIconPath"
    )
    $scriptText = $scriptText.Replace(
        "if not IsUnderProgramFiles(CurrentInstallLocation) then begin",
        "if False then begin"
    )
    $scriptText = [regex]::Replace(
        $scriptText,
        "(?ms)\[Tasks\].*?(?=\[Files\])",
        ""
    )
    $scriptText = [regex]::Replace(
        $scriptText,
        "(?ms)\[Icons\].*?(?=\[Code\])",
        ""
    )
    if ($FailAfterFirstFile) {
        $scriptText = $scriptText.Replace(
            'Source: "{#SourceDir}\NetOpsSuite.exe"; DestDir: "{app}"; Flags: ignoreversion',
            'Source: "{#SourceDir}\NetOpsSuite.exe"; DestDir: "{app}"; Flags: ignoreversion; AfterInstall: FailUpgrade'
        )
        $scriptText += @"

procedure FailUpgrade;
begin
  if not SaveStringToFile(
    ExpandConstant('{app}\_internal'),
    'intentional copy blocker',
    False
  ) then
    RaiseException('Could not create the intentional transaction test blocker.');
end;
"@
    }
    if ($LockBackupCleanup) {
        $cleanupLockCode = @"
var
  TestCleanupLockHandle: Integer;

function TestCreateFileW(
  FileName: String;
  DesiredAccess, ShareMode, SecurityAttributes,
  CreationDisposition, FlagsAndAttributes, TemplateFile: Cardinal
): Integer;
  external 'CreateFileW@kernel32.dll stdcall';

procedure LockCommittedBackupForTest;
begin
  TestCleanupLockHandle := TestCreateFileW(
    AddBackslash(RuntimeBackupPath) + 'stable-only.txt',
    `$80000000, 0, 0, 3, 0, 0
  );
  if TestCleanupLockHandle = -1 then
    RaiseException('Could not lock the committed backup test file.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
"@
        $scriptText = $scriptText.Replace(
            "procedure CurStepChanged(CurStep: TSetupStep);",
            $cleanupLockCode
        )
        $scriptText = [regex]::Replace(
            $scriptText,
            "else if CurStep = ssDone then begin\r?\n(\s*)InstallCommitted",
            "else if CurStep = ssDone then begin`r`n`$1LockCommittedBackupForTest;`r`n`$1InstallCommitted",
            1
        )
    }

    $testScriptPath = Join-Path $script:scriptRoot "test-$Version.iss"
    Write-Utf8NoBom -Path $testScriptPath -Value $scriptText
    & $script:iscc `
        "/DAppVersion=$Version" `
        "/DSourceDir=$Payload" `
        "/DOutputDir=$outputDirectory" `
        $testScriptPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "ISCC failed for test version $Version."
    }

    $setupPath = Join-Path $outputDirectory "NetOpsSuite-setup-$Version.exe"
    if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
        throw "Expected test setup was not created: $setupPath"
    }
    return $setupPath
}

function Invoke-TestSetup {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SetupPath,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [switch]$ExpectFailure
    )

    $logPath = Join-Path $script:logRoot "$Name.log"
    $arguments = @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/DIR=$($script:installDirectory)",
        "/LOG=$logPath"
    )
    $process = Start-Process `
        -FilePath $SetupPath `
        -ArgumentList $arguments `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($ExpectFailure -and $process.ExitCode -eq 0) {
        throw "Test setup unexpectedly succeeded: $Name"
    }
    if (-not $ExpectFailure -and $process.ExitCode -ne 0) {
        throw "Test setup failed with exit code $($process.ExitCode): $Name"
    }
}

function Assert-FileContent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Expected
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Expected file is missing: $Path"
    }
    $actual = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($actual -ne $Expected) {
        throw "Unexpected file content at $Path."
    }
}

function Assert-PathMissing {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (Test-Path -LiteralPath $Path) {
        throw "Path should have been removed: $Path"
    }
}

function Assert-FilesEqual {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Actual,
        [Parameter(Mandatory = $true)]
        [string]$Expected
    )

    $actualHash = (Get-FileHash -LiteralPath $Actual -Algorithm SHA256).Hash
    $expectedHash = (Get-FileHash -LiteralPath $Expected -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedHash) {
        throw "Files differ: $Actual and $Expected"
    }
}

function Write-InterruptedBackupMarker {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PreviousVersion,
        [Parameter(Mandatory = $true)]
        [string]$IncomingVersion,
        [switch]$LeaveCurrentPayloadMissing
    )

    $backupPath = Join-Path $script:installDirectory ".netops-suite-runtime-backup"
    $executableBackupPath =
        Join-Path $script:installDirectory ".netops-suite-executable-backup.exe"
    $markerPath = "$backupPath.in-progress.ini"
    $runtimePath = Join-Path $script:installDirectory "_internal"
    $executablePath = Join-Path $script:installDirectory "NetOpsSuite.exe"
    if (Test-Path -LiteralPath $backupPath) {
        throw "Backup path is unexpectedly occupied: $backupPath"
    }
    Move-Item -LiteralPath $runtimePath -Destination $backupPath
    Move-Item -LiteralPath $executablePath -Destination $executableBackupPath
    if (-not $LeaveCurrentPayloadMissing) {
        New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
        Write-Utf8NoBom -Path (Join-Path $runtimePath "partial.txt") -Value "partial"
        Write-Utf8NoBom -Path $executablePath -Value "partial-executable"
    }
    $marker = @(
        "[Backup]",
        "AppId={$($script:testAppId)}",
        "InstallLocation=$($script:installDirectory)",
        "BackupPath=$backupPath",
        "ExecutableBackupPath=$executableBackupPath",
        "PreviousVersion=$PreviousVersion",
        "IncomingVersion=$IncomingVersion"
    ) -join "`r`n"
    Write-Utf8NoBom -Path $markerPath -Value $marker
}

function Write-UntrustedBackupMarker {
    $backupPath = Join-Path $script:installDirectory ".netops-suite-runtime-backup"
    $executableBackupPath =
        Join-Path $script:installDirectory ".netops-suite-executable-backup.exe"
    $markerPath = "$backupPath.in-progress.ini"
    New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
    Write-Utf8NoBom -Path (Join-Path $backupPath "untrusted.txt") -Value "untrusted"
    $marker = @(
        "[Backup]",
        "AppId={00000000-0000-0000-0000-000000000000}",
        "InstallLocation=$($script:installDirectory)",
        "BackupPath=$backupPath",
        "ExecutableBackupPath=$executableBackupPath",
        "PreviousVersion=1.0.13",
        "IncomingVersion=1.0.15"
    ) -join "`r`n"
    Write-Utf8NoBom -Path $markerPath -Value $marker
}

function Remove-TestArtifacts {
    $uninstaller = Join-Path $script:installDirectory "unins000.exe"
    if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
        $uninstallProcess = Start-Process `
            -FilePath $uninstaller `
            -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        if ($uninstallProcess.ExitCode -ne 0) {
            Write-Warning "Test uninstaller exited with $($uninstallProcess.ExitCode)."
        }
    }

    $registryPath =
        "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\{$($script:testAppId)}_is1"
    if (Test-Path -LiteralPath $registryPath) {
        Remove-Item -LiteralPath $registryPath -Recurse -Force
    }

    $resolvedTestRoot = [IO.Path]::GetFullPath($script:testRoot)
    $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolvedTestRoot.StartsWith(
        $resolvedTempRoot,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing unsafe test cleanup path: $resolvedTestRoot"
    }
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        if (-not (Test-Path -LiteralPath $resolvedTestRoot)) {
            return
        }
        try {
            Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force -ErrorAction Stop
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (Test-Path -LiteralPath $resolvedTestRoot) {
        throw "Could not remove isolated test root: $resolvedTestRoot"
    }
}

$script:iscc = Resolve-TestIsccPath
$script:testRoot = Join-Path `
    ([IO.Path]::GetTempPath()) `
    ("netops-suite-upgrade-test-" + [guid]::NewGuid().ToString("N"))
$script:installDirectory = Join-Path $script:testRoot "installed"
$script:scriptRoot = Join-Path $script:testRoot "scripts"
$script:outputRoot = Join-Path $script:testRoot "output"
$script:sourceRoot = Join-Path $script:testRoot "sources"
$script:logRoot = Join-Path $script:testRoot "logs"
$script:testAppId = [guid]::NewGuid().ToString().ToUpperInvariant()
$script:originalInstallerScript =
    Get-Content -LiteralPath $installerScriptPath -Raw -Encoding UTF8

New-Item `
    -ItemType Directory `
    -Path @(
        $script:installDirectory,
        $script:scriptRoot,
        $script:outputRoot,
        $script:sourceRoot,
        $script:logRoot
    ) `
    -Force | Out-Null

try {
    $oldPayload = New-TestPayload `
        -Name "old" `
        -KeepValue "old" `
        -AdditionalFiles @("obsolete.txt")
    $newPayload = New-TestPayload `
        -Name "new" `
        -KeepValue "new" `
        -AdditionalFiles @("rollback-only.txt")
    $releaseCandidatePayload = New-TestPayload `
        -Name "release-candidate" `
        -KeepValue "release-candidate" `
        -AdditionalFiles @("rc-only.txt")
    $stablePayload = New-TestPayload `
        -Name "stable" `
        -KeepValue "stable" `
        -AdditionalFiles @("stable-only.txt")
    $committedPayload = New-TestPayload `
        -Name "committed" `
        -KeepValue "committed" `
        -AdditionalFiles @("committed-only.txt")
    $failedPayload = New-TestPayload `
        -Name "failed" `
        -KeepValue "failed-copy"
    $untrustedPayload = New-TestPayload `
        -Name "untrusted-marker" `
        -KeepValue "must-not-install"

    $oldSetup = Build-TestInstaller -Version "1.0.10" -Payload $oldPayload
    $newSetup = Build-TestInstaller -Version "1.0.11" -Payload $newPayload
    $releaseCandidateSetup = Build-TestInstaller `
        -Version "1.0.12-rc.1" `
        -Payload $releaseCandidatePayload
    $stableSetup = Build-TestInstaller -Version "1.0.12" -Payload $stablePayload
    $committedSetup = Build-TestInstaller `
        -Version "1.0.13" `
        -Payload $committedPayload `
        -LockBackupCleanup
    $failedSetup = Build-TestInstaller `
        -Version "1.0.14" `
        -Payload $failedPayload `
        -FailAfterFirstFile
    $untrustedSetup = Build-TestInstaller `
        -Version "1.0.15" `
        -Payload $untrustedPayload

    Invoke-TestSetup -SetupPath $oldSetup -Name "old-install"
    Write-Utf8NoBom `
        -Path (Join-Path $script:installDirectory "notes.txt") `
        -Value "user-note"
    Write-Utf8NoBom `
        -Path (Join-Path $script:installDirectory "iperf3.exe") `
        -Value "manual-iperf"
    $userDataPath = Join-Path $script:testRoot "user-data\sentinel.txt"
    New-Item -ItemType Directory -Path (Split-Path $userDataPath) -Force | Out-Null
    Write-Utf8NoBom -Path $userDataPath -Value "user-data"

    Invoke-TestSetup -SetupPath $newSetup -Name "upgrade"
    Assert-PathMissing `
        -Path (Join-Path $script:installDirectory "_internal\obsolete.txt")
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "_internal\keep.txt") `
        -Expected "new"

    Write-InterruptedBackupMarker `
        -PreviousVersion "1.0.11" `
        -IncomingVersion "1.0.12-rc.1"
    Invoke-TestSetup `
        -SetupPath $releaseCandidateSetup `
        -Name "cross-version-recovery"
    Assert-PathMissing `
        -Path (Join-Path $script:installDirectory "_internal\partial.txt")
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "_internal\keep.txt") `
        -Expected "release-candidate"

    Invoke-TestSetup -SetupPath $stableSetup -Name "prerelease-to-stable"
    Assert-PathMissing `
        -Path (Join-Path $script:installDirectory "_internal\rc-only.txt")
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "_internal\keep.txt") `
        -Expected "stable"
    Assert-FilesEqual `
        -Actual (Join-Path $script:installDirectory "NetOpsSuite.exe") `
        -Expected (Join-Path $stablePayload "NetOpsSuite.exe")

    Write-Utf8NoBom `
        -Path (Join-Path $script:installDirectory ".netops-suite-runtime-backup.marker.tmp") `
        -Value "[Backup]`r`nAppId=partial"
    Invoke-TestSetup -SetupPath $stableSetup -Name "partial-marker-recovery"
    Assert-PathMissing `
        -Path (Join-Path $script:installDirectory ".netops-suite-runtime-backup.marker.tmp")

    Invoke-TestSetup `
        -SetupPath $committedSetup `
        -Name "committed-backup-cleanup-retry"
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "_internal\keep.txt") `
        -Expected "committed"
    if (-not (Test-Path `
        -LiteralPath (Join-Path $script:installDirectory ".netops-suite-runtime-backup") `
        -PathType Container
    )) {
        throw "Expected a locked committed runtime backup to remain."
    }
    if (-not (Test-Path `
        -LiteralPath (Join-Path $script:installDirectory ".netops-suite-runtime-backup.committed.ini") `
        -PathType Leaf
    )) {
        throw "Expected the committed runtime backup marker to remain."
    }

    Invoke-TestSetup `
        -SetupPath $failedSetup `
        -Name "failed-upgrade" `
        -ExpectFailure
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "_internal\keep.txt") `
        -Expected "committed"
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "_internal\committed-only.txt") `
        -Expected "committed-only.txt"
    Assert-FilesEqual `
        -Actual (Join-Path $script:installDirectory "NetOpsSuite.exe") `
        -Expected (Join-Path $committedPayload "NetOpsSuite.exe")

    Write-InterruptedBackupMarker `
        -PreviousVersion "1.0.13" `
        -IncomingVersion "1.0.14" `
        -LeaveCurrentPayloadMissing
    Invoke-TestSetup `
        -SetupPath $failedSetup `
        -Name "interrupted-followup-failure" `
        -ExpectFailure
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "_internal\keep.txt") `
        -Expected "committed"
    Assert-FilesEqual `
        -Actual (Join-Path $script:installDirectory "NetOpsSuite.exe") `
        -Expected (Join-Path $committedPayload "NetOpsSuite.exe")

    Invoke-TestSetup `
        -SetupPath $oldSetup `
        -Name "blocked-downgrade" `
        -ExpectFailure
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "_internal\keep.txt") `
        -Expected "committed"
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "notes.txt") `
        -Expected "user-note"
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "iperf3.exe") `
        -Expected "manual-iperf"
    Assert-FileContent -Path $userDataPath -Expected "user-data"
    Assert-PathMissing `
        -Path (Join-Path $script:installDirectory ".netops-suite-runtime-backup")
    Assert-PathMissing `
        -Path (Join-Path $script:installDirectory ".netops-suite-runtime-backup.in-progress.ini")
    Assert-PathMissing `
        -Path (Join-Path $script:installDirectory ".netops-suite-runtime-backup.committed.ini")
    Assert-PathMissing `
        -Path (Join-Path $script:installDirectory ".netops-suite-executable-backup.exe")

    Write-UntrustedBackupMarker
    Invoke-TestSetup `
        -SetupPath $untrustedSetup `
        -Name "untrusted-marker" `
        -ExpectFailure
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "_internal\keep.txt") `
        -Expected "committed"
    Assert-FileContent `
        -Path (Join-Path $script:installDirectory "_internal\committed-only.txt") `
        -Expected "committed-only.txt"

    Write-Host "Installer upgrade transaction integration test passed."
}
catch {
    Get-ChildItem -LiteralPath $script:logRoot -Filter "*.log" -ErrorAction SilentlyContinue |
        ForEach-Object {
            Write-Warning "Installer log: $($_.FullName)"
            Get-Content -LiteralPath $_.FullName -Tail 80 -ErrorAction SilentlyContinue
        }
    throw
}
finally {
    Remove-TestArtifacts
}
