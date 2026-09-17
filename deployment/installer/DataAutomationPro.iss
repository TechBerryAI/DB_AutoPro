; ---------------------------------------------------------------------------
;  Data Automation Pro - Windows installer (Inno Setup 6)
;
;  Build with deployment\build.bat, which passes the version in:
;      ISCC.exe /DAppVersion=0.0.1-demo deployment\installer\DataAutomationPro.iss
;
;  Install model
;  -------------
;  Per-user install into {localappdata}\Programs\Data Automation, so the app
;  runs without administrator rights AND can write its own config/, logs/ and
;  backups/ folders. See deployment\README.md for the production alternative.
; ---------------------------------------------------------------------------

#define AppName "Data Automation Pro"
#define AppPublisher "TechBerry AI"
#define AppExeName "Data Automation Pro.exe"

#ifndef AppVersion
  #define AppVersion "0.0.1-demo"
#endif

; Numeric four-part version for the Windows file-version resource.
#ifndef FileVersion
  #define FileVersion "0.0.1.0"
#endif

; Absolute paths are derived from this script's location - nothing is hard-coded
; to a particular developer machine.
#define ProjectRoot AddBackslash(SourcePath) + "..\.."
#define DistDir ProjectRoot + "\dist\" + AppName
#define IconFile ProjectRoot + "\deployment\assets\DataAutomationPro.ico"

[Setup]
; A stable AppId is what makes "install over the previous demo" an upgrade
; rather than a second, parallel installation. Do not change it between
; versions of this product.
AppId={{8F3C21A6-5B7E-4E2D-9C41-0A6D5E7B1C93}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
VersionInfoVersion={#FileVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir={#ProjectRoot}\dist
OutputBaseFilename=Data Automation Pro Setup Demo
SetupIconFile={#IconFile}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install: no UAC prompt, and the app can write to its own folder.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
DisableDirPage=no
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Dirs]
; User data directories. "uninsneveruninstall" keeps backups and logs on the
; disk when the demo is uninstalled or upgraded.
Name: "{app}\backups"; Flags: uninsneveruninstall
Name: "{app}\logs"; Flags: uninsneveruninstall
Name: "{app}\config"; Flags: uninsneveruninstall

[Files]
; Application payload. config\*.json is excluded here and handled below so that
; a re-install never overwrites saved servers, accounts or UI preferences.
Source: "{#DistDir}\*"; DestDir: "{app}"; \
    Excludes: "config\*.json,logs\*,backups\*"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; YAML profiles ship with the app and are refreshed on upgrade; client-specific
; overrides belong in config\prod.yaml (see README).
; Runtime JSON state is written by the app itself on first launch and is never
; shipped, because saved_servers.json / ui_preferences.json hold credentials.

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
    WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove only compiled caches the app generates; user data is left in place.
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Make sure the writable data folders exist even on a silent install.
    ForceDirectories(ExpandConstant('{app}\logs'));
    ForceDirectories(ExpandConstant('{app}\backups'));
    ForceDirectories(ExpandConstant('{app}\config'));
  end;
end;
