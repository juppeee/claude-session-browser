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
