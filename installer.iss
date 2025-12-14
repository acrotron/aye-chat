; Inno Setup Script for Aye Chat
; Build with: iscc installer.iss

#define MyAppName "Aye Chat"
#define MyAppPublisher "Acrotron, Inc."
#define MyAppURL "https://github.com/AcrotronAI/aye-chat"
#define MyAppExeName "aye.exe"

; Version is passed from CI/CD via /DMyAppVersion=x.x.x
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

; Numeric version for Windows VersionInfo (must be X.X.X.X format)
#ifndef MyAppNumericVersion
  #define MyAppNumericVersion "0.0.0.0"
#endif

[Setup]
; Unique identifier for this application (generate new GUID for your app)
AppId={{A7E3B8C1-5D4F-4E6A-9B2C-1D3E5F7A8B9C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={userpf}\AyeChat
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Output settings
OutputDir=Output
OutputBaseFilename=aye-chat-setup
; Compression
Compression=lzma2/max
SolidCompression=yes
; Modern installer look
WizardStyle=modern
; Per-user installation (no admin required)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Architecture
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Uninstall settings
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; Version info (VersionInfoVersion must be numeric X.X.X.X format)
VersionInfoVersion={#MyAppNumericVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
VersionInfoCopyright=Copyright (C) 2024-2025 {#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppNumericVersion}
; Icon (optional - uncomment if you have one)
SetupIconFile=assets\aye-chat.ico
; Disclaimer (informational, shown first)
InfoBeforeFile=DISCLAIMER
; License (must accept before installing)
LicenseFile=LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "addtopath"; Description: "Add to PATH environment variable"; GroupDescription: "System integration:"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "contextmenu"; Description: "Add ""Open Aye Chat here"" to folder context menu"; GroupDescription: "System integration:"

[Files]
; Main application files from PyInstaller output
Source: "dist\aye-chat\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; License and disclaimer files
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "DISCLAIMER"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu shortcut
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
; Desktop shortcut (optional)
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Add context menu for folders (right-click on folder) - uses HKCU for per-user install
Root: HKCU; Subkey: "Software\Classes\Directory\shell\ayechat"; ValueType: string; ValueData: "Open Aye Chat here"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\ayechat"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\ayechat\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" chat -r ""%1"""; Tasks: contextmenu

; Add context menu for folder background (right-click inside folder)
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\ayechat"; ValueType: string; ValueData: "Open Aye Chat here"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\ayechat"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\ayechat\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" chat -r ""%V"""; Tasks: contextmenu

[Code]
// Pascal script for PATH manipulation

const
  SMTO_ABORTIFHUNG = 2;
  WM_SETTINGCHANGE = $001A;
  // User environment for per-user install (no admin required)
  EnvironmentKey = 'Environment';

// External function declaration for broadcasting environment changes
function SendMessageTimeoutW(hWnd: HWND; Msg: UINT; wParam: LongInt; lParam: String; fuFlags: UINT; uTimeout: UINT; var lpdwResult: DWORD): DWORD;
  external 'SendMessageTimeoutW@user32.dll stdcall';

function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  // Look for the path with leading and trailing semicolon
  Result := Pos(';' + Param + ';', ';' + OrigPath + ';') = 0;
end;

procedure AddToPath();
var
  OrigPath: string;
  NewPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', OrigPath) then
    OrigPath := '';

  if NeedsAddPath(ExpandConstant('{app}')) then
  begin
    if OrigPath <> '' then
      NewPath := OrigPath + ';' + ExpandConstant('{app}')
    else
      NewPath := ExpandConstant('{app}');

    RegWriteExpandStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', NewPath);
  end;
end;

procedure RemoveFromPath();
var
  OrigPath: string;
  NewPath: string;
  AppDir: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', OrigPath) then
    exit;

  AppDir := ExpandConstant('{app}');
  NewPath := ';' + OrigPath + ';';

  // Remove the app directory from path
  P := Pos(';' + AppDir + ';', NewPath);
  if P > 0 then
  begin
    Delete(NewPath, P, Length(AppDir) + 1);
    // Clean up the string
    NewPath := Copy(NewPath, 2, Length(NewPath) - 2);
    RegWriteExpandStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', NewPath);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('addtopath') then
      AddToPath();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath();
end;

// Broadcast environment change to all windows
procedure BroadcastEnvironmentChange();
var
  Res: DWORD;
begin
  // This notifies other applications that the environment has changed
  SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, 'Environment', SMTO_ABORTIFHUNG, 5000, Res);
end;

procedure DeinitializeSetup();
begin
  BroadcastEnvironmentChange();
end;

procedure DeinitializeUninstall();
begin
  BroadcastEnvironmentChange();
end;
