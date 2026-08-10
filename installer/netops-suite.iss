#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#ifndef SourceDir
  #error SourceDir is not defined
#endif

#ifndef OutputDir
  #error OutputDir is not defined
#endif

#define AppUserModelID "NetOpsSuite.DesktopApp"

[Setup]
AppId={{E5B8B0F9-5B63-4A5F-BB0A-89F14E37E7B8}
AppName=NetOps Suite
AppVersion={#AppVersion}
AppPublisher=NetOps Suite
DefaultDirName={autopf}\NetOps Suite
DefaultGroupName=NetOps Suite
DisableProgramGroupPage=yes
AllowRootDirectory=no
PrivilegesRequired=admin
CloseApplications=yes
RedirectionGuard=yes
OutputDir={#OutputDir}
OutputBaseFilename=NetOpsSuite-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
SetupIconFile=..\assets\icons\netops_toolkit.ico
UninstallDisplayIcon={app}\NetOpsSuite.exe

[Tasks]
Name: "desktopicon"; Description: "바탕 화면 바로가기 만들기"; GroupDescription: "추가 아이콘:"

[Files]
Source: "{#SourceDir}\NetOpsSuite.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "\NetOpsSuite.exe"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\NetOps Suite"; Filename: "{app}\NetOpsSuite.exe"; IconFilename: "{app}\NetOpsSuite.exe"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\NetOps Suite"; Filename: "{app}\NetOpsSuite.exe"; IconFilename: "{app}\NetOpsSuite.exe"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Code]
const
  NetOpsAppId = '{E5B8B0F9-5B63-4A5F-BB0A-89F14E37E7B8}';
  NetOpsUninstallKey =
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{E5B8B0F9-5B63-4A5F-BB0A-89F14E37E7B8}_is1';
  FileAttributeReparsePoint = $00000400;
  InvalidFileAttributes = $FFFFFFFF;

var
  RuntimeRotationChecked: Boolean;
  RuntimeBackupActive: Boolean;
  InstallCommitted: Boolean;
  CurrentInstallLocation: String;
  ManagedExecutablePath: String;
  ManagedRuntimePath: String;
  ExecutableBackupPath: String;
  RuntimeBackupPath: String;
  RuntimeBackupMarkerTempPath: String;
  RuntimeBackupInProgressMarkerPath: String;
  RuntimeBackupCommittedMarkerPath: String;

function GetFileAttributesW(FileName: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function NormalizePath(PathName: String): String;
begin
  Result := RemoveBackslashUnlessRoot(ExpandFileName(PathName));
end;

function IsReparsePoint(PathName: String): Boolean;
var
  Attributes: Cardinal;
begin
  Attributes := GetFileAttributesW(PathName);
  Result :=
    (Attributes <> InvalidFileAttributes) and
    ((Attributes and FileAttributeReparsePoint) <> 0);
end;

function HasReparsePointInPath(PathName: String): Boolean;
var
  CurrentPath: String;
  ParentPath: String;
begin
  Result := False;
  CurrentPath := NormalizePath(PathName);
  while CurrentPath <> '' do begin
    if IsReparsePoint(CurrentPath) then begin
      Result := True;
      Exit;
    end;

    ParentPath := ExtractFileDir(CurrentPath);
    if ParentPath = '' then
      Exit;
    ParentPath := RemoveBackslashUnlessRoot(ParentPath);
    if CompareText(ParentPath, CurrentPath) = 0 then
      Exit;
    CurrentPath := ParentPath;
  end;
end;

function IsUnderProgramFiles(PathName: String): Boolean;
var
  ProgramFilesPrefix: String;
begin
  ProgramFilesPrefix := AddBackslash(NormalizePath(ExpandConstant('{autopf}')));
  Result :=
    CompareText(
      Copy(PathName, 1, Length(ProgramFilesPrefix)),
      ProgramFilesPrefix
    ) = 0;
end;

function ResolveRegisteredInstall(var InstalledVersion: String): Boolean;
var
  RegisteredInstallLocation: String;
begin
  Result := False;
  if not RegQueryStringValue(
    HKLM64, NetOpsUninstallKey, 'InstallLocation', RegisteredInstallLocation
  ) then
    Exit;
  if not RegQueryStringValue(
    HKLM64, NetOpsUninstallKey, 'DisplayVersion', InstalledVersion
  ) then
    Exit;

  RegisteredInstallLocation :=
    NormalizePath(RegisteredInstallLocation);
  CurrentInstallLocation :=
    NormalizePath(ExpandConstant('{app}'));
  Result :=
    CompareText(RegisteredInstallLocation, CurrentInstallLocation) = 0;
end;

function WithoutBuildMetadata(VersionString: String): String;
var
  SeparatorPosition: Integer;
begin
  SeparatorPosition := Pos('+', VersionString);
  if SeparatorPosition > 0 then
    Result := Copy(VersionString, 1, SeparatorPosition - 1)
  else
    Result := VersionString;
end;

function SemanticVersionCore(VersionString: String): String;
var
  NormalizedVersion: String;
  SeparatorPosition: Integer;
begin
  NormalizedVersion := WithoutBuildMetadata(VersionString);
  SeparatorPosition := Pos('-', NormalizedVersion);
  if SeparatorPosition > 0 then
    Result := Copy(NormalizedVersion, 1, SeparatorPosition - 1)
  else
    Result := NormalizedVersion;
end;

function SemanticVersionPrerelease(VersionString: String): String;
var
  NormalizedVersion: String;
  SeparatorPosition: Integer;
begin
  NormalizedVersion := WithoutBuildMetadata(VersionString);
  SeparatorPosition := Pos('-', NormalizedVersion);
  if SeparatorPosition > 0 then
    Result := Copy(
      NormalizedVersion,
      SeparatorPosition + 1,
      Length(NormalizedVersion) - SeparatorPosition
    )
  else
    Result := '';
end;

function NextPrereleaseIdentifier(
  Prerelease: String; var IdentifierPosition: Integer
): String;
var
  StartPosition: Integer;
begin
  StartPosition := IdentifierPosition;
  while (IdentifierPosition <= Length(Prerelease)) and
        (Prerelease[IdentifierPosition] <> '.') do
    IdentifierPosition := IdentifierPosition + 1;
  Result := Copy(
    Prerelease, StartPosition, IdentifierPosition - StartPosition
  );
  if IdentifierPosition <= Length(Prerelease) then
    IdentifierPosition := IdentifierPosition + 1;
end;

function IsNumericIdentifier(Identifier: String): Boolean;
var
  CharacterIndex: Integer;
begin
  Result := Identifier <> '';
  if not Result then
    Exit;
  for CharacterIndex := 1 to Length(Identifier) do
    if (Identifier[CharacterIndex] < '0') or
       (Identifier[CharacterIndex] > '9') then begin
      Result := False;
      Exit;
    end;
end;

function WithoutLeadingZeroes(Identifier: String): String;
var
  CharacterIndex: Integer;
begin
  CharacterIndex := 1;
  while (CharacterIndex < Length(Identifier)) and
        (Identifier[CharacterIndex] = '0') do
    CharacterIndex := CharacterIndex + 1;
  Result := Copy(
    Identifier,
    CharacterIndex,
    Length(Identifier) - CharacterIndex + 1
  );
end;

function CompareNumericIdentifiers(LeftIdentifier, RightIdentifier: String): Integer;
begin
  LeftIdentifier := WithoutLeadingZeroes(LeftIdentifier);
  RightIdentifier := WithoutLeadingZeroes(RightIdentifier);
  if Length(LeftIdentifier) < Length(RightIdentifier) then
    Result := -1
  else if Length(LeftIdentifier) > Length(RightIdentifier) then
    Result := 1
  else
    Result := CompareStr(LeftIdentifier, RightIdentifier);
end;

function ComparePrereleaseVersions(LeftPrerelease, RightPrerelease: String): Integer;
var
  LeftPosition: Integer;
  RightPosition: Integer;
  LeftIdentifier: String;
  RightIdentifier: String;
  LeftIsNumeric: Boolean;
  RightIsNumeric: Boolean;
begin
  if LeftPrerelease = '' then begin
    if RightPrerelease = '' then
      Result := 0
    else
      Result := 1;
    Exit;
  end;
  if RightPrerelease = '' then begin
    Result := -1;
    Exit;
  end;

  LeftPosition := 1;
  RightPosition := 1;
  while (LeftPosition <= Length(LeftPrerelease)) or
        (RightPosition <= Length(RightPrerelease)) do begin
    if LeftPosition > Length(LeftPrerelease) then begin
      Result := -1;
      Exit;
    end;
    if RightPosition > Length(RightPrerelease) then begin
      Result := 1;
      Exit;
    end;

    LeftIdentifier := NextPrereleaseIdentifier(
      LeftPrerelease, LeftPosition
    );
    RightIdentifier := NextPrereleaseIdentifier(
      RightPrerelease, RightPosition
    );
    LeftIsNumeric := IsNumericIdentifier(LeftIdentifier);
    RightIsNumeric := IsNumericIdentifier(RightIdentifier);
    if LeftIsNumeric and RightIsNumeric then
      Result := CompareNumericIdentifiers(LeftIdentifier, RightIdentifier)
    else if LeftIsNumeric then
      Result := -1
    else if RightIsNumeric then
      Result := 1
    else
      Result := CompareStr(LeftIdentifier, RightIdentifier);
    if Result <> 0 then
      Exit;
  end;
  Result := 0;
end;

function TryCompareSemanticVersions(
  LeftVersion, RightVersion: String; var Comparison: Integer
): Boolean;
var
  LeftCore: Int64;
  RightCore: Int64;
begin
  Result :=
    StrToVersion(SemanticVersionCore(LeftVersion), LeftCore) and
    StrToVersion(SemanticVersionCore(RightVersion), RightCore);
  if not Result then
    Exit;

  Comparison := ComparePackedVersion(LeftCore, RightCore);
  if Comparison = 0 then
    Comparison := ComparePrereleaseVersions(
      SemanticVersionPrerelease(LeftVersion),
      SemanticVersionPrerelease(RightVersion)
    );
end;

function IsStrictVersionUpgrade(InstalledVersion: String): Boolean;
var
  VersionComparison: Integer;
begin
  Result :=
    TryCompareSemanticVersions(
      '{#AppVersion}', InstalledVersion, VersionComparison
    ) and
    (VersionComparison > 0);
end;

procedure InitializeRuntimePaths;
begin
  ManagedExecutablePath :=
    AddBackslash(CurrentInstallLocation) + 'NetOpsSuite.exe';
  ManagedRuntimePath := AddBackslash(CurrentInstallLocation) + '_internal';
  ExecutableBackupPath :=
    AddBackslash(CurrentInstallLocation) +
    '.netops-suite-executable-backup.exe';
  RuntimeBackupPath :=
    AddBackslash(CurrentInstallLocation) +
    '.netops-suite-runtime-backup';
  RuntimeBackupMarkerTempPath :=
    RuntimeBackupPath + '.marker.tmp';
  RuntimeBackupInProgressMarkerPath :=
    RuntimeBackupPath + '.in-progress.ini';
  RuntimeBackupCommittedMarkerPath :=
    RuntimeBackupPath + '.committed.ini';
end;

function IsRuntimeBackupMarkerContentsValid(MarkerPath: String): Boolean;
var
  PreviousVersion: String;
  IncomingVersion: String;
  VersionComparison: Integer;
begin
  PreviousVersion := GetIniString(
    'Backup', 'PreviousVersion', '', MarkerPath
  );
  IncomingVersion := GetIniString(
    'Backup', 'IncomingVersion', '', MarkerPath
  );
  Result :=
    FileExists(MarkerPath) and
    (CompareText(
      GetIniString('Backup', 'AppId', '', MarkerPath),
      NetOpsAppId
    ) = 0) and
    (CompareText(
      NormalizePath(GetIniString(
        'Backup', 'InstallLocation', '', MarkerPath
      )),
      CurrentInstallLocation
    ) = 0) and
    (CompareText(
      NormalizePath(GetIniString(
        'Backup', 'BackupPath', '', MarkerPath
      )),
      RuntimeBackupPath
    ) = 0) and
    (CompareText(
      NormalizePath(GetIniString(
        'Backup', 'ExecutableBackupPath', '', MarkerPath
      )),
      ExecutableBackupPath
    ) = 0) and
    TryCompareSemanticVersions(
      IncomingVersion, PreviousVersion, VersionComparison
    ) and
    (VersionComparison > 0);
end;

function IsRuntimeBackupMarkerValid(
  MarkerPath, ExpectedState: String
): Boolean;
begin
  Result :=
    IsRuntimeBackupMarkerContentsValid(MarkerPath) and
    (((ExpectedState = 'in-progress') and
      (CompareText(
        MarkerPath, RuntimeBackupInProgressMarkerPath
      ) = 0)) or
     ((ExpectedState = 'committed') and
      (CompareText(
        MarkerPath, RuntimeBackupCommittedMarkerPath
      ) = 0)));
end;

procedure WriteRuntimeBackupMarker(InstalledVersion: String);
begin
  if not SetIniString(
    'Backup', 'AppId', NetOpsAppId,
    RuntimeBackupMarkerTempPath
  ) then
    RaiseException('Could not create the runtime backup marker.');
  if not SetIniString(
    'Backup', 'InstallLocation', CurrentInstallLocation,
    RuntimeBackupMarkerTempPath
  ) then begin
    DeleteFile(RuntimeBackupMarkerTempPath);
    RaiseException('Could not record the runtime backup location.');
  end;
  if not SetIniString(
    'Backup', 'BackupPath', RuntimeBackupPath,
    RuntimeBackupMarkerTempPath
  ) then begin
    DeleteFile(RuntimeBackupMarkerTempPath);
    RaiseException('Could not record the managed runtime backup path.');
  end;
  if not SetIniString(
    'Backup', 'ExecutableBackupPath', ExecutableBackupPath,
    RuntimeBackupMarkerTempPath
  ) then begin
    DeleteFile(RuntimeBackupMarkerTempPath);
    RaiseException('Could not record the executable backup path.');
  end;
  if not SetIniString(
    'Backup', 'PreviousVersion', InstalledVersion,
    RuntimeBackupMarkerTempPath
  ) then begin
    DeleteFile(RuntimeBackupMarkerTempPath);
    RaiseException('Could not record the previous runtime version.');
  end;
  if not SetIniString(
    'Backup', 'IncomingVersion', '{#AppVersion}',
    RuntimeBackupMarkerTempPath
  ) then begin
    DeleteFile(RuntimeBackupMarkerTempPath);
    RaiseException('Could not record the incoming runtime version.');
  end;
  if not IsRuntimeBackupMarkerContentsValid(
    RuntimeBackupMarkerTempPath
  ) then begin
    DeleteFile(RuntimeBackupMarkerTempPath);
    RaiseException('Could not validate the runtime backup marker.');
  end;
  if not RenameFile(
    RuntimeBackupMarkerTempPath,
    RuntimeBackupInProgressMarkerPath
  ) then begin
    DeleteFile(RuntimeBackupMarkerTempPath);
    RaiseException('Could not commit the runtime backup marker.');
  end;
  if not IsRuntimeBackupMarkerValid(
    RuntimeBackupInProgressMarkerPath, 'in-progress'
  ) then
    RaiseException('The committed runtime backup marker is invalid.');
end;

function PromoteRuntimeBackupMarker: Boolean;
begin
  Result := False;
  if not IsRuntimeBackupMarkerValid(
    RuntimeBackupInProgressMarkerPath, 'in-progress'
  ) then
    Exit;
  if FileExists(RuntimeBackupCommittedMarkerPath) then
    Exit;
  if not RenameFile(
    RuntimeBackupInProgressMarkerPath,
    RuntimeBackupCommittedMarkerPath
  ) then
    Exit;
  Result := IsRuntimeBackupMarkerValid(
    RuntimeBackupCommittedMarkerPath, 'committed'
  );
end;

function MarkerIncomingVersionMatches(
  MarkerPath, InstalledVersion: String
): Boolean;
var
  VersionComparison: Integer;
begin
  Result :=
    TryCompareSemanticVersions(
      GetIniString('Backup', 'IncomingVersion', '', MarkerPath),
      InstalledVersion,
      VersionComparison
    ) and
    (VersionComparison = 0);
end;

procedure RemoveAbandonedRuntimeMarkerTemp;
begin
  if not FileExists(RuntimeBackupMarkerTempPath) then
    Exit;
  if FileExists(RuntimeBackupInProgressMarkerPath) or
     FileExists(RuntimeBackupCommittedMarkerPath) or
     DirExists(RuntimeBackupPath) or
     FileExists(RuntimeBackupPath) or
     FileExists(ExecutableBackupPath) or
     DirExists(ExecutableBackupPath) or
     not DirExists(ManagedRuntimePath) or
     not FileExists(ManagedExecutablePath) then
    RaiseException('An incomplete runtime marker is in an unsafe state.');
  if not DeleteFile(RuntimeBackupMarkerTempPath) then
    RaiseException('Could not remove an abandoned runtime marker.');
  Log('Removed an abandoned NetOps Suite runtime marker.');
end;

function RecoverInterruptedRuntimeRotation(
  InstalledVersion: String
): Boolean;
var
  InProgressMarkerExists: Boolean;
  CommittedMarkerExists: Boolean;
  RuntimeBackupExists: Boolean;
  ExecutableBackupExists: Boolean;
  MarkerPath: String;
  MarkerState: String;
  TreatAsCommitted: Boolean;
begin
  Result := False;
  RemoveAbandonedRuntimeMarkerTemp;
  InProgressMarkerExists :=
    FileExists(RuntimeBackupInProgressMarkerPath);
  CommittedMarkerExists :=
    FileExists(RuntimeBackupCommittedMarkerPath);
  RuntimeBackupExists := DirExists(RuntimeBackupPath);
  ExecutableBackupExists := FileExists(ExecutableBackupPath);

  if FileExists(RuntimeBackupPath) or
     DirExists(ExecutableBackupPath) then
    RaiseException('A runtime backup has an unexpected file type.');

  if InProgressMarkerExists and CommittedMarkerExists then
    RaiseException('Conflicting runtime backup markers already exist.');

  if not InProgressMarkerExists and
     not CommittedMarkerExists and
     not RuntimeBackupExists and
     not ExecutableBackupExists then
    Exit;

  if InProgressMarkerExists then begin
    MarkerPath := RuntimeBackupInProgressMarkerPath;
    MarkerState := 'in-progress';
  end else if CommittedMarkerExists then begin
    MarkerPath := RuntimeBackupCommittedMarkerPath;
    MarkerState := 'committed';
  end else
    RaiseException('A runtime backup exists without a trusted marker.');

  if (InProgressMarkerExists or CommittedMarkerExists) and
     not RuntimeBackupExists and
     not ExecutableBackupExists and
     DirExists(ManagedRuntimePath) and
     FileExists(ManagedExecutablePath) then begin
    if not IsRuntimeBackupMarkerValid(MarkerPath, MarkerState) then
      RaiseException('The existing runtime backup marker is not trusted.');
    if not DeleteFile(MarkerPath) then
      RaiseException('Could not remove a completed runtime backup marker.');
    Log('Removed a completed NetOps Suite runtime backup marker.');
    Exit;
  end;

  if not (InProgressMarkerExists or CommittedMarkerExists) or
     not (RuntimeBackupExists or ExecutableBackupExists) or
     not IsRuntimeBackupMarkerValid(MarkerPath, MarkerState) then
    RaiseException('An untrusted or incomplete runtime backup already exists.');

  TreatAsCommitted :=
    (MarkerState = 'committed') or
    ((MarkerState = 'in-progress') and
     DirExists(ManagedRuntimePath) and
     FileExists(ManagedExecutablePath) and
     MarkerIncomingVersionMatches(MarkerPath, InstalledVersion));
  if TreatAsCommitted then begin
    if not DirExists(ManagedRuntimePath) or
       not FileExists(ManagedExecutablePath) then
      RaiseException('The committed replacement payload is missing.');
    if RuntimeBackupExists and
       not DelTree(RuntimeBackupPath, True, True, True) then
      RaiseException('Could not finish removing the committed runtime backup.');
    if ExecutableBackupExists and
       not DeleteFile(ExecutableBackupPath) then
      RaiseException('Could not finish removing the executable backup.');
    if not DeleteFile(MarkerPath) then
      RaiseException('Could not remove the committed runtime backup marker.');
    Log('Finished cleanup of a committed NetOps Suite runtime backup.');
    Exit;
  end;

  if RuntimeBackupExists then begin
    if FileExists(ManagedRuntimePath) and
       not DeleteFile(ManagedRuntimePath) then
      RaiseException('Could not remove the interrupted replacement runtime file.');
    if DirExists(ManagedRuntimePath) and
       not DelTree(ManagedRuntimePath, True, True, True) then
      RaiseException('Could not remove the interrupted replacement runtime.');
    if not RenameFile(RuntimeBackupPath, ManagedRuntimePath) then
      RaiseException('Could not restore the previous NetOps Suite runtime.');
  end;
  if ExecutableBackupExists then begin
    if FileExists(ManagedExecutablePath) and
       not DeleteFile(ManagedExecutablePath) then
      RaiseException('Could not remove the interrupted replacement executable.');
    if not RenameFile(ExecutableBackupPath, ManagedExecutablePath) then
      RaiseException('Could not restore the previous NetOps Suite executable.');
  end;
  if not DeleteFile(RuntimeBackupInProgressMarkerPath) then
    RaiseException('Could not remove the recovered runtime backup marker.');

  Log('Recovered the previous NetOps Suite runtime after an interrupted setup.');
  Result := True;
end;

procedure RotateManagedRuntime;
var
  InstalledVersion: String;
  RecoveredInterruptedRotation: Boolean;
begin
  if RuntimeRotationChecked then
    Exit;
  RuntimeRotationChecked := True;

  if not ResolveRegisteredInstall(InstalledVersion) then begin
    Log('Managed runtime cleanup skipped: no matching registered installation.');
    Exit;
  end;
  InitializeRuntimePaths;

  if not IsUnderProgramFiles(CurrentInstallLocation) then begin
    Log('Managed runtime cleanup skipped outside Program Files.');
    Exit;
  end;
  if HasReparsePointInPath(ManagedExecutablePath) or
     HasReparsePointInPath(ManagedRuntimePath) or
     HasReparsePointInPath(ExecutableBackupPath) or
     HasReparsePointInPath(RuntimeBackupPath) or
     HasReparsePointInPath(RuntimeBackupMarkerTempPath) or
     HasReparsePointInPath(RuntimeBackupInProgressMarkerPath) or
     HasReparsePointInPath(RuntimeBackupCommittedMarkerPath) then
    RaiseException('Managed runtime cleanup refused an unsafe redirected path.');

  RecoveredInterruptedRotation :=
    RecoverInterruptedRuntimeRotation(InstalledVersion);
  if not RecoveredInterruptedRotation and
     not IsStrictVersionUpgrade(InstalledVersion) then begin
    Log('Managed runtime cleanup skipped: this is not a version upgrade.');
    Exit;
  end;
  if not DirExists(ManagedRuntimePath) then begin
    Log('Managed runtime cleanup skipped: _internal was not found.');
    Exit;
  end;
  if not FileExists(ManagedExecutablePath) then
    RaiseException('The registered NetOps Suite executable is missing.');
  if DirExists(RuntimeBackupPath) or
     FileExists(RuntimeBackupPath) or
     FileExists(ExecutableBackupPath) or
     DirExists(ExecutableBackupPath) or
     FileExists(RuntimeBackupMarkerTempPath) or
     DirExists(RuntimeBackupMarkerTempPath) or
     FileExists(RuntimeBackupInProgressMarkerPath) or
     FileExists(RuntimeBackupCommittedMarkerPath) then
    RaiseException('A runtime backup path is still occupied.');

  WriteRuntimeBackupMarker(InstalledVersion);
  if not RenameFile(ManagedRuntimePath, RuntimeBackupPath) then begin
    DeleteFile(RuntimeBackupInProgressMarkerPath);
    RaiseException('Could not prepare the previous NetOps Suite runtime.');
  end;
  RuntimeBackupActive := True;
  if not RenameFile(
    ManagedExecutablePath, ExecutableBackupPath
  ) then
    RaiseException('Could not prepare the previous NetOps Suite executable.');
  Log('Prepared the previous NetOps Suite payload for transactional cleanup.');
end;

procedure RestoreManagedRuntime;
begin
  if not RuntimeBackupActive then
    Exit;

  if HasReparsePointInPath(ManagedExecutablePath) or
     HasReparsePointInPath(ManagedRuntimePath) or
     HasReparsePointInPath(ExecutableBackupPath) or
     HasReparsePointInPath(RuntimeBackupPath) then begin
    Log('Runtime restore refused an unsafe redirected path.');
    Exit;
  end;
  if FileExists(ManagedRuntimePath) and
     not DeleteFile(ManagedRuntimePath) then begin
    Log('Could not remove the incomplete replacement runtime file.');
    Exit;
  end;
  if DirExists(ManagedRuntimePath) and
     not DelTree(ManagedRuntimePath, True, True, True) then begin
    Log('Could not remove the incomplete replacement runtime.');
    Exit;
  end;
  if not RenameFile(RuntimeBackupPath, ManagedRuntimePath) then begin
    Log('Could not restore the previous NetOps Suite runtime.');
    Exit;
  end;
  if FileExists(ExecutableBackupPath) then begin
    if FileExists(ManagedExecutablePath) and
       not DeleteFile(ManagedExecutablePath) then begin
      Log('Could not remove the incomplete replacement executable.');
      Exit;
    end;
    if not RenameFile(
      ExecutableBackupPath, ManagedExecutablePath
    ) then begin
      Log('Could not restore the previous NetOps Suite executable.');
      Exit;
    end;
  end;

  DeleteFile(RuntimeBackupInProgressMarkerPath);
  RuntimeBackupActive := False;
  Log('Restored the previous NetOps Suite runtime after setup stopped.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  BackupCleanupSucceeded: Boolean;
begin
  if CurStep = ssInstall then
    RotateManagedRuntime
  else if CurStep = ssDone then begin
    InstallCommitted := True;
    if RuntimeBackupActive then begin
      if PromoteRuntimeBackupMarker then begin
        RuntimeBackupActive := False;
        BackupCleanupSucceeded := True;
        if DirExists(RuntimeBackupPath) and
           not DelTree(RuntimeBackupPath, True, True, True) then
          BackupCleanupSucceeded := False;
        if FileExists(ExecutableBackupPath) and
           not DeleteFile(ExecutableBackupPath) then
          BackupCleanupSucceeded := False;
        if BackupCleanupSucceeded then begin
          if not DeleteFile(RuntimeBackupCommittedMarkerPath) then
            Log('The committed runtime marker will be removed on the next setup.');
          Log('Removed the obsolete NetOps Suite runtime after successful setup.');
        end else
          Log('Could not remove the committed payload backup; cleanup will be retried on the next setup.');
      end else
        Log('Could not commit the runtime backup marker; the next setup will verify the installed version before cleanup.');
    end;
  end;
end;

procedure DeinitializeSetup;
begin
  if RuntimeBackupActive and not InstallCommitted then
    RestoreManagedRuntime;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  InstalledVersion: String;
  VersionComparison: Integer;
begin
  Result := '';
  if not ResolveRegisteredInstall(InstalledVersion) then
    Exit;
  if not TryCompareSemanticVersions(
    '{#AppVersion}', InstalledVersion, VersionComparison
  ) then begin
    Result := 'Setup could not safely compare the installed and incoming versions.';
    Exit;
  end;
  if VersionComparison < 0 then
    Result :=
      'A newer NetOps Suite version is already installed. ' +
      'Uninstall it before installing an older version.';
end;
