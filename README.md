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

## Installieren

[**ClaudeSessionBrowser.exe herunterladen**](https://github.com/juppeee/claude-session-browser/releases/latest/download/ClaudeSessionBrowser.exe)
und **doppelklicken** – das war's.

Beim ersten Start richtet sich die App selbst ein: Sie kopiert sich nach
`%LOCALAPPDATA%\ClaudeSessionBrowser`, legt eine Verknüpfung (Startmenü +
Desktop) an und startet von dort. Die heruntergeladene Datei im Download-Ordner
kann danach gelöscht werden.

> Beim allerersten Start zeigt Windows ggf. eine SmartScreen-Warnung
> („Unbekannter Herausgeber"), weil die App nicht signiert ist. Einfach auf
> **„Weitere Informationen" → „Trotzdem ausführen"** klicken. Danach kommt sie
> nicht mehr (die installierte Kopie trägt keine „aus dem Web"-Markierung).

## Updates veröffentlichen

1. Versionsnummer in `claude_sessions.py` (`VERSION`) erhöhen.
2. Neue `.exe` bauen und als GitHub-Release hochladen.
3. `version.json` aktualisieren (gleiche Versionsnummer + Notiz) und committen/pushen.

Die App vergleicht beim Start ihre `VERSION` mit der `version.json` im Repo und
zeigt bei einer neueren Version einen Hinweis an.

## Lizenz

MIT
