#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#ifndef SourceDir
  #error SourceDir is not defined
#endif

#ifndef OutputDir
  #error OutputDir is not defined
#endif

[Setup]
AppId={{E5B8B0F9-5B63-4A5F-BB0A-89F14E37E7B8}
AppName=NetOps Suite
AppVersion={#AppVersion}
AppPublisher=NetOps Suite
DefaultDirName={autopf}\NetOps Suite
DefaultGroupName=NetOps Suite
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir={#OutputDir}
OutputBaseFilename=NetOpsSuite-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\assets\icons\netops_toolkit.ico
UninstallDisplayIcon={app}\NetOpsSuite.exe

[Tasks]
Name: "desktopicon"; Description: "바탕 화면 바로가기 만들기"; GroupDescription: "추가 아이콘:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\NetOps Suite"; Filename: "{app}\NetOpsSuite.exe"
Name: "{autodesktop}\NetOps Suite"; Filename: "{app}\NetOpsSuite.exe"; Tasks: desktopicon
