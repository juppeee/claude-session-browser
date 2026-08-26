# -*- coding: utf-8 -*-
"""
Zweisprachigkeit fuer den Claude Session Browser.

Der deutsche Satz ist der Schluessel:

    t("Mit Windows starten")            -> "Start with Windows"
    t("Noch {pct}% Akku", pct=12)       -> "Battery at {pct}%" -> "Battery at 12%"

Fehlt eine Uebersetzung, kommt der deutsche Satz zurueck. Die Oberflaeche
bleibt damit immer bedienbar, auch waehrend die Tabelle noch waechst.

Dieselbe Tabelle bedient Python und JavaScript: `js_payload()` reicht sie
beim Start in die Oberflaeche, `set_lang()` liefert sie beim Umschalten neu.
"""

import ctypes
import json
import re

# Sprachen, die es gibt. Alles andere faellt auf Englisch zurueck.
LANGS = ("de", "en")

_lang = "de"

# Platzhalter der Form {name} - fuer die Pruefung in tools/check_i18n.py
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


# --------------------------------------------------------------------------- #
#  Systemsprache
# --------------------------------------------------------------------------- #
def detect_system_lang():
    """Deutsch, wenn die Windows-Oberflaeche deutsch ist - sonst Englisch.

    GetUserDefaultUILanguage() liefert eine LANGID; die unteren 10 Bit sind
    die Hauptsprache, 0x07 steht fuer Deutsch. Damit sind alle Varianten
    abgedeckt (de-DE, de-AT, de-CH), ohne sie einzeln aufzuzaehlen.
    """
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "de" if (langid & 0x3FF) == 0x07 else "en"
    except Exception:
        # Kein Windows oder API nicht erreichbar: Englisch ist die
        # sicherere Annahme, Deutsch waere ein Sonderfall.
        return "en"


def resolve(setting):
    """Einstellungswert ('auto' | 'de' | 'en') -> tatsaechliche Sprache."""
    if setting in LANGS:
        return setting
    return detect_system_lang()


# --------------------------------------------------------------------------- #
#  Umschalten und uebersetzen
# --------------------------------------------------------------------------- #
def set_lang(setting):
    """Sprache setzen. Nimmt auch 'auto' und loest es auf."""
    global _lang
    _lang = resolve(setting)
    return _lang


def current():
    return _lang


def table(lang=None):
    """Die Tabelle der gerade aktiven Sprache. Fuer Deutsch leer - dort ist
    der Schluessel schon der fertige Satz."""
    return TRANSLATIONS.get(lang or _lang) or {}


def t(text, **vars):
    """Uebersetzt und setzt Platzhalter ein.

    Die Platzhalter bleiben Teil des Schluessels, damit die Wortstellung im
    Englischen frei ist: "Frei in {mins} Minuten" kann zu "{mins} minutes to
    go" werden, ohne dass der Code etwas davon merkt.
    """
    out = TRANSLATIONS.get(_lang, {}).get(text, text)
    if vars:
        try:
            return out.format(**vars)
        except (KeyError, IndexError, ValueError):
            # Kaputte Uebersetzung soll die App nicht mitreissen. Lieber der
            # deutsche Satz als eine Ausnahme mitten im Tray-Menue.
            try:
                return text.format(**vars)
            except Exception:
                return text
    return out


def js_payload(setting=None):
    """Sprache + Tabelle als JSON fuer die Oberflaeche."""
    lang = resolve(setting) if setting is not None else _lang
    return json.dumps({"lang": lang, "table": TRANSLATIONS.get(lang) or {}},
                      ensure_ascii=False)


def placeholders(text):
    """Menge der Platzhalternamen in einem Satz - fuer die Pruefung."""
    return set(_PLACEHOLDER.findall(text or ""))


