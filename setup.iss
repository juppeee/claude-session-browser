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
#define MyAppVersion "1.3.1"
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
; NUR die Haupt-App per Restart-Manager schliessen. Ohne diesen Filter greift
; Inno auch nach csb_updater.exe - und genau der fuehrt gerade das Update aus.
; Der Installer wartete dann ewig darauf, dass sich der Updater beendet.
CloseApplicationsFilter=ClaudeSessionBrowser.exe

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
; Separater Updater (wie Chrome/VS Code). restartreplace: laeuft gerade eine
; alte Updater-Version aus diesem Ordner, ist die Datei gesperrt - dann wird
; sie beim naechsten Neustart ersetzt statt den Install scheitern zu lassen.
Source: "dist\csb_updater.exe"; DestDir: "{app}"; Flags: ignoreversion restartreplace

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
var
  InstallLog: String;

procedure Log(Msg: String);
var
  LogFile: String;
begin
  LogFile := ExpandConstant('{tmp}\csb_installer_debug.log');
  SaveStringToFile(LogFile, Msg + #13#10, True);
end;

function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
  LockFile: String;
  I: Integer;
begin
  Result := True;
  InstallLog := ExpandConstant('{tmp}\csb_installer_debug.log');
  SaveStringToFile(InstallLog, '=== CSB Installer Start ===' + #13#10, False);

  if WizardSilent then
  begin
    Log('Silent-Mode erkannt');

    // Mehrfach versuchen alle CSB-Prozesse zu killen
    for I := 1 to 3 do
    begin
      Log('Kill-Versuch ' + IntToStr(I));
      Exec(ExpandConstant('{cmd}'),
           '/c taskkill /F /IM ClaudeSessionBrowser.exe /T 2>nul',
           '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Sleep(1000);
    end;

    // 5s warten bis alles freigegeben
    Log('Warte 5s...');
    Sleep(5000);

    // Lock-File loeschen
    LockFile := ExpandConstant('{localappdata}\ClaudeSessionBrowser.instance.lock');
    if FileExists(LockFile) then
    begin
      Log('Loesche Lock-File: ' + LockFile);
      DeleteFile(LockFile);
    end;

    Log('InitializeSetup fertig');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    Log('PostInstall - App-Pfad: ' + ExpandConstant('{app}'));
    Log('PostInstall fertig');
  end;
end;

procedure DeinitializeSetup();
var
  AppPath: String;
  ResultCode: Integer;
begin
  Log('DeinitializeSetup - Finaler Start der App');

  // Sicherheits-Start: Falls [Run] nicht funktioniert hat
  if WizardSilent then
  begin
    AppPath := ExpandConstant('{app}\{#MyAppExeName}');
    Log('Starte App: ' + AppPath);
    if FileExists(AppPath) then
    begin
      Exec(AppPath, '', '', SW_SHOW, ewNoWait, ResultCode);
      Log('App gestartet, ResultCode: ' + IntToStr(ResultCode));
    end
    else
      Log('FEHLER: App nicht gefunden: ' + AppPath);
  end;

  Log('=== Installer Ende ===');
end;
