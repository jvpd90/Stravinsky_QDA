; Inno Setup script for the Stravinsky installer -- run through build.ps1,
; which passes the version and folders:
;   ISCC /DAppVersion=1.0.0 /DSourceDir=<dist\Stravinsky> /DOutputDir=<out> installer.iss
;
; Per-user install: no administrator rights needed. Installs to
; %LOCALAPPDATA%\Programs\Stravinsky, adds Start-menu (and optionally
; desktop) shortcuts and an uninstaller. Running a newer setup over an
; older one upgrades it in place (same AppId).

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<folder with Stravinsky.exe>
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

#define AppName "Stravinsky"
#define AppExe "Stravinsky.exe"

[Setup]
; Never change the AppId: it is how Windows recognises upgrades and the uninstaller.
AppId={{6F1E2B7C-4D3A-4C8E-9B21-7A5D3E9F0C41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppPublisherURL=https://github.com/jvpd90/Stravinsky_QDA
AppSupportURL=https://github.com/jvpd90/Stravinsky_QDA/issues
AppUpdatesURL=https://github.com/jvpd90/Stravinsky_QDA/releases
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile={#SourceDir}\_internal\Stravinsky_QDA\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
ShowLanguageDialog=auto

[Languages]
Name: "portuguese"; MessagesFile: "compiler:Languages\Portuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Upgrades replace the bundled libraries wholesale, so no stale files linger.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
