; ==========================================================================
; Claude Session Browser - Inno Setup Installer-Script
; ==========================================================================
; Erzeugt einen per-user Installer (kein Admin noetig) der die onedir-Version
; nach %LOCALAPPDATA%\Programs\ClaudeSessionBrowser\ installiert.
;
; WICHTIG: Der Installer fasst NIEMALS %USERPROFILE%\.claude\ an - dort
; liegen die User-Settings und die JSONL-Session-Files. Weder Install noch
; Uninstall duerfen das anfassen.
;
; Silent-Install (fuer Auto-Updater):
;   ClaudeSessionBrowser-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
; ==========================================================================

#define MyAppName "Claude Session Browser"
#define MyAppVersion "1.1.6"
#define MyAppPublisher "juppeee"
#define MyAppURL "https://github.com/juppeee/claude-session-browser"
#define MyAppExeName "ClaudeSessionBrowser.exe"

[Setup]
; AppId ist ein stabiler GUID - MUSS ueber alle Versionen identisch bleiben
; damit Inno Upgrades erkennt statt paralleler Installs.
AppId={{A2E1C4F8-9B3D-4E5A-8F2B-7C6D5A4E3F21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}

; Per-user Install (kein Admin-Prompt)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Default-Zielordner: %LOCALAPPDATA%\Programs\ClaudeSessionBrowser\
DefaultDirName={autopf}\ClaudeSessionBrowser
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableReadyPage=no
DisableDirPage=no
DisableFinishedPage=no

; Uninstall-Eintrag in "Apps und Features" (Windows-Systemsteuerung)
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}

; Ausgabe
OutputDir=dist
OutputBaseFilename=ClaudeSessionBrowser-Setup
SetupIconFile=claude_sessions.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

; Kein Restart, keine Ready-Dialoge im Silent-Mode
CloseApplications=force
RestartApplications=no

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Beim Windows-Start automatisch mitstarten"; GroupDescription: "Systemintegration"; Flags: unchecked

[Files]
; Kompletter onedir-Output. Der Runner selbst + alle DLLs + _internal-Ordner.
Source: "dist\ClaudeSessionBrowser\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\ClaudeSessionBrowser\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Autostart-Eintrag - nur wenn User im Wizard opt-in
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "ClaudeSessionBrowser"; \
    ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: startupicon

[Run]
; Nach dem Install starten - WICHTIG: auch bei Silent-Install starten!
; Der alte Update-Batch (v1.1.2/v1.1.3) hatte einen Bug beim Relaunch.
; Indem der Installer selbst die App startet ist der Batch egal.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall

[UninstallRun]
; Alte Runner-Instanz beenden bevor Dateien geloescht werden (verhindert Lock)
Filename: "{cmd}"; Parameters: "/c taskkill /F /IM {#MyAppExeName} /T"; \
    Flags: runhidden; RunOnceId: "KillRunner"

[UninstallDelete]
; Nur {app} entfernen (macht Inno eh). ABSICHTLICH NICHT loeschen:
;   - %USERPROFILE%\.claude\session_browser_settings.json  (Settings)
;   - %USERPROFILE%\.claude\projects\                       (User-Sessions)
; Das bleibt beim User auch nach Uninstall.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
// Beim Silent-Install laufende Instanz sauber beenden bevor ueberschrieben
// wird. CloseApplications=force macht das eigentlich schon, aber Sicherheit.
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
  LockFile: String;
begin
  Result := True;
  if WizardSilent then
  begin
    // Alte Instanz killen
    Exec(ExpandConstant('{cmd}'),
         '/c taskkill /F /IM ' + '{#MyAppExeName}' + ' /T',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    // 3s warten bis Mutex + Lock freigegeben
    Sleep(3000);
    // Stale Lock-File loeschen falls vorhanden (Single-Instance-Guard)
    LockFile := ExpandConstant('{localappdata}\ClaudeSessionBrowser.instance.lock');
    if FileExists(LockFile) then
      DeleteFile(LockFile);
  end;
end;