# --------------------------------------------------------------------------- #
#  Die Tabelle
# --------------------------------------------------------------------------- #
# Deutsch braucht keinen Eintrag: der Schluessel ist der deutsche Satz.
#
# Regeln fuer neue Eintraege:
#   - natuerliches Englisch, keine Wort-fuer-Wort-Uebertragung
#   - Fachbegriffe wie Claude Code sie benutzt: Session bleibt Session
#   - Platzhalter muessen links und rechts dieselben sein ({pct} bleibt {pct})
#   - Namen bleiben: Clawd, Buddy, Clawdmeter, Claude Session Browser
TRANSLATIONS = {
    "en": {
        # ---- Zeitangaben -------------------------------------------------
        "heute {zeit}": "today {zeit}",
        "gestern {zeit}": "yesterday {zeit}",
        "vor {tage} Tagen": "{tage} days ago",
        # Im Englischen steht der Monat vorn. %b liefert ohne gesetztes Locale
        # die englischen Kuerzel (Jul, Aug …), genau richtig hier.
        "%d.%m.%Y": "%b %d, %Y",

        # ---- Monitore ----------------------------------------------------
        "Primär": "Primary",
        "Monitor {nr}": "Display {nr}",

        # ---- Session starten ---------------------------------------------
        "Ungültige Session-ID.": "Invalid session ID.",
        "Unsicherer claude_cmd-Wert.": "Unsafe value for claude_cmd.",
        "Windows Terminal (wt) nicht gefunden.":
            "Windows Terminal (wt) not found.",

        # ---- Tray und Benachrichtigungen ---------------------------------
        "Öffnen": "Open",
        "Beenden": "Quit",
        "Dein Claude-Limit ist zurück – weitermachen!":
            "Your Claude limit is back – carry on!",
        "Dein Claude-Limit ist zurückgesetzt": "Your Claude limit has reset",
        "Du kannst weitermachen": "You're good to go",
        "Clawdmeter hat nur noch {pct}% Akku":
            "Clawdmeter is down to {pct}% battery",
        "{pct}% deines 5-Stunden-Limits verbraucht. "
        "Zurückgesetzt um {when} – in {mins} Minuten.":
            "{pct}% of your 5-hour limit used. "
            "Resets at {when} – in {mins} minutes.",

        # ---- Update ------------------------------------------------------
        "Update läuft bereits.": "An update is already running.",
        "Installer-Download unvollständig.":
            "The installer download is incomplete.",
        "Heruntergeladener Installer ist keine gültige .exe.":
            "The downloaded installer is not a valid .exe.",
        "Heruntergeladene Datei ist keine gültige .exe.":
            "The downloaded file is not a valid .exe.",
        "Ungültiger SHA-256 im Server-Manifest.":
            "Invalid SHA-256 in the server manifest.",
        "Integritäts-Prüfung fehlgeschlagen "
        "(SHA-256). Update abgebrochen.":
            "Integrity check failed (SHA-256). Update cancelled.",
        "Integritäts-Prüfung fehlgeschlagen "
        "(SHA-256 stimmt nicht). Update abgebrochen.":
            "Integrity check failed (SHA-256 mismatch). Update cancelled.",
        "Kein Internet / Repo nicht erreichbar.":
            "No connection, or the repository is unreachable.",
        "Download unvollständig – bitte erneut versuchen.":
            "The download is incomplete – please try again.",

        # ---- Oberflaeche (uebersetzt in Bloecken) -------------------------
        "(erkennt Terminal +":
            "(detects terminal +",
        "(ohne Titel)":
            "(untitled)",
        "(unbekannt)":
            "(unknown)",
        ") laufen, verstecken. Standardmäßig aus – aktiviere es nur, wenn dich diese Sessions stören.":
            ") running, hide them. Off by default – only turn it on if these sessions bother you.",
        "+ Ordner der gewählten Session ausblenden":
            "+ hide the folder of the selected session",
        "0%":
            "0%",
        "Ab wie viel Prozent des 5-Stunden-Limits gewarnt wird.":
            "The percentage of the 5-hour limit at which you get warned.",
        "Ab wie viel Restladung gewarnt wird.":
            "The remaining battery level at which you get warned.",
        "Abbrechen":
            "Cancel",
        "Akku-Warnung ab {v}%":
            "Battery warning below {v}%",
        "Aktivieren":
            "Enable",
        "Aktualisieren":
            "Refresh",
        "Aktuell bei":
            "Currently at",
        "Anbindung aktiv":
            "Connection active",
        "Anzahl ausgetauschter Nachrichten – gute Anhaltszahl für den Umfang.":
            "Number of messages exchanged – a good indicator of the session's size.",
        "App":
            "App",
        "App jetzt komplett beenden":
            "Quit the app completely now",
        "Aus offenen Fenstern wählen…":
            "Choose from open windows…",
        "Auslastung":
            "Usage",
        "Aussehen":
            "Appearance",
        "Auto":
            "Auto",
        "Automatisch erzeugte Kurzbeschreibung der Session – oder dein selbst vergebener Name.":
            "Automatically generated short description of the session – or the name you gave it yourself.",
        "Autostart an ✓":
            "Start with Windows on ✓",
        "Autostart aus":
            "Start with Windows off",
        "Autostart konnte nicht gesetzt werden":
            "Couldn't set startup",
        "Bei Limit-Reset benachrichtigen":
            "Notify on limit reset",
        "Buddy":
            "Buddy",
        "Buddy an ✓":
            "Buddy on ✓",
        "Buddy aus":
            "Buddy off",
        "Buddy platzieren…":
            "Place Buddy…",
        "Buddy zeigt: {name}":
            "Buddy is showing: {name}",
        "Buddy: Überraschung!":
            "Buddy: surprise!",
        "C:\\Users\\...":
            "C:\\Users\\...",
        "Cam-Name":
            "Camera name",
        "Claude":
            "Claude",
        "Claude Session Browser":
            "Claude Session Browser",
        "Claude-Befehl":
            "Claude command",
        "Claude-Buddy":
            "Claude Buddy",
        "Clawd-Buddy spiegeln":
            "Mirror Clawd Buddy",
        "Clawdmeter":
            "Clawdmeter",
        "Clawdmeter an ✓":
            "Clawdmeter on ✓",
        "Clawdmeter aus":
            "Clawdmeter off",
        "Das Arbeitsverzeichnis, in dem die Session gestartet wurde.":
            "The working directory the session was started in.",
        "Das Gerät und seine Firmware stammen von Hermann Björgvin. Für Verbrauch und Akku reicht seine Firmware — der Session Browser bringt nur die Anbindung für Windows mit.":
            "The device and its firmware come from Hermann Björgvin. For usage and battery his firmware is all you need — the Session Browser only adds the Windows connection.",
        "Clawdmeter-Firmware, Fork":
            "Clawdmeter firmware, fork",
        "Hermanns Firmware sucht sich die Animation nach Verbrauchsgeschwindigkeit aus. Damit das Gerät zeigt, was Claude gerade macht, muss dieser Fork darauf laufen — Branch csb-buddy.":
            "Hermann's firmware picks its animation from how fast your quota is burning. For the device to show what Claude is doing, it needs this fork on it — branch csb-buddy.",
        "Das Gerät zeigt dieselbe Animation wie dein Clawd-Buddy auf dem Desktop — statt selbst eine nach Auslastung zu wählen. Braucht einen eingeschalteten Buddy.":
            "The device shows the same animation as your Clawd Buddy on the desktop — instead of picking one based on usage itself. Needs Buddy to be turned on.",
        "Das siehst du für jede Session. Alle Spalten kannst du in den Einstellungen ein-/ausblenden und die Reihenfolge ändern.":
            "You'll see this for every session. You can show/hide any column and change the order in the settings.",
        "Deckkraft":
            "Opacity",
        "Dein":
            "Your",
        "Dein Browser für alle lokalen Claude-Code-Sessions – durchsuchen, einfärben und per Klick wieder einsteigen. Lass uns kurz einrichten – dauert nur eine Minute.":
            "Your browser for all your local Claude Code sessions – search, color-code, and jump back in with a click. Let's set things up – it only takes a minute.",
        "Deine Einstellungen, Farben und Titel bleiben dabei vollständig erhalten.":
            "Your settings, colors, and titles stay fully intact.",
        "Deine allererste Nachricht der Session (standardmäßig ausgeblendet).":
            "The very first message of the session (hidden by default).",
        "Der Buddy erscheint nur, wenn das gewählte Fenster gerade im Vordergrund ist.":
            "Buddy only shows up when the selected window is in the foreground.",
        "Der Buddy ist ausgeschaltet. Die Einstellungen darunter wirken erst, wenn du ihn oben einschaltest.":
            "Buddy is turned off. The settings below only kick in once you turn it on above.",
        "Der Buddy kann immer da sein oder nur wenn ein bestimmtes Programm gerade im Vordergrund ist – z.B. nur wenn Claude Code im Terminal läuft.":
            "Buddy can always be around, or only show up when a specific program is in the foreground – for example, only while Claude Code is running in the terminal.",
        "Details ansehen":
            "View details",
        "Deutsch":
            "German",
        "Die Akzentfarbe der Oberfläche. Du kannst sie später jederzeit in den Einstellungen ändern.":
            "The accent color of the interface. You can change it anytime later in settings.",
        "Die App startet automatisch nach dem Anmelden – praktisch damit der Buddy und der Tray-Modus sofort verfügbar sind. Registry-Eintrag unter HKCU\\Run.":
            "The app starts automatically when you log in – handy so Buddy and tray mode are ready right away. Registry entry under HKCU\\Run.",
        "Die Spalten":
            "The columns",
        "Die wichtigsten Handgriffe – der Rest ergibt sich beim Ausprobieren.":
            "The essentials – you'll figure out the rest as you go.",
        "Diese App – Quelltext und Releases auf GitHub.":
            "This app – source code and releases on GitHub.",
        "Doppelklick":
            "Double-click",
        "Durchsuchen…":
            "Browse…",
        "Ecken/Kanten per Schnellwahl (auf jedem Monitor) oder „Buddy platzieren…\" für freies Ziehen mit Raster.":
            "Corners/edges via quick-select (on any monitor), or \"Place Buddy…\" for free dragging with grid snapping.",
        "Ein winziger animierter Clawd (20×20 Pixel) schwebt auf dem Desktop und zeigt, was gerade passiert – schreibt Claude gerade Code, denkt er nach, wurde ein Limit erreicht? Standardmäßig taucht er nur auf wenn Claude Code läuft, blendet sich weich rein und wieder aus.":
            "A tiny animated Clawd (20×20 pixels) floats on your desktop and shows what's happening right now – is Claude writing code, thinking, or did it hit a limit? By default it only shows up while Claude Code is running, fading smoothly in and out.",
        "Ein winziger animierter Clawd (20×20 Pixel) schwebt auf dem Desktop – frameless, immer im Vordergrund. Zieh ihn mit der Maus wohin du magst. Rechts- oder Doppelklick schickt ihn kurz weg – er kommt beim nächsten neuen Claude-Terminal von selbst zurück.":
            "A tiny animated Clawd (20×20 pixels) floats on your desktop – frameless, always on top. Drag it with your mouse wherever you like. Right-click or double-click sends it away for a bit – it comes back on its own with the next new Claude terminal.",
        "Eingabeaufforderung (cmd)":
            "Command Prompt (cmd)",
        "Einstellungen":
            "Settings",
        "English":
            "English",
        "Enter":
            "Enter",
        "Erst im Tab „Sessions\" eine Session auswählen.":
            "First select a session in the \"Sessions\" tab.",
        "Erste Frage":
            "First question",
        "F11":
            "F11",
        "F2":
            "F2",
        "Farbe":
            "Color",
        "Farbe der Session festlegen":
            "Set session color",
        "Farbe für Buttons, Auswahl und Hervorhebungen.":
            "Color for buttons, selections, and highlights.",
        "Farbe für diese Session":
            "Color for this session",
        "Fast geschafft":
            "Almost there",
        "Fenster auswählen":
            "Select window",
        "Filtert live nach Titel, Ordner, ID oder erster Frage – auch mit mehreren Wörtern.":
            "Filters live by title, folder, ID, or first question – works with multiple words too.",
        "Gerät":
            "Device",
        "Gerät gewählt ✓":
            "Device selected ✓",
        "Gerät spiegelt den Clawd-Buddy ✓":
            "Device mirrors the Clawd Buddy ✓",
        "Gerät wird automatisch gesucht":
            "Automatically searching for a device",
        "Gerät wählt wieder selbst":
            "Device picks automatically again",
        "Geräte neu suchen":
            "Search for devices again",
        "Grundton der Oberfläche – Flächen, Zeilen und Ränder werden daraus abgeleitet.":
            "Base tone of the interface – surfaces, rows, and borders are derived from it.",
        "Größe":
            "Size",
        "Größe 40–200 px, Deckkraft, optionaler Rahmen in deiner Wunschfarbe.":
            "Size 40–200 px, opacity, optional border in the color of your choice.",
        "Größe und Deckkraft ändern sich sofort. Für die Position wähle eine Ecke oder Kante – oder ziehe den Buddy per „Platzieren\" frei hin (Bewegung rastet aufs Raster und schnappt am Bildschirmrand).":
            "Size and opacity change instantly. For position, pick a corner or edge – or drag Buddy freely with \"Place\" (movement snaps to the grid and to the screen edge).",
        "Heimatordner ausblenden":
            "Hide home folder",
        "ID":
            "ID",
        "Im Hintergrund weiterlaufen":
            "Keep running in the background",
        "Immer sichtbar":
            "Always visible",
        "In Session einsteigen":
            "Jump into session",
        "Interne ID (standardmäßig ausgeblendet). Praktisch zum Suchen.":
            "Internal ID (hidden by default). Handy for searching.",
        "Jetzt installieren":
            "Install now",
        "Jetzt verbinden":
            "Connect now",
        "Kein Sessions-Ordner gefunden":
            "No sessions folder found",
        "Keine":
            "None",
        "Keine Fenster gefunden.":
            "No windows found.",
        "Keine Sessions":
            "No sessions",
        "Kurz „Überraschung\" zeigen":
            "Show \"surprise\" briefly",
        "Lege ihn unter „Einstellungen“ fest.":
            "Set it under \"Settings\".",
        "Limit-Benachrichtigung an ✓":
            "Limit notification on ✓",
        "Limit-Benachrichtigung aus":
            "Limit notification off",
        "Lädt herunter…":
            "Downloading…",
        "Meldet sich einmal pro 5-Stunden-Fenster, sobald die Auslastung die Schwelle erreicht – zusammen mit der Uhrzeit, wann es wieder freigeht.":
            "Pings you once per 5-hour window as soon as usage hits the threshold – along with the time it frees up again.",
        "Meldet sich einmal, sobald der Akku des Geräts unter die Schwelle fällt. Erst nach dem Laden wieder.":
            "Pings you once when the device's battery drops below the threshold. Won't again until it's charged.",
        "Mit Windows starten":
            "Start with Windows",
        "Nach Updates suchen":
            "Check for updates",
        "Nachrichten":
            "Messages",
        "Neu: Dein Clawd-Buddy ✨":
            "New: Your Clawd Buddy ✨",
        "Neuer Titel":
            "New title",
        "Nichts gefunden.":
            "Nothing found.",
        "Normalerweise wählt der Buddy die Animation automatisch nach dem, was in deinen Sessions passiert. Klick eine Animation an, um sie kurz auf dem echten Buddy vorzuspielen.":
            "Normally Buddy picks the animation automatically based on what's happening in your sessions. Click an animation to preview it briefly on the real Buddy.",
        "Nur wenn Claude Code läuft":
            "Only while Claude Code is running",
        "Nur wenn dieses Fenster vorne ist:":
            "Only when this window is in front:",
        "Ordner":
            "Folder",
        "Party-Modus (nur Tanz)":
            "Party mode (dance only)",
        "Passt zu jedem Fenster, dessen Titel den eingegebenen Text enthält (Groß-/Kleinschreibung egal).":
            "Matches any window whose title contains the text you enter (case doesn't matter).",
        "Pfad/Name der Claude-CLI (Standard: claude).":
            "Path/name of the Claude CLI (default: claude).",
        "Platzieren":
            "Place",
        "Quelltext, Fehler melden, Änderungen nachlesen.":
            "Source code, report bugs, check out what's changed.",
        "Rahmen":
            "Frame",
        "Rechtsklick":
            "Right-click",
        "Rechtsklick oder Doppelklick auf den Buddy schickt ihn kurz weg – ausgeschaltet wird er dadurch nicht. Beim nächsten neuen Claude-Terminal ist er wieder da.":
            "Right-click or double-click on Buddy sends him away for a bit – that doesn't turn him off. He'll be back with the next new Claude terminal.",
        "Schickt deine Claude-Auslastung per Bluetooth an ein Clawdmeter-Gerät. Das Gerät muss einmalig in den Windows-Bluetooth-Einstellungen gekoppelt werden.":
            "Sends your Claude usage over Bluetooth to a Clawdmeter device. The device needs to be paired once in Windows Bluetooth settings.",
        "Schnellwahl":
            "Quick pick",
        "Schwelle für die Akku-Warnung":
            "Threshold for the battery warning",
        "Schwelle für die Vorwarnung":
            "Threshold for the early warning",
        "Session umbenennen – der Titel bleibt dauerhaft dein eigener.":
            "Rename session – the title stays yours for good.",
        "Session-ID":
            "Session ID",
        "Session-ID in die Zwischenablage kopieren":
            "Copy session ID to clipboard",
        "Session-ID kopiert ✓":
            "Session ID copied ✓",
        "Sessions":
            "Sessions",
        "Sessions in diesen Ordnern werden komplett ausgeblendet.":
            "Sessions in these folders are hidden completely.",
        "Sessions, die direkt in deinem Benutzerordner (":
            "Sessions located directly in your user folder (",
        "So geht's schnell":
            "Here's the fast way",
        "Speichern":
            "Save",
        "Später":
            "Later",
        "Standard-Titel":
            "Default title",
        "Standard-Titel wiederhergestellt":
            "Default title restored",
        "Suche":
            "Search",
        "Suche nach Titel, Ordner, Inhalt …":
            "Search by title, folder, content…",
        "Tab „Buddy\" → Toggle „An\". Beim ersten Mal steht er in der Bildschirmmitte.":
            "\"Buddy\" tab → toggle \"On\". The first time, he'll appear in the center of the screen.",
        "Titel":
            "Title",
        "Titel ändern":
            "Change title",
        "Titel ändern (F2)":
            "Change title (F2)",
        "Umbenennung rückgängig – zeigt wieder den automatisch erzeugten Titel":
            "Undo rename – shows the auto-generated title again",
        "Update fehlgeschlagen: {grund}":
            "Update failed: {grund}",
        "Update konnte nicht übernommen werden – bitte manuell installieren":
            "Update couldn't be applied – please install manually",
        "Update verfügbar":
            "Update available",
        "Verbindungen":
            "Connections",
        "Verhalten":
            "Behavior",
        "Verlauf":
            "History",
        "Vollbild an/aus.":
            "Toggle fullscreen.",
        "Vorwarnen bevor das Limit voll ist":
            "Give a heads-up before the limit is reached",
        "Vorwarnung an ✓":
            "Early warning on ✓",
        "Vorwarnung aus":
            "Early warning off",
        "Wann du zuletzt mit der Session gearbeitet hast (heute / gestern / Datum).":
            "When you last worked on the session (today / yesterday / date).",
        "Warnen wenn der Akku zur Neige geht":
            "Warn when the battery is running low",
        "Warnschwelle: {v}%":
            "Warning threshold: {v}%",
        "Weiter":
            "Next",
        "Welche Spalten in der Tabelle erscheinen und in welcher Reihenfolge.":
            "Which columns appear in the table and in what order.",
        "Welches gekoppelte Gerät benutzt wird.":
            "Which paired device is used.",
        "Wenn aktiv, versteckt der X-Button die App nur (Icon im System-Tray unten rechts, Klick öffnet sie wieder).":
            "When enabled, the X button just hides the app (icon in the system tray at the bottom right, click it to bring it back).",
        "Wie eine Session gestartet wird.":
            "How a session gets started.",
        "Wie viel vom 5-Stunden-Fenster und von der Woche verbraucht ist. Aktualisiert sich von selbst.":
            "How much of the 5-hour window and the week you've used up. Updates on its own.",
        "Willkommen 👋":
            "Welcome 👋",
        "Windows Terminal":
            "Windows Terminal",
        "Windows-Systembenachrichtigung wenn dein Claude-Limit sich zurückgesetzt hat und du wieder loslegen kannst. Braucht den System-Tray aktiv.":
            "Windows system notification when your Claude limit has reset and you can get going again. Needs the system tray enabled.",
        "Wird geladen…":
            "Loading…",
        "Wo Claude die Session-Dateien speichert. Wird automatisch gesucht, lässt sich aber überschreiben.":
            "Where Claude stores the session files. Found automatically, but you can override it.",
        "Womit öffnen?":
            "Open with?",
        "Wähle deine Farbe":
            "Choose your color",
        "Wähle eine Session aus, um Details zu sehen.":
            "Select a session to see its details.",
        "Zuletzt aktiv":
            "Last active",
        "Zurück":
            "Back",
        "aktuell":
            "current",
        "claude.exe":
            "claude.exe",
        "unbekannt":
            "unknown",
        "{du} von dir · {claude} von Claude":
            "{du} from you · {claude} from Claude",
        "{n} Sessions":
            "{n} sessions",
        "{n} Treffer":
            "{n} matches",
        "Öffnet das Menü mit Farbe, Umbenennen und Ordner ausblenden.":
            "Opens the menu with color, rename, and hide folder.",
        "Öffnet die Session direkt in Claude Code – der schnellste Weg zurück in ein Gespräch.":
            "Opens the session directly in Claude Code – the fastest way back into a conversation.",
        "Öffnet die aktuell markierte Session (wenn das Suchfeld nicht aktiv ist).":
            "Opens the currently selected session (when the search field isn't active).",
        "Übernehmen":
            "Apply",
        # ---- Zustandstexte mit eingesetzten Werten -----------------------
        " ({nr}. Versuch)": " (attempt {nr})",
        "5 Stunden": "5 hours",
        "Aktuell ✓": "Up to date ✓",
        "Aus.": "Off.",
        "Bluetooth-Modul nicht verfügbar (bleak fehlt).":
            "Bluetooth module unavailable (bleak is missing).",
        "Fehler beim Laden: {grund}": "Couldn't load: {grund}",
        "Lädt erneut…": "Trying again…",
        "Nicht verbunden: {grund}": "Not connected: {grund}",
        "Noch keine Auslastungsdaten – kommt mit der nächsten Abfrage.":
            "No usage data yet – it arrives with the next check.",
        "Prüfe…": "Checking…",
        "Reset-Zeit noch unbekannt": "Reset time not known yet",
        "Update verfügbar: v{v}": "Update available: v{v}",
        "v{v} verfügbar": "v{v} available",
        "Verbinde…": "Connecting…",
        "Verbunden.": "Connected.",
        "Verbunden — zuletzt gesendet vor {sek}s.":
            "Connected — last sent {sek}s ago.",
        "voll": "full",
        "voll – zurückgesetzt in {dauer}": "full – resets in {dauer}",
        "zurückgesetzt in {dauer}": "resets in {dauer}",
        "{name} · {n} Frames · Klick zum Vorspielen":
            "{name} · {n} frames · click to play it",

        # ---- Ueber Umwege uebersetzt -------------------------------------
        # Diese Saetze stehen nicht im Markup, sondern als Wert in einer
        # Liste und laufen ueber t(variable). Weder die Suche im Markup noch
        # die nach t("…") findet sie - deshalb hier von Hand.
        #
        # Farbtoene der Oberflaeche
        "Warm": "Warm",
        "Neutral": "Neutral",
        "Kühl": "Cool",
        "Ozean": "Ocean",
        "Violett": "Violet",
        "Wald": "Forest",
        "Schwarz": "Black",
        # Tastenkuerzel-Fusszeile
        "einsteigen": "resume",
        "umbenennen": "rename",
        "Menü": "menu",
        "Vollbild": "full screen",
        "Buddy kurz wegschicken": "send the buddy away",
        "dasselbe": "same thing",
        "verschieben": "move him",
        "Dialog schließen": "close the dialog",

        # ---- Zuvor uebersehen: Text als Funktionsargument ----------------
        "Woche": "Week",
        "Buddy läuft": "Buddy is running",
        "Buddy läuft · {grund}": "Buddy is running · {grund}",
        "Ecke/Kante auf dem Monitor unter dem Buddy":
            "Corner or edge on whichever display the buddy is on",
        "(nicht gesetzt)": "(not set)",
        "Suche…": "Searching…",
        "Keine gekoppelten Bluetooth-Geräte":
            "No paired Bluetooth devices",
        "Update auf v{neu} (aktuell v{alt})":
            "Update to v{neu} (you have v{alt})",
        "Verbesserungen und Fehlerbehebungen.":
            "Improvements and bug fixes.",
        "Sessions-Ordner gefunden:": "Sessions folder found:",
        "Kein Sessions-Ordner gefunden – du kannst ihn später in den "
        "Einstellungen festlegen.":
            "No sessions folder found – you can set it later in the settings.",

        # ---- Karten-Ueberschriften ---------------------------------------
        # Vor jeder steht ein Symbol. Der Sammler hielt den Text deshalb fuer
        # untrennbar mit einem eingesetzten Wert verbunden und liess ihn aus -
        # im Baum sind Symbol und Text aber getrennt.
        "Sessions-Ordner": "Sessions folder",
        "Anzeige": "Display",
        "Weitere ausgeblendete Ordner": "More hidden folders",
        "Spalten": "Columns",
        "Dein Limit": "Your limit",
        "Akzentfarbe": "Accent colour",
        "Hintergrund": "Background",
        "Fenster schließen": "Closing the window",
        "Autostart": "Start with Windows",
        "Benachrichtigungen": "Notifications",
        "Terminal & Claude": "Terminal & Claude",
        "Updates": "Updates",
        "Projekt": "Project",
        "Dein kleiner Buddy auf dem Desktop": "Your little desktop buddy",
        "Wann sichtbar": "When to show him",
        "Aussehen & Position": "Looks & position",
        "Animationen ausprobieren": "Try the animations",
        "Aktuelle Version: v{v} — beim Start wird automatisch nach Updates "
        "gesucht (ohne Internet wird das übersprungen).":
            "You have v{v} — the app checks for updates on start, and skips "
            "the check when you're offline.",

        # Tastennamen in der Fusszeile
        "Doppelklick": "Double-click",
        "Rechtsklick": "Right-click",

        # ---- In Bedingungen versteckt ------------------------------------
        # Diese stehen als 'a' : 'b' mitten in einer Abfrage. Im fertigen
        # Baum sind sie normaler Text und werden dort ersetzt, aber keine
        # Suche im Quelltext kann sie als uebersetzungspflichtig erkennen.
        "An": "On",
        "Aus": "Off",
        "Gefunden": "Found",
        "Nicht gefunden": "Not found",
        "Startet…": "Starting…",
        "Fast fertig…": "Nearly there…",
        "Bereit! Programm startet neu…": "Ready – restarting…",
        "Zur Download-Seite": "Open the download page",
        "Automatisch (nichts gefunden)": "Automatic (nothing found)",
        "Sprite-Daten fehlen – bitte neu installieren.":
            "Sprite data is missing – please reinstall.",
        "Los geht's! 🎉": "Let's go! 🎉",
        "Cam": "Cam",
        "Ziehen": "Drag",
        "z.B. „claude": "e.g. \"claude",
        "Akku des Clawdmeter: {pct} %": "Clawdmeter battery: {pct}%",

        # Schnellwahl der Ecken und Kanten
        "Oben links": "Top left",
        "Oben Mitte": "Top centre",
        "Oben rechts": "Top right",
        "Mitte links": "Middle left",
        "Mitte": "Centre",
        "Mitte rechts": "Middle right",
        "Unten links": "Bottom left",
        "Unten Mitte": "Bottom centre",
        "Unten rechts": "Bottom right",

        # ---- Rueckfrage-Erkennung ueber Hooks ----------------------------
        "Rückfragen zuverlässig erkennen": "Spot permission prompts reliably",
        # Der Satz ist im Markup von einem <code> unterbrochen, deshalb zwei
        # Bruchstuecke - die Reihenfolge bleibt im Englischen dieselbe.
        "Claude Code meldet dem Buddy selbst, wenn es auf deine Antwort "
        "wartet. Ohne das muss die App raten – und rät falsch, sobald "
        "mehrere Terminals offen sind: eines arbeitet, das andere fragt. "
        "Trägt einen Hook in":
            "Claude Code tells the buddy itself when it's waiting on you. "
            "Without it the app has to guess – and guesses wrong as soon as "
            "you have several terminals open: one is working, the other is "
            "asking. Adds a hook to",
        "ein; deine übrigen Hooks bleiben unangetastet.":
            "; your other hooks stay untouched.",
        "~/.claude/settings.json": "~/.claude/settings.json",
        "Eingerichtet. Neu gestartete Claude-Code-Sitzungen melden sich von "
        "selbst.":
            "Set up. Claude Code sessions started from now on report in by "
            "themselves.",
        "Hook eingerichtet ✓ – gilt ab der nächsten Claude-Code-Sitzung":
            "Hook added ✓ – takes effect in your next Claude Code session",
        "Hook entfernt": "Hook removed",
        "Hook konnte nicht gesetzt werden": "Couldn't set the hook",

        # ---- Rundgang nach einem Update ----------------------------------
        "Neu in dieser Version ✨": "New in this version ✨",
        "Neu:": "New:",
        "Kurzer Rundgang – deine Einstellungen bleiben unberührt.":
            "A quick tour – your settings stay untouched.",
        "Ein animierter Clawd-Buddy für deinen Desktop, der zeigt was Claude "
        "gerade macht. Neuer Tab „Buddy\" mit allen Einstellungen – Position, "
        "Größe, Rahmen, Sichtbarkeit nur wenn Claude Code läuft.":
            "An animated Clawd buddy for your desktop that shows what Claude "
            "is doing. A new \"Buddy\" tab holds every setting – position, "
            "size, frame, and showing up only while Claude Code is running.",

        # ---- Einstellungen: Sprache --------------------------------------
        "Darstellung": "Appearance",
        "Sprache": "Language",
        "Sprache der Oberfläche": "Interface language",
        "Automatisch": "Automatic",
        "„Automatisch\" richtet sich nach Windows: deutsche Oberfläche auf "
        "deutschen Systemen, sonst Englisch.":
            "\"Automatic\" follows Windows: German on German systems, "
            "English everywhere else.",
    },
}
