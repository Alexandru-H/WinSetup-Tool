; WinSetup Tool — Inno Setup script
; Build: iscc.exe installer\setup.iss
; Output: dist\WinSetupTool-1.0.0-Setup.exe

#define MyAppName "WinSetup Tool"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Alexandru Hoaghea"
#define MyAppExeName "WinSetupTool.exe"
#define MyAppId "{{8F9C2E1A-3B4D-4E5F-A6B7-C8D9E0F1A2B3}"

[Setup]
; Identity & metadata
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=
AppSupportURL=
AppUpdatesURL=
AppCopyright=Copyright (C) 2026 {#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}

; Install destination — Program Files (system-wide)
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableDirPage=no
DisableProgramGroupPage=yes

; Privileges — install requires admin (writes to Program Files)
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=

; Uninstaller registration in Programs and Features
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}

; Visual & UX
WizardStyle=modern
SetupIconFile=..\Furina_icon.ico
LicenseFile=..\LICENSE.txt
ShowLanguageDialog=no

; Compression — LZMA2 max gives best size/speed tradeoff
Compression=lzma2/max
SolidCompression=yes

; Output
OutputDir=..\dist
OutputBaseFilename=WinSetupTool-{#MyAppVersion}-Setup

; Architecture
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Minimum OS — Windows 10 1809+
MinVersion=10.0.17763

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Ship the entire onedir build folder.
; Source path is relative to this .iss file (installer/), so
; ..\dist\WinSetupTool\ resolves correctly.
Source: "..\dist\WinSetupTool\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu shortcut — always created
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; \
    Filename: "{uninstallexe}"

; Desktop shortcut — only if user opted in via Tasks page
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch app at end of installer (unchecked by default)
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent unchecked

[Code]
// ----------------------------------------------------------------------
// Pre-install / pre-uninstall check: ensure WinSetupTool.exe isn't
// running. Inno Setup can't overwrite a locked .exe, so we prompt
// the user to close it first.
// ----------------------------------------------------------------------

function IsAppRunning(const FileName: string): Boolean;
var
  WMI: Variant;
  Procs: Variant;
begin
  Result := False;
  try
    WMI := CreateOleObject('WbemScripting.SWbemLocator');
    WMI := WMI.ConnectServer('.', 'root\CIMV2');
    Procs := WMI.ExecQuery(
      'SELECT * FROM Win32_Process WHERE Name = "' + FileName + '"');
    Result := (Procs.Count > 0);
  except
    // If WMI query fails, assume not running — safer than blocking
    // install on a check failure
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if IsAppRunning('{#MyAppExeName}') then
  begin
    if MsgBox(
      '{#MyAppName} is currently running.' + #13#10 +
      'Please close it before continuing the installation.' + #13#10#13#10 +
      'Click OK after closing the app, or Cancel to abort.',
      mbConfirmation, MB_OKCANCEL) = IDCANCEL then
    begin
      Result := 'Installation cancelled. Close {#MyAppName} and try again.';
      Exit;
    end;
    // Re-check after user said OK
    if IsAppRunning('{#MyAppExeName}') then
      Result := '{#MyAppName} is still running. Please close it manually.';
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if IsAppRunning('{#MyAppExeName}') then
  begin
    if MsgBox(
      '{#MyAppName} is currently running.' + #13#10 +
      'Please close it before uninstalling.' + #13#10#13#10 +
      'Click OK after closing the app, or Cancel to abort.',
      mbConfirmation, MB_OKCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
    // Re-check
    if IsAppRunning('{#MyAppExeName}') then
    begin
      MsgBox('{#MyAppName} is still running. Please close it and try again.',
             mbError, MB_OK);
      Result := False;
    end;
  end;
end;
