# Claude Session Browser

Ein modernes Desktop-Tool (Windows), um alle lokalen **Claude-Code-Sessions**
zu durchsuchen und per Klick wieder einzusteigen (`claude --resume <id>`).

![Logo](claude_sessions.ico)

## Funktionen

- Übersicht aller lokalen Sessions aus `~/.claude/projects`
- Titel (Claudes Auto-Titel oder eigene), Ordner, Nachrichten-Anzahl, letzte Aktivität
- Suche, klickbare Sortierung, anpassbare Spalten
- Sessions einfärben, umbenennen, ID kopieren
- Wiedereinstieg per Knopfdruck (Windows Terminal oder cmd)
- Einstellungen: Sessions-Ordner (mit Auto-Suche), ausgeblendete Ordner,
  Akzentfarbe, Terminal-Wahl, Claude-Befehl
- **In-App-Updater** über GitHub (offline-sicher: ohne Internet wird der Check
  einfach übersprungen)

## Starten (aus dem Quellcode)

Voraussetzung: Python 3 mit `pywebview` (nutzt die Edge-WebView2-Engine, auf
Windows 10/11 vorinstalliert).

```bash
pip install pywebview
python claude_sessions.py
```

## Eigenständige .exe bauen

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name ClaudeSessionBrowser --icon claude_sessions.ico claude_sessions.py
```

Die fertige Datei liegt danach unter `dist/ClaudeSessionBrowser.exe` und läuft
ohne Python-Installation.

## Windows-Sicherheitswarnung beim ersten Start

Die `.exe` ist nicht signiert (kein kostenpflichtiges Zertifikat), daher zeigt
Windows beim **heruntergeladenen** Programm eine Warnung („Unbekannter
Herausgeber" / SmartScreen) oder blockiert es über Smart App Control
(„Gefährliche Dateierweiterung aus dem Web"). Das ist normal und harmlos.

**Lösung – die „aus dem Web"-Markierung entfernen** (PowerShell):

```powershell
Unblock-File "$env:USERPROFILE\Downloads\ClaudeSessionBrowser.exe"
```

**Oder gleich beim Download** (lädt nach LocalAppData und entsperrt sofort):

```powershell
$d="$env:LOCALAPPDATA\ClaudeSessionBrowser"; ni -ItemType Directory -Force $d | Out-Null
iwr "https://github.com/juppeee/claude-session-browser/releases/latest/download/ClaudeSessionBrowser.exe" -OutFile "$d\ClaudeSessionBrowser.exe"
Unblock-File "$d\ClaudeSessionBrowser.exe"; explorer $d
```

Hinweis: Updates über den eingebauten Updater sind davon nicht betroffen – die
laden die Datei ohne Web-Markierung herunter.

## Updates veröffentlichen

1. Versionsnummer in `claude_sessions.py` (`VERSION`) erhöhen.
2. Neue `.exe` bauen und als GitHub-Release hochladen.
3. `version.json` aktualisieren (gleiche Versionsnummer + Notiz) und committen/pushen.

Die App vergleicht beim Start ihre `VERSION` mit der `version.json` im Repo und
zeigt bei einer neueren Version einen Hinweis an.

## Lizenz

MIT
